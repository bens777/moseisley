"""Real web search for Moseisley Chat and the Manager's benchmark research —
BYOK, per user. Three connectable providers, each with a distinct strength:

  · brave       — Brave Search API, free tier (2000 searches/month). Native
    recency (`freshness`) and a news result cluster on the same endpoint.
  · tavily      — Tavily Search API, agent-oriented web research; native
    `topic` (general/news), `time_range` recency, `include_domains`, and a
    relevance `score` per result. Has a free tier (not tracked here — see
    provider dashboard).
  · perplexity  — Perplexity sonar, paid; answers come WITH the citations
    that become BenchmarkFinding sources. No verified native recency/topic/
    domain filtering, so those are never simulated for it (§10/§17 — no
    false guarantee).

STRICT TRUTH RULE (same spirit as revenue): a result exists only if a search
provider actually returned it. This module returns titles, URLs and snippets
(and, for Perplexity, its cited answer), and the caller is prompted to cite
them. Failures are structured (`WebSearchUnavailable.state`, see below) so
the tool layer can tell the model exactly what happened instead of
estimating or fabricating.

The key is the USER'S, stored encrypted in provider_connections exactly like
LLM keys (configuration_json.kind = "search" marks the row; the provider
names below are the discriminator every LLM-side scan filters on). There is
no platform search key, and no keyless scraping fallback: a user with no
compatible search provider connected gets NoSearchProvider, which the
project flow treats as a designed path — connect Brave (free) or paste your
own sources — never as an error.

PROVIDER SELECTION (§3): DEFAULT_PROVIDER_ORDER (perplexity > tavily > brave
— paid/highest-quality first) is unchanged, pre-existing behavior used when
no `mode` is given. A `mode` ("web"/"news"/"research") selects a different,
still-explicit order via MODE_PROVIDER_ORDER — never a keyword-matching
heuristic over the query text. If the top pick fails with a TRANSIENT state
(rate_limited/quota_exhausted/provider_timeout/provider_unavailable),
search() tries the next USER-CONNECTED candidate in that same order — never
a Moseisley-owned credential, and never on a non-transient failure
(provider_key_invalid, invalid_request) or a genuine empty result, where
retrying a different provider wouldn't reflect what actually happened.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.crypto import decrypt_secret
from backend.core.models import ProviderConnection

logger = logging.getLogger("mychief.websearch")

TIMEOUT = 20.0
MAX_COUNT = 8

# Preference order when several are connected AND no mode is specified —
# unchanged, pre-existing behavior. Also THE discriminator: every LLM-side
# scan of provider_connections excludes these names, so a search key never
# counts as an AI brain (gate, EXPERT detection, model catalogs).
SEARCH_PROVIDERS = ("perplexity", "tavily", "brave")
DEFAULT_PROVIDER_ORDER = SEARCH_PROVIDERS

# Explicit, simple selection policy (§3) — a lookup table, not a heuristic.
# "research" (deep research/synthesis) prefers Tavily, built specifically for
# agent-oriented web research. "news"/"web" (current information, discovery)
# prefer Brave, which has native recency filtering and a news result
# cluster. Perplexity stays in the mix everywhere (still paid/high-quality),
# just not always first when a mode more specifically fits another provider.
MODE_PROVIDER_ORDER: dict[str, tuple[str, ...]] = {
    "research": ("tavily", "perplexity", "brave"),
    "news": ("brave", "tavily", "perplexity"),
    "web": ("perplexity", "brave", "tavily"),
}
MODES = ("web", "news", "research")
RECENCY_VALUES = ("day", "week", "month", "year", "any")
MAX_DOMAINS = 10

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
TAVILY_URL = "https://api.tavily.com/search"
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_MODEL = "sonar"

_TAGS = re.compile(r"<[^>]+>")
_DOMAIN_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63}(?<!-))+$")
# Brave's `freshness` shorthand values (api.search.brave.com/app/documentation,
# verified 2026-08). Tavily's `time_range` already uses the same words we do.
_BRAVE_FRESHNESS = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}


class WebSearchUnavailable(Exception):
    """The user's provider could not answer. The caller must say so — never
    guess. `state` is the structured, actionable status (§17) every caller
    can switch on; subclasses narrow it for specific, distinguishable
    upstream failures, and anything not specifically recognized stays the
    generic provider_unavailable."""
    state = "provider_unavailable"


class InvalidSearchRequest(WebSearchUnavailable):
    state = "invalid_request"


class SearchRateLimited(WebSearchUnavailable):
    state = "rate_limited"


class SearchQuotaExhausted(WebSearchUnavailable):
    state = "quota_exhausted"


class SearchKeyInvalid(WebSearchUnavailable):
    state = "provider_key_invalid"


class SearchTimeout(WebSearchUnavailable):
    state = "provider_timeout"


class NoSearchResults(WebSearchUnavailable):
    state = "no_results"


class NoSearchProvider(Exception):
    """The user has no search (or no MODE-COMPATIBLE search) provider
    connected. A designed state, not a failure: the flow offers to connect
    one (Brave is free) or to take pasted sources."""
    state = "provider_not_connected"


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    # Optional, provider-supplied only — NEVER guessed when a provider
    # doesn't return one (§4/§8).
    published_at: str | None = None
    source: str | None = None     # hostname, derived safely from url
    score: float | None = None    # the provider's own relevance score


@dataclass
class SearchResponse:
    provider: str
    results: list[SearchResult]
    # Perplexity synthesizes an answer grounded in `results` — useful context,
    # but findings must cite the result URLs, never the answer text alone.
    answer: str | None = None


def _domain_of(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _validate_domains(domains: list[str] | None) -> list[str]:
    """Hostname-shape allowlist only (§12) — never a scheme, path, query, or
    anything else. This never grants Moseisley a new way to fetch an
    arbitrary URL: it only ever biases a search provider's own query."""
    if not domains:
        return []
    if len(domains) > MAX_DOMAINS:
        raise InvalidSearchRequest(f"at most {MAX_DOMAINS} domain filters are supported")
    out = []
    for raw in domains:
        d = str(raw).strip().lower()
        if not d or len(d) > 253 or not _DOMAIN_RE.match(d):
            raise InvalidSearchRequest(f"invalid domain filter: {raw!r}")
        out.append(d)
    return out


