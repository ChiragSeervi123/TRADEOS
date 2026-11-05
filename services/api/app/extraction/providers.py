"""Extraction provider adapters.

- `DemoProvider`: deterministic local parser, no network. Always available.
- `AnthropicProvider`: Claude via the official SDK with structured outputs
  (`output_config.format` JSON schema). Used only when ANTHROPIC_API_KEY is set AND
  the organisation has opted in to external AI processing.

Both return the same raw schema; the output is re-validated with Pydantic and then
matched against master data deterministically (the LLM never decides the SKU).
"""

import base64
import json
import time
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.config import get_settings
from app.extraction import demo_parser
from app.extraction.schemas import JSON_SCHEMAS, RAW_MODELS


@dataclass
class ProviderResult:
    ok: bool
    provider: str
    model: str | None
    raw: dict | None = None
    parsed: object | None = None
    error: str | None = None
    latency_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class ExtractionInput:
    kind: str  # order | purchase_invoice | scheme
    text: str | None = None
    content: bytes | None = None
    content_type: str | None = None
    catalog_hint: list[str] = field(default_factory=list)  # "SKU | name variant"
    known_brands: list[str] = field(default_factory=list)
    known_categories: list[str] = field(default_factory=list)


class DemoProvider:
    name = "demo_parser"
    model = "demo-parser-1.0"

    def extract(self, inp: ExtractionInput) -> ProviderResult:
        start = time.perf_counter()
        if not inp.text:
            return ProviderResult(
                ok=False,
                provider=self.name,
                model=self.model,
                error="No machine-readable text. Images and scanned PDFs need the AI provider (not configured) or manual entry.",
            )
        if inp.kind == "order":
            parsed = demo_parser.parse_order(inp.text)
        elif inp.kind == "purchase_invoice":
            parsed = demo_parser.parse_invoice(inp.text)
        else:
            parsed = demo_parser.parse_scheme(inp.text, inp.known_brands, inp.known_categories)
        return ProviderResult(ok=True, provider=self.name, model=self.model, raw=parsed.model_dump(mode="json"), parsed=parsed, latency_ms=int((time.perf_counter() - start) * 1000))


SYSTEM_PROMPTS = {
    "order": (
        "You extract retail purchase orders sent to an Indian FMCG distributor (WhatsApp messages, notes, PDFs, photos). "
        "Return every ordered item as a line. Keep `source_description` exactly as written. `product_text` is the product "
        "as the customer described it (brand, product, pack size). `quantity` is the number ordered and `unit` the ordering "
        "unit as written (e.g. case, pcs, dozen, box) or null. Set `sku_hint` only when the text contains an SKU code that "
        "appears in the catalogue list, otherwise null. Identify the retailer (shop name, phone or code) if present. "
        "Use null for anything not stated; never guess quantities, products or dates. Put greetings and remarks in `notes` "
        "and anything unclear in `warnings`. Confidence values are your own 0-1 estimate per field/line."
    ),
    "purchase_invoice": (
        "You extract supplier purchase invoices for an Indian FMCG distributor. Return header fields, every line item and "
        "the totals exactly as printed (numbers without currency symbols or thousands separators; dates as YYYY-MM-DD). "
        "`line_total` is the line amount before tax if the invoice shows it, otherwise as printed. Use null for anything "
        "missing or unreadable and add a warning; never compute or invent values that are not on the document."
    ),
    "scheme": (
        "You read trade-scheme circulars from FMCG manufacturers and propose candidate scheme rules for human review. "
        "For each distinct scheme give the benefit type and parameters, product scope (brands, categories, product "
        "descriptions), eligible territories, validity dates (YYYY-MM-DD) and quote the source sentence in `source_section`. "
        "If anything is ambiguous, leave it null and explain in `warnings`. Do not invent rules."
    ),
}


