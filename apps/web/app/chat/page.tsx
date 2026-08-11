"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Button, ErrorBox, Input } from "@/components/ui";

type Message = { id: string; role: string; content: string; channel: string };

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  async function load() {
    try {
      setMessages(await api<Message[]>("/chat/messages"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }

  useEffect(() => { load(); }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

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
    <div className="mx-auto flex h-[calc(100dvh-7.5rem)] max-w-3xl flex-col md:h-[calc(100vh-3rem)]">
      <div className="mb-4">
        <h1 className="text-xl font-bold">Chat</h1>
        <p className="mt-0.5 font-mono text-[11px] uppercase tracking-widest text-ink-faint">comms · direct line to your crew</p>
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto rounded-md border border-line bg-panel/60 p-4">
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
        <div ref={bottom} />
      </div>
      {error && <div className="mt-2"><ErrorBox message={error} /></div>}
      <form onSubmit={send} className="mt-3 flex gap-2">
        <Input placeholder="Message your crew…" value={text} onChange={(e) => setText(e.target.value)} />
        <Button type="submit" disabled={busy}>Send</Button>
      </form>
    </div>
  );
}
