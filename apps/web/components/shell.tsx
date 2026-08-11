"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { api, getToken, setToken } from "@/lib/api";
import { Wordmark } from "@/components/brand";
import { ManagerPanel } from "@/components/manager";
import { FactoryToggle, GiftBanner, TrialBanner } from "@/components/factory-toggle";

type NavItem = { href: string; label: string; glyph: string };
type NavGroup = { label: string; items: NavItem[] };

const NAV_GROUPS: NavGroup[] = [
  {
    label: "command",
    items: [
      { href: "/command", label: "Command Center", glyph: "⌂" },
      { href: "/chat", label: "Chat", glyph: "»" },
    ],
  },
  {
    label: "intelligence",
    items: [
      { href: "/xray", label: "X-Ray", glyph: "⊘" },
      { href: "/market", label: "Market", glyph: "◎" },
    ],
  },
  {
    label: "operations",
    items: [
      { href: "/goals", label: "Goals", glyph: "◬" },
      { href: "/projects", label: "Projects", glyph: "▤" },
      { href: "/agents", label: "Crew", glyph: "⬡" },
    ],
  },
  {
    label: "resources",
    items: [
      { href: "/money", label: "Money", glyph: "▣" },
      { href: "/connections", label: "Connections", glyph: "#" },
      { href: "/bar", label: "The Bar", glyph: "🍺" },
    ],
  },
  {
    label: "system",
    items: [
      { href: "/activity", label: "Activity", glyph: "≡" },
      { href: "/settings", label: "Settings", glyph: "⚙" },
    ],
  },
];

const PUBLIC_PATHS = ["/", "/login", "/reset-password", "/legal", "/pricing"];
const PUBLIC_PREFIXES = ["/friends"];

function NavLinks({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  return (
    <nav aria-label="Main navigation" className="space-y-4">
      {NAV_GROUPS.map((group) => (
        <div key={group.label}>
          <div className="mb-1 px-3 font-mono text-[9px] uppercase tracking-[0.2em] text-ink-faint">
            {group.label}
          </div>
          <div className="space-y-0.5">
            {group.items.map((item) => {
              const active = pathname === item.href;
              return (
                <Link
                  key={item.href}
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
            })}
          </div>
        </div>
      ))}
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
    <div className="flex min-h-screen">
      {/* desktop sidebar */}
      <aside className="hidden w-60 shrink-0 flex-col overflow-y-auto border-r border-line bg-panel/40 p-4 md:sticky md:top-0 md:flex md:h-screen">
        <Link href="/command" className="mb-5 block px-1">
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
          <a href="https://discord.gg/MgXM3pt2Fh" target="_blank" rel="noopener noreferrer"
             className="block px-3 py-1.5 font-mono text-[10px] uppercase tracking-widest text-ink-faint transition hover:text-ink-mute">
            🛸 Star Gate Support
          </a>
        </div>
      </aside>

      {/* mobile layout */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-line bg-ground/95 px-4 backdrop-blur md:hidden">
          <Link href="/command"><Wordmark /></Link>
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
                <a href="https://discord.gg/MgXM3pt2Fh" target="_blank" rel="noopener noreferrer"
                   className="block px-3 py-1.5 font-mono text-[10px] uppercase tracking-widest text-ink-faint transition hover:text-ink-mute">
                  🛸 Star Gate Support
                </a>
              </div>
            </div>
          </div>
        )}

        <main className="min-w-0 flex-1 p-4 pb-20 md:p-6">{children}</main>
      </div>

      {/* the Manager is reachable from every authenticated page (§12) */}
      <ManagerPanel />
    </div>
  );
}
