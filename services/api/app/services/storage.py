"""Private file storage with a local adapter (dev) and an S3-compatible extension point.

Files are never served directly: downloads go through short-lived HMAC-signed URLs.
Uploads are validated by extension, declared MIME type, size, and magic bytes.
"""

import hashlib
import time
import uuid
from pathlib import Path

from app.config import get_settings
from app.errors import AppError
from app.security import sign, verify_signature

ALLOWED = {
    # extension: (mime types, magic-byte check)
    ".pdf": ({"application/pdf"}, lambda b: b.startswith(b"%PDF-")),
    ".png": ({"image/png"}, lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
    ".jpg": ({"image/jpeg"}, lambda b: b.startswith(b"\xff\xd8\xff")),
    ".jpeg": ({"image/jpeg"}, lambda b: b.startswith(b"\xff\xd8\xff")),
    ".webp": ({"image/webp"}, lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP"),
    ".txt": ({"text/plain"}, lambda b: _is_text(b)),
    ".csv": ({"text/csv", "application/vnd.ms-excel", "text/plain"}, lambda b: _is_text(b)),
}


def _is_text(b: bytes) -> bool:
    try:
        b[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return False
    return b"\x00" not in b[:4096]


def validate_upload(filename: str, content_type: str | None, content: bytes) -> str:
    """Returns the normalised content type, or raises AppError."""
    s = get_settings()
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise AppError("UNSUPPORTED_FILE_TYPE", f"Unsupported file type '{ext or 'none'}'. Allowed: PDF, PNG, JPG, WEBP, TXT, CSV.", 415)
    if not content:
        raise AppError("EMPTY_FILE", "The file is empty.", 422)
    if len(content) > s.max_upload_mb * 1024 * 1024:
        raise AppError("FILE_TOO_LARGE", f"Files are limited to {s.max_upload_mb} MB.", 413)
    mimes, magic = ALLOWED[ext]
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct and ct not in mimes and ct != "application/octet-stream":
        raise AppError("MIME_MISMATCH", f"Declared type '{ct}' does not match the '{ext}' extension.", 415)
    if not magic(content):
        raise AppError("CONTENT_MISMATCH", "File content does not match its extension.", 415)
    # Malware scanning integration point: call the scanner here in production and
    # reject on a positive result. Not configured in this build (see docs/SECURITY.md).
    return sorted(mimes)[0] if not ct or ct == "application/octet-stream" else ct


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise AppError("INVALID_KEY", "Invalid storage key.", 400)
        return p

    def put(self, org_id, content: bytes, ext: str) -> str:
        key = f"{org_id}/{uuid.uuid4().hex}{ext}"
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return key

    def get(self, key: str) -> bytes:
        p = self._path(key)
        if not p.exists():
            raise AppError("NOT_FOUND", "File not found in storage.", 404)
        return p.read_bytes()


def storage() -> LocalStorage:
    return LocalStorage(get_settings().storage_dir)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def signed_url(document_id, ttl: int | None = None) -> dict:
    exp = int(time.time()) + (ttl or get_settings().signed_url_seconds)
    sig = sign(f"doc:{document_id}", exp)
    return {"url": f"/api/v1/documents/{document_id}/content?expires={exp}&signature={sig}", "expires_at": exp}


def check_signature(document_id, expires: int, signature: str) -> bool:
    return verify_signature(f"doc:{document_id}", expires, signature)
