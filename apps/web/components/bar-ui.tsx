"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/* The Bar — one-time fuel purchases, Cantina brand system (.cantina tokens).
   Honest commerce: price and fuel up front, "never expires" stated, no timers,
   no fake scarcity, no pre-selected upsell. Never rendered on public pages. */

export type Drink = {
  sku: string; name: string; price_usd: number; fuel: number;
  gift: boolean; tagline: string; available: boolean;
};

export type BarMenu = {
  open: boolean; items: Drink[]; fuel_balance: number; closed_reason: string | null;
};

export type FriendOption = { handle: string; display_name: string };

const GLYPHS: Record<string, string> = {
  nebula_ale: "🍺", purple_tentacle_punch: "🍹", buy_a_round: "🥂",
};

const ACCENTS: Record<string, string> = {
  nebula_ale: "cw-panel-cyan", purple_tentacle_punch: "cw-panel-magenta",
  buy_a_round: "cw-panel-gold",
};

const LEDS: Record<string, string> = {
  nebula_ale: "cw-led-cyan", purple_tentacle_punch: "cw-led-magenta",
  buy_a_round: "cw-led-gold",
};

export function FriendPicker({ value, onChange }: {
  value: string; onChange: (handle: string) => void;
}) {
  const [friends, setFriends] = useState<FriendOption[] | null>(null);
  useEffect(() => {
    api<FriendOption[]>("/public/friends", { params: { limit: "50" } })
      .then(setFriends).catch(() => setFriends([]));
  }, []);

  if (friends === null) {
    return <p className="cw-faint text-xs">loading the room…</p>;
  }
  if (friends.length === 0) {
    return (
      <p className="cw-faint text-xs">
        Nobody has published a profile in the cantina yet — no one to send a round to.
      </p>
    );
  }
  return (
    <label className="block">
      <span className="cw-faint mb-1 block font-mono text-[10px] uppercase tracking-widest">
        send it to
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="cw-ink min-h-[42px] w-full rounded-md border border-[var(--cw-line)] bg-[var(--cw-raised)] px-3 text-sm"
      >
        <option value="">choose a friend…</option>
        {friends.map((f) => (
          <option key={f.handle} value={f.handle}>
            {f.display_name} · @{f.handle}
          </option>
        ))}
      </select>
    </label>
  );
}

export function DrinkCard({ drink, barOpen, onBuy, busy }: {
  drink: Drink; barOpen: boolean; busy: boolean;
  onBuy: (sku: string, friendHandle?: string) => void;
}) {
  const [friend, setFriend] = useState("");
  const disabled = !barOpen || !drink.available || busy
    || (drink.gift && !friend);
  return (
    <div className={`cw-panel ${ACCENTS[drink.sku] ?? ""} flex flex-col p-5 ${
      barOpen && drink.available ? "" : "opacity-55"}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="text-3xl leading-none" aria-hidden>{GLYPHS[drink.sku] ?? "🍸"}</div>
        <div className="text-right">
          <div className="cw-ink font-display text-xl font-bold">
            ${drink.price_usd}
          </div>
          <div className="cw-faint font-mono text-[10px] uppercase tracking-widest">
            one time
          </div>
        </div>
      </div>
      <h3 className="cw-ink mt-3 font-display text-lg font-bold">
        {drink.name}
      </h3>
      <p className="cw-mute mt-1 flex items-center gap-2 font-mono text-[11px] uppercase tracking-widest">
        <span className={`led ${LEDS[drink.sku] ?? "cw-led-cyan"}`} aria-hidden />
        {drink.gift ? `+${drink.fuel} fuel for them` : `+${drink.fuel} fuel for you`}
      </p>
      <p className="cw-mute mt-2 flex-1 text-sm">{drink.tagline}</p>

      {drink.gift && barOpen && drink.available && (
        <div className="mt-3"><FriendPicker value={friend} onChange={setFriend} /></div>
      )}

      <button
        onClick={() => onBuy(drink.sku, drink.gift ? friend : undefined)}
        disabled={disabled}
        className="cw-cta mt-4 min-h-[44px] w-full rounded-full px-4 text-sm font-bold uppercase tracking-wide disabled:cursor-not-allowed disabled:opacity-50"
      >
        {!barOpen || !drink.available
          ? "bar closed"
          : drink.gift ? "buy the round" : "order"}
      </button>
    </div>
  );
}
