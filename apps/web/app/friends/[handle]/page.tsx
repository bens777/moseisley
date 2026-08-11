import type { Metadata } from "next";
import ProfileClient from "./profile-client";

type Props = { params: Promise<{ handle: string }> };

const INTERNAL_API = process.env.INTERNAL_API_URL || process.env.NEXT_PUBLIC_API_URL
  || "http://localhost:8000";

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { handle } = await params;
  const safe = handle.replace(/[^a-z0-9-]/gi, "").slice(0, 30);
  let title = `@${safe} — Friends of the Cantina | Moseisley.sh`;
  let description = "Builders, founders and weird creatures building with AI.";
  try {
    const resp = await fetch(`${INTERNAL_API}/api/public/friends/${safe}`,
                             { next: { revalidate: 300 }, signal: AbortSignal.timeout(2500) });
    if (resp.ok) {
      const p = await resp.json();
      title = `${p.display_name} (@${p.handle}) — Friends of the Cantina | Moseisley.sh`;
      if (p.bio) description = p.bio;
    }
  } catch { /* fall back to generic public metadata */ }
  return {
    title,
    description,
    openGraph: { title, description, siteName: "Moseisley.sh" },
  };
}

export default async function ProfilePage({ params }: Props) {
  const { handle } = await params;
  return <ProfileClient handle={handle} />;
}
