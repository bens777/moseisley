"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { FactoryState } from "@/components/factory-toggle";

/* Says plainly which AI mode you're in and what it costs you. Collapsible, but
   never dismissible-forever: the answer to "who is paying for this?" should
   always be one click away. */

const COLLAPSE_KEY = "mode_card_collapsed";

/* Mirrors MODE_LED in factory-toggle.tsx so the dot here and the dot in the
   sidebar selector always mean the same thing. */
const LED: Record<string, string> = {
  factory: "bg-ok led-pulse", dev: "bg-signal", custom: "bg-brand",
};

type Provider = { provider: string; has_secret: boolean };

export function ModeCard() {
  const [state, setState] = useState<FactoryState | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    api<FactoryState>("/settings").then(setState).catch(() => {});
    api<Provider[]>("/providers").then(setProviders).catch(() => {});
    try { setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1"); } catch { /* private mode */ }
  }, []);

  if (!state || !state.factory.available) return null;

  const mode = state.ai_mode;
  const f = state.factory;
  const tier = f.tier;

  // the expired case already has its own out-of-fuel messaging (sidebar banner
  // + the Settings AI-mode card) — point at it instead of repeating it
  if (mode === "factory" && tier === "expired") {
    return (
      <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-crit/40 bg-crit/[0.06] px-4 py-2.5">
        <span className="led bg-crit" aria-hidden />
        <span className="font-mono text-[11px] font-bold uppercase tracking-widest text-crit">
          trial over
        </span>
        <Link href="/settings" className="text-xs text-brand hover:underline">
          See your options →
        </Link>
      </div>
    );
  }

  const connected = providers.filter((p) => p.has_secret || p.provider === "mock")
                             .map((p) => p.provider);

  let title = "";
  let body: React.ReactNode = null;
  if (mode === "factory" && tier === "trial") {
    title = `ROOKIE MODE — AI included, free for ${f.trial_days_left ?? 0} more day${
      f.trial_days_left === 1 ? "" : "s"}`;
    body = (
      <>
        Zero setup, we provide the brain.
        <span className="mt-1 block">
          Want Moseisley free forever? Add an OpenRouter key and switch to DEV — your
          crew runs on free models, on your own quota.{" "}
          <Link href="/connections" className="text-brand hover:underline">Add key →</Link>
        </span>
      </>
    );
  } else if (mode === "factory") {
    title = "ROOKIE MODE — AI included with your pass";
    body = <>{f.fuel_used_today ?? 0}/{f.fuel_cap ?? 0} requests today.</>;
  } else if (mode === "dev") {
    title = "DEV MODE — running on YOUR OpenRouter key, free models";
    body = (
      <>
        Free forever, limited by OpenRouter&apos;s daily free quota on your account.{" "}
        <Link href="/settings" className="text-brand hover:underline">Switch mode</Link>
      </>
    );
  } else {
    title = "EXPERT MODE — your own providers";
    body = (
      <>
        {connected.length ? connected.join(", ") : "no provider connected yet"}.{" "}
        <Link href="/connections" className="text-brand hover:underline">Manage keys →</Link>
      </>
    );
  }

  function toggle() {
    setCollapsed((v) => {
      const next = !v;
      try { localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0"); } catch { /* ignore */ }
      return next;
    });
  }

  return (
    <div className="mb-4 rounded-md border border-line bg-panel/60 px-4 py-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className={`led ${LED[mode] ?? "bg-ink-faint"}`} aria-hidden />
        <span className="min-w-0 font-mono text-[11px] font-bold uppercase tracking-widest text-ink">
          {title}
        </span>
        <button onClick={toggle} aria-expanded={!collapsed}
                className="ml-auto shrink-0 font-mono text-[10px] uppercase tracking-widest text-ink-faint transition hover:text-ink-mute">
          {collapsed ? "details ▸" : "hide ▾"}
        </button>
      </div>
      {!collapsed && <p className="mt-1.5 text-xs leading-relaxed text-ink-mute">{body}</p>}
    </div>
  );
}
