/* eslint-disable @next/next/no-img-element */
import Link from "next/link";

export const metadata = { title: "Legal" };

const SUPPORT = "cantina@moseisley.sh";

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="border-t border-[var(--cw-line)] py-8">
      <h2 className="font-display text-lg font-bold">{title}</h2>
      <div className="cw-mute mt-3 space-y-3 text-sm leading-relaxed">{children}</div>
    </section>
  );
}

function SupportLine() {
  return (
    <p>
      Support: <a href={`mailto:${SUPPORT}`} className="text-[var(--cw-cyan)] hover:underline">{SUPPORT}</a>
    </p>
  );
}

export default function LegalPage() {
  return (
    <div className="cantina min-h-screen overflow-x-hidden">
      <header className="relative z-20 mx-auto flex max-w-3xl items-center justify-between gap-3 px-4 py-4 sm:px-6">
        <Link href="/" className="flex min-w-0 items-center gap-2.5">
          <img src="/brand/logo-mark.webp" alt="Moseisley — the cantina of AI agents"
               width={44} height={44} loading="eager"
               className="rounded-full border-2 border-[var(--cw-purple)] shadow-[0_0_18px_-2px_rgb(160_110_242/0.8)]" />
          <span className="font-display truncate text-lg font-bold tracking-tight">
            moseisley<span className="text-[var(--cw-magenta)]">.sh</span>
          </span>
        </Link>
        <Link href="/" className="cw-mute rounded-full border border-[var(--cw-line)] bg-[var(--cw-panel)] px-4 py-2 text-sm hover:border-[var(--cw-purple)]">← back</Link>
      </header>
      <main className="relative mx-auto max-w-3xl px-4 pb-16 sm:px-6">
        <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden"><div className="cw-stars" /></div>
        <h1 className="font-display relative text-2xl font-bold sm:text-3xl">
          <span className="cw-title">Legal</span>
        </h1>
        <p className="cw-mute relative mt-2 text-sm">
          Legal Notice · Terms of Service · Privacy Policy · Cancellation
        </p>

        <div className="relative">
        <Section id="legal-notice" title="Legal Notice (Mentions légales)">
          <p>
            Moseisley.sh is published and operated by <strong className="cw-ink">Benoit
            Sartre, EI</strong> (Entreprise Individuelle / micro-entreprise under French
            law), SIRET 915&nbsp;385&nbsp;124&nbsp;00010, 66 Avenue des
            Champs-Elysées, 75008 Paris, France.
          </p>
          <p>
            Publication director: Benoit Sartre. Hosting provider details: to be
            completed by the operator at production deployment.
          </p>
          <SupportLine />
        </Section>

        <Section id="terms" title="Terms of Service">
          <p>
            Moseisley.sh provides a personal AI command center: you connect your own AI
            provider accounts and data sources, and coordinate AI agents that work within
            the limits you configure (permissions, budgets, kill switches).
          </p>
          <p>
            You remain responsible for the instructions you give your crew, for the
            provider accounts you connect, and for reviewing actions that require your
            approval. AI outputs may be imperfect; verified and estimated values are
            labeled as such and estimates are not guarantees.
          </p>
          <p>
            The Community edition is provided free and self-hosted, without warranty.
            Hosted subscriptions (Basic $9/month, Pro $19/month) are billed through
            Stripe; billing terms and the current price are confirmed at checkout.
            Subscriptions cover the hosted Moseisley service only: AI provider usage is
            billed separately by your chosen provider using your own API key and is not
            included in any Moseisley subscription.
          </p>
          <SupportLine />
        </Section>

        <Section id="privacy" title="Privacy Policy">
          <p>
            Your canonical data (goals, memory, documents, ledger) is stored in the
            application database and is yours: it can be exported as JSON and Markdown at
            any time, and disconnecting an integration can purge its derived data.
          </p>
          <p>
            Provider API keys are encrypted at rest and never shown again after saving,
            never logged, and never shared with external agents. Message content you send
            to your crew is transmitted to the AI provider you configured, under that
            provider's terms. We do not sell your data.
          </p>
          <SupportLine />
        </Section>

        <Section id="cancellation" title="Cancellation / Withdrawal">
          <p>
            Paid subscriptions (Basic, Pro) can be canceled at any time from Settings → Billing →
            Manage subscription (Stripe Customer Portal); the subscription then ends at
            the current billing period. Consumers in the EU may exercise their statutory
            withdrawal right within 14 days of purchase by contacting us.
          </p>
          <p>
            Contact the Cantina:{" "}
            <a href={`mailto:${SUPPORT}`} className="text-[var(--cw-cyan)] hover:underline">{SUPPORT}</a>
          </p>
        </Section>
        </div>
      </main>
      <footer className="relative border-t border-[var(--cw-line)] py-8">
        <div className="cw-faint mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-2 px-4 font-mono text-[11px] sm:px-6">
          <span>moseisley.sh</span>
          <a href={`mailto:${SUPPORT}`} className="hover:text-[var(--cw-mute)]">Support: {SUPPORT}</a>
        </div>
      </footer>
    </div>
  );
}
