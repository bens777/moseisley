"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Button, ErrorBox, Input } from "@/components/ui";
import { VoiceInput } from "@/components/voice-input";

/* The Orchestrator thread — the agent that runs your crew, on the native_chat
   session (/chat/*). Deliberately NOT merged with the Manager: two different
   agents on two different sessions, each keeping its own history. Lifted out of
   the old /chat page unchanged so the unified chat home can mount it as a tab. */

type Message = { id: string; role: string; content: string; channel: string };

export function OrchestratorThread() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLInputElement>(null);

  async function load() {
    try {
      setMessages(await api<Message[]>("/chat/messages"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }

  useEffect(() => { load(); }, []);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim() || busy) return;
    const t = text;
    setText("");
    setBusy(true);
    setError(null);
    setMessages((m) => [...m, { id: "tmp", role: "user", content: t, channel: "web" }]);
    try {
      await api("/chat/message", { body: { text: t } });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "send failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6">
        <div className="mx-auto w-full max-w-3xl space-y-3">
          {messages.length === 0 && (
            <div className="rounded-md border border-dashed border-line-strong bg-panel/40 p-4">
              <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">comms channel open</div>
              <p className="mt-2 text-sm text-ink-mute">
                Your crew is listening. State a goal, ask for a status report, or give an order:
              </p>
              <ul className="mt-2 space-y-1 text-sm text-ink-mute">
                <li><span className="text-brand">·</span> “I want €10,000/month independent income by June 2027.”</li>
                <li><span className="text-brand">·</span> “Where am I compared with my goals?”</li>
                <li><span className="text-brand">·</span> “What should I focus on today?”</li>
              </ul>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={m.id + i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[80%] whitespace-pre-wrap rounded-md px-3 py-2 text-sm ${
                m.role === "user" ? "bg-brand/15 text-ink border border-brand/25" : "bg-raised text-ink"
              }`}>
                {m.content}
                {m.channel === "telegram" && <span className="ml-2 text-[10px] text-ink-mute">via Telegram</span>}
              </div>
            </div>
          ))}
          {busy && <div className="text-xs text-ink-mute">Your crew is thinking…</div>}
          {error && <ErrorBox message={error} />}
          <div ref={bottom} />
        </div>
      </div>

      <div className="border-t border-line px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        <form onSubmit={send} className="mx-auto flex w-full max-w-3xl items-end gap-2">
          <Input ref={composer} placeholder="Message your crew…" value={text}
                 onChange={(e) => setText(e.target.value)}
                 className="min-h-[56px] rounded-lg border-2 border-brand/40 bg-ground/60 px-4 text-base focus:border-brand" />
          <VoiceInput disabled={busy} onTranscript={(heard) => {
            setText((current) => (current ? `${current} ${heard}` : heard));
            const box = composer.current;
            if (box) {
              box.focus();
              requestAnimationFrame(() => {
                box.selectionStart = box.selectionEnd = box.value.length;
              });
            }
          }} />
          <Button type="submit" disabled={busy} className="min-h-[56px] px-5">Send</Button>
        </form>
      </div>
    </>
  );
}
