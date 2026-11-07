"use client";

import { useMemo, useState } from "react";
import { api, useApi } from "@/lib/api";
import { STATUS_LABEL } from "@/lib/format";
import VideoDetail from "@/components/VideoDetail";
import { Badge, Card, Empty, ErrorBox, PageHeader, statusTone, useAction } from "@/components/ui";

type Entry = { kind: "video"; at: string; v: any } | { kind: "slot"; at: string };

export default function CalendarPage() {
  const [weeks, setWeeks] = useState(3);
  const range = useMemo(() => {
    const s = new Date(); s.setDate(s.getDate() - 7);
    const e = new Date(); e.setDate(e.getDate() + weeks * 7);
    return `start=${encodeURIComponent(s.toISOString())}&end=${encodeURIComponent(e.toISOString())}`;
  }, [weeks]);
  const { data, error, reload } = useApi<any>(`/api/calendar?${range}`, { every: 30000 });
  const { busy, run } = useAction();
  const [open, setOpen] = useState<number | null>(null);

  if (error && !data) return <ErrorBox>{error}</ErrorBox>;
  if (!data) return <div className="text-sm text-muted">Loading…</div>;

  const tz: string = data.timezone;
  const dayKey = (iso: string) => new Date(iso).toLocaleDateString("en-CA", { timeZone: tz });
  const dayLabel = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { timeZone: tz, weekday: "long", day: "numeric", month: "long" });
  const time = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit" });
  const slotLabel = (iso: string) => `${dayLabel(iso)}, ${time(iso)}`;

  const entries: Entry[] = [
    ...data.items.filter((v: any) => v.scheduled_at).map((v: any) => ({ kind: "video" as const, at: v.scheduled_at, v })),
    ...data.open_slots.map((at: string) => ({ kind: "slot" as const, at })),
  ].sort((a, b) => a.at.localeCompare(b.at));

  const days = new Map<string, Entry[]>();
  for (const e of entries) {
    const k = dayKey(e.at);
    days.set(k, [...(days.get(k) ?? []), e]);
  }
  const todayKey = new Date().toLocaleDateString("en-CA", { timeZone: tz });

  const reschedule = (id: number, at: string) =>
    run(`m${id}`, () => api(`/api/videos/${id}/schedule`, { method: "PATCH", body: { scheduled_at: at } }).then(reload), "Rescheduled");

  return (
    <>
      <PageHeader
        title="Content Calendar"
        subtitle={`Publishing slots in ${tz}: ${data.publish_times.join(", ")} daily. Change them in AI Settings.`}
        actions={
          <select value={weeks} onChange={(e) => setWeeks(Number(e.target.value))} className="rounded-lg border border-line bg-panel2 px-3 py-2 text-sm">
            {[1, 2, 3, 4, 6].map((w) => <option key={w} value={w}>{w} week{w > 1 ? "s" : ""} ahead</option>)}
          </select>
        }
      />
      {days.size === 0 && <Empty>No slots in this range.</Empty>}
      <div className="space-y-4">
        {Array.from(days.entries()).map(([k, list]) => (
          <Card key={k} title={<span className={k === todayKey ? "text-brand" : ""}>{dayLabel(list[0].at)}{k === todayKey ? " · today" : ""}</span>}>
            <ul className="space-y-2">
              {list.map((e) =>
                e.kind === "slot" ? (
                  <li key={e.at} className="flex items-center gap-3 rounded-lg border border-dashed border-line px-3 py-2 text-sm text-muted">
                    <span className="w-12 font-mono">{time(e.at)}</span>
                    <span>Open slot — the planner will fill it automatically</span>
                  </li>
                ) : (
                  <li key={e.v.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-line bg-panel2 px-3 py-2 text-sm">
                    <span className="w-12 font-mono">{time(e.at)}</span>
                    <button onClick={() => setOpen(e.v.id)} className="min-w-0 flex-1 truncate text-left font-medium hover:underline">{e.v.title}</button>
                    <Badge tone={statusTone(e.v.status)}>{STATUS_LABEL[e.v.status] ?? e.v.status}</Badge>
                    {["planned", "scripted", "seo_ready", "rendered", "ready"].includes(e.v.status) && data.open_slots.length > 0 && (
                      <select
                        disabled={busy === `m${e.v.id}`}
                        value=""
                        onChange={(ev) => ev.target.value && reschedule(e.v.id, ev.target.value)}
                        className="max-w-[170px] rounded-md border border-line bg-panel px-2 py-1 text-xs text-muted"
                        aria-label="Move to another slot"
                      >
                        <option value="">Move to…</option>
                        {data.open_slots.slice(0, 40).map((s: string) => <option key={s} value={s}>{slotLabel(s)}</option>)}
                      </select>
                    )}
                  </li>
                ),
              )}
            </ul>
          </Card>
        ))}
      </div>
      <VideoDetail id={open} onClose={() => setOpen(null)} onChanged={reload} />
    </>
  );
}
