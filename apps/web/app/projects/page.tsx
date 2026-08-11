"use client";
import { useState } from "react";
import { api, euros } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { ActiveInstructions } from "@/components/instructions";
import { Button, Card, EmptyState, ErrorBox, Input, Loading, Pill, SectionHeader } from "@/components/ui";

type Money = Record<string, number>;
type ProjectMetrics = {
  runtime_total_seconds: number; runtime_week_seconds: number; operations: number;
  ai_cost: { reported: number; estimated: number; unknown_requests: number; currency: string };
  ai_tokens_total: number; capital_deployed_cents: number;
  verified_revenue_month: Money; verified_mrr: Money; crew_roles: string[];
  pending_approvals: number; experiments: number; active_instructions: number;
};
type Project = {
  id: string; name: string; description: string; status: string;
  strategy: string | null; urls: Record<string, string>; currency: string;
  capital_allocated_cents: number; metrics: ProjectMetrics;
};
type RevenueEvent = {
  id: string; source: string; source_ref: string | null; description: string;
  amount_cents: number; currency: string; occurred_at: string; recurring: boolean;
  recurrence_interval: string | null; verification_status: string; manual: boolean;
  last_synced_at: string | null;
};

function fmtRuntime(seconds: number): string {
  if (!seconds) return "0m";
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

function fmtMoney(m: Money): string {
  const parts = Object.entries(m).map(([cur, cents]) =>
    cur === "EUR" ? euros(cents) : `${(cents / 100).toFixed(2)} ${cur}`);
  return parts.length ? parts.join(" + ") : "€0";
}

function aiCostLabel(c: ProjectMetrics["ai_cost"]): string {
  const bits: string[] = [];
  if (c.reported > 0) bits.push(`$${c.reported.toFixed(2)} reported`);
  if (c.estimated > 0) bits.push(`$${c.estimated.toFixed(2)} est.`);
  if (c.unknown_requests > 0) bits.push(`${c.unknown_requests} req unknown`);
  return bits.length ? bits.join(" · ") : "$0.00";
}

const STATUS_COLORS: Record<string, string> = {
  active: "green", experiment: "blue", hold: "yellow", killed: "red", completed: "gray",
};

function RevenueForm({ projectId, onDone }: { projectId: string; onDone: () => void }) {
  const [amount, setAmount] = useState("");
  const [desc, setDesc] = useState("");
  const [recurring, setRecurring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="mt-2 space-y-2 rounded-md border border-line bg-ground/40 p-3">
      <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
        record verified revenue — manual entry (labeled MANUAL, never impersonates Stripe)
      </div>
      <div className="flex flex-wrap gap-2">
        <Input placeholder="Amount in EUR, e.g. 20.00" value={amount} inputMode="decimal"
               onChange={(e) => setAmount(e.target.value)} className="sm:max-w-40" />
        <Input placeholder="Description (e.g. Invoice #12 paid)" value={desc}
               onChange={(e) => setDesc(e.target.value)} className="sm:max-w-72" />
        <label className="flex min-h-[42px] items-center gap-2 text-sm text-ink-mute">
          <input type="checkbox" checked={recurring}
                 onChange={(e) => setRecurring(e.target.checked)} />
          monthly recurring (counts toward MRR)
        </label>
        <Button onClick={async () => {
          setError(null);
          const cents = Math.round(parseFloat(amount.replace(",", ".")) * 100);
          if (!Number.isFinite(cents) || cents <= 0) { setError("enter a positive amount"); return; }
          try {
            await api(`/projects/${projectId}/revenue`, { body: {
              amount_cents: cents, currency: "EUR", description: desc,
              recurring, recurrence_interval: recurring ? "monthly" : null,
            }});
            setAmount(""); setDesc(""); setRecurring(false); onDone();
          } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
        }}>Record</Button>
      </div>
      {error && <p className="text-xs text-crit">{error}</p>}
    </div>
  );
}

function ProjectCard({ p, onChange }: { p: Project; onChange: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<{ revenue_events: RevenueEvent[] } | null>(null);
  const m = p.metrics;

  async function toggleExpand() {
    if (!expanded) {
      setDetail(await api(`/projects/${p.id}`));
    }
    setExpanded(!expanded);
  }

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="min-w-0 truncate text-base font-semibold text-ink">{p.name}</h3>
        <Pill color={STATUS_COLORS[p.status] || "gray"} pulse={p.status === "active"}>
          {p.status}
        </Pill>
        {m.pending_approvals > 0 && (
          <Pill color="yellow" pulse>{m.pending_approvals} approval{m.pending_approvals > 1 ? "s" : ""}</Pill>
        )}
        <div className="ml-auto flex flex-wrap gap-3 font-mono text-[10px] uppercase tracking-widest text-ink-faint">
          {p.urls.website && (
            <a href={p.urls.website} target="_blank" rel="noreferrer"
               className="min-w-0 truncate text-signal hover:underline">{p.urls.website.replace(/^https?:\/\//, "")}</a>
          )}
          {p.urls.repository && (
            <a href={p.urls.repository} target="_blank" rel="noreferrer"
               className="hover:text-ink">repo</a>
          )}
        </div>
      </div>
      {p.description && <p className="mt-1 text-sm text-ink-mute">{p.description}</p>}

      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        <div>
          <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">verified MRR</div>
          <div className="font-mono text-lg font-semibold text-ok">{fmtMoney(m.verified_mrr)}</div>
        </div>
        <div>
          <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">revenue 30d</div>
          <div className="font-mono text-lg font-semibold">{fmtMoney(m.verified_revenue_month)}</div>
        </div>
        <div>
          <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">capital deployed</div>
          <div className="font-mono text-lg font-semibold">{euros(m.capital_deployed_cents)}</div>
          <div className="text-[10px] text-ink-faint">of {euros(p.capital_allocated_cents)} allocated</div>
        </div>
        <div>
          <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">AI cost</div>
          <div className="truncate font-mono text-sm font-semibold">{aiCostLabel(m.ai_cost)}</div>
          <div className="text-[10px] text-ink-faint">{m.ai_tokens_total.toLocaleString()} tokens</div>
        </div>
        <div>
          <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">agent runtime</div>
          <div className="font-mono text-sm font-semibold">{fmtRuntime(m.runtime_total_seconds)}</div>
          <div className="text-[10px] text-ink-faint">{fmtRuntime(m.runtime_week_seconds)} this week</div>
        </div>
        <div>
          <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">operations</div>
          <div className="font-mono text-sm font-semibold">{m.operations}</div>
        </div>
        <div>
          <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">crew</div>
          <div className="truncate text-sm">{m.crew_roles.length ? m.crew_roles.join(", ") : "—"}</div>
        </div>
        <div>
          <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">automations</div>
          <div className="font-mono text-sm font-semibold">{m.active_instructions}</div>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        <Button variant="ghost" onClick={toggleExpand}>
          {expanded ? "Hide details" : "Details & revenue"}
        </Button>
      </div>

      {expanded && (
        <div className="mt-2">
          <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
            revenue events (source-backed)
          </div>
          {!detail?.revenue_events?.length ? (
            <p className="mt-1 text-sm text-ink-faint">
              No verified revenue recorded yet. Estimates and pipeline never appear here.
            </p>
          ) : (
            <div className="mt-1 space-y-1">
              {detail.revenue_events.map((e) => (
                <div key={e.id} className="flex flex-wrap items-center gap-2 border-b border-line/50 py-1.5 text-sm last:border-0">
                  <span className={`font-mono font-semibold ${e.verification_status === "reversed" ? "text-ink-faint line-through" : "text-ok"}`}>
                    {euros(e.amount_cents)}{e.currency !== "EUR" ? ` ${e.currency}` : ""}
                  </span>
                  {e.recurring && <Pill color="blue">MRR · {e.recurrence_interval}</Pill>}
                  <Pill color={e.manual ? "yellow" : "green"}>
                    {e.manual ? "manual" : e.source}
                  </Pill>
                  <span className="min-w-0 truncate text-ink-mute">{e.description}</span>
                  <span className="ml-auto font-mono text-[10px] text-ink-faint">
                    {new Date(e.occurred_at).toLocaleDateString()}
                  </span>
                  {e.verification_status !== "reversed" && (
                    <button
                      onClick={async () => {
                        if (window.confirm("Mark as reversed/refunded?")) {
                          await api(`/projects/${p.id}/revenue/${e.id}/reverse`, { body: {} });
                          setDetail(await api(`/projects/${p.id}`));
                          onChange();
                        }
                      }}
                      className="font-mono text-[10px] uppercase text-ink-faint hover:text-crit">
                      reverse
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
          <RevenueForm projectId={p.id} onDone={async () => {
            setDetail(await api(`/projects/${p.id}`));
            onChange();
          }} />
        </div>
      )}
    </Card>
  );
}

function NewProjectForm({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [desc, setDesc] = useState("");
  const [error, setError] = useState<string | null>(null);
  if (!open) return <Button onClick={() => setOpen(true)}>New project</Button>;
  return (
    <Card title="New project">
      <div className="space-y-2">
        <Input placeholder="Name (e.g. Example SaaS)" value={name}
               onChange={(e) => setName(e.target.value)} />
        <Input placeholder="Website URL (optional)" value={website}
               onChange={(e) => setWebsite(e.target.value)} />
        <Input placeholder="What is this activity? (optional)" value={desc}
               onChange={(e) => setDesc(e.target.value)} />
        {error && <ErrorBox message={error} />}
        <div className="flex gap-2">
          <Button onClick={async () => {
            try {
              await api("/projects", { body: {
                name, description: desc, urls: website ? { website } : {},
              }});
              setOpen(false); setName(""); setWebsite(""); setDesc("");
              onDone();
            } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
          }} disabled={!name.trim()}>Create</Button>
          <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
        </div>
      </div>
    </Card>
  );
}

export default function ProjectsPage() {
  const projects = useApi<Project[]>("/projects");
  if (projects.loading) return <Loading />;

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <SectionHeader title="Projects" sub="portfolio · AI-operated activities"
                     action={<NewProjectForm onDone={projects.reload} />} />
      {projects.error && <ErrorBox message="Could not load the portfolio." />}
      {!projects.data?.length ? (
        <EmptyState
          label="portfolio"
          title="No projects yet"
          body="A project is a real activity your crew operates — a SaaS, a newsletter, a consulting funnel. Its verified revenue, capital, AI cost and runtime are tracked from canonical records."
          action={<span className="text-xs text-ink-faint">Create one above, or ask the ◈ Manager.</span>}
        />
      ) : (
        <div className="space-y-4">
          {projects.data.map((p) => (
            <ProjectCard key={p.id} p={p} onChange={projects.reload} />
          ))}
        </div>
      )}
      <ActiveInstructions kind="project_instruction" title="Project operating instructions"
                          hint="No project-specific operating instructions yet." />
    </div>
  );
}
