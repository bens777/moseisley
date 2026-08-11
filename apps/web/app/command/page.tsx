"use client";
import Link from "next/link";
import { api, euros, hours } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { FactoryState } from "@/components/factory-toggle";
import { StoppedBanner } from "@/components/shell";
import {
  Button, Card, EmptyState, ErrorBox, Insignia, Loading, Pill, Progress,
  SectionLabel, Stat,
} from "@/components/ui";

type Orchestrator = { configured: boolean; provider: string | null; model: string | null };
type UsageSummary = {
  today: { total_tokens: number; reported_cost: number; estimated_cost: number;
           unknown_cost_requests: number };
  month: { reported_cost: number; estimated_cost: number };
};
type Connection = { id: string; integration_type: string };
type Money = Record<string, number>;
type Overview = {
  runtime_week: { total_seconds: number; runs: number; by_role: Record<string, number> };
  usage_week: { tokens: { total: number }; reported_cost: number; estimated_cost: number;
                unknown_cost_requests: number };
  treasury: { spending_enabled: boolean; available_cents: number; currency: string };
  capital_deployed_cents: number;
  verified_revenue_month: Money;
  verified_mrr: Money;
  operations_completed: number;
  pending_approvals: number;
  active_projects: number;
};

function fmtRuntime(seconds: number): string {
  if (!seconds) return "0m";
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function fmtMoneyMap(m: Money): string {
  const parts = Object.entries(m).map(([cur, cents]) =>
    cur === "EUR" ? euros(cents) : `${(cents / 100).toFixed(2)} ${cur}`);
  return parts.length ? parts.join(" + ") : "€0";
}

function aiCost(o: Overview["usage_week"]): { value: string; hint: string } {
  const bits: string[] = [];
  if (o.reported_cost > 0) bits.push(`$${o.reported_cost.toFixed(2)}`);
  if (o.estimated_cost > 0) bits.push(`$${o.estimated_cost.toFixed(2)} est.`);
  const value = bits.length ? bits.join(" + ") : "$0.00";
  const hint = o.unknown_cost_requests > 0
    ? `${o.unknown_cost_requests} req unknown cost · BYOK`
    : "billed by your providers (BYOK)";
  return { value, hint };
}

function InitSequence({ orch, factoryOn, modeLabel, hasGoals, hasConnections, scanned }: {
  orch: Orchestrator | null; factoryOn: boolean; modeLabel: string; hasGoals: boolean;
  hasConnections: boolean; scanned: boolean;
}) {
  const aiByFactory = factoryOn && !orch?.configured;
  const steps = [
    aiByFactory
      ? { done: true, label: `${modeLabel} AI online`,
          sub: modeLabel === "Dev" ? "your OpenRouter key — free models"
                                   : "platform AI included — zero config", href: "/settings" }
      : { done: !!orch?.configured, label: "Connect your AI", sub: "provider key + orchestrator model", href: "/connections" },
    { done: hasGoals, label: "Define your objective", sub: "tell your crew the mission", href: "/goals" },
    { done: hasConnections, label: "Connect your world", sub: "email, calendar or demo data", href: "/connections" },
    { done: scanned, label: "Run X-Ray", sub: "analyze your last 90 days", href: "/xray" },
  ];
  const next = steps.find((s) => !s.done);
  return (
    <Card title="Command center initialization" tone="attention">
      <ol className="space-y-2">
        {steps.map((s, i) => (
          <li key={s.label} className="flex min-h-[40px] flex-wrap items-center gap-3">
            <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-sm border font-mono text-[11px] ${
              s.done ? "border-ok/40 bg-ok/10 text-ok" : "border-line-strong text-ink-faint"
            }`} aria-hidden>{s.done ? "✓" : i + 1}</span>
            <span className={`text-sm font-medium ${s.done ? "text-ink-faint line-through" : ""}`}>
              {s.label}
            </span>
            <span className="font-mono text-[10px] uppercase tracking-wide text-ink-faint">{s.sub}</span>
            {!s.done && s === next && (
              <Link href={s.href} className="ml-auto">
                <Button>Continue setup</Button>
              </Link>
            )}
          </li>
        ))}
      </ol>
      {aiByFactory && (
        <Link href="/connections"
              className="mt-2 inline-block font-mono text-[10px] uppercase tracking-wide text-ink-faint hover:text-ink-mute hover:underline">
          Use my own keys instead →
        </Link>
      )}
    </Card>
  );
}

type Today = {
  goal_trajectory: string;
  goals: { id: string; title: string; progress: number; deadline: string | null }[];
  top_actions: { title: string; why?: string }[];
  no_action: boolean | null;
  strategist_summary: string | null;
  value_found_this_month: {
    verified_money_cents: number;
    estimated_opportunity_cents: number;
    estimated_time_recoverable_minutes: number;
  };
  market_status: string;
  treasury: { monthly_limit_cents: number | null; spent_this_month_cents: number; spending_enabled: boolean };
  needs_you: number;
  handled_automatically: number;
};

type Agent = { id: string; adapter_type: string; display_name: string; enabled: boolean;
               is_active: boolean; health_status: string };
type Finding = { id: string; type: string; title: string; verified: boolean;
                 estimated_value_cents: number | null };
type Latest = { findings: Record<string, Finding[]> };

function greeting(): string {
  const h = new Date().getHours();
  if (h < 5) return "Night watch";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

export default function CommandCenterPage() {
  const today = useApi<Today>("/today");
  const agents = useApi<Agent[]>("/agents");
  const intel = useApi<Latest>("/xray/latest");
  const orch = useApi<Orchestrator>("/orchestrator");
  const settings = useApi<FactoryState>("/settings");
  const usage = useApi<UsageSummary>("/usage/summary");
  const connections = useApi<Connection[]>("/integrations");
  const overview = useApi<Overview>("/metrics/overview");
  const projects = useApi<{ id: string; name: string; status: string;
    metrics: { verified_mrr: Money; capital_deployed_cents: number;
               runtime_total_seconds: number } }[]>("/projects");

  if (today.loading) return <Loading />;
  if (today.error) return <ErrorBox message={today.error} />;
  const d = today.data;
  if (!d) return null;

  const scanned0 = d.market_status !== "NOT YET SCANNED";
  // the "connect AI" step is satisfied by ROOKIE (platform AI) or by DEV with
  // the user's own OpenRouter key connected
  const rookieOn = settings.data?.ai_mode === "factory"
    && !!settings.data?.factory.available
    && settings.data?.factory.tier !== "expired";
  const devOn = settings.data?.ai_mode === "dev" && !!settings.data?.dev_key_connected;
  const factoryOn = rookieOn || devOn;
  const needsSetup = !orch.loading && !settings.loading
    && (!(orch.data?.configured || factoryOn) || d.goals.length === 0);

  const crewReady = (agents.data || []).filter((a) => a.enabled && a.health_status !== "error");
  const activeAgent = (agents.data || []).find((a) => a.is_active);
  const findings = intel.data
    ? Object.values(intel.data.findings).flat().filter((f) => f.verified).slice(0, 4)
    : [];
  const scanned = d.market_status !== "NOT YET SCANNED";

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <StoppedBanner />
      {/* top command bar */}
      <div className="rounded-md border border-line bg-panel/80 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.25em] text-ink-faint">
              moseisley.sh
            </div>
            <h1 className="text-xl font-bold tracking-tight">Command Center</h1>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 font-mono text-[11px] uppercase tracking-wide text-ink-mute">
            <span className="flex items-center gap-1.5">
              <span className={`led ${crewReady.length ? "bg-ok led-pulse" : "bg-ink-faint"}`} aria-hidden />
              {crewReady.length} crew ready
            </span>
            <span className="flex items-center gap-1.5">
              <span className={`led ${scanned ? "bg-signal" : "bg-ink-faint"}`} aria-hidden />
              {scanned ? `radar: ${d.market_status.toLowerCase()}` : "radar offline"}
            </span>
            <span className="flex items-center gap-1.5">
              <span className={`led ${d.needs_you ? "bg-warn led-pulse" : "bg-ok"}`} aria-hidden />
              {d.needs_you ? `${d.needs_you} decision${d.needs_you > 1 ? "s" : ""} pending` : "all systems nominal"}
            </span>
          </div>
        </div>
        <p className="mt-2 text-sm text-ink-mute">
          {greeting()}. Here is what your crew is doing.
          {orch.data?.configured && (
            <span className="ml-2 font-mono text-[11px] uppercase tracking-wide text-ink-faint">
              orchestrator: {orch.data.provider} · {orch.data.model}
            </span>
          )}
        </p>
      </div>

      {needsSetup && (
        <InitSequence orch={orch.data} factoryOn={factoryOn}
                      modeLabel={devOn ? "Dev" : "Rookie"} hasGoals={d.goals.length > 0}
                      hasConnections={(connections.data || []).length > 0}
                      scanned={scanned0} />
      )}

      {/* needs you — most prominent when non-zero */}
      {d.needs_you > 0 ? (
        <Card tone="attention">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <span className="flex h-11 w-11 items-center justify-center rounded-sm border border-warn/50 bg-warn/10 font-mono text-lg text-warn" aria-hidden>
                !
              </span>
              <div>
                <SectionLabel tone="brand">needs you</SectionLabel>
                <div className="font-display text-lg font-semibold">
                  {d.needs_you} decision{d.needs_you > 1 ? "s" : ""} waiting for your approval
                </div>
              </div>
            </div>
            <Link href="/money">
              <Button>Review now</Button>
            </Link>
          </div>
        </Card>
      ) : (
        <div className="flex items-center gap-2 rounded-md border border-line bg-panel/50 px-4 py-2.5 font-mono text-[11px] uppercase tracking-widest text-ink-mute">
          <span className="led bg-ok" aria-hidden /> needs you: nothing — your crew has the deck
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        {/* mission progress */}
        <Card title="Mission progress" className="lg:col-span-2">
          {d.goals.length === 0 ? (
            <EmptyState
              label="mission control"
              title="No active mission"
              body="Define what you want your crew to accomplish."
              action={<Link href="/goals"><Button>Create first goal</Button></Link>}
            />
          ) : (
            <div className="space-y-3">
              {d.goals.map((g) => (
                <div key={g.id} className="min-w-0">
                  <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                    <span className="min-w-0 break-words text-sm font-medium">{g.title}</span>
                    <span className="font-mono text-[11px] text-ink-faint">
                      {g.deadline ? `T-minus ${g.deadline}` : "no deadline"}
                    </span>
                  </div>
                  <Progress value={g.progress} />
                </div>
              ))}
              <div className="flex items-center gap-2 border-t border-line pt-2">
                <Pill color={{ "ON TRACK": "green", "AT RISK": "red" }[d.goal_trajectory] || "gray"}>
                  {d.goal_trajectory}
                </Pill>
                <Link href="/goals" className="text-xs text-ink-faint hover:text-ink-mute">
                  manage goals →
                </Link>
              </div>
            </div>
          )}
        </Card>

        {/* crew status */}
        <Card title="Crew status">
          {agents.loading ? (
            <Loading />
          ) : (
            <div className="space-y-2.5">
              {(agents.data || []).map((a) => (
                <div key={a.id} className="flex min-w-0 items-center gap-2.5">
                  <Insignia kind={a.adapter_type} size="sm" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{a.display_name}</div>
                    <div className="font-mono text-[10px] uppercase tracking-wide text-ink-faint">
                      {a.is_active ? "answering your comms" : "standing by"}
                    </div>
                  </div>
                  <Pill color={a.health_status === "error" ? "red" : a.is_active ? "green" : "gray"}
                        pulse={a.is_active}>
                    {a.health_status === "error" ? "fault" : a.is_active ? "active" : "ready"}
                  </Pill>
                </div>
              ))}
              <Link href="/agents" className="block pt-1 text-xs text-ink-faint hover:text-ink-mute">
                manage crew →
              </Link>
            </div>
          )}
        </Card>
      </div>

      {/* today's operations */}
      <Card title="Today's operations"
            action={
              <Button variant="ghost" onClick={async () => { await api("/strategist/run", { body: {} }); today.reload(); }}>
                Run strategist
              </Button>
            }>
        {d.no_action ? (
          <p className="flex flex-wrap items-center gap-2 text-sm text-ink-mute">
            <Pill color="green">NO_ACTION</Pill> Nothing materially needs you today.
          </p>
        ) : d.top_actions.length === 0 ? (
          <EmptyState
            label="strategist idle"
            title="No operations planned yet"
            body="The Strategist reviews your goals and findings every morning — or run it now."
            action={
              <Button onClick={async () => { await api("/strategist/run", { body: {} }); today.reload(); }}>
                Plan today's operations
              </Button>
            }
          />
        ) : (
          <ol className="space-y-2">
            {d.top_actions.map((a, i) => (
              <li key={i} className="flex min-w-0 gap-3 text-sm">
                <span className="shrink-0 font-mono text-ink-faint">{String(i + 1).padStart(2, "0")}</span>
                <span className="min-w-0 break-words">
                  <span className="font-medium">{a.title}</span>
                  {a.why && <span className="ml-2 text-ink-mute">— {a.why}</span>}
                </span>
              </li>
            ))}
          </ol>
        )}
        {d.strategist_summary && (
          <p className="mt-3 break-words border-t border-line pt-3 text-xs text-ink-faint">{d.strategist_summary}</p>
        )}
      </Card>

      {/* REAL operational KPIs (§2, §51) — aggregated from canonical records */}
      {overview.data && (() => {
        const o = overview.data;
        const cost = aiCost(o.usage_week);
        return (
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Card><Stat label="Crew runtime" value={fmtRuntime(o.runtime_week.total_seconds)}
                        hint={`${o.runtime_week.runs} runs this week · actual execution time`} /></Card>
            <Card><Stat label="AI tokens" value={fmtTokens(o.usage_week.tokens.total)}
                        hint="this week · provider-reported" /></Card>
            <Card><Stat label="AI cost" value={cost.value} hint={cost.hint} /></Card>
            <Card><Stat label="Treasury"
                        value={o.treasury.spending_enabled ? euros(o.treasury.available_cents) : "off"}
                        hint={o.treasury.spending_enabled ? "available for your crew" : "spending disabled"} /></Card>
            <Card><Stat label="Capital deployed" value={euros(o.capital_deployed_cents)}
                        hint="actually spent by crew" /></Card>
            <Card><Stat label="Verified revenue" value={fmtMoneyMap(o.verified_revenue_month)}
                        hint="30d · source-backed only" /></Card>
            <Card><Stat label="Verified MRR" value={fmtMoneyMap(o.verified_mrr)}
                        hint="recurring, verified ≤35d" /></Card>
            <Card><Stat label="Pending" value={o.pending_approvals}
                        hint={`approvals · ${o.active_projects} active project${o.active_projects === 1 ? "" : "s"}`} /></Card>
          </div>
        );
      })()}

      {/* portfolio results — real per-project numbers (§10, §50) */}
      {(projects.data || []).length > 0 && (
        <Card title="Portfolio results"
              action={<Link href="/projects" className="text-xs text-ink-faint hover:text-ink-mute">open portfolio →</Link>}>
          <div className="space-y-2">
            {(projects.data || []).slice(0, 4).map((p) => (
              <div key={p.id} className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 border-b border-line/50 pb-2 text-sm last:border-0 last:pb-0">
                <span className="min-w-0 truncate font-medium">{p.name}</span>
                <span className="font-mono text-[11px] uppercase tracking-wide text-ink-faint">{p.status}</span>
                <span className="ml-auto flex flex-wrap gap-x-4 font-mono text-[11px] tabular-nums text-ink-mute">
                  <span>MRR <span className="text-ok">{fmtMoneyMap(p.metrics.verified_mrr)}</span></span>
                  <span>deployed {euros(p.metrics.capital_deployed_cents)}</span>
                  <span>runtime {fmtRuntime(p.metrics.runtime_total_seconds)}</span>
                </span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* ESTIMATED user value — conservative, visually separate from verified results (§52) */}
      {(d.value_found_this_month.estimated_opportunity_cents > 0
        || d.value_found_this_month.estimated_time_recoverable_minutes > 0
        || d.value_found_this_month.verified_money_cents > 0) && (
        <Card title="Estimated user value — conservative (not business results)">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Stat label="Money found (X-Ray)" value={euros(d.value_found_this_month.verified_money_cents)}
                  hint="evidence-backed recoverable, 30d" />
            <Stat label="Est. opportunity" value={`≥ ${euros(d.value_found_this_month.estimated_opportunity_cents)}`}
                  hint="ESTIMATED — never counted as revenue" />
            <Stat label="Est. time recoverable" value={`≥ ${hours(d.value_found_this_month.estimated_time_recoverable_minutes)}`}
                  hint="ESTIMATED, conservative lower bound" />
          </div>
          <p className="mt-3 border-t border-line pt-2 text-[11px] text-ink-faint">
            These are X-Ray opportunity estimates about YOUR time and money — separate
            from crew runtime and verified revenue above. Methodology: lowest defensible
            value from evidence-backed findings; details on the X-Ray page.
          </p>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {/* radar */}
        <Card title="Radar">
          {!scanned ? (
            <EmptyState
              label="radar offline"
              title="No market scan yet"
              bullets={["competitor moves", "buyer demand signals", "market shifts"]}
              action={<Link href="/market"><Button>Start first scan</Button></Link>}
            />
          ) : (
            <div className={`flex items-center justify-between gap-3 rounded-sm border border-line bg-ground/60 p-3 ${d.market_status === "NO MATERIAL CHANGE" ? "" : "scanline"}`}>
              <div className="flex min-w-0 items-center gap-3">
                <Insignia kind="radar" size="sm" />
                <div className="min-w-0">
                  <div className="font-mono text-[11px] uppercase tracking-wide text-signal">
                    {d.market_status}
                  </div>
                  <div className="text-xs text-ink-faint">latest sweep result</div>
                </div>
              </div>
              <Link href="/market" className="shrink-0 text-xs text-ink-faint hover:text-ink-mute">
                open radar →
              </Link>
            </div>
          )}
        </Card>

        {/* recent intelligence */}
        <Card title="Recent intelligence">
          {intel.loading ? (
            <Loading />
          ) : findings.length === 0 ? (
            <EmptyState
              label="x-ray"
              title="No verified findings yet"
              body="Run an X-Ray over your last 90 days to surface unpaid invoices, dropped leads and recoverable time."
              action={<Link href="/xray"><Button>Run X-Ray</Button></Link>}
            />
          ) : (
            <div className="space-y-2">
              {findings.map((f) => (
                <div key={f.id} className="flex min-w-0 items-center justify-between gap-2 text-sm">
                  <span className="min-w-0 truncate">{f.title}</span>
                  {f.estimated_value_cents != null && (
                    <span className="shrink-0 font-mono text-xs text-brand">{euros(f.estimated_value_cents)}</span>
                  )}
                </div>
              ))}
              <Link href="/xray" className="block pt-1 text-xs text-ink-faint hover:text-ink-mute">
                full intelligence report →
              </Link>
            </div>
          )}
        </Card>
      </div>

      {/* treasury strip */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line bg-panel/50 px-4 py-3">
        <div className="flex items-center gap-3">
          <Insignia kind="treasury" size="sm" />
          <span className="font-mono text-[11px] uppercase tracking-widest text-ink-mute">treasury</span>
          <span className="font-mono text-sm tabular-nums">
            {euros(d.treasury.spent_this_month_cents)}
            <span className="text-ink-faint"> / {euros(d.treasury.monthly_limit_cents)}</span>
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Pill color={d.treasury.spending_enabled ? "green" : "gray"}>
            spending {d.treasury.spending_enabled ? "on" : "off"}
          </Pill>
          <Link href="/money" className="text-xs text-ink-faint hover:text-ink-mute">manage →</Link>
        </div>
      </div>

      {/* AI usage strip — persisted usage only, labeled sources */}
      {usage.data && (usage.data.month.reported_cost > 0 || usage.data.month.estimated_cost > 0
        || usage.data.today.total_tokens > 0) && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line bg-panel/50 px-4 py-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px] uppercase tracking-wide text-ink-mute">
            <span className="tracking-widest text-ink-faint">ai usage</span>
            <span>today: {usage.data.today.total_tokens.toLocaleString()} tokens</span>
            {usage.data.month.reported_cost > 0 && (
              <span>30d reported: ${usage.data.month.reported_cost.toFixed(2)}</span>
            )}
            {usage.data.month.estimated_cost > 0 && (
              <span>30d estimated: ${usage.data.month.estimated_cost.toFixed(2)}</span>
            )}
            {usage.data.today.unknown_cost_requests > 0 && (
              <span className="text-warn">{usage.data.today.unknown_cost_requests} unknown-cost req</span>
            )}
          </div>
          <Link href="/settings" className="text-xs text-ink-faint hover:text-ink-mute">details →</Link>
        </div>
      )}

      {activeAgent && (
        <p className="text-center font-mono text-[10px] uppercase tracking-widest text-ink-faint">
          comms routed through {activeAgent.display_name}
        </p>
      )}
    </div>
  );
}
