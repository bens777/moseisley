"use client";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui";

type Msg = { id: string; role: string; content: string; created_at: string };
type Draft = Record<string, unknown> | null;

/* One persistent Manager conversation, reachable from every authenticated page.
   Desktop: right side panel. Mobile: bottom sheet. Page context is sent with
   every message so "change this to 8am" resolves against what's on screen. */
export function ManagerPanel({ pageContext }: { pageContext?: Record<string, unknown> }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [draft, setDraft] = useState<Draft>(null);
  const [showDraftJson, setShowDraftJson] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    api<Msg[]>("/manager/messages").then(setMessages).catch(() => {});
    api<{ draft: Draft }>("/manager/draft")
      .then((r) => setDraft(r.draft && Object.keys(r.draft).length ? r.draft : null))
      .catch(() => {});
  }, []);

  useEffect(() => { if (open) load(); }, [open, load]);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, draft, open]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    setInput("");
    setMessages((m) => [...m, { id: `tmp-${Date.now()}`, role: "user", content: text,
                                created_at: new Date().toISOString() }]);
    try {
      const r = await api<{ reply: string; draft: Draft }>("/manager/message", {
        body: { text, page_context: { page: pathname, ...(pageContext || {}) } },
      });
      setMessages((m) => [...m, { id: `tmp-r-${Date.now()}`, role: "assistant",
                                  content: r.reply, created_at: new Date().toISOString() }]);
      setDraft(r.draft && Object.keys(r.draft).length ? r.draft : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Manager unavailable");
    } finally {
      setBusy(false);
    }
  }

  async function applyDraft() {
    setBusy(true);
    try {
      const r = await api<{ saved?: boolean; error?: string }>("/manager/draft/apply", { body: {} });
      if (r.saved) {
        setDraft(null);
        setMessages((m) => [...m, { id: `tmp-s-${Date.now()}`, role: "assistant",
                                    content: "Saved. The instruction is now active — see its page for details.",
                                    created_at: new Date().toISOString() }]);
      } else if (r.error) setError(r.error);
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {!open && (
        <button
          aria-label="Open Manager chat"
          onClick={() => setOpen(true)}
          className="fixed bottom-4 right-4 z-40 flex min-h-[48px] items-center gap-2 rounded-md border border-brand/50 bg-raised px-4 font-mono text-xs font-bold uppercase tracking-widest text-brand shadow-lg shadow-black/40 transition hover:border-brand"
        >
          <span aria-hidden>◈</span> manager
        </button>
      )}

      {open && (
        <div
          role="dialog" aria-label="Manager chat"
          className="fixed inset-x-0 bottom-0 z-30 flex h-[78dvh] flex-col rounded-t-lg border-t border-line-strong bg-panel shadow-2xl shadow-black/60 md:inset-x-auto md:inset-y-0 md:right-0 md:h-auto md:w-[400px] md:rounded-none md:border-l md:border-t-0"
        >
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <div className="flex items-center gap-2">
              <span aria-hidden className="font-mono text-brand">◈</span>
              <span className="font-mono text-[11px] font-bold uppercase tracking-widest">Manager</span>
              <span className="led led-pulse bg-ok" aria-hidden />
            </div>
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate font-mono text-[10px] uppercase tracking-widest text-ink-faint">
                context: {pathname}
              </span>
              <button aria-label="Close Manager chat" onClick={() => setOpen(false)}
                      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-ink-mute hover:text-ink">
                ✕
              </button>
            </div>
          </div>

          <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
            {messages.length === 0 && (
              <p className="text-sm text-ink-faint">
                Ask about your crew, costs, projects — or describe an automation
                (&ldquo;watch X for me every morning…&rdquo;). I stage changes as drafts;
                nothing is saved until you confirm.
              </p>
            )}
            {messages.map((m) => (
              <div key={m.id}
                   className={`max-w-[92%] break-words rounded-md px-3 py-2 text-sm leading-relaxed ${
                     m.role === "user" ? "ml-auto bg-raised text-ink" : "bg-ground/60 text-ink-mute"}`}>
                {m.content}
              </div>
            ))}
            {draft && (
              <div className="rounded-md border border-brand/40 bg-brand/5 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[10px] font-bold uppercase tracking-widest text-brand">
                    draft — not saved
                  </span>
                  <button onClick={() => setShowDraftJson(!showDraftJson)}
                          className="font-mono text-[10px] uppercase tracking-widest text-ink-faint hover:text-ink">
                    {showDraftJson ? "human" : "json"}
                  </button>
                </div>
                {showDraftJson ? (
                  <pre className="mt-2 max-h-48 overflow-auto rounded-sm bg-ground/70 p-2 font-mono text-[10px] leading-relaxed text-signal">
                    {JSON.stringify(draft, null, 2)}
                  </pre>
                ) : (
                  <div className="mt-2 space-y-1 text-sm text-ink-mute">
                    <div className="font-medium text-ink">{String(draft.name || "")}</div>
                    <div className="font-mono text-[11px]">
                      {String(draft.kind || "")}
                      {(draft.schedule as Record<string, unknown>)?.frequency
                        ? ` · ${(draft.schedule as Record<string, string>).frequency} ${(draft.schedule as Record<string, string>).time || ""}`
                        : ""}
                      {Array.isArray(draft.delivery) && draft.delivery.length
                        ? ` · → ${(draft.delivery as string[]).join(", ")}` : ""}
                    </div>
                  </div>
                )}
                <div className="mt-3 flex gap-2">
                  <Button onClick={applyDraft} disabled={busy}>Save / Apply</Button>
                  <Button variant="ghost" disabled={busy} onClick={async () => {
                    await api("/manager/draft", { method: "DELETE" });
                    setDraft(null);
                  }}>Discard</Button>
                </div>
              </div>
            )}
            {busy && (
              <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-ink-faint">
                <span className="led led-pulse bg-signal" aria-hidden /> manager thinking…
              </div>
            )}
            {error && <p className="break-words text-xs text-crit">{error}</p>}
          </div>

          <div className="border-t border-line p-3">
            <div className="flex items-end gap-2">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
                }}
                rows={2}
                placeholder="Talk to your Manager…"
                className="min-h-[44px] w-full min-w-0 resize-none rounded-md border border-line-strong bg-ground/60 px-3 py-2 text-sm text-ink placeholder-ink-faint focus:border-brand/60 focus:outline-none"
              />
              <Button onClick={send} disabled={busy || !input.trim()}>Send</Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
