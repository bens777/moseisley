"use client";
import { ReactNode } from "react";

/* ── Panels ─────────────────────────────────────────────────────────── */

export function Card({
  title, children, action, tone = "default", className = "",
}: {
  title?: string; children: ReactNode; action?: ReactNode;
  tone?: "default" | "attention"; className?: string;
}) {
  const tones = {
    default: "border-line bg-panel/80",
    attention: "border-warn/50 bg-warn/[0.06]",
  };
  return (
    <div className={`min-w-0 rounded-md border p-4 ${tones[tone]} ${className}`}>
      {(title || action) && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          {title && <SectionLabel>{title}</SectionLabel>}
          {action}
        </div>
      )}
      {children}
    </div>
  );
}

export function SectionLabel({ children, tone = "mute" }: { children: ReactNode; tone?: "mute" | "brand" }) {
  return (
    <h2 className={`font-mono text-[11px] font-semibold uppercase tracking-widest ${
      tone === "brand" ? "text-brand" : "text-ink-mute"
    }`}>
      {children}
    </h2>
  );
}

export function SectionHeader({ title, sub, action }: { title: string; sub?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-bold">{title}</h1>
        {sub && <p className="mt-0.5 font-mono text-[11px] uppercase tracking-widest text-ink-faint">{sub}</p>}
      </div>
      {action && <div className="flex flex-wrap gap-2">{action}</div>}
    </div>
  );
}

/* ── Data display ───────────────────────────────────────────────────── */

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="min-w-0">
      <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">{label}</div>
      <div className="mt-0.5 truncate font-mono text-xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-[11px] text-ink-faint">{hint}</div>}
    </div>
  );
}

export function Progress({ value, tone = "brand" }: { value: number; tone?: "brand" | "signal" | "ok" }) {
  const pct = Math.max(0, Math.min(100, Math.round(value * 100)));
  const bar = { brand: "bg-brand", signal: "bg-signal", ok: "bg-ok" }[tone];
  return (
    <div className="flex min-w-0 items-center gap-2">
      <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-sm bg-raised" role="progressbar"
           aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className={`h-full rounded-sm ${bar} transition-[width] duration-500`} style={{ width: `${pct}%` }} />
      </div>
      <span className="shrink-0 font-mono text-[11px] tabular-nums text-ink-mute">{pct}%</span>
    </div>
  );
}

const pillColors: Record<string, { box: string; led: string }> = {
  green: { box: "bg-ok/10 text-ok border-ok/30", led: "bg-ok" },
  red: { box: "bg-crit/10 text-crit border-crit/30", led: "bg-crit" },
  yellow: { box: "bg-warn/10 text-warn border-warn/30", led: "bg-warn" },
  gray: { box: "bg-raised text-ink-mute border-line-strong", led: "bg-ink-faint" },
  blue: { box: "bg-signal/10 text-signal border-signal/30", led: "bg-signal" },
};

export function Pill({ color = "gray", pulse = false, children }: {
  color?: string; pulse?: boolean; children: ReactNode;
}) {
  const c = pillColors[color] || pillColors.gray;
  return (
    <span className={`inline-flex max-w-full items-center gap-1.5 rounded-sm border px-2 py-0.5 font-mono text-[11px] font-medium uppercase tracking-wide ${c.box}`}>
      <span className={`led ${c.led} ${pulse ? "led-pulse" : ""}`} aria-hidden />
      <span className="truncate">{children}</span>
    </span>
  );
}

/* ── Controls ───────────────────────────────────────────────────────── */

export function Button({
  children, onClick, variant = "primary", disabled, type, className = "",
}: {
  children: ReactNode; onClick?: () => void;
  variant?: "primary" | "ghost" | "danger"; disabled?: boolean;
  type?: "button" | "submit"; className?: string;
}) {
  const styles = {
    primary: "bg-brand text-ground hover:bg-brand-soft",
    ghost: "border border-line-strong text-ink-mute hover:bg-raised hover:text-ink",
    danger: "bg-crit/20 text-crit border border-crit/40 hover:bg-crit/30",
  };
  return (
    <button
      type={type || "button"}
      onClick={onClick}
      disabled={disabled}
      className={`min-h-[38px] rounded-md px-3 py-1.5 text-sm font-medium transition disabled:opacity-40 ${styles[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

/* React 19 passes `ref` straight through to function components, so the
   composer can focus this and put the caret after a voice transcript. */
export function Input(props: React.InputHTMLAttributes<HTMLInputElement>
                             & { ref?: React.Ref<HTMLInputElement> }) {
  return (
    <input
      {...props}
      className={`min-h-[42px] w-full min-w-0 rounded-md border border-line-strong bg-panel px-3 py-2 text-sm text-ink placeholder-ink-faint focus:border-brand/60 focus:outline-none ${props.className || ""}`}
    />
  );
}

/* ── States ─────────────────────────────────────────────────────────── */

export function Loading() {
  return (
    <div className="flex items-center justify-center gap-2 p-10 font-mono text-[11px] uppercase tracking-widest text-ink-faint">
      <span className="led led-pulse bg-signal" aria-hidden />
      loading…
    </div>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="break-words rounded-md border border-crit/40 bg-crit/10 p-3 text-sm text-crit">
      {message}
    </div>
  );
}

export function Empty({ message }: { message: string }) {
  return <div className="p-6 text-center text-sm text-ink-faint">{message}</div>;
}

export function EmptyState({
  label, title, body, action, bullets,
}: {
  label: string; title: string; body?: string; action?: ReactNode; bullets?: string[];
}) {
  return (
    <div className="rounded-md border border-dashed border-line-strong bg-panel/40 p-6 text-center">
      <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">{label}</div>
      <div className="font-display mt-2 text-base font-semibold text-ink">{title}</div>
      {body && <p className="mx-auto mt-1.5 max-w-sm text-sm text-ink-mute">{body}</p>}
      {bullets && (
        <ul className="mx-auto mt-2 inline-block text-left text-sm text-ink-mute">
          {bullets.map((b) => (
            <li key={b} className="flex items-center gap-2">
              <span className="text-brand">·</span>{b}
            </li>
          ))}
        </ul>
      )}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}

/* ── Crew insignia ──────────────────────────────────────────────────── */

export const AGENT_GLYPHS: Record<string, string> = {
  native: "△",
  custom_http: "◇",
  openclaw: "◆",
  strategist: "△",
  radar: "◎",
  xray: "⊘",
  treasury: "▣",
  crew: "⬡",
};

export function Insignia({ kind, size = "md" }: { kind: string; size?: "sm" | "md" | "lg" }) {
  const sizes = { sm: "h-7 w-7 text-sm", md: "h-9 w-9 text-base", lg: "h-11 w-11 text-lg" };
  return (
    <span aria-hidden
      className={`flex shrink-0 items-center justify-center rounded-sm border border-line-strong bg-raised font-mono text-brand ${sizes[size]}`}>
      {AGENT_GLYPHS[kind] || "○"}
    </span>
  );
}
