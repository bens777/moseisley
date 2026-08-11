# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Write to **<cantina@moseisley.sh>** with:

- what you found and where (file, endpoint, or a short reproduction),
- what an attacker could do with it,
- anything you already know about the fix.

You'll get an acknowledgement within a few days. If the issue is confirmed,
you'll be told when a fix ships, and credited in the release notes unless you
prefer otherwise.

Please give a reasonable window to fix things before disclosing publicly, and
don't run tests against the hosted service at moseisley.sh — use your own
self-hosted install.

## Scope

In scope: anything in this repository — the FastAPI backend, the Next.js
dashboard, the agent/tool boundary, authentication and session handling, secret
storage, the permission/budget enforcement code, and the deployment manifests.

Out of scope: vulnerabilities in third-party AI providers themselves, findings
that require an already-compromised host or database, and reports produced
solely by an automated scanner with no demonstrated impact.

## What Moseisley already assumes

These are deliberate design properties. If you find a way around one of them,
that is a vulnerability worth reporting:

- Provider API keys are AES-256-GCM encrypted at rest, never returned by any
  API after saving, never logged, and never shown to agents.
- The platform's own AI key (`FACTORY_OPENROUTER_API_KEY`, hosted deployments
  only) is read from configuration, never stored in the database, and never
  serialized into any response.
- Kill switches, budgets, spending limits and permission checks are
  deterministic server-side code. No prompt, tool output or model response can
  override them.
- Tenant isolation: a user can never read or mutate another user's data.
- The ledger is append-only; historical events cannot be edited or deleted.

## Self-hosting hardening

If you run your own instance: set a strong `POSTGRES_PASSWORD`, generate a real
`MASTER_ENCRYPTION_KEY` (32 random bytes) and `APP_SECRET`, don't expose the
API port directly to the internet without TLS in front of it, and keep `.env`
out of version control — `.gitignore` already covers it.
