"use client";
/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { useEffect, useState } from "react";

/* THE DARVAS CHALLENGE — public, no auth.

   A simulated portfolio trading fictional money by a deterministic rulebook,
   with every decision published. The transparency is the product demo: the
   legal framing is stated at the top, in the body text, and beside the number,
   never as fine print. */

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Decision = {
  date: string; symbol: string; action: "buy" | "sell" | "trail"; reason: string;
  price: string; units: string; box_top: string; box_bottom: string; stop: string;
  equity_cents_after: number; realized_pnl_cents: number | null;
};
type Position = {
  symbol: string; units: string; entry_price: string; entry_date: string; stop: string;
  last_price: string | null; cost_cents: number; value_cents: number;
  unrealized_pnl_cents: number;
};
type State = {
  legal: string; status: "running" | "paused" | "not_started"; note: string;
  as_of: string | null;
  method: { name: string; rules: string[]; decided_by: string };
  watchlist: { symbol: string; name: string }[];
  stats: {
    start_cents: number; equity_cents: number; pnl_cents: number; pnl_pct: number;
    closed_trades: number; wins: number; win_rate_pct: number | null;
    max_drawdown_pct: number; realized_pnl_cents: number;
  };
  equity_curve: { date: string; equity_cents: number; status: string }[];
  positions: Position[];
  decisions: Decision[];
  decision_count: number;
};

const usd = (cents: number) =>
  (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });

const ACTION_STYLE: Record<Decision["action"], string> = {
  buy: "border-[var(--cw-cyan)]/50 bg-[var(--cw-cyan)]/10 text-[var(--cw-cyan)]",
  sell: "border-[var(--cw-magenta)]/50 bg-[var(--cw-magenta)]/10 text-[var(--cw-magenta)]",
  trail: "border-[var(--cw-gold)]/50 bg-[var(--cw-gold)]/10 text-[var(--cw-gold)]",
};

