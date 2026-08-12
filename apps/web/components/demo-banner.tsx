"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/* Demo data is gone from the product. This is the one thing left of it: a
   notice for accounts that still carry a demo connection from before, wired to
   the clear path that already existed.

   Nothing derived from that connection is counted or displayed any more — the
   exclusion happens server-side the moment this shipped — so this banner is
   about tidying the account, not about correcting what the user is seeing. */

type Connection = { id: string; integration_type: string };

export function useDemoData(): boolean {
  const [demo, setDemo] = useState(false);
  useEffect(() => {
    api<Connection[]>("/integrations")
      .then((cs) => setDemo(cs.some((c) => c.integration_type === "demo")))
      .catch(() => setDemo(false));
  }, []);
  return demo;
}

export function DemoBanner() {
  const demo = useDemoData();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!demo) return null;

  async function clear() {
    setBusy(true);
    setError(null);
    try {
      await api("/integrations/demo/clear", { body: {} });
      window.location.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not clear the demo data");
      setBusy(false);
    }
  }

  return (
    <div className="mb-4 rounded-md border border-warn/40 bg-warn/[0.06] px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="font-mono text-[10px] font-bold uppercase tracking-widest text-warn">
          demo data removed
        </span>
        <span className="min-w-0 text-xs leading-relaxed text-ink-mute">
          Moseisley no longer contains any sample data. Your old demo connection is
          still on the account, but nothing from it counts towards your findings or
          numbers any more.
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <button onClick={clear} disabled={busy}
                  className="min-h-[32px] rounded-md border border-warn/50 px-2.5 font-mono text-[10px] font-bold uppercase tracking-widest text-warn transition hover:bg-warn/10 disabled:opacity-40">
            {busy ? "clearing…" : "Clear mine now"}
          </button>
          <Link href="/connections"
                className="min-h-[32px] rounded-md border border-brand/50 px-2.5 py-1.5 font-mono text-[10px] font-bold uppercase tracking-widest text-brand transition hover:bg-brand/10">
            Connect a real source →
          </Link>
        </div>
      </div>
      {error && <p className="mt-1.5 text-xs text-crit">{error}</p>}
    </div>
  );
}
