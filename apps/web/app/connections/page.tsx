"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { FactoryState } from "@/components/factory-toggle";
import { Button, Card, ErrorBox, Input, Loading, Pill } from "@/components/ui";

type Provider = { id: string; provider: string; enabled: boolean; display_hint: string | null; has_secret: boolean };
type Connection = { id: string; integration_type: string; name: string; status: string; capabilities: Record<string, string>; last_health_ok: boolean | null };
type Binding = { linked: boolean; voice_reply_mode?: string };
type Model = { model_id: string; display_name: string; source: string };
type Orchestrator = { provider: string | null; model: string | null; configured: boolean; provider_connected: boolean | null };
type ProviderDef = { id: string; label: string; needs_key: boolean; state: "connected" | "disabled" | "not_connected"; display_hint: string | null };

function ModelSelector({ provider, value, onChange }: {
  provider: string; value: string; onChange: (m: string) => void;
}) {
  const [models, setModels] = useState<Model[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!provider) return;
    setLoading(true);
    api<Model[]>(`/providers/${provider}/models`, { params: search ? { search } : undefined })
      .then(setModels)
      .catch(() => setModels([]))
      .finally(() => setLoading(false));
  }, [provider, search]);

  return (
    <div className="space-y-2">
      {(provider === "openrouter" || models.length > 20) && (
        <Input placeholder="Search models…" value={search} onChange={(e) => setSearch(e.target.value)} />
      )}
      {loading ? (
        <div className="font-mono text-[11px] text-ink-faint">loading catalog…</div>
      ) : (
        <select value={value} onChange={(e) => onChange(e.target.value)}
                className="min-h-[42px] w-full rounded-md border border-line-strong bg-panel px-3 py-2 text-sm">
          <option value="">Select a model…</option>
          {models.map((m) => (
            <option key={m.model_id} value={m.model_id}>
              {m.display_name} {m.source === "fallback" ? "(fallback catalog)" : ""}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

function OrchestratorPanel({ defs }: { defs: ProviderDef[] }) {
  const orch = useApi<Orchestrator>("/orchestrator");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (orch.data?.provider) {
      setProvider(orch.data.provider);
      setModel(orch.data.model || "");
    }
  }, [orch.data]);

  const selectedDef = defs.find((d) => d.id === provider);
  const selectedConnected = selectedDef?.state === "connected";

  async function save() {
    setBusy(true);
    setStatus(null);
    try {
      await api("/orchestrator", { method: "PUT", body: { provider, model } });
      setStatus("saved");
      orch.reload();
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    setBusy(true);
    setStatus("testing…");
    const result = await api<{ ok: boolean; detail?: string; model?: string }>(
      "/orchestrator/test", { body: {} });
    setStatus(result.ok ? `test ok — ${result.model}` : `test failed: ${result.detail}`);
    setBusy(false);
  }

  return (
    <Card title="Orchestrator — your primary intelligence">
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-widest text-ink-faint">Provider</div>
          <select value={provider}
                  onChange={(e) => { setProvider(e.target.value); setModel(""); }}
                  className="min-h-[42px] w-full rounded-md border border-line-strong bg-panel px-3 py-2 text-sm">
            <option value="">Select provider…</option>
            {defs.map((d) => (
              <option key={d.id} value={d.id}>
                {d.label}{d.state === "connected" ? "" : d.state === "disabled" ? " — disabled" : " — not connected"}
              </option>
            ))}
          </select>
        </div>
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-widest text-ink-faint">Model</div>
          {provider && selectedConnected ? (
            <ModelSelector provider={provider} value={model} onChange={setModel} />
          ) : provider ? (
            <div className="text-xs text-warn">
              {selectedDef?.state === "disabled"
                ? `${selectedDef.label} is disabled — enable it below to use it here.`
                : `${selectedDef?.label ?? provider} is not connected — add its API key below first.`}
            </div>
          ) : (
            <div className="text-xs text-ink-faint">Select a provider to choose a model.</div>
          )}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Pill color={orch.data?.configured ? "green" : "gray"} pulse={!!orch.data?.configured}>
          {orch.data?.configured ? "active" : "not configured"}
        </Pill>
        {orch.data?.configured && (
          <span className="font-mono text-[11px] text-ink-mute">
            {orch.data.provider} · {orch.data.model}
          </span>
        )}
        <Button variant="ghost" disabled={busy || !orch.data?.configured} onClick={test}>Test</Button>
        <Button disabled={busy || !provider || !model || !selectedConnected} onClick={save}>Save</Button>
        {status && <span className="break-words font-mono text-[11px] text-ink-mute">{status}</span>}
      </div>
      <p className="mt-2 text-xs text-ink-faint">
        The Orchestrator answers your Web and Telegram messages. Crew roles inherit this
        model unless you give them a custom one on the Crew page.
      </p>
    </Card>
  );
}

export default function ConnectionsPage() {
  const providers = useApi<Provider[]>("/providers");
  const defs = useApi<ProviderDef[]>("/providers/definitions");
  const connections = useApi<Connection[]>("/integrations");
  const telegram = useApi<Binding>("/telegram/binding");
  const settings = useApi<FactoryState>("/settings");
  const [keyInput, setKeyInput] = useState<Record<string, string>>({});
  const [modelCounts, setModelCounts] = useState<Record<string, number>>({});
  const [pairCode, setPairCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (providers.data || []).forEach((p) => {
      api<Model[]>(`/providers/${p.provider}/models`)
        .then((models) => setModelCounts((c) => ({ ...c, [p.provider]: models.length })))
        .catch(() => {});
    });
  }, [providers.data]);

  if (providers.loading || defs.loading || connections.loading || telegram.loading) return <Loading />;

  async function saveProvider(provider: string) {
    setError(null);
    try {
      const body: Record<string, unknown> = { provider, api_key: keyInput[provider] || null };
      if (provider === "mock") body.api_key = "mock";
      await api("/providers", { body });
      setKeyInput({ ...keyInput, [provider]: "" });
      providers.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    }
  }

  async function refreshModels(provider: string) {
    const result = await api<{ count: number; source: string }>(
      `/providers/${provider}/models/refresh`, { body: {} });
    setModelCounts((c) => ({ ...c, [provider]: result.count }));
  }

  async function addDemo() {
    await api("/integrations", {
      body: {
        integration_type: "demo", name: "Demo data (synthetic)",
        capabilities: { "gmail.read": "READ", "calendar.read": "READ" },
      },
    });
    connections.reload();
  }

  const configured = new Map((providers.data || []).map((p) => [p.provider, p]));
  // hosted trial: own keys are a subscriber feature (server-enforced too)
  const byokLocked = settings.data?.byok_allowed === false;

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div>
        <h1 className="text-xl font-bold">Connections</h1>
        <p className="mt-0.5 font-mono text-[11px] uppercase tracking-widest text-ink-faint">
          uplinks · providers · integrations
        </p>
      </div>
      {error && <ErrorBox message={error} />}

      <OrchestratorPanel defs={defs.data || []} />

      <Card title="AI providers — bring your own keys">
        {byokLocked && (
          <p className="mb-3 text-xs text-ink-faint">
            OpenRouter powers DEV mode · free models only. Other providers unlock with a pass.
          </p>
        )}
        <div className="space-y-3">
          {(defs.data || []).map((def) => {
            const p = def.id;
            const label = def.label;
            const row = configured.get(p);
            // OpenRouter powers DEV mode → always connectable; others gated
            const lockedHere = byokLocked && p !== "openrouter";
            return (
              <div key={p} className="flex flex-wrap items-center gap-2 border-b border-line/60 pb-3 last:border-0 last:pb-0">
                <div className="w-44 shrink-0 text-sm">{label}</div>
                {row ? (
                  <>
                    <Pill color={row.enabled ? "green" : "gray"}>
                      {row.enabled ? "connected" : "disabled"}
                    </Pill>
                    <span className="font-mono text-xs text-ink-faint">
                      {row.display_hint || (row.has_secret ? "•••" : "no key")}
                    </span>
                    {modelCounts[p] != null && (
                      <span className="font-mono text-[10px] uppercase tracking-wide text-ink-faint">
                        {modelCounts[p]} models
                      </span>
                    )}
                    <div className="flex flex-wrap gap-1.5">
                      <Button variant="ghost" onClick={async () => {
                        const r = await api<{ ok: boolean }>(`/providers/${p}/test`, { body: {} });
                        alert(r.ok ? "Connection OK" : "Connection failed");
                      }}>Test</Button>
                      <Button variant="ghost" onClick={() => refreshModels(p)}>Refresh models</Button>
                      <Button variant="ghost" onClick={async () => {
                        await api(`/providers/${p}/toggle`, { body: { enabled: !row.enabled } });
                        providers.reload();
                      }}>{row.enabled ? "Disable" : "Enable"}</Button>
                      <Button variant="danger" onClick={async () => {
                        await api(`/providers/${p}`, { method: "DELETE" });
                        providers.reload();
                      }}>Disconnect</Button>
                    </div>
                  </>
                ) : (
                  <>
                    <Pill color="gray">not connected</Pill>
                    {def.needs_key && (
                      <Input placeholder="API key"
                             className={`w-full sm:max-w-xs ${lockedHere ? "opacity-40" : ""}`}
                             disabled={lockedHere}
                             value={keyInput[p] || ""}
                             onChange={(e) => setKeyInput({ ...keyInput, [p]: e.target.value })} />
                    )}
                    <Button variant="ghost" disabled={lockedHere}
                            onClick={() => saveProvider(p)}>Connect</Button>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </Card>

      <Card title="Communication — Telegram">
        {telegram.data?.linked ? (
          <div className="flex flex-wrap items-center gap-3">
            <Pill color="green">LINKED</Pill>
            <span className="text-xs text-ink-faint">voice replies: {telegram.data.voice_reply_mode}</span>
            <Button variant="danger" onClick={async () => {
              await api("/telegram/binding", { method: "DELETE" });
              telegram.reload();
            }}>Unlink</Button>
          </div>
        ) : (
          <div className="space-y-2">
            <Button variant="ghost" onClick={async () => {
              const r = await api<{ code: string }>("/telegram/pairing-code", { body: {} });
              setPairCode(r.code);
            }}>Generate pairing code</Button>
            {pairCode && (
              <p className="text-sm">
                Send <code className="rounded-sm bg-raised px-2 py-0.5 font-mono">/link {pairCode}</code> to
                your Moseisley.sh bot within 10 minutes.
              </p>
            )}
          </div>
        )}
      </Card>

      <Card title="Integrations" action={<Button variant="ghost" onClick={addDemo}>Add demo data</Button>}>
        {!connections.data?.length ? (
          <p className="text-sm text-ink-faint">
            Nothing connected. Connect Google (OAuth setup — see README), MCP servers,
            webhooks, n8n or your own S3-compatible storage — or add synthetic demo data
            to try X-Ray immediately.
          </p>
        ) : (
          <div className="space-y-2">
            {connections.data.map((c) => (
              <div key={c.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <div className="min-w-0">
                  <span className="font-medium">{c.name}</span>
                  <span className="ml-2 text-xs text-ink-faint">({c.integration_type})</span>
                  <div className="mt-0.5 flex flex-wrap gap-1">
                    {Object.entries(c.capabilities || {}).map(([cap, lvl]) => (
                      <span key={cap} className="rounded-sm bg-raised px-1.5 py-0.5 font-mono text-[10px] text-ink-mute">
                        {cap}: {lvl}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Pill color={c.status === "connected" ? "green" : "red"}>{c.status}</Pill>
                  <Button variant="danger" onClick={async () => {
                    await api(`/integrations/${c.id}?purge=true`, { method: "DELETE" });
                    connections.reload();
                  }}>Disconnect</Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
