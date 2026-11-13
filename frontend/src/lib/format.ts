export function num(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (Math.abs(n) >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + " млн";
  if (Math.abs(n) >= 10_000) return (n / 1_000).toFixed(1).replace(/\.0$/, "") + " тыс.";
  return n.toLocaleString("ru-RU");
}

export function pct(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined) return "—";
  return `${n.toFixed(digits)}%`;
}

export function dt(iso: string | null | undefined, opts: { tz?: string; short?: boolean } = {}): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", {
    timeZone: opts.tz,
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    ...(opts.short ? {} : { year: "numeric" }),
  });
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "никогда";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return "через " + dur(-s);
  if (s < 45) return "только что";
  return dur(s) + " назад";
}

export function dur(sec: number): string {
  if (sec < 90) return `${Math.round(sec)} с`;
  if (sec < 5400) return `${Math.round(sec / 60)} мин`;
  if (sec < 172800) return `${Math.round(sec / 3600)} ч`;
  return `${Math.round(sec / 86400)} дн.`;
}

export const STATUS_LABEL: Record<string, string> = {
  new: "Новая",
  autonomous: "Автономный",
  manual: "Ручной",
  paused: "Пауза",
  unconfigured: "Не настроен",
  planned: "Запланировано",
  scripted: "Сценарий готов",
  seo_ready: "SEO готово",
  rendered: "Отрисовано",
  ready: "Готово к публикации",
  publishing: "Публикуется",
  published: "Опубликовано",
  failed: "Ошибка",
  cancelled: "Отменено",
  approved: "Одобрено",
  scheduled: "По расписанию",
  connected: "Подключено",
  disconnected: "Отключено",
  not_connected: "Не подключено",
  needs_reauth: "Нужна повторная авторизация",
  uploading: "Загружается",
  running: "Выполняется",
  rejected: "Отклонено",
  error: "Ошибка",
  ok: "ОК",
  used: "Использовано",
};
