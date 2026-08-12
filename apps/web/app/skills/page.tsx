"use client";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Button, Card, ErrorBox, Loading, Pill } from "@/components/ui";

/* SKILLS — capabilities you add to your account. Each card is a manifest the
   backend owns: what it does, what it needs, and one switch. Nothing here
   decides anything; the catalog and the gating both come from the API. */

type Skill = {
  id: string; name: string; category: string; one_liner: string;
  what_it_does: string[]; requirements: string[]; roles: string[];
  schedule_labels: string[];
  config_fields: { key: string; label: string; type: string; default: string; help: string }[];
  enabled: boolean; gated: boolean; gate_reason: string | null;
  enabled_at: string | null; last_activity: string | null;
  config: Record<string, string>;
};

function fmtWhen(iso: string | null): string {
  if (!iso) return "no activity yet";
  const d = new Date(iso);
  const days = Math.floor((Date.now() - d.getTime()) / 86_400_000);
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function StatusLed({ skill }: { skill: Skill }) {
  const [tone, label] = skill.enabled
    ? ["bg-ok led-pulse", "enabled"]
    : skill.gated
      ? ["bg-warn", "pro"]
      : ["bg-ink-faint", "off"];
  return (
    <span className="flex shrink-0 items-center gap-1.5 font-mono text-[10px] uppercase tracking-widest text-ink-faint">
      <span className={`led ${tone}`} aria-hidden /> {label}
    </span>
  );
}

function SkillCard({ skill, onChange }: { skill: Skill; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runTime, setRunTime] = useState(
    skill.config.run_time || skill.config_fields.find((f) => f.key === "run_time")?.default || "");

  async function toggle() {
    setBusy(true);
    setError(null);
    try {
      const action = skill.enabled ? "disable" : "enable";
      await api(`/skills/${skill.id}/${action}`, {
        body: skill.enabled ? {} : { config: runTime ? { run_time: runTime } : {} },
      });
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not change this skill");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`flex min-w-0 flex-col rounded-md border p-4 transition ${
      skill.enabled ? "border-brand/40 bg-brand/5" : "border-line bg-panel/60"}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
            {skill.category}
          </div>
          <h2 className="mt-0.5 break-words text-base font-semibold text-ink">{skill.name}</h2>
        </div>
        <StatusLed skill={skill} />
      </div>

      <p className="mt-2 text-sm leading-relaxed text-ink-mute">{skill.one_liner}</p>

      <button onClick={() => setOpen(!open)} aria-expanded={open}
              className="mt-3 self-start font-mono text-[10px] uppercase tracking-widest text-ink-faint transition hover:text-ink-mute">
        {open ? "Hide detail ▾" : "What it does · what it needs ▸"}
      </button>

      {open && (
        <div className="mt-3 space-y-3 border-t border-line pt-3">
          <div>
            <div className="font-mono text-[10px] font-bold uppercase tracking-widest text-ink-faint">
              What it does
            </div>
            <ul className="mt-1 space-y-1">
              {skill.what_it_does.map((line, i) => (
                <li key={i} className="flex gap-2 text-xs leading-relaxed text-ink-mute">
                  <span aria-hidden className="shrink-0 text-ok">·</span>
                  <span className="min-w-0">{line}</span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <div className="font-mono text-[10px] font-bold uppercase tracking-widest text-ink-faint">
              What it needs
            </div>
            <ul className="mt-1 space-y-1">
              {skill.requirements.map((line, i) => (
                <li key={i} className="flex gap-2 text-xs leading-relaxed text-ink-mute">
                  <span aria-hidden className="shrink-0 text-warn">·</span>
                  <span className="min-w-0">{line}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] uppercase tracking-wide text-ink-faint">
            {skill.roles.length > 0 && <span>crew: {skill.roles.join(", ")}</span>}
            {skill.schedule_labels.length > 0 && (
              <span>runs: {skill.schedule_labels.join(" · ")}</span>
            )}
          </div>
        </div>
      )}

      {!skill.enabled && !skill.gated && skill.config_fields.some((f) => f.key === "run_time") && (
        <label className="mt-3 flex flex-wrap items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-ink-faint">
          run at
          <input type="time" value={runTime} onChange={(e) => setRunTime(e.target.value)}
                 className="min-h-[36px] rounded-md border border-line-strong bg-ground/60 px-2 text-sm text-ink" />
        </label>
      )}

      {skill.gated && !skill.enabled && (
        <p className="mt-3 rounded-md border border-warn/40 bg-warn/10 p-2.5 text-xs text-ink-mute">
          {skill.gate_reason}{" "}
          <Link href="/settings#billing" className="text-brand hover:underline">See plans →</Link>
        </p>
      )}

      {error && <div className="mt-3"><ErrorBox message={error} /></div>}

      <div className="mt-auto flex flex-wrap items-center gap-3 pt-4">
        <Button variant={skill.enabled ? "ghost" : "primary"} disabled={busy || (skill.gated && !skill.enabled)}
                onClick={toggle}>
          {busy ? "Working…" : skill.enabled ? "Turn off" : "Add to my account"}
        </Button>
        {skill.enabled && (
          <span className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
            last activity: {fmtWhen(skill.last_activity)}
          </span>
        )}
      </div>
    </div>
  );
}

export default function SkillsPage() {
  const skills = useApi<{ skills: Skill[] }>("/skills");

  if (skills.loading) return <Loading />;
  if (skills.error) return <ErrorBox message={skills.error} />;
  const all = skills.data?.skills || [];
  const on = all.filter((s) => s.enabled).length;

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div>
        <h1 className="text-xl font-bold">Skills</h1>
        <p className="mt-0.5 font-mono text-[11px] uppercase tracking-widest text-ink-faint">
          capabilities you add to your crew · {on} of {all.length} on
        </p>
      </div>

      <div className="rounded-md border border-line bg-panel/50 p-3 text-sm text-ink-mute">
        Each skill switches on crew members and scheduled work that already exist — nothing
        new runs behind your back. Turning one off puts things back the way they were and
        keeps everything it produced.
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {all.map((s) => <SkillCard key={s.id} skill={s} onChange={skills.reload} />)}
      </div>

      <p className="text-xs text-ink-faint">
        Everything a skill schedules shows up on your{" "}
        <Link href="/schedule" className="text-brand hover:underline">Schedule</Link>, where you
        can change its cadence or switch off a single job.
      </p>
    </div>
  );
}
