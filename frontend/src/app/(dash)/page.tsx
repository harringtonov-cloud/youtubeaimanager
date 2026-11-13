"use client";

import Link from "next/link";
import { api, useApi } from "@/lib/api";
import { ago, dt, STATUS_LABEL } from "@/lib/format";
import { Badge, Button, Card, Dot, Empty, ErrorBox, PageHeader, Stat, statusTone, useAction } from "@/components/ui";

const JOBS: { name: string; label: string }[] = [
  { name: "generate_ideas", label: "Найти идеи сейчас" },
  { name: "tick", label: "Запустить планировщик" },
  { name: "sync_analytics", label: "Синхронизировать аналитику" },
];

export default function Overview() {
  const { data, error, reload } = useApi<any>("/api/overview", { every: 10000 });
  const { busy, run } = useAction();

  if (error && !data) return <ErrorBox>{error}</ErrorBox>;
  if (!data) return <div className="text-sm text-muted">Загрузка…</div>;
  if (!data.ai || !data.server || !data.queue || !data.counts || !data.youtube) {
    return <ErrorBox>Неожиданный ответ сервера: {JSON.stringify(data).slice(0, 300)}</ErrorBox>;
  }

  const { ai, server, youtube, queue, counts } = data;
  const modeTone = ai.mode === "autonomous" ? "ok" : ai.mode === "manual" ? "warn" : "bad";

  return (
    <>
      <PageHeader
        title="Обзор"
        subtitle="Текущее состояние автономного конвейера"
        actions={JOBS.map((j) => (
          <Button key={j.name} small busy={busy === j.name} onClick={() => run(j.name, () => api(`/api/jobs/run/${j.name}`, { method: "POST" }).then(reload), `${j.label}: поставлено в очередь`)}>
            {j.label}
          </Button>
        ))}
      />

      {ai.mode === "manual" && (
        <div className="mb-4 rounded-xl border border-warn/30 bg-warn/10 p-4 text-sm text-warn">
          Автопубликация ВЫКЛЮЧЕНА. Видео создаются, но не загружаются. Подключите YouTube и включите автопубликацию в разделе{" "}
          <Link href="/settings" className="underline">Настройки ИИ</Link> после прохождения проверок.
        </div>
      )}
      {ai.mode === "paused" && (
        <div className="mb-4 rounded-xl border border-bad/30 bg-bad/10 p-4 text-sm text-bad">
          Автопубликация приостановлена: подключение YouTube требует внимания. Откройте{" "}
          <Link href="/youtube" className="underline">Подключение YouTube</Link>.
        </div>
      )}
      {ai.mode === "unconfigured" && (
        <div className="mb-4 rounded-xl border border-bad/30 bg-bad/10 p-4 text-sm text-bad">
          ИИ простаивает: <code>LLM_API_KEY</code> не задан на сервере.
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Статус ИИ" value={<Badge tone={modeTone}>{STATUS_LABEL[ai.mode] ?? ai.mode}</Badge>} hint={ai.label} />
        <Stat label="Создано видео" value={counts.videos_created} hint={`отрисовано: ${counts.videos_rendered}`} />
        <Stat label="Опубликовано" value={counts.videos_published} hint={ai.next_publication_at ? `следующее: ${dt(ai.next_publication_at, { short: true })}` : "ничего не запланировано"} />
        <Stat label="Идей в пуле" value={counts.ideas_new} hint={`всего: ${counts.ideas_total}`} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="Сервер">
          <ul className="space-y-2 text-sm">
            <li className="flex items-center justify-between"><span>API / База данных</span><span className="flex items-center gap-2"><Dot on={server.database} />{server.database ? "ок" : "не работает"}</span></li>
            <li className="flex items-center justify-between"><span>Redis</span><span className="flex items-center gap-2"><Dot on={server.redis} />{server.redis ? "ок" : "не работает"}</span></li>
            <li className="flex items-center justify-between"><span>Воркер</span><span className="flex items-center gap-2"><Dot on={server.worker_online} />{server.worker_online ? `онлайн (${Math.round(server.worker_heartbeat_age_s)} с)` : "офлайн"}</span></li>
            <li className="flex items-center justify-between"><span>Хранилище</span><span className="text-muted">{server.storage}</span></li>
            <li className="flex items-center justify-between"><span>Версия</span><span className="text-muted">{server.version}</span></li>
          </ul>
        </Card>

        <Card title="YouTube" action={<Link href="/youtube" className="text-xs text-muted hover:text-ink">Управление →</Link>}>
          {youtube.connected ? (
            <div className="space-y-1 text-sm">
              <div className="flex items-center gap-2"><Dot on /> <b>{youtube.channel_title}</b></div>
              <div className="text-muted">{youtube.subscriber_count ?? "—"} подписчиков</div>
              <div className="text-xs text-muted">проверено: {ago(youtube.last_verified_at)}</div>
            </div>
          ) : (
            <div className="space-y-2 text-sm">
              <Badge tone={statusTone(youtube.status)}>{STATUS_LABEL[youtube.status] ?? youtube.status}</Badge>
              {youtube.last_error && <div className="text-xs text-bad">{youtube.last_error}</div>}
              <div><Link href="/youtube" className="text-brand underline">Подключить канал</Link></div>
            </div>
          )}
        </Card>

        <Card title="Очередь задач">
          <ul className="space-y-2 text-sm">
            <li className="flex justify-between"><span>В работе</span><b>{queue.processing}</b></li>
            <li className="flex justify-between"><span>Ждут следующего этапа</span><b>{queue.waiting}</b></li>
            <li className="flex justify-between"><span>Готовы к публикации</span><b>{queue.ready}</b></li>
            <li className="flex justify-between"><span>С ошибкой</span><b className={queue.failed ? "text-bad" : ""}>{queue.failed}</b></li>
            <li className="flex justify-between text-muted"><span>Сообщения в брокере</span><span>{queue.broker_length ?? "н/д"}</span></li>
          </ul>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Последние ошибки">
          {data.recent_errors.length === 0 ? (
            <Empty>Ошибок за последние 3 дня нет</Empty>
          ) : (
            <ul className="space-y-2">
              {data.recent_errors.map((e: any) => (
                <li key={e.id} className="rounded-lg border border-bad/20 bg-bad/5 px-3 py-2 text-sm">
                  <div className="flex justify-between text-xs text-muted"><span>{e.component}</span><span>{ago(e.ts)}</span></div>
                  <div className="mt-0.5 break-words text-bad">{e.message}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Последние действия" action={<Link href="/logs" className="text-xs text-muted hover:text-ink">Все логи →</Link>}>
          {data.recent_actions.length === 0 ? (
            <Empty>Пока ничего не происходило</Empty>
          ) : (
            <ul className="space-y-1.5">
              {data.recent_actions.map((a: any) => (
                <li key={a.id} className="flex gap-3 text-sm">
                  <span className="w-16 shrink-0 text-xs text-muted">{ago(a.ts)}</span>
                  <span className="shrink-0 text-xs uppercase text-muted">{a.component}</span>
                  <span className="min-w-0 break-words">{a.message}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Конвейер по статусам" className="mt-4">
        <div className="flex flex-wrap gap-2">
          {Object.entries(queue.by_status as Record<string, number>).map(([k, v]) => (
            <Badge key={k} tone={statusTone(k)}>{STATUS_LABEL[k] ?? k}: {v}</Badge>
          ))}
          {Object.keys(queue.by_status).length === 0 && <span className="text-sm text-muted">Видео пока нет</span>}
        </div>
      </Card>
    </>
  );
}
