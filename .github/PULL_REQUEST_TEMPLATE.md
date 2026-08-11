## What this changes

<!-- One paragraph. What behaviour is different after this PR? -->

## Scope

<!-- Which files, and why each one had to change. Focused diffs get merged faster. -->

## Checklist

- [ ] `pytest -q` passes
- [ ] `ruff check backend tests` is clean
- [ ] `npx tsc --noEmit` and `npx next build` are clean (if the frontend changed)
- [ ] New behaviour has tests
- [ ] Diff is scoped to one concern — no drive-by refactors
- [ ] **No secrets**: no API keys, tokens, real endpoints, or personal data in
      code, fixtures, comments or screenshots
- [ ] Deterministic guarantees intact — nothing lets an LLM decide money,
      permissions or safety
- [ ] Screenshots attached for UI changes (mobile + desktop)

## Notes for the reviewer

<!-- Anything you're unsure about, or deliberately left out. -->
