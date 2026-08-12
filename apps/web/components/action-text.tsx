"use client";
import Link from "next/link";
import { parseMessage } from "@/lib/actions";
import type { Route } from "next";

/* Manager message body: plain text, plus the whitelisted [label](action:id)
   links rendered as buttons. Nothing else is ever turned into a link — the
   parser only recognises the action form, so URLs the model writes stay inert
   text on the page. */
export function ActionText({ content }: { content: string }) {
  return (
    <>
      {parseMessage(content).map((part, i) =>
        part.kind === "text" ? (
          <span key={i}>{part.text}</span>
        ) : (
          <Link
            key={i}
            href={part.href as Route}
            className="mx-0.5 inline-flex min-h-[28px] items-center rounded-md border border-copper/50 bg-copper/10 px-2.5 py-0.5 text-[13px] font-medium text-copper transition hover:bg-copper/20"
          >
            {part.label}
          </Link>
        )
      )}
    </>
  );
}
