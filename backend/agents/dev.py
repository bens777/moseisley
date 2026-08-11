"""Dev Agent (third pass §20-§26): reviews the platform, proposes improvements,
prepares isolated patches, and never touches main without a hash-bound approval.

SAFETY MODEL (deterministic, enforced here — not by the LLM):
- MAY without approval: read sanitized repo data, analyze telemetry, create
  proposals, create an isolated git worktree/branch, generate + apply a patch
  THERE, run tests THERE.
- MUST NOT without a valid approval bound to the exact patch hash: merge,
  modify main, deploy, run migrations, change secrets/Treasury.
- The LLM NEVER receives secrets: .env*, key/credential files are excluded
  from every context (SECRET_DENYLIST), and only file paths + sanitized
  contents are passed.
- Approval invalidation: approvals reference patch_hash; if the patch changes,
  approved_patch_hash no longer matches and merge is refused (§25).
"""
from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import shlex
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.core.models import ApprovalRequest, CrewRun, DevProposal, Instruction, User
from backend.ledger import service as ledger
from backend.ops import instructions as instructions_svc
from backend.providers import registry

REPO_ROOT = Path(__file__).resolve().parents[2]

SECRET_DENYLIST = (
    ".env", ".env.*", "*.pem", "*.key", "*secret*", "*credential*",
    "*.sqlite", "*.db", "mychief.db",
)

MAX_PROPOSALS_PER_REVIEW = 3
MAX_CONTEXT_FILES = 12
MAX_FILE_CHARS = 12000


class DevError(RuntimeError):
    pass


def _is_secret_path(path: str) -> bool:
    name = Path(path).name
    return any(fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(path, pat)
               for pat in SECRET_DENYLIST)


async def _run(cmd: list[str] | str, *, cwd: Path, timeout: float = 600.0) -> tuple[int, str]:
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as e:
        proc.kill()
        raise DevError(f"command timed out: {' '.join(cmd)}") from e
    return proc.returncode or 0, out.decode(errors="replace")


def repo_available() -> bool:
    return (REPO_ROOT / ".git").exists()


async def _repo_tree() -> str:
    code, out = await _run(["git", "ls-files"], cwd=REPO_ROOT, timeout=30)
    if code != 0:
        return "(repository listing unavailable)"
    files = [f for f in out.splitlines() if not _is_secret_path(f)]
    return "\n".join(files[:400])


async def _telemetry(db: AsyncSession, user_id: str) -> dict:
    """Sanitized platform telemetry the Dev Agent may see. No secrets."""
    from sqlalchemy import func

    from backend.core.models import Event, LlmUsage

    failures = list((await db.execute(
        select(CrewRun.crew_role, func.count(CrewRun.id))
        .where(CrewRun.user_id == user_id, CrewRun.status == "failed")
        .group_by(CrewRun.crew_role))).all())
    event_counts = list((await db.execute(
        select(Event.event_type, func.count(Event.id))
        .where(Event.user_id == user_id).group_by(Event.event_type)
        .order_by(func.count(Event.id).desc()).limit(20))).all())
    failed_llm = int((await db.execute(
        select(func.count(LlmUsage.id)).where(
            LlmUsage.user_id == user_id, LlmUsage.status == "failed"))).scalar_one())
    return {"crew_failures": {r: c for r, c in failures},
            "top_ledger_events": {t: c for t, c in event_counts},
            "failed_llm_requests": failed_llm}


