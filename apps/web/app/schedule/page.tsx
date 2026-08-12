"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Button, Card, EmptyState, ErrorBox, Insignia, Loading, Pill } from "@/components/ui";

/* SCHEDULE — the truth about everything that runs on its own for this user.
   One table: what runs, how often, when next, how it went last time, and the
   switch. Cadence edits are presets only, exactly what the backend accepts. */

type LastResult = { status: "ok" | "error" | "skipped" | "never_run"; detail: string | null };

type Job = {
  id: string; job_type: string; title: string; role: string; what: string;
  cadence: string; frequency: string | null; next_run_at: string | null;
  last_run_at: string | null; last_result: LastResult; enabled: boolean;
  editable: boolean; instruction_id: string | null; timezone: string;
};

const FREQUENCIES = ["hourly", "daily", "weekly"] as const;
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const RESULT_PILL: Record<LastResult["status"], { color: "green" | "red" | "gray"; label: string }> = {
  ok: { color: "green", label: "ok" },
  error: { color: "red", label: "failed" },
  skipped: { color: "gray", label: "skipped" },
  never_run: { color: "gray", label: "not yet run" },
};

function fmt(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function CadenceEditor({ job, onDone }: { job: Job; onDone: () => void }) {
  const [frequency, setFrequency] = useState(job.frequency || "daily");
  const [time, setTime] = useState("08:00");
  const [weekday, setWeekday] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api(`/schedule/${job.id}/cadence`, {
        method: "PUT", body: { frequency, time, weekday },
      });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not change the cadence");
      setBusy(false);
    }
  }

  return (
    <div className="mt-3 flex flex-wrap items-end gap-2 border-t border-line pt-3">
      <label className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
        <span className="mb-1 block">runs</span>
        <select value={frequency} onChange={(e) => setFrequency(e.target.value)}
                className="min-h-[38px] rounded-md border border-line-strong bg-ground/60 px-2 text-sm normal-case tracking-normal text-ink">
          {FREQUENCIES.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
      </label>
      {frequency !== "hourly" && (
        <label className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
          <span className="mb-1 block">at</span>
          <input type="time" value={time} onChange={(e) => setTime(e.target.value)}
                 className="min-h-[38px] rounded-md border border-line-strong bg-ground/60 px-2 text-sm text-ink" />
        </label>
      )}
      {frequency === "weekly" && (
        <label className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
          <span className="mb-1 block">on</span>
          <select value={weekday} onChange={(e) => setWeekday(Number(e.target.value))}
                  className="min-h-[38px] rounded-md border border-line-strong bg-ground/60 px-2 text-sm normal-case tracking-normal text-ink">
            {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
          </select>
        </label>
      )}
      <span className="self-center font-mono text-[10px] uppercase tracking-widest text-ink-faint">
        {job.timezone}
      </span>
      <Button onClick={save} disabled={busy}>Save cadence</Button>
      <Button variant="ghost" onClick={onDone} disabled={busy}>Cancel</Button>
      {error && <div className="w-full"><ErrorBox message={error} /></div>}
    </div>
  );
}

function JobRow({ job, onChange }: { job: Job; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const result = RESULT_PILL[job.last_result.status];

  async function toggle() {
    setBusy(true);
    setError(null);
    try {
      await api(`/schedule/${job.id}/toggle`, { body: { enabled: !job.enabled } });
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not change this job");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`min-w-0 rounded-md border border-line p-3 ${
      job.enabled ? "bg-ground/40" : "bg-ground/20 opacity-70"}`}>
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
        <Insignia kind={job.role} size="sm" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 break-words text-sm font-medium text-ink">{job.title}</span>
            <Pill color={job.enabled ? "green" : "gray"} pulse={job.enabled}>
              {job.enabled ? "on" : "off"}
            </Pill>
          </div>
          {job.what && <p className="mt-0.5 text-xs text-ink-mute">{job.what}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 font-mono text-[11px] uppercase tracking-wide text-ink-mute">
          <span title="cadence">{job.cadence}</span>
          <span title="next run">
            next <span className="text-ink">{job.enabled ? fmt(job.next_run_at) : "—"}</span>
          </span>
          <span className="flex items-center gap-1.5" title={job.last_result.detail || undefined}>
            last <Pill color={result.color}>{result.label}</Pill>
          </span>
        </div>
        <div className="flex shrink-0 gap-1.5">
          {job.editable && !editing && (
            <Button variant="ghost" onClick={() => setEditing(true)}>Cadence</Button>
          )}
          <Button variant="ghost" onClick={toggle} disabled={busy}>
            {job.enabled ? "Turn off" : "Turn on"}
          </Button>
        </div>
      </div>
      {job.last_result.status === "error" && job.last_result.detail && (
        <p className="mt-2 break-words border-t border-line pt-2 font-mono text-[11px] text-crit">
          {job.last_result.detail}
        </p>
      )}
      {editing && <CadenceEditor job={job} onDone={() => { setEditing(false); onChange(); }} />}
      {error && <div className="mt-2"><ErrorBox message={error} /></div>}
    </div>
  );
}

export default function SchedulePage() {
  const schedule = useApi<{ timezone: string; jobs: Job[] }>("/schedule");

  if (schedule.loading) return <Loading />;
  if (schedule.error) return <ErrorBox message={schedule.error} />;
  const jobs = schedule.data?.jobs || [];

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div>
        <h1 className="text-xl font-bold">Schedule</h1>
        <p className="mt-0.5 font-mono text-[11px] uppercase tracking-widest text-ink-faint">
          everything that runs on its own · times in {schedule.data?.timezone || "UTC"}
        </p>
      </div>

      <Card title="Recurring jobs">
        {jobs.length === 0 ? (
          <EmptyState
            label="nothing recurring"
            title="No scheduled work yet"
            body="Your default sweeps appear here once your Command Center has loaded, and every automation you save through the Manager joins them."
          />
        ) : (
          <div className="space-y-2">
            {jobs.map((j) => <JobRow key={j.id} job={j} onChange={schedule.reload} />)}
          </div>
        )}
      </Card>

      <p className="text-xs text-ink-faint">
        Turning a job off stops it for good — it will not be re-created on your next
        visit. Automations you saved through the Manager keep their own record, so
        editing them here edits the automation itself.
      </p>
    </div>
  );
}
