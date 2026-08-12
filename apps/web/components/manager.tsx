"use client";
/* eslint-disable @next/next/no-img-element */
import { usePathname } from "next/navigation";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { ActionText } from "@/components/action-text";
import { VoiceInput } from "@/components/voice-input";
import { Button } from "@/components/ui";

type Msg = { id: string; role: string; content: string; created_at: string };
type Draft = Record<string, unknown> | null;

/* One persistent Manager conversation, reachable from every authenticated page.
   Desktop: right side panel. Mobile: bottom sheet. Page context is sent with
   every message so "change this to 8am" resolves against what's on screen. */
/* The Manager conversation itself — messages, draft card, composer. Owns its
   own state so it can be mounted anywhere: inside the drawer (below) or
   full-page on the chat home. Both mounts talk to the same session and history.
   `variant` only changes the frame: "panel" is the narrow drawer, "page" is the
   full-height home screen (centered reading column, larger composer). */
export function ManagerConversation({ pageContext, pending, variant = "panel" }: {
  pageContext?: Record<string, unknown>;
  pending?: string;
  variant?: "panel" | "page";
}) {
  const page = variant === "page";
  const column = page ? "mx-auto w-full max-w-3xl" : "";
  const pathname = usePathname();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [draft, setDraft] = useState<Draft>(null);
  const [showDraftJson, setShowDraftJson] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    // On the chat home the Manager gets to speak first: the backend posts at
    // most one proactive nudge, once ever, so this is safe to call on mount.
    const opening = page
      ? api<{ posted: boolean }>("/manager/nudge", { body: {} }).catch(() => null)
      : Promise.resolve(null);
    opening.then(() => api<Msg[]>("/manager/messages").then(setMessages).catch(() => {}));
    api<{ draft: Draft }>("/manager/draft")
      .then((r) => setDraft(r.draft && Object.keys(r.draft).length ? r.draft : null))
      .catch(() => {});
  }, [page]);

  // text typed in the bar arrives pre-filled, ready to send or edit
  useEffect(() => { if (pending) setInput(pending); }, [pending]);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, draft]);

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
        <div ref={scrollRef} className={`min-h-0 flex-1 overflow-y-auto ${page ? "px-4 py-6" : "p-4"}`}>
         <div className={`space-y-3 ${column}`}>
          {messages.length === 0 && (page ? (
            <div className="py-10 text-center">
              <img src="/brand/logo-mark.webp" alt="" width={44} height={44} aria-hidden
                   className="mx-auto h-11 w-11 rounded-full border border-copper/50" />
              <p className="mt-3 font-mono text-[11px] font-bold uppercase tracking-widest text-copper">
                Manager
              </p>
              <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-ink-mute">
                Tell me what you need — or say &ldquo;do the setup&rdquo; and I&apos;ll handle
                everything. Ask about your crew, costs and projects, or describe an automation
                (&ldquo;watch X for me every morning…&rdquo;). I stage changes as drafts;
                nothing is saved until you confirm.
              </p>
            </div>
          ) : (
            <p className="text-sm text-ink-faint">
              Ask about your crew, costs, projects — or describe an automation
              (&ldquo;watch X for me every morning…&rdquo;). I stage changes as drafts;
              nothing is saved until you confirm.
            </p>
          ))}
          {messages.map((m) => (
            <div key={m.id}
                 className={`max-w-[92%] whitespace-pre-wrap break-words rounded-md px-3 py-2 text-sm leading-relaxed ${
                   m.role === "user" ? "ml-auto bg-raised text-ink" : "bg-ground/60 text-ink-mute"}`}>
              {/* the Manager guides with buttons; user text is never parsed */}
              {m.role === "user" ? m.content : <ActionText content={m.content} />}
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
        </div>

        <div className={`border-t border-line ${
          page ? "px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]" : "p-3"}`}>
          <div className={`flex items-end gap-2 ${column}`}>
            <textarea
              ref={composer}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
              }}
              rows={2}
              placeholder="Talk to your Manager…"
              className={`w-full min-w-0 resize-none rounded-md border bg-ground/60 px-3 py-2 text-ink placeholder-ink-faint focus:outline-none ${
                page
                  ? "min-h-[56px] rounded-lg border-2 border-copper/50 px-4 py-3 text-base focus:border-copper focus:shadow-[0_0_0_3px_rgb(225_79_208/0.25)]"
                  : "min-h-[44px] border-line-strong text-sm focus:border-brand/60"}`}
            />
            <VoiceInput disabled={busy} onTranscript={(text) => {
              // the transcript is a draft: it lands in the box, never sends
              setInput((current) => (current ? `${current} ${text}` : text));
              const box = composer.current;
              if (box) {
                box.focus();
                requestAnimationFrame(() => {
                  box.selectionStart = box.selectionEnd = box.value.length;
                });
              }
            }} />
            <Button onClick={send} disabled={busy || !input.trim()}>Send</Button>
          </div>
        </div>
    </>
  );
}

