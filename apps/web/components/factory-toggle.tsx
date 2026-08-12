"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

/* AI mode selector — three modes, exactly one active.
     ROOKIE (internal "factory") · platform AI, 14-day trial → Basic/Pro
     DEV    (internal "dev")     · the user's own OpenRouter key, ":free" only
     EXPERT (internal "custom")  · full BYOK, subscriber-only
   Internal ai_mode values are deliberately unchanged — this is a label rename
   plus one new mode. Modeled on EmergencyStop's confirm-and-act pattern; the
   selector and tier banner render in BOTH sidebar and mobile drawer stacks. */

/* Where "self-host the Cantina" points. The repository is private during
   development, so this falls back to the Community tier on the pricing page;
   swap in the public repo/docs URL when it ships. */
export const SELF_HOST_URL = "/pricing";

export type AiMode = "factory" | "dev" | "custom";

export const MODE_LABEL: Record<AiMode, string> = {
  factory: "Rookie", dev: "Dev", custom: "Expert",
};
export const MODE_SUBTITLE: Record<AiMode, string> = {
  factory: "AI included · zero setup",
  dev: "Your OpenRouter key · free models",
  custom: "All providers · your keys",
};

export type FactoryState = {
  ai_mode: AiMode;
  byok_allowed?: boolean;
  dev_key_connected?: boolean;
  factory: {
    available: boolean;
    tier?: "trial" | "paid" | "expired";
    trial_days_left?: number;
    fuel_used_today?: number;
    fuel_cap?: number;
    fuel_balance?: number;   // purchased at The Bar; never expires
    has_provider_connections?: boolean;
  };
  settings?: Record<string, unknown>;
};

/* Shared action shown wherever the crew is out of fuel. */
export function BarLink({ className = "" }: { className?: string }) {
  return (
    <Link href="/bar"
          className={`inline-flex min-h-[36px] items-center gap-1.5 rounded-md border border-brand/50 px-3 font-mono text-[10px] font-bold uppercase tracking-widest text-brand transition hover:bg-brand/10 ${className}`}>
      Grab a drink at the Bar 🍺
    </Link>
  );
}

/* Dismissible "someone bought you a round" banner (settings_json flag). */
export function GiftBanner() {
  const state = useFactoryState();
  const [dismissed, setDismissed] = useState(false);
  const gift = state?.settings?.bar_gift_pending as
    { from?: string; fuel?: number } | null | undefined;
  if (!gift || dismissed) return null;
  return (
    <div className="rounded-md border border-brand/50 bg-brand/10 p-2">
      <p className="text-center font-mono text-[10px] uppercase tracking-widest text-brand">
        🍺 {gift.from || "Someone"} bought you a drink at the Cantina
        {gift.fuel ? ` (+${gift.fuel} fuel)` : ""}
      </p>
      <button
        onClick={async () => {
          setDismissed(true);
          await api("/settings", { method: "PATCH", body: { settings: { bar_gift_pending: null } } })
            .catch(() => {});
        }}
        className="mt-1.5 w-full min-h-[32px] rounded-sm border border-line-strong text-[10px] uppercase tracking-widest text-ink-mute"
      >
        Cheers, dismiss
      </button>
    </div>
  );
}

export function useFactoryState(): FactoryState | null {
  const [state, setState] = useState<FactoryState | null>(null);
  useEffect(() => {
    api<FactoryState>("/settings").then(setState).catch(() => setState(null));
  }, []);
  return state;
}

/* One small muted line, shown where own-keys inputs are locked. Not a modal,
   not a nag — it states the fact and links to the upgrade. */
export function ByokLockedNote({ className = "" }: { className?: string }) {
  return (
    <p className={`text-xs text-ink-faint ${className}`}>
      Free trial — platform AI included.{" "}
      <Link href="/settings#billing" className="text-brand hover:underline">
        Upgrade to connect your own keys
      </Link>
      , or <Link href={SELF_HOST_URL} className="text-brand hover:underline">self-host</Link>{" "}
      for free.
    </p>
  );
}

const MODE_LED: Record<AiMode, string> = {
  factory: "bg-ok led-pulse", dev: "bg-signal", custom: "bg-brand",
};

