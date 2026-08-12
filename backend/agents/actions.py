"""Clickable actions in Manager messages.

The Manager guides by handing the user a button, not by describing where to
click. It writes `[label](action:route_id)` and the web client renders that as
a link — but ONLY for route ids in the whitelist below, which is the single
source of truth (apps/web/lib/actions.ts mirrors it, and a test keeps the two
in step).

Two layers stop a Manager reply from becoming an arbitrary link:
  · here — replies are sanitized before they are stored, so an unknown route id
    degrades to its plain label and never reaches the client as an action;
  · in the client — only the `action:` form is parsed at all, so a normal
    markdown link to a URL stays literal text.
"""
from __future__ import annotations

import re

# route_id -> in-app path. Keep in sync with apps/web/lib/actions.ts.
ACTION_ROUTES: dict[str, str] = {
    "crew_genesis": "/welcome",
    "connections": "/connections",
    "command": "/command",
    "bar": "/bar",
    "settings": "/settings",
    "schedule": "/schedule",
    "data": "/data",
    "security": "/security",
    "skills": "/skills",
    "challenge": "/challenge",
    "trading": "/trading",
    "crew": "/agents",
    "goals": "/goals",
    "projects": "/projects",
    "xray": "/xray",
    "radar": "/market",
    "money": "/money",
}

# [label](action:route_id) — labels are short and single-line by construction.
ACTION_PATTERN = re.compile(r"\[([^\]\n]{1,60})\]\(action:([a-z_]{1,32})\)")


def is_known(route_id: str) -> bool:
    return route_id in ACTION_ROUTES


def sanitize(text: str) -> str:
    """Drop action links the whitelist doesn't know, keeping their label text."""
    return ACTION_PATTERN.sub(
        lambda m: m.group(0) if is_known(m.group(2)) else m.group(1), text)


def found_in(text: str) -> list[str]:
    """Whitelisted route ids referenced by a message (for tests and ledger)."""
    return [rid for _label, rid in ACTION_PATTERN.findall(text) if is_known(rid)]


def prompt_block() -> str:
    """The action vocabulary, injected into the Manager's system prompt."""
    lines = "\n".join(f"- action:{rid} → {path}" for rid, path in ACTION_ROUTES.items())
    return (
        "## CLICKABLE ACTIONS (the only links you may write)\n"
        "Write a next step as [short label](action:route_id) and the user gets a button.\n"
        "Use one when it is the concrete next step — never more than two per message.\n"
        "Any other link form is stripped before the user sees it: never write a URL,\n"
        "a bare path, or an action id that is not in this list.\n"
        f"{lines}"
    )