/* One persistent Manager conversation, reachable from every authenticated page.
   Desktop: right side panel. Mobile: full screen. Page context is sent with
   every message so "change this to 8am" resolves against what's on screen. */
export function ManagerPanel({ pageContext, open, setOpen, pending }: {
  pageContext?: Record<string, unknown>;
  open: boolean;
  setOpen: (v: boolean) => void;
  pending?: string;
}) {
  const pathname = usePathname();
  if (!open) return null;
  return (
    <div
      role="dialog" aria-label="Manager chat"
      className="fixed inset-0 z-40 flex flex-col border-line-strong bg-panel shadow-2xl shadow-black/60 md:inset-y-0 md:left-auto md:right-0 md:w-[560px] md:border-l"
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <div className="flex items-center gap-2">
          <img src="/brand/logo-mark.webp" alt="" width={20} height={20} aria-hidden
               className="h-5 w-5 shrink-0 rounded-full border border-brand/50" />
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
      <ManagerConversation pageContext={pageContext} pending={pending} />
    </div>
  );
}

/* Imperative opener so any page can hand the user to the Manager (the dock
   owns the state and lives in the shell). Same module-registry pattern as the
   Cantina radio — no context provider needed for one handle. */
let dockOpen: ((prefill?: string) => void) | null = null;

export function openManagerPanel(prefill?: string) { dockOpen?.(prefill); }

/* ── The Manager bar: the product's front door ──────────────────────
   A persistent strip under the page header (sticky on mobile) with the
   conversation one keystroke away. Typing here opens the full drawer with the
   text carried over. Replaces the old bottom-right launcher. */

function TelegramButton() {
  const billing = useApi<{ plan: string; configured: boolean }>("/billing");
  const [hint, setHint] = useState(false);
  if (billing.loading) return null;
  // Telegram is a PRO_FEATURES capability, and gating only applies where
  // Stripe billing is configured (self-host keeps everything).
  const entitled = !billing.data?.configured || billing.data?.plan === "pro";

  if (entitled) {
    return (
      <Link href="/connections"
            title="Set up the Telegram gateway"
            className="flex min-h-[32px] shrink-0 items-center gap-1.5 rounded-md border border-line-strong px-2.5 font-mono text-[10px] uppercase tracking-widest text-ink-mute transition hover:bg-raised hover:text-ink">
        <span aria-hidden>✈</span> Telegram
      </Link>
    );
  }
  return (
    <div className="relative shrink-0">
      <button onClick={() => setHint(!hint)} aria-expanded={hint}
              className="flex min-h-[32px] items-center gap-1.5 rounded-md border border-line-strong px-2.5 font-mono text-[10px] uppercase tracking-widest text-ink-faint transition hover:text-ink-mute">
        <span aria-hidden>✈</span> Telegram
        <span className="rounded-sm bg-brand/15 px-1 text-[9px] font-bold text-brand">pro</span>
      </button>
      {hint && (
        <div className="absolute right-0 top-full z-40 mt-1 w-56 rounded-md border border-line-strong bg-panel p-2.5 text-xs text-ink-mute shadow-lg shadow-black/40">
          Talking to your crew from Telegram is part of Pro.{" "}
          <Link href="/settings#billing" className="text-brand hover:underline">See plans →</Link>
        </div>
      )}
    </div>
  );
}

