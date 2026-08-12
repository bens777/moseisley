"""SKILL CATALOG — capabilities a user can add to their account.

A skill is a MANIFEST, not an engine. It composes primitives that already exist:

  · crew roles      → crew_configs.enabled (+ optional prompt override)
  · recurring work  → scheduled_jobs, through scheduler.enqueue, using only
                      job types that already have a handler
  · automations     → instructions, through backend.ops.instructions

Nothing here executes anything. Enabling a skill flips existing switches;
disabling puts them back. That is the whole design, and it is what makes a
future marketplace safe: a third-party manifest can only ever ask for a
composition of things the platform already does.

HONESTY RULES for every manifest in this file:
  · a role may only be listed if it does something today;
  · a schedule entry's job_type MUST exist in backend.jobs.handlers.HANDLERS;
  · a role driven by instruction_run MUST be in crew.DELEGATABLE, and a role
    driven by the autopilot job MUST be in autopilot.RUNNERS — those two sets
    do not overlap, and getting it wrong ships a skill that silently no-ops;
  · requirements list what the user genuinely needs, including the ones that
    are inconvenient to admit.
Tests assert each of these against the live code.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Every scheduled skill needs this: the job handlers refuse to run scheduled
# work without it on hosted deployments (self-hosting is unaffected).
SCHEDULED_AUTONOMY = "scheduled_autonomy"


@dataclass(frozen=True)
class ConfigField:
    """Something the skill asks the user before it can be set up."""

    key: str
    label: str
    type: str = "time"            # time | text
    default: str = ""
    help: str = ""


@dataclass(frozen=True)
class ScheduleEntry:
    """One recurring job, created through scheduler.enqueue.

    `shared_key=True` reuses the platform's own per-user idempotency key for a
    built-in job, so enabling a skill can never create a second copy of a sweep
    the platform already runs.
    """

    job_type: str
    interval_seconds: int
    default_time: str
    label: str
    payload: dict = field(default_factory=dict)
    shared_key: bool = False
    weekday: int | None = None
    day_of_month: int | None = None


@dataclass(frozen=True)
class InstructionEntry:
    """An automation record, created through backend.ops.instructions.

    Used when the work runs as a crew delegation rather than an autopilot loop.
    `schedule` is left empty on purpose: the skill owns the job so it can use a
    cadence the instruction schema does not offer.
    """

    name: str
    kind: str
    assigned_role: str
    task: str


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    category: str
    one_liner: str
    what_it_does: tuple[str, ...]
    requirements: tuple[str, ...]
    roles: tuple[str, ...] = ()
    schedules: tuple[ScheduleEntry, ...] = ()
    instructions: tuple[InstructionEntry, ...] = ()
    config_fields: tuple[ConfigField, ...] = ()
    features: tuple[str, ...] = ()      # entitlement ids required to enable


DAILY = 86400
MONTHLY = 30 * DAILY

CATALOG: tuple[Skill, ...] = (
    Skill(
        id="inbox-triage",
        name="Inbox Triage",
        category="Email",
        one_liner="Each morning, sorts your recent mail into what needs you and what doesn't.",
        what_it_does=(
            "Reads the last 90 days of mail from your connected email source every morning.",
            "Flags the messages that look like they need a reply, and ignores the rest.",
            "Writes its summary onto the job itself — you read it on your Schedule page, "
            "or run it on demand from the Crew page.",
            "It never sends or replies to anything. It only tells you what it found.",
        ),
        requirements=(
            "A connected email source — Google. Nothing is simulated: with nothing "
            "connected this skill has nothing to read, and says so.",
            "Pro on hosted deployments — scheduled work is a Pro capability. "
            "Self-hosted installs have it.",
            "Classification quality depends on the model your AI mode provides.",
        ),
        roles=("inbox_triage",),
        schedules=(
            ScheduleEntry(job_type="autopilot", payload={"loop": "inbox_triage"},
                          interval_seconds=DAILY, default_time="07:30",
                          label="Inbox triage sweep"),
        ),
        config_fields=(
            ConfigField(key="run_time", label="Run each day at", type="time",
                        default="07:30", help="In your account timezone."),
        ),
        features=(SCHEDULED_AUTONOMY,),
    ),
    Skill(
        id="market-watch",
        name="Market Watch",
        category="Intelligence",
        one_liner="A daily sweep of your market that only speaks up when something moved.",
        what_it_does=(
            "Turns on the Radar role and makes sure its daily sweep is running.",
            "Reports competitor moves, demand signals and market shifts — and stays quiet "
            "when nothing material changed.",
            "Results land on the Command Center's Radar card and the Market page.",
            "If the platform already runs your daily sweep, this adopts it rather than "
            "adding a second one.",
        ),
        requirements=(
            "Pro: the Radar role is a Pro capability, as is scheduled work.",
            "Market quality is far better with an xAI key connected — Radar prefers it "
            "for live search. It falls back to your other providers.",
        ),
        roles=("radar",),
        schedules=(
            ScheduleEntry(job_type="market_radar", interval_seconds=DAILY,
                          default_time="06:00", label="Radar sweep", shared_key=True),
        ),
        features=("market_radar", SCHEDULED_AUTONOMY),
    ),
    Skill(
        id="follow-up-chaser",
        name="Follow-Up Chaser",
        category="Email",
        one_liner="Finds the conversations you left hanging and the promises you haven't kept.",
        what_it_does=(
            "Runs two passes over your last 90 days of mail each morning: unanswered asks "
            "aimed at you, and commitments you made that were never closed out.",
            "Surfaces only the material ones — it is deliberately quiet about noise.",
            "Both passes write their summary onto their job, visible on your Schedule page.",
            "It drafts nothing and sends nothing on this schedule. It reports.",
        ),
        requirements=(
            "A connected email source — Google. Nothing is simulated: with nothing "
            "connected this skill has nothing to read, and says so.",
            "Pro on hosted deployments — scheduled work is a Pro capability.",
            "It reads sent mail as well as inbound to tell 'unanswered' from 'handled'.",
        ),
        roles=("follow_up", "commitment_tracker"),
        schedules=(
            ScheduleEntry(job_type="autopilot", payload={"loop": "follow_up"},
                          interval_seconds=DAILY, default_time="08:15",
                          label="Unanswered conversations"),
            ScheduleEntry(job_type="autopilot", payload={"loop": "commitment_tracker"},
                          interval_seconds=DAILY, default_time="08:30",
                          label="Unkept commitments"),
        ),
        config_fields=(
            ConfigField(key="run_time", label="Run each day at", type="time",
                        default="08:15",
                        help="The commitments pass runs 15 minutes later."),
        ),
        features=(SCHEDULED_AUTONOMY,),
    ),
    Skill(
        id="x-ray-monthly",
        name="Monthly X-Ray",
        category="Intelligence",
        one_liner="A full 90-day sweep for unpaid invoices, dropped leads and recoverable time.",
        what_it_does=(
            "Runs the X-Ray analysis over your last 90 days, once every 30 days.",
            "Surfaces evidence-backed findings: money you are owed, leads that went cold, "
            "time you could get back.",
            "Findings appear on the X-Ray page and in the Command Center's intelligence card.",
            "It is a re-run of the same X-Ray you can start by hand at any time — this just "
            "makes it happen without you remembering.",
        ),
        requirements=(
            "Pro: the X-Ray role is a Pro capability, as is scheduled work.",
            "A connected data source — X-Ray over an empty account finds nothing.",
            "Every 30 days, not calendar-monthly: the scheduler counts days, it does not "
            "know about month boundaries.",
        ),
        roles=("xray",),
        instructions=(
            InstructionEntry(name="Monthly X-Ray", kind="custom", assigned_role="xray",
                             task="Run a full X-Ray over the last 90 days and report "
                                  "verified findings."),
        ),
        schedules=(
            ScheduleEntry(job_type="instruction_run", interval_seconds=MONTHLY,
                          default_time="09:00", label="Monthly X-Ray"),
        ),
        features=("xray", SCHEDULED_AUTONOMY),
    ),
)

BY_ID: dict[str, Skill] = {s.id: s for s in CATALOG}


def serialize(skill: Skill) -> dict:
    return {
        "id": skill.id, "name": skill.name, "category": skill.category,
        "one_liner": skill.one_liner,
        "what_it_does": list(skill.what_it_does),
        "requirements": list(skill.requirements),
        "roles": list(skill.roles),
        "schedule_labels": [s.label for s in skill.schedules],
        "config_fields": [{"key": c.key, "label": c.label, "type": c.type,
                           "default": c.default, "help": c.help}
                          for c in skill.config_fields],
        "features": list(skill.features),
    }


def reference_block() -> str:
    """The catalog as Manager prompt context, so "what can you do for me?" is
    answered from the real list rather than from imagination."""
    lines = ["## SKILLS (capabilities the user can switch on — this is the whole list)"]
    for s in CATALOG:
        lines.append(f"\n**{s.name}** (`{s.id}`, {s.category}) — {s.one_liner}")
        lines.append("  does: " + " ".join(s.what_it_does))
        lines.append("  needs: " + " ".join(s.requirements))
    lines.append("\nEnable one with the setup.enable_skill tool after the user agrees, or "
                 "send them to the page. Never describe a skill that is not on this list. "
                 "→ [Skills](action:skills)")
    return "\n".join(lines)
