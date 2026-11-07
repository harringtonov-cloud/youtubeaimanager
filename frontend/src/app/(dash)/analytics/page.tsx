"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { ago, dt, num, pct } from "@/lib/format";
import { BarChart, Button, Card, Empty, ErrorBox, PageHeader, Stat, Table, useAction } from "@/components/ui";

export default function AnalyticsPage() {
  const [days, setDays] = useState(30);
  const sum = useApi<any>(`/api/analytics/summary?days=${days}`, { every: 120000 });
  const ts = useApi<any>(`/api/analytics/timeseries?days=${days}`, { every: 120000 });
  const ins = useApi<any>("/api/analytics/insights?limit=5", { every: 300000 });
  const { busy, run } = useAction();

  const t = sum.data?.totals;
  const engagement = t && t.views ? ((t.likes + t.comments) / t.views) * 100 : null;
  const ctr = t && t.impressions ? (t.views / t.impressions) * 100 : null;

  return (
    <>
      <PageHeader
        title="Analytics"
        subtitle="YouTube Data + Analytics API. The AI Director uses these results to plan the next videos."
        actions={
          <>
            <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="rounded-lg border border-line bg-panel2 px-3 py-2 text-sm">
              {[7, 14, 30, 90, 365].map((d) => <option key={d} value={d}>Last {d} days</option>)}
            </select>
            <Button busy={busy === "sync"} onClick={() => run("sync", () => api("/api/analytics/sync", { method: "POST" }), "Sync queued — refresh in a minute")}>Sync now</Button>
            <Button variant="primary" busy={busy === "learn"} onClick={() => run("learn", () => api("/api/analytics/learn", { method: "POST" }), "AI review queued")}>Run AI review</Button>
          </>
        }
      />
      {sum.error && !sum.data && <ErrorBox>{sum.error}</ErrorBox>}

      {sum.data && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Stat label="Views" value={num(t.views)} hint={`${sum.data.videos_with_data} videos with data`} />
            <Stat label="Avg views / video" value={num(sum.data.avg_views)} />
            <Stat label="Impressions" value={t.impressions ? num(t.impressions) : "—"} hint={ctr != null ? `${ctr.toFixed(1)}% views/impr.` : "needs analytics scope"} />
            <Stat label="Engagement" value={engagement != null ? pct(engagement, 2) : "—"} hint={`${num(t.likes)} likes · ${num(t.comments)} comments`} />
            <Stat label="Subscribers gained" value={num(t.subs)} hint={`${t.minutes} watch min`} />
          </div>

          <Card title="Views by publish day" className="mt-4">
            {ts.data ? <BarChart data={ts.data.items.map((d: any) => ({ label: d.day, value: d.views }))} /> : null}
          </Card>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Card title="Top videos">
              {sum.data.top.length === 0 ? <Empty>No statistics collected yet. Stats appear after the first video is published.</Empty> : (
                <ul className="space-y-2">
                  {sum.data.top.map((r: any) => (
                    <li key={r.publication_id} className="flex items-center justify-between gap-3 text-sm">
                      <span className="min-w-0 truncate">{r.title}</span>
                      <span className="shrink-0 text-muted">{num(r.views)} views · {pct(r.engagement * 100, 1)} eng.</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            <Card title="Weakest videos">
              {sum.data.worst.length === 0 ? <Empty>Shown once there are more than 5 videos.</Empty> : (
                <ul className="space-y-2">
                  {sum.data.worst.map((r: any) => (
                    <li key={r.publication_id} className="flex items-center justify-between gap-3 text-sm">
                      <span className="min-w-0 truncate">{r.title}</span>
                      <span className="shrink-0 text-muted">{num(r.views)} views</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <Card title="All videos compared" className="mt-4">
            {sum.data.videos.length === 0 ? <Empty>No data yet.</Empty> : (
              <Table head={["Video", "Category", "Published", "Views", "Impr.", "CTR", "Avg view %", "Likes", "Subs"]}>
                {sum.data.videos.map((r: any) => (
                  <tr key={r.publication_id}>
                    <td className="max-w-[240px] truncate px-3 py-2">{r.url ? <a className="hover:underline" href={r.url} target="_blank" rel="noreferrer">{r.title}</a> : r.title}</td>
                    <td className="px-3 py-2 text-muted">{r.category || "—"}</td>
                    <td className="px-3 py-2 text-muted">{dt(r.published_at, { short: true })}</td>
                    <td className="px-3 py-2">{num(r.views)}</td>
                    <td className="px-3 py-2">{num(r.impressions)}</td>
                    <td className="px-3 py-2">{r.ctr != null ? pct(r.ctr) : "—"}</td>
                    <td className="px-3 py-2">{r.avg_view_percentage != null ? pct(r.avg_view_percentage, 0) : "—"}</td>
                    <td className="px-3 py-2">{num(r.likes)}</td>
                    <td className="px-3 py-2">{num(r.subs_gained)}</td>
                  </tr>
                ))}
              </Table>
            )}
          </Card>
        </>
      )}

      <Card title="AI conclusions (fed back into planning)" className="mt-4">
        {ins.data?.items?.length ? (
          <ul className="space-y-4">
            {ins.data.items.map((i: any) => (
              <li key={i.id}>
                <div className="text-xs text-muted">{ago(i.created_at)}</div>
                <p className="mt-1 whitespace-pre-line text-sm leading-relaxed">{i.summary}</p>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No AI review yet. It runs nightly once published videos have statistics.</Empty>
        )}
      </Card>
    </>
  );
}
