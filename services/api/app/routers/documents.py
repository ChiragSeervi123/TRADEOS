import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select

from app import audit
from app.common import PageParams, page_out
from app.db import get_db
from app.deps import Ctx, get_ctx
from app.errors import AppError, forbidden, not_found
from app.models import Document
from app.services.storage import check_signature, signed_url, storage

router = APIRouter(prefix="/documents", tags=["documents"])

# Who may see which document kind.
KIND_PERMS = {
    "order": "orders.read",
    "purchase_invoice": "finance.read",
    "credit_note": "finance.read",
    "scheme_circular": "schemes.read",
    "evidence": "finance.read",
    "other": "finance.read",
}


def doc_out(d: Document, with_url=False):
    out = {
        "id": str(d.id),
        "kind": d.kind,
        "filename": d.filename,
        "content_type": d.content_type,
        "size_bytes": d.size_bytes,
        "sha256": d.sha256,
        "has_text": bool(d.text_content),
        "created_at": d.created_at.isoformat(),
    }
    if with_url:
        out.update(signed_url(d.id))
        out["text_content"] = d.text_content
    return out


def _check(ctx: Ctx, d: Document):
    if not ctx.can(KIND_PERMS.get(d.kind, "finance.read")):
        raise forbidden()


@router.get("")
def list_documents(params: PageParams = Depends(), kind: str | None = None, ctx: Ctx = Depends(get_ctx)):
    allowed = [k for k, p in KIND_PERMS.items() if ctx.can(p)]
    stmt = select(Document).where(Document.organization_id == ctx.org_id, Document.kind.in_(allowed))
    if kind:
        stmt = stmt.where(Document.kind == kind)
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.scalars(stmt.order_by(Document.created_at.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    return page_out([doc_out(d) for d in rows], total, params)


@router.get("/{document_id}")
def get_document(document_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    d = ctx.get(Document, document_id, "Document")
    _check(ctx, d)
    return doc_out(d, with_url=True)


@router.get("/{document_id}/content")
def download(document_id: uuid.UUID, expires: int = Query(...), signature: str = Query(..., max_length=128), db=Depends(get_db)):
    """Signed, short-lived download (no bearer token needed, so it works in <img>/<a>)."""
    if not check_signature(document_id, expires, signature):
        raise AppError("INVALID_SIGNATURE", "This download link is invalid or has expired.", 403)
    d = db.get(Document, document_id)
    if not d:
        raise not_found("Document")
    data = storage().get(d.storage_key)
    safe = "".join(c for c in d.filename if c.isalnum() or c in "._- ")[:120] or "document"
    return Response(data, media_type=d.content_type, headers={"Content-Disposition": f'inline; filename="{safe}"', "Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


@router.post("/{document_id}/access-log")
def log_access(document_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    d = ctx.get(Document, document_id, "Document")
    _check(ctx, d)
    audit.record(ctx, "document.view", "document", d.id)
    ctx.db.commit()
    return {"ok": True}
