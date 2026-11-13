"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { ago, STATUS_LABEL } from "@/lib/format";
import { Badge, Button, Card, Empty, ErrorBox, Field, PageHeader, Table, inputCls, statusTone, useAction } from "@/components/ui";

const FILTERS = [
  { key: "new,approved", label: "Пул" },
  { key: "used", label: "Использованные" },
  { key: "rejected", label: "Отклонённые" },
  { key: "", label: "Все" },
];

export default function DirectorPage() {
  const [filter, setFilter] = useState("new,approved");
  const ideas = useApi<any>(`/api/ideas?limit=100${filter ? `&status=${filter}` : ""}`, { every: 20000 });
  const state = useApi<any>("/api/director/state", { every: 60000 });
  const { busy, run } = useAction();
  const [topic, setTopic] = useState("");
  const [angle, setAngle] = useState("");

  const reloadAll = () => { ideas.reload(); state.reload(); };

  async function addIdea(e: React.FormEvent) {
    e.preventDefault();
    const r = await run("add", () => api("/api/ideas", { method: "POST", body: { topic, angle } }), "Идея добавлена в пул");
    if (r) { setTopic(""); setAngle(""); reloadAll(); }
  }

  const counts = state.data?.idea_counts ?? {};
  const insight = state.data?.insight;

  return (
    <>
      <PageHeader
        title="AI-режиссёр"
        subtitle="Изучает тренды, оценивает идеи по прошлым результатам и поддерживает контент-план заполненным"
        actions={
          <Button variant="primary" busy={busy === "run"} onClick={() => run("run", () => api("/api/director/run", { method: "POST" }), "Режиссёр запущен — новые идеи появятся через минуту")}>
            Найти новые идеи
          </Button>
        }
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Что узнал режиссёр" className="lg:col-span-2">
          {insight ? (
            <>
              <p className="whitespace-pre-line text-sm leading-relaxed">{insight.summary}</p>
              <div className="mt-2 text-xs text-muted">Обновлено: {ago(insight.created_at)}</div>
            </>
          ) : (
            <Empty>Обзора аналитики пока нет. Когда первые видео наберут просмотры, режиссёр напишет здесь свои выводы.</Empty>
          )}
        </Card>
        <Card title="Пул идей">
          <div className="flex flex-wrap gap-2">
            {["new", "approved", "used", "rejected"].map((s) => (
              <Badge key={s} tone={statusTone(s)}>{STATUS_LABEL[s] ?? s}: {counts[s] ?? 0}</Badge>
            ))}
          </div>
          <form onSubmit={addIdea} className="mt-4 space-y-2">
            <Field label="Добавьте свою тему">
              <input className={inputCls} value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="например: Почему у осьминогов три сердца" minLength={5} required />
            </Field>
            <input className={inputCls} value={angle} onChange={(e) => setAngle(e.target.value)} placeholder="Ракурс (необязательно)" />
            <Button type="submit" busy={busy === "add"} className="w-full">Добавить в пул</Button>
          </form>
        </Card>
      </div>

      {state.data?.performance?.length > 0 && (
        <Card title="Память об эффективности (что набирает больше просмотров)" className="mt-4">
          <Table head={["Тип", "Ключ", "Видео", "Ср. просмотры", "Удержание", "Оценка"]}>
            {state.data.performance.slice(0, 14).map((p: any) => (
              <tr key={p.kind + p.key}>
                <td className="px-3 py-2 text-muted">{p.kind}</td>
                <td className="px-3 py-2">{p.key}</td>
                <td className="px-3 py-2">{p.videos}</td>
                <td className="px-3 py-2">{p.avg_views}</td>
                <td className="px-3 py-2">{p.avg_retention}%</td>
                <td className="px-3 py-2"><Badge tone={p.score >= 60 ? "ok" : p.score >= 35 ? "warn" : "bad"}>{p.score}</Badge></td>
              </tr>
            ))}
          </Table>
        </Card>
      )}

      <Card
        title="Идеи"
        className="mt-4"
        action={
          <div className="flex gap-1">
            {FILTERS.map((f) => (
              <button key={f.label} onClick={() => setFilter(f.key)} className={`rounded-md px-2.5 py-1 text-xs ${filter === f.key ? "bg-brand/25 text-white" : "text-muted hover:text-ink"}`}>{f.label}</button>
            ))}
          </div>
        }
      >
        {ideas.error && <ErrorBox>{ideas.error}</ErrorBox>}
        {ideas.data && ideas.data.items.length === 0 && <Empty>Здесь пока нет идей. Нажмите «Найти новые идеи».</Empty>}
        <ul className="divide-y divide-line">
          {ideas.data?.items.map((i: any) => (
            <li key={i.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <div className="font-medium">{i.topic}</div>
                {i.angle && <div className="mt-0.5 text-sm text-muted">{i.angle}</div>}
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <Badge tone={statusTone(i.status)}>{STATUS_LABEL[i.status] ?? i.status}</Badge>
                  {i.category && <Badge>{i.category}</Badge>}
                  {i.hook_style && <Badge>{i.hook_style}</Badge>}
                  <Badge tone="info">оценка {i.score}</Badge>
                  <span className="text-xs text-muted">{i.source} · {ago(i.created_at)}</span>
                </div>
              </div>
              {i.status !== "used" && (
                <div className="flex shrink-0 gap-2">
                  {i.status !== "approved" && <Button small onClick={() => run(`a${i.id}`, () => api(`/api/ideas/${i.id}`, { method: "PATCH", body: { status: "approved" } }).then(reloadAll))} busy={busy === `a${i.id}`}>Одобрить</Button>}
                  {i.status !== "rejected" && <Button small variant="danger" onClick={() => run(`r${i.id}`, () => api(`/api/ideas/${i.id}`, { method: "PATCH", body: { status: "rejected" } }).then(reloadAll))} busy={busy === `r${i.id}`}>Отклонить</Button>}
                  {i.status !== "rejected" && !i.video_id && <Button small variant="primary" busy={busy === `v${i.id}`} onClick={() => run(`v${i.id}`, () => api(`/api/videos/from-idea/${i.id}`, { method: "POST" }).then(reloadAll), "Производство видео запущено")}>Создать видео</Button>}
                </div>
              )}
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}
