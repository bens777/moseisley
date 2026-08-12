/* eslint-disable @next/next/no-img-element */
import Link from "next/link";

export const metadata = { title: "Pricing" };

const TIERS = [
  {
    tag: "self-hosted",
    name: "Community",
    price: "$0",
    cadence: "free forever",
    features: [
      "Open-source",
      "Self-hosted",
      "Bring your own AI keys (BYOK) · unlimited with your keys",
      "Full product on your own machine",
    ],
    priceNote: "No credit card required",
    cta: { label: "View on GitHub", href: "https://github.com/bens777/moseisley", external: true },
    ctaNote: "",
    highlight: false,
  },
  {
    tag: "hosted",
    name: "Basic",
    price: "$9",
    cadence: "per month",
    features: [
      // mirrors FACTORY_BASIC_DAILY_REQUESTS in backend/core/config.py — keep in sync
      "Built-in AI included · 150 AI requests/day",
      "Or bring your own keys & Ollama",
      "Hosted Moseisley",
      "Web Command Center",
      "Orchestrator + Chat",
      "Goals + persistent memory",
      "JSON workspace / export",
      "Provider & model selection",
      "OpenRouter support",
      "Core Crew functionality",
      "Activity / Ledger",
      "Emergency Stop",
    ],
    priceNote: "",
    cta: { label: "Start free trial", href: "/register", external: false },
    ctaNote: "14 days free · then $9/month",
    highlight: false,
  },
  {
    tag: "hosted",
    name: "Pro",
    price: "$19",
    cadence: "per month",
    features: [
      "Everything in Basic",
      // mirrors FACTORY_PRO_DAILY_REQUESTS in backend/core/config.py — keep in sync
      "Bigger fuel tank · 400 AI requests/day",
      "Full Crew",
      "Telegram gateway",
      "X-Ray analysis",
      "Strategist + Challenger",
      "Market Radar + Auditor",
      "Experiments",
      "Scheduled autonomous operations",
      "Autopilot capabilities",
      "Advanced integrations",
      "Treasury / approvals",
      "Full Personal OS functionality",
    ],
    priceNote: "",
    cta: { label: "Start free trial", href: "/register", external: false },
    ctaNote: "14 days free · then $19/month",
    highlight: true,
  },
];

