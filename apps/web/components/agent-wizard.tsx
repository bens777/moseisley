"use client";
/* eslint-disable @next/next/no-img-element */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { RuntimeCard, type Runtime } from "@/components/runtimes";
import { Button, ErrorBox } from "@/components/ui";

/* Create-agent wizard: avatar → identity → runtime → brain.
   Everything goes through existing routes: POST /api/agents creates the
   AgentConfig (avatar + role live in its configuration_json — no migration),
   and the brain step reuses PUT /api/crew/{role}/model-policy. */

type Role = { role: string; name: string; mission: string };
type Provider = { provider: string; has_secret: boolean };
type Model = { model_id: string; display_name: string };

/* Character names for the shipped illustrations — the wizard pre-fills the
   agent name from whichever face the user picks. */
const AVATAR_NAMES: Record<string, string> = {
  "crew-orchestrator.webp": "Orchestrator",
  "crew-strategist.webp": "Strategist",
  "crew-radar.webp": "Radar",
  "crew-xray.webp": "X-Ray",
  "crew-challenger.webp": "Challenger",
  "crew-auditor.webp": "Auditor",
  "crew-followup.webp": "Follow-Up",
  "crew-dev.webp": "Dev",
  "crew-treasury.webp": "Guardian",
  "crew-bartender.webp": "Bartender",
};

const STEPS = ["Avatar", "Identity", "Runtime", "Brain"];

