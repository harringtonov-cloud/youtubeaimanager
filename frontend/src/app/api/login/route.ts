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
  if (!token) return NextResponse.json({ detail: "Enter the admin token" }, { status: 400 });

  let r: Response;
  try {
    r = await fetch(`${backendUrl()}/api/auth/check`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: "Backend is unreachable. Check BACKEND_URL and that the API is running." }, { status: 502 });
  }
  if (r.status === 503) {
    return NextResponse.json({ detail: "ADMIN_TOKEN is not configured on the backend." }, { status: 503 });
  }
  if (!r.ok) return NextResponse.json({ detail: "Invalid token" }, { status: 401 });

  const res = NextResponse.json({ ok: true });
  res.cookies.set(COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 30,
  });
  return res;
}
