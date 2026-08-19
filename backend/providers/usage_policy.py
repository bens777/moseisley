"""Provider spend policy — the hard billing boundary (owner directive
extension, 2026-08; widened 2026-08-18 to also cover LLM calls, not only
non-LLM Intelligence Sources).

Distinct from `factory_pool.AI_MODES` (Rookie/Dev/Expert), which decides WHICH
provider/key a call uses. This decides something orthogonal: given that a
specific resolved call is KNOWN to cost the user money on their own provider
account, is it allowed to proceed at all? ai_mode=EXPERT (custom) means "I
have my own paid keys connected" — it does NOT by itself mean "you may spend
them." Account tier/credit status (e.g. an OpenRouter account that has bought
$10+ of credits) is a THIRD, still-orthogonal axis: it changes OpenRouter's
own free-model *request allowance*, never Moseisley's permission to spend.

FREE_ONLY is a hard stop: Moseisley must not knowingly route to a call that
can generate provider charges, even with a valid paid key connected, even on
an upgraded-tier account, even mid-orchestrator-fallback. It is enforced here,
not just in the UI — every capability call-site must go through
require_paid_capability_allowed() before it can incur provider charges.
FREE_ONLY fails CLOSED: if a call's cost cannot be reliably classified as
free, it is treated as paid (see registry.generate()'s OpenRouter ":free"
check — the only classification the codebase can make with confidence).

Default: FREE_ONLY for every user, unconditionally — deliberately NOT
inferred from ai_mode. An earlier draft defaulted an EXPERT-mode (custom) user
to PAID_ALLOWED for backward compatibility, but ai_mode only records WHICH
key routing uses; it was never a considered "yes, spend my money" decision,
and inferring one from it reopens exactly the loophole §6/§7 rule out —
connecting a key must never imply permission to spend it. A pre-existing
EXPERT user's next paid call is blocked once, with a clear message, until
they explicitly choose "Allow paid usage."

ASK_BEFORE_SPENDING is structured to extend cleanly into the existing
ApprovalRequest system (backend/core/models.py, action_type="spend") without
being fully wired end-to-end here, and is deliberately hidden from normal
user-facing UI until it is — see require_paid_capability_allowed's docstring
for exactly what is and isn't implemented.
"""
from __future__ import annotations

from backend.core.models import User

FREE_ONLY = "free_only"
PAID_ALLOWED = "paid_allowed"
ASK_BEFORE_SPENDING = "ask_before_spending"
POLICIES = (FREE_ONLY, PAID_ALLOWED, ASK_BEFORE_SPENDING)

# Policies a normal user is offered. ASK_BEFORE_SPENDING is real and
# enforced (see require_paid_capability_allowed) but not yet backed by a
# complete approval UI, so it is not surfaced as a choice. Kept as its own
# tuple, not a UI-layer filter, so the API is the one source of truth for
# what's currently a supported CHOICE vs. an internal state.
USER_FACING_POLICIES = (FREE_ONLY, PAID_ALLOWED)

SETTINGS_KEY = "intelligence_source_policy"


class PaidCapabilityBlocked(Exception):
    """FREE_ONLY is active and a capability call would knowingly cost money."""

    def __init__(self, capability: str, provider: str) -> None:
        self.capability = capability
        self.provider = provider
        super().__init__(
            f"[paid_capability_blocked] {capability} on {provider} needs paid usage, "
            "and your usage policy is Free only. Switch to \"Allow paid usage\" "
            "in Connections to use it.")


class ApprovalRequired(Exception):
    """ASK_BEFORE_SPENDING is active and no matching approval exists yet."""

    def __init__(self, capability: str, provider: str) -> None:
        self.capability = capability
        self.provider = provider
        super().__init__(
            f"[approval_required] {capability} on {provider} costs money and your "
            "policy is Ask before spending. Approve it first.")


def get_policy(user: User) -> str:
    value = (user.settings_json or {}).get(SETTINGS_KEY)
    return value if value in POLICIES else FREE_ONLY


def set_policy(user: User, policy: str) -> None:
    if policy not in POLICIES:
        raise ValueError(f"unknown usage policy: {policy}")
    user.settings_json = {**(user.settings_json or {}), SETTINGS_KEY: policy}


def require_paid_capability_allowed(user: User, *, capability: str, provider: str) -> None:
    """Hard gate for a specific resolved call KNOWN to cost the user money on
    their own provider account — an LLM call to a non-free model (e.g. any
    OpenRouter model without a ":free" suffix, or any other LLM provider,
    which has no verifiable free tier) or a non-LLM capability with no free
    tier (e.g. Grok/xAI X search).

    FREE_ONLY: always raises PaidCapabilityBlocked — a hard stop, per §6/§7/§8:
    Moseisley must not knowingly route to a paid capability even with a valid
    key connected, even on an upgraded-tier account.

    PAID_ALLOWED: always permits.

    ASK_BEFORE_SPENDING: currently raises ApprovalRequired unconditionally.
    This is the deliberately-unfinished third state the product spec asked to
    be "structured so it can be added cleanly," not a request to build a full
    approval UI here: the natural next step is for the call site to create an
    ApprovalRequest(action_type="spend", action_payload_json={"capability":
    capability, "provider": provider}) via the existing approvals system
    (backend/core/models.py ApprovalRequest, already resolved through
    /api/approvals) and re-check for an approved match before calling this.
    That wiring is intentionally not included — see the final report. It is
    also not offered as a UI choice yet (see USER_FACING_POLICIES)."""
    policy = get_policy(user)
    if policy == PAID_ALLOWED:
        return
    if policy == ASK_BEFORE_SPENDING:
        raise ApprovalRequired(capability, provider)
    raise PaidCapabilityBlocked(capability, provider)
