"use client";
/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, getToken } from "@/lib/api";

/* ── types mirror the public API serializers ── */
export type PublicPerson = {
  handle: string; display_name: string; bio: string; avatar_url: string | null;
  location: string | null; links: Record<string, string>; project_count: number;
  member_since: string; last_active_at: string;
};
export type PublicProject = {
  id: string; name: string; tagline: string; description: string; url: string | null;
  image_url: string | null; category: string; tags: string[]; status: string;
  created_at: string; owner?: { handle: string; display_name: string; avatar_url: string | null };
};
export type PublicUpdate = {
  id: string; text: string; url: string | null; image_url: string | null;
  edited: boolean; created_at: string;
  owner?: { handle: string; display_name: string; avatar_url: string | null };
  project?: { id: string; name: string; url: string | null } | null;
};
export type OwnProfile = {
  handle: string; display_name: string; bio: string; avatar_url: string | null;
  location: string | null; links: Record<string, string>; is_published: boolean;
};
export type Me = {
  profile: OwnProfile | null;
  projects: (PublicProject & { is_public: boolean })[];
  updates: (PublicUpdate & { project_id: string | null; is_public: boolean })[];
  limits: { max_projects: number; max_update_chars: number };
};

export const CATEGORY_LABELS: Record<string, string> = {
  all: "All", saas: "SaaS", ai_agents: "AI Agents", open_source: "Open Source",
  newsletter: "Newsletter", content: "Content", services: "Services",
  community: "Community", dev_tool: "Dev Tools", marketplace: "Marketplace", other: "Other",
};

export function useMe(): { me: Me | null; loggedIn: boolean; reload: () => void } {
  const [me, setMe] = useState<Me | null>(null);
  const loggedIn = typeof window !== "undefined" && !!getToken();
  const reload = useCallback(() => {
    if (!getToken()) return;
    api<Me>("/friends/me").then(setMe).catch(() => {});
  }, []);
  useEffect(() => { reload(); }, [reload]);
  return { me, loggedIn, reload };
}

export function timeAgo(iso: string): string {
  const s = Math.max(1, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d`;
  return new Date(iso).toLocaleDateString();
}

export function domainOf(url: string | null): string | null {
  if (!url) return null;
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return null; }
}

const AVATAR_HUES = ["#a06ef2", "#e14fd0", "#35d8e8", "#4f7df6", "#e8b34d", "#6fe3a1"];

export function Avatar({ name, url, size = 56, className = "" }: {
  name: string; url?: string | null; size?: number; className?: string;
}) {
  const initials = name.split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() || "").join("");
  const hue = AVATAR_HUES[(name.charCodeAt(0) || 0) % AVATAR_HUES.length];
  if (url) {
    return <img src={url} alt="" width={size} height={size} loading="lazy"
                className={`shrink-0 rounded-full border-2 border-[var(--cw-purple)]/60 object-cover ${className}`}
                style={{ width: size, height: size }} />;
  }
  return (
    <span aria-hidden
          className={`flex shrink-0 items-center justify-center rounded-full border-2 border-[var(--cw-purple)]/60 font-display font-bold text-white ${className}`}
          style={{ width: size, height: size, fontSize: size * 0.36,
                   background: `linear-gradient(140deg, ${hue}, #171029)` }}>
      {initials || "☺"}
    </span>
  );
}

export function ExtLink({ href, children, className = "" }: {
  href: string; children: React.ReactNode; className?: string;
}) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow" className={className}>
      {children}
    </a>
  );
}

export function CategoryPill({ category }: { category: string }) {
  return (
    <span className="rounded-full border border-[var(--cw-line)] bg-[var(--cw-deep)]/70 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-widest text-[var(--cw-cyan)]">
      {CATEGORY_LABELS[category] || category}
    </span>
  );
}

/* ── cards ── */

export function PersonCard({ p }: { p: PublicPerson }) {
  return (
    <Link href={`/friends/${p.handle}`}
          className="cw-panel block p-5 text-center transition hover:-translate-y-1 hover:border-[var(--cw-magenta)]/60">
      <Avatar name={p.display_name} url={p.avatar_url} size={72} className="mx-auto" />
      <div className="font-display mt-3 truncate text-lg font-bold">{p.display_name}</div>
      <div className="truncate text-[13px] text-[var(--cw-purple)]">@{p.handle}</div>
      {p.bio && <p className="mt-2 line-clamp-2 text-[13px] leading-relaxed text-[var(--cw-mute)]">{p.bio}</p>}
      <div className="mt-3 text-[11px] uppercase tracking-widest text-[var(--cw-faint)]">
        {p.project_count} project{p.project_count === 1 ? "" : "s"}
      </div>
      <div className="mt-3 rounded-full border border-[var(--cw-line)] px-3 py-1.5 text-[12px] font-semibold text-[var(--cw-ink)]">
        View profile
      </div>
    </Link>
  );
}

