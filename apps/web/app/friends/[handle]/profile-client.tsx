"use client";
/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import {
  Avatar, ExtLink, ProfileEditor, ProjectCard, ProjectForm, PublicPerson,
  PublicProject, PublicUpdate, UpdateCard, UpdateComposer, domainOf, useMe,
} from "@/components/friends-ui";

type ProfilePage = PublicPerson & { projects: PublicProject[]; updates: PublicUpdate[] };

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const LINK_LABELS: Record<string, string> = {
  website: "Website", x: "X", github: "GitHub", linkedin: "LinkedIn",
  youtube: "YouTube", newsletter: "Newsletter", other: "Link",
};

export default function ProfileClient({ handle }: { handle: string }) {
  const { me, loggedIn, reload } = useMe();
  const [page, setPage] = useState<ProfilePage | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [modal, setModal] = useState<"profile" | "project" | "update" | null>(null);
  const [editProject, setEditProject] = useState<PublicProject | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = await fetch(`${API}/api/public/friends/${encodeURIComponent(handle)}`,
                               { cache: "no-store" });
      if (!resp.ok) { setNotFound(true); return; }
      setPage(await resp.json());
      setNotFound(false);
    } catch { setNotFound(true); }
  }, [handle]);
  useEffect(() => { load(); }, [load]);

  const isOwner = loggedIn && me?.profile?.handle === handle.toLowerCase();

  async function deleteUpdate(id: string) {
    if (!window.confirm("Delete this update?")) return;
    await api(`/friends/updates/${id}`, { method: "DELETE" });
    load();
    reload();
  }

  async function deleteProject(id: string) {
    if (!window.confirm("Delete this project? This cannot be undone.")) return;
    await api(`/friends/projects/${id}`, { method: "DELETE" });
    load();
    reload();
  }

  return (
    <div className="cantina min-h-screen overflow-x-hidden">
      <header className="relative z-20 mx-auto flex max-w-5xl items-center justify-between gap-3 px-4 py-4 sm:px-6">
        <Link href="/friends" className="flex items-center gap-2.5 text-sm text-[var(--cw-mute)] hover:text-[var(--cw-ink)]">
          ← Friends of the Cantina
        </Link>
        <Link href="/" className="flex items-center gap-2">
          <img src="/brand/logo-mark.webp" alt="Moseisley" width={36} height={36}
               className="rounded-full border-2 border-[var(--cw-purple)]" />
        </Link>
      </header>

      {notFound ? (
        <div className="mx-auto max-w-xl px-4 py-24 text-center">
          <p className="font-display text-2xl font-bold">No such table in the cantina.</p>
          <p className="mt-2 text-sm text-[var(--cw-mute)]">
            This builder may have moved on, gone private, or never existed.
          </p>
          <Link href="/friends" className="cw-cta mt-6 inline-block rounded-full px-6 py-3 text-sm font-bold text-white">
            Back to the directory
          </Link>
        </div>
      ) : !page ? (
        <p className="py-24 text-center text-sm text-[var(--cw-faint)]">finding their table…</p>
      ) : (
        <main className="relative mx-auto max-w-5xl space-y-12 px-4 pb-20 sm:px-6">
          <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-72 overflow-hidden"><div className="cw-stars" /></div>

          {/* header */}
          <section className="cw-panel cw-panel-magenta relative p-6 sm:p-8">
            <div className="flex flex-col items-center gap-5 text-center sm:flex-row sm:items-start sm:text-left">
              <Avatar name={page.display_name} url={page.avatar_url} size={104} />
              <div className="min-w-0 flex-1">
                <h1 className="font-display text-3xl font-bold">{page.display_name}</h1>
                <div className="mt-0.5 text-[15px] text-[var(--cw-purple)]">@{page.handle}</div>
                {page.bio && <p className="mt-3 max-w-xl text-[14.5px] leading-relaxed text-[var(--cw-mute)]">{page.bio}</p>}
                <div className="mt-3 flex flex-wrap items-center justify-center gap-x-4 gap-y-1 text-[12px] text-[var(--cw-faint)] sm:justify-start">
                  {page.location && <span>📍 {page.location}</span>}
                  <span>member since {new Date(page.member_since).toLocaleDateString(undefined, { month: "short", year: "numeric" })}</span>
                  <span>{page.project_count} project{page.project_count === 1 ? "" : "s"}</span>
                </div>
                {Object.keys(page.links || {}).length > 0 && (
                  <div className="mt-4 flex flex-wrap justify-center gap-2 sm:justify-start">
                    {Object.entries(page.links).map(([key, url]) => (
                      <ExtLink key={key} href={url}
                               className="rounded-full border border-[var(--cw-line)] bg-[var(--cw-deep)]/70 px-3.5 py-1.5 text-[12px] font-semibold text-[var(--cw-cyan)] hover:border-[var(--cw-cyan)]">
                        {LINK_LABELS[key] || key} ↗
                      </ExtLink>
                    ))}
                  </div>
                )}
              </div>
            </div>
            {isOwner && (
              <div className="mt-5 flex flex-wrap justify-center gap-2 border-t border-[var(--cw-line)] pt-4 sm:justify-start">
                <button onClick={() => setModal("profile")}
                        className="rounded-full border border-[var(--cw-line)] px-4 py-2 text-[13px] font-semibold hover:border-[var(--cw-purple)]">
                  Edit profile
                </button>
                <button onClick={() => setModal("project")}
                        className="rounded-full border border-[var(--cw-line)] px-4 py-2 text-[13px] font-semibold hover:border-[var(--cw-purple)]">
                  Add project
                </button>
                <button onClick={() => setModal("update")}
                        className="cw-cta rounded-full px-4 py-2 text-[13px] font-bold text-white">
                  Post update
                </button>
              </div>
            )}
          </section>

          {/* projects */}
          <section>
            <h2 className="font-display text-2xl font-bold">Projects</h2>
            {page.projects.length === 0 ? (
              <div className="cw-panel mt-5 p-8 text-center">
                <p className="text-sm text-[var(--cw-mute)]">This builder hasn&apos;t shared a project yet.</p>
              </div>
            ) : (
              <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {page.projects.map((p) => (
                  <div key={p.id} className="relative">
                    <ProjectCard p={p} showOwner={false} />
                    {isOwner && (
                      <div className="mt-1.5 flex gap-3 text-[11px]">
                        <button onClick={() => setEditProject(p)}
                                className="text-[var(--cw-purple)] hover:underline">edit</button>
                        <button onClick={() => deleteProject(p.id)}
                                className="text-[#ff9a9a] hover:underline">delete</button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* updates */}
          <section>
            <h2 className="font-display text-2xl font-bold">Latest updates</h2>
            {page.updates.length === 0 ? (
              <div className="cw-panel mt-5 p-8 text-center">
                <p className="text-sm text-[var(--cw-mute)]">Nothing shipped… yet.</p>
              </div>
            ) : (
              <div className="mx-auto mt-5 grid max-w-3xl gap-4">
                {page.updates.map((u) => (
                  <div key={u.id}>
                    <UpdateCard u={{ ...u, owner: { handle: page.handle,
                      display_name: page.display_name, avatar_url: page.avatar_url } }} />
                    {isOwner && (
                      <div className="mt-1 flex gap-3 text-[11px]">
                        <button onClick={() => deleteUpdate(u.id)}
                                className="text-[#ff9a9a] hover:underline">delete</button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>
        </main>
      )}

      {modal === "profile" && (
        <ProfileEditor me={me} onClose={() => setModal(null)}
                       onDone={() => { reload(); load(); }} />
      )}
      {(modal === "project" || editProject) && (
        <ProjectForm me={me} existing={editProject}
                     onClose={() => { setModal(null); setEditProject(null); }}
                     onDone={() => { reload(); load(); }} />
      )}
      {modal === "update" && me && (
        <UpdateComposer me={me} onClose={() => setModal(null)}
                        onDone={() => { reload(); load(); }} />
      )}
    </div>
  );
}