function EquityCurve({ points }: { points: State["equity_curve"] }) {
  if (points.length < 2) {
    return (
      <p className="cw-faint py-10 text-center text-sm">
        The curve appears after the first full run.
      </p>
    );
  }
  const W = 800, H = 220, PAD = 4;
  const values = points.map((p) => p.equity_cents);
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const x = (i: number) => (i / (points.length - 1)) * W;
  const y = (v: number) => PAD + (1 - (v - min) / span) * (H - PAD * 2);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.equity_cents).toFixed(1)}`).join(" ");
  const area = `${line} L${W},${H} L0,${H} Z`;
  const startY = y(points[0].equity_cents);
  const up = values[values.length - 1] >= values[0];
  const stroke = up ? "var(--cw-cyan)" : "var(--cw-magenta)";

  return (
    <figure className="m-0">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-56 w-full" role="img"
           aria-label={`Simulated equity from ${points[0].date} to ${points[points.length - 1].date}, ${usd(values[values.length - 1])}`}>
        <defs>
          <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
        {/* the $10,000 start line — every curve is read against it */}
        <line x1="0" y1={startY} x2={W} y2={startY} stroke="var(--cw-line)"
              strokeWidth="1" strokeDasharray="4 4" />
        <path d={area} fill="url(#eq)" />
        <path d={line} fill="none" stroke={stroke} strokeWidth="2"
              strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={x(points.length - 1)} cy={y(values[values.length - 1])} r="3.5"
                fill={stroke} />
      </svg>
      <figcaption className="cw-faint mt-2 flex justify-between font-mono text-[10px] uppercase tracking-widest">
        <span>{points[0].date}</span>
        <span>fictional equity · start {usd(points[0].equity_cents)}</span>
        <span>{points[points.length - 1].date}</span>
      </figcaption>
    </figure>
  );
}

function Stat({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="cw-panel p-4">
      <div className="cw-faint font-mono text-[10px] uppercase tracking-[0.2em]">{label}</div>
      <div className={`mt-1 font-mono text-xl font-bold tabular-nums ${tone || "text-[var(--cw-ink)]"}`}>
        {value}
      </div>
    </div>
  );
}

export default function ChallengePage() {
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/api/public/challenge`, { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error("could not load the challenge"); return r.json(); })
      .then(setState)
      .catch((e) => setError(e instanceof Error ? e.message : "could not load"));
  }, []);

  return (
    <div className="cantina min-h-screen">
      <div aria-hidden className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="cw-stars" />
      </div>

      <div className="relative mx-auto max-w-5xl px-4 py-10 sm:px-6">
        {/* masthead */}
        <header className="mb-8">
          <Link href="/" className="cw-faint font-mono text-[11px] uppercase tracking-[0.2em] hover:text-[var(--cw-ink)]">
            ← moseisley<span className="text-[var(--cw-magenta)]">.sh</span>
          </Link>
          <h1 className="font-display mt-4 text-3xl font-black sm:text-5xl">
            <span className="cw-title">The Darvas Challenge</span>
          </h1>
          <p className="cw-mute mt-3 max-w-2xl text-base leading-relaxed">
            An agent trades the Nicolas Darvas box method on ten liquid crypto pairs,
            with <strong className="text-[var(--cw-ink)]">fictional money</strong>, and
            publishes every decision it makes. No hindsight, no edited log, no real funds.
          </p>
        </header>

        {/* the legal framing: first thing under the headline, not fine print */}
        <div className="mb-8 rounded-xl border-2 border-[var(--cw-gold)]/60 bg-[var(--cw-gold)]/10 p-4">
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-[var(--cw-gold)]">
            ⚠ read this first
          </div>
          <p className="mt-2 text-sm font-semibold leading-relaxed text-[var(--cw-ink)]">
            {state?.legal ?? "Simulated portfolio. Fictional money. Educational demonstration — not investment advice or a solicitation."}
          </p>
          <p className="cw-mute mt-1.5 text-xs leading-relaxed">
            There is no brokerage account and no broker connection anywhere in this system.
            No order has ever been placed. Nothing here is a recommendation to buy or sell
            anything, and past simulated results say nothing about future results.
          </p>
        </div>

        {error && (
          <div className="cw-panel mb-6 border-[var(--cw-magenta)]/50 p-4 text-sm text-[var(--cw-magenta)]">
            {error}
          </div>
        )}

        {state && (
          <>
            {/* status */}
            <div className="mb-6 flex flex-wrap items-center gap-3">
              <span className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-widest">
                <span className={`inline-block h-2 w-2 rounded-full ${
                  state.status === "running" ? "bg-[var(--cw-cyan)]"
                    : state.status === "paused" ? "bg-[var(--cw-gold)]"
                    : "bg-[var(--cw-faint)]"}`} />
                <span className="text-[var(--cw-ink)]">
                  {state.status === "running" ? "running"
                    : state.status === "paused" ? "paused — data feed down"
                    : "not started yet"}
                </span>
              </span>
              {state.as_of && <span className="cw-faint font-mono text-[11px]">as of {state.as_of}</span>}
            </div>

            {state.status === "paused" && (
              <div className="cw-panel mb-6 border-[var(--cw-gold)]/50 p-4">
                <div className="font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-[var(--cw-gold)]">
                  challenge paused
                </div>
                <p className="cw-mute mt-2 text-sm">
                  {state.note || "The market-data feed is unavailable."} The portfolio is
                  frozen exactly where it was — we would rather show you a gap than invent
                  a price.
                </p>
              </div>
            )}

            {/* stats */}
            <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat label="Fictional equity" value={usd(state.stats.equity_cents)} />
              <Stat label="P&L" value={`${state.stats.pnl_pct >= 0 ? "+" : ""}${state.stats.pnl_pct}%`}
                    tone={state.stats.pnl_pct >= 0 ? "text-[var(--cw-cyan)]" : "text-[var(--cw-magenta)]"} />
              <Stat label="Win rate"
                    value={state.stats.win_rate_pct === null ? "—" : `${state.stats.win_rate_pct}%`} />
              <Stat label="Max drawdown" value={`−${state.stats.max_drawdown_pct}%`} />
            </div>

            <div className="cw-panel mb-6 p-4 sm:p-5">
              <div className="cw-faint mb-3 font-mono text-[10px] uppercase tracking-[0.2em]">
                equity curve · fictional money
              </div>
              <EquityCurve points={state.equity_curve} />
            </div>

            {/* method */}
            <div className="cw-panel mb-6 p-4 sm:p-5">
              <h2 className="font-display text-lg font-bold text-[var(--cw-ink)]">
                The rulebook
              </h2>
              <ol className="mt-3 space-y-1.5">
                {state.method.rules.map((r, i) => (
                  <li key={i} className="cw-mute flex gap-3 text-sm">
                    <span className="cw-faint shrink-0 font-mono text-xs">{String(i + 1).padStart(2, "0")}</span>
                    <span className="min-w-0">{r}</span>
                  </li>
                ))}
              </ol>
              <p className="cw-faint mt-3 border-t border-[var(--cw-line)] pt-3 text-xs">
                {state.method.decided_by} Watchlist:{" "}
                {state.watchlist.map((w) => w.symbol).join(" · ")}.
              </p>
            </div>

            {/* positions */}
            <div className="cw-panel mb-6 p-4 sm:p-5">
              <h2 className="font-display text-lg font-bold text-[var(--cw-ink)]">
                Open positions <span className="cw-faint text-sm font-normal">(simulated)</span>
              </h2>
              {state.positions.length === 0 ? (
                <p className="cw-faint mt-3 text-sm">Flat — no box has broken out.</p>
              ) : (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[34rem] font-mono text-xs tabular-nums">
                    <thead>
                      <tr className="cw-faint text-left uppercase tracking-widest">
                        <th className="pb-2 pr-4 font-medium">Pair</th>
                        <th className="pb-2 pr-4 font-medium">Entry</th>
                        <th className="pb-2 pr-4 font-medium">Stop</th>
                        <th className="pb-2 pr-4 font-medium">Last</th>
                        <th className="pb-2 text-right font-medium">Unrealized</th>
                      </tr>
                    </thead>
                    <tbody className="cw-mute">
                      {state.positions.map((p) => (
                        <tr key={p.symbol} className="border-t border-[var(--cw-line)]">
                          <td className="py-2 pr-4 text-[var(--cw-ink)]">{p.symbol}</td>
                          <td className="py-2 pr-4">{p.entry_price}</td>
                          <td className="py-2 pr-4 text-[var(--cw-gold)]">{p.stop}</td>
                          <td className="py-2 pr-4">{p.last_price ?? "—"}</td>
                          <td className={`py-2 text-right ${
                            p.unrealized_pnl_cents >= 0 ? "text-[var(--cw-cyan)]" : "text-[var(--cw-magenta)]"}`}>
                            {p.unrealized_pnl_cents >= 0 ? "+" : ""}{usd(p.unrealized_pnl_cents)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* the decision log — the transparency IS the marketing */}
            <div className="cw-panel mb-8 p-4 sm:p-5">
              <h2 className="font-display text-lg font-bold text-[var(--cw-ink)]">
                Every decision, with its reason
              </h2>
              <p className="cw-faint mt-1 text-xs">
                {state.decision_count} recorded
                {state.decisions.length < state.decision_count &&
                  ` · showing the most recent ${state.decisions.length}`}
              </p>
              {state.decisions.length === 0 ? (
                <p className="cw-faint mt-3 text-sm">
                  Nothing yet. The first entry appears when a close breaks above a box top.
                </p>
              ) : (
                <ul className="mt-3 space-y-2">
                  {state.decisions.map((d, i) => (
                    <li key={`${d.date}-${d.symbol}-${d.action}-${i}`}
                        className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-[var(--cw-line)] pt-2 first:border-0 first:pt-0">
                      <span className="cw-faint font-mono text-[11px] tabular-nums">{d.date}</span>
                      <span className={`rounded-full border px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-widest ${ACTION_STYLE[d.action]}`}>
                        {d.action}
                      </span>
                      <span className="font-mono text-sm font-bold text-[var(--cw-ink)]">{d.symbol}</span>
                      <span className="cw-mute font-mono text-xs tabular-nums">@ {d.price}</span>
                      {d.realized_pnl_cents !== null && (
                        <span className={`font-mono text-xs tabular-nums ${
                          d.realized_pnl_cents >= 0 ? "text-[var(--cw-cyan)]" : "text-[var(--cw-magenta)]"}`}>
                          {d.realized_pnl_cents >= 0 ? "+" : ""}{usd(d.realized_pnl_cents)}
                        </span>
                      )}
                      <span className="cw-mute w-full text-xs leading-relaxed sm:w-auto sm:flex-1">
                        {d.reason}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}

        {/* CTA */}
        <div className="cw-panel cw-panel-magenta p-6 text-center">
          <h2 className="font-display text-xl font-black text-[var(--cw-ink)] sm:text-2xl">
            This is one agent following one rulebook.
          </h2>
          <p className="cw-mute mx-auto mt-2 max-w-xl text-sm leading-relaxed">
            Yours can watch your market, triage your inbox, chase your unpaid invoices —
            and show its working the same way.
          </p>
          <Link href="/register"
                className="cw-cta mt-5 inline-block rounded-full px-7 py-3 text-sm font-bold uppercase tracking-wide text-white">
            Build your own crew →
          </Link>
          <p className="cw-faint mt-4 text-[11px] leading-relaxed">
            Simulated portfolio. Fictional money. Educational demonstration — not
            investment advice or a solicitation.
          </p>
        </div>
      </div>
    </div>
  );
}
