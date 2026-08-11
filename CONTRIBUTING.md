# Contributing to Moseisley

Thanks for walking in. Patches, bug reports and honest criticism are all
welcome — the Challenger is a crew member for a reason.

## Running the stack locally

**With Docker (closest to production):**

```bash
cp .env.example .env          # set APP_SECRET + MASTER_ENCRYPTION_KEY
docker compose up -d --build
docker compose exec api alembic upgrade head
```

**Without Docker (fastest loop):**

```bash
# backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DATABASE_URL=sqlite+aiosqlite:///./mychief.db .venv/bin/alembic upgrade head
.venv/bin/uvicorn backend.api.app:app --port 8000

# worker (second terminal)
.venv/bin/python -m backend.worker.main

# frontend (third terminal)
cd apps/web && npm install && npm run dev      # http://localhost:3000
```

Connect the **Mock** provider plus **demo data** in *Connections* and the whole
product works offline, with no API keys and no spend.

## Tests

```bash
.venv/bin/python -m pytest -q            # full suite
.venv/bin/python -m ruff check backend tests
cd apps/web && npx tsc --noEmit && npx next build
```

Everything must be green before a PR. Tests use an in-memory SQLite database
and mock LLM clients — they never touch the network or a real provider. If you
add a code path that can spend money or call a provider, add a test that proves
it doesn't when it shouldn't.

## Code style

- **Python:** ruff, line length **120**, target py311, rules `E,F,I,UP,B`.
  Run `ruff check --fix` before pushing.
- **TypeScript/React:** the existing conventions — function components, no
  external UI libraries, Tailwind with the project's design tokens
  (`--color-*` in the dashboard, `--cw-*` inside `.cantina`). Don't introduce a
  component library or a state manager for a two-line problem.
- **Comments** explain *why*, not *what*. The codebase is deliberately light on
  narration and heavy on intent.
- Deterministic code decides money, permissions and safety. An LLM never does.
  Keep that boundary intact.

## Pull requests

- **Focused diffs.** One concern per PR. A PR that reskins a page *and* changes
  routing logic will be asked to split.
- **State the scope** in the description: which files, and why each one had to
  change.
- **Tests pass**, and new behaviour comes with new tests.
- **No secrets**, ever — no keys, tokens, real endpoints or personal data in
  code, fixtures, comments or screenshots. Test fixtures use obviously fake
  values like `sk-test-1234567890abcdef`.
- **No behaviour changes smuggled into refactors.** If a rename changes what the
  software does, say so out loud.
- Screenshots for UI changes are appreciated (both breakpoints for anything in
  the dashboard shell or the public site).

## Contribution licensing (inbound = outbound)

Moseisley is fair-code, released under the
[Moseisley Sustainable Use License](LICENSE.md).

By submitting a contribution you agree that:

1. Your contribution is licensed to the project under that same license
   (inbound = outbound), and
2. **you keep your copyright**, while granting the maintainer a perpetual,
   worldwide, royalty-free, irrevocable license to use, modify, distribute and
   **relicense** your contribution — including as part of a commercially
   licensed version of Moseisley.

That second point is what lets the hosted service at <https://moseisley.sh> fund
the project and offer commercial licenses. There is no CLA bot and no form to
sign: opening a pull request is the agreement. If you are contributing on behalf
of an employer, make sure you're allowed to.

## Reporting security issues

Please don't open a public issue — see [SECURITY.md](SECURITY.md).