export default function PricingPage() {
  return (
    <div className="cantina min-h-screen overflow-x-hidden">
      <header className="relative z-20 mx-auto flex max-w-5xl items-center justify-between gap-3 px-4 py-4 sm:px-6">
        <Link href="/" className="flex min-w-0 items-center gap-2.5">
          <img src="/brand/logo-mark.webp" alt="Moseisley — the cantina of AI agents"
               width={44} height={44} loading="eager"
               className="rounded-full border-2 border-[var(--cw-purple)] shadow-[0_0_18px_-2px_rgb(160_110_242/0.8)]" />
          <span className="font-display truncate text-lg font-bold tracking-tight">
            moseisley<span className="text-[var(--cw-magenta)]">.sh</span>
          </span>
        </Link>
        <Link href="/login"
              className="rounded-full border border-[var(--cw-line)] bg-[var(--cw-panel)] px-4 py-2 text-sm text-[var(--cw-ink)] hover:border-[var(--cw-purple)]">
          Log in
        </Link>
      </header>

      <main className="relative pb-16">
        <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden"><div className="cw-stars" /></div>
        <div className="relative mx-auto max-w-5xl px-4 sm:px-6">
          <div className="text-center">
            <h1 className="font-display text-3xl font-bold sm:text-4xl">
              <span className="cw-title">Pricing</span>
            </h1>
            <p className="mx-auto mt-3 max-w-md text-sm text-[var(--cw-mute)]">
              One product, three ways to run it. Hosted passes include built-in AI;
              your data and your AI keys stay yours either way.
            </p>
          </div>

          <p className="mx-auto mt-6 max-w-2xl rounded-2xl border border-[var(--cw-gold)]/40 bg-[var(--cw-gold)]/5 px-5 py-3 text-center text-sm text-[var(--cw-mute)]">
            <span className="font-semibold text-[var(--cw-gold)]">
              Every hosted pass starts with a 14-day free trial — AI included, no API key needed.
            </span>{" "}
            After that: keep our built-in AI, or connect your own keys and models
            (OpenAI, Anthropic, Ollama…). Your own provider usage is billed by that provider.
          </p>

          <div className="mt-10 grid gap-5 md:grid-cols-3">
            {TIERS.map((t) => (
              <div key={t.name}
                   className={`cw-panel flex min-w-0 flex-col p-6 text-center ${
                     t.highlight ? "cw-panel-magenta" : ""}`}>
                <img src="/brand/logo-mark.webp" alt="" width={40} height={40} loading="lazy"
                     className={`mx-auto rounded-full ${t.highlight ? "" : "opacity-60 grayscale-[30%]"}`} />
                <div className="mt-2 font-mono text-[10px] uppercase tracking-[0.3em] text-[var(--cw-faint)]">
                  {t.tag}
                </div>
                <h2 className="font-display mt-1 text-xl font-bold">Moseisley {t.name}</h2>
                <div className="mt-1">
                  <span className={`font-display text-3xl font-black ${t.highlight ? "cw-neon-magenta" : ""}`}>
                    {t.price}
                  </span>
                  <span className="ml-1.5 text-xs text-[var(--cw-faint)]">{t.cadence}</span>
                </div>
                {t.priceNote && (
                  <div className="mt-1 text-[11px] text-[var(--cw-faint)]">{t.priceNote}</div>
                )}
                <ul className="mt-4 flex-1 space-y-1.5 text-left text-[13px] text-[var(--cw-mute)]">
                  {t.features.map((f) => (
                    <li key={f} className="flex gap-2">
                      <span className="text-[var(--cw-purple)]">·</span>
                      <span className="min-w-0 break-words">{f}</span>
                    </li>
                  ))}
                </ul>
                <p className="mt-4 text-[11px] leading-relaxed text-[var(--cw-faint)]">
                  {t.name === "Community"
                    ? "You run it, you own it. AI provider usage billed by your providers."
                    : "Built-in AI is included with a fair-use daily limit. Prefer your own keys? That usage is billed by your provider."}
                </p>
                {t.cta.external ? (
                  <a href={t.cta.href} target="_blank" rel="noopener noreferrer"
                     className="mt-4 block rounded-full border border-[var(--cw-line)] px-4 py-2.5 text-center text-sm font-bold text-[var(--cw-ink)] hover:border-[var(--cw-purple)]">
                    {t.cta.label}
                  </a>
                ) : (
                  <Link href={t.cta.href}
                        className={`mt-4 block rounded-full px-4 py-2.5 text-center text-sm font-bold ${
                          t.highlight
                            ? "cw-cta uppercase tracking-wide text-white"
                            : "border border-[var(--cw-line)] text-[var(--cw-ink)] hover:border-[var(--cw-purple)]"
                        }`}>
                    {t.cta.label}
                  </Link>
                )}
                {t.ctaNote && (
                  <p className="mt-2 text-center text-[11px] text-[var(--cw-faint)]">{t.ctaNote}</p>
                )}
              </div>
            ))}
          </div>

          <p className="mx-auto mt-8 max-w-2xl text-center text-[11px] leading-relaxed text-[var(--cw-faint)]">
            Subscriptions are billed through Stripe and can be canceled anytime from the
            customer portal. AI provider usage is billed separately by your chosen
            provider using your own API key.
          </p>
        </div>
      </main>

      <footer className="relative border-t border-[var(--cw-line)] py-8">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-2 px-4 text-[12px] text-[var(--cw-faint)] sm:px-6">
          <span>moseisley.sh</span>
          <div className="flex flex-wrap gap-4">
            <Link href="/legal" className="hover:text-[var(--cw-mute)]">Legal</Link>
            <a href="mailto:cantina@moseisley.sh" className="hover:text-[var(--cw-mute)]">
              Support: cantina@moseisley.sh
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}
