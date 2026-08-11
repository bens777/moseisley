"use client";
import { useState } from "react";
import { api, euros, hours } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Button, Card, EmptyState, ErrorBox, Loading, Pill } from "@/components/ui";

type Finding = {
  id: string; type: string; title: string; description: string;
  evidence: unknown[]; confidence: number; verified: boolean;
  estimated_value_cents: number | null; estimated_time_minutes: number | null;
  recommended_action: string | null; status: string;
};

type Latest = {
  run: { id: string; horizon_days: number; completed_at: string; summary: Record<string, number | boolean> } | null;
  findings: Record<string, Finding[]>;
  no_verified_money_message: string | null;
};

const SECTIONS: [string, string][] = [
  ["found_money", "Found Money"],
  ["estimated_opportunity", "Estimated Opportunity"],
  ["found_time", "Found Time"],
  ["goal_drift", "Goal Drift"],
  ["lost_commitment", "Lost Commitments"],
  ["project_drift", "Project Drift"],
  ["automatable_work", "Automatable Work"],
  ["shadow_backtest", "90-Day Backtest"],
];

function FindingCard({ f, onUpdate }: { f: Finding; onUpdate: () => void }) {
  return (
    <div className="rounded-md border border-line p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-medium">{f.title}</div>
          <div className="mt-1 text-xs text-ink-mute">{f.description}</div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Pill color={f.verified ? "green" : (f.estimated_value_cents != null || f.estimated_time_minutes != null) ? "yellow" : "blue"}>
            {f.verified ? "VERIFIED"
              : (f.estimated_value_cents != null || f.estimated_time_minutes != null) ? "ESTIMATED" : "INFERENCE"}
          </Pill>
          <span className="text-[10px] text-ink-mute">confidence {(f.confidence * 100).toFixed(0)}%</span>
        </div>
      </div>
      <div className="mt-2 flex items-center gap-3 text-xs text-ink-mute">
        {f.estimated_value_cents != null && <span>{euros(f.estimated_value_cents)}</span>}
        {f.estimated_time_minutes != null && <span>{hours(f.estimated_time_minutes)}</span>}
        {f.status !== "open" && <Pill>{f.status}</Pill>}
      </div>
      {f.recommended_action && (
        <div className="mt-2 text-xs text-brand">→ {f.recommended_action}</div>
      )}
      {Array.isArray(f.evidence) && f.evidence.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-ink-mute">Evidence</summary>
          <ul className="mt-1 space-y-1 text-xs text-ink-mute">
            {f.evidence.slice(0, 5).map((e, i) => (
              <li key={i} className="border-l-2 border-line-strong pl-2">
                {typeof e === "string" ? e : JSON.stringify(e)}
              </li>
            ))}
          </ul>
        </details>
      )}
      {f.status === "open" && (
        <div className="mt-2 flex gap-2">
          <button onClick={async () => { await api(`/xray/findings/${f.id}`, { method: "PATCH", body: { status: "actioned" } }); onUpdate(); }}
                  className="text-xs text-brand hover:underline">Mark actioned</button>
          <button onClick={async () => { await api(`/xray/findings/${f.id}`, { method: "PATCH", body: { status: "dismissed" } }); onUpdate(); }}
                  className="text-xs text-ink-mute hover:underline">Dismiss</button>
        </div>
      )}
    </div>
  );
}

export default function XRayPage() {
  const { data, error, loading, reload } = useApi<Latest>("/xray/latest");
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  async function run(days: number) {
    setRunning(true);
    setRunError(null);
    try {
      await api("/xray/run", { body: { horizon_days: days } });
      reload();
    } catch (e) {
      setRunError(e instanceof Error ? e.message : "run failed");
    } finally {
      setRunning(false);
    }
  }

  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} />;

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">X-Ray — Opportunity Scan</h1>
          <p className="mt-0.5 font-mono text-[11px] uppercase tracking-widest text-ink-faint">opportunity · situation scanner</p>
          <p className="mt-1 max-w-xl text-sm text-ink-mute">
            Scans your connected activity to find money, time, commitments and
            operational problems you may be missing. These are findings about YOUR
            situation — not crew performance, not revenue, not treasury.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {[30, 60, 90].map((d) => (
            <Button key={d} variant={d === 90 ? "primary" : "ghost"} disabled={running} onClick={() => run(d)}>
              {running ? "Analyzing…" : `Analyze ${d} days`}
            </Button>
          ))}
        </div>
      </div>
      {runError && <ErrorBox message={runError} />}
      {!data?.run && (
        <EmptyState
          label="scanner idle"
          title="No intelligence report yet"
          body="Connect your data, then let X-Ray analyze your recent history for:"
          bullets={["unpaid and overdue invoices", "warm leads that went cold", "forgotten commitments", "recoverable hours"]}
          action={<Button disabled={running} onClick={() => run(90)}>{running ? "Analyzing…" : "Analyze last 90 days"}</Button>}
        />
      )}
      {data?.run && (
        <>
          <Card title={`Last run — ${data.run.horizon_days} days`}>
            <div className="flex flex-wrap gap-4 text-sm text-ink-mute">
              <span>{String(data.run.summary.emails_analyzed)} emails</span>
              <span>{String(data.run.summary.events_analyzed)} calendar events</span>
              <span>{String(data.run.summary.findings)} findings</span>
              <span className="text-brand">{euros(Number(data.run.summary.verified_money_cents))} verified</span>
            </div>
            {data.no_verified_money_message && (
              <p className="mt-2 text-sm text-ink-mute">{data.no_verified_money_message}</p>
            )}
          </Card>
          {SECTIONS.map(([key, label]) => {
            const items = data.findings[key];
            if (!items?.length) return null;
            return (
              <Card key={key} title={label}>
                <div className="space-y-2">
                  {items.map((f) => <FindingCard key={f.id} f={f} onUpdate={reload} />)}
                </div>
              </Card>
            );
          })}
        </>
      )}
    </div>
  );
}
