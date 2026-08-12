"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect, useRef, useState } from "react";
import { api, getToken, setToken } from "@/lib/api";
import { Wordmark } from "@/components/brand";
import { ManagerDock } from "@/components/manager";
import { FactoryToggle, GiftBanner, TrialBanner } from "@/components/factory-toggle";
import { HOME_PATH } from "@/lib/routes";

type NavItem = { href: string; label: string; glyph: string };

/* ChatGPT-style: the conversation sits alone at the top — it is home — then the
   few places you actually visit, and everything technical one click away under
   ADVANCED. Routes are unchanged; ADVANCED only affects what shows by default. */
const HOME_NAV: NavItem = { href: HOME_PATH, label: "Manager", glyph: "◬" };

const PRIMARY_NAV: NavItem[] = [
  { href: "/command", label: "Command Center", glyph: "⌂" },
  { href: "/agents", label: "Crew", glyph: "⬡" },
  { href: "/skills", label: "Skills", glyph: "✦" },
  { href: "/bar", label: "The Bar", glyph: "🍺" },
];

const ADVANCED_NAV: NavItem[] = [
  { href: "/xray", label: "X-Ray", glyph: "⊘" },
  { href: "/market", label: "Market", glyph: "◎" },
  { href: "/goals", label: "Goals", glyph: "◬" },
  { href: "/projects", label: "Projects", glyph: "▤" },
  { href: "/money", label: "Money", glyph: "▣" },
  { href: "/schedule", label: "Schedule", glyph: "◷" },
  { href: "/data", label: "My Data", glyph: "▦" },
  { href: "/security", label: "Security", glyph: "⛨" },
  { href: "/trading", label: "Trading", glyph: "↗" },
  { href: "/connections", label: "Connections", glyph: "#" },
  { href: "/activity", label: "Activity", glyph: "≡" },
  { href: "/settings", label: "Settings", glyph: "⚙" },
];

const ADVANCED_OPEN_KEY = "nav_advanced_open";

const PUBLIC_PATHS = ["/", "/login", "/register", "/reset-password", "/legal", "/pricing",
                      "/challenge"];
const PUBLIC_PREFIXES = ["/friends"];

function NavItemLink({ item, active, onNavigate }: {
  item: NavItem; active: boolean; onNavigate?: () => void;
}) {
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={`flex min-h-[40px] items-center gap-2.5 rounded-md border-l-2 px-3 py-1.5 text-sm transition ${
        active
          ? "border-brand bg-raised font-medium text-ink"
          : "border-transparent text-ink-mute hover:bg-panel hover:text-ink"
      }`}
    >
      <span aria-hidden className={`w-4 text-center font-mono text-xs ${active ? "text-brand" : "text-ink-faint"}`}>
        {item.glyph}
      </span>
      {item.label}
    </Link>
  );
}

function NavLinks({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  // if the user is standing inside an advanced page, never hide it from them
  const insideAdvanced = ADVANCED_NAV.some((i) => pathname === i.href);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    try { setOpen(localStorage.getItem(ADVANCED_OPEN_KEY) === "1"); } catch { /* private mode */ }
  }, []);

  function toggle() {
    setOpen((v) => {
      const next = !v;
      try { localStorage.setItem(ADVANCED_OPEN_KEY, next ? "1" : "0"); } catch { /* ignore */ }
      return next;
    });
  }

  const expanded = open || insideAdvanced;

  return (
    <nav aria-label="Main navigation" className="space-y-4">
      {/* home: the Manager conversation itself */}
      <NavItemLink item={HOME_NAV} active={pathname === HOME_NAV.href} onNavigate={onNavigate} />

      <div className="space-y-0.5 border-t border-line pt-3">
        {PRIMARY_NAV.map((item) => (
          <NavItemLink key={item.href} item={item} active={pathname === item.href}
                       onNavigate={onNavigate} />
        ))}
      </div>

      <div className="border-t border-line pt-3">
        <button
          type="button"
          onClick={toggle}
          aria-expanded={expanded}
          aria-controls="advanced-nav"
          className="flex w-full min-h-[32px] items-center gap-1.5 px-3 font-mono text-[9px] uppercase tracking-[0.2em] text-ink-faint transition hover:text-ink-mute"
        >
          <span aria-hidden>⚙</span> Advanced
          <span aria-hidden className="ml-auto">{expanded ? "▾" : "▸"}</span>
        </button>
        {expanded && (
          <div id="advanced-nav" className="mt-1 space-y-0.5">
            {ADVANCED_NAV.map((item) => (
              <NavItemLink key={item.href} item={item} active={pathname === item.href}
                           onNavigate={onNavigate} />
            ))}
          </div>
        )}
      </div>
    </nav>
  );
}

