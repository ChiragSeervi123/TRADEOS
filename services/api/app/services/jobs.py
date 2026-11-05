"""Database-backed job queue (ADR 0003).

- `enqueue` inserts a job in the caller's transaction.
- In `inline` mode the request handler calls `run_pending_inline` after commit, so
  dev/tests behave synchronously without a worker.
- In `worker` mode `python -m app.worker` polls and runs jobs.
- Failures retry with bounded exponential backoff; after max_attempts a job is
  marked `dead` (dead-letter) and kept for inspection.
- Handlers must be idempotent: a job may run more than once after a crash.
"""

import logging
import traceback
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select, update

from app import db as dbmod
from app.config import get_settings
from app.db import utcnow
from app.models import Job

log = logging.getLogger("tradeos.jobs")
HANDLERS: dict[str, Callable] = {}


def handler(job_type: str):
    def deco(fn):
        HANDLERS[job_type] = fn
        return fn

    return deco


def enqueue(db, job_type: str, payload: dict, org_id=None, idempotency_key: str | None = None, max_attempts: int = 3) -> Job:
    if idempotency_key:
        existing = db.scalars(select(Job).where(Job.idempotency_key == idempotency_key)).first()
        if existing:
            return existing
    job = Job(organization_id=org_id, job_type=job_type, payload=payload, idempotency_key=idempotency_key, max_attempts=max_attempts)
    db.add(job)
    db.flush()
    return job


def _claim(db, job_id) -> Job | None:
    """Atomically move a queued job to running (safe with concurrent workers)."""
    res = db.execute(
        update(Job).where(Job.id == job_id, Job.status.in_(("queued", "failed"))).values(status="running", attempts=Job.attempts + 1, updated_at=utcnow())
    )
    db.commit()
    if res.rowcount != 1:
        return None
    return db.get(Job, job_id)


def run_job(job_id) -> Job | None:
    _ensure_handlers()
    db = dbmod.SessionLocal()
    try:
        job = _claim(db, job_id)
        if not job:
            return None
        fn = HANDLERS.get(job.job_type)
        try:
            if fn is None:
                raise RuntimeError(f"No handler for job type {job.job_type}")
            result = fn(db, job)
            job.status = "succeeded"
            job.result = result if isinstance(result, dict) else {"result": str(result)}
            job.last_error = None
            db.commit()
        except Exception as exc:  # noqa: BLE001 - jobs record and retry any failure
            db.rollback()
            job = db.get(Job, job_id)
            job.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            if job.attempts >= job.max_attempts:
                job.status = "dead"
                log.error("job_dead", extra={"job_id": str(job.id)})
            else:
                job.status = "failed"
                job.run_after = utcnow() + timedelta(seconds=min(300, 5 * 2 ** job.attempts))
            db.commit()
            if get_settings().environment == "test":
                log.debug(traceback.format_exc())
        return job
    finally:
        db.close()


def run_pending_inline(job_ids: list) -> None:
    if get_settings().jobs_mode != "inline":
        return
    for jid in job_ids:
        run_job(jid)


def due_jobs(db, limit: int = 10) -> list:
    return db.scalars(
        select(Job.id).where(Job.status.in_(("queued", "failed")), Job.run_after <= utcnow()).order_by(Job.run_after).limit(limit)
    ).all()


def _ensure_handlers():
    # Import modules that register handlers.
    from app.services import extraction  # noqa: F401
    from app.services import scheduled  # noqa: F401
