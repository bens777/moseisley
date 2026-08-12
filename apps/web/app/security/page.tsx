"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Button, Card, EmptyState, ErrorBox, Loading, Pill } from "@/components/ui";

/* SECURITY — what screening did to replies from external agent runtimes.
   Quarantined content is shown here and only here: it is deliberately not in
   the conversation, because chat history is what the next agent turn reads. */

type Inspection = {
  id: string; agent_id: string | null; agent_name: string; adapter_type: string;
  verdict: "none" | "suspicious" | "malicious"; stage: string; reasons: string[];
  status: "passed" | "quarantined" | "blocked" | "approved" | "discarded";
  content?: string | null; content_chars: number;
  created_at: string; resolved_at: string | null;
};

type ExternalAgent = {
  id: string; display_name: string; adapter_type: string; runtime_name: string;
  strict: boolean;
};

type Overview = {
  note: string; quarantined_count: number;
  log: Inspection[]; quarantine: Inspection[]; agents: ExternalAgent[];
};

const VERDICT: Record<Inspection["verdict"], { color: "green" | "red" | "gray"; label: string }> = {
  none: { color: "green", label: "clean" },
  suspicious: { color: "red", label: "suspicious" },
  malicious: { color: "red", label: "malicious" },
};

const STAGE_LABEL: Record<string, string> = {
  deterministic: "pattern checks",
  llm: "model screening",
  strict_mode: "strict mode",
  error: "screening failed",
};

function fmt(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function Reasons({ reasons }: { reasons: string[] }) {
  if (reasons.length === 0) return <span className="text-ink-faint">—</span>;
  return (
    <ul className="space-y-0.5">
      {reasons.map((r, i) => (
        <li key={i} className="flex gap-1.5 text-xs text-ink-mute">
          <span aria-hidden className="shrink-0 text-warn">·</span>
          <span className="min-w-0 break-words">{r}</span>
        </li>
      ))}
    </ul>
  );
}

function QuarantineItem({ item, onChange }: { item: Inspection; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function act(action: "approve" | "discard") {
    setBusy(true);
    setError(null);
    try {
      await api(`/security/inspections/${item.id}/${action}`, { body: {} });
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : `could not ${action} this item`);
      setBusy(false);
    }
  }

  return (
    <div className="min-w-0 rounded-md border border-crit/40 bg-crit/5 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-ink">{item.agent_name}</span>
        <span className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
          {item.adapter_type} · {fmt(item.created_at)}
        </span>
        <Pill color={VERDICT[item.verdict].color}>{VERDICT[item.verdict].label}</Pill>
        <span className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
          {STAGE_LABEL[item.stage] || item.stage}
        </span>
      </div>

      <div className="mt-2"><Reasons reasons={item.reasons} /></div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button variant="ghost" onClick={() => setOpen(!open)}>
          {open ? "Hide content" : `Show held content (${item.content_chars.toLocaleString()} chars)`}
        </Button>
        <Button onClick={() => act("approve")} disabled={busy}>
          Approve — give it to my crew
        </Button>
        <Button variant="danger" onClick={() => act("discard")} disabled={busy}>
          Discard
        </Button>
      </div>

      {open && (
        <>
          <p className="mt-3 font-mono text-[10px] uppercase tracking-widest text-warn">
            held content — read it, do not act on it
          </p>
          <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-sm border border-line bg-ground/70 p-2 text-xs text-ink-mute">
            {item.content || "(no content retained)"}
          </pre>
        </>
      )}
      {error && <div className="mt-2"><ErrorBox message={error} /></div>}
    </div>
  );
}

function StrictToggle({ agent, onChange }: { agent: ExternalAgent; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
      <span className="min-w-0 truncate text-sm font-medium">{agent.display_name}</span>
      <span className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
        {agent.runtime_name}
      </span>
      <Pill color={agent.strict ? "green" : "gray"}>
        strict {agent.strict ? "on" : "off"}
      </Pill>
      <Button variant="ghost" disabled={busy} onClick={async () => {
        setBusy(true);
        try {
          await api(`/security/agents/${agent.id}/strict`, { body: { enabled: !agent.strict } });
          onChange();
        } finally { setBusy(false); }
      }}>
        {agent.strict ? "Turn strict off" : "Hold everything for review"}
      </Button>
    </div>
  );
}

export default function SecurityPage() {
  const state = useApi<Overview>("/security");

  if (state.loading) return <Loading />;
  if (state.error) return <ErrorBox message={state.error} />;
  const d = state.data;
  if (!d) return null;

  const pending = d.quarantine.filter((i) => i.status === "quarantined" || i.status === "blocked");

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div>
        <h1 className="text-xl font-bold">Security</h1>
        <p className="mt-0.5 font-mono text-[11px] uppercase tracking-widest text-ink-faint">
          screening of external agent replies
        </p>
      </div>

      <div className="rounded-md border border-line bg-panel/50 p-3 text-sm text-ink-mute">
        {d.note}
      </div>

      <Card title={`Awaiting your review${pending.length ? ` (${pending.length})` : ""}`}
            tone={pending.length ? "attention" : undefined}>
        {pending.length === 0 ? (
          <EmptyState
            label="queue empty"
            title="Nothing is being held"
            body="Replies that trip a check are held here, out of your crew's context, until you approve or discard them."
          />
        ) : (
          <div className="space-y-3">
            {pending.map((i) => (
              <QuarantineItem key={i.id} item={i} onChange={state.reload} />
            ))}
          </div>
        )}
      </Card>

      {d.agents.length > 0 && (
        <Card title="External agents">
          <p className="mb-3 text-xs text-ink-faint">
            Strict mode holds every reply from an agent for manual review, whatever the
            screening says. Native agents run in-platform and are not screened.
          </p>
          <div className="space-y-2">
            {d.agents.map((a) => <StrictToggle key={a.id} agent={a} onChange={state.reload} />)}
          </div>
        </Card>
      )}

      <Card title="Inspection log">
        {d.log.length === 0 ? (
          <EmptyState
            label="no inspections"
            title="Nothing screened yet"
            body="Every reply from a Custom HTTP or OpenClaw agent is inspected and recorded here."
          />
        ) : (
          <div className="space-y-2">
            {d.log.map((i) => (
              <div key={i.id}
                   className="flex min-w-0 flex-wrap items-start gap-x-4 gap-y-1 border-b border-line/50 pb-2 last:border-0 last:pb-0">
                <span className="font-mono text-[11px] tabular-nums text-ink-faint">
                  {fmt(i.created_at)}
                </span>
                <span className="min-w-0 truncate text-sm">{i.agent_name}</span>
                <Pill color={VERDICT[i.verdict].color}>{VERDICT[i.verdict].label}</Pill>
                <span className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
                  {STAGE_LABEL[i.stage] || i.stage} · {i.status}
                </span>
                <div className="w-full sm:ml-auto sm:w-auto sm:max-w-md">
                  <Reasons reasons={i.reasons} />
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