export function ProjectCard({ p, showOwner = true }: { p: PublicProject; showOwner?: boolean }) {
  const domain = domainOf(p.url);
  return (
    <div className="cw-panel flex min-w-0 flex-col p-5">
      <div className="flex items-start gap-3">
        {p.image_url ? (
          <img src={p.image_url} alt="" width={44} height={44} loading="lazy"
               className="h-11 w-11 shrink-0 rounded-xl border border-[var(--cw-line)] object-cover" />
        ) : (
          <span aria-hidden className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-[var(--cw-line)] bg-[var(--cw-deep)] font-display text-lg font-bold text-[var(--cw-purple)]">
            {p.name[0]?.toUpperCase()}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <div className="font-display truncate text-base font-bold">{p.name}</div>
          {p.tagline && <p className="mt-0.5 line-clamp-2 text-[13px] text-[var(--cw-mute)]">{p.tagline}</p>}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <CategoryPill category={p.category} />
        {p.tags.slice(0, 3).map((t) => (
          <span key={t} className="rounded-full bg-[var(--cw-raised)] px-2 py-0.5 text-[10px] text-[var(--cw-mute)]">{t}</span>
        ))}
      </div>
      {showOwner && p.owner && (
        <Link href={`/friends/${p.owner.handle}`}
              className="mt-3 flex items-center gap-2 text-[12px] text-[var(--cw-mute)] hover:text-[var(--cw-ink)]">
          <Avatar name={p.owner.display_name} url={p.owner.avatar_url} size={22} />
          by {p.owner.display_name}
        </Link>
      )}
      <div className="mt-auto flex items-center justify-between gap-2 pt-3">
        {p.url && domain ? (
          <ExtLink href={p.url}
                   className="truncate text-[13px] font-semibold text-[var(--cw-cyan)] hover:underline">
            {domain} ↗
          </ExtLink>
        ) : <span />}
      </div>
    </div>
  );
}

export function UpdateCard({ u }: { u: PublicUpdate }) {
  const domain = domainOf(u.url);
  return (
    <div className="cw-panel min-w-0 p-4">
      {u.owner && (
        <Link href={`/friends/${u.owner.handle}`} className="flex items-center gap-2.5">
          <Avatar name={u.owner.display_name} url={u.owner.avatar_url} size={34} />
          <span className="min-w-0">
            <span className="font-display text-sm font-bold">{u.owner.display_name}</span>
            <span className="ml-2 text-[12px] text-[var(--cw-purple)]">@{u.owner.handle}</span>
            <span className="ml-2 text-[11px] text-[var(--cw-faint)]">
              {timeAgo(u.created_at)}{u.edited ? " · edited" : ""}
            </span>
          </span>
        </Link>
      )}
      <p className="mt-2.5 whitespace-pre-wrap break-words text-[14px] leading-relaxed text-[var(--cw-ink)]">
        {u.text}
      </p>
      {u.url && domain && (
        <ExtLink href={u.url}
                 className="mt-2 inline-block rounded-full border border-[var(--cw-cyan)]/40 bg-[var(--cw-cyan)]/5 px-3 py-1 text-[12px] font-semibold text-[var(--cw-cyan)] hover:underline">
          {domain} ↗
        </ExtLink>
      )}
      {u.image_url && (
        <img src={u.image_url} alt="" loading="lazy"
             className="mt-2 max-h-72 w-auto max-w-full rounded-xl border border-[var(--cw-line)]" />
      )}
      {u.project && (
        <div className="mt-2 text-[11px] uppercase tracking-widest text-[var(--cw-faint)]">
          project: <span className="text-[var(--cw-mute)]">{u.project.name}</span>
        </div>
      )}
    </div>
  );
}

/* ── modal shell ── */
export function Modal({ title, onClose, children }: {
  title: string; onClose: () => void; children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 p-0 backdrop-blur-sm sm:items-center sm:p-4"
         role="dialog" aria-modal="true" aria-label={title}>
      <div className="cantina flex max-h-[92dvh] w-full max-w-lg flex-col overflow-hidden rounded-t-2xl border border-[var(--cw-line)] bg-[var(--cw-panel)] sm:rounded-2xl">
        <div className="flex items-center justify-between border-b border-[var(--cw-line)] px-5 py-3.5">
          <span className="font-display text-base font-bold">{title}</span>
          <button onClick={onClose} aria-label="Close"
                  className="flex h-9 w-9 items-center justify-center rounded-full text-[var(--cw-mute)] hover:text-[var(--cw-ink)]">✕</button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
      </div>
    </div>
  );
}

const inputCls = "min-h-[44px] w-full min-w-0 rounded-xl border border-[var(--cw-line)] bg-[var(--cw-deep)]/80 px-3.5 py-2.5 text-sm text-[var(--cw-ink)] placeholder-[var(--cw-faint)] focus:border-[var(--cw-purple)] focus:outline-none";
const btnPrimary = "cw-cta rounded-full px-5 py-2.5 text-sm font-bold text-white disabled:opacity-40";
const btnGhost = "rounded-full border border-[var(--cw-line)] px-5 py-2.5 text-sm font-medium text-[var(--cw-ink)] hover:border-[var(--cw-purple)] disabled:opacity-40";

export function Field({ label, children, hint }: {
  label: string; children: React.ReactNode; hint?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-bold uppercase tracking-widest text-[var(--cw-faint)]">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-[var(--cw-faint)]">{hint}</span>}
    </label>
  );
}

/* ── profile editor (claim your table) ── */
export function ProfileEditor({ me, onDone, onClose }: {
  me: Me | null; onDone: () => void; onClose: () => void;
}) {
  const p = me?.profile;
  const [form, setForm] = useState({
    handle: p?.handle || "", display_name: p?.display_name || "", bio: p?.bio || "",
    avatar_url: p?.avatar_url || "", location: p?.location || "",
    website: p?.links?.website || "", x: p?.links?.x || "", github: p?.links?.github || "",
    linkedin: p?.links?.linkedin || "", youtube: p?.links?.youtube || "",
    newsletter: p?.links?.newsletter || "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [k]: e.target.value });

  async function save(publish: boolean) {
    setBusy(true);
    setError(null);
    try {
      await api("/friends/me", { method: "PUT", body: {
        handle: form.handle, display_name: form.display_name, bio: form.bio,
        avatar_url: form.avatar_url || null, location: form.location || null,
        links: { website: form.website, x: form.x, github: form.github,
                 linkedin: form.linkedin, youtube: form.youtube, newsletter: form.newsletter },
      }});
      if (publish) await api("/friends/me/publish", { body: { published: true } });
      onDone();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not save");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={p ? "Edit your table" : "Claim your table"} onClose={onClose}>
      <div className="space-y-4">
        {!p && (
          <p className="text-[13px] text-[var(--cw-mute)]">
            Create your public profile and show the Cantina what you&apos;re building.
            Nothing is public until you hit publish.
          </p>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Display name *">
            <input className={inputCls} value={form.display_name} onChange={set("display_name")}
                   placeholder="Alice" maxLength={80} />
          </Field>
          <Field label="Handle *" hint="lowercase letters, digits, hyphens — your public URL">
            <input className={inputCls} value={form.handle} onChange={set("handle")}
                   placeholder="alice" maxLength={30} />
          </Field>
        </div>
        <Field label="Bio" hint={`${form.bio.length} / 300`}>
          <textarea className={`${inputCls} resize-none`} rows={3} value={form.bio}
                    onChange={set("bio")} maxLength={300}
                    placeholder="Building tiny AI products." />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Avatar image URL"><input className={inputCls} value={form.avatar_url} onChange={set("avatar_url")} placeholder="https://…" /></Field>
          <Field label="Location (public)"><input className={inputCls} value={form.location} onChange={set("location")} placeholder="Paris" /></Field>
          <Field label="Website"><input className={inputCls} value={form.website} onChange={set("website")} placeholder="https://…" /></Field>
          <Field label="X"><input className={inputCls} value={form.x} onChange={set("x")} placeholder="https://x.com/…" /></Field>
          <Field label="GitHub"><input className={inputCls} value={form.github} onChange={set("github")} placeholder="https://github.com/…" /></Field>
          <Field label="LinkedIn"><input className={inputCls} value={form.linkedin} onChange={set("linkedin")} placeholder="https://linkedin.com/in/…" /></Field>
          <Field label="YouTube"><input className={inputCls} value={form.youtube} onChange={set("youtube")} placeholder="https://youtube.com/…" /></Field>
          <Field label="Newsletter"><input className={inputCls} value={form.newsletter} onChange={set("newsletter")} placeholder="https://…" /></Field>
        </div>
        {error && <p className="break-words text-sm text-[#ff9a9a]">{error}</p>}
        <div className="flex flex-wrap justify-end gap-2 pt-1">
          <button className={btnGhost} disabled={busy} onClick={() => save(false)}>Save draft</button>
          <button className={btnPrimary} disabled={busy || !form.handle || !form.display_name}
                  onClick={() => save(true)}>
            {p?.is_published ? "Save" : "Publish profile"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

/* ── project form ── */
export function ProjectForm({ me, existing, onDone, onClose }: {
  me: Me | null; existing?: (PublicProject & { is_public?: boolean }) | null;
  onDone: () => void; onClose: () => void;
}) {
  const [form, setForm] = useState({
    name: existing?.name || "", tagline: existing?.tagline || "",
    url: existing?.url || "", image_url: existing?.image_url || "",
    category: existing?.category || "saas", tags: (existing?.tags || []).join(", "),
    status: existing?.status || "active",
    description: existing?.description || "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setError(null);
    const body = {
      name: form.name, tagline: form.tagline, url: form.url || null,
      image_url: form.image_url || null, category: form.category,
      tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
      status: form.status, description: form.description,
    };
    try {
      if (existing) await api(`/friends/projects/${existing.id}`, { method: "PATCH", body });
      else await api("/friends/projects", { body });
      onDone();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not save");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={existing ? "Edit project" : "Add your project"} onClose={onClose}>
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Project name *">
            <input className={inputCls} value={form.name} maxLength={120}
                   onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="TinyCRM" />
          </Field>
          <Field label="URL">
            <input className={inputCls} value={form.url}
                   onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://…" />
          </Field>
        </div>
        <Field label="Tagline" hint={`${form.tagline.length} / 160`}>
          <input className={inputCls} value={form.tagline} maxLength={160}
                 onChange={(e) => setForm({ ...form, tagline: e.target.value })}
                 placeholder="AI that answers every call, DM & chat, 24/7." />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Category">
            <select className={inputCls} value={form.category}
                    onChange={(e) => setForm({ ...form, category: e.target.value })}>
              {Object.entries(CATEGORY_LABELS).filter(([k]) => k !== "all").map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </Field>
          <Field label="Status">
            <select className={inputCls} value={form.status}
                    onChange={(e) => setForm({ ...form, status: e.target.value })}>
              {["active", "building", "launched", "paused"].map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </Field>
          <Field label="Tags (comma-separated)">
            <input className={inputCls} value={form.tags}
                   onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="ai, crm" />
          </Field>
          <Field label="Logo image URL">
            <input className={inputCls} value={form.image_url}
                   onChange={(e) => setForm({ ...form, image_url: e.target.value })} placeholder="https://…" />
          </Field>
        </div>
        <Field label="Description (optional)">
          <textarea className={`${inputCls} resize-none`} rows={3} value={form.description}
                    onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </Field>
        {me && !existing && (
          <p className="text-[11px] text-[var(--cw-faint)]">
            {me.projects.length} / {me.limits.max_projects} projects used
          </p>
        )}
        {error && <p className="break-words text-sm text-[#ff9a9a]">{error}</p>}
        <div className="flex justify-end gap-2">
          <button className={btnGhost} onClick={onClose}>Cancel</button>
          <button className={btnPrimary} disabled={busy || !form.name} onClick={save}>
            {existing ? "Save changes" : "Add project"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

/* ── update composer ── */
export function UpdateComposer({ me, onDone, onClose }: {
  me: Me; onDone: () => void; onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [projectId, setProjectId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const max = me.limits.max_update_chars;

  async function publish() {
    setBusy(true);
    setError(null);
    try {
      await api("/friends/updates", { body: {
        text, url: url || null, project_id: projectId || null } });
      onDone();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not publish");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="What are you building?" onClose={onClose}>
      <div className="space-y-4">
        <div>
          <textarea className={`${inputCls} resize-none`} rows={4} value={text} maxLength={max}
                    onChange={(e) => setText(e.target.value)}
                    placeholder="Just shipped…" autoFocus />
          <div className={`mt-1 text-right text-[11px] ${text.length > max - 40 ? "text-[var(--cw-magenta)]" : "text-[var(--cw-faint)]"}`}>
            {text.length} / {max}
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Add URL (optional)">
            <input className={inputCls} value={url} onChange={(e) => setUrl(e.target.value)}
                   placeholder="https://…" />
          </Field>
          <Field label="Attach to project (optional)">
            <select className={inputCls} value={projectId}
                    onChange={(e) => setProjectId(e.target.value)}>
              <option value="">none</option>
              {me.projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
        </div>
        {error && <p className="break-words text-sm text-[#ff9a9a]">{error}</p>}
        <div className="flex justify-end gap-2">
          <button className={btnGhost} onClick={onClose}>Cancel</button>
          <button className={btnPrimary} disabled={busy || !text.trim()} onClick={publish}>
            Publish update
          </button>
        </div>
      </div>
    </Modal>
  );
}