function SystemStatus() {
  const [crew, setCrew] = useState<{ ready: number; total: number } | null>(null);
  useEffect(() => {
    api<{ enabled: boolean; health_status: string }[]>("/agents")
      .then((agents) =>
        setCrew({ ready: agents.filter((a) => a.enabled && a.health_status !== "error").length,
                  total: agents.length }))
      .catch(() => setCrew(null));
  }, []);
  if (!crew) return null;
  const ok = crew.ready > 0;
  return (
    <div className="flex items-center gap-2 px-3 font-mono text-[10px] uppercase tracking-widest text-ink-faint">
      <span className={`led ${ok ? "bg-ok led-pulse" : "bg-ink-faint"}`} aria-hidden />
      {crew.ready}/{crew.total} crew ready
    </div>
  );
}

function EmergencyStop() {
  const [engaged, setEngaged] = useState<boolean | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ kill_switches: Record<string, boolean> }>("/settings")
      .then((s) => setEngaged(!!s.kill_switches.emergency_stop))
      .catch(() => setEngaged(null));
  }, []);

  async function toggle() {
    setBusy(true);
    try {
      const next = !engaged;
      await api("/settings/emergency-stop", { body: { on: next } });
      setEngaged(next);
      setConfirming(false);
      window.location.reload();
    } finally {
      setBusy(false);
    }
  }

  if (engaged === null) return null;
  return (
    <div className="px-1">
      {confirming ? (
        <div className="rounded-md border border-crit/50 bg-crit/10 p-2">
          <p className="mb-2 text-center font-mono text-[10px] uppercase tracking-widest text-crit">
            {engaged ? "resume all systems?" : "stop all systems?"}
          </p>
          <div className="flex gap-1.5">
            <button onClick={toggle} disabled={busy}
                    className="min-h-[36px] flex-1 rounded-sm bg-crit px-2 text-xs font-bold uppercase tracking-wide text-ground">
              {engaged ? "Resume" : "Stop"}
            </button>
            <button onClick={() => setConfirming(false)}
                    className="min-h-[36px] flex-1 rounded-sm border border-line-strong px-2 text-xs text-ink-mute">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setConfirming(true)}
          className={`flex min-h-[40px] w-full items-center justify-center gap-2 rounded-md border font-mono text-[11px] font-bold uppercase tracking-widest transition ${
            engaged
              ? "border-crit bg-crit/20 text-crit"
              : "border-crit/40 text-crit/80 hover:border-crit hover:bg-crit/10 hover:text-crit"
          }`}
        >
          <span aria-hidden>■</span>
          {engaged ? "system stopped" : "emergency stop"}
        </button>
      )}
    </div>
  );
}

const SUPPORT_URL = "https://discord.gg/MgXM3pt2Fh";
const SUPPORT_BLURB = "Join the Discord crew and help improve the Cantina!";

function SupportButton({ variant }: { variant: "desktop" | "mobile" }) {
  const [tip, setTip] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  function enter() {
    if (variant !== "desktop") return;
    timer.current = setTimeout(() => setTip(true), 150);   // short delay, no flicker
  }
  function leave() {
    if (timer.current) clearTimeout(timer.current);
    setTip(false);
  }

  return (
    <div className="relative px-1" onMouseEnter={enter} onMouseLeave={leave}>
      {tip && (
        <div role="tooltip"
             className="absolute bottom-full left-1 right-1 mb-2 rounded-md border border-line-strong bg-raised px-2.5 py-1.5 text-xs leading-snug text-ink-mute shadow-lg shadow-black/40">
          {SUPPORT_BLURB}
        </div>
      )}
      <a href={SUPPORT_URL} target="_blank" rel="noopener noreferrer"
         title={variant === "desktop" ? SUPPORT_BLURB : undefined}
         className="flex min-h-[40px] w-full items-center justify-center gap-2 rounded-md border border-brand/50 bg-brand/15 font-mono text-[11px] font-bold uppercase tracking-widest text-brand transition hover:bg-brand/25">
        🛸 Star Gate Support
      </a>
      {variant === "mobile" && (
        <p className="mt-1.5 px-1 text-center text-[11px] leading-snug text-ink-faint">
          {SUPPORT_BLURB}
        </p>
      )}
    </div>
  );
}

export function StoppedBanner() {
  const [engaged, setEngaged] = useState(false);
  useEffect(() => {
    api<{ kill_switches: Record<string, boolean> }>("/settings")
      .then((s) => setEngaged(!!s.kill_switches.emergency_stop))
      .catch(() => {});
  }, []);
  if (!engaged) return null;
  return (
    <div className="mb-4 rounded-md border border-crit bg-crit/10 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="font-mono text-xs font-bold uppercase tracking-[0.25em] text-crit">
            ■ system stopped
          </div>
          <div className="mt-1 grid grid-cols-2 gap-x-6 font-mono text-[10px] uppercase tracking-widest text-ink-mute sm:grid-cols-4">
            <span>llm calls: off</span><span>crew: paused</span>
            <span>external actions: off</span><span>spending: off</span>
          </div>
        </div>
        <button
          onClick={async () => {
            if (!window.confirm("Resume all systems?")) return;
            await api("/settings/emergency-stop", { body: { on: false } });
            window.location.reload();
          }}
          className="min-h-[40px] rounded-md bg-crit px-4 font-mono text-xs font-bold uppercase tracking-widest text-ground"
        >
          Resume system
        </button>
      </div>
    </div>
  );
}

