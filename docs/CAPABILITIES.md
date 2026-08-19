# Capabilities

What's actually in this repository, honestly labeled. Built by reading the
code, not by copying a roadmap — if a row says **Available**, you can find it
under `backend/` or `apps/web/` today.

Three states:

- **Available** — shipped, working, in this codebase right now.
- **Planned** — a real Moseisley product feature that exists in the broader
  product but has not landed in Community yet. Not a promise of a date.
- **Hosted-only** — code that exists in this repo to power
  [moseisley.sh](https://moseisley.sh), the managed service. It's inert
  without Stripe/platform credentials you won't have as a self-hoster, and
  it isn't something Community is missing — it's the plumbing for a
  different product (see [README.md](../README.md#license)).

## Goals & agents

| Capability | Status | Notes |
|---|---|---|
| Natural-language goal compiler | Available | `backend/life_kernel/goal_compiler.py` — a metric, target and deadline from plain language |
| World Model / Focus | Available | deterministic situational context handed to every agent (`world_model.py`, `focus.py`) |
| Crew delegation | Available | Orchestrator, Strategist, Challenger, X-Ray, Radar, Auditor, Follow-Up, Commitment Tracker, Inbox Triage, Dev Agent — bounded, on-demand (`backend/agents/crew.py`) |
| Operational goals (Plan → Milestones → Actions) | Planned | today a goal is a target you track, not a broken-down execution plan |
| Autonomous Goal Runner | Planned | scheduler-driven continuation/replanning of a goal without a manual trigger each time |
| Unified attention/status layer | Planned | one surface for "what needs me, what's running, what's next" across every goal |

## Memory

| Capability | Status | Notes |
|---|---|---|
| Structured key-value memory | Available | facts/preferences/beliefs/predictions/decisions, provenance-tracked (`backend/life_kernel/memory.py`) |
| File references + optional retrieval chunks | Available | `FileRef`/`DocumentChunk` — BYOS metadata and an embeddings-ready chunk table, unused by default |
| Personal Vault (document OCR/classification/retrieval) | Planned | attach-and-remember for invoices, contracts, letters with searchable extraction |
| Semantic/vector memory search | Planned | current retrieval, where it exists, is lexical — this repo does not claim vector search |

## AI providers & BYOK

| Capability | Status | Notes |
|---|---|---|
| BYOK across 7 providers + custom endpoint | Available | Anthropic, OpenAI, Gemini, xAI, Mistral, DeepSeek, OpenRouter, plus any OpenAI-compatible `base_url` (`backend/providers/registry.py`) |
| Local models via a self-hosted endpoint | Available | point the Custom provider at your own Ollama (or anything OpenAI-compatible) |
| OpenRouter as the free path | Available | DEV mode — `:free` models, no subscription, works self-hosted or hosted |
| YouTube / X / general document / general audio "Intelligence" tools | Planned | today's Audio module is speech-to-text/text-to-speech for voice input and Telegram, not a general transcribe-any-file or analyze-any-video tool |

## Integrations

| Capability | Status | Notes |
|---|---|---|
| MCP client | Available | connect an external Model Context Protocol server as a tool/data source (`backend/integrations/mcp/client.py`) |
| MCP server (expose Moseisley's own tools) | Planned | not started |
| Google (Gmail + Calendar) | Available | OAuth connection, deterministic Tool Broker enforcement |
| S3-compatible storage (BYOS) | Available | `backend/storage/s3.py` — any S3-compatible endpoint, not AWS-only |
| Webhooks | Available | `backend/integrations/webhook` |
| Telegram gateway | Available | polling (self-host) or webhook (hosted), voice notes in and out |
| A2A protocol | Planned | not started |

## Operations & safety

| Capability | Status | Notes |
|---|---|---|
| Tool Broker + Policy Engine | Available | the one gate between an agent and any real integration (`backend/integrations/broker.py`, `backend/policies/engine.py`) |
| Kill switches | Available | deterministic, checked at execution boundaries |
| Treasury / spend policy / budgets | Available | bounded experiments, approval-gated spend |
| Append-only Ledger | Available | every meaningful state change, immutable |
| Tenant isolation | Available | a user can never read or mutate another user's data |
| X-Ray | Available | scans your last 90 days of connected data for unpaid invoices, dropped leads, recoverable time |
| Market Radar | Available | scheduled external market-intelligence sweeps |
| Schedule | Available | one table for everything that recurs |

## Hosted-only (not something Community is missing)

| Capability | Status |
|---|---|
| Stripe billing, subscription plans, trial tiers | Hosted-only |
| Platform-funded "ROOKIE" AI (`FACTORY_OPENROUTER_API_KEY`) | Hosted-only |
| Managed infrastructure, backups, uptime | Hosted-only |

None of this is gated code you're missing — it's genuinely inert without
Stripe/platform credentials, and self-hosting never needs it. See the
[Quickstart](../README.md#quickstart--self-host-in-five-minutes) and
[`docs/operations.md`](operations.md) for what self-hosting actually requires.

## Explicitly not in this repository

IoT/wearables, an "Academy" learning product, creator revenue-sharing,
automated business-to-agent audits, and full-duplex realtime voice do not
exist here in any form — not planned, not started, not present as dormant
code.
