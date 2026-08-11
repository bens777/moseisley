/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { MobileNav } from "@/components/mobile-nav";
import { WelcomeGate } from "@/components/welcome-gate";

/* ═══ THE CANTINA OF AI AGENTS — public homepage world ═══
   Art direction: derived from the official Moseisley logo (purple cephalopod,
   space black, magenta/cyan neon, gold accents). All character art is original
   generated brand art stored in /public/brand. */

const CREW = [
  { img: "crew-orchestrator.webp", name: "The Orchestrator", role: "One brain, whole crew",
    line: "Runs the room. Knows your goals, delegates bounded jobs, briefs you.",
    quip: "“Everyone works. I make sure it matters.”", status: "COMMANDS", tone: "gold", featured: true },
  { img: "crew-strategist.webp", name: "The Strategist", role: "Plans the route",
    line: "Reads goals, state and constraints. Returns 3 real moves — or honestly, none.",
    quip: "“Eight tentacles, one plan.”", status: "PLOTTING", tone: "magenta" },
  { img: "crew-radar.webp", name: "Radar", role: "Watches the horizon",
    line: "Daily market sweeps, evidence-graded. Most days: “no material change.”",
    quip: "“I heard something. It was nothing. I keep listening.”", status: "SCANNING", tone: "cyan" },
  { img: "crew-xray.webp", name: "X-Ray", role: "Sees what you miss",
    line: "Scans your last 90 days for unpaid invoices, dropped leads, lost promises.",
    quip: "“Your inbox glows in interesting places.”", status: "2 FOUND", tone: "cyan" },
  { img: "crew-challenger.webp", name: "The Challenger", role: "Paid to disagree",
    line: "Attacks your strategy: sunk cost, bias, missing data, regime change.",
    quip: "“Wrong. Probably. Let's check.”", status: "OBJECTING", tone: "magenta" },
  { img: "crew-auditor.webp", name: "The Auditor", role: "Keeps everyone honest",
    line: "Scores predictions against outcomes. Deterministic math, kept calibration.",
    quip: "“Feelings are not a metric.”", status: "SCORING", tone: "gold" },
  { img: "crew-followup.webp", name: "Follow-Up", role: "Never forgets a lead",
    line: "Chases silent threads and lost deals with drafts that wait for your sign-off.",
    quip: "“You said you'd reply. You didn't. I did — well, drafted.”", status: "TRACKING", tone: "cyan" },
  { img: "crew-dev.webp", name: "The Dev", role: "Upgrades the ship",
    line: "Reviews the platform, preps tested patches in isolation. Merges only with your approval.",
    quip: "“It's not broken, it's pre-improved.”", status: "TINKERING", tone: "magenta" },
];

const AWAY = [
  { img: "crew-radar.webp", who: "Radar", doing: "sweeping X for your topics", led: "cw-led-cyan" },
  { img: "crew-strategist.webp", who: "Strategist", doing: "reworking tomorrow's plan", led: "cw-led-magenta" },
  { img: "crew-followup.webp", who: "Follow-Up", doing: "drafting a recovery reply", led: "cw-led-cyan" },
  { img: "crew-dev.webp", who: "Dev", doing: "reviewing the repository", led: "cw-led-magenta" },
  { img: "crew-challenger.webp", who: "Challenger", doing: "attacking an assumption", led: "cw-led-magenta" },
  { img: "crew-auditor.webp", who: "Auditor", doing: "scoring last week's calls", led: "cw-led-gold" },
  { img: "crew-treasury.webp", who: "The Guardian", doing: "watching the vault", led: "cw-led-gold" },
];

const PROVIDERS = [
  { name: "Anthropic", tone: "#e8b34d" }, { name: "OpenAI", tone: "#6fe3a1" },
  { name: "Gemini", tone: "#4f7df6" }, { name: "xAI", tone: "#f1eaff" },
  { name: "Mistral", tone: "#e8734d" }, { name: "DeepSeek", tone: "#35d8e8" },
  { name: "OpenRouter", tone: "#e14fd0" }, { name: "Custom", tone: "#a06ef2" },
];

const DOCKS = [
  { name: "NATIVE", note: "ships with the cantina", status: "ONLINE", led: "cw-led-green", icon: null },
  { name: "OPENCLAW", note: "gateway supported", status: "DOCKED", led: "cw-led-cyan", icon: "/brand/openclaw-pixel-lobster.svg" },
  { name: "HERMES", note: "docks via custom HTTP", status: "AVAILABLE", led: "cw-led-magenta", icon: null },
  { name: "JARVIS", note: "docks via custom HTTP", status: "AVAILABLE", led: "cw-led-magenta", icon: null },
  { name: "CUSTOM", note: "any HTTP agent", status: "OPEN PORT", led: "cw-led-gold", icon: null },
];

