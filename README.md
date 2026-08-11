# Moseisley

**A cantina of AI agents that work for you.**

> Walk in, give the crew a mission, and let them get on with it. The Orchestrator
> runs the room, the Strategist plans the route, Radar watches the horizon, X-Ray
> digs through your last 90 days, the Challenger argues back, and the Guardian
> refuses to spend your money without asking. You keep one big red button.

Moseisley is a self-hosted AI command center: a persistent control layer that
understands what you want (Goal Compiler), models your current reality (World
Model), analyzes your history (X-Ray), decides what deserves attention (Daily
Strategist + Challenger), watches the market (Market Radar), runs bounded
experiments with deterministic budgets (Experiment Engine + Treasury), executes
permitted actions through a policy boundary (Tool Broker), and audits itself
(Auditor + append-only Ledger).

**Design objective:** maximum useful autonomy under explicit deterministic user
constraints. LLMs reason; deterministic code decides on money, permissions and
safety. Always.

**Don't want to run it yourself?** A hosted cantina is open at
**<https://moseisley.sh>** — skip the setup, 14-day free trial, AI included.

---

## Quickstart — self-host in five minutes

```bash
git clone <your-fork-or-this-repo> moseisley && cd moseisley
cp .env.example .env
```

Edit `.env` — only three values actually matter to get running:

| Variable | What to put |
|---|---|
| `APP_SECRET` | any long random string |
| `MASTER_ENCRYPTION_KEY` | 32 bytes, base64: `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"` |
| `POSTGRES_PASSWORD` | any strong password (compose passes it to the database) |

Then:

```bash
docker compose up -d --build
docker compose exec api alembic upgrade head
```

Open <http://localhost:3000>, register an account, and go to **Connections** to
add an AI provider key — or connect the *Mock* provider plus *demo data* to try
the whole product offline first.

**Everything else in `.env.example` is optional.** Telegram, Google, S3, email
and Stripe all degrade gracefully when unset: the feature simply isn't there,
nothing crashes.

### The self-host default: DEV and EXPERT modes

Moseisley has three AI modes. On a self-hosted install you get the two that
matter, free and unlimited:

- **DEV** — your own OpenRouter key, restricted to `:free` models. Costs you
  nothing beyond what OpenRouter gives your account.
- **EXPERT** — any provider, any model, including a local Ollama through the
  custom provider (`base_url`, e.g. `http://localhost:11434/v1`).
- **ROOKIE** — platform-provided AI. This only exists if the operator sets
  `FACTORY_OPENROUTER_API_KEY`, which is how the hosted service funds a trial.
  **Leave it unset when self-hosting** — that is the correct default, and the
  subscription gating that comes with it stays switched off entirely.

With no `STRIPE_API_KEY` configured, there are no plans, no trial and no
gating: every feature is yours.

## Architecture (self-hosted, provider-independent)

```
Next.js dashboard ── FastAPI api ── PostgreSQL (canonical state)
Telegram ─────────── Moseisley.sh Gateway ┘        └ append-only Ledger
                     worker (scheduler, market radar, strategist, telegram polling)
LLM providers (OpenAI / xAI / Anthropic / custom / mock)  → replaceable adapters
Agents (Native / Custom HTTP / OpenClaw)                  → replaceable workers
Storage (local disk / any S3-compatible / BYOS buckets)   → StorageAdapter
Email (any SMTP / Mailpit / console)                      → EmailProvider
```

- Authentication is **application-owned** (email+password, verification, reset, DB-backed
  sessions, rate limiting) — no external identity SaaS.
- Secrets are AES-256-GCM encrypted at rest; agents never see credentials.
- Treasury, permissions, kill switches and budgets are deterministic code an LLM cannot override.

## External accounts (all optional; everything degrades gracefully)

| Feature | Requirement | Setup |
|---|---|---|
| LLM reasoning | OpenAI / xAI / Anthropic / any OpenAI-compatible key | paste in Connections |
| Telegram | bot token from @BotFather | `TELEGRAM_BOT_TOKEN`; self-host: `TELEGRAM_MODE=polling` (no public URL needed); cloud: set webhook to `/api/telegram/webhook` with `TELEGRAM_WEBHOOK_SECRET` |
| Voice notes | STT-capable provider key (OpenAI-compatible) or local Whisper | automatic once provider configured |
| Gmail/Calendar | Google Cloud OAuth client (Gmail API + Calendar API enabled) | `GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI`, then Connections → Google |
| BYOS storage | any S3-compatible bucket | Connections → integration type `s3` |
| Real payments | Stripe account (test mode first) | feature-flagged OFF by default; Treasury simulator needs nothing |

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

~100 tests cover tenant isolation, secret non-exposure, deterministic Treasury (€12 auto /
€70 approval / €150 deny / spending-off deny), kill switches, ledger immutability, prompt
injection inertness, scheduler locking/idempotency, agent adapter sanitization, auth flows.

## Documentation

- [`docs/operations.md`](docs/operations.md) — running it: AI modes and the model
  pools you can edit, subscriptions and the Stripe portal, The Bar, fuel caps,
  local development without Docker.
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) — dependency licenses.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to run the stack and send a patch.
- [`SECURITY.md`](SECURITY.md) — reporting a vulnerability (please don't open a
  public issue).

## License

Moseisley is **fair-code**, released under the
[Moseisley Sustainable Use License](LICENSE.md).

**Free, forever:** use it, modify it, self-host it for yourself, your team or
your company — including commercially, inside your own organisation — and
redistribute your changes under the same license.

**Not permitted:** offering Moseisley to third parties as a hosted or managed
service, removing license notices, or branding a commercial derivative as
Moseisley. Commercial licenses for hosting are available —
<mailto:cantina@moseisley.sh>.

The plain-English summary at the top of [LICENSE.md](LICENSE.md) is not legal
advice; the license text governs.

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
Contributions are accepted under the project license, and contributors keep
their copyright while granting the maintainer the right to relicense (this is
what keeps the hosted service able to fund the project).
