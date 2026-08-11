"use client";
import { api, euros } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useState } from "react";
import { ActiveInstructions } from "@/components/instructions";
import { Button, Card, EmptyState, ErrorBox, Loading, Pill, Stat } from "@/components/ui";

type Treasury = {
  budget: {
    monthly_limit_cents: number | null; daily_limit_cents: number | null;
    autonomous_threshold_cents: number | null; approval_threshold_cents: number | null;
    per_transaction_hard_limit_cents: number | null; spending_enabled: boolean;
  };
  spent_today_cents: number; spent_this_month_cents: number;
};
type Intent = {
  id: string; amount_cents: number; purpose: string; status: string;
  decision_reason: string | null; created_at: string;
};
type Approval = { id: string; action_type: string; payload: Record<string, unknown>; created_at: string };

const statusColor: Record<string, string> = {
  executed: "green", auto_approved: "green", approved: "green",
  awaiting_approval: "yellow", denied: "red", failed: "red",
};

export default function MoneyPage() {
  const treasury = useApi<Treasury>("/treasury");
  const intents = useApi<Intent[]>("/spend-intents");
  const approvals = useApi<Approval[]>("/approvals");

  if (treasury.loading || intents.loading || approvals.loading) return <Loading />;
  if (treasury.error) return <ErrorBox message={treasury.error} />;
  const b = treasury.data!.budget;

  async function toggleSpending() {
    await api("/treasury", { method: "PATCH", body: { spending_enabled: !b.spending_enabled } });
    treasury.reload();
  }

  async function resolve(id: string, approve: boolean) {
    await api(`/approvals/${id}/resolve`, { body: { approve } });
    approvals.reload();
    intents.reload();
    treasury.reload();
  }

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div>
        <h1 className="text-xl font-bold">Money</h1>
        <p className="mt-0.5 font-mono text-[11px] uppercase tracking-widest text-ink-faint">treasury · deterministic spending control</p>
      </div>

      <Card title="Budget">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Stat label="Monthly" value={`${euros(treasury.data!.spent_this_month_cents)} / ${euros(b.monthly_limit_cents)}`} />
          <Stat label="Today" value={`${euros(treasury.data!.spent_today_cents)} / ${euros(b.daily_limit_cents)}`} />
          <Stat label="Autonomous limit" value={euros(b.autonomous_threshold_cents)} />
          <Stat label="Hard maximum" value={euros(b.per_transaction_hard_limit_cents)} />
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Pill color={b.spending_enabled ? "green" : "gray"}>
            Spending {b.spending_enabled ? "ON" : "OFF"}
          </Pill>
          <Button variant={b.spending_enabled ? "danger" : "primary"} onClick={toggleSpending}>
            {b.spending_enabled ? "DISABLE SPENDING" : "Enable spending"}
          </Button>
        </div>
      </Card>

      <Card title={`Needs your approval (${approvals.data?.length || 0})`}>
        {!approvals.data?.length ? (
          <p className="flex items-center gap-2 py-1 font-mono text-[11px] uppercase tracking-widest text-ink-faint">
            <span className="led bg-ok" aria-hidden /> no decisions pending — crew operating within limits
          </p>
        ) : (
          <div className="space-y-2">
            {approvals.data.map((a) => (
              <div key={a.id} className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-warn/40 bg-warn/10 p-3">
                <div className="min-w-0 text-sm">
                  <div className="break-words font-medium">
                    {a.action_type === "spend"
                      ? `Spend ${euros(Number(a.payload.amount_cents))} — ${a.payload.purpose}`
                      : a.action_type}
                  </div>
                  <div className="text-xs text-ink-mute">{new Date(a.created_at).toLocaleString()}</div>
                </div>
                <div className="flex gap-2">
                  <Button onClick={() => resolve(a.id, true)}>APPROVE</Button>
                  <Button variant="danger" onClick={() => resolve(a.id, false)}>DENY</Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="Recent intents">
        {!intents.data?.length ? (
          <EmptyState
            label="treasury"
            title="No spend intents yet"
            body="When your crew wants to spend money, the request lands here: small amounts auto-approve within your limits, larger ones wait for your sign-off, and anything above the hard cap is denied."
          />
        ) : (
          <div className="space-y-1">
            {intents.data.map((i) => (
              <div key={i.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-line/60 py-2 text-sm">
                <div className="min-w-0">
                  <span className="font-mono font-medium tabular-nums">{euros(i.amount_cents)}</span>
                  <span className="ml-2 break-words text-ink-mute">{i.purpose}</span>
                  {i.decision_reason && <span className="ml-2 text-xs text-ink-faint">({i.decision_reason})</span>}
                </div>
                <Pill color={statusColor[i.status] || "gray"}>{i.status.replace("_", " ")}</Pill>
              </div>
            ))}
          </div>
        )}
      </Card>

      <AiUsagePanel />

      <ActiveInstructions kind="budget_rule" title="Treasury & budget rules"
                          hint="No budget-rule instructions yet. Deterministic Treasury limits above always apply regardless." />
    </div>
  );
}

/* ── AI usage / cost view (§27-§29): BYOK, persisted provider data only ── */
type UsageView = {
  window: string;
  totals: { requests: number; tokens: { input: number; cached_input: number;
    output: number; reasoning: number; total: number };
    reported_cost: number; estimated_cost: number; unknown_cost_requests: number };
  runtime: { total_seconds: number; runs: number; by_role: Record<string, number> };
  breakdowns: Record<string, { key: string | null; requests: number; total_tokens: number;
                               reported_cost: number; estimated_cost: number }[]>;
  byok_note: string;
};

function fmtRt(seconds: number): string {
  if (!seconds) return "0m";
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

function AiUsagePanel() {
  const [window_, setWindow] = useState<"today" | "week" | "month">("week");
  const usage = useApi<UsageView>("/metrics/usage", { window: window_ });
  const [dim, setDim] = useState<"agent" | "project" | "provider" | "model" | "day">("agent");
  const u = usage.data;

  return (
    <Card title="AI usage — paid directly through your connected provider accounts"
          action={
            <div className="flex gap-1 font-mono text-[10px] uppercase tracking-widest">
              {(["today", "week", "month"] as const).map((w) => (
                <button key={w} onClick={() => setWindow(w)}
                        className={`rounded-sm border px-2 py-1 ${
                          window_ === w ? "border-brand/50 text-brand" : "border-line text-ink-faint hover:text-ink"}`}>
                  {w}
                </button>
              ))}
            </div>
          }>
      {!u ? <Loading /> : (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Tokens" value={u.totals.tokens.total.toLocaleString()}
                  hint={`${u.totals.requests} requests`} />
            <Stat label="Reported cost"
                  value={u.totals.reported_cost > 0 ? `$${u.totals.reported_cost.toFixed(2)}` : "—"}
                  hint="provider-billed" />
            <Stat label="Estimated cost"
                  value={u.totals.estimated_cost > 0 ? `$${u.totals.estimated_cost.toFixed(2)}` : "—"}
                  hint={u.totals.unknown_cost_requests > 0
                        ? `${u.totals.unknown_cost_requests} req UNKNOWN` : "from pricing snapshots"} />
            <Stat label="Agent runtime" value={fmtRt(u.runtime.total_seconds)}
                  hint={`${u.runtime.runs} runs — never "time saved"`} />
          </div>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-line pt-2 font-mono text-[10px] uppercase tracking-wide text-ink-faint">
            <span>input {u.totals.tokens.input.toLocaleString()}</span>
            <span>cached {u.totals.tokens.cached_input.toLocaleString()}</span>
            <span>output {u.totals.tokens.output.toLocaleString()}</span>
            <span>reasoning {u.totals.tokens.reasoning.toLocaleString()}</span>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-1 font-mono text-[10px] uppercase tracking-widest">
            <span className="mr-1 text-ink-faint">by</span>
            {(["agent", "project", "provider", "model", "day"] as const).map((d) => (
              <button key={d} onClick={() => setDim(d)}
                      className={`rounded-sm border px-2 py-1 ${
                        dim === d ? "border-brand/50 text-brand" : "border-line text-ink-faint hover:text-ink"}`}>
                {d}
              </button>
            ))}
          </div>
          <div className="mt-2 space-y-1">
            {(u.breakdowns[dim] || []).filter((r) => r.requests > 0).length === 0 ? (
              <p className="text-sm text-ink-faint">No usage recorded in this window.</p>
            ) : (
              (u.breakdowns[dim] || []).map((r) => (
                <div key={String(r.key)} className="flex flex-wrap items-center gap-x-3 border-b border-line/40 py-1 text-sm last:border-0">
                  <span className="min-w-0 flex-1 truncate font-mono text-xs">{r.key || "(unattributed)"}</span>
                  <span className="font-mono text-[11px] tabular-nums text-ink-mute">
                    {r.total_tokens.toLocaleString()} tok
                  </span>
                  <span className="font-mono text-[11px] tabular-nums text-ink-mute">
                    {r.reported_cost > 0 && `$${r.reported_cost.toFixed(2)} rep`}
                    {r.reported_cost > 0 && r.estimated_cost > 0 && " + "}
                    {r.estimated_cost > 0 && `$${r.estimated_cost.toFixed(2)} est`}
                    {r.reported_cost === 0 && r.estimated_cost === 0 && "cost unknown"}
                  </span>
                </div>
              ))
            )}
          </div>
          <p className="mt-3 text-[11px] text-ink-faint">{u.byok_note}</p>
        </>
      )}
    </Card>
  );
}
