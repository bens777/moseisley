# Operating Moseisley

Everything beyond "it runs": local development without Docker, the AI mode
system and the model pools you can edit, and the optional commercial layer
(subscriptions, fuel caps, The Bar) that only activates when you configure
Stripe. A plain self-hosted install needs none of it.

## Quick start (local, no Docker)

```bash
# backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # set APP_SECRET, MASTER_ENCRYPTION_KEY; sqlite is fine locally
DATABASE_URL=sqlite+aiosqlite:///./mychief.db .venv/bin/alembic upgrade head
.venv/bin/uvicorn backend.api.app:app --port 8000
# worker (second terminal)
.venv/bin/python -m backend.worker.main
# frontend (third terminal)
cd apps/web && npm install && npm run dev   # http://localhost:3000
```

Register an account in the dashboard, then in **Connections** add provider API keys
(or the *Mock* provider + *demo data* to try everything offline), and run your **X-Ray**.

## Self-hosted deployment (Docker)

```bash
cp .env.example .env   # set APP_SECRET, MASTER_ENCRYPTION_KEY, POSTGRES_PASSWORD
docker compose up -d --build
docker compose exec api alembic upgrade head
```

Services: `postgres`, `api` (:8000), `worker`, `web` (:3000), optional `mailpit` (dev profile).

## Factory mode (platform AI: 14-day trial, then paid or BYOK)

Operators can ship working AI at signup with a single env var:

```bash
FACTORY_OPENROUTER_API_KEY=sk-or-...   # one OpenRouter key, PREPAID credits
```

- **Trial** (first `FACTORY_TRIAL_DAYS`, default 14): platform AI included,
  `FACTORY_TRIAL_DAILY_REQUESTS` requests/user/day (default 40).
- **Paid** (via Stripe): platform AI included, with a plan-sized tank —
  Basic $9 → `FACTORY_BASIC_DAILY_REQUESTS`/day (default 150), Pro $19 →
  `FACTORY_PRO_DAILY_REQUESTS`/day (default 400). Any other plan string falls
  back to the Basic cap. The pre-split `FACTORY_PAID_DAILY_REQUESTS` is
  deprecated: if it is still set it applies to both plans and logs a warning at
  startup, so existing deployments keep booting unchanged.
- **Expired**: platform AI is cut; users get exactly three options — subscribe,
  bring their own keys (Connections), or connect self-hosted Ollama (custom
  provider with a base URL).

Zero-maintenance design: prepaid OpenRouter credits are the hard spend
ceiling — top up when OpenRouter emails a low-balance notice. If credits run
dry, users see a calm "generator is recharging" message and BYOK keeps
working. The model list is the `FACTORY_MODELS` constant at the top of
`backend/providers/factory_pool.py` — editing that block is the only
operational task there is. The key is never stored in the database and never
returned by any API. Self-host default (key unset): factory mode simply
doesn't exist and the product is BYOK-only, unchanged.

Operator note: factory traffic is processed by OpenRouter and the routed model
vendors (DeepSeek et al.) — disclose this third-party AI processing in your
privacy policy.

### The three AI modes (ROOKIE / DEV / EXPERT)

Exactly one mode is active per user. The internal `ai_mode` values in
`settings_json` are `factory` / `dev` / `custom` — the names below are the
user-facing labels only.

| Mode | Internal | Runs on | Who can use it |
|---|---|---|---|
| **ROOKIE** — AI included · zero setup | `factory` | the platform OpenRouter key, daily fuel cap | 14-day trial, then Basic/Pro |
| **DEV** — your OpenRouter key · free models | `dev` | the user's own OpenRouter key, `:free` models only | everyone, always (trial, expired, unsubscribed) |
| **EXPERT** — all providers · your keys | `custom` | any provider key, any model, Ollama | subscribers (and every self-hosted install) |

DEV is enforced server-side: in dev mode every call goes through the user's own
OpenRouter connection, and any non-free model is silently swapped for one from
the `DEV_MODELS` pool in `backend/providers/factory_pool.py` (same bucket shape
and 429/5xx fallback as the factory pool). No platform key is ever touched, no
fuel is counted, and their quota is whatever OpenRouter gives their account.
Without a connected key, dev-mode calls raise `[dev_key_missing]` (HTTP 424).

Because DEV costs the platform nothing, **connecting an OpenRouter key never
requires a subscription** — every other provider stays subscriber-gated on
hosted deployments. Routing remains the real gate: a trial user with an
OpenRouter key can use it via DEV (free models only); EXPERT still needs a pass.

### Hosted vs self-hosted: who can bring their own keys

On the **hosted** platform (Stripe billing configured), the free tier is the
14-day trial and it runs on platform AI: provider-key inputs are visible but
disabled, the factory↔custom toggle is hidden, and connecting your own keys —
including a self-hosted Ollama through the custom provider — is a Basic/Pro
feature. When the trial ends without a subscription there are two honest
options: subscribe, or run Moseisley yourself. Subscribing unlocks BYOK
instantly; any keys stored before subscribing are kept untouched and start
working the moment the subscription is active.

**Self-hosting is never gated.** With Stripe unconfigured, BYOK, Ollama and the
toggle all behave as they always have — free, unlimited, no subscription
concept at all. Gating follows the existing entitlements rule: it exists only
where billing is configured.

### Managing a subscription (Stripe Customer Portal)

Paying users get a muted **"Manage subscription · cancel anytime"** link in
Settings → Billing. It opens a Stripe Customer Portal session — the only place
subscriptions are canceled, switched between Basic and Pro, or repaid with a
new card; Moseisley never handles card details. Set
`STRIPE_PORTAL_RETURN_URL` (default: `FRONTEND_ORIGIN` + `/settings`) for where
Stripe returns the user afterwards, and enable the portal once in the Stripe
dashboard under **Settings → Billing → Customer portal**.

Whatever the user does there arrives back through the existing verified
webhook: `customer.subscription.updated` (plan change, past_due, reactivation)
and `customer.subscription.deleted` (cancellation → plan drops to Community)
are synced authoritatively from Stripe, so entitlements and factory fuel caps
follow within one webhook round-trip.

### The Bar (one-time fuel top-ups)

Signed-in users can buy extra factory requests as drinks — never advertised on
the public site or pricing page:

| Drink | Price | Fuel |
|---|---|---|
| Nebula Ale | $2 | +50 requests for the buyer |
| Purple Tentacle Punch | $5 | +150 requests for the buyer |
| Buy a Round | $5 | +100 requests gifted to a friend in the cantina |

Create three **one-time** Prices in Stripe and set `STRIPE_PRICE_ID_ALE`,
`STRIPE_PRICE_ID_PUNCH`, `STRIPE_PRICE_ID_ROUND`. Any of them unset → the bar
is closed and the rest of the product is unaffected.

Rules: purchased fuel **never expires**; it is spent only after the day's
included allowance is used, and only by platform-AI (factory) calls — BYOK
traffic never touches it. It works for every tier, so a user whose trial ended
can buy a drink and keep running. Fuel is credited **only** from the verified
`checkout.session.completed` webhook (idempotent on the Stripe session id),
never from the browser redirect. The menu is the `BAR_MENU` constant in
`backend/billing/stripe_billing.py`.

