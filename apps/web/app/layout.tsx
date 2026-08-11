import type { Metadata } from "next";
import { Inter, JetBrains_Mono, Space_Grotesk } from "next/font/google";
import "./globals.css";
import Shell from "@/components/shell";
import { CantinaRadio } from "@/components/cantina-radio";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const spaceGrotesk = Space_Grotesk({ subsets: ["latin"], variable: "--font-space-grotesk" });
const jetbrainsMono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains-mono" });

export const metadata: Metadata = {
  metadataBase: new URL("https://moseisley.sh"),
  title: {
    default: "Moseisley.sh — Your AI crew, working for you.",
    template: "%s · Moseisley.sh",
  },
  description:
    "Moseisley.sh is your personal AI command center. Give your agents goals, tools and autonomy — Moseisley.sh coordinates the rest.",
  openGraph: {
    title: "Moseisley.sh — Your AI crew, working for you.",
    description:
      "Give your agents goals, tools and autonomy. Moseisley.sh coordinates the rest.",
    url: "https://moseisley.sh",
    siteName: "Moseisley.sh",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Moseisley.sh — Your AI crew, working for you.",
    description:
      "Give your agents goals, tools and autonomy. Moseisley.sh coordinates the rest.",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${spaceGrotesk.variable} ${jetbrainsMono.variable}`}>
      <body>
        <Shell>{children}</Shell>
        {/* mounted at the root so the <audio> element survives client-side
            navigation between every page, public and authenticated */}
        <CantinaRadio />
      </body>
    </html>
  );
}
