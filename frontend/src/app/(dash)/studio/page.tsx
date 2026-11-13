"use client";

import { useState } from "react";
import { useApi } from "@/lib/api";
import { ago, dt, STATUS_LABEL } from "@/lib/format";
import VideoDetail, { Thumb } from "@/components/VideoDetail";
import { Badge, Card, Empty, ErrorBox, PageHeader, statusTone } from "@/components/ui";

const TABS = [
  { key: "", label: "Все" },
  { key: "planned,scripted,seo_ready,rendered", label: "В производстве" },
  { key: "ready,publishing", label: "Готовы" },
  { key: "failed", label: "С ошибкой" },
  { key: "published", label: "Опубликованные" },
];
const STEPS = ["planned", "scripted", "seo_ready", "rendered", "ready", "published"];

function Progress({ status, stage }: { status: string; stage: string | null }) {
  const idx = status === "publishing" ? 4 : STEPS.indexOf(status);
  return (
    <div className="flex items-center gap-1" title={STATUS_LABEL[status] ?? status}>
      {STEPS.map((s, i) => (
        <span key={s} className={`h-1.5 w-6 rounded-full ${status === "failed" ? "bg-bad/40" : i <= idx ? "bg-brand" : "bg-line"} ${stage && i === idx + 1 ? "animate-pulse bg-brand/60" : ""}`} />
      ))}
    </div>
  );
}

export default function StudioPage() {
  const [tab, setTab] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const { data, error, reload } = useApi<any>(`/api/videos?limit=60${tab ? `&status=${tab}` : ""}`, { every: 8000 });

  return (
    <>
      <PageHeader title="Видеостудия" subtitle="Каждое видео проходит путь: сценарий → SEO → рендер → обложка → публикация. Нажмите на строку, чтобы увидеть детали." />
      <div className="mb-3 flex flex-wrap gap-1">
        {TABS.map((t) => (
          <button key={t.label} onClick={() => setTab(t.key)} className={`rounded-lg px-3 py-1.5 text-sm ${tab === t.key ? "bg-brand/25 text-white" : "text-muted hover:bg-white/5 hover:text-ink"}`}>{t.label}</button>
        ))}
      </div>
      {error && !data && <ErrorBox>{error}</ErrorBox>}
      <Card>
        {data && data.items.length === 0 && <Empty>Здесь пока нет видео. Планировщик создаёт их автоматически, либо воспользуйтесь кнопкой «Создать видео» в AI-режиссёре.</Empty>}
        <ul className="divide-y divide-line">
          {data?.items.map((v: any) => (
            <li key={v.id}>
              <button onClick={() => setOpen(v.id)} className="flex w-full items-center gap-4 py-3 text-left hover:bg-white/[0.02]">
                <Thumb id={v.id} has={v.has_thumbnail} />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{v.title}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                    <Badge tone={statusTone(v.status)}>{STATUS_LABEL[v.status] ?? v.status}</Badge>
                    {v.processing_stage && <Badge tone="info">⟳ {v.processing_stage}</Badge>}
                    <span>план: {dt(v.scheduled_at, { short: true })}</span>
                    <span>обновлено: {ago(v.updated_at)}</span>
                  </div>
                  {v.error && <div className="mt-1 truncate text-xs text-bad">{v.error}</div>}
                </div>
                <div className="hidden sm:block"><Progress status={v.status} stage={v.processing_stage} /></div>
              </button>
            </li>
          ))}
        </ul>
      </Card>
      <VideoDetail id={open} onClose={() => setOpen(null)} onChanged={reload} />
    </>
  );
}
