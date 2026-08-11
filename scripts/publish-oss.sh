#!/usr/bin/env bash
#
# publish-oss.sh — cut a public open-source release of Moseisley.
#
# WHY THIS EXISTS
#   The private repository on the server is the source of truth and keeps its
#   full history (owner directives, deployment runbooks, build reports). The
#   public repository must contain a CLEAN history: one commit, no secrets, no
#   internal documents. This script produces exactly that.
#
# HOW IT WORKS
#   `git archive HEAD` exports the committed tree only. That mechanically
#   excludes .git history, untracked files and anything .gitignore'd, and it
#   honours `export-ignore` in .gitattributes (which is how internal docs stay
#   private). The export is then re-scanned for secret patterns, committed once
#   in a fresh repository, and pushed.
#
# USAGE
#   ./scripts/publish-oss.sh --dry-run                         # everything except the push
#   ./scripts/publish-oss.sh git@github.com:USER/moseisley.git # first publish (force-push)
#   ./scripts/publish-oss.sh git@github.com:USER/moseisley.git # later releases (new commit)
#
#   FIRST PUBLISH: the remote is empty, so the single "initial public release"
#   commit is force-pushed. SUBSEQUENT RUNS: the script fetches the existing
#   public main, replays the current tree on top of it as ONE new commit
#   ("Moseisley — release YYYY-MM-DD"), and pushes fast-forward. The public
#   history therefore grows one commit per release and never exposes the
#   private history.
#
#   Skip the test run with SKIP_TESTS=1 (not recommended).
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DRY_RUN=0
REMOTE=""
case "${1:-}" in
  --dry-run|"") DRY_RUN=1 ;;
  *)            REMOTE="$1" ;;
esac

say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 1. the working tree must be clean and green ──────────────────────────────
say "1/6  Checking the working tree"
[ -z "$(git status --porcelain)" ] || fail "working tree is dirty — commit or stash first"
HEAD_SHA="$(git rev-parse --short HEAD)"
echo "     clean at $HEAD_SHA"

say "2/6  Running the test suite"
if [ "${SKIP_TESTS:-0}" = "1" ]; then
  echo "     SKIPPED (SKIP_TESTS=1)"
elif [ -x .venv/bin/python ]; then
  .venv/bin/python -m pytest -q || fail "tests failed — not publishing"
  .venv/bin/python -m ruff check backend tests || fail "ruff failed — not publishing"
else
  fail ".venv not found — create it or run with SKIP_TESTS=1"
fi

# ── 3. pristine export of the committed tree ─────────────────────────────────
say "3/6  Exporting a pristine tree (git archive)"
EXPORT_DIR="$(mktemp -d -t moseisley-oss-XXXXXX)"
trap 'rm -rf "$EXPORT_DIR"' EXIT
git archive HEAD | tar -x -C "$EXPORT_DIR"
echo "     $(find "$EXPORT_DIR" -type f | wc -l) files → $EXPORT_DIR"

# these must never appear in the export; belt and braces on top of .gitignore
for forbidden in .env .git .venv node_modules .next __pycache__; do
  if find "$EXPORT_DIR" -name "$forbidden" -print -quit | grep -q .; then
    fail "export contains '$forbidden' — aborting"
  fi
done

# ── 4. secret re-scan (same patterns as the pre-release audit) ───────────────
say "4/6  Re-scanning the export for secrets"
# Live-credential shapes. Test fixtures use short, obviously-fake values
# (sk-test-…, sk-or-user-key) and are excluded by the length requirements.
PATTERNS='sk-or-v1-[A-Za-z0-9]{20,}|sk-proj-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9-]{20,}'
PATTERNS="$PATTERNS"'|whsec_[A-Za-z0-9]{16,}|price_1[A-Za-z0-9]{16,}|rk_live_[A-Za-z0-9]{16,}'
PATTERNS="$PATTERNS"'|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,}'
PATTERNS="$PATTERNS"'|-----BEGIN [A-Z ]*PRIVATE KEY|postgres(ql)?(\+asyncpg)?://[^ "'"'"':]*:[^ "'"'"'@]*@(?!postgres|localhost|127\.0\.0\.1)'

if grep -rInaEP "$PATTERNS" "$EXPORT_DIR" 2>/dev/null | grep -v '^Binary'; then
  fail "secret-looking strings found in the export (above) — aborting"
fi
echo "     no live-credential patterns found"

# a real .env must never be in there, and the example must stay a template
[ -f "$EXPORT_DIR/.env" ] && fail "export contains a real .env"
grep -qE '^(APP_SECRET=change-me|MASTER_ENCRYPTION_KEY=$)' "$EXPORT_DIR/.env.example" \
  || fail ".env.example does not look like a placeholder template"
echo "     .env absent, .env.example is a template"

# ── 5. fresh single-commit repository ────────────────────────────────────────
say "5/6  Building the public repository (single commit)"
RELEASE_DATE="$(date -u +%Y-%m-%d)"
cd "$EXPORT_DIR"
git init -q -b main
git add -A

if [ -n "$REMOTE" ] && git ls-remote --exit-code --heads "$REMOTE" main >/dev/null 2>&1; then
  # remote already has history: replay this tree as ONE new commit on top of it
  PUBLISH_MODE="update"
  git remote add origin "$REMOTE"
  git fetch -q --depth=1 origin main
  git reset -q --soft FETCH_HEAD          # keep the exported tree, adopt public history
  git add -A
  if git diff --cached --quiet; then
    say "Nothing changed since the last public release — nothing to publish."
    exit 0
  fi
  git -c user.name="Moseisley" -c user.email="cantina@moseisley.sh" \
      commit -q -m "Moseisley — release $RELEASE_DATE"
  PUSH_ARGS="origin main"
else
  PUBLISH_MODE="first"
  git -c user.name="Moseisley" -c user.email="cantina@moseisley.sh" \
      commit -q -m "Moseisley — initial public release"
  [ -n "$REMOTE" ] && git remote add origin "$REMOTE"
  PUSH_ARGS="--force origin main"
fi
echo "     mode: $PUBLISH_MODE · $(git rev-list --count HEAD) commit(s) · $(git ls-files | wc -l) files"

# ── 6. push (or explain what would happen) ───────────────────────────────────
say "6/6  Publishing"
if [ "$DRY_RUN" = "1" ]; then
  cat <<EOF
     DRY RUN — nothing was pushed.
     Export prepared at: $EXPORT_DIR (removed on exit)
     Source commit:      $HEAD_SHA
     Would run:          git push $PUSH_ARGS
     Tree preview:
$(git ls-files | head -25 | sed 's/^/       /')
       ... $(git ls-files | wc -l) files total
EOF
else
  git push $PUSH_ARGS
  say "Published to $REMOTE ($PUBLISH_MODE)"
fi