export function ManagerDock({ pageContext }: { pageContext?: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState("");
  const [barInput, setBarInput] = useState("");
  const autoOpened = useRef(false);

  // First login: no Manager conversation yet → open the panel so onboarding
  // starts by itself. Derived from the existing messages endpoint; no new API.
  useEffect(() => {
    api<Msg[]>("/manager/messages")
      .then((m) => {
        if (m.length === 0 && !autoOpened.current) {
          autoOpened.current = true;
          setOpen(true);
        }
      })
      .catch(() => {});
  }, []);

  function openWith(text: string) {
    setPending(text);
    setBarInput("");
    setOpen(true);
  }

  useEffect(() => {
    dockOpen = (prefill?: string) => {
      if (prefill) setPending(prefill);
      setOpen(true);
    };
    return () => { dockOpen = null; };
  }, []);

  return (
    <>
      {/* ONE Manager surface at a time: the drawer carries the composer, so the
          bar collapses to a strip while it is open — never two inputs on screen. */}
      <div className={`sticky top-0 z-20 -mx-4 mb-5 border-b border-copper/60 bg-ground/95 px-4 shadow-[0_0_28px_-8px_rgb(225_79_208/0.55)] backdrop-blur md:static md:mx-0 md:rounded-lg md:border md:border-copper/60 md:bg-panel/70 md:px-6 ${
        open ? "py-2.5 md:py-3" : "py-4 md:py-5"}`}>
        {open ? (
          <div className="flex items-center gap-3">
            <button onClick={() => setOpen(false)}
                    aria-expanded
                    aria-label="Close the Manager"
                    className="flex min-w-0 flex-1 items-center gap-2 font-mono text-[13px] font-bold uppercase tracking-widest text-copper">
              <img src="/brand/logo-mark.webp" alt="" width={22} height={22} aria-hidden
                   className="h-[22px] w-[22px] shrink-0 rounded-full border border-copper/50" />
              Manager
              <span className="hidden font-normal text-ink-faint sm:inline">— open</span>
            </button>
            <TelegramButton />
            <button onClick={() => setOpen(false)} aria-label="Close the Manager"
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink-mute transition hover:bg-raised hover:text-ink">
              ✕
            </button>
          </div>
        ) : (
          <>
            <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <button onClick={() => setOpen(true)}
                      aria-expanded={false}
                      className="flex shrink-0 items-center gap-2 font-mono text-[13px] font-bold uppercase tracking-widest text-copper">
                <img src="/brand/logo-mark.webp" alt="" width={22} height={22} aria-hidden
                     className="h-[22px] w-[22px] shrink-0 rounded-full border border-copper/50" />
                Manager
                <span className="hidden font-normal text-ink-faint sm:inline">— talk to your crew</span>
              </button>
              <p className="min-w-0 text-sm text-ink-mute">
                Hey, I&apos;m your Manager. Tell me what you need — or say
                &ldquo;do the setup&rdquo; and I&apos;ll handle everything.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <input
                value={barInput}
                onChange={(e) => setBarInput(e.target.value)}
                onFocus={() => { if (barInput) openWith(barInput); }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && barInput.trim()) { e.preventDefault(); openWith(barInput); }
                }}
                placeholder="Ask anything, or say “do the setup”…"
                aria-label="Message your Manager"
                className="min-h-[56px] w-full min-w-0 flex-1 rounded-lg border-2 border-copper/50 bg-ground/60 px-4 text-[19px] text-ink placeholder-ink-faint focus:border-copper focus:shadow-[0_0_0_3px_rgb(225_79_208/0.25)] focus:outline-none sm:w-auto"
              />
              <TelegramButton />
            </div>
          </>
        )}
      </div>

      <ManagerPanel pageContext={pageContext} open={open} setOpen={setOpen} pending={pending} />
    </>
  );
}
