"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Button, Card, Pill } from "@/components/ui";

export type Instruction = {
  id: string; name: string; kind: string; enabled: boolean; created_by: string;
  assigned_role: string | null; provider: string | null; model: string | null;
  project_id: string | null; config: Record<string, unknown>;
  schedule: Record<string, unknown>; delivery: string[]; version: number;
  status: string; last_run_at: string | null; next_run_at: string | null;
  last_result: Record<string, unknown>;
};

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric",
                                       hour: "2-digit", minute: "2-digit" });
}

function InstructionRow({ ins, onChange }: { ins: Instruction; onChange: () => void }) {
  const [view, setView] = useState<"human" | "json" | "edit">("human");
  const [jsonText, setJsonText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try { await fn(); onChange(); }
    catch (e) { setError(e instanceof Error ? e.message : "failed"); }
    finally { setBusy(false); }
  }

  const schedule = ins.schedule || {};
  const scheduleLabel = schedule.frequency
    ? `${schedule.frequency}${schedule.time ? ` at ${schedule.time}` : ""}${
        schedule.timezone ? ` (${schedule.timezone})` : ""}`
    : "manual only";

  return (
    <div className="min-w-0 rounded-md border border-line bg-ground/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 truncate text-sm font-medium text-ink">{ins.name}</span>
        <Pill color={ins.enabled ? (ins.status === "error" ? "red" : "green") : "gray"}
              pulse={ins.enabled && ins.status !== "error"}>
          {ins.enabled ? (ins.status === "error" ? "error" : "active") : "disabled"}
        </Pill>
        <span className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
          v{ins.version} · by {ins.created_by}
        </span>
        <div className="ml-auto flex gap-1 font-mono text-[10px] uppercase tracking-widest">
          {(["human", "json", "edit"] as const).map((v) => (
            <button key={v} onClick={() => {
              setView(v);
              if (v === "edit") setJsonText(JSON.stringify(
                { name: ins.name, kind: ins.kind, config: ins.config,
                  schedule: ins.schedule, delivery: ins.delivery,
                  assigned_role: ins.assigned_role, enabled: ins.enabled }, null, 2));
            }} className={`rounded-sm border px-2 py-1 ${
              view === v ? "border-brand/50 text-brand" : "border-line text-ink-faint hover:text-ink"}`}>
              {v}
            </button>
          ))}
        </div>
      </div>

      {view === "human" && (
        <div className="mt-2 grid gap-x-6 gap-y-1 text-[12px] text-ink-mute sm:grid-cols-2">
          <div>Schedule: <span className="text-ink">{scheduleLabel}</span></div>
          <div>Agent: <span className="text-ink">{ins.assigned_role || "orchestrator"}</span>
            {ins.model ? <span className="text-ink-faint"> · {ins.model}</span>
              : <span className="text-ink-faint"> · inherits model</span>}</div>
          <div>Last run: <span className="text-ink">{fmtTime(ins.last_run_at)}</span></div>
          <div>Next run: <span className="text-ink">{fmtTime(ins.next_run_at)}</span></div>
          {ins.delivery?.length > 0 && (
            <div>Delivers to: <span className="text-ink">{ins.delivery.join(", ")}</span></div>
          )}
          {Object.keys(ins.last_result || {}).length > 0 && (
            <div className="min-w-0 truncate sm:col-span-2">
              Last result: <span className="font-mono text-[11px] text-ink">
                {JSON.stringify(ins.last_result).slice(0, 120)}</span>
            </div>
          )}
        </div>
      )}
      {view === "json" && (
        <pre className="mt-2 max-h-64 overflow-auto rounded-sm bg-ground/80 p-2 font-mono text-[10px] leading-relaxed text-signal">
          {JSON.stringify(ins, null, 2)}
        </pre>
      )}
      {view === "edit" && (
        <div className="mt-2 space-y-2">
          <textarea value={jsonText} onChange={(e) => setJsonText(e.target.value)} rows={12}
                    className="w-full min-w-0 rounded-md border border-line-strong bg-ground/80 p-2 font-mono text-[11px] text-ink focus:border-brand/60 focus:outline-none" />
          <Button disabled={busy} onClick={() => act(async () => {
            const parsed = JSON.parse(jsonText);
            await api(`/instructions/${ins.id}`, { method: "PUT", body: parsed });
            setView("human");
          })}>Save changes</Button>
        </div>
      )}

      <div className="mt-2 flex flex-wrap gap-1.5">
        <Button variant="ghost" disabled={busy}
                onClick={() => act(() => api(`/instructions/${ins.id}/run`, { body: {} }))}>
          Run now
        </Button>
        <Button variant="ghost" disabled={busy}
                onClick={() => act(() => api(`/instructions/${ins.id}/toggle`,
                                             { body: { enabled: !ins.enabled } }))}>
          {ins.enabled ? "Disable" : "Enable"}
        </Button>
        <Button variant="ghost" disabled={busy}
                onClick={() => act(() => api(`/instructions/${ins.id}/duplicate`, { body: {} }))}>
          Duplicate
        </Button>
        {ins.version > 1 && (
          <Button variant="ghost" disabled={busy}
                  onClick={() => act(() => api(`/instructions/${ins.id}/rollback`,
                                               { body: { version: ins.version - 1 } }))}>
            Roll back
          </Button>
        )}
        <Button variant="danger" disabled={busy}
                onClick={() => {
                  if (window.confirm(`Delete "${ins.name}"?`)) {
                    act(() => api(`/instructions/${ins.id}`, { method: "DELETE" }));
                  }
                }}>
          Delete
        </Button>
      </div>
      {error && <p className="mt-2 break-words text-xs text-crit">{error}</p>}
    </div>
  );
}

/* Page-scoped "ACTIVE INSTRUCTIONS" area (third pass §16). */
export function ActiveInstructions({ kind, title, hint, projectId }: {
  kind?: string; title: string; hint: string; projectId?: string;
}) {
  const params: Record<string, string> = {};
  if (kind) params.kind = kind;
  if (projectId) params.project_id = projectId;
  const list = useApi<Instruction[]>("/instructions", params);

  return (
    <Card title={title}>
      {!list.data?.length ? (
        <p className="text-sm text-ink-faint">{hint}</p>
      ) : (
        <div className="space-y-2">
          {list.data.map((ins) => (
            <InstructionRow key={ins.id} ins={ins} onChange={list.reload} />
          ))}
        </div>
      )}
      <p className="mt-3 text-[11px] text-ink-faint">
        Create or change these conversationally — open the ◈ Manager and describe
        what you want.
      </p>
    </Card>
  );
}