const TICKER = [
  "CREW ONLINE — 12 STATIONS", "RADAR: NO MATERIAL CHANGE… AGAIN", "MISSION ACTIVE",
  "VAULT GUARDED", "CHALLENGER FILED AN OBJECTION", "PATCH READY — AWAITING APPROVAL",
  "2 SIGNALS FOUND", "GUARDIAN REJECTED A €150 IMPULSE BUY", "SYSTEM NOMINAL",
  "DOCK 7 — OPEN", "AUDITOR: FEELINGS STILL NOT A METRIC", "EMERGENCY STOP ARMED",
];

const ROUTE = [
  { img: null, label: "You set the mission", sub: "“Reach €5k/month by October.”", tone: "gold" },
  { img: "crew-orchestrator.webp", label: "The Orchestrator takes it", sub: "compiles the goal, briefs the crew", tone: "gold" },
  { img: "crew-strategist.webp", label: "The crew assembles", sub: "Strategist plans · Radar scans · X-Ray digs", tone: "magenta" },
  { img: "crew-treasury.webp", label: "The Guardian checks the spend", sub: "€12 auto · €70 asks you · €150 blocked", tone: "gold" },
  { img: "crew-followup.webp", label: "The mission runs", sub: "drafts prepared, actions wait for your sign-off", tone: "cyan" },
  { img: "crew-auditor.webp", label: "The Auditor closes the loop", sub: "outcome scored, calibration updated, crew learns", tone: "cyan" },
];

function Portrait({ img, size, alt, className = "", eager = false }: {
  img: string; size: number; alt: string; className?: string; eager?: boolean;
}) {
  return (
    <img src={`/brand/${img}`} width={size} height={size} alt={alt}
         loading={eager ? "eager" : "lazy"}
         className={`cw-portrait aspect-square object-cover ${className}`} />
  );
}

const toneText: Record<string, string> = {
  gold: "cw-neon-gold", magenta: "cw-neon-magenta", cyan: "cw-neon-cyan",
};

