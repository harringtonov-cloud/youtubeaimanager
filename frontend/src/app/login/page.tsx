"use client";

import { useState } from "react";
import { Button, ErrorBox, inputCls } from "@/components/ui";

export default function LoginPage() {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || "Sign-in failed");
      window.location.href = "/";
    } catch (err: any) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-2xl border border-line bg-panel p-6">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand text-lg font-bold">▶</div>
          <div>
            <div className="font-semibold">YouTube AI Manager</div>
            <div className="text-xs text-muted">Owner sign-in</div>
          </div>
        </div>
        <label className="mb-1 block text-xs font-medium text-muted" htmlFor="token">Admin token (ADMIN_TOKEN)</label>
        <input id="token" type="password" autoFocus autoComplete="current-password" value={token} onChange={(e) => setToken(e.target.value)} className={inputCls} placeholder="••••••••••••" />
        {error && <div className="mt-3"><ErrorBox>{error}</ErrorBox></div>}
        <Button type="submit" variant="primary" busy={busy} className="mt-4 w-full">Sign in</Button>
      </form>
    </main>
  );
}
