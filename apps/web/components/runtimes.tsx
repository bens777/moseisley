"use client";
import { Pill } from "@/components/ui";

/* Runtime cards. The catalog comes from GET /api/agents/runtimes — the same
   backend constant the API derives CREATABLE_TYPES from — so a card can never
   offer something the API would refuse. Nothing here is written in the client:
   every bullet is server content, rendered as-is. */

export type Runtime = {
  id: string;
  name: string;
  status: "available" | "blocked";
  blocked_reason: string | null;
  summary: string;
  best_for: string[];
  security: string[];
  weak_points: string[];
};

const SECTIONS: { key: keyof Pick<Runtime, "best_for" | "security" | "weak_points">;
                  label: string; tone: string; bullet: string }[] = [
  { key: "best_for", label: "Best for", tone: "text-ink-mute", bullet: "text-ok" },
  { key: "security", label: "Security", tone: "text-ink-mute", bullet: "text-signal" },
  { key: "weak_points", label: "Weak points", tone: "text-ink-mute", bullet: "text-warn" },
];

function Profile({ runtime }: { runtime: Runtime }) {
  return (
    <div className="mt-3 space-y-3 border-t border-line pt-3">
      {SECTIONS.map((s) => (
        <div key={s.key}>
          <div className="font-mono text-[10px] font-bold uppercase tracking-widest text-ink-faint">
            {s.label}
          </div>
          <ul className="mt-1 space-y-1">
            {runtime[s.key].map((line, i) => (
              <li key={i} className={`flex gap-2 text-xs leading-relaxed ${s.tone}`}>
                <span aria-hidden className={`${s.bullet} shrink-0`}>·</span>
                <span className="min-w-0">{line}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/* Selectable card for the wizard. A blocked runtime is shown, greyed, with its
   reason — hiding it would leave the user wondering whether they missed it. */
export function RuntimeCard({ runtime, selected, onSelect }: {
  runtime: Runtime; selected?: boolean; onSelect?: (id: string) => void;
}) {
  const blocked = runtime.status !== "available";
  const selectable = !blocked && !!onSelect;

  return (
    <div
      role={selectable ? "button" : undefined}
      tabIndex={selectable ? 0 : undefined}
      aria-pressed={selectable ? !!selected : undefined}
      aria-disabled={blocked || undefined}
      onClick={selectable ? () => onSelect(runtime.id) : undefined}
      onKeyDown={selectable ? (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(runtime.id); }
      } : undefined}
      className={`rounded-md border p-3 text-left transition ${
        blocked
          ? "border-line bg-ground/20 opacity-60"
          : selected
            ? "border-brand bg-brand/10"
            : `border-line ${selectable ? "cursor-pointer hover:border-brand/50" : ""}`
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-ink">{runtime.name}</span>
        {blocked ? (
          <Pill color="gray">{runtime.blocked_reason || "unavailable"}</Pill>
        ) : (
          selected && <Pill color="green">selected</Pill>
        )}
      </div>
      <p className="mt-0.5 text-xs text-ink-mute">{runtime.summary}</p>
      <Profile runtime={runtime} />
    </div>
  );
}

/* Read-only reference: the same catalog, nothing selectable. */
export function RuntimeReference({ runtimes }: { runtimes: Runtime[] }) {
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {runtimes.map((r) => <RuntimeCard key={r.id} runtime={r} />)}
    </div>
  );
}