export function AgentWizard({ roles, onClose, onCreated }: {
  roles: Role[]; onClose: () => void; onCreated: () => void;
}) {
  const [step, setStep] = useState(0);
  const [avatars, setAvatars] = useState<string[]>([]);
  const [avatar, setAvatar] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [type, setType] = useState("native");
  const [endpoint, setEndpoint] = useState("");
  const [authHeader, setAuthHeader] = useState("Authorization");
  const [baseUrl, setBaseUrl] = useState("");
  const [credential, setCredential] = useState("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<Model[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [runtimeList, setRuntimeList] = useState<Runtime[]>([]);
  const [aiMode, setAiMode] = useState<string>("factory");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ avatars: string[] }>("/agents/avatars").then((r) => setAvatars(r.avatars)).catch(() => {});
    api<{ ai_mode: string }>("/settings").then((s) => setAiMode(s.ai_mode)).catch(() => {});
    api<Provider[]>("/providers").then(setProviders).catch(() => {});
    api<{ runtimes: Runtime[] }>("/agents/runtimes")
      .then((r) => setRuntimeList(r.runtimes)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!provider) { setModels([]); return; }
    api<Model[]>(`/providers/${provider}/models`).then(setModels).catch(() => setModels([]));
  }, [provider]);

  const expert = aiMode === "custom";
  const connected = providers.filter((p) => p.has_secret || p.provider === "mock");

  function pickAvatar(a: string) {
    setAvatar(a);
    if (!name) setName(AVATAR_NAMES[a] ?? "");
    setStep(1);
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const configuration: Record<string, unknown> = {};
      if (avatar) configuration.avatar = avatar;
      if (role) configuration.role = role;
      if (type === "custom_http") {
        configuration.endpoint = endpoint.trim();
        configuration.auth_header = authHeader.trim() || "Authorization";
      }
      if (type === "openclaw" && baseUrl.trim()) configuration.base_url = baseUrl.trim();

      await api("/agents", {
        body: {
          adapter_type: type,
          display_name: name.trim(),
          configuration,
          credential: credential.trim() || null,
        },
      });

      // EXPERT only: pin the role's model, through the existing crew route
      if (expert && role && provider && model) {
        await api(`/crew/${role}/model-policy`, {
          method: "PUT",
          body: { model_policy: "custom", provider, model },
        });
      }
      onCreated();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not create the agent");
    } finally {
      setBusy(false);
    }
  }

  const canNext =
    (step === 0 && !!avatar) ||
    (step === 1 && !!name.trim()) ||
    (step === 2 && (type === "native"
      || (type === "custom_http" && !!endpoint.trim())
      || type === "openclaw"));

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center bg-ground/80 p-0 backdrop-blur-sm sm:items-center sm:p-4"
         role="dialog" aria-modal="true" aria-label="Create an agent">
      <div className="flex max-h-[92dvh] w-full max-w-2xl flex-col overflow-hidden rounded-t-lg border border-line-strong bg-panel sm:rounded-lg">
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <div className="flex items-center gap-2 font-mono text-[11px] font-bold uppercase tracking-widest text-brand">
            create agent
            <span className="text-ink-faint">· {STEPS[step]}</span>
          </div>
          <button onClick={onClose} aria-label="Close"
                  className="flex h-9 w-9 items-center justify-center rounded-md text-ink-mute hover:text-ink">✕</button>
        </div>

        <div className="flex gap-1 px-4 pt-3" aria-hidden>
          {STEPS.map((s, i) => (
            <span key={s} className={`h-1 flex-1 rounded-full ${i <= step ? "bg-brand" : "bg-line"}`} />
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {step === 0 && (
            <>
              <p className="mb-3 text-sm text-ink-mute">Pick a face for your agent.</p>
              <div className="grid grid-cols-3 gap-3 sm:grid-cols-5">
                {avatars.map((a) => (
                  <button key={a} onClick={() => pickAvatar(a)}
                          aria-label={AVATAR_NAMES[a] ?? a}
                          aria-pressed={avatar === a}
                          className={`group rounded-md border p-2 transition hover:scale-105 ${
                            avatar === a ? "border-brand bg-brand/10" : "border-line hover:border-brand/60"}`}>
                    <img src={`/brand/${a}`} alt="" width={72} height={72} loading="lazy"
                         className={`mx-auto aspect-square w-full rounded-full border-2 object-cover transition ${
                           avatar === a ? "border-brand" : "border-line-strong group-hover:border-brand/60"}`} />
                    <div className="mt-1.5 truncate text-center font-mono text-[10px] uppercase tracking-wide text-ink-faint">
                      {AVATAR_NAMES[a] ?? a}
                    </div>
                  </button>
                ))}
              </div>
            </>
          )}

          {step === 1 && (
            <div className="space-y-3">
              <label className="block">
                <span className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-ink-faint">name</span>
                <input value={name} onChange={(e) => setName(e.target.value)}
                       className="min-h-[42px] w-full rounded-md border border-line-strong bg-ground/60 px-3 text-sm text-ink focus:border-brand/60 focus:outline-none" />
              </label>
              <label className="block">
                <span className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-ink-faint">crew role (optional)</span>
                <select value={role} onChange={(e) => setRole(e.target.value)}
                        className="min-h-[42px] w-full rounded-md border border-line-strong bg-ground/60 px-3 text-sm text-ink focus:border-brand/60 focus:outline-none">
                  <option value="">No role — a free-standing agent</option>
                  {roles.map((r) => (
                    <option key={r.role} value={r.role}>{r.name} — {r.mission}</option>
                  ))}
                </select>
              </label>
              {role && (
                <p className="text-xs text-ink-faint">
                  {roles.find((r) => r.role === role)?.mission}
                </p>
              )}
            </div>
          )}

          {step === 2 && (
            <div className="space-y-3">
              <p className="text-sm text-ink-mute">
                What this agent runs on. Every profile below is what the runtime
                actually does today — strengths and limits both.
              </p>
              {runtimeList.map((r) => (
                <RuntimeCard key={r.id} runtime={r} selected={type === r.id}
                             onSelect={setType} />
              ))}
              {type === "custom_http" && (
                <div className="space-y-2 rounded-md border border-line p-3">
                  <input value={endpoint} onChange={(e) => setEndpoint(e.target.value)}
                         placeholder="https://your-agent.example/run"
                         className="min-h-[40px] w-full rounded-md border border-line-strong bg-ground/60 px-3 text-sm text-ink" />
                  <input value={authHeader} onChange={(e) => setAuthHeader(e.target.value)}
                         placeholder="Authorization"
                         className="min-h-[40px] w-full rounded-md border border-line-strong bg-ground/60 px-3 text-sm text-ink" />
                  <input value={credential} onChange={(e) => setCredential(e.target.value)}
                         placeholder="header value (stored encrypted)" type="password"
                         className="min-h-[40px] w-full rounded-md border border-line-strong bg-ground/60 px-3 text-sm text-ink" />
                </div>
              )}
              {type === "openclaw" && (
                <div className="space-y-2 rounded-md border border-line p-3">
                  <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
                         placeholder="gateway base URL (blank = default)"
                         className="min-h-[40px] w-full rounded-md border border-line-strong bg-ground/60 px-3 text-sm text-ink" />
                  <input value={credential} onChange={(e) => setCredential(e.target.value)}
                         placeholder="gateway token (optional, stored encrypted)" type="password"
                         className="min-h-[40px] w-full rounded-md border border-line-strong bg-ground/60 px-3 text-sm text-ink" />
                </div>
              )}
            </div>
          )}

          {step === 3 && (
            <div className="space-y-3">
              {!expert ? (
                <p className="rounded-md border border-line bg-ground/40 p-3 text-sm text-ink-mute">
                  Handled automatically by your current AI mode — your crew runs on the
                  models that mode provides. Switch to EXPERT in Settings to pin a
                  specific provider and model per role.
                </p>
              ) : !role ? (
                <p className="rounded-md border border-line bg-ground/40 p-3 text-sm text-ink-mute">
                  Model policy is set per crew role. Give this agent a role in step 2 to
                  choose its brain, or leave it inheriting the Orchestrator&apos;s model.
                </p>
              ) : (
                <>
                  <label className="block">
                    <span className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-ink-faint">provider</span>
                    <select value={provider} onChange={(e) => { setProvider(e.target.value); setModel(""); }}
                            className="min-h-[42px] w-full rounded-md border border-line-strong bg-ground/60 px-3 text-sm text-ink">
                      <option value="">Inherit the Orchestrator&apos;s model</option>
                      {connected.map((p) => <option key={p.provider} value={p.provider}>{p.provider}</option>)}
                    </select>
                  </label>
                  {provider && (
                    <label className="block">
                      <span className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-ink-faint">model</span>
                      <select value={model} onChange={(e) => setModel(e.target.value)}
                              className="min-h-[42px] w-full rounded-md border border-line-strong bg-ground/60 px-3 text-sm text-ink">
                        <option value="">Select a model…</option>
                        {models.map((m) => <option key={m.model_id} value={m.model_id}>{m.display_name}</option>)}
                      </select>
                    </label>
                  )}
                  {connected.length === 0 && (
                    <p className="text-xs text-warn">
                      No provider keys connected yet — add one in Connections first.
                    </p>
                  )}
                </>
              )}
            </div>
          )}

          {error && <div className="mt-3"><ErrorBox message={error} /></div>}
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-line px-4 py-3">
          <Button variant="ghost" disabled={busy || step === 0} onClick={() => setStep(step - 1)}>
            Back
          </Button>
          {step < STEPS.length - 1 ? (
            <Button disabled={!canNext} onClick={() => setStep(step + 1)}>Next</Button>
          ) : (
            <Button disabled={busy || !name.trim()} onClick={submit}>
              {busy ? "Creating…" : "Create agent"}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
