import { NextRequest, NextResponse } from "next/server";
import { COOKIE, backendUrl } from "@/lib/server";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  let token = "";
  try {
    token = String((await req.json()).token ?? "").trim();
  } catch {
    /* fallthrough */
  }
  if (!token) return NextResponse.json({ detail: "Введите токен администратора" }, { status: 400 });

  let r: Response;
  try {
    r = await fetch(`${backendUrl()}/api/auth/check`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: "Сервер недоступен. Проверьте BACKEND_URL и что API запущен." }, { status: 502 });
  }
  if (r.status === 503) {
    return NextResponse.json({ detail: "ADMIN_TOKEN не настроен на сервере." }, { status: 503 });
  }
  if (!r.ok) return NextResponse.json({ detail: "Неверный токен" }, { status: 401 });

  // "Secure" cookies are dropped by browsers on plain http (e.g. self-hosted at http://<ip>:3000),
  // so only set the flag when the request actually came in over https.
  const https = req.nextUrl.protocol === "https:" || req.headers.get("x-forwarded-proto") === "https";
  const res = NextResponse.json({ ok: true });
  res.cookies.set(COOKIE, token, {
    httpOnly: true,
    secure: https,
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 30,
  });
  return res;
}
