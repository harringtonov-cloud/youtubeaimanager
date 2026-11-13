"use client";

import { useEffect, useState } from "react";
import { api, ApiError, useApi } from "@/lib/api";
import Preflight, { PreflightReport } from "@/components/Preflight";
import { Badge, Button, Card, ErrorBox, Field, PageHeader, inputCls, useAction, useToast } from "@/components/ui";

type S = Record<string, any>;

const LANGS = ["en", "ru", "es", "de", "fr", "pt", "it", "tr", "uk", "pl", "uz", "kk", "ar", "hi", "id", "ja", "ko", "zh", "nl"];

const LANG_RU: Record<string, string> = { en: "Английский", ru: "Русский", es: "Испанский", de: "Немецкий", fr: "Французский", pt: "Португальский", it: "Итальянский", tr: "Турецкий", uk: "Украинский", pl: "Польский", uz: "Узбекский", kk: "Казахский", ar: "Арабский", hi: "Хинди", id: "Индонезийский", ja: "Японский", ko: "Корейский", zh: "Китайский", nl: "Нидерландский" };
const VISUAL_RU: Record<string, string> = { auto: "Авто", ai: "ИИ-картинки", pexels: "Pexels", pixabay: "Pixabay", generated: "Сгенерированные" };

function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="flex cursor-pointer items-start gap-3">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-1 h-4 w-4 accent-[#6d5efc]" />
      <span>
        <span className="block text-sm">{label}</span>
        {hint && <span className="block text-xs text-muted">{hint}</span>}
      </span>
    </label>
  );
}

