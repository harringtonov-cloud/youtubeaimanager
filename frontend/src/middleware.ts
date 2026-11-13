import { NextRequest, NextResponse } from "next/server";

const COOKIE = "yam_session";

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const authed = Boolean(req.cookies.get(COOKIE)?.value);

  if (pathname === "/login") {
    return authed ? NextResponse.redirect(new URL("/", req.url)) : NextResponse.next();
  }
  if (!authed) {
    if (pathname.startsWith("/api/")) {
      return NextResponse.json({ detail: "Вы не вошли в систему" }, { status: 401 });
    }
    return NextResponse.redirect(new URL("/login", req.url));
  }
  return NextResponse.next();
}

export const config = {
  // login/logout endpoints are public; static assets are excluded
  matcher: ["/((?!_next/|favicon|api/login|api/logout).*)"],
};