function LogoutButton({ router }: { router: ReturnType<typeof useRouter> }) {
  return (
    <button
      onClick={async () => {
        try {
          await api("/auth/logout", { method: "POST", body: {} });
        } catch {}
        setToken(null);
        router.replace("/login");
      }}
      className="block w-full min-h-[40px] rounded-md px-3 py-1.5 text-left text-sm text-ink-faint hover:bg-panel hover:text-ink-mute"
    >
      Log out
    </button>
  );
}

export default function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const isPublic = PUBLIC_PATHS.includes(pathname)
    || PUBLIC_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`));
  /* Home is the conversation: it owns the viewport — no page padding, no scroll
     on the frame. */
  const isHome = pathname === HOME_PATH;
  /* The Manager bar + drawer exist for pages with no chat surface of their own.
     Home IS the conversation — Manager and Orchestrator both live there as tabs
     — so the dock would only overlap it. /chat redirects there and renders
     nothing, but it is listed so the dock never flashes over the redirect. */
  const hasOwnChat = isHome || pathname === "/chat";

  useEffect(() => {
    if (isPublic) {
      setReady(true);
      return;
    }
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    setReady(true);
  }, [pathname, isPublic, router]);

  // close the drawer on navigation & lock body scroll while open
  useEffect(() => setDrawerOpen(false), [pathname]);
  useEffect(() => {
    document.body.style.overflow = drawerOpen ? "hidden" : "";
    return () => { document.body.style.overflow = ""; };
  }, [drawerOpen]);

  if (isPublic) return <>{children}</>;
  if (!ready) return null;

  return (
    <div className={`flex ${isHome ? "h-[100dvh] overflow-hidden" : "min-h-screen"}`}>
      {/* desktop sidebar */}
      <aside className="hidden w-60 shrink-0 flex-col overflow-y-auto border-r border-line bg-panel/40 p-4 md:sticky md:top-0 md:flex md:h-screen">
        <Link href={HOME_PATH} className="mb-5 block px-1">
          <Wordmark />
        </Link>
        <NavLinks pathname={pathname} />
        <div className="mt-auto space-y-2 pt-6">
          <GiftBanner />
          <TrialBanner />
          <FactoryToggle />
          <EmergencyStop />
          <SystemStatus />
          <LogoutButton router={router} />
          <SupportButton variant="desktop" />
        </div>
      </aside>

      {/* mobile layout */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center justify-between border-b border-line bg-ground/95 px-4 backdrop-blur md:hidden">
          <Link href={HOME_PATH}><Wordmark /></Link>
          <button
            aria-label={drawerOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={drawerOpen}
            onClick={() => setDrawerOpen(!drawerOpen)}
            className="flex h-11 w-11 items-center justify-center rounded-md border border-line-strong text-ink"
          >
            {drawerOpen ? (
              <span aria-hidden className="text-lg leading-none">✕</span>
            ) : (
              <span aria-hidden className="flex flex-col gap-[5px]">
                <span className="block h-px w-5 bg-ink" />
                <span className="block h-px w-5 bg-ink" />
                <span className="block h-px w-5 bg-ink" />
              </span>
            )}
          </button>
        </header>

        {drawerOpen && (
          <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
            <button
              aria-label="Close navigation"
              className="absolute inset-0 bg-ground/70 backdrop-blur-sm"
              onClick={() => setDrawerOpen(false)}
            />
            <div className="drawer-enter absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col overflow-y-auto border-r border-line bg-panel p-4">
              <div className="mb-5 flex items-center justify-between px-1">
                <Wordmark />
                <button
                  aria-label="Close navigation"
                  onClick={() => setDrawerOpen(false)}
                  className="flex h-11 w-11 items-center justify-center rounded-md text-ink-mute"
                >
                  ✕
                </button>
              </div>
              <NavLinks pathname={pathname} onNavigate={() => setDrawerOpen(false)} />
              <div className="mt-auto space-y-2 pt-6">
                <GiftBanner />
                <TrialBanner />
                <FactoryToggle />
                <EmergencyStop />
                <SystemStatus />
                <LogoutButton router={router} />
                <SupportButton variant="mobile" />
              </div>
            </div>
          </div>
        )}

        <main className={isHome
          ? "flex min-h-0 min-w-0 flex-1 flex-col"
          : "min-w-0 flex-1 p-4 pb-20 md:p-6"}>
          {/* the Manager is the front door: always the first thing on the page —
              except where the page already has a chat of its own */}
          {!hasOwnChat && <ManagerDock />}
          {children}
        </main>
      </div>
    </div>
  );
}
