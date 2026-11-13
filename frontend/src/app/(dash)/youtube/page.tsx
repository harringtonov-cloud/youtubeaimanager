"use client";

import { useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import { ago, STATUS_LABEL } from "@/lib/format";
import Preflight, { PreflightReport } from "@/components/Preflight";
import { Badge, Button, Card, ErrorBox, PageHeader, statusTone, useAction, useToast } from "@/components/ui";

export default function YouTubePage() {
  const { data, error, reload } = useApi<any>("/api/youtube/status", { every: 30000 });
  const [report, setReport] = useState<PreflightReport | null>(null);
  const [flash, setFlash] = useState<{ ok: boolean; text: string } | null>(null);
  const { busy, run } = useAction();
  const toast = useToast();

  useEffect(() => {
    // result of the Google redirect: /youtube?connected=1 or ?error=...
    const p = new URLSearchParams(window.location.search);
    if (p.get("connected")) setFlash({ ok: true, text: "Канал YouTube подключён." });
    else if (p.get("error")) setFlash({ ok: false, text: `Не удалось подключиться: ${p.get("error")}` });
    if (p.toString()) window.history.replaceState({}, "", "/youtube");
  }, []);

  async function connect() {
    const r = await run("connect", () => api<{ auth_url: string }>("/api/youtube/connect", { method: "POST" }));
    if (r?.auth_url) window.location.href = r.auth_url;
  }

  async function check() {
    const r = await run("preflight", () => api<PreflightReport>("/api/preflight?live=true"));
    if (r) setReport(r);
  }

  if (error && !data) return <ErrorBox>{error}</ErrorBox>;
  if (!data) return <div className="text-sm text-muted">Загрузка…</div>;

  return (
    <>
      <PageHeader title="Подключение YouTube" subtitle="Google OAuth 2.0 · токен обновления хранится в зашифрованном виде и никогда не показывается" />
      {flash && <div className="mb-4">{flash.ok ? <div className="rounded-lg border border-ok/30 bg-ok/10 px-3 py-2 text-sm text-ok">{flash.text}</div> : <ErrorBox>{flash.text}</ErrorBox>}</div>}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Канал">
          <div className="flex items-center gap-4">
            {data.thumbnail_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={data.thumbnail_url} alt="" className="h-14 w-14 rounded-full" />
            ) : (
              <div className="flex h-14 w-14 items-center justify-center rounded-full bg-panel2 text-xl">▶</div>
            )}
            <div className="min-w-0">
              <div className="truncate text-lg font-semibold">{data.channel_title ?? "Не подключено"}</div>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <Badge tone={statusTone(data.status)}>{STATUS_LABEL[data.status] ?? data.status}</Badge>
                {data.quota_blocked && <Badge tone="warn">{data.block_reason === "upload_limit" ? "Достигнут лимит загрузок: публикация приостановлена" : "Квота API исчерпана: публикация приостановлена"}</Badge>}
              </div>
            </div>
          </div>

          {data.connected && (
            <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
              <div><dt className="text-xs text-muted">Подписчики</dt><dd>{data.subscriber_count ?? "—"}</dd></div>
              <div><dt className="text-xs text-muted">Видео</dt><dd>{data.video_count ?? "—"}</dd></div>
              <div><dt className="text-xs text-muted">Подключено</dt><dd>{ago(data.connected_at)}</dd></div>
              <div><dt className="text-xs text-muted">Последняя проверка</dt><dd>{ago(data.last_verified_at)}</dd></div>
            </dl>
          )}
          {data.last_error && <div className="mt-3"><ErrorBox>{data.last_error}</ErrorBox></div>}

          <div className="mt-5 flex flex-wrap gap-2">
            <Button variant="primary" onClick={connect} busy={busy === "connect"} disabled={!data.oauth_configured}>
              {data.status === "not_connected" ? "Подключить YouTube" : "Переподключить / сменить канал"}
            </Button>
            {data.status !== "not_connected" && (
              <>
                <Button busy={busy === "verify"} onClick={() => run("verify", () => api("/api/youtube/verify", { method: "POST" }).then(() => reload()), "Проверка завершена")}>Проверить сейчас</Button>
                {data.quota_blocked && <Button busy={busy === "resume"} onClick={() => run("resume", () => api("/api/youtube/resume", { method: "POST" }).then(() => reload()), "Публикация возобновлена")}>Возобновить публикацию</Button>}
                <Button variant="danger" busy={busy === "disc"} onClick={() => { if (confirm("Отключить YouTube? Автопубликация остановится.")) run("disc", () => api("/api/youtube/disconnect", { method: "POST" }).then(() => reload()), "Отключено"); }}>Отключить</Button>
              </>
            )}
          </div>
          {!data.oauth_configured && <div className="mt-3"><ErrorBox>GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET не заданы на сервере.</ErrorBox></div>}
        </Card>

        <Card title="Настройка Google Cloud">
          <ol className="list-decimal space-y-2 pl-5 text-sm text-muted">
            <li>В Google Cloud Console включите <b className="text-ink">YouTube Data API v3</b> и <b className="text-ink">YouTube Analytics API</b>.</li>
            <li>Создайте OAuth-клиент типа <b className="text-ink">Web application</b> (веб-приложение).</li>
            <li>Добавьте этот Authorized redirect URI (разрешённый URI перенаправления):
              <code className="mt-1 block break-all rounded bg-panel2 px-2 py-1 text-xs text-ink" onClick={() => { navigator.clipboard?.writeText(data.redirect_uri); toast("ok", "Скопировано"); }}>{data.redirect_uri}</code>
            </li>
            <li>Экран согласия OAuth: добавьте себя как тестового пользователя, затем <b className="text-ink">опубликуйте приложение (In production)</b> — иначе Google отзовёт токен через 7 дней.</li>
            <li>Укажите client id и secret в переменных сервера и нажмите «Подключить YouTube».</li>
          </ol>
          <div className="mt-3 text-xs text-muted">Запрашиваемые доступы (scopes): {data.scopes_required?.map((s: string) => s.split("/").pop()).join(", ")}</div>
          <div className="mt-2 text-xs text-warn">Примечание: видео, загруженные непроверенным API-проектом, остаются приватными, пока проект не пройдёт аудит Google. Если загрузки остаются приватными, проверьте статус аудита YouTube API Services.</div>
        </Card>
      </div>

      <Card title="Проверка готовности" className="mt-4" action={<Button small busy={busy === "preflight"} onClick={check}>Запустить проверку</Button>}>
        {report ? <Preflight report={report} /> : <div className="text-sm text-muted">Проверяет токен, канал YouTube, LLM, воркер, хранилище и расписание. Автопубликацию можно включить только после успешной проверки.</div>}
      </Card>
    </>
  );
}
