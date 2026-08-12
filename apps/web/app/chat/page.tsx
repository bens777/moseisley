import { redirect } from "next/navigation";
import { HOME_PATH } from "@/lib/routes";

/* Retired: both threads that used to live here — the Manager and the
   Orchestrator — are tabs on the chat home now. The route stays so old links,
   bookmarks and anything still pointing at /chat land on the real thing. */
export default function ChatPage() {
  redirect(HOME_PATH);
}