async def run_dev_review(db: AsyncSession, user: User, instruction: Instruction) -> dict:
    """The configurable weekly review (§21): telemetry + repo overview → LLM →
    persisted proposals. No code is modified here."""
    run = CrewRun(user_id=user.id, crew_role="dev", runtime="native",
                  task_summary=f"dev review: {instruction.name}"[:500])
    db.add(run)
    await db.flush()

    existing_titles = [p.title for p in (await db.execute(
        select(DevProposal).where(DevProposal.user_id == user.id,
                                  DevProposal.status.in_(("proposed", "patch_ready")))
    )).scalars()]
    from backend.agents import crew as crew_svc

    prompt = "\n\n".join([
        await crew_svc.get_prompt(db, user.id, "dev"),
        f"## User instruction\n{(instruction.config_json or {}).get('instruction') or 'Standard weekly review.'}",
        f"## Platform telemetry (sanitized)\n{json.dumps(await _telemetry(db, user.id))}",
        f"## Repository files\n{await _repo_tree() if repo_available() else '(no repository in this deployment)'}",
        f"## Open proposals (do not duplicate)\n{json.dumps(existing_titles)}",
        "Reply with EXACTLY the JSON object from your output contract.",
    ])
    try:
        result = await registry.generate(db, user.id, [{"role": "user", "content": prompt}],
                                         crew_role="dev", purpose="chat", run_id=run.id,
                                         json_mode=True, max_tokens=2000)
    except Exception as e:
        run.status = "failed"
        run.result_summary = str(e)[:500]
        run.finished_at = datetime.now(UTC)
        await db.flush()
        await instructions_svc.record_run_result(db, instruction,
                                                 {"error": str(e)[:300]}, status="error")
        raise

    parsed = result.parse_json() or {}
    proposals_in = parsed.get("proposals") or [] if isinstance(parsed, dict) else []
    created = []
    for p in proposals_in[:MAX_PROPOSALS_PER_REVIEW]:
        if not isinstance(p, dict) or not p.get("title"):
            continue
        proposal = DevProposal(
            user_id=user.id, title=str(p.get("title"))[:300],
            why=str(p.get("why") or ""), expected_benefit=str(p.get("expected_benefit") or ""),
            evidence_json=list(p.get("evidence") or []), plan_md=str(p.get("plan_md") or ""),
            files_affected_json=[f for f in (p.get("files_affected") or [])
                                 if not _is_secret_path(str(f))],
            schema_impact=str(p.get("schema_impact") or "none")[:500],
            risk=p.get("risk") if p.get("risk") in ("low", "medium", "high") else "medium",
            test_plan=str(p.get("test_plan") or ""))
        db.add(proposal)
        await db.flush()
        await ledger.record(db, user.id, "dev_proposal_created", actor_type="agent",
                            actor_id="dev", entity_type="dev_proposal",
                            entity_id=proposal.id, payload={"title": proposal.title})
        created.append(proposal.id)

    run.status = "completed"
    run.result_summary = json.dumps({"proposals_created": len(created)})
    run.finished_at = datetime.now(UTC)
    await db.flush()
    review_result = {"proposals_created": len(created), "proposal_ids": created,
                     "crew_run_id": run.id}
    await instructions_svc.record_run_result(db, instruction, review_result)
    return review_result


async def get_proposal(db: AsyncSession, user_id: str, proposal_id: str) -> DevProposal:
    p = (await db.execute(select(DevProposal).where(
        DevProposal.id == proposal_id, DevProposal.user_id == user_id
    ))).scalar_one_or_none()
    if p is None:
        raise DevError("proposal not found")
    return p


def _worktree_path(proposal: DevProposal) -> Path:
    base = Path(get_settings().storage_local_path or "/tmp") / "dev-worktrees"
    return base / f"proposal-{proposal.id[:8]}"


