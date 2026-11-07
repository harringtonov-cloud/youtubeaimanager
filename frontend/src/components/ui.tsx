"use client";

import React, { createContext, useCallback, useContext, useState } from "react";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

export function Card({ title, action, children, className }: { title?: React.ReactNode; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-xl border border-line bg-panel", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: React.ReactNode; hint?: React.ReactNode; tone?: "ok" | "warn" | "bad" }) {
  return (
    <div className="rounded-xl border border-line bg-panel p-4">
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      <div className={cx("mt-1 text-2xl font-semibold", tone === "ok" && "text-ok", tone === "warn" && "text-warn", tone === "bad" && "text-bad")}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

const TONES: Record<string, string> = {
  ok: "bg-ok/15 text-ok border-ok/30",
  warn: "bg-warn/15 text-warn border-warn/30",
  bad: "bg-bad/15 text-bad border-bad/30",
  info: "bg-brand/20 text-[#b4acff] border-brand/40",
  mute: "bg-white/5 text-muted border-line",
};

export function Badge({ tone = "mute", children }: { tone?: keyof typeof TONES; children: React.ReactNode }) {
  return <span className={cx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium", TONES[tone])}>{children}</span>;
}

export function statusTone(s: string): keyof typeof TONES {
  if (["published", "connected", "ok", "approved", "scheduled"].includes(s)) return "ok";
  if (["failed", "error", "rejected", "needs_reauth"].includes(s)) return "bad";
  if (["publishing", "ready", "uploading", "running"].includes(s)) return "info";
  if (["cancelled", "disconnected", "not_connected", "used"].includes(s)) return "mute";
  return "warn";
}

export function Dot({ on, warn }: { on: boolean; warn?: boolean }) {
  return <span className={cx("inline-block h-2 w-2 rounded-full", on ? "bg-ok" : warn ? "bg-warn" : "bg-bad")} />;
}

type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger"; busy?: boolean; small?: boolean };

export function Button({ variant = "ghost", busy, small, className, children, disabled, ...rest }: BtnProps) {
  const v = {
    primary: "bg-brand text-white hover:bg-brand/90 border-transparent",
    ghost: "bg-panel2 text-ink hover:bg-line border-line",
    danger: "bg-bad/15 text-bad hover:bg-bad/25 border-bad/30",
  }[variant];
  return (
    <button
      {...rest}
      disabled={disabled || busy}
      className={cx("inline-flex items-center justify-center gap-2 rounded-lg border font-medium transition disabled:cursor-not-allowed disabled:opacity-50", small ? "px-2.5 py-1 text-xs" : "px-3.5 py-2 text-sm", v, className)}
    >
      {busy && <Spinner />}
      {children}
    </button>
  );
}

export function Spinner() {
  return <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line px-4 py-10 text-center text-sm text-muted">{children}</div>;
}

export function ErrorBox({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-sm text-bad">{children}</div>;
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-ink">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      <div className="flex flex-wrap gap-2">{actions}</div>
    </div>
  );
}

export function Table({ head, children }: { head: string[]; children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead>
          <tr className="border-b border-line text-xs uppercase tracking-wide text-muted">
            {head.map((h) => (
              <th key={h} className="px-3 py-2 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">{children}</tbody>
      </table>
    </div>
  );
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode; wide?: boolean }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 pt-10" onClick={onClose}>
      <div className={cx("w-full rounded-xl border border-line bg-panel", wide ? "max-w-4xl" : "max-w-xl")} onClick={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between border-b border-line px-4 py-3">
          <h3 className="text-sm font-semibold">{title}</h3>
          <button onClick={onClose} className="text-muted hover:text-ink" aria-label="Close">✕</button>
        </header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

/* ---------- toasts ---------- */
type Toast = { id: number; kind: "ok" | "bad"; text: string };
const ToastCtx = createContext<(kind: "ok" | "bad", text: string) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((kind: "ok" | "bad", text: string) => {
    const id = Date.now() + Math.random();
    setItems((s) => [...s, { id, kind, text }]);
    setTimeout(() => setItems((s) => s.filter((t) => t.id !== id)), kind === "bad" ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-80 flex-col gap-2">
        {items.map((t) => (
          <div key={t.id} className={cx("pointer-events-auto rounded-lg border px-3 py-2 text-sm shadow-lg", t.kind === "ok" ? "border-ok/30 bg-[#0f2a1a] text-ok" : "border-bad/30 bg-[#2a1010] text-bad")}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/** Run an async action with busy state + toast feedback. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const run = useCallback(
    async (key: string, fn: () => Promise<any>, okText?: string) => {
      setBusy(key);
      try {
        const r = await fn();
        if (okText) toast("ok", okText);
        return r;
      } catch (e: any) {
        toast("bad", e?.message ?? "Action failed");
        return undefined;
      } finally {
        setBusy(null);
      }
    },
    [toast],
  );
  return { busy, run };
}

/* ---------- tiny SVG bar chart ---------- */
export function BarChart({ data, height = 140 }: { data: { label: string; value: number }[]; height?: number }) {
  if (!data.length) return <Empty>No data yet</Empty>;
  const max = Math.max(...data.map((d) => d.value), 1);
  const w = 100 / data.length;
  return (
    <div>
      <svg viewBox={`0 0 100 ${height / 3}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
        {data.map((d, i) => {
          const h = (d.value / max) * (height / 3 - 4);
          return <rect key={i} x={i * w + w * 0.15} y={height / 3 - h} width={w * 0.7} height={Math.max(h, 0.6)} rx={0.8} className="fill-brand" opacity={0.9}><title>{`${d.label}: ${d.value}`}</title></rect>;
        })}
      </svg>
      <div className="mt-1 flex justify-between text-[10px] text-muted">
        <span>{data[0].label}</span>
        <span>{data[data.length - 1].label}</span>
      </div>
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted/80">{hint}</span>}
    </label>
  );
}

export const inputCls = "w-full rounded-lg border border-line bg-panel2 px-3 py-2 text-sm text-ink placeholder:text-muted/60 focus:border-brand focus:outline-none";
