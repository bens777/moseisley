"use client";
/* eslint-disable @next/next/no-img-element */
import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { AgentWizard } from "@/components/agent-wizard";
import { ActiveInstructions } from "@/components/instructions";
import { RuntimeReference, type Runtime as RuntimeProfile } from "@/components/runtimes";
import {
  Button, Card, ErrorBox, Input, Insignia, Loading, Pill, SectionHeader, SectionLabel,
} from "@/components/ui";

type CrewMember = {
  role: string; name: string; mission: string; enabled: boolean;
  model_policy: string; provider: string | null; model: string | null;
  uses_default_prompt: boolean; prompt_version: number; runtime: string;
  last_run: { status: string; task: string | null; finished_at: string | null } | null;
  usage_month: { requests: number; total_tokens: number; reported_cost: number;
                 estimated_cost: number } | null;
};
type CrewState = { orchestrator: { provider?: string; model?: string }; crew: CrewMember[] };
type Runtime = {
  id: string; adapter_type: string; display_name: string; is_active: boolean;
  configuration?: { avatar?: string; role?: string };
  health_status: string; has_credentials: boolean;
};

const ROLE_GLYPHS: Record<string, string> = {
  orchestrator: "⌂", strategist: "△", challenger: "◮", xray: "⊘", radar: "◎",
  auditor: "≜", goal_compiler: "◬", follow_up: "↻", commitment_tracker: "◉",
  inbox_triage: "≡", manager: "◈", dev: "⌬",
};

type Overview = { runtime_week: { by_role: Record<string, number> } };
type UsageBreakdown = { breakdowns: { agent: { key: string | null; requests: number;
  total_tokens: number; reported_cost: number; estimated_cost: number }[] } };

