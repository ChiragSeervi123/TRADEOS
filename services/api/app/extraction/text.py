"""Text extraction from uploaded documents (no external services).

- PDF: embedded text via pypdf (works for digitally generated PDFs).
- Plain text / CSV: decoded as UTF-8.
- Images and scanned PDFs: no local OCR engine is bundled. They require either the
  configured AI provider (vision) or manual entry; the pipeline reports this clearly.
"""

import io
import unicodedata

from pypdf import PdfReader
from pypdf.errors import PdfReadError


def normalize_text(text: str) -> str:
    """Normalise whitespace and unicode without destroying content (the original is
    kept separately for provenance)."""
    t = unicodedata.normalize("NFKC", text or "")
    lines = [" ".join(line.split()) for line in t.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip()


def extract_text(content: bytes, content_type: str) -> tuple[str | None, dict]:
    """Returns (text or None, metadata)."""
    meta: dict = {"method": None, "pages": None}
    if content_type == "application/pdf":
        try:
            reader = PdfReader(io.BytesIO(content))
            pages = [p.extract_text() or "" for p in reader.pages]
        except (PdfReadError, ValueError, KeyError) as exc:
            return None, {"method": "pdf_text", "error": f"unreadable PDF: {exc}"}
        meta.update(method="pdf_text", pages=len(pages))
        text = "\n".join(f"[page {i + 1}]\n{p}" if len(pages) > 1 else p for i, p in enumerate(pages))
        if len(text.strip()) < 10:
            meta["scanned"] = True
            return None, meta
        return text, meta
    if content_type.startswith("text/") or content_type == "application/vnd.ms-excel":
        meta["method"] = "utf8"
        return content.decode("utf-8", errors="replace"), meta
    meta["method"] = "image"
    return None, meta
