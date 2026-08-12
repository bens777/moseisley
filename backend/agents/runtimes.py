"""RUNTIME CATALOG — the honest profile of every agent runtime in this build.

Single source of truth. The create-agent wizard, the Runtimes reference on the
Crew page and the Manager's platform knowledge all read from here, and
CREATABLE_TYPES is derived from it, so a runtime cannot be advertised as
available while the API refuses it.

Every bullet below describes behaviour that exists in this repository today:
  · native      → backend/agents/native/chat.py, via the orchestrator tool loop
  · custom_http → backend/agents/adapters/custom_http.py
  · openclaw    → backend/agents/adapters/openclaw.py
  · hermes      → no adapter module exists; creation is refused

Nothing here is aspirational. If an adapter gains a capability, this file
changes in the same commit.
"""
from __future__ import annotations

AVAILABLE = "available"
BLOCKED = "blocked"

RUNTIME_CATALOG: list[dict] = [
    {
        "id": "native",
        "name": "Native",
        "status": AVAILABLE,
        "blocked_reason": None,
        "summary": "Runs on the platform — nothing to configure.",
        "best_for": [
            "Everything, unless you already run your own agent elsewhere. It is the "
            "default and the only runtime with the platform's full tool loop.",
            "Work that has to create goals, read and write memory, delegate to the crew "
            "or draft automations.",
            "Starting today: no endpoint, no token, no gateway to stand up.",
        ],
        "security": [
            "Runs inside the platform. Nothing is sent to a third-party agent host.",
            "The only outbound calls are to the model provider your AI mode already uses.",
            "The Emergency Stop halts it, like every other execution path.",
        ],
        "weak_points": [
            "Only as good as the model your current AI mode gives it.",
            "You cannot run your own agent framework or code inside it — that is what "
            "the other two runtimes are for.",
            "Its health always reads \"ok\": there is no separate service to probe.",
        ],
    },
    {
        "id": "custom_http",
        "name": "Custom HTTP",
        "status": AVAILABLE,
        "blocked_reason": None,
        "summary": "Your own agent, behind an HTTP endpoint you control.",
        "best_for": [
            "An agent you already run — any framework, any language — reachable over HTTP.",
            "Keeping your agent's own logic and data where they already live.",
            "The documented escape hatch when no first-class runtime fits.",
        ],
        "security": [
            "Your auth header value is encrypted at rest and sent only to the endpoint "
            "you configured.",
            "No provider keys, OAuth tokens or Telegram credentials are ever forwarded. "
            "The payload is your message plus a sanitized context: focus, ideal state, "
            "a goals snapshot, your display name and timezone.",
            "The platform cannot vouch for what your endpoint does with that context. "
            "The URL is used exactly as you enter it — including plain http:// and "
            "addresses inside your own network — and replies come back as untrusted text.",
        ],
        "weak_points": [
            "No access to platform tools. It can answer with text, but it cannot create "
            "goals, write memory, delegate to the crew or save an automation.",
            "Any failure — unreachable, non-2xx, non-JSON, or a reply without a "
            "recognised text field — silently falls back to the Native agent and marks "
            "this runtime unhealthy.",
            "Its model usage happens on your side, so it never appears in the platform's "
            "token and cost figures.",
        ],
    },
    {
        "id": "openclaw",
        "name": "OpenClaw",
        "status": AVAILABLE,
        "blocked_reason": None,
        "summary": "An OpenClaw gateway you already run.",
        "best_for": [
            "Reusing an OpenClaw gateway as a reasoning worker over its "
            "OpenAI-compatible chat completions API.",
            "Keeping OpenClaw's own model setup while goals, memory and permissions "
            "stay with the platform.",
            "Gateways on your own machine: the default target is http://localhost:18789.",
        ],
        "security": [
            "The gateway token is encrypted at rest and sent as a bearer token only to "
            "the base URL you configured.",
            "Less leaves the platform than with Custom HTTP: only your message and the "
            "Focus document, not the full context.",
            "OpenClaw's own channel integrations stay unused, and its replies are "
            "treated as untrusted text.",
        ],
        "weak_points": [
            "No access to platform tools — text replies only, like any external runtime.",
            "Any failure falls back to the Native agent and marks this runtime unhealthy.",
            "Only chat completions are used. OpenClaw's sessions, tools and file handling "
            "are not wired to anything here.",
        ],
    },
    {
        "id": "hermes",
        "name": "Hermes",
        "status": BLOCKED,
        "blocked_reason": "No stable HTTP API yet",
        "summary": "Not connectable in this build.",
        "best_for": [
            "Nothing yet — creating one is refused.",
            "If you run Hermes today, put your own HTTP wrapper in front of it and "
            "connect that as Custom HTTP.",
        ],
        "security": [
            "Not applicable: no Hermes code runs in this build.",
        ],
        "weak_points": [
            "The hermes-agent gateway is channel-oriented and exposes no stable "
            "request/reply HTTP API to adapt.",
            "Blocked in the API, not just hidden in the UI: creating one returns 400.",
        ],
    },
]

BY_ID: dict[str, dict] = {r["id"]: r for r in RUNTIME_CATALOG}


def creatable_ids() -> set[str]:
    """The runtimes POST /agents accepts — derived, never a second hand-kept list."""
    return {r["id"] for r in RUNTIME_CATALOG if r["status"] == AVAILABLE}


def blocked_reason(runtime_id: str) -> str | None:
    return (BY_ID.get(runtime_id) or {}).get("blocked_reason")


def reference_block() -> str:
    """The catalog as prompt context, so the Manager can answer "which agent type
    should I use?" from the same facts the cards show."""
    lines = ["## AGENT RUNTIMES (what a new agent can run on — answer from this only)"]
    for r in RUNTIME_CATALOG:
        state = ("available" if r["status"] == AVAILABLE
                 else f"BLOCKED — {r['blocked_reason']}")
        lines.append(f"\n**{r['name']}** (`{r['id']}`, {state}) — {r['summary']}")
        lines.append("  best for: " + " ".join(r["best_for"]))
        lines.append("  security: " + " ".join(r["security"]))
        lines.append("  weak points: " + " ".join(r["weak_points"]))
    lines.append("\nNever offer a blocked runtime as if it worked. When someone has no "
                 "agent of their own to plug in, Native is the answer. "
                 "New agents are created from the Crew page. → [Crew](action:crew)")
    return "\n".join(lines)
