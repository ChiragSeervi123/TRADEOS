"""Test fixtures. Uses SQLite by default; set TEST_DATABASE_URL to run against PostgreSQL
(CI does this). Each test gets a clean schema."""

import os
import tempfile
import uuid
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="tradeos_test_"))
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{(_tmp / 'test.db').as_posix()}")
os.environ["STORAGE_DIR"] = str(_tmp / "storage")
os.environ["JOBS_MODE"] = "inline"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["ANTHROPIC_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db as dbmod  # noqa: E402
from app.db import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Membership, Organization, User  # noqa: E402
from app.routers.organizations import bootstrap_org  # noqa: E402
from app.security import create_access_token, hash_password, rate_limiter  # noqa: E402

_PW_HASH = hash_password("Password123!")


@pytest.fixture(autouse=True)
def _schema():
    Base.metadata.drop_all(dbmod.engine)
    Base.metadata.create_all(dbmod.engine)
    rate_limiter.reset()
    yield


@pytest.fixture
def db():
    s = dbmod.SessionLocal()
    yield s
    s.close()


@pytest.fixture
def client():
    return TestClient(app)


class Tenant:
    """A distributor org with helpers to create users of any role."""

    def __init__(self, client: TestClient, name: str, org_type: str = "distributor"):
        self.client = client
        s = dbmod.SessionLocal()
        org = Organization(name=name, slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}", org_type=org_type, settings={})
        s.add(org)
        s.flush()
        bootstrap_org(s, org)
        s.commit()
        self.org_id = org.id
        s.close()
        self.owner = self.user("org_owner")

    def user(self, role: str) -> dict:
        s = dbmod.SessionLocal()
        u = User(email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", full_name=f"Test {role}", password_hash=_PW_HASH)
        s.add(u)
        s.flush()
        s.add(Membership(user_id=u.id, organization_id=self.org_id, role=role))
        s.commit()
        uid = u.id
        s.close()
        return {"Authorization": f"Bearer {create_access_token(uid, self.org_id)}", "_user_id": str(uid)}

    @staticmethod
    def h(headers: dict) -> dict:
        return {k: v for k, v in headers.items() if not k.startswith("_")}

    def get(self, path, headers=None, **kw):
        return self.client.get(f"/api/v1{path}", headers=self.h(headers or self.owner), **kw)

    def post(self, path, json=None, headers=None, **kw):
        return self.client.post(f"/api/v1{path}", json=json, headers=self.h(headers or self.owner), **kw)

    def patch(self, path, json=None, headers=None, **kw):
        return self.client.patch(f"/api/v1{path}", json=json, headers=self.h(headers or self.owner), **kw)

    def put(self, path, json=None, headers=None, **kw):
        return self.client.put(f"/api/v1{path}", json=json, headers=self.h(headers or self.owner), **kw)

    def delete(self, path, headers=None, **kw):
        return self.client.delete(f"/api/v1{path}", headers=self.h(headers or self.owner), **kw)


@pytest.fixture
def tenant(client):
    return Tenant(client, "Alpha Distributors")


@pytest.fixture
def other_tenant(client):
    return Tenant(client, "Beta Traders")


def ok(resp, status: int = 200):
    assert resp.status_code == status, f"{resp.status_code}: {resp.text}"
    return resp.json()
