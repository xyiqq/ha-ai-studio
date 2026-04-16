"""Backend utility helpers for HA AI Studio."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import uuid
from typing import Any

from aiohttp import web


def json_response(payload: Any, status_code: int = 200) -> web.Response:
    """Return a JSON response."""
    return web.json_response(payload, status=status_code)


def json_message(message: str, status_code: int = 400, **extra: Any) -> web.Response:
    """Return a standard JSON message payload."""
    payload = {"success": False, "message": message}
    payload.update(extra)
    return json_response(payload, status_code=status_code)


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def generate_id(prefix: str) -> str:
    """Generate a stable random identifier with a readable prefix."""
    return f"{prefix}_{uuid.uuid4().hex}"


def summarize_text(text: str | None, limit: int = 240) -> str:
    """Return a single-line summary clipped to the requested limit."""
    raw = " ".join((text or "").split()).strip()
    if not raw:
        return ""
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 3)].rstrip() + "..."


def clip_text(text: str | None, limit: int = 4000) -> str:
    """Clip large text values while preserving line structure."""
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 14)].rstrip() + "\n...[truncated]"


def parse_json_object(raw_text: str | None) -> dict[str, Any] | None:
    """Extract and parse the first JSON object found in a model response."""
    text = (raw_text or "").strip()
    if not text:
        return None

    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced_match:
        text = fenced_match.group(1).strip()

    candidates = [text]
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and first_brace < last_brace:
        candidates.append(text[first_brace : last_brace + 1])

    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def normalize_citation(citation: dict[str, Any]) -> dict[str, Any]:
    """Normalize a citation payload into a predictable shape."""
    return {
        "type": str(citation.get("type") or "context"),
        "title": str(citation.get("title") or citation.get("label") or "Context"),
        "path": str(citation.get("path") or ""),
        "line": int(citation.get("line") or 0),
        "snippet": clip_text(str(citation.get("snippet") or citation.get("content") or ""), 500),
    }
