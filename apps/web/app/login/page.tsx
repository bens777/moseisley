"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { API_URL, setToken } from "@/lib/api";
import { Wordmark } from "@/components/brand";
import { Button, ErrorBox, Input } from "@/components/ui";

type Mode = "login" | "register" | "forgot";

const MODE_LABELS: Record<Mode, string> = {
  login: "access · log in",
  register: "access · new account",
  forgot: "access · password recovery",
};

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(
    params.get("reset") === "success"
      ? "Password updated. Log in with your new password."
      : null
  );
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
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
          throw new Error(typeof data.detail === "string" ? data.detail : "Registration failed");
        }
        setInfo("Account created. A verification email was sent — you can log in now.");
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
      router.replace("/command");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <Link href="/">
            <Wordmark size="text-2xl" />
          </Link>
          <p className="mt-2 text-sm text-ink-mute">Your AI crew, working for you.</p>
        </div>
        <form onSubmit={submit} className="space-y-3 rounded-md border border-line bg-panel/80 p-5">
          <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
            {MODE_LABELS[mode]}
          </div>
          <Input type="email" required placeholder="you@company.com" value={email}
                 autoComplete="email"
                 onChange={(e) => setEmail(e.target.value)} />
          {mode !== "forgot" && (
            <Input type="password" required minLength={8} placeholder="Password (8+ characters)"
                   autoComplete={mode === "register" ? "new-password" : "current-password"}
                   value={password} onChange={(e) => setPassword(e.target.value)} />
          )}
          {mode === "forgot" && (
            <p className="text-xs text-ink-mute">
              We&apos;ll email you a link to choose a new password. The link expires in 60 minutes.
            </p>
          )}
          {error && <ErrorBox message={error} />}
          {info && <div className="text-xs text-ok">{info}</div>}
          <Button type="submit" disabled={busy} className="w-full">
            {mode === "login" ? "Log in" : mode === "register" ? "Create account" : "Send reset link"}
          </Button>
          <div className="flex items-center justify-between">
            <button
              type="button"
              onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(null); }}
              className="min-h-[32px] text-xs text-ink-faint hover:text-ink-mute"
            >
              {mode === "login" ? "No account? Register" : "Have an account? Log in"}
            </button>
            {mode === "login" && (
              <button
                type="button"
                onClick={() => { setMode("forgot"); setError(null); setInfo(null); }}
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

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
