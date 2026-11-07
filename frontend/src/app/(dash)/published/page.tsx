"use client";

import { useState } from "react";
import { useApi } from "@/lib/api";
import { dt, num, pct } from "@/lib/format";
import VideoDetail, { Thumb } from "@/components/VideoDetail";
import { Badge, Card, Empty, ErrorBox, PageHeader, Table, statusTone } from "@/components/ui";

export default function PublishedPage() {
  const { data, error, reload } = useApi<any>("/api/published?limit=100", { every: 60000 });
  const [open, setOpen] = useState<number | null>(null);

  return (
    <>
      <PageHeader title="Published Videos" subtitle="Publication history with the latest stats. Each video is uploaded exactly once." />
      {error && !data && <ErrorBox>{error}</ErrorBox>}
      <Card>
        {data && data.items.length === 0 && <Empty>Nothing published yet.</Empty>}
        {data && data.items.length > 0 && (
          <Table head={["Video", "Status", "Published", "Views", "Likes", "CTR", "Retention", ""]}>
            {data.items.map((p: any) => {
              const a = p.analytics;
              return (
                <tr key={p.id} className="hover:bg-white/[0.02]">
                  <td className="px-3 py-2">
                    <button onClick={() => setOpen(p.video_id)} className="flex items-center gap-3 text-left">
                      <Thumb id={p.video_id} has={p.has_thumbnail} className="h-12 w-20" />
                      <span className="max-w-[260px] truncate font-medium hover:underline">{p.title}</span>
                    </button>
                  </td>
                  <td className="px-3 py-2"><Badge tone={statusTone(p.status)}>{p.status}</Badge>{p.last_error && <div className="mt-1 max-w-[180px] truncate text-xs text-bad" title={p.last_error}>{p.last_error}</div>}</td>
                  <td className="px-3 py-2 text-muted">{dt(p.published_at ?? p.publish_at, { short: true })}</td>
                  <td className="px-3 py-2">{num(a?.views)}</td>
                  <td className="px-3 py-2">{num(a?.likes)}</td>
                  <td className="px-3 py-2">{a?.ctr != null ? pct(a.ctr) : "—"}</td>
                  <td className="px-3 py-2">{a?.avg_view_percentage != null ? pct(a.avg_view_percentage, 0) : "—"}</td>
                  <td className="px-3 py-2 text-right">{p.url && <a href={p.url} target="_blank" rel="noreferrer" className="text-brand underline">YouTube ↗</a>}</td>
                </tr>
              );
            })}
          </Table>
        )}
      </Card>
      <VideoDetail id={open} onClose={() => setOpen(null)} onChanged={reload} />
    </>
  );
}
