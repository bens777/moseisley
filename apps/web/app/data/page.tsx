"use client";
import Link from "next/link";
import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Button, Card, EmptyState, ErrorBox, Input, Loading, Pill } from "@/components/ui";

/* MY DATA — the truth about what the user has handed the crew: what is
   connected, and what they pasted or uploaded. Knowledge is stored as ordinary
   documents under /knowledge/, the same path the rest of the platform reads. */

type Doc = { id: string; path: string; content_md: string; created_at: string; updated_at: string };
type Connection = {
  id: string; integration_type: string; name: string; status: string;
  capabilities: Record<string, string>; last_health_ok: boolean | null;
};

const KNOWLEDGE_PREFIX = "/knowledge/";
const ACCEPT = ".md,.txt,.json";
const MAX_CHARS = 200_000;

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  });
}

function slugify(name: string): string {
  const base = name.replace(/\.[^.]+$/, "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return base || "note";
}

function AddKnowledge({ onAdded }: { onAdded: () => void }) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  async function store(name: string, content: string) {
    if (content.length > MAX_CHARS) {
      setError(`That is larger than ${MAX_CHARS.toLocaleString()} characters — split it up.`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api("/documents", {
        method: "PUT",
        body: {
          path: `${KNOWLEDGE_PREFIX}${slugify(name)}.md`,
          content_md: content,
          metadata: { title: name.trim() || "Untitled note", source: "my-data" },
        },
      });
      setTitle("");
      setText("");
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not save that");
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File) {
    const content = await file.text();
    await store(file.name, content);
    if (fileInput.current) fileInput.current.value = "";
  }

  return (
    <Card title="Add knowledge"
          action={
            <>
              <input ref={fileInput} type="file" accept={ACCEPT} className="hidden"
                     onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }} />
              <Button variant="ghost" onClick={() => fileInput.current?.click()} disabled={busy}>
                Upload .md / .txt / .json
              </Button>
            </>
          }>
      <p className="mb-3 text-sm text-ink-mute">
        Anything your crew should know: how you work, your offer, your clients, your
        constraints. Paste it here and they can read it.
      </p>
      <div className="space-y-2">
        <Input placeholder="Name it — “Pricing rules”, “About my business”…"
               value={title} onChange={(e) => setTitle(e.target.value)} />
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={6}
          placeholder="Paste the text…"
          className="w-full min-w-0 resize-y rounded-md border border-line-strong bg-ground/60 px-3 py-2 text-sm text-ink placeholder-ink-faint focus:border-brand/60 focus:outline-none"
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => store(title, text)} disabled={busy || !text.trim()}>
            {busy ? "Saving…" : "Save to my crew's knowledge"}
          </Button>
          <span className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
            {text.length.toLocaleString()} characters
          </span>
        </div>
        {error && <ErrorBox message={error} />}
      </div>
    </Card>
  );
}

function KnowledgeList({ docs, onChange }: { docs: Doc[]; onChange: () => void }) {
  const [confirming, setConfirming] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function remove(id: string) {
    setError(null);
    try {
      await api(`/documents/${id}`, { method: "DELETE" });
      setConfirming(null);
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not delete that");
    }
  }

  if (docs.length === 0) {
    return (
      <EmptyState
        label="no knowledge yet"
        title="Your crew knows nothing about you yet"
        body="Paste or upload anything they should know — it stays yours, and you can delete it at any time."
      />
    );
  }

  return (
    <div className="space-y-2">
      {docs.map((d) => {
        const name = String((d as Doc & { metadata?: { title?: string } }).metadata?.title
          || d.path.split("/").pop());
        return (
          <div key={d.id}
               className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 rounded-md border border-line bg-ground/40 p-3">
            <span aria-hidden className="font-mono text-xs text-ink-faint">▤</span>
            <div className="min-w-0 flex-1">
              <div className="min-w-0 break-words text-sm font-medium text-ink">{name}</div>
              <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
                added {fmtDate(d.created_at)} · {d.content_md.length.toLocaleString()} characters
              </div>
            </div>
            {confirming === d.id ? (
              <div className="flex items-center gap-2">
                <span className="text-xs text-ink-mute">Delete for good?</span>
                <Button variant="danger" onClick={() => remove(d.id)}>Delete</Button>
                <Button variant="ghost" onClick={() => setConfirming(null)}>Cancel</Button>
              </div>
            ) : (
              <Button variant="ghost" onClick={() => setConfirming(d.id)}>Delete</Button>
            )}
          </div>
        );
      })}
      {error && <ErrorBox message={error} />}
    </div>
  );
}

export default function MyDataPage() {
  const docs = useApi<Doc[]>("/documents");
  const connections = useApi<Connection[]>("/integrations");

  if (docs.loading) return <Loading />;

  const knowledge = (docs.data || []).filter((d) => d.path.startsWith(KNOWLEDGE_PREFIX));
  const context = (docs.data || []).filter((d) => d.path.startsWith("/context/"));
  const conns = connections.data || [];

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div>
        <h1 className="text-xl font-bold">My data</h1>
        <p className="mt-0.5 font-mono text-[11px] uppercase tracking-widest text-ink-faint">
          everything you have given your crew
        </p>
      </div>

      <Card title="Connected sources"
            action={<Link href="/connections" className="text-xs text-ink-faint hover:text-ink-mute">
              manage connections →
            </Link>}>
        {conns.length === 0 ? (
          <EmptyState
            label="nothing connected"
            title="No data sources connected"
            body="Connect Google for mail and calendar, an MCP server or a webhook. Your crew only ever reads what you connect — there is no sample data."
            action={<Link href="/connections"><Button>Connect a source</Button></Link>}
          />
        ) : (
          <div className="space-y-2">
            {conns.map((c) => (
              <div key={c.id} className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
                <span className="min-w-0 truncate text-sm font-medium">{c.name}</span>
                <span className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
                  {c.integration_type}
                </span>
                <span className="font-mono text-[10px] uppercase tracking-wide text-ink-faint">
                  {Object.keys(c.capabilities || {}).join(" · ") || "no capabilities granted"}
                </span>
                <Pill color={c.status === "connected" ? "green" : c.status === "error" ? "red" : "gray"}>
                  {c.status}
                </Pill>
              </div>
            ))}
          </div>
        )}
        <p className="mt-3 border-t border-line pt-3 text-xs text-ink-faint">
          There is no Google Drive connector — upload the files you want your crew to
          read below, or connect S3-compatible storage from Connections.
        </p>
      </Card>

      <AddKnowledge onAdded={docs.reload} />

      <Card title="Your crew's knowledge">
        <KnowledgeList docs={knowledge} onChange={docs.reload} />
      </Card>

      {context.length > 0 && (
        <Card title="Context documents">
          <p className="mb-3 text-sm text-ink-mute">
            The structural documents behind every crew decision. They always exist and
            cannot be deleted — edit them instead.
          </p>
          <div className="space-y-2">
            {context.map((d) => (
              <div key={d.id} className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                <span className="min-w-0 truncate font-mono text-xs text-ink">{d.path}</span>
                <span className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
                  updated {fmtDate(d.updated_at)}
                </span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
