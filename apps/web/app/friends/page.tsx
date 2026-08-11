"use client";
/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { getToken } from "@/lib/api";
import {
  CATEGORY_LABELS, PersonCard, ProfileEditor, ProjectCard, ProjectForm,
  PublicPerson, PublicProject, PublicUpdate, UpdateCard, UpdateComposer, useMe,
} from "@/components/friends-ui";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function pub<T>(path: string, params: Record<string, string | number> = {}): Promise<T> {
  const qs = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]));
  const resp = await fetch(`${API}/api/public/friends${path}?${qs}`, { cache: "no-store" });
  if (!resp.ok) throw new Error("failed to load");
  return resp.json();
}

const PAGE = 12;

export default function FriendsPage() {
  const { me, loggedIn, reload } = useMe();
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [category, setCategory] = useState("all");
  const [people, setPeople] = useState<PublicPerson[]>([]);
  const [projects, setProjects] = useState<PublicProject[]>([]);
  const [updates, setUpdates] = useState<PublicUpdate[]>([]);
  const [projectOffset, setProjectOffset] = useState(0);
  const [moreProjects, setMoreProjects] = useState(false);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<"profile" | "project" | "update" | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 350);
    return () => clearTimeout(t);
  }, [q]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ppl, projs, upds] = await Promise.all([
        pub<PublicPerson[]>("", { q: debouncedQ, limit: PAGE }),
        pub<PublicProject[]>("/projects", { q: debouncedQ, category, limit: PAGE + 1 }),
        pub<PublicUpdate[]>("/updates", { limit: PAGE }),
      ]);
      setPeople(ppl);
      setMoreProjects(projs.length > PAGE);
      setProjects(projs.slice(0, PAGE));
      setProjectOffset(PAGE);
      setUpdates(upds);
    } catch { /* directory best-effort */ }
    setLoading(false);
  }, [debouncedQ, category]);

  useEffect(() => { load(); }, [load]);

  async function loadMoreProjects() {
    const next = await pub<PublicProject[]>("/projects",
      { q: debouncedQ, category, limit: PAGE + 1, offset: projectOffset });
    setMoreProjects(next.length > PAGE);
    setProjects([...projects, ...next.slice(0, PAGE)]);
    setProjectOffset(projectOffset + PAGE);
  }

  const hasProfile = !!me?.profile;
  const published = !!me?.profile?.is_published;

  function ctaLabel(): { label: string; action: () => void } {
    if (!loggedIn) return { label: "Join the Cantina", action: () => { window.location.href = "/login"; } };
    if (!hasProfile) return { label: "Claim your table", action: () => setModal("profile") };
    return { label: "Add your project", action: () => setModal("project") };
  }
  const cta = ctaLabel();

  return (
    <div className="cantina min-h-screen overflow-x-hidden">
      {/* nav */}
      <header className="relative z-20 mx-auto flex max-w-7xl items-center justify-between gap-3 px-4 py-4 sm:px-6">
        <Link href="/" className="flex min-w-0 items-center gap-2.5">
          <img src="/brand/logo-mark.webp" alt="Moseisley" width={44} height={44}
               className="rounded-full border-2 border-[var(--cw-purple)]" />
          <span className="font-display truncate text-lg font-bold tracking-tight">
            moseisley<span className="text-[var(--cw-magenta)]">.sh</span>
          </span>
        </Link>
        <nav className="flex items-center gap-1 sm:gap-2">
          <Link href="/#crew" className="hidden rounded-full px-3 py-2 text-sm text-[var(--cw-mute)] hover:text-[var(--cw-ink)] sm:block">The Crew</Link>
          <Link href="/#missions" className="hidden rounded-full px-3 py-2 text-sm text-[var(--cw-mute)] hover:text-[var(--cw-ink)] sm:block">How it works</Link>
          <span className="hidden rounded-full px-3 py-2 text-sm font-semibold text-[var(--cw-ink)] sm:block">Friends</span>
          <Link href="/pricing" className="hidden rounded-full px-3 py-2 text-sm text-[var(--cw-mute)] hover:text-[var(--cw-ink)] sm:block">Pricing</Link>
          <Link href={loggedIn ? "/command" : "/login"}
                className="rounded-full border border-[var(--cw-line)] bg-[var(--cw-panel)] px-4 py-2 text-sm text-[var(--cw-ink)] hover:border-[var(--cw-purple)]">
            {loggedIn ? "Command center" : "Log in"}
          </Link>
        </nav>
      </header>

      {/* hero */}
      <section className="relative overflow-hidden">
        <div aria-hidden className="pointer-events-none absolute inset-0"><div className="cw-stars" /></div>
        <div className="relative mx-auto max-w-4xl px-4 pb-10 pt-8 text-center sm:px-6">
          <p className="cw-neon-magenta font-mono text-[11px] uppercase tracking-[0.3em]">
            ▸ community ◂
          </p>
          <h1 className="font-display mt-3 text-4xl font-bold tracking-tight sm:text-5xl">
            Friends of <span className="cw-title">the Cantina</span>.
          </h1>
          <p className="mx-auto mt-4 max-w-lg text-[15px] text-[var(--cw-mute)]">
            Meet the builders, founders and weird creatures building with AI.
          </p>
          <div className="mx-auto mt-6 flex max-w-xl flex-col gap-3 sm:flex-row">
            <input value={q} onChange={(e) => setQ(e.target.value)}
                   placeholder="Search people, projects or topics…"
                   aria-label="Search the directory"
                   className="min-h-[48px] w-full min-w-0 flex-1 rounded-full border border-[var(--cw-line)] bg-[var(--cw-deep)]/80 px-5 text-sm text-[var(--cw-ink)] placeholder-[var(--cw-faint)] focus:border-[var(--cw-purple)] focus:outline-none" />
            <button onClick={cta.action}
                    className="cw-cta shrink-0 rounded-full px-6 py-3 text-sm font-bold uppercase tracking-wide text-white">
              {cta.label}
            </button>
          </div>
          {loggedIn && hasProfile && (
            <div className="mt-4 flex flex-wrap items-center justify-center gap-2 text-[12px]">
              <span className={published ? "text-[#6fe3a1]" : "text-[var(--cw-gold)]"}>
                {published ? `● your table is live — /friends/${me?.profile?.handle}` : "○ profile saved but not published yet"}
              </span>
              <button onClick={() => setModal("profile")} className="text-[var(--cw-purple)] hover:underline">edit profile</button>
              <button onClick={() => setModal("update")} className="text-[var(--cw-purple)] hover:underline">post update</button>
              {hasProfile && published && (
                <Link href={`/friends/${me?.profile?.handle}`} className="text-[var(--cw-purple)] hover:underline">view my table</Link>
              )}
            </div>
          )}
        </div>
      </section>

      <main className="mx-auto max-w-7xl space-y-16 px-4 pb-20 sm:px-6">
        {/* people */}
        <section>
          <h2 className="font-display text-2xl font-bold">Discover <span className="cw-title">people</span></h2>
          {loading && people.length === 0 ? (
            <p className="mt-6 text-sm text-[var(--cw-faint)]">scanning the room…</p>
          ) : people.length === 0 ? (
            <div className="cw-panel mt-6 p-10 text-center">
              <p className="font-display text-lg font-bold">No regulars at this table yet.</p>
              <p className="mt-1 text-sm text-[var(--cw-mute)]">Be the first to claim a table and show what you&apos;re building.</p>
            </div>
          ) : (
            <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {people.map((p) => <PersonCard key={p.handle} p={p} />)}
            </div>
          )}
        </section>

        {/* projects */}
        <section>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="font-display text-2xl font-bold">What they&apos;re <span className="cw-title">building</span></h2>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(CATEGORY_LABELS).map(([k, v]) => (
                <button key={k} onClick={() => setCategory(k)}
                        className={`min-h-[34px] rounded-full px-3 text-[12px] font-semibold ${
                          category === k
                            ? "bg-[var(--cw-purple-deep)] text-white"
                            : "border border-[var(--cw-line)] text-[var(--cw-mute)] hover:text-[var(--cw-ink)]"}`}>
                  {v}
                </button>
              ))}
            </div>
          </div>
          {projects.length === 0 ? (
            <div className="cw-panel mt-6 p-10 text-center">
              <p className="font-display text-lg font-bold">Nothing on the mission board here yet.</p>
              <p className="mt-1 text-sm text-[var(--cw-mute)]">Add your project and let the Cantina discover it.</p>
            </div>
          ) : (
            <>
              <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {projects.map((p) => <ProjectCard key={p.id} p={p} />)}
              </div>
              {moreProjects && (
                <div className="mt-6 text-center">
                  <button onClick={loadMoreProjects}
                          className="rounded-full border border-[var(--cw-line)] px-6 py-2.5 text-sm text-[var(--cw-ink)] hover:border-[var(--cw-purple)]">
                    Load more projects
                  </button>
                </div>
              )}
            </>
          )}
        </section>

        {/* updates */}
        <section>
          <h2 className="font-display text-2xl font-bold">From <span className="cw-title">the Cantina</span></h2>
          {updates.length === 0 ? (
            <div className="cw-panel mt-6 p-10 text-center">
              <p className="font-display text-lg font-bold">All quiet at the bar.</p>
              <p className="mt-1 text-sm text-[var(--cw-mute)]">Members&apos; shipping updates will appear here.</p>
            </div>
          ) : (
            <div className="mx-auto mt-6 grid max-w-3xl gap-4">
              {updates.map((u) => <UpdateCard key={u.id} u={u} />)}
            </div>
          )}
        </section>
      </main>

      <footer className="border-t border-[var(--cw-line)] py-8">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 text-[12px] text-[var(--cw-faint)] sm:px-6">
          <span>moseisley.sh — the cantina of AI agents</span>
          <div className="flex flex-wrap gap-4">
            <Link href="/pricing" className="hover:text-[var(--cw-mute)]">Pricing</Link>
            <Link href="/legal" className="hover:text-[var(--cw-mute)]">Legal</Link>
            <a href="mailto:cantina@moseisley.sh" className="hover:text-[var(--cw-mute)]">cantina@moseisley.sh</a>
          </div>
        </div>
      </footer>

      {modal === "profile" && (
        <ProfileEditor me={me} onClose={() => setModal(null)}
                       onDone={() => { reload(); load(); }} />
      )}
      {modal === "project" && (
        <ProjectForm me={me} onClose={() => setModal(null)}
                     onDone={() => { reload(); load(); }} />
      )}
      {modal === "update" && me && (
        <UpdateComposer me={me} onClose={() => setModal(null)}
                        onDone={() => { reload(); load(); }} />
      )}
    </div>
  );
}