export function FactoryToggle({ compact = true }: { compact?: boolean }) {
  const state = useFactoryState();
  const [picking, setPicking] = useState(false);   // collapsed by default
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // clicking outside (or ESC) collapses it again — same in the desktop
  // sidebar and the mobile drawer, both of which render this component
  useEffect(() => {
    if (!picking) return;
    function onPointerDown(e: MouseEvent | TouchEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setPicking(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setPicking(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [picking]);

  if (!state || !state.factory.available) return null;
  const active = state.ai_mode;
  // EXPERT stays locked exactly where "custom" was locked before.
  const expertLocked = state.byok_allowed === false;
  const devKey = state.dev_key_connected === true;
  const modes: AiMode[] = expertLocked ? ["factory", "dev"] : ["factory", "dev", "custom"];

  async function select(next: AiMode) {
    if (!state || next === active) { setPicking(false); return; }
    setBusy(true);
    try {
      await api("/settings", { method: "PATCH", body: { settings: { ai_mode: next } } });
      setPicking(false);
      if (next === "dev" && !devKey) {
        window.location.href = "/connections";   // needs their OpenRouter key
        return;
      }
      if (next === "custom" && !state.factory.has_provider_connections) {
        window.location.href = "/connections";   // no keys yet — BYOK screen
        return;
      }
      window.location.reload();
    } catch (e) {
      setHint(e instanceof Error ? e.message : "could not switch mode");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div ref={wrapRef} className="px-1">
      {picking ? (
        <div className="rounded-md border border-crit/50 bg-crit/10 p-2">
          <p className="mb-2 text-center font-mono text-[10px] uppercase tracking-widest text-crit">
            pick your crew&apos;s brains
          </p>
          <div className="space-y-1">
            {modes.map((m) => (
              <button key={m} onClick={() => select(m)} disabled={busy}
                      className={`flex w-full min-h-[36px] flex-col items-start justify-center rounded-sm px-2 py-1 text-left transition ${
                        m === active
                          ? "bg-crit text-ground"
                          : "border border-line-strong text-ink-mute hover:bg-raised"}`}>
                <span className="font-mono text-[10px] font-bold uppercase tracking-widest">
                  {MODE_LABEL[m]}{m === active ? " · active" : ""}
                </span>
                <span className={`font-mono text-[9px] ${m === active ? "text-ground/80" : "text-ink-faint"}`}>
                  {MODE_SUBTITLE[m]}
                </span>
              </button>
            ))}
          </div>
          {!devKey && (
            <p className="mt-1.5 text-center font-mono text-[9px] uppercase tracking-widest text-ink-faint">
              dev needs your key —{" "}
              <Link href="/connections" className="text-brand hover:underline">connect OpenRouter</Link>
            </p>
          )}
          <button onClick={() => setPicking(false)}
                  className="mt-1.5 w-full min-h-[32px] rounded-sm border border-line-strong text-[10px] uppercase tracking-widest text-ink-mute">
            Cancel
          </button>
          {hint && <p className="mt-1 text-center text-[10px] text-crit">{hint}</p>}
        </div>
      ) : (
        <button
          onClick={() => setPicking(true)}
          className="flex min-h-[40px] w-full items-center justify-center gap-2 rounded-md border border-crit/40 font-mono text-[10px] font-bold uppercase tracking-widest text-ink-mute transition hover:border-crit hover:bg-crit/10"
          title={`${MODE_SUBTITLE[active]} — tap to change`}
          aria-expanded={picking}
        >
          <span className={`led ${MODE_LED[active]}`} aria-hidden />
          {MODE_LABEL[active]} · active
        </button>
      )}
      {!compact && (
        <p className="mt-1 text-center font-mono text-[9px] uppercase tracking-widest text-ink-faint">
          {MODE_SUBTITLE[active]}
        </p>
      )}
    </div>
  );
}

/* Slim tier banner: trial → days left; paid → nothing; expired → out of fuel. */
export function TrialBanner() {
  const state = useFactoryState();
  if (!state || !state.factory.available || state.ai_mode !== "factory") return null;
  const f = state.factory;
  if (f.tier === "paid") return null;
  const bonus = f.fuel_balance ?? 0;
  if (f.tier === "expired") {
    return (
      <div className="space-y-1.5">
        <Link href="/settings"
              className="block rounded-md border border-crit/50 bg-crit/10 px-2 py-1.5 text-center font-mono text-[9px] uppercase tracking-widest text-crit hover:bg-crit/20">
          {bonus > 0
            ? `■ trial over — running on ${bonus} bonus fuel`
            : "■ your crew is out of fuel — refuel"}
        </Link>
        {bonus === 0 && <div className="px-1 text-center"><BarLink /></div>}
      </div>
    );
  }
  return (
    <div className="px-1 text-center font-mono text-[9px] uppercase tracking-widest text-ink-faint">
      <span className="led bg-ok mr-1.5 inline-block align-middle" aria-hidden />
      rookie fuel: {f.trial_days_left ?? 0} day{(f.trial_days_left ?? 0) === 1 ? "" : "s"} left
      {bonus > 0 && <div className="mt-0.5">+{bonus} bonus fuel</div>}
    </div>
  );
}

/* Trial-over screen: two honest options on the hosted platform — subscribe, or
   run the open-source Cantina yourself. On a self-hosted deployment BYOK is
   never gated, so the own-keys route is shown instead. No dark patterns. */
export function FactoryExpiredOptions() {
  const state = useFactoryState();
  const byokAllowed = state?.byok_allowed !== false;
  const [error, setError] = useState<string | null>(null);
  const checkout = (plan: "basic" | "pro") => async () => {
    try {
      const r = await api<{ url: string }>("/billing/checkout", { body: { plan } });
      window.location.href = r.url;
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
  };
  return (
    <div className="space-y-2">
      <p className="font-mono text-[11px] uppercase tracking-widest text-crit">
        ■ trial over — pick your fuel source
      </p>
      <div className="rounded-md border border-line p-3">
        <div className="text-sm font-medium">1 · Refuel monthly — AI included</div>
        <div className="mt-1.5 flex flex-wrap gap-2">
          <button onClick={checkout("basic")}
                  className="min-h-[36px] rounded-md border border-line-strong px-3 text-sm text-ink hover:bg-raised">
            Basic — $9/mo
          </button>
          <button onClick={checkout("pro")}
                  className="min-h-[36px] rounded-md bg-brand px-3 text-sm font-medium text-ground">
            Pro — $19/mo
          </button>
        </div>
      </div>
      <div className="rounded-md border border-line p-3">
        <div className="text-sm font-medium">2 · Go DEV — your free OpenRouter key</div>
        <p className="mt-0.5 text-xs text-ink-mute">
          Keep working for nothing: connect your own{" "}
          <Link href="/connections" className="text-brand hover:underline">OpenRouter key</Link>{" "}
          and your crew runs on free models. No subscription, no platform limits —
          your key, your quota.
        </p>
      </div>
      <div className="rounded-md border border-line p-3">
        <div className="text-sm font-medium">3 · Just need a top-up?</div>
        <p className="mt-0.5 text-xs text-ink-mute">
          One-time drinks at the Bar: from $2 for 50 requests. No subscription,
          and the fuel never expires.
        </p>
        <div className="mt-2"><BarLink /></div>
      </div>
      {byokAllowed ? (
        <div className="rounded-md border border-line p-3">
          <div className="text-sm font-medium">4 · EXPERT — bring your own keys</div>
          <p className="mt-0.5 text-xs text-ink-mute">
            Any provider, or your own Ollama via a{" "}
            <Link href="/connections" className="text-brand hover:underline">custom provider</Link>{" "}
            (base URL e.g. http://localhost:11434/v1). Billed directly by them.
          </p>
        </div>
      ) : (
        <div className="rounded-md border border-line p-3">
          <div className="text-sm font-medium">4 · Self-host the Cantina — free forever</div>
          <p className="mt-0.5 text-xs text-ink-mute">
            Run Moseisley on your own machine: no subscription, no limits, and
            your own API keys or a local Ollama.{" "}
            <Link href={SELF_HOST_URL} className="text-brand hover:underline">
              How to self-host →
            </Link>
          </p>
        </div>
      )}
      {error && <p className="text-xs text-crit">{error}</p>}
    </div>
  );
}
