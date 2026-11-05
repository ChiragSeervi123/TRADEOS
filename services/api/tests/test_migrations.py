"""Migrations apply on a clean database and match the ORM models (no drift)."""

import os
import tempfile
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

import app.models  # noqa: F401
from app.db import Base
from app.migrate import alembic_config


def test_migrations_apply_and_match_models(monkeypatch):
    if os.environ.get("TEST_DATABASE_URL"):
        url = os.environ["TEST_DATABASE_URL"]
    else:
        url = f"sqlite:///{(Path(tempfile.mkdtemp()) / 'mig.db').as_posix()}"
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "database_url", url)
    eng = create_engine(url)
    Base.metadata.drop_all(eng)
    with eng.begin() as c:
        c.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    cfg = alembic_config()
    command.upgrade(cfg, "head")
    with eng.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], diff
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