async def prepare_patch(db: AsyncSession, user: User, proposal_id: str) -> dict:
    """Generate + test a patch in an ISOLATED worktree. main is never touched."""
    if not repo_available():
        raise DevError("no git repository available in this deployment")
    proposal = await get_proposal(db, user.id, proposal_id)
    branch = f"dev/proposal-{proposal.id[:8]}"
    worktree = _worktree_path(proposal)

    # (re)create the isolated worktree from current main
    await _run(["git", "worktree", "remove", "--force", str(worktree)], cwd=REPO_ROOT)
    await _run(["git", "branch", "-D", branch], cwd=REPO_ROOT)
    worktree.parent.mkdir(parents=True, exist_ok=True)
    code, out = await _run(["git", "worktree", "add", "-b", branch, str(worktree), "main"],
                           cwd=REPO_ROOT, timeout=120)
    if code != 0:
        raise DevError(f"could not create worktree: {out[:300]}")

    # context: proposal + affected files (sanitized) → unified diff
    file_blobs = []
    for rel in (proposal.files_affected_json or [])[:MAX_CONTEXT_FILES]:
        path = worktree / rel
        if _is_secret_path(rel) or not path.is_file():
            continue
        file_blobs.append(f"### {rel}\n```\n{path.read_text(errors='replace')[:MAX_FILE_CHARS]}\n```")
    prompt = "\n\n".join([
        "You are the Dev Agent preparing a patch. Produce a minimal unified diff "
        "(git apply format, paths relative to repo root, a/ b/ prefixes) that "
        "implements the proposal below. Reply with EXACTLY one JSON object: "
        '{"patch": "<unified diff>", "notes": "<what it does>"}.',
        f"## Proposal\n{proposal.title}\n{proposal.why}\n\nPLAN:\n{proposal.plan_md}",
        f"## Test plan\n{proposal.test_plan}",
        "## Current files\n" + ("\n\n".join(file_blobs) if file_blobs else "(new files only)"),
    ])
    result = await registry.generate(db, user.id, [{"role": "user", "content": prompt}],
                                     crew_role="dev", purpose="chat",
                                     json_mode=True, max_tokens=4000)
    parsed = result.parse_json() or {}
    patch_text = (parsed.get("patch") or "") if isinstance(parsed, dict) else ""
    if not patch_text.strip():
        proposal.test_results_json = {"error": "model returned no patch"}
        await db.flush()
        raise DevError("model returned no patch")

    patch_file = worktree / ".dev-agent.patch"
    patch_file.write_text(patch_text)
    code, out = await _run(["git", "apply", "--whitespace=fix", str(patch_file)],
                           cwd=worktree, timeout=60)
    patch_file.unlink(missing_ok=True)
    if code != 0:
        proposal.test_results_json = {"error": f"patch failed to apply: {out[:500]}"}
        await db.flush()
        raise DevError("patch failed to apply")

    # commit in the isolated branch; hash binds any future approval
    await _run(["git", "add", "-A"], cwd=worktree)
    await _run(["git", "-c", "user.email=dev-agent@moseisley.sh",
                "-c", "user.name=Moseisley Dev Agent", "commit", "-m",
                f"dev-agent: {proposal.title}"], cwd=worktree, timeout=60)
    _, diff_stat = await _run(["git", "diff", "--stat", "main", branch], cwd=REPO_ROOT)
    _, full_diff = await _run(["git", "diff", "main", branch], cwd=REPO_ROOT)
    patch_hash = hashlib.sha256(full_diff.encode()).hexdigest()

    # tests inside the worktree only
    test_cmd = get_settings().dev_test_command
    code, test_out = await _run(test_cmd, cwd=worktree, timeout=1800)
    proposal.branch_name = branch
    proposal.patch_hash = patch_hash
    proposal.patch_stats_json = {"stat": diff_stat[-1500:],
                                 "notes": str(parsed.get("notes") or "")[:500]}
    proposal.test_results_json = {"command": test_cmd, "exit_code": code,
                                  "output_tail": test_out[-2000:]}
    proposal.status = "patch_ready" if code == 0 else "failed"
    # a new patch invalidates any earlier approval (§25)
    proposal.approved_patch_hash = None
    proposal.approval_id = None
    proposal.approved_at = None
    await db.flush()
    await ledger.record(db, user.id, "dev_patch_ready", actor_type="agent", actor_id="dev",
                        entity_type="dev_proposal", entity_id=proposal.id,
                        payload={"patch_hash": patch_hash, "tests_passed": code == 0})
    if proposal.status == "patch_ready":
        await _request_approval(db, user, proposal)
    return {"status": proposal.status, "patch_hash": patch_hash,
            "tests_passed": code == 0}


async def _request_approval(db: AsyncSession, user: User, proposal: DevProposal) -> None:
    approval = ApprovalRequest(
        user_id=user.id, action_type="dev_merge",
        action_payload_json={"proposal_id": proposal.id, "patch_hash": proposal.patch_hash,
                             "title": proposal.title},
        risk_level={"low": 1, "medium": 2, "high": 3}.get(proposal.risk, 2))
    db.add(approval)
    await db.flush()
    proposal.approval_id = approval.id
    await ledger.record(db, user.id, "approval_requested", actor_type="agent",
                        actor_id="dev", entity_type="approval", entity_id=approval.id,
                        payload={"action_type": "dev_merge", "proposal_id": proposal.id})
    await _notify_telegram(db, user, proposal, approval)