export default function LandingPage() {
  return (
    <div className="cantina min-h-screen overflow-x-hidden">

      {/* doorman: greets on first visit, its OK click unlocks the radio */}
      <WelcomeGate />

      {/* ══ NAV ══ */}
      <header className="relative z-20 mx-auto flex max-w-7xl items-center justify-between gap-3 px-4 py-4 sm:px-6">
        <Link href="/" className="flex min-w-0 items-center gap-2.5">
          <img src="/brand/logo-mark.webp" alt="Moseisley — the cantina of AI agents"
               width={44} height={44} className="rounded-full border-2 border-[var(--cw-purple)] shadow-[0_0_18px_-2px_rgb(160_110_242/0.8)]" loading="eager" />
          <span className="font-display truncate text-lg font-bold tracking-tight">
            moseisley<span className="text-[var(--cw-magenta)]">.sh</span>
          </span>
        </Link>
        <nav className="flex items-center gap-1 sm:gap-2">
          <Link href="#crew" className="hidden rounded-full px-3 py-2 text-sm text-[var(--cw-mute)] hover:text-[var(--cw-ink)] md:block">The Crew</Link>
          <Link href="#missions" className="hidden rounded-full px-3 py-2 text-sm text-[var(--cw-mute)] hover:text-[var(--cw-ink)] md:block">How it works</Link>
          <Link href="/friends" className="hidden rounded-full px-3 py-2 text-sm text-[var(--cw-mute)] hover:text-[var(--cw-ink)] md:block">Friends</Link>
          <Link href="/pricing" className="hidden rounded-full px-3 py-2 text-sm text-[var(--cw-mute)] hover:text-[var(--cw-purple)] md:block">Pricing</Link>
          <Link href="/login" className="hidden rounded-full border border-[var(--cw-line)] bg-[var(--cw-panel)] px-4 py-2 text-sm text-[var(--cw-ink)] hover:border-[var(--cw-purple)] md:block">
            Log in
          </Link>
          <MobileNav />
        </nav>
      </header>

      {/* ══ HERO — enter the cantina ══ */}
      <section className="relative overflow-hidden">
        <div aria-hidden className="pointer-events-none absolute inset-0"><div className="cw-stars" /></div>
        <div className="relative mx-auto max-w-7xl px-4 pb-8 pt-2 sm:px-6 md:pt-6">
          <div className="grid items-center gap-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-6">

            {/* key art — the world, first */}
            <div className="relative order-first min-w-0 lg:order-last">
              <picture>
                <source media="(max-width: 640px)" srcSet="/brand/hero-cantina-mobile.webp" />
                <img src="/brand/hero-cantina.webp" alt="Inside the Moseisley cantina: the purple Strategist presents a rising holographic trajectory at the mission table while the robot bartender pours drinks, Radar watches the scope, the Dev tinkers, and the Treasury Guardian watches the vault"
                     width={1920} height={814} loading="eager"
                     className="cw-art w-full" />
              </picture>
              <div aria-hidden className="pointer-events-none absolute -bottom-4 left-1/2 h-10 w-3/4 -translate-x-1/2 rounded-full bg-[var(--cw-purple-deep)] opacity-30 blur-2xl" />
            </div>

            {/* messaging */}
            <div className="min-w-0 text-center lg:text-left">
              <p className="cw-neon-magenta mb-3 font-mono text-[11px] uppercase tracking-[0.3em]">
                ▸ the cantina of AI agents ◂
              </p>
              <p className="mb-4 inline-flex items-center gap-2 rounded-full border border-[var(--cw-line)] bg-[var(--cw-panel)]/80 px-4 py-1.5 text-[11px] font-semibold uppercase tracking-widest text-[var(--cw-mute)]">
                <span className="led led-pulse cw-led-cyan" aria-hidden />
                your personal AI command center
              </p>
              <h1 className="font-display text-4xl font-bold leading-[1.05] tracking-tight sm:text-5xl xl:text-6xl">
                Make better decisions.
                <br />
                <span className="cw-title">Change your trajectory.</span>
              </h1>
              <p className="mx-auto mt-5 max-w-md text-base text-[var(--cw-mute)] sm:text-lg lg:mx-0">
                Put the world&apos;s best AI models and agents to work on your personal
                and professional goals.
              </p>
              <p className="mx-auto mt-2 max-w-md text-sm font-medium text-[var(--cw-faint)] lg:mx-0">
                Set the mission. Your AI crew handles the rest.
              </p>
              <div className="mt-7 flex flex-wrap items-center justify-center gap-3 lg:justify-start">
                <Link href="/login"
                      className="cw-cta rounded-full px-7 py-3.5 text-sm font-bold uppercase tracking-wide text-white">
                  Launch your command center
                </Link>
                <Link href="#missions"
                      className="rounded-full border border-[var(--cw-line)] bg-[var(--cw-panel)]/70 px-7 py-3.5 text-sm font-medium text-[var(--cw-ink)] backdrop-blur-sm hover:border-[var(--cw-cyan)]">
                  See how it works
                </Link>
              </div>
            </div>
          </div>
        </div>

        {/* cantina ticker — real product state as the game language */}
        <div className="relative border-y border-[var(--cw-line)] bg-[var(--cw-deep)]/80 py-2.5" aria-hidden>
          <div className="cw-ticker-track">
            {[0, 1].map((half) => (
              <div key={half} className="flex shrink-0 items-center">
                {TICKER.map((t, i) => (
                  <span key={`${half}-${i}`} className="mx-6 flex items-center gap-2 whitespace-nowrap font-mono text-[11px] uppercase tracking-widest text-[var(--cw-mute)]">
                    <span className={`led ${["cw-led-cyan", "cw-led-magenta", "cw-led-gold", "cw-led-green"][i % 4]}`} />
                    {t}
                  </span>
                ))}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ══ MEET THE REGULARS ══ */}
      <section id="crew" className="relative py-16 md:py-24">
        <div className="mx-auto max-w-7xl px-4 sm:px-6">
          <h2 className="font-display text-center text-3xl font-bold sm:text-4xl">
            Meet <span className="cw-title">the regulars</span>.
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-center text-[15px] text-[var(--cw-mute)]">
            One Orchestrator, a crew of specialists. Every one of them is a real,
            configurable worker in the product — with its own prompt, model and permissions.
          </p>

          <div className="mt-12 grid grid-cols-2 gap-x-4 gap-y-10 sm:grid-cols-3 lg:grid-cols-4">
            {CREW.map((c) => (
              <div key={c.name} className={`cw-char group text-center ${c.featured ? "col-span-2 sm:col-span-1" : ""}`}>
                <div className="relative mx-auto w-fit">
                  <Portrait img={c.img} size={c.featured ? 232 : 200} alt={`${c.name} — ${c.role}`}
                            className={`mx-auto w-40 sm:w-44 lg:w-52 ${c.featured ? "w-48 sm:w-52 lg:w-60" : ""} cw-float`} />
                  <span className={`absolute -bottom-1 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full border border-[var(--cw-line)] bg-[var(--cw-deep)] px-3 py-1 font-mono text-[10px] font-bold uppercase tracking-widest ${toneText[c.tone]}`}>
                    <span className="led led-pulse mr-1.5 inline-block cw-led-cyan" aria-hidden />{c.status}
                  </span>
                </div>
                <h3 className="font-display mt-5 text-lg font-bold">{c.name}</h3>
                <div className="text-[13px] font-semibold text-[var(--cw-purple)]">{c.role}</div>
                <p className="mx-auto mt-1.5 max-w-[26ch] text-[13px] leading-relaxed text-[var(--cw-mute)]">{c.line}</p>
                <p className="mx-auto mt-1.5 max-w-[26ch] text-[12px] italic text-[var(--cw-faint)] opacity-0 transition group-hover:opacity-100">
                  {c.quip}
                </p>
              </div>
            ))}
          </div>
          <p className="mt-10 text-center text-xs text-[var(--cw-faint)]">
            Model policy per crew member: inherit the Orchestrator&apos;s brain, or give any of
            them their own. The Treasury Guardian below is different — deterministic code, not an LLM.
          </p>
        </div>
      </section>

      {/* ══ HOW MISSIONS WORK ══ */}
      <section id="missions" className="relative border-t border-[var(--cw-line)] py-16 md:py-24">
        <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden"><div className="cw-stars" /></div>
        <div className="relative mx-auto max-w-3xl px-4 sm:px-6">
          <h2 className="font-display text-center text-3xl font-bold sm:text-4xl">
            How <span className="cw-title">missions</span> work.
          </h2>
          <p className="mx-auto mt-3 max-w-lg text-center text-[15px] text-[var(--cw-mute)]">
            A mission chip travels through the cantina. Watch it go.
          </p>

          <div className="relative mx-auto mt-12 max-w-xl">
            {/* the route + travelling chip */}
            <div aria-hidden className="cw-route-line absolute bottom-6 left-[34px] top-6 sm:left-1/2 sm:-translate-x-1/2" />
            <div aria-hidden className="cw-mission-chip absolute left-[27px] h-4 w-4 rounded-full bg-[var(--cw-cyan)] sm:left-1/2 sm:-ml-2" />
            <ol className="space-y-9">
              {ROUTE.map((s, i) => (
                <li key={s.label} className={`relative flex items-center gap-5 sm:w-[calc(50%+2.5rem)] ${i % 2 ? "sm:ml-auto sm:flex-row" : "sm:flex-row-reverse sm:text-right"}`}>
                  {s.img ? (
                    <Portrait img={s.img} size={72} alt="" className="w-[68px] shrink-0" />
                  ) : (
                    <span className="flex h-[68px] w-[68px] shrink-0 items-center justify-center rounded-full border-[3px] border-[var(--cw-gold)]/70 bg-[var(--cw-panel)] font-display text-xl font-bold text-[var(--cw-gold)] shadow-[0_0_24px_-6px_rgb(232_179_77/0.7)]">
                      YOU
                    </span>
                  )}
                  <div className="min-w-0">
                    <div className={`font-display text-base font-bold ${toneText[s.tone]}`}>{s.label}</div>
                    <div className="text-[13px] text-[var(--cw-mute)]">{s.sub}</div>
                  </div>
                </li>
              ))}
            </ol>
          </div>
          <p className="mt-10 text-center text-xs text-[var(--cw-faint)]">
            Bounded runs, never an infinite loop. Every step lands in an append-only ledger.
          </p>
        </div>
      </section>

      {/* ══ WHILE YOU'RE GONE ══ */}
      <section className="relative border-t border-[var(--cw-line)] py-16 md:py-24">
        <div className="mx-auto max-w-6xl px-4 sm:px-6">
          <h2 className="font-display text-center text-3xl font-bold sm:text-4xl">
            While you&apos;re gone, <span className="cw-title">the cantina stays open</span>.
          </h2>
          <div className="mt-12 flex flex-wrap items-stretch justify-center gap-4">
            {AWAY.map((a, i) => (
              <div key={a.who}
                   className={`cw-panel flex min-w-0 items-center gap-3.5 px-4 py-3 ${i % 2 ? "cw-float" : "cw-float-slow"}`}
                   style={{ animationDelay: `${i * 0.7}s` }}>
                <Portrait img={a.img} size={56} alt="" className="w-14 shrink-0" />
                <div className="min-w-0">
                  <div className="flex items-center gap-2 font-display text-sm font-bold">
                    <span className={`led led-pulse ${a.led}`} aria-hidden />{a.who}
                  </div>
                  <div className="text-[12.5px] text-[var(--cw-mute)]">{a.doing}</div>
                </div>
              </div>
            ))}
          </div>
          <p className="mx-auto mt-10 max-w-lg text-center text-sm text-[var(--cw-mute)]">
            Scheduled sweeps, reviews and follow-ups run on your timetable — and everything
            that costs money or acts on the outside world waits for your approval.
          </p>
        </div>
      </section>

      {/* ══ CHOOSE YOUR BRAINS ══ */}
      <section className="relative border-t border-[var(--cw-line)] py-16 md:py-24">
        <div className="mx-auto max-w-6xl px-4 sm:px-6">
          <div className="grid items-center gap-10 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
            <div className="text-center lg:text-left">
              <div className="relative mx-auto w-fit lg:mx-0">
                <Portrait img="crew-bartender.webp" size={200} alt="The bartender droid mixing glowing drinks" className="w-44 sm:w-52 cw-float" />
                <span aria-hidden className="cw-bubble left-10 top-16 h-2 w-2" />
                <span aria-hidden className="cw-bubble left-14 top-20 h-1.5 w-1.5" style={{ animationDelay: "1.2s" }} />
                <span aria-hidden className="cw-bubble left-8 top-24 h-1 w-1" style={{ animationDelay: "2.3s" }} />
              </div>
              <h2 className="font-display mt-6 text-3xl font-bold sm:text-4xl">
                Choose <span className="cw-title">your brains</span>.
              </h2>
              <p className="mx-auto mt-3 max-w-md text-[15px] text-[var(--cw-mute)] lg:mx-0">
                The bartender serves whatever you bring. Pick the model that runs your
                Orchestrator — and give any crew member a different one.
              </p>
              <p className="mx-auto mt-3 max-w-md rounded-2xl border border-[var(--cw-gold)]/40 bg-[var(--cw-gold)]/5 px-4 py-2.5 text-[13px] text-[var(--cw-mute)] lg:mx-0">
                <span className="font-semibold text-[var(--cw-gold)]">Built-in AI, or your own keys.</span>{" "}
                Hosted passes come with AI included. Connect your own provider accounts
                whenever you want — that usage is billed by the provider.
              </p>
            </div>
            <div className="cw-panel cw-panel-cyan p-6 sm:p-8">
              <div className="mb-5 text-center font-mono text-[10px] uppercase tracking-[0.3em] text-[var(--cw-faint)]">
                tonight&apos;s menu — all brains welcome
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-6 sm:grid-cols-4">
                {PROVIDERS.map((p) => (
                  <div key={p.name} className="cw-bottle px-3 pb-3 pt-5 text-center">
                    <div aria-hidden className="mx-auto mb-2 h-8 w-4 rounded-md"
                         style={{ background: `linear-gradient(180deg, ${p.tone}cc, ${p.tone}55)`,
                                  boxShadow: `0 0 14px -2px ${p.tone}` }} />
                    <div className="truncate text-[13px] font-semibold">{p.name}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* docking bay */}
          <div className="cw-panel mt-10 p-6 sm:p-8">
            <div className="mb-5 flex flex-wrap items-center justify-between gap-2">
              <h3 className="font-display text-xl font-bold">
                Docking bay <span className="cw-neon-cyan">07</span> — outside agents welcome
              </h3>
              <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--cw-faint)]">
                they get missions & sanitized context — never your keys, never the vault
              </span>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
              {DOCKS.map((d) => (
                <div key={d.name} className="rounded-2xl border border-[var(--cw-line)] bg-[var(--cw-deep)]/70 px-4 py-3 text-center">
                  <div className="flex items-center justify-center gap-2">
                    {d.icon && <img src={d.icon} alt="" width={20} height={20} loading="lazy" />}
                    <span className="font-mono text-sm font-bold tracking-[0.15em]">{d.name}</span>
                  </div>
                  <div className="mt-1 text-[11px] text-[var(--cw-faint)]">{d.note}</div>
                  <div className="mt-1.5 flex items-center justify-center gap-1.5 font-mono text-[10px] uppercase tracking-widest text-[var(--cw-mute)]">
                    <span className={`led led-pulse ${d.led}`} aria-hidden />{d.status}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ══ RADAR ROOM ══ */}
      <section className="relative border-t border-[var(--cw-line)] py-16 md:py-24">
        <div className="mx-auto max-w-6xl px-4 sm:px-6">
          <div className="grid items-center gap-10 lg:grid-cols-2">
            <div className="order-2 lg:order-1">
              <div className="cw-panel cw-panel-cyan p-6">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--cw-line)] pb-3">
                  <span className="font-display text-base font-bold">Morning brief</span>
                  <span className="rounded-full border border-[var(--cw-gold)]/50 px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-[var(--cw-gold)]">example</span>
                </div>
                <div className="mt-4 space-y-3 text-sm">
                  <div className="flex items-start gap-2.5">
                    <span className="cw-neon-cyan font-mono text-xs">01</span>
                    <p className="text-[var(--cw-mute)]"><span className="font-semibold text-[var(--cw-ink)]">New agent runtime shipped</span> — three competitor threads gaining real traction overnight.</p>
                  </div>
                  <div className="flex items-start gap-2.5">
                    <span className="cw-neon-cyan font-mono text-xs">02</span>
                    <p className="text-[var(--cw-mute)]"><span className="font-semibold text-[var(--cw-ink)]">Users complaining about pricing</span> in your niche — possible opening for your offer.</p>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-[var(--cw-line)] pt-3">
                    <span className="font-mono text-[11px] uppercase tracking-widest text-[var(--cw-faint)]">sentiment</span>
                    <span className="rounded-full bg-[var(--cw-cyan)]/10 px-3 py-1 font-mono text-[11px] font-bold uppercase tracking-widest text-[var(--cw-cyan)]">mixed → positive</span>
                    <span className="text-[11px] text-[var(--cw-faint)]">evidence-based — never invented percentages</span>
                  </div>
                </div>
              </div>
            </div>
            <div className="order-1 text-center lg:order-2 lg:text-left">
              <Portrait img="crew-radar.webp" size={224} alt="Radar, the amphibian signal operator" className="mx-auto w-48 sm:w-56 cw-float lg:mx-0" />
              <h2 className="font-display mt-6 text-3xl font-bold sm:text-4xl">
                The <span className="cw-title">radar room</span> never sleeps.
              </h2>
              <p className="mx-auto mt-3 max-w-md text-[15px] text-[var(--cw-mute)] lg:mx-0">
                Tell Radar what to watch on X — topics, accounts, competitors. Every
                morning: only the changes that matter, straight to your Telegram.
                On quiet days it says the honest thing: “no material change.”
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ══ DEV WORKSHOP + THE VAULT ══ */}
      <section className="relative border-t border-[var(--cw-line)] py-16 md:py-24">
        <div className="mx-auto grid max-w-6xl gap-10 px-4 sm:px-6 lg:grid-cols-2">
          {/* dev workshop */}
          <div className="cw-panel cw-panel-magenta p-6 sm:p-8">
            <div className="flex items-center gap-4">
              <Portrait img="crew-dev.webp" size={110} alt="The Dev, four-armed mechanic" className="w-24 shrink-0 sm:w-28" />
              <div>
                <h3 className="font-display text-2xl font-bold">The <span className="cw-neon-magenta">workshop</span></h3>
                <p className="mt-1 text-[13.5px] text-[var(--cw-mute)]">
                  The Dev reviews the platform, spots friction, and preps improvements.
                </p>
              </div>
            </div>
            <ol className="mt-5 space-y-2.5 text-sm text-[var(--cw-mute)]">
              <li className="flex gap-2.5"><span className="cw-neon-magenta font-mono text-xs">1</span> Proposes a change — with evidence and a plan.</li>
              <li className="flex gap-2.5"><span className="cw-neon-magenta font-mono text-xs">2</span> Builds the patch in an isolated branch. Never on main.</li>
              <li className="flex gap-2.5"><span className="cw-neon-magenta font-mono text-xs">3</span> Runs the tests there. Shows you the results.</li>
              <li className="flex gap-2.5"><span className="cw-neon-magenta font-mono text-xs">4</span> Waits. Nothing merges without your approval — bound to the exact patch.</li>
            </ol>
            <div className="mt-5 flex items-center gap-3">
              <span className="rounded-full bg-[var(--cw-magenta)]/15 px-4 py-2 font-mono text-[11px] font-bold uppercase tracking-widest text-[var(--cw-magenta)]">
                ⚙ patch ready — tests passed
              </span>
              <span className="rounded-full border border-[var(--cw-line)] px-4 py-2 font-mono text-[11px] uppercase tracking-widest text-[var(--cw-faint)]">
                approve patch →
              </span>
            </div>
          </div>

          {/* the vault */}
          <div className="cw-panel cw-panel-gold p-6 sm:p-8">
            <div className="flex items-center gap-4">
              <Portrait img="crew-treasury.webp" size={110} alt="The Treasury Guardian robot" className="w-24 shrink-0 sm:w-28" />
              <div>
                <h3 className="font-display text-2xl font-bold">The <span className="cw-neon-gold">vault</span></h3>
                <p className="mt-1 text-[13.5px] text-[var(--cw-mute)]">
                  The Guardian isn&apos;t an AI. It&apos;s deterministic code no prompt can sweet-talk.
                </p>
              </div>
            </div>
            <div className="mt-5 grid grid-cols-3 gap-3 text-center">
              {[["€12", "AUTO", "cw-led-green"], ["€70", "ASK OWNER", "cw-led-gold"], ["€150", "BLOCKED", "cw-led-magenta"]].map(([amt, verdict, led]) => (
                <div key={amt} className="rounded-2xl border border-[var(--cw-line)] bg-[var(--cw-deep)]/70 px-2 py-4">
                  <div className="font-display text-xl font-bold">{amt}</div>
                  <div className="mt-1.5 flex items-center justify-center gap-1.5 font-mono text-[10px] font-bold uppercase tracking-widest text-[var(--cw-mute)]">
                    <span className={`led ${led}`} aria-hidden />{verdict}
                  </div>
                </div>
              ))}
            </div>
            <ul className="mt-5 space-y-1.5 text-[13px] text-[var(--cw-mute)]">
              <li>· Approvals on your dashboard and Telegram</li>
              <li>· Every action in an append-only ledger</li>
              <li>· And one big red button that stops the whole machine:</li>
            </ul>
            <div className="mt-4 flex justify-center">
              <span className="rounded-full border-4 border-[#2a1020] bg-gradient-to-b from-[#f0758a] to-[#c9384a] px-8 py-3 font-display text-sm font-black uppercase tracking-[0.2em] text-white shadow-[0_0_30px_-4px_rgb(224_90_110/0.8)]">
                ■ emergency stop
              </span>
            </div>
          </div>
        </div>
      </section>

      {/* ══ ACTIVE MISSIONS (EXAMPLE) ══ */}
      <section className="relative border-t border-[var(--cw-line)] py-16 md:py-24">
        <div className="mx-auto max-w-6xl px-4 sm:px-6">
          <h2 className="font-display text-center text-3xl font-bold sm:text-4xl">
            Your projects become <span className="cw-title">missions</span>.
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-center text-[15px] text-[var(--cw-mute)]">
            Each activity your crew operates gets real, verified numbers — revenue with
            provenance, capital actually deployed, AI cost, agent runtime. Never estimates
            dressed up as results.
          </p>
          <div className="mx-auto mt-10 grid max-w-4xl gap-5 sm:grid-cols-2">
            {[
              { name: "Example SaaS", crew: ["crew-strategist.webp", "crew-radar.webp", "crew-dev.webp"],
                stats: [["Verified MRR", "€20", "cw-neon-cyan"], ["Capital deployed", "€75", ""], ["AI cost", "$3.42", ""]] },
              { name: "Consulting funnel", crew: ["crew-followup.webp", "crew-xray.webp"],
                stats: [["Verified revenue", "€500", "cw-neon-cyan"], ["Leads recovered", "3", ""], ["AI cost", "$1.88", ""]] },
            ].map((m) => (
              <div key={m.name} className="cw-panel p-6">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-display text-lg font-bold">{m.name}</span>
                  <span className="rounded-full border border-[var(--cw-gold)]/50 px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-[var(--cw-gold)]">example</span>
                </div>
                <div className="mt-3 flex items-center gap-1.5">
                  <span className="mr-1 text-[11px] uppercase tracking-widest text-[var(--cw-faint)]">crew:</span>
                  {m.crew.map((c) => (
                    <img key={c} src={`/brand/${c}`} alt="" width={34} height={34} loading="lazy"
                         className="cw-portrait w-8 border-2" />
                  ))}
                </div>
                <div className="mt-4 grid grid-cols-3 gap-2">
                  {m.stats.map(([label, value, cls]) => (
                    <div key={label}>
                      <div className="text-[10px] uppercase tracking-widest text-[var(--cw-faint)]">{label}</div>
                      <div className={`font-display text-lg font-bold ${cls}`}>{value}</div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <p className="mt-6 text-center text-[11px] text-[var(--cw-faint)]">
            Demo data shown as an example of the portfolio view — not real user results.
          </p>
        </div>
      </section>

      {/* ══ CANTINA PASSES ══ */}
      <section id="passes" className="relative border-t border-[var(--cw-line)] py-16 md:py-24">
        <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden"><div className="cw-stars" /></div>
        <div className="relative mx-auto max-w-6xl px-4 sm:px-6">
          <h2 className="font-display text-center text-3xl font-bold sm:text-4xl">
            Pick your <span className="cw-title">cantina pass</span>.
          </h2>
          <p className="mx-auto mt-4 max-w-2xl rounded-2xl border border-[var(--cw-gold)]/40 bg-[var(--cw-gold)]/5 px-5 py-3 text-center text-sm text-[var(--cw-mute)]">
            <span className="font-semibold text-[var(--cw-gold)]">
              Every hosted pass starts with a 14-day free trial — AI included, no API key needed.
            </span>{" "}
            After that: keep our built-in AI, or connect your own keys and models
            (OpenAI, Anthropic, Ollama…). Your own provider usage is billed by that provider.
          </p>
          <div className="mx-auto mt-10 grid max-w-4xl gap-5 md:grid-cols-3">
            {[
              // Daily request numbers mirror FACTORY_BASIC_DAILY_REQUESTS (150) and
              // FACTORY_PRO_DAILY_REQUESTS (400) in backend/core/config.py — keep in sync.
              { name: "Community", price: "$0", tag: "self-hosted", per: "free forever",
                priceNote: "No credit card required",
                perks: ["Open-source", "Self-hosted", "Full product, your machine", "BYOK · unlimited with your keys"],
                cta: "Deploy it yourself", ctaNote: "", highlight: false },
              { name: "Basic", price: "$9", tag: "hosted", per: "per month",
                priceNote: "",
                perks: ["Built-in AI included · 150 AI requests/day", "Or bring your own keys & Ollama", "Hosted cantina", "Command center + chat + goals", "Persistent memory + JSON export", "Model selection + OpenRouter", "Core crew · ledger · emergency stop"],
                cta: "Start free trial", ctaNote: "14 days free · then $9/month", highlight: false },
              { name: "Pro", price: "$19", tag: "hosted", per: "per month",
                priceNote: "",
                perks: ["Everything in Basic", "Bigger fuel tank · 400 AI requests/day", "Full crew + Telegram", "X-Ray + Strategist + Challenger", "Market radar + experiments", "Scheduled autonomy + Treasury"],
                cta: "Start free trial", ctaNote: "14 days free · then $19/month", highlight: true },
            ].map((t) => (
              <Link key={t.name} href="/pricing"
                    className={`cw-panel block p-6 text-center transition hover:-translate-y-1 ${t.highlight ? "cw-panel-magenta" : ""}`}>
                <img src="/brand/logo-mark.webp" alt="" width={40} height={40} loading="lazy"
                     className={`mx-auto rounded-full ${t.highlight ? "" : "opacity-60 grayscale-[30%]"}`} />
                <div className="mt-2 font-mono text-[10px] uppercase tracking-[0.3em] text-[var(--cw-faint)]">{t.tag}</div>
                <div className="font-display mt-1 text-xl font-bold">{t.name} pass</div>
                <div className="mt-1">
                  <span className={`font-display text-3xl font-black ${t.highlight ? "cw-neon-magenta" : ""}`}>{t.price}</span>
                  <span className="ml-1.5 text-xs text-[var(--cw-faint)]">{t.per}</span>
                </div>
                {t.priceNote && (
                  <div className="mt-1 text-[11px] text-[var(--cw-faint)]">{t.priceNote}</div>
                )}
                <ul className="mt-4 space-y-1.5 text-[13px] text-[var(--cw-mute)]">
                  {t.perks.map((p) => <li key={p}>{p}</li>)}
                </ul>
                <div className={`mt-5 rounded-full px-4 py-2.5 text-sm font-bold ${t.highlight ? "cw-cta text-white" : "border border-[var(--cw-line)] text-[var(--cw-ink)]"}`}>
                  {t.cta}
                </div>
                {t.ctaNote && (
                  <div className="mt-2 text-[11px] text-[var(--cw-faint)]">{t.ctaNote}</div>
                )}
              </Link>
            ))}
          </div>
        </div>
      </section>

      {/* ══ FINAL CTA — the door is open ══ */}
      <section className="relative overflow-hidden border-t border-[var(--cw-line)]">
        <img src="/brand/exterior-night.webp"
             alt="The Moseisley cantina at night — neon sign glowing, door open, a ringed planet overhead"
             width={1600} height={893} loading="lazy"
             className="h-[420px] w-full object-cover object-center sm:h-[520px]" />
        <div className="absolute inset-0 bg-gradient-to-t from-[var(--cw-black)] via-[rgb(7_4_15/0.55)] to-transparent" />
        <div className="absolute inset-x-0 bottom-0 pb-14 text-center">
          <img src="/brand/logo-mark.webp" alt="" width={64} height={64} loading="lazy"
               className="mx-auto mb-4 rounded-full border-2 border-[var(--cw-purple)] shadow-[0_0_28px_-2px_rgb(160_110_242/0.9)]" />
          <h2 className="font-display px-4 text-3xl font-black sm:text-5xl">
            Your crew is <span className="cw-title">waiting inside</span>.
          </h2>
          <div className="mt-6">
            <Link href="/login" className="cw-cta rounded-full px-8 py-4 text-sm font-bold uppercase tracking-wide text-white">
              Launch your command center
            </Link>
          </div>
          <p className="mt-4 text-[12px] text-[var(--cw-mute)]">
            Self-hostable · your data stays yours · one red button stops everything
          </p>
        </div>
      </section>

      <footer className="relative border-t border-[var(--cw-line)] py-8">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 text-[12px] text-[var(--cw-faint)] sm:px-6">
          <span>moseisley.sh — the cantina of AI agents</span>
          <div className="flex flex-wrap gap-4">
            <Link href="/friends" className="hover:text-[var(--cw-mute)]">Friends</Link>
            <Link href="/pricing" className="hover:text-[var(--cw-mute)]">Pricing</Link>
            <Link href="/legal" className="hover:text-[var(--cw-mute)]">Legal</Link>
            <a href="mailto:cantina@moseisley.sh" className="hover:text-[var(--cw-mute)]">
              Contact the Cantina: cantina@moseisley.sh
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}
