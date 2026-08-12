"use client";
import { useState } from "react";
import { api, API_URL } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Button, Card, ErrorBox, Input, Loading, Pill } from "@/components/ui";

/* TRADING — the journal of signals the user's own TradingView strategies sent,
   and the assistant that sizes them.

   Moseisley cannot place an order. TradingView has no public API for it and we
   connect to no broker. Every surface here says so. */

type Signal = {
  id: string; received_at: string; ticker: string; action: string; price: string;
  stop: string | null; strategy: string; note: string;
  screening: { verdict?: string; reasons?: string[] };
  recommendation: {
    shares?: number; notional_cents?: number; risk_cents?: number;
    capital_fraction_pct?: string; basis?: string; reason?: string; error?: string;
  };
};

type Journal = {
  disclaimer: string;
  settings: { enabled: boolean; capital_cents: number; risk_pct: string };
  endpoint: { configured: boolean; selector: string | null; signal_count: number;
              last_used_at: string | null };
  signals: Signal[];
  total: number;
};

const usd = (cents: number) =>
  (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });

const ACTION_TONE: Record<string, string> = {
  buy: "border-ok/50 bg-ok/10 text-ok",
  sell: "border-crit/50 bg-crit/10 text-crit",
  close: "border-line-strong bg-raised text-ink-mute",
};

function Disclaimer({ prominent = false }: { prominent?: boolean }) {
  return (
    <div className={prominent
      ? "rounded-md border-2 border-warn/60 bg-warn/10 p-4"
      : "rounded-md border border-line bg-panel/50 p-3"}>
      {prominent && (
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-warn">
          ⚠ read this first
        </div>
      )}
      <p className={prominent
        ? "mt-2 text-sm font-semibold leading-relaxed text-ink"
        : "text-xs leading-relaxed text-ink-mute"}>
        Not investment advice. Signals come from YOUR TradingView strategies. You alone
        execute and are responsible.
      </p>
      {prominent && (
        <p className="mt-1.5 text-xs leading-relaxed text-ink-mute">
          Moseisley cannot place orders. TradingView has no public API for trading a
          user&apos;s account, and this platform connects to no broker — there is no
          button here that buys anything. The assistant does arithmetic on numbers you
          declared, and you decide what to do with it.
        </p>
      )}
    </div>
  );
}

