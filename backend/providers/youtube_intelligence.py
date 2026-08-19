"""Gemini → YouTube Intelligence: the user's OWN connected Gemini credential
reasons directly about a public YouTube video's actual audiovisual content —
not metadata scraping, not transcript-only analysis. Moseisley never
downloads, proxies, or stores the video; Gemini's own infrastructure fetches
it server-side from the URL (ai.google.dev/gemini-api/docs/video-understanding,
verified 2026-08). Preview capability per Google's own docs: one video per
request, public videos only — a private/unlisted/inaccessible video is
Gemini's own rejection, surfaced here as a clean error, never guessed at.

BYOK only (§6/§15): this NEVER falls back to a Moseisley-owned Gemini key and
is NEVER routed through the Factory allowance — the same boundary
usage_policy.py enforces for any other paid capability on the user's own
provider account. Unlike web search (Brave/Tavily/Perplexity), there is no
"connect a different provider" escape hatch: only Gemini serves this
capability, so `provider_not_connected` is the one gate.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qs, urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.crypto import decrypt_secret
from backend.core.models import LlmUsage
from backend.providers import registry
from backend.providers.clients import GeminiClient, ProviderError

logger = logging.getLogger("mychief.youtube_intelligence")

# Exact-match allowlist — never substring/endswith, which a hostname like
# "youtube.com.evil.example" or "evil-youtube.com" would slip past (§5/§14).
_YOUTUBE_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be", "www.youtu.be",
})
# YouTube's video id shape: 11 chars of [A-Za-z0-9_-]. Validating this closes
# off anything smuggled into the id segment/query param.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_TIMESTAMP_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

SYSTEM_INSTRUCTION = (
    "You are Moseisley YouTube Intelligence, analyzing the actual audiovisual "
    "content of a public YouTube video on behalf of the user — not just its "
    "title, description, or transcript. Ground your answer in what you "
    "actually see and hear in the video. If asked for specific moments, "
    "times, or a timeline, give timestamps in HH:MM:SS format based only on "
    "what you observe — never invent a timestamp you are not confident in. "
    "If the video's content does not support the request, say so plainly "
    "rather than guessing."
)

# Light instruction hints, not a forced schema (§4 — free-form instruction is
# the primitive; modes only nudge it). "timeline" is the one mode that also
# requests structured JSON — see analyze()'s wants_json.
ANALYSIS_MODE_HINTS: dict[str, str] = {
    "summary": "Provide a concise summary of the video.",
    "detailed": "Provide a thorough, detailed analysis of the video's content.",
    "qa": "Answer the question directly and precisely, grounded in the video.",
    "key_points": "Extract the key points as a clear, focused list.",
    "timeline": (
        'Provide a chronological timeline of the video\'s important moments. '
        'Respond with a JSON array of objects, each with exactly the keys '
        '"timestamp" (HH:MM:SS), "title", and "description". Only include '
        "moments you can genuinely identify in the video — never fabricate "
        "a timestamp."
    ),
}


class YoutubeUrlInvalid(Exception):
    pass


class ProviderNotConnected(Exception):
    """Structured, actionable state — never a generic error (§6)."""


def canonical_youtube_url(raw_url: str) -> str:
    """Validate + normalize to https://www.youtube.com/watch?v=<id> — the
    ONLY form ever sent to Gemini, never the user's raw string (closes off
    parameter smuggling / disguised-host tricks and gives a clean, stable
    source_url for metadata). Robust parsing (§5/§14): exact hostname
    allowlist, not substring matching; scheme restricted to https."""
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise YoutubeUrlInvalid("empty URL")
    try:
        parts = urlsplit(raw_url.strip())
    except ValueError as e:
        raise YoutubeUrlInvalid("malformed URL") from e
    if parts.scheme != "https":
        raise YoutubeUrlInvalid("only https YouTube URLs are supported")
    try:
        host = (parts.hostname or "").lower()
    except ValueError as e:  # a malformed authority component (e.g. bad IPv6)
        raise YoutubeUrlInvalid("malformed URL") from e
    if host not in _YOUTUBE_HOSTS:
        raise YoutubeUrlInvalid("not a recognized YouTube URL")

    video_id: str | None = None
    if host in ("youtu.be", "www.youtu.be"):
        video_id = parts.path.lstrip("/").split("/")[0] or None
    elif parts.path == "/watch":
        video_id = (parse_qs(parts.query).get("v") or [None])[0]
    elif parts.path.startswith("/shorts/"):
        video_id = parts.path[len("/shorts/"):].split("/")[0] or None

    if not video_id or not _VIDEO_ID_RE.match(video_id):
        raise YoutubeUrlInvalid("could not find a valid YouTube video id in that URL")
    return f"https://www.youtube.com/watch?v={video_id}"


def _try_parse_timeline(text: str) -> list[dict] | None:
    """Best-effort structured parse for timeline mode — never fatal, never
    fabricates a moment: a row with a malformed/missing timestamp is dropped,
    not guessed at (§8)."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        ts = item.get("timestamp")
        if not isinstance(ts, str) or not _TIMESTAMP_RE.match(ts):
            continue
        out.append({
            "timestamp": ts,
            "title": str(item.get("title") or "")[:200],
            "description": str(item.get("description") or "")[:1000],
        })
    return out


