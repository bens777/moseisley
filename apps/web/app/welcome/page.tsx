"use client";
/* eslint-disable @next/next/no-img-element */
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { HOME_PATH } from "@/lib/routes";

/* CREW GENESIS — the first thing a new account sees, and re-runnable later
   from the Crew page (/welcome?rerun=1). Full-screen cantina; every step can
   be skipped. Roles, avatars and gating all come from the backend. */

type Member = {
  role: string; name: string; avatar: string; rationale: string;
  mission?: string; gated?: boolean;
};
type GenesisState = {
  done: boolean; skipped: boolean; gated_roles: string[];
  agents: { id: string; name: string; role: string | null; avatar: string | null }[];
  roles: { role: string; name: string; mission: string; avatar: string }[];
};

const CHIPS = [
  "Automate my busywork", "Prospect & follow up leads", "Manage my inbox",
  "Watch my market", "Grow my business", "Build something",
];

const ASSEMBLING = [
  "The Manager is assembling your crew…",
  "Reading the room…",
  "Checking who's sober enough to work…",
  "Handing out badges…",
];

function Assembling() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setI((n) => (n + 1) % ASSEMBLING.length), 1500);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="flex flex-col items-center gap-4 py-16 text-center">
      <img src="/brand/logo-mark.webp" alt="" width={64} height={64}
           className="cw-portrait w-16 animate-pulse" />
      <p className="font-display text-lg font-bold text-[var(--cw-ink)]">{ASSEMBLING[i]}</p>
      <div className="flex gap-1.5" aria-hidden>
        {[0, 1, 2].map((d) => (
          <span key={d} className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--cw-magenta)]"
                style={{ animationDelay: `${d * 0.15}s` }} />
        ))}
      </div>
    </div>
  );
}

