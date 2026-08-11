"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import {
  BarLink, ByokLockedNote, FactoryExpiredOptions, FactoryToggle, MODE_LABEL,
  MODE_SUBTITLE, useFactoryState,
} from "@/components/factory-toggle";
import { Button, Card, ErrorBox, Input, Loading, Pill, Stat } from "@/components/ui";

type Settings = {
  timezone: string; autonomy_mode: string; settings: Record<string, unknown>;
  kill_switches: Record<string, boolean>;
};

const SWITCH_LABELS: Record<string, string> = {
  pause_all_agents: "PAUSE ALL AGENTS",
  disable_llm: "DISABLE ALL LLM CALLS",
  disable_spending: "DISABLE SPENDING",
  disable_external_actions: "DISABLE EXTERNAL ACTIONS",
};

const PLAN_LABELS: Record<string, string> = {
  community: "Moseisley Community",
  basic: "Moseisley Basic — $9/month",
  pro: "Moseisley Pro — $19/month",
};

const TIER_LABELS: Record<string, string> = {
  trial: "free trial", paid: "included in your plan", expired: "trial over",
};

function AiModeCard() {
  const state = useFactoryState();
  if (!state || !state.factory.available) return null;
  const f = state.factory;
  const factoryOn = state.ai_mode === "factory";
  const used = f.fuel_used_today ?? 0;
  const cap = f.fuel_cap ?? 0;
  const low = cap > 0 && used >= cap * 0.8;
  const bonus = f.fuel_balance ?? 0;
  const expired = f.tier === "expired";
  const devOn = state.ai_mode === "dev";
  const byokLocked = state.byok_allowed === false;  // hosted trial: EXPERT locked
  return (
    <Card title="AI mode">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0">
          <div className="font-display text-base font-semibold">
            {MODE_LABEL[state.ai_mode]} mode
          </div>
          <div className="font-mono text-[11px] uppercase tracking-wide text-ink-faint">
            {factoryOn
              ? `platform AI · ${TIER_LABELS[f.tier ?? "trial"] ?? f.tier}`
              : MODE_SUBTITLE[state.ai_mode]}
          </div>
        </div>
        <Pill color={factoryOn ? (expired ? "red" : "green") : devOn ? "blue" : "yellow"}>
          {factoryOn ? f.tier ?? "rookie" : MODE_LABEL[state.ai_mode].toLowerCase()}
        </Pill>
      </div>
      {factoryOn && f.tier === "trial" && (
        <p className="mt-2 font-mono text-[11px] uppercase tracking-wide text-ink-mute">
          Trial: {f.trial_days_left} day{f.trial_days_left === 1 ? "" : "s"} left
        </p>
      )}
      {factoryOn && !expired && cap > 0 && (
        <p className={`mt-1 font-mono text-[11px] uppercase tracking-wide ${
          low ? "text-warn" : "text-ink-mute"}`}>
          Fuel today: {used} / {cap}{low ? " — running low, resets tomorrow" : ""}
        </p>
      )}
      {factoryOn && bonus > 0 && (
        <p className="mt-1 font-mono text-[11px] uppercase tracking-wide text-ink-mute">
          Bonus fuel: {bonus} — bought at the Bar, never expires
        </p>
      )}
      {factoryOn && expired ? (
        <div className="mt-3"><FactoryExpiredOptions /></div>
      ) : byokLocked && !devOn ? (
        <div className="mt-3 space-y-2">
          <div className="max-w-xs"><FactoryToggle compact={false} /></div>
          <ByokLockedNote />
          {/* purchased fuel works on every tier — trial users included */}
          <BarLink />
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <div className="max-w-xs grow"><FactoryToggle compact={false} /></div>
          {factoryOn && <BarLink />}
        </div>
      )}
      <p className="mt-2 text-xs text-ink-faint">
        {devOn
          ? "DEV runs your crew on your own OpenRouter key, restricted to free models — your key, your quota, no platform limits."
          : byokLocked
          ? "ROOKIE runs your crew on platform AI while the trial is on. DEV works today with your own OpenRouter key (free models). EXPERT — all providers, all models, Ollama — unlocks with a pass, and self-hosting unlocks everything for free."
          : "ROOKIE routes your crew through platform AI (a daily request cap applies). DEV uses your own OpenRouter key with free models. EXPERT uses any provider key you like, with no cap."}
      </p>
    </Card>
  );
}