async def _notify_telegram(db: AsyncSession, user: User, proposal: DevProposal,
                           approval: ApprovalRequest) -> bool:
    from backend.api.routes.telegram import get_gateway
    from backend.core.models import TelegramBinding

    gateway = get_gateway()
    if gateway is None:
        return False
    binding = (await db.execute(select(TelegramBinding).where(
        TelegramBinding.user_id == user.id))).scalar_one_or_none()
    if binding is None:
        return False
    tests = proposal.test_results_json or {}
    text = "\n".join([
        "*DEV AGENT — PATCH READY*", "", proposal.title, "",
        (proposal.patch_stats_json or {}).get("stat", "").strip()[:400],
        f"Tests: {'passed' if tests.get('exit_code') == 0 else 'FAILED'}",
        f"Schema migration: {'YES' if proposal.schema_impact not in ('', 'none') else 'no'}",
        f"Risk: {proposal.risk.upper()}",
        f"Patch: `{(proposal.patch_hash or '')[:12]}`",
    ])
    keyboard = {"inline_keyboard": [[
        {"text": "APPROVE", "callback_data": f"dev:{approval.id}:approve"},
        {"text": "REJECT", "callback_data": f"dev:{approval.id}:deny"},
    ]]}
    try:
        await gateway.client.send_message(binding.telegram_chat_id, text,
                                          reply_markup=keyboard)
        return True
    except Exception:
        return False


async def resolve_approval(db: AsyncSession, user_id: str, approval_id: str,
                           *, approve: bool, channel: str) -> dict:
    """Resolve a dev_merge approval. Approval binds to the CURRENT patch hash."""
    approval = (await db.execute(select(ApprovalRequest).where(
        ApprovalRequest.id == approval_id, ApprovalRequest.user_id == user_id,
        ApprovalRequest.action_type == "dev_merge"))).scalar_one_or_none()
    if approval is None:
        raise DevError("approval not found")
    if approval.status != "pending":
        raise DevError(f"approval already {approval.status}")
    proposal = await get_proposal(db, user_id,
                                  (approval.action_payload_json or {})["proposal_id"])
    approval.status = "approved" if approve else "denied"
    approval.resolved_at = datetime.now(UTC)
    approval.resolution_channel = channel
    bound_hash = (approval.action_payload_json or {}).get("patch_hash")
    if approve:
        if bound_hash != proposal.patch_hash:
            approval.status = "denied"
            await db.flush()
            raise DevError("patch changed since this approval card was issued — "
                           "a fresh approval is required")
        proposal.status = "approved"
        proposal.approved_patch_hash = bound_hash
        proposal.approved_at = datetime.now(UTC)
        event = "dev_proposal_approved"
    else:
        proposal.status = "rejected"
        event = "dev_proposal_rejected"
    await db.flush()
    await ledger.record(db, user_id, "approval_resolved", actor_type="user",
                        entity_type="approval", entity_id=approval.id,
                        payload={"approved": approve, "channel": channel,
                                 "patch_hash": bound_hash})
    await ledger.record(db, user_id, event, actor_type="user",
                        entity_type="dev_proposal", entity_id=proposal.id,
                        payload={"patch_hash": bound_hash, "channel": channel})
    return {"proposal_id": proposal.id, "status": proposal.status}


async def merge(db: AsyncSession, user: User, proposal_id: str) -> dict:
    """Merge an APPROVED patch into main. Refuses on any hash mismatch (§26).
    Deploy remains a separate human Docker Compose action."""
    if not repo_available():
        raise DevError("no git repository available in this deployment")
    proposal = await get_proposal(db, user.id, proposal_id)
    if proposal.status != "approved":
        raise DevError("proposal is not approved")
    if not proposal.approved_patch_hash or not proposal.branch_name:
        raise DevError("no approved patch")
    _, full_diff = await _run(["git", "diff", "main", proposal.branch_name], cwd=REPO_ROOT)
    current_hash = hashlib.sha256(full_diff.encode()).hexdigest()
    if current_hash != proposal.approved_patch_hash:
        proposal.status = "patch_ready"
        proposal.approved_patch_hash = None
        await db.flush()
        raise DevError("patch changed after approval — approval invalidated")
    code, out = await _run(["git", "merge", "--no-ff", "-m",
                            f"dev-agent: merge approved proposal {proposal.id[:8]} — {proposal.title}",
                            proposal.branch_name], cwd=REPO_ROOT, timeout=120)
    if code != 0:
        await _run(["git", "merge", "--abort"], cwd=REPO_ROOT)
        raise DevError(f"merge failed: {out[:300]}")
    _, head = await _run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, timeout=10)
    proposal.status = "merged"
    proposal.merged_commit = head.strip()
    await db.flush()
    await ledger.record(db, user.id, "dev_proposal_merged", actor_type="user",
                        entity_type="dev_proposal", entity_id=proposal.id,
                        payload={"commit": proposal.merged_commit,
                                 "patch_hash": proposal.approved_patch_hash})
    return {"merged": True, "commit": proposal.merged_commit}
