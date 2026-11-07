"use client";

import { useState } from "react";
import { useApi } from "@/lib/api";
import { ago, dt } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, PageHeader, Table, inputCls, statusTone } from "@/components/ui";

const LEVELS = ["", "INFO", "WARNING", "ERROR"];

export default function LogsPage() {
  const [level, setLevel] = useState("");
  const [component, setComponent] = useState("");
  const [q, setQ] = useState("");
  const [live, setLive] = useState(true);
  const qs = new URLSearchParams({ limit: "200" });
  if (level) qs.set("level", level);
  if (component) qs.set("component", component);
  if (q) qs.set("q", q);
  const logs = useApi<any>(`/api/logs?${qs}`, { every: live ? 5000 : undefined });
  const jobs = useApi<any>("/api/logs/jobs?limit=15", { every: live ? 15000 : undefined });

  const lvlTone = (l: string) => (l === "ERROR" || l === "CRITICAL" ? "bad" : l === "WARNING" ? "warn" : "mute");

  return (
    <>
      <PageHeader title="System Logs" subtitle="Everything the AI, worker and API did. Kept for 30 days." />
      <Card className="mb-4">
        <div className="flex flex-wrap items-center gap-3">
          <select value={level} onChange={(e) => setLevel(e.target.value)} className="rounded-lg border border-line bg-panel2 px-3 py-2 text-sm">
            {LEVELS.map((l) => <option key={l} value={l}>{l ? `${l}+` : "All levels"}</option>)}
          </select>
          <select value={component} onChange={(e) => setComponent(e.target.value)} className="rounded-lg border border-line bg-panel2 px-3 py-2 text-sm">
            <option value="">All components</option>
            {logs.data?.components?.map((c: string) => <option key={c}>{c}</option>)}
          </select>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search message…" className={`${inputCls} max-w-xs`} />
          <label className="ml-auto flex items-center gap-2 text-sm text-muted"><input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} className="accent-[#6d5efc]" />Live</label>
        </div>
      </Card>

      {logs.error && !logs.data && <ErrorBox>{logs.error}</ErrorBox>}
      <Card title={`Events${logs.data ? ` (${logs.data.total})` : ""}`}>
        {logs.data && logs.data.items.length === 0 && <Empty>No log entries match.</Empty>}
        <ul className="divide-y divide-line font-mono text-xs">
          {logs.data?.items.map((l: any) => (
            <li key={l.id} className="flex flex-col gap-1 py-2 sm:flex-row sm:gap-3">
              <span className="w-32 shrink-0 text-muted" title={l.ts}>{dt(l.ts, { short: true })}</span>
              <span className="w-16 shrink-0"><Badge tone={lvlTone(l.level) as any}>{l.level}</Badge></span>
              <span className="w-24 shrink-0 text-muted">{l.component}{l.video_id ? ` #${l.video_id}` : ""}</span>
              <span className="min-w-0 break-words text-ink">{l.message}{l.data && Object.keys(l.data).length > 0 && <span className="text-muted"> {JSON.stringify(l.data)}</span>}</span>
            </li>
          ))}
        </ul>
      </Card>

      <Card title="Background jobs" className="mt-4">
        {jobs.data && jobs.data.items.length === 0 && <Empty>No jobs have run yet.</Empty>}
        {jobs.data && jobs.data.items.length > 0 && (
          <Table head={["Job", "Status", "Started", "Result"]}>
            {jobs.data.items.map((j: any) => (
              <tr key={j.id}>
                <td className="px-3 py-2">{j.name}</td>
                <td className="px-3 py-2"><Badge tone={statusTone(j.status)}>{j.status}</Badge></td>
                <td className="px-3 py-2 text-muted">{ago(j.started_at)}</td>
                <td className="max-w-md truncate px-3 py-2 text-muted" title={j.detail ?? ""}>{j.detail ?? "—"}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
