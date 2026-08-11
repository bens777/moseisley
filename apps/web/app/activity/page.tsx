"use client";
import { useState } from "react";
import { useApi } from "@/lib/hooks";
import { Card, EmptyState, ErrorBox, Loading } from "@/components/ui";

type Event = {
  id: string; event_type: string; actor_type: string; actor_id: string | null;
  entity_type: string | null; entity_id: string | null;
  payload: Record<string, unknown>; created_at: string;
};

const FILTERS = ["all", "money", "agents", "market", "goals", "integrations", "actions"];

/* Human-readable operational timeline (§41): WHO did WHAT and WHY, expandable
   to the full recorded detail. */
const ACTOR_GLYPHS: Record<string, string> = {
  agent: "⬡", user: "◉", system: "◇",
};

function who(e: Event): string {
  if (e.actor_type === "agent") return (e.actor_id || "crew").toUpperCase();
  if (e.actor_type === "user") return "YOU";
  return "SYSTEM";
}

function headline(e: Event): string {
  const p = e.payload || {};
  const s = (k: string) => (p[k] != null ? String(p[k]) : "");
  switch (e.event_type) {
    case "crew_run_started": return `started: ${s("task") || s("role") || "operation"}`;
    case "crew_run_completed": return `completed: ${s("task") || s("role") || "operation"}`;
    case "revenue_recorded":
      return `verified revenue recorded — ${(Number(p.amount_cents || 0) / 100).toFixed(2)} ${s("currency")}${p.recurring ? " (recurring)" : ""}`;
    case "revenue_reversed": return "revenue event reversed";
    case "instruction_created": return `new instruction: ${s("name")} (${s("kind")})`;
    case "instruction_updated": return `instruction updated → v${s("version")}`;
    case "instruction_toggled": return `instruction ${p.enabled ? "enabled" : "disabled"}`;
    case "instruction_run_completed": return `instruction run finished — ${s("summary").slice(0, 80)}`;
    case "market_report_created":
      return `market report: ${s("instruction")} — ${s("sentiment").replace(/_/g, " ")} (${s("material_changes")} material)`;
    case "market_brief_delivered": return "market brief delivered to Telegram";
    case "dev_proposal_created": return `dev proposal: ${s("title")}`;
    case "dev_patch_ready": return `dev patch ready ${String(p.patch_hash || "").slice(0, 10)} — tests ${p.tests_passed ? "passed" : "FAILED"}`;
    case "dev_proposal_approved": return `dev proposal approved (${s("channel")})`;
    case "dev_proposal_merged": return `dev proposal merged → ${String(p.commit || "").slice(0, 8)}`;
    case "manager_draft_created": return `manager drafted: ${s("name")} (${s("kind")})`;
    case "manager_draft_saved": return `manager draft saved: ${s("name")}`;
    case "goal_created": return `goal created: ${s("title")}`;
    case "goal_updated": return `goal updated: ${Object.keys(p).join(", ")}`;
    case "spend_requested": return `spend requested — €${(Number(p.amount_cents || 0) / 100).toFixed(2)} ${s("purpose").slice(0, 60)}`;
    case "approval_requested": return `approval needed: ${s("action_type")}`;
    case "approval_resolved": return `approval ${p.approved ? "APPROVED" : "denied"} via ${s("channel")}`;
    default:
      return Object.entries(p).slice(0, 3)
        .map(([k, v]) => `${k}: ${typeof v === "object" ? "…" : String(v).slice(0, 50)}`)
        .join(" · ") || e.event_type.replace(/_/g, " ");
  }
}

function Row({ e }: { e: Event }) {
  const [expanded, setExpanded] = useState(false);
  const time = new Date(e.created_at);
  return (
    <div className="border-b border-line/60 py-2 last:border-0">
      <button onClick={() => setExpanded(!expanded)}
              className="flex w-full min-w-0 flex-wrap items-baseline gap-x-3 gap-y-0.5 text-left text-sm">
        <span className="shrink-0 font-mono text-xs tabular-nums text-ink-faint">
          {time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
        </span>
        <span className={`shrink-0 font-mono text-[11px] font-bold uppercase tracking-wide ${
          e.actor_type === "agent" ? "text-brand" : e.actor_type === "user" ? "text-ok" : "text-ink-faint"}`}>
          <span aria-hidden className="mr-1">{ACTOR_GLYPHS[e.actor_type] || "◇"}</span>{who(e)}
        </span>
        <span className="min-w-0 flex-1 break-words font-medium text-ink">
          {e.event_type.replace(/_/g, " ")}
        </span>
        <span className="min-w-0 basis-full break-words pl-0 text-xs text-ink-mute sm:pl-16">
          {headline(e)}
        </span>
      </button>
      {expanded && (
        <div className="mt-2 rounded-sm bg-ground/70 p-2">
          <div className="mb-1 font-mono text-[10px] uppercase tracking-widest text-ink-faint">
            {time.toLocaleString()} · actor {e.actor_type}{e.actor_id ? `/${e.actor_id}` : ""}
            {e.entity_type ? ` · ${e.entity_type} ${String(e.entity_id || "").slice(0, 12)}` : ""}
          </div>
          <pre className="max-h-56 overflow-auto font-mono text-[10px] leading-relaxed text-signal">
            {JSON.stringify(e.payload, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

function dayLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "today";
  if (d.toDateString() === yesterday.toDateString()) return "yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

export default function ActivityPage() {
  const [filter, setFilter] = useState("all");
  const { data, error, loading } = useApi<Event[]>("/activity", { filter });

  const groups: [string, Event[]][] = [];
  for (const e of data || []) {
    const label = dayLabel(e.created_at);
    const last = groups[groups.length - 1];
    if (last && last[0] === label) last[1].push(e);
    else groups.push([label, [e]]);
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div>
        <h1 className="text-xl font-bold">Activity</h1>
        <p className="mt-0.5 font-mono text-[11px] uppercase tracking-widest text-ink-faint">
          operational timeline · append-only ledger
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button key={f} onClick={() => setFilter(f)}
                  className={`min-h-[36px] rounded-full px-3 py-1 text-xs capitalize ${
                    filter === f ? "bg-brand text-ground" : "bg-raised text-ink-mute hover:text-ink"
                  }`}>
            {f}
          </button>
        ))}
      </div>
      {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
        !data?.length ? (
          <EmptyState
            label="ops log"
            title="No operations recorded yet"
            body="Every action your crew takes — runs, reports, drafts, approvals, spend, revenue — lands here permanently, with who did it, why, and the full recorded detail on tap."
          />
        ) : (
          <div className="space-y-4">
            {groups.map(([label, events]) => (
              <Card key={label} title={label}>
                <div>
                  {events.map((e) => <Row key={e.id} e={e} />)}
                </div>
              </Card>
            ))}
          </div>
        )
      )}
    </div>
  );
}