function GenesisFlow() {
  const router = useRouter();
  const params = useSearchParams();
  const rerun = params.get("rerun") === "1";

  const [step, setStep] = useState<0 | 1 | 2>(0);
  const [intent, setIntent] = useState("");
  const [chips, setChips] = useState<string[]>([]);
  const [state, setState] = useState<GenesisState | null>(null);
  const [crew, setCrew] = useState<Member[]>([]);
  const [kept, setKept] = useState<Set<string>>(new Set());
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    api<GenesisState>("/crew/genesis/state").then((s) => {
      setState(s);
      if (rerun && s.agents.length) {
        // re-run: everything you already have is KEEP by default
        const existing: Member[] = s.agents.filter((a) => a.role).map((a) => ({
          role: a.role!, name: a.name, avatar: a.avatar || "crew-orchestrator.webp",
          rationale: "already in your crew",
          gated: s.gated_roles.includes(a.role!),
        }));
        setCrew(existing);
        setKept(new Set(existing.map((m) => m.role)));
      }
    }).catch(() => {});
  }, [rerun]);

  async function propose() {
    setBusy(true);
    setError(null);
    setStep(1);
    const text = [...chips, intent.trim()].filter(Boolean).join(". ");
    try {
      const r = await api<{ crew: Member[]; source: string }>("/crew/genesis/propose",
                                                              { body: { intent: text } });
      const merged = [...crew];
      for (const m of r.crew) if (!merged.some((x) => x.role === m.role)) merged.push(m);
      setCrew(merged);
      // gated roles start UNSELECTED; everything else is pre-kept
      setKept(new Set(merged.filter((m) => !m.gated).map((m) => m.role)));
      setStep(2);
    } catch (e) {
      setError(e instanceof Error ? e.message : "the Manager could not reach the crew");
      setStep(0);
    } finally {
      setBusy(false);
    }
  }

  function toggle(m: Member) {
    setKept((prev) => {
      const next = new Set(prev);
      if (next.has(m.role)) {
        next.delete(m.role);
        if (rerun) setRemoved((r) => new Set(r).add(m.role));
      } else {
        next.add(m.role);
        setRemoved((r) => { const n = new Set(r); n.delete(m.role); return n; });
      }
      return next;
    });
  }

  async function create() {
    if (rerun && removed.size && !confirming) { setConfirming(true); return; }
    setBusy(true);
    setError(null);
    try {
      await api("/crew/genesis/apply", {
        body: {
          crew: crew.filter((m) => kept.has(m.role))
                    .map((m) => ({ role: m.role, name: m.name, avatar: m.avatar })),
          remove_roles: [...removed],
        },
      });
      // home IS the Manager conversation — genesis hands the user straight to it
      router.replace(HOME_PATH);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not create your crew");
      setBusy(false);
    }
  }

  async function skip() {
    try { await api("/crew/genesis/apply", { body: { skip: true } }); } catch { /* ignore */ }
    router.replace(HOME_PATH);
  }

  const addable = (state?.roles ?? []).filter((r) => !crew.some((m) => m.role === r.role));

  return (
    <div className="cantina fixed inset-0 z-50 overflow-y-auto">
      <div aria-hidden className="pointer-events-none fixed inset-0 overflow-hidden"><div className="cw-stars" /></div>
      <div className="relative mx-auto max-w-3xl px-4 py-10 sm:px-6">
        <div className="mb-8 text-center">
          <img src="/brand/logo-mark.webp" alt="" width={56} height={56}
               className="cw-portrait mx-auto w-14" />
          <h1 className="font-display mt-3 text-2xl font-black sm:text-3xl">
            <span className="cw-title">{rerun ? "Redesign your crew" : "Assemble your crew"}</span>
          </h1>
        </div>

        {step === 0 && (
          <div className="cw-panel p-6">
            <p className="font-display text-lg font-bold text-[var(--cw-ink)]">
              What should your crew handle?
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {CHIPS.map((c) => (
                <button key={c}
                        onClick={() => setChips((p) => p.includes(c) ? p.filter((x) => x !== c) : [...p, c])}
                        aria-pressed={chips.includes(c)}
                        className={`rounded-full border px-3 py-1.5 text-sm transition ${
                          chips.includes(c)
                            ? "border-[var(--cw-magenta)] bg-[var(--cw-magenta)]/15 text-[var(--cw-ink)]"
                            : "border-[var(--cw-line)] text-[var(--cw-mute)] hover:border-[var(--cw-purple)]"}`}>
                  {c}
                </button>
              ))}
            </div>
            <textarea value={intent} onChange={(e) => setIntent(e.target.value)} rows={3}
                      placeholder="…or tell me in your own words."
                      className="mt-4 w-full rounded-lg border border-[var(--cw-line)] bg-[var(--cw-black)]/60 px-3 py-2 text-sm text-[var(--cw-ink)] placeholder-[var(--cw-faint)] focus:border-[var(--cw-purple)] focus:outline-none" />
            {error && <p className="mt-3 text-sm text-[var(--cw-magenta)]">{error}</p>}
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <button onClick={propose} disabled={busy}
                      className="cw-cta rounded-full px-6 py-3 text-sm font-bold uppercase tracking-wide text-white">
                Assemble my crew
              </button>
              <button onClick={skip} className="cw-faint text-xs hover:underline">
                Skip — take me to my Manager
              </button>
            </div>
          </div>
        )}

        {step === 1 && <div className="cw-panel p-6"><Assembling /></div>}

        {step === 2 && (
          <div className="cw-panel p-6">
            <p className="font-display text-lg font-bold text-[var(--cw-ink)]">
              Your crew{rerun ? "" : " — tap to keep or drop"}
            </p>
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
              {crew.map((m) => {
                const on = kept.has(m.role);
                return (
                  <button key={m.role} onClick={() => toggle(m)} aria-pressed={on}
                          className={`rounded-xl border p-3 text-center transition hover:scale-[1.03] ${
                            on ? "border-[var(--cw-magenta)] bg-[var(--cw-magenta)]/10"
                               : "border-[var(--cw-line)] opacity-70"}`}>
                    <img src={`/brand/${m.avatar}`} alt="" width={72} height={72} loading="lazy"
                         className={`mx-auto aspect-square w-full max-w-[84px] rounded-full border-2 object-cover ${
                           on ? "border-[var(--cw-magenta)]" : "border-[var(--cw-line)]"}`} />
                    <div className="mt-2 truncate text-sm font-medium text-[var(--cw-ink)]">{m.name}</div>
                    <div className="mt-0.5 line-clamp-2 text-[11px] text-[var(--cw-mute)]">
                      {m.rationale || m.mission}
                    </div>
                    {m.gated && (
                      <div className="mt-1.5">
                        <span className="rounded-sm bg-[var(--cw-gold)]/20 px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-widest text-[var(--cw-gold)]">
                          pro
                        </span>
                        <div className="mt-1 text-[10px] text-[var(--cw-faint)]">included in the Pro pass</div>
                      </div>
                    )}
                  </button>
                );
              })}
            </div>

            {addable.length > 0 && (
              <details className="mt-4">
                <summary className="cursor-pointer font-mono text-[11px] uppercase tracking-widest text-[var(--cw-faint)] hover:text-[var(--cw-mute)]">
                  + add one
                </summary>
                <div className="mt-2 flex flex-wrap gap-2">
                  {addable.map((r) => (
                    <button key={r.role}
                            onClick={() => {
                              setCrew((c) => [...c, { role: r.role, name: r.name, avatar: r.avatar,
                                                      rationale: r.mission,
                                                      gated: state?.gated_roles.includes(r.role) }]);
                              setKept((k) => new Set(k).add(r.role));
                            }}
                            className="rounded-full border border-[var(--cw-line)] px-3 py-1.5 text-xs text-[var(--cw-mute)] hover:border-[var(--cw-purple)]">
                      {r.name}
                    </button>
                  ))}
                </div>
              </details>
            )}

            {[...kept].some((r) => crew.find((m) => m.role === r)?.gated) && (
              <p className="mt-3 text-xs text-[var(--cw-gold)]">
                Pro members stay unselected until you upgrade — the rest will be created now.
              </p>
            )}
            {confirming && (
              <p className="mt-3 text-sm text-[var(--cw-ink)]">
                This will remove {removed.size} crew member{removed.size === 1 ? "" : "s"}.
                Press again to confirm.
              </p>
            )}
            {error && <p className="mt-3 text-sm text-[var(--cw-magenta)]">{error}</p>}

            <div className="mt-5 flex flex-wrap items-center gap-3">
              <button onClick={create} disabled={busy || kept.size === 0}
                      className="cw-cta rounded-full px-6 py-3 text-sm font-bold uppercase tracking-wide text-white">
                {busy ? "Creating…" : confirming ? "Yes, apply" : `Create my crew (${kept.size})`}
              </button>
              <button onClick={() => setStep(0)} className="cw-faint text-xs hover:underline">
                ← back
              </button>
              <button onClick={skip} className="cw-faint text-xs hover:underline">
                Skip — take me to my Manager
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function WelcomePage() {
  return <Suspense><GenesisFlow /></Suspense>;
}
