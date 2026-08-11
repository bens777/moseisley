"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { API_URL } from "@/lib/api";
import { Wordmark } from "@/components/brand";
import { Button, ErrorBox, Input } from "@/components/ui";

function ResetForm() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token") || "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const resp = await fetch(`${API_URL}/api/auth/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password }),
      });
      if (!resp.ok) {
        throw new Error(
          "This reset link is invalid or has expired. Request a new one from the login page."
        );
      }
      router.replace("/login?reset=success");
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
          <p className="mt-2 text-sm text-ink-mute">Choose a new password.</p>
        </div>
        {!token ? (
          <div className="space-y-3 rounded-md border border-line bg-panel/80 p-5">
            <ErrorBox message="Missing reset token. Use the link from your email, or request a new one." />
            <Link href="/login" className="block text-center text-xs text-ink-faint hover:text-ink-mute">
              ← back to login
            </Link>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-3 rounded-md border border-line bg-panel/80 p-5">
            <div className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
              access · reset password
            </div>
            <Input type="password" required minLength={8} placeholder="New password (8+ characters)"
                   autoComplete="new-password"
                   value={password} onChange={(e) => setPassword(e.target.value)} />
            <Input type="password" required minLength={8} placeholder="Confirm new password"
                   autoComplete="new-password"
                   value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            {error && <ErrorBox message={error} />}
            <Button type="submit" disabled={busy} className="w-full">
              {busy ? "Saving…" : "Set new password"}
            </Button>
            <Link href="/login" className="block text-center text-xs text-ink-faint hover:text-ink-mute">
              ← back to login
            </Link>
          </form>
        )}
      </div>
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense>
      <ResetForm />
    </Suspense>
  );
}
