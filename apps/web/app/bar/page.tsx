"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { BarMenu, DrinkCard } from "@/components/bar-ui";
import { Loading } from "@/components/ui";

/* The Bar — in-dashboard one-time fuel purchases. Authenticated only; this
   page is deliberately absent from the public site and the pricing page. */

export default function BarPage() {
  const menu = useApi<BarMenu>("/billing/bar/menu");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  useEffect(() => {
    const status = new URLSearchParams(window.location.search).get("purchase");
    if (status === "success") {
      setFlash("Order received. Your fuel lands as soon as the payment clears — refresh in a moment.");
    } else if (status === "canceled") {
      setFlash("No worries — nothing was charged.");
    }
  }, []);

  async function buy(sku: string, friendHandle?: string) {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ url: string }>("/billing/bar/checkout", {
        body: { sku, friend_id: friendHandle || null },
      });
      window.location.href = r.url;
    } catch (e) {
      setError(e instanceof Error ? e.message : "the bartender is unavailable");
      setBusy(false);
    }
  }

  if (menu.loading) return <Loading />;
  const m = menu.data;
  if (!m) return null;

  return (
    <div className="cantina -m-4 min-h-screen p-4 md:-m-6 md:p-6">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6">
          <div className="cw-faint font-mono text-[10px] uppercase tracking-[0.3em]">
            moseisley.sh · back room
          </div>
          <h1 className="cw-title font-display text-3xl font-black tracking-tight sm:text-4xl">
            The Bar
          </h1>
          <p className="cw-mute mt-2 max-w-2xl text-sm">
            Extra fuel for your crew, one round at a time. Every drink is a single
            payment — no subscription, no auto-renewal.{" "}
            <strong className="cw-ink">Purchased fuel never expires.</strong>{" "}
            It is spent only after your daily allowance runs out.
          </p>
        </header>

        <div className="cw-panel mb-6 flex flex-wrap items-center gap-x-6 gap-y-2 p-4">
          <div>
            <div className="cw-faint font-mono text-[10px] uppercase tracking-widest">
              bonus fuel in your tank
            </div>
            <div className="cw-ink font-display text-2xl font-bold">
              {m.fuel_balance} <span className="cw-mute text-sm font-normal">requests</span>
            </div>
          </div>
          <Link href="/settings"
                className="cw-cyan-text ml-auto font-mono text-[11px] uppercase tracking-widest hover:underline">
            AI mode &amp; daily allowance →
          </Link>
        </div>

        {flash && (
          <p className="cw-ink cw-panel mb-4 p-3 text-sm">{flash}</p>
        )}
        {!m.open && (
          <p className="cw-mute cw-panel mb-4 p-3 text-sm">
            🚪 {m.closed_reason ?? "The bar is closed."} Your daily factory allowance and
            your own API keys keep working exactly as before.
          </p>
        )}
        {error && (
          <p className="cw-ink cw-panel cw-panel-magenta mb-4 p-3 text-sm">{error}</p>
        )}

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {m.items.map((drink) => (
            <DrinkCard key={drink.sku} drink={drink} barOpen={m.open}
                       busy={busy} onBuy={buy} />
          ))}
        </div>

        <p className="cw-faint mt-6 text-xs">
          Payments are processed by Stripe; fuel is credited once the payment is
          confirmed. Fuel powers platform AI (ROOKIE mode) only — if you run
          on your own API keys, your provider bills you directly and nothing here
          applies. Questions:{" "}
          <a href="mailto:cantina@moseisley.sh" className="cw-cyan-text hover:underline">cantina@moseisley.sh</a>
        </p>
      </div>
    </div>
  );
}
