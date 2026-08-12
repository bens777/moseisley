/* Clickable actions the Manager can put in a message.

   Mirrors backend/agents/actions.py — that file is the source of truth and a
   test fails if the two drift. Only ids listed here ever become links: the
   pattern requires the `action:` form, so a markdown link to a URL is left as
   literal text, and an id that is not in this map renders as its label. */
export const ACTION_ROUTES: Record<string, string> = {
  crew_genesis: "/welcome",
  connections: "/connections",
  command: "/command",
  bar: "/bar",
  settings: "/settings",
  schedule: "/schedule",
  data: "/data",
  security: "/security",
  skills: "/skills",
  challenge: "/challenge",
  trading: "/trading",
  crew: "/agents",
  goals: "/goals",
  projects: "/projects",
  xray: "/xray",
  radar: "/market",
  money: "/money",
};

export const ACTION_PATTERN = /\[([^\]\n]{1,60})\]\(action:([a-z_]{1,32})\)/g;

export type MessagePart =
  | { kind: "text"; text: string }
  | { kind: "action"; label: string; href: string };

/* Split a Manager message into plain text and whitelisted actions. Anything
   else — including [label](https://…) — stays text. */
export function parseMessage(content: string): MessagePart[] {
  const parts: MessagePart[] = [];
  let cursor = 0;
  for (const m of content.matchAll(ACTION_PATTERN)) {
    const href = ACTION_ROUTES[m[2]];
    if (!href) continue;                       // unknown id: leave it in the text run
    if (m.index > cursor) parts.push({ kind: "text", text: content.slice(cursor, m.index) });
    parts.push({ kind: "action", label: m[1], href });
    cursor = m.index + m[0].length;
  }
  if (cursor < content.length) parts.push({ kind: "text", text: content.slice(cursor) });
  return parts;
}
