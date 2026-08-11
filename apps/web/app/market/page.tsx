"use client";
import { useState } from "react";
import { api, euros } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { ActiveInstructions } from "@/components/instructions";
import { Button, Card, EmptyState, ErrorBox, Input, Loading, Pill, SectionHeader } from "@/components/ui";

type Opportunity = {
  id: string; title: string; description: string; buyer: string | null;
  evidence: { title: string; evidence_level: string; strength: number }[];
  status: string; confidence: number; estimated_test_cost_cents: number | null;
};

type Report = {
  id: string; instruction_id: string | null; status: string; sentiment: string | null;
  summary: {
    material_changes?: { title: string; why_it_matters: string; evidence?: string }[];
    sentiment_basis?: string; narratives?: string[]; important_posts?: string[];
    emerging_topics?: string[]; pain_points?: string[]; competitor_movement?: string[];
    opportunities?: string[]; threats?: string[]; parse_error?: boolean;
  };
  sources: string[]; query: { from_date?: string; to_date?: string; topics?: string[];
                              accounts?: string[]; mock?: boolean };
  delivered: string[]; created_at: string;
};
type ProviderDef = { id: string; state: string };

const SENTIMENT_COLORS: Record<string, string> = {
  positive: "green", mixed: "yellow", negative: "red", no_material_change: "gray",
};

