"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useApi } from "@/lib/api";
import { Dot, ToastProvider, cx } from "./ui";

const NAV = [
  { href: "/", label: "Overview", icon: "◧" },
  { href: "/director", label: "AI Director", icon: "✦" },
  { href: "/studio", label: "Video Studio", icon: "▣" },
  { href: "/calendar", label: "Content Calendar", icon: "▦" },
  { href: "/published", label: "Published Videos", icon: "▶" },
  { href: "/analytics", label: "Analytics", icon: "▲" },
  { href: "/youtube", label: "YouTube Connection", icon: "⛓" },
  { href: "/settings", label: "AI Settings", icon: "⚙" },
  { href: "/logs", label: "System Logs", icon: "≣" },
];

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const { data } = useApi<any>("/api/overview", { every: 15000 });

  const mode = data?.ai?.mode as string | undefined;
  const modeTone = mode === "autonomous" ? "bg-ok" : mode === "manual" ? "bg-warn" : mode ? "bg-bad" : "bg-muted";

  async function logout() {
    await fetch("/api/logout", { method: "POST" });
    window.location.href = "/login";
  }

  const nav = (
    <nav className="flex flex-1 flex-col gap-0.5 p-3">
      {NAV.map((n) => {
        const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
        return (
          <Link key={n.href} href={n.href} onClick={() => setOpen(false)} className={cx("flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition", active ? "bg-brand/20 text-white" : "text-muted hover:bg-white/5 hover:text-ink")}>
            <span className="w-4 text-center">{n.icon}</span>
            {n.label}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <ToastProvider>
      <div className="flex min-h-screen">
        {/* desktop sidebar */}
        <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-line bg-panel lg:flex">
          <div className="flex items-center gap-3 border-b border-line px-5 py-4">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand font-bold">▶</div>
            <div className="text-sm font-semibold leading-tight">YouTube<br />AI Manager</div>
          </div>
          {nav}
          <div className="space-y-2 border-t border-line p-4 text-xs text-muted">
            <div className="flex items-center gap-2"><span className={cx("h-2 w-2 rounded-full", modeTone)} />{data?.ai?.label ?? "Connecting…"}</div>
            <div className="flex items-center gap-2"><Dot on={Boolean(data?.server?.worker_online)} />Worker {data?.server?.worker_online ? "online" : "offline"}</div>
            <button onClick={logout} className="mt-1 text-muted underline-offset-2 hover:text-ink hover:underline">Sign out</button>
          </div>
        </aside>

        {/* mobile drawer */}
        {open && (
          <div className="fixed inset-0 z-40 lg:hidden" onClick={() => setOpen(false)}>
            <div className="absolute inset-0 bg-black/60" />
            <aside className="relative flex h-full w-64 flex-col bg-panel" onClick={(e) => e.stopPropagation()}>{nav}</aside>
          </div>
        )}

        <div className="min-w-0 flex-1">
          <div className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-bg/90 px-4 py-3 backdrop-blur lg:hidden">
            <button onClick={() => setOpen(true)} className="text-lg" aria-label="Menu">☰</button>
            <div className="flex items-center gap-2 text-xs text-muted"><span className={cx("h-2 w-2 rounded-full", modeTone)} />{data?.ai?.label ?? "…"}</div>
          </div>
          <main className="mx-auto max-w-7xl p-4 sm:p-6">{children}</main>
        </div>
      </div>
    </ToastProvider>
  );
}