function SetupInstructions({ token, onIssue, endpoint }: {
  token: string | null; onIssue: () => void; endpoint: Journal["endpoint"];
}) {
  const [copied, setCopied] = useState<string | null>(null);
  const url = token
    ? `${API_URL}/api/webhooks/tradingview/${token}`
    : `${API_URL}/api/webhooks/tradingview/<your-token>`;
  const template = `{
  "ticker": "{{ticker}}",
  "action": "buy",
  "price": {{close}},
  "stop": {{plot_0}},
  "strategy": "Darvas box breakout"
}`;

  function copy(what: string, text: string) {
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(what);
      setTimeout(() => setCopied(null), 2000);
    }).catch(() => {});
  }

  return (
    <Card title="Connect your TradingView alerts">
      <ol className="space-y-4">
        <li className="flex gap-3">
          <span className="shrink-0 font-mono text-xs text-brand">01</span>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-ink">Get your private webhook URL</div>
            <p className="mt-0.5 text-xs text-ink-mute">
              Unguessable and revocable. Anyone holding it can post signals to your
              journal, so treat it like a password.
            </p>
            {token ? (
              <div className="mt-2">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="min-w-0 flex-1 overflow-x-auto rounded-sm border border-line-strong bg-ground/70 px-2 py-1.5 font-mono text-[11px] text-signal">
                    {url}
                  </code>
                  <Button variant="ghost" onClick={() => copy("url", url)}>
                    {copied === "url" ? "Copied" : "Copy"}
                  </Button>
                </div>
                <p className="mt-1.5 text-[11px] text-warn">
                  Shown once. Save it now — issuing a new one stops this URL working.
                </p>
              </div>
            ) : (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Button onClick={onIssue}>
                  {endpoint.configured ? "Issue a new URL" : "Create my webhook URL"}
                </Button>
                {endpoint.configured && (
                  <span className="text-xs text-ink-faint">
                    One is already active ({endpoint.signal_count} signals received).
                    Issuing a new one revokes it.
                  </span>
                )}
              </div>
            )}
          </div>
        </li>

        <li className="flex gap-3">
          <span className="shrink-0 font-mono text-xs text-brand">02</span>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-ink">Create the alert in TradingView</div>
            <p className="mt-0.5 text-xs leading-relaxed text-ink-mute">
              Open your strategy or indicator → <strong className="text-ink">Alerts</strong> →
              {" "}<strong className="text-ink">Create alert</strong>. Under
              {" "}<strong className="text-ink">Notifications</strong>, tick
              {" "}<strong className="text-ink">Webhook URL</strong> and paste the URL above.
            </p>
            <p className="mt-1.5 rounded-sm border border-warn/40 bg-warn/[0.06] px-2 py-1.5 text-[11px] text-ink-mute">
              Webhook notifications need a paid TradingView plan (Essential or above).
              On the free plan the alert fires but no webhook is sent.
            </p>
          </div>
        </li>

        <li className="flex gap-3">
          <span className="shrink-0 font-mono text-xs text-brand">03</span>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-ink">Paste this as the alert message</div>
            <p className="mt-0.5 text-xs text-ink-mute">
              It must be JSON. <code className="font-mono text-[11px]">ticker</code>,
              {" "}<code className="font-mono text-[11px]">action</code> and
              {" "}<code className="font-mono text-[11px]">price</code> are required;
              {" "}<code className="font-mono text-[11px]">stop</code> is what makes real
              position sizing possible.
            </p>
            <div className="mt-2 flex flex-wrap items-start gap-2">
              <pre className="min-w-0 flex-1 overflow-x-auto rounded-sm border border-line-strong bg-ground/70 p-2 font-mono text-[11px] leading-relaxed text-ink-mute">
{template}
              </pre>
              <Button variant="ghost" onClick={() => copy("tpl", template)}>
                {copied === "tpl" ? "Copied" : "Copy"}
              </Button>
            </div>
            <p className="mt-1.5 text-[11px] leading-relaxed text-ink-faint">
              <code className="font-mono">{"{{ticker}}"}</code> and
              {" "}<code className="font-mono">{"{{close}}"}</code> are TradingView
              placeholders it fills in. Set <code className="font-mono">action</code> to
              {" "}<code className="font-mono">buy</code>, <code className="font-mono">sell</code>
              {" "}or <code className="font-mono">close</code> per alert. Replace
              {" "}<code className="font-mono">{"{{plot_0}}"}</code> with whichever plot
              carries your stop, or drop the line if you have none.
            </p>
          </div>
        </li>
      </ol>
    </Card>
  );
}

