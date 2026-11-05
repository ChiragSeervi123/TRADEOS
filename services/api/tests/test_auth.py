from datetime import timedelta

from sqlalchemy import select

from app.db import utcnow
from app.models import AuditEvent, PasswordResetToken
from app.permissions import DISTRIBUTOR_ROLES, ROLE_PERMISSIONS, has_permission, permissions_for
from tests.conftest import ok

PW = "Str0ngPassword"


def register(client, email="owner@example.com"):
    return ok(client.post("/api/v1/auth/register", json={"email": email, "password": PW, "full_name": "Asha Owner"}), 201)


def bearer(t):
    return {"Authorization": f"Bearer {t['access_token']}"}


def test_register_create_org_and_me(client):
    t = register(client)
    assert t["organization_id"] is None
    r = ok(client.post("/api/v1/organizations", json={"name": "Shree Distributors"}, headers=bearer(t)), 201)
    tokens = r["tokens"]
    me = ok(client.get("/api/v1/auth/me", headers=bearer(tokens)))
    assert me["organization"]["name"] == "Shree Distributors"
    assert me["role"] == "org_owner"
    assert "orders.approve" in me["permissions"]
    checklist = ok(client.get("/api/v1/organizations/current/onboarding", headers=bearer(tokens)))
    assert checklist["total"] == 8
    assert any(i["key"] == "units" and i["done"] for i in checklist["items"])


def test_weak_password_and_duplicate_email(client):
    r = client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "alllowercase1", "full_name": "A"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "WEAK_PASSWORD"
    register(client, "dup@example.com")
    r = client.post("/api/v1/auth/register", json={"email": "DUP@example.com", "password": PW, "full_name": "B"})
    assert r.status_code == 409


def test_login_failure_is_audited_and_rate_limited(client, db):
    register(client, "rl@example.com")
    for _ in range(3):
        r = client.post("/api/v1/auth/login", json={"email": "rl@example.com", "password": "wrong"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.login_failed")).first()
    codes = [client.post("/api/v1/auth/login", json={"email": "rl@example.com", "password": "wrong"}).status_code for _ in range(10)]
    assert 429 in codes


def test_refresh_rotation_and_reuse_detection(client):
    t = register(client, "rot@example.com")
    t2 = ok(client.post("/api/v1/auth/refresh", json={"refresh_token": t["refresh_token"]}))
    assert t2["refresh_token"] != t["refresh_token"]
    # Reusing the old token revokes the family, so the new one stops working too.
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": t["refresh_token"]}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": t2["refresh_token"]}).status_code == 401


def test_password_reset_single_use_and_expiry(client, db):
    register(client, "reset@example.com")
    r = ok(client.post("/api/v1/auth/forgot-password", json={"email": "reset@example.com"}))
    token = r["dev_reset_token"]
    ok(client.post("/api/v1/auth/reset-password", json={"token": token, "password": "NewPassw0rd!"}))
    assert client.post("/api/v1/auth/reset-password", json={"token": token, "password": "NewPassw0rd!"}).status_code == 400
    ok(client.post("/api/v1/auth/login", json={"email": "reset@example.com", "password": "NewPassw0rd!"}))
    # expired token
    r = ok(client.post("/api/v1/auth/forgot-password", json={"email": "reset@example.com"}))
    row = db.scalars(select(PasswordResetToken).where(PasswordResetToken.used_at.is_(None))).first()
    row.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert client.post("/api/v1/auth/reset-password", json={"token": r["dev_reset_token"], "password": "Another1Pass"}).status_code == 400
    # unknown email: same response shape, no token
    r = ok(client.post("/api/v1/auth/forgot-password", json={"email": "nobody@example.com"}))
    assert "dev_reset_token" not in r


def test_invitation_flow(client, tenant):
    inv = ok(tenant.post("/users/invitations", {"email": "rep@example.com", "role": "sales_rep"}), 201)
    tokens = ok(
        client.post(
            "/api/v1/auth/accept-invitation",
            json={"token": inv["invite_token"], "full_name": "Ravi Rep", "password": PW},
        )
    )
    me = ok(client.get("/api/v1/auth/me", headers=bearer(tokens)))
    assert me["role"] == "sales_rep"
    assert "finance.read" not in me["permissions"]
    # token cannot be reused
    assert client.post("/api/v1/auth/accept-invitation", json={"token": inv["invite_token"]}).status_code == 400


def test_only_owner_grants_owner_and_last_owner_protected(tenant):
    admin = tenant.user("org_admin")
    r = tenant.post("/users/invitations", {"email": "x@example.com", "role": "org_owner"}, headers=admin)
    assert r.status_code == 403
    members = ok(tenant.get("/users"))
    owner = next(m for m in members if m["role"] == "org_owner")
    r = tenant.patch(f"/users/memberships/{owner['membership_id']}", {"role": "accountant"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "LAST_OWNER"


def test_unauthenticated_and_bad_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"
    assert "request_id" in r.json()["error"]


def test_role_permission_matrix():
    assert has_permission("org_owner", "orders.approve")
    assert not has_permission("sales_rep", "orders.approve")
    assert not has_permission("sales_rep", "finance.read")
    assert not has_permission("warehouse_operator", "pricing.write")
    assert not has_permission("warehouse_operator", "finance.write")
    assert has_permission("warehouse_operator", "fulfilment.execute")
    assert has_permission("accountant", "payments.allocate")
    assert not has_permission("accountant", "orders.approve")
    assert not has_permission("operations_manager", "schemes.approve")
    # manufacturer roles never get distributor permissions
    assert permissions_for("manufacturer_manager", "distributor") == frozenset()
    assert "orders.read" not in permissions_for("org_owner", "manufacturer")
    assert "shared_data.read" in permissions_for("manufacturer_viewer", "manufacturer")
    for role in DISTRIBUTOR_ROLES:
        assert role in ROLE_PERMISSIONS