def _classify_http_error(resp: httpx.Response, provider: str) -> WebSearchUnavailable:
    """Maps an upstream HTTP failure to a structured state (§17) — never a
    raw status code/body surfaced to the user. 401/403 -> key rejected; 429
    -> rate limited, UNLESS the body itself signals a quota/plan limit (the
    only reliable way to tell the two apart across providers — never
    guessed beyond what the response actually says); 5xx -> unavailable."""
    status = resp.status_code
    if status in (401, 403):
        return SearchKeyInvalid(f"{provider} rejected the key — check it on Connections")
    if status == 429:
        try:
            body_text = resp.text.lower()
        except Exception:  # noqa: BLE001 — classification must never itself crash
            body_text = ""
        if any(w in body_text for w in ("quota", "plan limit", "monthly limit", "usage limit")):
            return SearchQuotaExhausted(f"{provider} usage quota exhausted")
        return SearchRateLimited(f"{provider} rate limit reached — try again shortly")
    if status >= 500:
        return WebSearchUnavailable(f"{provider} is temporarily unavailable (HTTP {status})")
    return WebSearchUnavailable(f"{provider} returned HTTP {status}")


def _safe_json(resp: httpx.Response, provider: str) -> dict:
    try:
        return resp.json()
    except ValueError as e:
        raise WebSearchUnavailable(f"{provider} returned a malformed response") from e


