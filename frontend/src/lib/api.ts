"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

function messageFrom(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (typeof d === "object" && d ? `${(d as any).loc?.slice(-1)?.[0] ?? ""}: ${(d as any).msg}` : String(d))).join("; ");
  }
  if (detail && typeof detail === "object" && "message" in (detail as any)) return String((detail as any).message);
  return fallback;
}

export async function api<T = any>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const res = await fetch(`/api/proxy${path}`, {
    method: init.method ?? "GET",
    headers: init.body !== undefined ? { "content-type": "application/json" } : undefined,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  });
  if (res.status === 401 && typeof window !== "undefined") {
    window.location.href = "/login";
    throw new ApiError(401, "Сессия истекла");
  }
  let data: any = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (res.ok && typeof data === "string") {
    // e.g. Render's "service is waking up" HTML page instead of JSON
    throw new ApiError(502, "Сервер просыпается или вернул ответ не в формате JSON. Подождите минуту и обновите страницу.");
  }
  if (!res.ok && typeof data === "string" && /^\s*<(!doctype|html)/i.test(data)) {
    // an HTML error page from the hosting platform (e.g. Render 502 while the free API is asleep)
    throw new ApiError(res.status, `Сервер пока не отвечает (HTTP ${res.status}). Бесплатный сервер может просыпаться: подождите минуту, страница повторит попытку автоматически.`);
  }
  if (!res.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? data.detail : data;
    throw new ApiError(res.status, messageFrom(detail, `Ошибка запроса (${res.status})`), detail);
  }
  return data as T;
}

/** Fetch + optional polling. Keeps previous data while refreshing (no flicker). */
export function useApi<T = any>(path: string | null, opts: { every?: number } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
      if (!alive.current) return;
      setData(d);
      setError(null);
    } catch (e: any) {
      if (alive.current) setError(e?.message ?? "Ошибка запроса");
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    setLoading(true);
    load();
    if (!opts.every) return () => { alive.current = false; };
    const t = setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, opts.every);
    return () => {
      alive.current = false;
      clearInterval(t);
    };
  }, [load, opts.every]);

  return { data, error, loading, reload: load, setData };
}
