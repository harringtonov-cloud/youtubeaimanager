"use client";

import { useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import { dt, STATUS_LABEL } from "@/lib/format";
import { Badge, Button, ErrorBox, Modal, statusTone, useAction } from "./ui";

export function mediaSrc(url: string): string {
  return url.startsWith("/") ? `/api/proxy${url}` : url;
}

function useMedia(id: number, kind: "video" | "thumbnail", enabled: boolean) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    api<{ url: string }>(`/api/videos/${id}/media?kind=${kind}`)
      .then((r) => alive && setSrc(mediaSrc(r.url)))
      .catch(() => alive && setSrc(null));
    return () => { alive = false; };
  }, [id, kind, enabled]);
  return src;
}

export function Thumb({ id, has, className }: { id: number; has: boolean; className?: string }) {
  const src = useMedia(id, "thumbnail", has);
  if (!src) return <div className={`flex items-center justify-center rounded-md bg-panel2 text-xs text-muted ${className ?? "h-14 w-24"}`}>{has ? "…" : "нет обложки"}</div>;
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt="" className={`rounded-md object-cover ${className ?? "h-14 w-24"}`} />;
}

export default function VideoDetail({ id, onClose, onChanged }: { id: number | null; onClose: () => void; onChanged: () => void }) {
  const { data: v, error, reload } = useApi<any>(id ? `/api/videos/${id}` : null, { every: id ? 8000 : undefined });
  const { busy, run } = useAction();
  const videoSrc = useMedia(id ?? 0, "video", Boolean(id && v?.has_video));
  const thumbSrc = useMedia(id ?? 0, "thumbnail", Boolean(id && v?.has_thumbnail));

  const act = (key: string, path: string, ok: string) =>
    run(key, async () => { await api(path, { method: "POST" }); reload(); onChanged(); }, ok);

  return (
    <Modal open={id !== null} onClose={onClose} title={v ? `#${v.id} · ${v.title}` : "Видео"} wide>
      {error && <ErrorBox>{error}</ErrorBox>}
      {!v && !error && <div className="text-sm text-muted">Загрузка…</div>}
      {v && (
        <>
            <div className="mb-4 flex flex-wrap gap-2">
              {v.status === "failed" && <Button small variant="primary" busy={busy === "retry"} onClick={() => act("retry", `/api/videos/${v.id}/retry`, "Повтор поставлен в очередь")}>Повторить неудачный этап</Button>}
              {!["published", "publishing", "cancelled", "failed"].includes(v.status) && !v.approved && <Button small busy={busy === "approve"} onClick={() => act("approve", `/api/videos/${v.id}/approve`, "Одобрено для публикации")}>Одобрить</Button>}
              {!["published", "publishing", "cancelled", "failed"].includes(v.status) && <Button small variant="primary" busy={busy === "pub"} onClick={() => { if (confirm("Опубликовать это видео на YouTube прямо сейчас?")) act("pub", `/api/videos/${v.id}/publish-now`, "Загрузка началась, это займёт 1–3 минуты. Проверьте раздел «Опубликованные видео»"); }}>Опубликовать сейчас</Button>}
              {!["published", "publishing", "cancelled"].includes(v.status) && <Button small variant="danger" busy={busy === "cancel"} onClick={() => { if (confirm("Отменить это видео?")) act("cancel", `/api/videos/${v.id}/cancel`, "Отменено"); }}>Отменить</Button>}
              {v.status !== "publishing" && v.status !== "published" && <Button small variant="danger" busy={busy === "del"} onClick={() => { if (confirm("Удалить это видео навсегда? Файлы будут удалены, а идея больше не будет использоваться.")) run("del", async () => { await api(`/api/videos/${v.id}`, { method: "DELETE" }); onChanged(); onClose(); }, "Удалено"); }}>Удалить</Button>}
              {v.status === "published" && <Button small variant="danger" busy={busy === "del"} onClick={() => { if (confirm("Убрать это видео из менеджера? (На YouTube оно останется. Используйте, если вы уже удалили его в YouTube Studio.)")) run("del", async () => { await api(`/api/videos/${v.id}`, { method: "DELETE" }); onChanged(); onClose(); }, "Убрано из менеджера"); }}>Убрать из списка</Button>}
              {v.status === "published" && <Button small variant="danger" busy={busy === "delyt"} onClick={() => { if (confirm("Удалить это видео с YouTube И из менеджера? Это действие нельзя отменить.")) run("delyt", async () => { await api(`/api/videos/${v.id}?from_youtube=true`, { method: "DELETE" }); onChanged(); onClose(); }, "Удалено с YouTube и из менеджера"); }}>Удалить с YouTube</Button>}
            </div>
        <div className="grid gap-5 md:grid-cols-[220px_1fr]">
          <div className="space-y-3">
            {videoSrc ? (
              <video src={videoSrc} controls playsInline preload="metadata" poster={thumbSrc ?? undefined} className="aspect-[9/16] w-full rounded-lg bg-black" />
            ) : (
              <div className="flex aspect-[9/16] w-full items-center justify-center rounded-lg border border-dashed border-line px-3 text-center text-xs text-muted">
                {v.has_video ? "Загрузка видео…" : "Ещё не отрисовано"}
              </div>
            )}
            {thumbSrc && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={thumbSrc} alt="Обложка" className="w-full rounded-lg" />
            )}
          </div>

          <div className="min-w-0 space-y-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={statusTone(v.status)}>{STATUS_LABEL[v.status] ?? v.status}</Badge>
              {v.processing_stage && <Badge tone="info">выполняется: {v.processing_stage}</Badge>}
              {v.approved && <Badge tone="ok">одобрено</Badge>}
              <span className="text-xs text-muted">запланировано: {dt(v.scheduled_at)}</span>
              {v.duration_seconds && <span className="text-xs text-muted">{v.duration_seconds.toFixed(0)} с</span>}
            </div>

            {v.error && <ErrorBox>{v.error}{v.failed_stage ? ` (этап: ${v.failed_stage})` : ""}</ErrorBox>}
            {v.publication?.url && <a href={v.publication.url} target="_blank" rel="noreferrer" className="text-brand underline">{v.publication.url}</a>}
            {v.publication?.last_error && <ErrorBox>Загрузка: {v.publication.last_error}</ErrorBox>}


            {v.seo && (
              <section>
                <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">SEO</h4>
                <div className="space-y-1.5 rounded-lg border border-line bg-panel2 p-3">
                  <div className="font-medium">{v.seo.title}</div>
                  <p className="whitespace-pre-line text-muted">{v.seo.description}</p>
                  <div className="flex flex-wrap gap-1">{(v.seo.tags ?? []).map((t: string) => <Badge key={t}>{t}</Badge>)}</div>
                </div>
              </section>
            )}

            {v.script && (
              <section>
                <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Сценарий · ~{v.script.estimated_seconds ?? "?"} с · слов: {v.script.word_count ?? "?"}</h4>
                <ol className="space-y-2">
                  {(v.script.scenes ?? []).map((s: any, i: number) => (
                    <li key={i} className="rounded-lg border border-line bg-panel2 p-3">
                      <div className="text-ink">{s.narration}</div>
                      <div className="mt-1 text-xs text-muted">🎬 {s.visual_query}{s.on_screen_text ? ` · текст: “${s.on_screen_text}”` : ""}</div>
                    </li>
                  ))}
                </ol>
              </section>
            )}

            {v.assets && (
              <section>
                <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Источники видео и аудио (лицензии)</h4>
                <pre className="max-h-40 overflow-auto rounded-lg border border-line bg-panel2 p-3 text-xs text-muted">{JSON.stringify(v.assets, null, 2)}</pre>
              </section>
            )}
          </div>
        </div>
        </>
      )}
    </Modal>
  );
}
