export function num(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (Math.abs(n) >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (Math.abs(n) >= 10_000) return (n / 1_000).toFixed(1).replace(/\.0$/, "") + "K";
  return n.toLocaleString("en-US");
}

export function pct(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined) return "—";
  return `${n.toFixed(digits)}%`;
}

export function dt(iso: string | null | undefined, opts: { tz?: string; short?: boolean } = {}): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-GB", {
    timeZone: opts.tz,
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    ...(opts.short ? {} : { year: "numeric" }),
  });
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return "in " + dur(-s);
  if (s < 45) return "just now";
  return dur(s) + " ago";
}

export function dur(sec: number): string {
  if (sec < 90) return `${Math.round(sec)}s`;
  if (sec < 5400) return `${Math.round(sec / 60)}m`;
  if (sec < 172800) return `${Math.round(sec / 3600)}h`;
  return `${Math.round(sec / 86400)}d`;
}

export const STATUS_LABEL: Record<string, string> = {
  planned: "Planned",
  scripted: "Script ready",
  seo_ready: "SEO ready",
  rendered: "Rendered",
  ready: "Ready to publish",
  publishing: "Publishing",
  published: "Published",
  failed: "Failed",
  cancelled: "Cancelled",
};
