"use client";
/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { API_URL, setToken } from "@/lib/api";
import { HOME_PATH } from "@/lib/routes";
import { Wordmark } from "@/components/brand";
import { Button, ErrorBox, Input } from "@/components/ui";

/* Shared access form behind both /login and /register. Validation is explicit
   and visible: the submit button is never disabled by a predicate the user
   can't see — it disables only while a request is in flight. */

export type Mode = "login" | "register" | "forgot";

const MODE_LABELS: Record<Mode, string> = {
  login: "access · log in",
  register: "access · new account",
  forgot: "access · password recovery",
};

const SUBMIT_LABELS: Record<Mode, string> = {
  login: "Log in",
  register: "Create account",
  forgot: "Send reset link",
};

const MIN_PASSWORD = 8;
/* The overlay is reassurance, not a progress bar: registration is fast now, so
   hold it long enough to be read instead of letting it strobe. */
const MIN_OVERLAY_MS = 2500;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function validate(mode: Mode, email: string, password: string): string | null {
  if (!email.trim()) return "Enter your email address.";
  if (!EMAIL_RE.test(email.trim())) return "That doesn't look like a valid email address.";
  if (mode === "forgot") return null;
  if (!password) return "Enter a password.";
  if (password.length < MIN_PASSWORD) {
    return `Password must be at least ${MIN_PASSWORD} characters (yours is ${password.length}).`;
  }
  return null;
}

const PREPARING_LINES = [
  "Aliens warming up the kitchen 🍳",
  "Tuning the radio…",
  "Briefing your crew…",
  "Polishing the good glasses…",
  "Waking the Orchestrator…",
];

function PreparingOverlay() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setI((n) => (n + 1) % PREPARING_LINES.length), 1600);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="cantina fixed inset-0 z-50 flex flex-col items-center justify-center gap-5 bg-[var(--cw-black)] p-6 text-center"
         role="status" aria-live="polite">
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden"><div className="cw-stars" /></div>
      <img src="/brand/logo-mark.webp" alt="" width={72} height={72}
           className="cw-portrait relative w-[72px] animate-pulse" />
      <p className="relative font-display text-xl font-bold text-[var(--cw-ink)] sm:text-2xl">
        The Cantina crew is preparing your table…
      </p>
      <p key={i} className="relative font-mono text-[12px] uppercase tracking-widest text-[var(--cw-mute)]">
        {PREPARING_LINES[i]}
      </p>
      <div aria-hidden className="relative flex gap-1.5">
        {[0, 1, 2].map((d) => (
          <span key={d}
                className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--cw-magenta)]"
                style={{ animationDelay: `${d * 0.15}s` }} />
        ))}
      </div>
    </div>
  );
}

export function AuthForm({ initialMode = "login" }: { initialMode?: Mode }) {
  const router = useRouter();
  const params = useSearchParams();
  const [mode, setMode] = useState<Mode>(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [touched, setTouched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(
    params.get("reset") === "success"
      ? "Password updated. Log in with your new password."
      : null
  );
  const [busy, setBusy] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const preparingSince = useRef(0);

  // shown only once the user has interacted, so the form never nags on load
  const hint = touched ? validate(mode, email, password) : null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    const problem = validate(mode, email, password);
    if (problem) { setError(problem); return; }   // explain, never silently do nothing

    setBusy(true);
    setError(null);
    try {
      if (mode === "forgot") {
        await fetch(`${API_URL}/api/auth/forgot-password`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email }),
        });
        // generic on purpose: never reveal whether the account exists
        setInfo("If an account exists for this email, we've sent a password reset link.");
        setMode("login");
        return;
      }
      if (mode === "register") {
        const resp = await fetch(`${API_URL}/api/auth/register`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password }),
        });
        if (!resp.ok) {
          const data = await resp.json().catch(() => ({}));
          const detail = typeof data.detail === "string" ? data.detail : null;
          throw new Error(
            detail === "REGISTER_USER_ALREADY_EXISTS"
              ? "An account already exists for this email — log in instead."
              : detail || "Registration failed. Please try again."
          );
        }
        setInfo("Account created. A verification email is on its way — logging you in…");
        preparingSince.current = Date.now();
        setPreparing(true);   // covers only the real wait: login + first dashboard paint
      }
      const form = new URLSearchParams({ username: email, password });
      const resp = await fetch(`${API_URL}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: form.toString(),
      });
      if (!resp.ok) throw new Error("Invalid email or password");
      const data = await resp.json();
      setToken(data.access_token);
      // never dismiss the overlay before it has been on screen long enough to read
      if (preparingSince.current) {
        const left = MIN_OVERLAY_MS - (Date.now() - preparingSince.current);
        if (left > 0) await new Promise((r) => setTimeout(r, left));
      }
      // new accounts go through Crew Genesis first; returning users land on the
      // Manager conversation, which is home
      router.replace(mode === "register" ? "/welcome" : HOME_PATH);
    } catch (e) {
      setPreparing(false);
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  if (preparing) return <PreparingOverlay />;

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <Link href="/">
            <Wordmark size="text-2xl" />
          </Link>
          <p className="mt-2 text-sm text-ink-mute">
            {mode === "register"
              ? "Create your account — 14-day free trial, AI included."
              : "Your AI crew, working for you."}
          </p>
        </div>
        <form onSubmit={submit} noValidate
              className="space-y-3 rounded-md border border-line bg-panel/80 p-5">
          <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
            {MODE_LABELS[mode]}
          </div>
          <Input type="email" placeholder="you@company.com" value={email}
                 autoComplete="email" aria-label="Email address"
                 onBlur={() => setTouched(true)}
                 onChange={(e) => setEmail(e.target.value)} />
          {mode !== "forgot" && (
            <Input type="password" placeholder={`Password (${MIN_PASSWORD}+ characters)`}
                   autoComplete={mode === "register" ? "new-password" : "current-password"}
                   aria-label="Password"
                   value={password}
                   onBlur={() => setTouched(true)}
                   onChange={(e) => setPassword(e.target.value)} />
          )}
          {mode === "forgot" && (
            <p className="text-xs text-ink-mute">
              We&apos;ll email you a link to choose a new password. The link expires in 60 minutes.
            </p>
          )}
          {hint && !error && <p className="text-xs text-warn">{hint}</p>}
          {error && <ErrorBox message={error} />}
          {info && <div className="text-xs text-ok">{info}</div>}
          <Button type="submit" disabled={busy} className="w-full">
            {busy ? "Working…" : SUBMIT_LABELS[mode]}
          </Button>
          <div className="flex items-center justify-between">
            {mode === "register" ? (
              <Link href="/login" className="min-h-[32px] text-xs text-ink-faint hover:text-ink-mute">
                Have an account? Log in
              </Link>
            ) : (
              <Link href="/register" className="min-h-[32px] text-xs text-ink-faint hover:text-ink-mute">
                No account? Create one
              </Link>
            )}
            {mode === "login" && (
              <button
                type="button"
                onClick={() => { setMode("forgot"); setError(null); setInfo(null); setTouched(false); }}
                className="min-h-[32px] text-xs text-ink-faint hover:text-ink-mute"
              >
                Forgot password?
              </button>
            )}
          </div>
        </form>
        <p className="text-center text-xs text-ink-faint">
          <Link href="/" className="hover:text-ink-mute">← back to moseisley.sh</Link>
        </p>
      </div>
    </div>
  );
}
