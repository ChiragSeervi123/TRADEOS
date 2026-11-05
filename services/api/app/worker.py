"""Background worker: `python -m app.worker` (set JOBS_MODE=worker on the API).

Polls the jobs table, runs due jobs with retry/backoff, and once an hour enqueues
the periodic expiry scan and reconciliation run for every distributor organisation.
"""

import logging
import signal
import time
from datetime import UTC, datetime

from sqlalchemy import select

from app import db as dbmod
from app.main import _configure_logging
from app.models import Organization
from app.services import jobs

log = logging.getLogger("tradeos.worker")
_running = True


def _stop(*_):
    global _running
    _running = False


def enqueue_periodic(db) -> int:
    hour = datetime.now(UTC).strftime("%Y%m%d%H")
    n = 0
    for org in db.scalars(select(Organization).where(Organization.org_type == "distributor")).all():
        for jt in ("expiry_scan", "reconciliation_run"):
            jobs.enqueue(db, jt, {"organization_id": str(org.id)}, org.id, idempotency_key=f"{jt}:{org.id}:{hour}")
            n += 1
    db.commit()
    return n


def main(poll_seconds: float = 2.0, once: bool = False):
    _configure_logging()
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    log.info("worker_started")
    last_periodic = 0.0
    while _running:
        db = dbmod.SessionLocal()
        try:
            if time.time() - last_periodic > 3600:
                enqueue_periodic(db)
                last_periodic = time.time()
            ids = jobs.due_jobs(db)
        finally:
            db.close()
        for jid in ids:
            jobs.run_job(jid)
        if once:
            break
        if not ids:
            time.sleep(poll_seconds)
    log.info("worker_stopped")


if __name__ == "__main__":
    main()
