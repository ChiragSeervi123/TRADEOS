"""Password hashing (Argon2id), JWT access tokens, opaque refresh/reset tokens."""

import hashlib
import hmac
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import get_settings

_hasher = PasswordHasher()
ALGO = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: uuid.UUID, org_id: uuid.UUID | None) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "org": str(org_id) if org_id else None,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=s.access_token_minutes)).timestamp()),
        "typ": "access",
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, s.secret_key, algorithm=ALGO)


def decode_access_token(token: str) -> dict | None:
    try:
        data = jwt.decode(token, get_settings().secret_key, algorithms=[ALGO])
    except jwt.PyJWTError:
        return None
    if data.get("typ") != "access":
        return None
    return data


def new_opaque_token() -> tuple[str, str]:
    """Returns (token, sha256 hash). Only the hash is stored."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sign(value: str, expires_at: int) -> str:
    msg = f"{value}:{expires_at}".encode()
    return hmac.new(get_settings().secret_key.encode(), msg, hashlib.sha256).hexdigest()


def verify_signature(value: str, expires_at: int, signature: str) -> bool:
    if expires_at < int(time.time()):
        return False
    return hmac.compare_digest(sign(value, expires_at), signature)


class RateLimiter:
    """In-process sliding-window limiter. Single-process only; use a shared store
    (e.g. Redis) when running multiple API replicas (documented in SECURITY.md)."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self):
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()