function WatchForm({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [topics, setTopics] = useState("");
  const [accounts, setAccounts] = useState("");
  const [time, setTime] = useState("08:00");
  const [telegram, setTelegram] = useState(true);
  const [error, setError] = useState<string | null>(null);
  if (!open) return <Button onClick={() => setOpen(true)}>New market watch</Button>;
  return (
    <Card title="New market watch">
      <div className="space-y-2">
        <Input placeholder="Name (e.g. AI agent market watch)" value={name}
               onChange={(e) => setName(e.target.value)} />
        <Input placeholder="Topics, comma-separated (e.g. OpenClaw, Claude Code, AI agents)"
               value={topics} onChange={(e) => setTopics(e.target.value)} />
        <Input placeholder="X accounts, comma-separated (e.g. @example1, @example2) — optional"
               value={accounts} onChange={(e) => setAccounts(e.target.value)} />
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-ink-mute">
            daily at
            <Input type="time" value={time} onChange={(e) => setTime(e.target.value)}
                   className="w-28" />
          </label>
          <label className="flex min-h-[42px] items-center gap-2 text-sm text-ink-mute">
            <input type="checkbox" checked={telegram}
                   onChange={(e) => setTelegram(e.target.checked)} />
            deliver to Telegram
          </label>
        </div>
        {error && <ErrorBox message={error} />}
        <div className="flex gap-2">
          <Button disabled={!name.trim() || !topics.trim()} onClick={async () => {
            setError(null);
            try {
              await api("/instructions", { body: {
                name, kind: "market_watch", assigned_role: "radar",
                config: {
                  topics: topics.split(",").map((t) => t.trim()).filter(Boolean),
                  accounts: accounts.split(",").map((a) => a.trim()).filter(Boolean),
                  lookback_days: 1,
                  instruction: "Report only material changes and explain why they matter.",
                },
                schedule: { frequency: "daily", time,
                            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone },
                delivery: telegram ? ["telegram"] : [],
              }});
              setOpen(false); setName(""); setTopics(""); setAccounts("");
              onDone();
            } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
          }}>Create watch</Button>
          <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
        </div>
        <p className="text-[11px] text-ink-faint">
          Tip: you can also just tell the ◈ Manager — &ldquo;watch OpenClaw and Claude
          Code every morning and send meaningful changes to Telegram.&rdquo;
        </p>
      </div>
    </Card>
  );
}

function ReportList({ items }: { items: string[] | undefined; }) {
  if (!items?.length) return null;
  return (
    <ul className="mt-1 space-y-0.5 text-sm text-ink-mute">
      {items.slice(0, 4).map((t, i) => (
        <li key={i} className="flex gap-2"><span className="text-brand" aria-hidden>·</span>
          <span className="min-w-0 break-words">{t}</span></li>
      ))}
    </ul>
  );
}

function ReportCard({ r }: { r: Report }) {
  const [expanded, setExpanded] = useState(false);
  const changes = r.summary.material_changes || [];
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Pill color={SENTIMENT_COLORS[r.sentiment || "no_material_change"] || "gray"}>
          {(r.sentiment || "no material change").replace(/_/g, " ")}
        </Pill>
        {r.query.mock && <Pill color="yellow">offline mock</Pill>}
        {r.delivered.includes("telegram") && <Pill color="blue">→ telegram</Pill>}
        <span className="ml-auto font-mono text-[10px] uppercase tracking-widest text-ink-faint">
          {new Date(r.created_at).toLocaleString()} ·
          window {r.query.from_date} → {r.query.to_date}
        </span>
      </div>
      {r.summary.sentiment_basis && (
        <p className="mt-1 text-xs italic text-ink-faint">
          basis: {r.summary.sentiment_basis}
        </p>
      )}
      {changes.length === 0 ? (
        <p className="mt-2 text-sm text-ink-mute">
          No material change — and that&rsquo;s exactly what it reports.
        </p>
      ) : (
        <ol className="mt-2 space-y-1.5">
          {changes.map((c, i) => (
            <li key={i} className="min-w-0 text-sm">
              <span className="font-medium text-ink">{i + 1}. {c.title}</span>
              <span className="ml-2 text-ink-mute">— {c.why_it_matters}</span>
              {c.evidence && (
                <div className="ml-5 font-mono text-[11px] text-ink-faint">evidence: {c.evidence}</div>
              )}
            </li>
          ))}
        </ol>
      )}
      {expanded && (
        <div className="mt-3 grid gap-3 border-t border-line pt-3 sm:grid-cols-2">
          {([["Narratives", r.summary.narratives], ["Important posts", r.summary.important_posts],
             ["Emerging topics", r.summary.emerging_topics], ["Pain points", r.summary.pain_points],
             ["Competitor movement", r.summary.competitor_movement],
             ["Opportunities", r.summary.opportunities], ["Threats", r.summary.threats]] as const)
            .filter(([, items]) => items?.length)
            .map(([label, items]) => (
              <div key={label}>
                <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">{label}</div>
                <ReportList items={items as string[]} />
              </div>
            ))}
          {r.sources.length > 0 && (
            <div className="sm:col-span-2">
              <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">sources</div>
              <ul className="mt-1 space-y-0.5">
                {r.sources.slice(0, 8).map((s) => (
                  <li key={s} className="min-w-0 truncate">
                    <a href={s} target="_blank" rel="noreferrer"
                       className="font-mono text-[11px] text-signal hover:underline">{s}</a>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      <button onClick={() => setExpanded(!expanded)}
              className="mt-2 font-mono text-[10px] uppercase tracking-widest text-ink-faint hover:text-ink">
        {expanded ? "less" : "full report + sources"}
      </button>
    </Card>
  );
}

function OpportunityRadar() {
  const opps = useApi<Opportunity[]>("/opportunities");
  const [scanning, setScanning] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function scan() {
    setScanning(true);
    setError(null);
    try {
      const result = await api<{ outcome: string }>("/market/scan", { body: {} });
      setOutcome(result.outcome);
      opps.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "scan failed");
    } finally {
      setScanning(false);
    }
  }

  return (
    <Card title="Opportunity radar — evidence-graded"
          action={<Button variant="ghost" disabled={scanning} onClick={scan}>
            {scanning ? "Scanning…" : "Run sweep"}</Button>}>
      {scanning && (
        <div className="scanline mb-2 flex items-center gap-2 rounded-md border border-signal/30 bg-signal/5 px-4 py-2.5 font-mono text-[11px] uppercase tracking-widest text-signal">
          <span className="led led-pulse bg-signal" aria-hidden /> radar sweep in progress
        </div>
      )}
      {outcome && !scanning && (
        <div className="mb-2"><Pill color={outcome === "NO MATERIAL CHANGE" ? "gray" : "blue"}>{outcome}</Pill></div>
      )}
      {error && <ErrorBox message={error} />}
      {!opps.data?.length ? (
        <p className="text-sm text-ink-faint">
          No opportunities detected yet — sweeps grade signals on an evidence ladder
          and most days report no material change.
        </p>
      ) : (
        <div className="space-y-3">
          {opps.data.map((o) => (
            <div key={o.id} className="min-w-0 rounded-md border border-line p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="break-words text-sm font-medium">{o.title}</div>
                  <div className="mt-1 break-words text-xs text-ink-mute">{o.description}</div>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <Pill color={o.status === "rejected" ? "gray" : o.status === "validated" ? "green" : "blue"}>
                    {o.status}
                  </Pill>
                  {o.estimated_test_cost_cents != null && (
                    <span className="font-mono text-[10px] text-ink-faint">
                      test ≈ {euros(o.estimated_test_cost_cents)}
                    </span>
                  )}
                </div>
              </div>
              {o.evidence?.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {o.evidence.map((e, i) => (
                    <span key={i} className="rounded-sm bg-raised px-1.5 py-0.5 font-mono text-[10px] text-ink-mute">
                      {e.evidence_level} · {(e.strength * 100).toFixed(0)}%
                    </span>
                  ))}
                </div>
              )}
              {o.status === "detected" && (
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button variant="ghost" onClick={async () => {
                    await api("/experiments", { body: {
                      hypothesis: `Micro test: ${o.title}`,
                      success_criterion: ">= 3 qualified replies",
                      kill_criterion: "< 1 qualified reply",
                      cash_budget_cents: 5000, human_time_budget_minutes: 120,
                      opportunity_id: o.id, prediction_probability: 0.5,
                    }});
                    opps.reload();
                  }}>Launch micro-test</Button>
                  <Button variant="ghost" onClick={async () => {
                    await api(`/opportunities/${o.id}/ignore`, { body: {} });
                    opps.reload();
                  }}>Ignore</Button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function MarketPage() {
  const reports = useApi<Report[]>("/market/reports");
  const defs = useApi<ProviderDef[]>("/providers/definitions");
  const xai = defs.data?.find((d) => d.id === "xai");

  if (reports.loading) return <Loading />;

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <SectionHeader title="Market" sub="X intelligence · watches · evidence-based sentiment"
                     action={<WatchForm onDone={reports.reload} />} />

      {xai && xai.state !== "connected" && (
        <div className="rounded-md border border-warn/40 bg-warn/5 px-4 py-3 text-sm text-ink-mute">
          Live X search runs on your own xAI key ({xai.state.replace("_", " ")}).{" "}
          <a href="/connections" className="text-brand hover:underline">Connect xAI</a> to
          activate real scans — watches are stored either way.
        </div>
      )}

      <ActiveInstructions kind="market_watch" title="Active market watches"
                          hint="No watches configured. Create one above or ask the ◈ Manager." />

      <div>
        <div className="mb-2 font-mono text-[11px] font-semibold uppercase tracking-widest text-ink-mute">
          Reports
        </div>
        {!reports.data?.length ? (
          <EmptyState label="radar" title="No market reports yet"
                      body="Reports appear here after a watch runs — on schedule or via Run now. Sentiment is evidence-based: positive, mixed, negative or no material change. Never invented percentages." />
        ) : (
          <div className="space-y-3">
            {reports.data.map((r) => <ReportCard key={r.id} r={r} />)}
          </div>
        )}
      </div>

      <OpportunityRadar />
    </div>
  );
}