async def analyze(
    db: AsyncSession, user_id: str, youtube_url: str, instruction: str,
    *, analysis_mode: str | None = None,
) -> dict:
    """The one primitive (§4/§7): YouTube URL + instruction -> Gemini
    audiovisual analysis, using the user's OWN connected Gemini credential.
    Raises YoutubeUrlInvalid / ProviderNotConnected for the two states
    callers must present distinctly (see error_detail) — never as a generic
    failure. A ProviderError (upstream Gemini failure) propagates as-is for
    the same reason."""
    canonical_url = canonical_youtube_url(youtube_url)

    row = await registry.get_provider_row(db, user_id, "gemini")
    if row is None or not row.enabled or not row.encrypted_secret:
        raise ProviderNotConnected()

    hint = ANALYSIS_MODE_HINTS.get((analysis_mode or "").strip().lower())
    instruction = instruction.strip()
    if hint:
        prompt = f"{hint}\n\n{instruction}" if instruction else hint
    else:
        prompt = instruction or "Summarize this video."
    wants_json = (analysis_mode or "").strip().lower() == "timeline"

    cfg = row.configuration_json or {}
    model = cfg.get("default_model") or DEFAULT_GEMINI_MODEL
    client = GeminiClient(decrypt_secret(row.encrypted_secret), cfg.get("base_url"), model)

    try:
        result = await client.analyze_youtube(
            canonical_url, prompt, system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json" if wants_json else None,
        )
    except ProviderError as e:
        # Never the key, never a raw upstream body — status code + our own
        # message only (matches ProviderError's own shape).
        logger.info("youtube intelligence provider error user=%s status=%s",
                    user_id, e.status_code)
        raise

    usage = LlmUsage(
        user_id=user_id, provider="gemini", model=result.model or model,
        requested_model=model, purpose="youtube_intelligence", status="success",
        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        cached_input_tokens=result.cached_input_tokens,
        reasoning_tokens=result.reasoning_tokens, total_tokens=result.total_tokens,
        provider_request_id=result.provider_request_id,
    )
    db.add(usage)
    await db.flush()

    out: dict = {
        "source_type": "youtube",
        "provider": "gemini",
        "source_url": canonical_url,
        "model": result.model or model,
        "analysis_mode": analysis_mode,
        "text": result.text,
    }
    if wants_json:
        timeline = _try_parse_timeline(result.text)
        if timeline is not None:
            out["timeline"] = timeline
    return out


def error_detail(exc: Exception) -> dict:
    """Map any exception analyze() can raise into a clean, structured,
    user-facing detail — never a raw provider stack trace (§13). Shared by
    the API route and the orchestrator tool so both present the same states
    the same way."""
    if isinstance(exc, ProviderNotConnected):
        return {"state": "provider_not_connected",
                "message": "Connect Gemini in Connections to analyze YouTube videos."}
    if isinstance(exc, YoutubeUrlInvalid):
        return {"state": "invalid_url", "message": str(exc)}
    if isinstance(exc, ProviderError):
        status = exc.status_code
        if status == 429:
            return {"state": "rate_limited",
                    "message": "Gemini's rate limit or quota was reached — try again shortly."}
        if status in (401, 403):
            return {"state": "provider_key_invalid",
                    "message": "Gemini rejected the connected key — reconnect it in Connections."}
        if status == 400:
            return {"state": "video_unavailable",
                    "message": "Gemini couldn't process this video — it may be private, "
                               "unlisted, age-restricted, or otherwise inaccessible. Only "
                               "public YouTube videos are supported."}
        if status is not None and status >= 500:
            return {"state": "provider_unavailable",
                    "message": "Gemini is temporarily unavailable — try again shortly."}
        return {"state": "provider_error", "message": "Gemini could not process this video."}
    return {"state": "error", "message": "Could not analyze this video."}