function AssistantSettings({ settings, onSaved }: {
  settings: Journal["settings"]; onSaved: () => void;
}) {
  const [enabled, setEnabled] = useState(settings.enabled);
  const [capital, setCapital] = useState(String(settings.capital_cents / 100 || ""));
  const [risk, setRisk] = useState(settings.risk_pct);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(nextEnabled: boolean) {
    setBusy(true);
    setError(null);
    try {
      await api("/trading/settings", {
        method: "PUT",
        body: {
          enabled: nextEnabled,
          capital_cents: Math.round(parseFloat(capital || "0") * 100),
          risk_pct: risk || "1",
        },
      });
      setEnabled(nextEnabled);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not save that");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Assistant mode"
          action={<Pill color={enabled ? "green" : "gray"} pulse={enabled}>
            {enabled ? "on" : "off"}
          </Pill>}>
      <p className="text-sm leading-relaxed text-ink-mute">
        With this on, every incoming signal gets a concrete size suggestion in your
        Manager conversation, computed from the two numbers below. The arithmetic runs
        in code — no model decides how much of your money to put on a trade.
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-ink-faint">
            capital you trade with ($)
          </span>
          <Input type="number" min="0" step="100" value={capital} placeholder="10000"
                 onChange={(e) => setCapital(e.target.value)} />
        </label>
        <label className="block">
          <span className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-ink-faint">
            risk per trade (%)
          </span>
          <Input type="number" min="0.1" max="100" step="0.1" value={risk}
                 onChange={(e) => setRisk(e.target.value)} />
        </label>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-ink-faint">
        With a stop in the signal, size = (capital × risk%) ÷ distance to your stop —
        a stop-out costs exactly that risk budget. Without a stop, the whole position is
        sized at the risk budget instead, because the downside cannot be bounded.
        Positions are never larger than your declared capital.
      </p>
      {error && <div className="mt-3"><ErrorBox message={error} /></div>}
      <div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-3">
        <Button onClick={() => save(true)} disabled={busy}>
          {busy ? "Saving…" : enabled ? "Save changes" : "Turn assistant on"}
        </Button>
        {enabled && (
          <Button variant="ghost" onClick={() => save(false)} disabled={busy}>
            Turn off
          </Button>
        )}
      </div>
    </Card>
  );
}

function SignalRow({ s }: { s: Signal }) {
  const rec = s.recommendation || {};
  return (
    <div className="min-w-0 rounded-md border border-line bg-ground/40 p-3">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="font-mono text-[11px] tabular-nums text-ink-faint">
          {new Date(s.received_at).toLocaleString(undefined, {
            month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
        </span>
        <span className={`rounded-full border px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-widest ${
          ACTION_TONE[s.action] || ACTION_TONE.close}`}>
          {s.action}
        </span>
        <span className="font-mono text-sm font-bold text-ink">{s.ticker}</span>
        <span className="font-mono text-xs tabular-nums text-ink-mute">@ {s.price}</span>
        {s.stop && (
          <span className="font-mono text-xs tabular-nums text-warn">stop {s.stop}</span>
        )}
        {s.strategy && (
          <span className="min-w-0 truncate text-xs text-ink-mute">{s.strategy}</span>
        )}
        {s.screening?.verdict === "suspicious" && (
          <Pill color="red">screened</Pill>
        )}
      </div>

      {rec.shares !== undefined && (
        <p className="mt-2 border-t border-line pt-2 text-sm text-ink">
          Suggested: <strong>{rec.shares} shares</strong>
          <span className="text-ink-mute">
            {" "}(~{usd(rec.notional_cents || 0)}, {rec.capital_fraction_pct}% of capital
            {rec.risk_cents !== undefined && `, risking ${usd(rec.risk_cents)}`})
          </span>
        </p>
      )}
      {rec.error && (
        <p className="mt-2 border-t border-line pt-2 text-xs text-warn">
          Not sized: {rec.error}
        </p>
      )}
      {s.note && <p className="mt-1.5 text-xs text-ink-faint">{s.note}</p>}
    </div>
  );
}

export default function TradingPage() {
  const journal = useApi<Journal>("/trading");
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function issue() {
    setError(null);
    try {
      const out = await api<{ token: string }>("/trading/endpoint", { body: {} });
      setToken(out.token);
      journal.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not create the endpoint");
    }
  }

  if (journal.loading) return <Loading />;
  if (journal.error) return <ErrorBox message={journal.error} />;
  const d = journal.data;
  if (!d) return null;

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div>
        <h1 className="text-xl font-bold">Trading</h1>
        <p className="mt-0.5 font-mono text-[11px] uppercase tracking-widest text-ink-faint">
          your TradingView signals · {d.total} received
        </p>
      </div>

      <Disclaimer prominent />
      {error && <ErrorBox message={error} />}

      <SetupInstructions token={token} onIssue={issue} endpoint={d.endpoint} />
      <AssistantSettings settings={d.settings} onSaved={journal.reload} />

      <Card title="Signals journal">
        {d.signals.length === 0 ? (
          <div className="py-6 text-center">
            <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
              nothing received yet
            </div>
            <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-ink-mute">
              Every alert your TradingView strategies send arrives here — time, ticker,
              action, price, strategy and the exact payload. Nothing is shown until a
              real signal arrives: there are no examples and no sample trades.
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {d.signals.map((s) => <SignalRow key={s.id} s={s} />)}
          </div>
        )}
      </Card>

      <Disclaimer />
    </div>
  );
}
