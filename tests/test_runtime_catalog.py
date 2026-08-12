"""The runtime catalog must describe reality, and stay the only list.

The danger with a catalog like this is drift: a card advertising a runtime the
API refuses, or a profile that outlives the adapter it describes. These tests
pin the catalog to the code it claims to document.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.agents import runtimes
from backend.agents.adapters.base import ADAPTER_TYPES
from backend.api.routes.agents import CREATABLE_TYPES

WEB = Path(__file__).resolve().parents[1] / "apps" / "web"


# ── shape ───────────────────────────────────────────────────────────

async def test_catalog_endpoint_returns_every_runtime_in_full(client, auth):
    body = (await client.get("/api/agents/runtimes", headers=auth)).json()
    got = body["runtimes"]
    assert [r["id"] for r in got] == ["native", "custom_http", "openclaw", "hermes"]

    for r in got:
        assert r["name"] and r["summary"]
        assert r["status"] in ("available", "blocked")
        for section in ("best_for", "security", "weak_points"):
            assert 1 <= len(r[section]) <= 3, (r["id"], section)
            assert all(isinstance(b, str) and b.strip() for b in r[section])
        if r["status"] == "blocked":
            assert r["blocked_reason"]
        else:
            assert r["blocked_reason"] is None


async def test_catalog_needs_a_login(client):
    assert (await client.get("/api/agents/runtimes")).status_code == 401


# ── the catalog is the source of truth, not a second opinion ────────

def test_available_runtimes_are_exactly_what_the_api_accepts():
    assert CREATABLE_TYPES == runtimes.creatable_ids()
    assert CREATABLE_TYPES == {"native", "custom_http", "openclaw"}


def test_every_available_external_runtime_has_a_real_adapter():
    """native is handled by the router itself; the others need adapter classes."""
    external = runtimes.creatable_ids() - {"native"}
    assert external <= set(ADAPTER_TYPES), "catalog offers a runtime with no adapter"
    assert external == set(ADAPTER_TYPES), "an adapter exists that the catalog omits"


def test_hermes_is_blocked_because_no_adapter_exists():
    assert "hermes" not in ADAPTER_TYPES
    hermes = runtimes.BY_ID["hermes"]
    assert hermes["status"] == "blocked"
    assert hermes["blocked_reason"] == "No stable HTTP API yet"


# ── blocked stays blocked ───────────────────────────────────────────

async def test_blocked_runtime_is_not_creatable(client, auth):
    resp = await client.post("/api/agents", headers=auth, json={
        "adapter_type": "hermes", "display_name": "Hermes"})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "custom_http" in detail                      # the existing escape hatch
    assert "no stable http api yet" in detail.lower()   # the catalog's own reason
    assert (await client.get("/api/agents", headers=auth)).json() == [
        a for a in (await client.get("/api/agents", headers=auth)).json()
        if a["adapter_type"] != "hermes"]


@pytest.mark.parametrize("runtime_id", ["native", "custom_http", "openclaw"])
async def test_every_available_runtime_is_actually_creatable(client, auth, runtime_id):
    config = {"endpoint": "https://agent.example/run"} if runtime_id == "custom_http" else {}
    resp = await client.post("/api/agents", headers=auth, json={
        "adapter_type": runtime_id, "display_name": f"Test {runtime_id}",
        "configuration": config})
    assert resp.status_code == 200, resp.text
    assert resp.json()["adapter_type"] == runtime_id


# ── honesty: the profiles say the things that are true ──────────────

def test_external_runtimes_disclose_the_fallback_and_the_missing_tools():
    """Both facts are surprising and both are real: adapters get no tool loop,
    and any failure silently answers with the native agent instead."""
    for runtime_id in ("custom_http", "openclaw"):
        weak = " ".join(runtimes.BY_ID[runtime_id]["weak_points"]).lower()
        assert "no access to platform tools" in weak, runtime_id
        assert "falls back to the native agent" in weak, runtime_id


def test_external_runtimes_disclose_untrusted_replies():
    for runtime_id in ("custom_http", "openclaw"):
        security = " ".join(runtimes.BY_ID[runtime_id]["security"]).lower()
        assert "untrusted text" in security, runtime_id


def test_custom_http_does_not_promise_a_vetted_endpoint():
    security = " ".join(runtimes.BY_ID["custom_http"]["security"]).lower()
    assert "cannot vouch" in security
    assert "http://" in security          # plain http really is accepted


def test_native_does_not_claim_to_be_offline():
    """It runs in-platform, but it still calls a model provider — say so."""
    security = " ".join(runtimes.BY_ID["native"]["security"]).lower()
    assert "model provider" in security
    assert "emergency stop" in security


def test_no_marketing_superlatives_anywhere():
    banned = ["best-in-class", "seamless", "blazing", "world-class", "cutting-edge",
              "effortless", "unlimited", "military-grade", "bank-grade", "100% secure",
              "fully secure", "enterprise-grade"]
    blob = " ".join(
        " ".join([r["summary"], *r["best_for"], *r["security"], *r["weak_points"]])
        for r in runtimes.RUNTIME_CATALOG).lower()
    for word in banned:
        assert word not in blob, word


# ── the Manager answers from the same catalog ───────────────────────

def test_reference_block_carries_every_runtime_and_its_state():
    block = runtimes.reference_block()
    for r in runtimes.RUNTIME_CATALOG:
        assert r["name"] in block
        assert f"`{r['id']}`" in block
    assert "BLOCKED — No stable HTTP API yet" in block
    assert "(action:crew)" in block


def test_reference_block_only_uses_whitelisted_actions():
    from backend.agents import actions

    ids = {rid for _label, rid in actions.ACTION_PATTERN.findall(runtimes.reference_block())}
    assert ids and ids <= set(actions.ACTION_ROUTES)


# ── the cards render the catalog, and only the catalog ──────────────

def test_wizard_and_reference_render_from_the_endpoint():
    wizard = (WEB / "components" / "agent-wizard.tsx").read_text(encoding="utf-8")
    assert '"/agents/runtimes"' in wizard
    assert "<RuntimeCard" in wizard
    # the old hardcoded three-item list is gone for good
    assert "const TYPES" not in wizard

    page = (WEB / "app" / "agents" / "page.tsx").read_text(encoding="utf-8")
    assert '"/agents/runtimes"' in page and "<RuntimeReference" in page


def test_cards_disable_blocked_runtimes_and_show_the_reason():
    card = (WEB / "components" / "runtimes.tsx").read_text(encoding="utf-8")
    assert 'const blocked = runtime.status !== "available"' in card
    assert "const selectable = !blocked && !!onSelect" in card
    assert "onClick={selectable ?" in card          # a blocked card cannot be picked
    assert "aria-disabled={blocked || undefined}" in card
    assert "{runtime.blocked_reason" in card        # and it says why
    # no profile text is authored in the client
    for phrase in ("Best for", "Security", "Weak points"):
        assert phrase in card                       # labels only…
    assert not re.search(r"runs on the platform|your own agent behind", card), \
        "runtime copy must come from the backend catalog"