class AnthropicProvider:
    name = "anthropic"

    def __init__(self):
        s = get_settings()
        self.model = s.anthropic_model
        self.effort = s.anthropic_effort
        self.fallback = s.anthropic_refusal_fallback
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=float(s.ai_timeout_seconds), max_retries=2)

    def _content(self, inp: ExtractionInput) -> list[dict]:
        blocks: list[dict] = []
        if inp.content and inp.content_type == "application/pdf":
            blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.standard_b64encode(inp.content).decode()}})
        elif inp.content and (inp.content_type or "").startswith("image/"):
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": inp.content_type, "data": base64.standard_b64encode(inp.content).decode()}})
        parts = []
        if inp.kind == "order" and inp.catalog_hint:
            parts.append("Catalogue (SKU | product) for SKU hints only:\n" + "\n".join(inp.catalog_hint[:400]))
        if inp.kind == "scheme" and (inp.known_brands or inp.known_categories):
            parts.append(f"Known brands: {', '.join(inp.known_brands[:200])}\nKnown categories: {', '.join(inp.known_categories[:100])}")
        if inp.text and not blocks:
            parts.append("Document text:\n<<<\n" + inp.text[:100000] + "\n>>>")
        parts.append("Extract the structured data now.")
        blocks.append({"type": "text", "text": "\n\n".join(parts)})
        return blocks

    def extract(self, inp: ExtractionInput) -> ProviderResult:
        anthropic = self._anthropic
        start = time.perf_counter()
        kwargs: dict = {
            "model": self.model,
            "max_tokens": 16000,
            "system": SYSTEM_PROMPTS[inp.kind],
            "messages": [{"role": "user", "content": self._content(inp)}],
            "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": JSON_SCHEMAS[inp.kind]}},
        }
        if self.fallback == "default":
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        base = ProviderResult(ok=False, provider=self.name, model=self.model)
        try:
            resp = self.client.beta.messages.create(**kwargs)
        except anthropic.AuthenticationError:
            base.error = "AI provider rejected the API key (authentication failed)."
            return base
        except anthropic.RateLimitError:
            base.error = "AI provider rate limit reached; retry later."
            base.meta["retryable"] = True
            return base
        except anthropic.BadRequestError as exc:
            base.error = f"AI provider rejected the request: {exc.message}"[:500]
            return base
        except anthropic.APITimeoutError:
            base.error = "AI provider timed out."
            base.meta["retryable"] = True
            return base
        except anthropic.APIStatusError as exc:
            base.error = f"AI provider error (HTTP {exc.status_code})."
            base.meta["retryable"] = exc.status_code >= 500
            return base
        except anthropic.APIConnectionError:
            base.error = "Could not reach the AI provider (network error)."
            base.meta["retryable"] = True
            return base
        base.latency_ms = int((time.perf_counter() - start) * 1000)
        base.request_id = getattr(resp, "_request_id", None)
        base.model = getattr(resp, "model", self.model)
        if resp.usage:
            base.input_tokens = resp.usage.input_tokens
            base.output_tokens = resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            base.error = "The AI provider declined to process this document" + (f" ({details.category})" if details and getattr(details, "category", None) else "") + ". Enter it manually."
            return base
        if resp.stop_reason == "max_tokens":
            base.error = "The AI response was truncated (document too long). Split the document or enter it manually."
            return base
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if not text:
            base.error = "The AI provider returned no structured output."
            return base
        try:
            raw = json.loads(text)
            parsed = RAW_MODELS[inp.kind].model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            base.error = f"AI output failed schema validation: {str(exc)[:300]}"
            base.raw = {"text": text[:20000]}
            return base
        base.ok = True
        base.raw = raw
        base.parsed = parsed
        return base


def provider_status(org=None) -> dict:
    s = get_settings()
    key = bool(s.anthropic_api_key)
    org_ok = bool(org and org.ai_external_enabled)
    if key and org_ok:
        state = "configured"
        active = "anthropic"
    elif key:
        state = "available_not_enabled"
        active = "demo_parser"
    else:
        state = "not_configured"
        active = "demo_parser"
    return {
        "provider": "anthropic",
        "status": state,
        "active_provider": active,
        "model": s.anthropic_model if key else None,
        "message": {
            "configured": "Claude is configured and enabled for this organisation. Documents are sent to Anthropic for extraction.",
            "available_not_enabled": "An API key is configured, but this organisation has not enabled external AI processing (Settings > Integrations). The local demo parser is used.",
            "not_configured": "No AI provider is configured (ANTHROPIC_API_KEY not set). Text and digital PDFs use the local demo parser; images need manual entry.",
        }[state],
        # A live connectivity check is not performed automatically; "configured" means
        # credentials are present, not that a round trip has been verified.
        "verified": False,
    }


def get_provider(org):
    status = provider_status(org)
    if status["active_provider"] == "anthropic":
        return AnthropicProvider()
    return DemoProvider()