export default function SettingsPage() {
  const { data, error, reload } = useApi<any>("/api/settings");
  const [form, setForm] = useState<S | null>(null);
  const [times, setTimes] = useState("");
  const [regions, setRegions] = useState("");
  const [report, setReport] = useState<PreflightReport | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const { busy, run } = useAction();
  const toast = useToast();

  useEffect(() => {
    if (data && !form) {
      setForm(data.settings);
      setTimes(data.settings.publish_times.join(", "));
      setRegions(data.settings.trend_regions.join(", "));
    }
  }, [data, form]);

  if (error && !data) return <ErrorBox>{error}</ErrorBox>;
  if (!data || !form) return <div className="text-sm text-muted">Загрузка…</div>;

  const set = (k: string, v: any) => setForm((f) => ({ ...(f as S), [k]: v }));
  const csv = (s: string) => s.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean);

  async function save(patch?: S) {
    setFormError(null);
    const body = patch ?? { ...form, publish_times: csv(times), trend_regions: csv(regions).map((r) => r.toUpperCase()) };
    try {
      const r = await api("/api/settings", { method: "PUT", body });
      setForm(r.settings);
      setTimes(r.settings.publish_times.join(", "));
      toast("ok", "Настройки сохранены");
      return true;
    } catch (e: any) {
      if (e instanceof ApiError && e.status === 409 && (e.detail as any)?.report) {
        setReport((e.detail as any).report);
        toast("bad", "Автопубликация НЕ включена: некоторые проверки не пройдены");
      } else {
        setFormError(e.message);
        toast("bad", e.message);
      }
      return false;
    }
  }

  async function toggleAutoPublish(on: boolean) {
    // enabling is validated server-side by a live readiness check; the server is the source of truth
    const ok = await run("auto", () => save({ auto_publish_enabled: on }));
    if (ok) { setReport(null); reload(); }
  }

  const secrets = data.secrets as Record<string, boolean>;
  const required = ["ADMIN_TOKEN", "SECRET_KEY", "DATABASE_URL", "REDIS_URL", "LLM_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"];

  return (
    <>
      <PageHeader title="Настройки ИИ" subtitle="Стратегия, расписание и правила публикации. Секреты хранятся в переменных окружения, но не здесь." actions={<Button variant="primary" busy={busy === "save"} onClick={() => run("save", save)}>Сохранить настройки</Button>} />
      {formError && <div className="mb-4"><ErrorBox>{formError}</ErrorBox></div>}

      <Card title="Автономная публикация" className="mb-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="max-w-xl text-sm">
            <div className="flex items-center gap-2 font-medium">Автопубликация <Badge tone={form.auto_publish_enabled ? "ok" : "warn"}>{form.auto_publish_enabled ? "ВКЛ" : "ВЫКЛ"}</Badge></div>
            <p className="mt-1 text-muted">Когда включено, готовые видео загружаются на YouTube в запланированное время без каких-либо действий с вашей стороны. При включении выполняется живая проверка подключения YouTube, ИИ, воркера и хранилища; если что-то не пройдёт, включение будет отклонено. Если доступ к YouTube будет потерян, автопубликация сама встанет на паузу.</p>
          </div>
          {form.auto_publish_enabled ? (
            <Button variant="danger" busy={busy === "auto"} onClick={() => toggleAutoPublish(false)}>Выключить</Button>
          ) : (
            <Button variant="primary" busy={busy === "auto"} onClick={() => toggleAutoPublish(true)}>Проверить и включить</Button>
          )}
        </div>
        {report && <div className="mt-4"><Preflight report={report} /></div>}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Стратегия канала">
          <div className="space-y-3">
            <Field label="Ниша / темы"><textarea className={inputCls} rows={2} value={form.niche} onChange={(e) => set("niche", e.target.value)} /></Field>
            <Field label="Аудитория"><input className={inputCls} value={form.audience} onChange={(e) => set("audience", e.target.value)} /></Field>
            <Field label="Тон подачи"><input className={inputCls} value={form.tone} onChange={(e) => set("tone", e.target.value)} /></Field>
            <Field label="Запрещённые темы" hint="Режиссёру и сценаристу велено избегать этих тем"><textarea className={inputCls} rows={2} value={form.forbidden_topics} onChange={(e) => set("forbidden_topics", e.target.value)} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Язык">
                <select className={inputCls} value={form.language} onChange={(e) => set("language", e.target.value)}>
                  {LANGS.map((l) => <option key={l} value={l}>{LANG_RU[l] ?? l} ({l})</option>)}
                </select>
              </Field>
              <Field label="Доля экспериментов (0–1)" hint="Доля новых тем по сравнению с проверенными">
                <input type="number" min={0} max={1} step={0.05} className={inputCls} value={form.explore_ratio} onChange={(e) => set("explore_ratio", Number(e.target.value))} />
              </Field>
            </div>
            <Field label="Регионы трендов" hint="Коды стран через запятую, например: US, GB"><input className={inputCls} value={regions} onChange={(e) => setRegions(e.target.value)} /></Field>
          </div>
        </Card>

        <Card title="Расписание">
          <div className="space-y-3">
            <Field label="Время публикации (ЧЧ:ММ, через запятую)" hint={`По одному видео на каждое время, ежедневно. Видео в день = число времён (${csv(times).length}).`}>
              <input className={inputCls} value={times} onChange={(e) => setTimes(e.target.value)} placeholder="09:00, 18:00" />
            </Field>
            <Field label="Часовой пояс (IANA)" hint="например: Asia/Tashkent, Europe/Moscow, America/New_York"><input className={inputCls} value={form.timezone} onChange={(e) => set("timezone", e.target.value)} /></Field>
            <Field label="Планировать вперёд (дней)"><input type="number" min={1} max={14} className={inputCls} value={form.plan_horizon_days} onChange={(e) => set("plan_horizon_days", Number(e.target.value))} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Мин. длина (сек)"><input type="number" min={10} max={170} className={inputCls} value={form.min_video_seconds} onChange={(e) => set("min_video_seconds", Number(e.target.value))} /></Field>
              <Field label="Макс. длина (сек)"><input type="number" min={15} max={175} className={inputCls} value={form.max_video_seconds} onChange={(e) => set("max_video_seconds", Number(e.target.value))} /></Field>
            </div>
            <Field label="Макс. попыток на этап" hint="Временные ошибки повторяются с нарастающей задержкой, затем видео помечается как неудачное">
              <input type="number" min={1} max={8} className={inputCls} value={form.max_attempts} onChange={(e) => set("max_attempts", Number(e.target.value))} />
            </Field>
          </div>
        </Card>

        <Card title="Производство видео">
          <div className="space-y-3">
            <Field label="Источник визуала" hint="Авто = стоковое видео, если есть ключи, иначе бесплатные ИИ-картинки; ИИ-картинки = всегда ИИ-картинки">
              <select className={inputCls} value={form.visual_source} onChange={(e) => set("visual_source", e.target.value)}>
                {["auto", "ai", "pexels", "pixabay", "generated"].map((v) => <option key={v} value={v}>{VISUAL_RU[v] ?? v}</option>)}
              </select>
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Провайдер голоса" hint="Пусто = значение по умолчанию из окружения">
                <select className={inputCls} value={form.tts_provider ?? ""} onChange={(e) => set("tts_provider", e.target.value || null)}>
                  <option value="">по умолчанию</option><option value="edge">edge (бесплатно)</option><option value="openai">совместимый с OpenAI</option>
                </select>
              </Field>
              <Field label="Название голоса" hint="Пусто = автоматически для выбранного языка"><input className={inputCls} value={form.tts_voice} onChange={(e) => set("tts_voice", e.target.value)} /></Field>
            </div>
          </div>
        </Card>

        <Card title="Правила публикации">
          <div className="space-y-3">
            <Field label="Видимость">
              <select className={inputCls} value={form.privacy_status} onChange={(e) => set("privacy_status", e.target.value)}>
                <option value="public">публичное</option><option value="unlisted">по ссылке</option><option value="private">приватное</option>
              </select>
            </Field>
            <Field label="ID категории по умолчанию" hint="27 = Образование, 28 = Наука и технологии"><input className={inputCls} value={form.category_id} onChange={(e) => set("category_id", e.target.value)} /></Field>
            <Toggle checked={form.music_enabled} onChange={(v) => set("music_enabled", v)} label="Фоновая музыка" hint="Мягкая фоновая музыка, которая становится тише, когда говорит голос" />
            <Toggle checked={form.first_comment_enabled} onChange={(v) => set("first_comment_enabled", v)} label="Публиковать первый комментарий" hint="После публикации добавляет вопрос, чтобы вызвать ответы (пропускается, если YouTube откажет)" />
            <Toggle checked={form.require_manual_approval} onChange={(v) => set("require_manual_approval", v)} label="Требовать моего одобрения перед публикацией" hint="Видео ждут, пока вы не нажмёте «Одобрить» в Видеостудии" />
            <Toggle checked={form.synthetic_media_disclosure} onChange={(v) => set("synthetic_media_disclosure", v)} label="Помечать контент, созданный ИИ" hint="Добавляет пометку в описание и ставит флаг YouTube об изменённом/синтетическом контенте" />
            <Toggle checked={form.made_for_kids} onChange={(v) => set("made_for_kids", v)} label="Сделано для детей" hint="Оставьте выключенным, если канал не ориентирован на детей (отключает комментарии и т. д.)" />
          </div>
        </Card>
      </div>

      <Card title="Секреты окружения (только статус)" className="mt-4">
        <div className="flex flex-wrap gap-2">
          {Object.entries(secrets).map(([k, v]) => (
            <Badge key={k} tone={v ? "ok" : required.includes(k) ? "bad" : "mute"}>{v ? "✓" : "✗"} {k}</Badge>
          ))}
        </div>
        <p className="mt-3 text-xs text-muted">Адрес LLM: {data.env.llm_base_url} · модель {data.env.llm_model} · TTS {data.env.tts_provider} · хранилище {data.env.storage_backend}. Меняйте их в Render (yam-api → Environment) и в GitHub Secrets.</p>
      </Card>
    </>
  );
}