function BillingCard() {
  const billing = useApi<{ plan: string; status: string; cancel_at_period_end: boolean;
                           current_period_end: string | null; configured: boolean }>("/billing");
  const [error, setError] = useState<string | null>(null);
  if (billing.loading || !billing.data) return null;
  const b = billing.data;
  const subscribed = b.plan === "basic" || b.plan === "pro";
  const checkout = (plan: "basic" | "pro") => async () => {
    try {
      const r = await api<{ url: string }>("/billing/checkout", { body: { plan } });
      window.location.href = r.url;
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
  };
  return (
    <Card title="Billing">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0">
          <div className="font-display text-base font-semibold">
            {PLAN_LABELS[b.plan] ?? "Moseisley Community"}
          </div>
          <div className="font-mono text-[11px] uppercase tracking-wide text-ink-faint">
            {subscribed
              ? `${b.status}${b.cancel_at_period_end ? " · cancels at period end" : ""}`
              : "free · self-hosted · bring your own keys"}
          </div>
        </div>
        <Pill color={subscribed ? "green" : "gray"}>{b.plan}</Pill>
        <div className="ml-auto flex flex-wrap gap-2">
          {subscribed ? (
            <>
              {b.plan === "basic" && (
                <Button onClick={checkout("pro")}>Upgrade to Pro</Button>
              )}
              <Button variant="ghost" onClick={async () => {
                try {
                  const r = await api<{ url: string }>("/billing/portal", { body: {} });
                  window.location.href = r.url;
                } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
              }}>Manage subscription · cancel anytime</Button>
            </>
          ) : (
            <>
              <Button variant="ghost" onClick={checkout("basic")}>Basic — $9/mo</Button>
              <Button onClick={checkout("pro")}>Pro — $19/mo</Button>
            </>
          )}
        </div>
      </div>
      {error && <p className="mt-2 break-words text-xs text-crit">{error}</p>}
      {!b.configured && !subscribed && (
        <p className="mt-2 text-xs text-ink-faint">
          Stripe billing is not configured on this deployment (Community mode).
        </p>
      )}
      <p className="mt-2 text-xs text-ink-faint">
        Subscriptions cover the hosted Moseisley service. AI provider usage is billed
        separately by your chosen provider using your own API key.
      </p>
      <p className="mt-2 text-xs text-ink-faint">
        Billing questions? <a href="mailto:cantina@moseisley.sh" className="text-brand hover:underline">cantina@moseisley.sh</a>
      </p>
    </Card>
  );
}

function UsageCard() {
  const usage = useApi<{
    today: { total_tokens: number; requests: number };
    month: { reported_cost: number; estimated_cost: number; unknown_cost_requests: number;
             total_tokens: number };
    by_role: Record<string, { reported_cost: number; estimated_cost: number; requests: number }>;
  }>("/usage/summary");
  if (usage.loading || !usage.data) return null;
  const u = usage.data;
  const roles = Object.entries(u.by_role)
    .sort((a, b) => (b[1].reported_cost + b[1].estimated_cost) - (a[1].reported_cost + a[1].estimated_cost))
    .slice(0, 6);
  return (
    <Card title="AI usage — persisted provider data only">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="Tokens today" value={u.today.total_tokens.toLocaleString()} />
        <Stat label="Tokens 30d" value={u.month.total_tokens.toLocaleString()} />
        <Stat label="Reported 30d" value={`$${u.month.reported_cost.toFixed(2)}`} hint="provider-billed" />
        <Stat label="Estimated 30d" value={`$${u.month.estimated_cost.toFixed(2)}`} hint="from pricing snapshots" />
      </div>
      {u.month.unknown_cost_requests > 0 && (
        <p className="mt-2 font-mono text-[11px] text-warn">
          {u.month.unknown_cost_requests} request(s) with unknown cost — no reliable pricing data.
        </p>
      )}
      {roles.length > 0 && (
        <div className="mt-3 space-y-1 border-t border-line pt-3">
          {roles.map(([role, r]) => (
            <div key={role} className="flex items-center justify-between font-mono text-[11px] text-ink-mute">
              <span className="uppercase tracking-wide">{role}</span>
              <span>${(r.reported_cost + r.estimated_cost).toFixed(2)} · {r.requests} req</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function SettingsPage() {
  const { data, error, loading, reload } = useApi<Settings>("/settings");
  const [tz, setTz] = useState<string | null>(null);

  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} />;
  if (!data) return null;

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div>
        <h1 className="text-xl font-bold">Settings</h1>
        <p className="mt-0.5 font-mono text-[11px] uppercase tracking-widest text-ink-faint">system configuration</p>
      </div>

      <Card title="Emergency controls">
        <p className="mb-3 text-xs text-ink-mute">
          Deterministic kill switches, enforced in code at execution time.
        </p>
        <div className="space-y-2">
          {Object.entries(SWITCH_LABELS).map(([key, label]) => {
            const on = data.kill_switches[key];
            return (
              <div key={key} className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium">{label}</span>
                <div className="flex items-center gap-2">
                  <Pill color={on ? "red" : "green"}>{on ? "ENGAGED" : "off"}</Pill>
                  <Button variant={on ? "primary" : "danger"} onClick={async () => {
                    await api("/settings/kill-switch", { body: { switch: key, on: !on } });
                    reload();
                  }}>
                    {on ? "Release" : "Engage"}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      <Card title="Autonomy mode">
        <div className="flex flex-wrap gap-2">
          {["advisory", "assisted", "autonomous"].map((mode) => (
            <button key={mode} onClick={async () => {
              await api("/settings", { method: "PATCH", body: { autonomy_mode: mode } });
              reload();
            }} className={`min-h-[38px] rounded-md px-3 py-1.5 text-sm capitalize ${
              data.autonomy_mode === mode ? "bg-brand text-ground" : "bg-raised text-ink-mute"
            }`}>
              {mode}
            </button>
          ))}
        </div>
        <p className="mt-2 text-xs text-ink-mute">
          Even in Autonomous mode your crew cannot alter your Constitution, bypass permissions,
          exceed Treasury limits or reveal secrets.
        </p>
      </Card>

      <Card title="Timezone">
        <div className="flex flex-wrap gap-2">
          <Input value={tz ?? data.timezone} onChange={(e) => setTz(e.target.value)}
                 placeholder="e.g. Europe/Berlin" className="w-full sm:max-w-xs" />
          <Button variant="ghost" onClick={async () => {
            if (tz) {
              await api("/settings", { method: "PATCH", body: { timezone: tz } });
              reload();
            }
          }}>Save</Button>
        </div>
        <p className="mt-2 text-xs text-ink-mute">Schedules (market radar 06:00, strategist 08:00) follow this timezone.</p>
      </Card>

      <AiModeCard />
      <div id="billing" className="scroll-mt-4"><BillingCard /></div>
      <UsageCard />

      <Card title="Data & privacy">
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" onClick={async () => {
            const data = await api<{ documents: unknown[] }>("/documents/export");
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "moseisley-export.json";
            a.click();
          }}>Export all context (Markdown)</Button>
        </div>
        <p className="mt-2 text-xs text-ink-mute">
          You are never locked in. Disconnecting an integration can purge all derived data.
        </p>
      </Card>
    </div>
  );
}
