"use client";
import { ReactNode, useState } from "react";
import { ManagerConversation } from "@/components/manager";
import { OrchestratorThread } from "@/components/orchestrator";

/* HOME — every conversation you have with the crew, full screen. The shell
   gives this route the whole viewport (no page padding, no Manager bar: the
   page IS the chat) and each tab mounts an existing thread component as-is.

   Tabs are data: each agent that gets its own session becomes an entry here.
   Only the Manager and the Orchestrator exist today, but nothing about the tab
   bar is hardcoded to two — add a row and it renders. Switching tabs only
   mounts and unmounts; the sessions and their history live on the backend and
   are never touched by the switch. */

type ChatTab = {
  key: string;
  label: string;
  hint: string;
  render: () => ReactNode;
};

const TABS: ChatTab[] = [
  {
    key: "manager",
    label: "Manager",
    hint: "your concierge — setup, drafts, anything you need",
    render: () => <ManagerConversation variant="page" pageContext={{ page: "home" }} />,
  },
  {
    key: "orchestrator",
    label: "Orchestrator",
    hint: "the agent that runs your crew",
    render: () => <OrchestratorThread />,
  },
];

export default function ChatHomePage() {
  const [active, setActive] = useState(TABS[0].key);
  const tab = TABS.find((t) => t.key === active) ?? TABS[0];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 border-b border-line px-4">
        <div role="tablist" aria-label="Conversation"
             className="mx-auto flex w-full max-w-3xl items-center gap-1">
          {TABS.map((t) => {
            const on = t.key === tab.key;
            return (
              <button
                key={t.key}
                role="tab"
                type="button"
                aria-selected={on}
                title={t.hint}
                onClick={() => setActive(t.key)}
                className={`-mb-px min-h-[40px] border-b-2 px-3 font-mono text-[11px] font-bold uppercase tracking-widest transition ${
                  on ? "border-copper text-ink" : "border-transparent text-ink-faint hover:text-ink-mute"
                }`}
              >
                {t.label}
              </button>
            );
          })}
          <span className="ml-auto hidden truncate pl-3 font-mono text-[10px] uppercase tracking-widest text-ink-faint sm:block">
            {tab.hint}
          </span>
        </div>
      </div>

      {/* one thread mounted at a time — each keeps its own session server-side */}
      <div key={tab.key} className="flex min-h-0 flex-1 flex-col">
        {tab.render()}
      </div>
    </div>
  );
}