async def _http_get(url: str, *, params: dict, headers: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        return await client.get(url, params=params, headers=headers)


async def _http_post(url: str, *, json: dict, headers: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        return await client.post(url, json=json, headers=headers)


async def _connected_rows(db: AsyncSession, user_id: str) -> dict[str, ProviderConnection]:
    return {r.provider: r for r in (await db.execute(
        select(ProviderConnection).where(
            ProviderConnection.user_id == user_id,
            ProviderConnection.provider.in_(SEARCH_PROVIDERS),
            ProviderConnection.enabled.is_(True)))).scalars()}


def _provider_order(mode: str | None) -> tuple[str, ...]:
    if mode is None:
        return DEFAULT_PROVIDER_ORDER
    return MODE_PROVIDER_ORDER.get(mode, DEFAULT_PROVIDER_ORDER)


async def _ordered_connections(
    db: AsyncSession, user_id: str, *, mode: str | None = None,
) -> list[tuple[str, str]]:
    """Every connected, mode-ordered (provider, decrypted key) pair — the
    full candidate list search() walks for fallback (§3)."""
    rows = await _connected_rows(db, user_id)
    out = []
    for provider in _provider_order(mode):
        row = rows.get(provider)
        if row is not None and row.encrypted_secret:
            out.append((provider, decrypt_secret(row.encrypted_secret)))
    return out


async def connection_for(db: AsyncSession, user_id: str, *,
                         mode: str | None = None) -> tuple[str, str] | None:
    """(provider, decrypted key) for this user's preferred search provider —
    the single top pick, mode-aware if `mode` is given. search() itself
    walks the full ordered list for fallback; this stays for simple callers
    that only need to know who would answer."""
    ordered = await _ordered_connections(db, user_id, mode=mode)
    return ordered[0] if ordered else None


async def connected_provider(db: AsyncSession, user_id: str) -> str | None:
    found = await connection_for(db, user_id)
    return found[0] if found else None


async def test_provider(provider: str, api_key: str) -> bool:
    """Smallest real validation call for a specific search key — a 1-result
    search, since none of these providers exposes a separate credential-check
    endpoint (OpenRouter's GET /key is the exception, not the norm here)."""
    if provider not in SEARCH_PROVIDERS:
        return False
    try:
        response = await _DISPATCH[provider]("ping", 1, api_key)
    except Exception:  # noqa: BLE001 — report health, never raise secrets
        return False
    return bool(response.results or response.answer)


# Transient upstream states worth trying a different connected provider for
# (§3) — never provider_key_invalid/invalid_request (retrying elsewhere
# won't fix a bad key or a malformed request) and never no_results (a
# genuinely completed search, not a failure).
_TRANSIENT_STATES = frozenset({"rate_limited", "quota_exhausted",
                               "provider_timeout", "provider_unavailable"})


async def search(
    db: AsyncSession, user_id: str, query: str, *, count: int = 6,
    mode: str | None = None, recency: str | None = None,
    domains: list[str] | None = None,
) -> SearchResponse:
    """query + optional mode/recency/domains -> normalized search
    intelligence results, via the USER's own connected provider(s) (§2).

    Raises NoSearchProvider when none is connected (or none compatible with
    `mode`), InvalidSearchRequest for a bad query/mode/recency/domain,
    NoSearchResults when a provider genuinely found nothing, and
    WebSearchUnavailable (or a more specific subclass — see `.state`) for
    everything else. Never fabricates, never falls back to scraping, never
    a Moseisley-owned credential."""
    query = query.strip()
    if not query:
        raise InvalidSearchRequest("empty query")
    if mode is not None and mode not in MODES:
        raise InvalidSearchRequest(f"mode must be one of {MODES}")
    if recency is not None and recency not in RECENCY_VALUES:
        raise InvalidSearchRequest(f"recency must be one of {RECENCY_VALUES}")
    validated_domains = _validate_domains(domains)
    count = max(1, min(count, MAX_COUNT))

    candidates = await _ordered_connections(db, user_id, mode=mode)
    if not candidates:
        raise NoSearchProvider()

    last_error: WebSearchUnavailable | None = None
    for provider, key in candidates:
        logger.info("web search (%s mode=%s recency=%s): %s",
                   provider, mode or "default", recency or "any", query)
        try:
            response = await _DISPATCH[provider](
                query, count, key, mode=mode, recency=recency, domains=validated_domains)
        except httpx.TimeoutException as e:
            err = SearchTimeout(f"{provider} timed out")
            last_error = err
            if len(candidates) > 1:
                logger.info("web search: %s timed out, trying next connected provider", provider)
                continue
            raise err from e
        except WebSearchUnavailable as e:
            last_error = e
            if e.state in _TRANSIENT_STATES and len(candidates) > 1:
                logger.info("web search: %s failed (%s), trying next connected provider",
                           provider, e.state)
                continue
            raise
        except Exception as e:  # noqa: BLE001 — an honest, structured failure beats a crash
            err = WebSearchUnavailable(f"{type(e).__name__} reaching {provider}")
            last_error = err
            if len(candidates) > 1:
                logger.warning("web search: %s failed unexpectedly, trying next provider",
                              provider, exc_info=True)
                continue
            raise err from e
        if not response.results:
            raise NoSearchResults(f"{provider} returned no results for this query")
        return response
    raise last_error or WebSearchUnavailable("no connected search provider could answer")


async def _brave(query: str, count: int, api_key: str, *, mode: str | None = None,
                 recency: str | None = None, domains: list[str] | None = None) -> SearchResponse:
    q = query
    if domains:
        q = f"{query} ({' OR '.join(f'site:{d}' for d in domains)})"
    params: dict = {"q": q, "count": count}
    freshness = _BRAVE_FRESHNESS.get(recency or "")
    if freshness:
        params["freshness"] = freshness
    if mode == "news":
        # Brave's Web Search API can surface a `news` result cluster on this
        # SAME endpoint when result_filter asks for it and the query/plan
        # supports it (api.search.brave.com/app/documentation, verified
        # 2026-08) — there is no separate general-availability news endpoint
        # to call instead. Falls back to plain web results below when the
        # cluster isn't present for this query — never a false guarantee.
        params["result_filter"] = "news,web"
    resp = await _http_get(BRAVE_URL, params=params,
                           headers={"X-Subscription-Token": api_key,
                                    "Accept": "application/json"})
    if resp.status_code != 200:
        raise _classify_http_error(resp, "brave")
    body = _safe_json(resp, "brave")
    news_rows = (body.get("news") or {}).get("results") or []
    web_rows = (body.get("web") or {}).get("results") or []
    rows = (news_rows if mode == "news" and news_rows else web_rows)[:count]
    results = []
    for r in rows:
        url = str(r.get("url") or "")
        if not url:
            continue
        results.append(SearchResult(
            title=str(r.get("title") or "")[:200],
            url=url,
            snippet=_TAGS.sub("", unescape(str(r.get("description") or "")))[:400],
            published_at=str(r.get("page_age") or r.get("age") or "") or None,
            source=_domain_of(url),
        ))
    return SearchResponse(provider="brave", results=results)


async def _tavily(query: str, count: int, api_key: str, *, mode: str | None = None,
                  recency: str | None = None, domains: list[str] | None = None) -> SearchResponse:
    payload: dict = {
        "query": query,
        "max_results": count,
        "search_depth": "advanced" if mode == "research" else "basic",
        "topic": "news" if mode == "news" else "general",
    }
    if recency and recency != "any":
        # Tavily's time_range (api.tavily.com docs, verified 2026-08) accepts
        # day/week/month/year directly — same words we already use.
        payload["time_range"] = recency
    if domains:
        payload["include_domains"] = domains
    resp = await _http_post(
        TAVILY_URL, json=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    if resp.status_code != 200:
        raise _classify_http_error(resp, "tavily")
    body = _safe_json(resp, "tavily")
    rows = (body.get("results") or [])[:count]
    results = []
    for r in rows:
        url = str(r.get("url") or "")
        if not url:
            continue
        score = r.get("score")
        results.append(SearchResult(
            title=str(r.get("title") or "")[:200],
            url=url,
            snippet=str(r.get("content") or "")[:400],
            published_at=str(r.get("published_date") or "") or None,
            source=_domain_of(url),
            score=float(score) if isinstance(score, (int, float)) else None,
        ))
    return SearchResponse(provider="tavily", results=results)


async def _perplexity(query: str, count: int, api_key: str, *, mode: str | None = None,
                      recency: str | None = None, domains: list[str] | None = None) -> SearchResponse:
    # mode/recency/domains: Perplexity's chat-completions-shaped search API
    # has no verified, documented equivalent of Tavily's topic/time_range or
    # Brave's freshness/result_filter — never simulated by stuffing them into
    # the prompt text as a fake filter (§10/§17: no false guarantee).
    # Perplexity always runs its own single search+synthesis regardless.
    resp = await _http_post(
        PERPLEXITY_URL,
        json={"model": PERPLEXITY_MODEL,
              "messages": [{"role": "user", "content": query}]},
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"})
    if resp.status_code != 200:
        raise _classify_http_error(resp, "perplexity")
    body = _safe_json(resp, "perplexity")
    answer = str(((body.get("choices") or [{}])[0].get("message") or {})
                 .get("content") or "").strip() or None

    # search_results is the current field (title/url/date); citations the older
    # plain-URL list. Either way every result carries the URL a finding cites.
    results = [SearchResult(title=str(r.get("title") or r.get("url") or "")[:200],
                            url=str(r.get("url") or ""),
                            snippet=str(r.get("snippet") or "")[:400],
                            published_at=str(r.get("date") or "") or None,
                            source=_domain_of(str(r.get("url") or "")))
               for r in (body.get("search_results") or []) if r.get("url")]
    if not results:
        results = [SearchResult(title=url[:200], url=url, snippet="", source=_domain_of(url))
                   for url in (body.get("citations") or []) if isinstance(url, str)]
    return SearchResponse(provider="perplexity", results=results[:count], answer=answer)


_DISPATCH = {"brave": _brave, "tavily": _tavily, "perplexity": _perplexity}