function fmtRuntime(seconds: number | undefined): string {
  if (!seconds) return "0m";
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

/* ── AI organization map (§18-§19): hierarchy on desktop, tree on mobile.
     Deterministic infrastructure is visually distinct — never shown as an LLM. */
function OrgNode({ m, runtime, usage, featured = false }: {
  m: CrewMember; runtime?: number;
  usage?: { total_tokens: number; reported_cost: number; estimated_cost: number };
  featured?: boolean;
}) {
  const cost = usage ? (usage.reported_cost || 0) + (usage.estimated_cost || 0) : 0;
  return (
    <a href={`#role-${m.role}`}
       className={`block min-w-0 rounded-md border p-2.5 transition hover:border-brand/60 ${
         featured ? "border-brand/40 bg-panel" : "border-line bg-panel/60"}`}>
      <div className="flex items-center gap-2">
        <span aria-hidden className="font-mono text-sm text-brand">{ROLE_GLYPHS[m.role] || "○"}</span>
        <span className="min-w-0 truncate font-mono text-[10px] font-bold uppercase tracking-widest text-ink">
          {m.name}
        </span>
        <span className={`led ${m.enabled ? "bg-ok led-pulse" : "bg-ink-faint"}`} aria-hidden />
      </div>
      <div className="mt-1 truncate font-mono text-[9px] uppercase tracking-wide text-ink-faint">
        {m.model_policy === "custom" ? "custom" : "inherit"} · {m.provider || "—"}
        {m.model ? ` / ${m.model}` : ""}
      </div>
      <div className="mt-0.5 flex flex-wrap gap-x-3 font-mono text-[9px] text-ink-faint">
        <span>wk {fmtRuntime(runtime)}</span>
        {usage && usage.total_tokens > 0 && <span>{(usage.total_tokens / 1000).toFixed(1)}k tok</span>}
        {cost > 0 && <span>${cost.toFixed(2)}</span>}
      </div>
    </a>
  );
}

function InfraNode({ label, glyph }: { label: string; glyph: string }) {
  return (
    <div className="flex min-w-0 items-center gap-2 rounded-md border border-dashed border-line-strong bg-ground/50 px-2.5 py-2">
      <span aria-hidden className="font-mono text-xs text-ink-faint">{glyph}</span>
      <div className="min-w-0">
        <div className="truncate font-mono text-[10px] font-bold uppercase tracking-widest text-ink-mute">{label}</div>
        <div className="font-mono text-[8px] uppercase tracking-wide text-ink-faint">deterministic · not an LLM</div>
      </div>
    </div>
  );
}

function Connector() {
  return <div aria-hidden className="mx-auto h-4 w-px bg-line-strong" />;
}

function OrgGraph({ crew, overview, usage }: {
  crew: CrewMember[]; overview: Overview | null; usage: UsageBreakdown | null;
}) {
  const byRole = new Map(crew.map((m) => [m.role, m]));
  const rt = overview?.runtime_week.by_role || {};
  const us = new Map((usage?.breakdowns.agent || []).map((u) => [u.key, u]));
  const specialists = ["strategist", "dev", "radar", "xray", "challenger", "auditor"]
    .map((r) => byRole.get(r)).filter(Boolean) as CrewMember[];
  const autopilot = ["goal_compiler", "follow_up", "commitment_tracker", "inbox_triage"]
    .map((r) => byRole.get(r)).filter(Boolean) as CrewMember[];
  const manager = byRole.get("manager");
  const orchestrator = byRole.get("orchestrator");

  return (
    <Card title="AI organization">
      <div className="mx-auto max-w-3xl">
        <div className="mx-auto w-fit rounded-md border border-line-strong bg-raised px-4 py-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.25em] text-ink">
          you
        </div>
        <Connector />
        {manager && (
          <>
            <div className="mx-auto max-w-56">
              <OrgNode m={manager} runtime={rt[manager.role]} usage={us.get(manager.role)} featured />
            </div>
            <Connector />
          </>
        )}
        {orchestrator && (
          <>
            <div className="mx-auto max-w-64">
              <OrgNode m={orchestrator} runtime={rt[orchestrator.role]}
                       usage={us.get(orchestrator.role)} featured />
            </div>
            <Connector />
          </>
        )}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {specialists.map((m) => (
            <OrgNode key={m.role} m={m} runtime={rt[m.role]} usage={us.get(m.role)} />
          ))}
        </div>
        <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {autopilot.map((m) => (
            <OrgNode key={m.role} m={m} runtime={rt[m.role]} usage={us.get(m.role)} />
          ))}
        </div>
        <Connector />
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <InfraNode label="Tool Broker" glyph="⇄" />
          <InfraNode label="Policy Engine" glyph="§" />
          <InfraNode label="Treasury" glyph="▣" />
          <InfraNode label="Scheduler" glyph="◷" />
        </div>
        <p className="mt-3 text-center text-[11px] text-ink-faint">
          Tap a station for its full configuration below. Runtime, tokens and cost are
          this week&rsquo;s recorded values.
        </p>
      </div>
    </Card>
  );
}

/* ── Dev Agent proposals (§20-§26) ── */
type DevProposal = {
  id: string; title: string; why: string; expected_benefit: string; risk: string;
  status: string; patch_hash: string | null; patch_stats: { stat?: string };
  test_results: { exit_code?: number; command?: string };
  schema_impact: string; approved_patch_hash: string | null; merged_commit: string | null;
};

function DevProposals() {
  const proposals = useApi<DevProposal[]>("/dev/proposals");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(id: string, path: string) {
    setBusy(id);
    setError(null);
    try { await api(`/dev/proposals/${id}/${path}`, { body: {} }); proposals.reload(); }
    catch (e) { setError(e instanceof Error ? e.message : "failed"); }
    finally { setBusy(null); }
  }

  const statusColor: Record<string, string> = {
    proposed: "blue", patch_ready: "yellow", approved: "green", rejected: "gray",
    merged: "green", failed: "red",
  };

  return (
    <Card title="Dev Agent — platform proposals">
      {error && <ErrorBox message={error} />}
      {!proposals.data?.length ? (
        <p className="text-sm text-ink-faint">
          No proposals yet. Configure the weekly Dev review below (or ask the ◈ Manager:
          &ldquo;review the platform every Friday morning&rdquo;) — the Dev Agent analyzes
          telemetry and the codebase, proposes improvements, and prepares tested patches
          in an isolated branch. Nothing merges without your explicit approval bound to
          the exact patch.
        </p>
      ) : (
        <div className="space-y-3">
          {proposals.data.map((p) => (
            <div key={p.id} className="min-w-0 rounded-md border border-line p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 break-words text-sm font-medium text-ink">{p.title}</span>
                <Pill color={statusColor[p.status] || "gray"}>{p.status.replace("_", " ")}</Pill>
                <Pill color={p.risk === "high" ? "red" : p.risk === "medium" ? "yellow" : "gray"}>
                  risk {p.risk}
                </Pill>
              </div>
              <p className="mt-1 break-words text-xs text-ink-mute">{p.why}</p>
              {p.patch_hash && (
                <div className="mt-1 font-mono text-[10px] text-ink-faint">
                  patch {p.patch_hash.slice(0, 12)}
                  {p.test_results?.exit_code != null &&
                    (p.test_results.exit_code === 0 ? " · tests passed" : " · TESTS FAILED")}
                  {p.schema_impact && p.schema_impact !== "none" && " · schema migration"}
                  {p.merged_commit && ` · merged ${p.merged_commit.slice(0, 8)}`}
                </div>
              )}
              <div className="mt-2 flex flex-wrap gap-1.5">
                {p.status === "proposed" && (
                  <Button variant="ghost" disabled={busy === p.id}
                          onClick={() => act(p.id, "prepare-patch")}>
                    {busy === p.id ? "Preparing…" : "Prepare patch"}
                  </Button>
                )}
                {p.status === "patch_ready" && (
                  <>
                    <Button disabled={busy === p.id}
                            onClick={() => act(p.id, "resolve")}>Approve</Button>
                    <Button variant="danger" disabled={busy === p.id}
                            onClick={() => act(p.id, "resolve?approve=false")}>Reject</Button>
                  </>
                )}
                {p.status === "approved" && (
                  <Button disabled={busy === p.id} onClick={() => act(p.id, "merge")}>
                    Merge to main
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function PromptEditor({ role, onClose }: { role: string; onClose: () => void }) {
  const prompt = useApi<{ prompt: string; uses_default: boolean }>(`/crew/${role}/prompt`);
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const value = text ?? prompt.data?.prompt ?? "";

  async function save(reset = false) {
    setBusy(true);
    await api(`/crew/${role}/prompt`, { method: "PUT", body: { prompt: reset ? null : value } });
    setBusy(false);
    onClose();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ground/80 p-4 backdrop-blur-sm"
         role="dialog" aria-modal="true" aria-label={`Edit ${role} prompt`}>
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-md border border-line bg-panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <SectionLabel>prompt · {role} {prompt.data?.uses_default ? "(default)" : "(custom)"}</SectionLabel>
          <button onClick={onClose} className="min-h-[36px] px-2 text-ink-mute">✕</button>
        </div>
        {prompt.loading ? <Loading /> : (
          <>
            <textarea value={value} onChange={(e) => setText(e.target.value)}
                      spellCheck={false}
                      className="min-h-[300px] flex-1 resize-y rounded-md border border-line-strong bg-ground p-3 font-mono text-xs leading-relaxed text-ink focus:border-brand/60 focus:outline-none" />
            <div className="mt-3 flex flex-wrap justify-end gap-2">
              <Button variant="ghost" disabled={busy} onClick={() => save(true)}>Reset to default</Button>
              <Button disabled={busy} onClick={() => save(false)}>Save</Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ModelPolicyEditor({ member, onClose }: { member: CrewMember; onClose: () => void }) {
  const providers = useApi<{ provider: string; has_secret: boolean }[]>("/providers");
  const [policy, setPolicy] = useState(member.model_policy);
  const [provider, setProvider] = useState(member.model_policy === "custom" ? member.provider || "" : "");
  const [models, setModels] = useState<{ model_id: string; display_name: string }[]>([]);
  const [model, setModel] = useState(member.model_policy === "custom" ? member.model || "" : "");
  const [error, setError] = useState<string | null>(null);

  async function loadModels(p: string) {
    setProvider(p);
    setModel("");
    if (p) setModels(await api(`/providers/${p}/models`));
  }

  async function save() {
    setError(null);
    try {
      await api(`/crew/${member.role}/model-policy`, {
        method: "PUT",
        body: policy === "inherit"
          ? { model_policy: "inherit" }
          : { model_policy: "custom", provider, model },
      });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ground/80 p-4 backdrop-blur-sm"
         role="dialog" aria-modal="true">
      <div className="w-full max-w-md rounded-md border border-line bg-panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <SectionLabel>model policy · {member.role}</SectionLabel>
          <button onClick={onClose} className="min-h-[36px] px-2 text-ink-mute">✕</button>
        </div>
        <div className="space-y-2">
          <label className="flex min-h-[40px] items-center gap-2 text-sm">
            <input type="radio" checked={policy === "inherit"} onChange={() => setPolicy("inherit")} />
            Inherit Orchestrator
          </label>
          <label className="flex min-h-[40px] items-center gap-2 text-sm">
            <input type="radio" checked={policy === "custom"} onChange={() => setPolicy("custom")} />
            Custom model
          </label>
          {policy === "custom" && (
            <div className="space-y-2 pl-6">
              <select value={provider} onChange={(e) => loadModels(e.target.value)}
                      className="min-h-[42px] w-full rounded-md border border-line-strong bg-ground px-3 py-2 text-sm">
                <option value="">Provider…</option>
                {(providers.data || []).filter((p) => p.has_secret || p.provider === "mock")
                  .map((p) => <option key={p.provider} value={p.provider}>{p.provider}</option>)}
              </select>
              <select value={model} onChange={(e) => setModel(e.target.value)}
                      className="min-h-[42px] w-full rounded-md border border-line-strong bg-ground px-3 py-2 text-sm">
                <option value="">Model…</option>
                {models.map((m) => <option key={m.model_id} value={m.model_id}>{m.display_name}</option>)}
              </select>
            </div>
          )}
          {error && <ErrorBox message={error} />}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button onClick={save} disabled={policy === "custom" && (!provider || !model)}>Save</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function CrewPage() {
  const crew = useApi<CrewState>("/crew");
  const runtimes = useApi<Runtime[]>("/agents");
  const catalog = useApi<{ runtimes: RuntimeProfile[] }>("/agents/runtimes");
  const overview = useApi<Overview>("/metrics/overview");
  const usage = useApi<UsageBreakdown>("/metrics/usage", { window: "week" });
  const [editPrompt, setEditPrompt] = useState<string | null>(null);
  const [editModel, setEditModel] = useState<CrewMember | null>(null);
  const [creating, setCreating] = useState(false);

  if (crew.loading || runtimes.loading) return <Loading />;
  if (crew.error) return <ErrorBox message={crew.error} />

  const fmtCost = (m: CrewMember["usage_month"]) => {
    if (!m) return "no usage yet";
    const total = (m.reported_cost || 0) + (m.estimated_cost || 0);
    const label = m.reported_cost > 0 ? "reported" : "estimated";
    return `$${total.toFixed(2)} ${label} · ${m.requests} req`;
  };

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <SectionHeader title="Crew" sub="your AI organization · one orchestrator coordinates it" />

      <OrgGraph crew={crew.data?.crew || []} overview={overview.data ?? null}
                usage={usage.data ?? null} />

      <DevProposals />

      <ActiveInstructions kind="dev_review" title="Dev Agent schedule"
                          hint="No weekly review configured. The Dev Agent only runs when you schedule it — pick your own day and time (ask the ◈ Manager or create it via Market-style form)." />

      <ActiveInstructions kind="agent_policy" title="Agent policies"
                          hint="No agent policy instructions yet." />

      <div className="grid gap-3 sm:grid-cols-2">
        {(crew.data?.crew || []).map((m) => (
          <div key={m.role} id={`role-${m.role}`}
               className={`min-w-0 scroll-mt-20 rounded-md border p-4 ${
                 m.role === "orchestrator" ? "border-brand/40 bg-panel sm:col-span-2" : "border-line bg-panel/60"
               }`}>
            <div className="flex items-start gap-3">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-sm border border-line-strong bg-raised font-mono text-lg text-brand" aria-hidden>
                {ROLE_GLYPHS[m.role] || "○"}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-display text-base font-semibold">{m.name}</span>
                  <Pill color={m.last_run?.status === "failed" ? "red" : m.enabled ? "green" : "gray"}
                        pulse={m.role === "orchestrator" && !!m.provider}>
                    {m.role === "orchestrator" ? (m.provider ? "active" : "not configured")
                      : m.last_run ? m.last_run.status : "ready"}
                  </Pill>
                </div>
                <div className="text-xs text-ink-mute">{m.mission}</div>
                <div className="mt-2 grid gap-x-4 gap-y-0.5 font-mono text-[10px] uppercase tracking-wide text-ink-faint sm:grid-cols-2">
                  <span>runtime: {m.runtime}</span>
                  <span className="truncate">
                    model: {m.role === "orchestrator" ? "" : m.model_policy === "inherit" ? "inherit · " : "custom · "}
                    {m.provider ? `${m.provider} / ${m.model || "?"}` : "unset"}
                  </span>
                  <span>prompt: {m.uses_default_prompt ? "default" : `custom v${m.prompt_version}`}</span>
                  <span>usage 30d: {fmtCost(m.usage_month)}</span>
                  {m.last_run?.task && (
                    <span className="truncate sm:col-span-2">last op: {m.last_run.task}</span>
                  )}
                </div>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2 border-t border-line pt-3">
              {m.role !== "orchestrator" && (
                <Button variant="ghost" onClick={() => setEditModel(m)}>Configure model</Button>
              )}
              <Button variant="ghost" onClick={() => setEditPrompt(m.role)}>Edit prompt</Button>
            </div>
          </div>
        ))}
      </div>

      <Card title="Agent runtimes — how external operators execute">
        <p className="mb-3 text-xs text-ink-faint">
          Roles define what a crew member does; runtimes define how it executes. Native runs
          in-platform. Bring external runtimes over HTTP or an OpenClaw gateway — your goals,
          memory and permissions always stay with Moseisley.sh.
        </p>
        <div className="space-y-2">
          {(runtimes.data || []).map((a) => (
            <div key={a.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <div className="flex min-w-0 items-center gap-2">
                {a.configuration?.avatar ? (
                  <img src={`/brand/${a.configuration.avatar}`} alt="" width={28} height={28}
                       loading="lazy"
                       className="h-7 w-7 shrink-0 rounded-full border border-brand/40 object-cover" />
                ) : (
                  <Insignia kind={a.adapter_type} size="sm" />
                )}
                <span className="truncate font-medium">{a.display_name}</span>
                <span className="font-mono text-[10px] text-ink-faint">({a.adapter_type})</span>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {a.is_active && <Pill color="green">comms active</Pill>}
                <Pill color={a.health_status === "error" ? "red" : "gray"}>{a.health_status}</Pill>
                {!a.is_active && (
                  <Button variant="ghost" onClick={async () => {
                    await api(`/agents/${a.id}/activate`, { body: {} });
                    runtimes.reload();
                  }}>Route comms here</Button>
                )}
              </div>
            </div>
          ))}
          <a href="/welcome?rerun=1"
             className="flex w-full min-h-[52px] items-center justify-center gap-2 rounded-md border border-dashed border-brand/40 text-sm text-brand transition hover:bg-brand/10">
            <span aria-hidden>✦</span>
            <span className="font-mono text-[11px] font-bold uppercase tracking-widest">redesign my crew →</span>
          </a>
          <button onClick={() => setCreating(true)}
                  className="flex w-full min-h-[52px] items-center justify-center gap-2 rounded-md border border-dashed border-line-strong text-sm text-ink-mute transition hover:border-brand/60 hover:bg-panel hover:text-ink">
            <span aria-hidden className="font-mono text-brand">+</span>
            <span className="font-mono text-[11px] font-bold uppercase tracking-widest">create agent</span>
          </button>
        </div>
      </Card>

      {/* read-only reference: compare the runtimes before creating anything */}
      <Card title="Runtime catalog — what each one is honestly good and bad at">
        <p className="mb-3 text-xs text-ink-faint">
          The same profiles the create-agent wizard shows. Written against what the
          adapters actually do in this build — a runtime listed as blocked is refused
          by the API too, not merely hidden here.
        </p>
        {catalog.loading ? <Loading />
          : catalog.error ? <ErrorBox message={catalog.error} />
          : <RuntimeReference runtimes={catalog.data?.runtimes || []} />}
      </Card>

      {editPrompt && <PromptEditor role={editPrompt} onClose={() => { setEditPrompt(null); crew.reload(); }} />}
      {editModel && <ModelPolicyEditor member={editModel} onClose={() => { setEditModel(null); crew.reload(); }} />}
    </div>
  );
}
