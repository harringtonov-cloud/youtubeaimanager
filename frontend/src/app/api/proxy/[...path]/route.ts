import { NextRequest, NextResponse } from "next/server";
import { COOKIE, backendUrl } from "@/lib/server";

export const dynamic = "force-dynamic";

/** Server-side proxy: the browser never sees ADMIN_TOKEN; it is attached from the httpOnly cookie. */
async function handle(req: NextRequest, { params }: { params: { path: string[] } }) {
  const token = req.cookies.get(COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in" }, { status: 401 });

  const path = params.path.map(encodeURIComponent).join("/");
  const url = `${backendUrl()}/${path}${req.nextUrl.search}`;

  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  const ct = req.headers.get("content-type");
  if (ct) headers["content-type"] = ct;
  const range = req.headers.get("range"); // video seeking
  if (range) headers["range"] = range;

  const hasBody = !["GET", "HEAD"].includes(req.method);
  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: req.method,
      headers,
      body: hasBody ? await req.text() : undefined,
      cache: "no-store",
      redirect: "follow",
    });
  } catch {
    return NextResponse.json({ detail: "Backend is unreachable" }, { status: 502 });
  }

  const out = new Headers();
  // fetch() transparently decompresses gzip/br bodies, so the upstream content-length (compressed size)
  // would truncate the reply: only forward it for uncompressed responses (e.g. video ranges).
  const compressed = Boolean(upstream.headers.get("content-encoding"));
  for (const h of ["content-type", "content-length", "content-range", "accept-ranges", "cache-control"]) {
    if (compressed && h === "content-length") continue;
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  if (upstream.status === 401) {
    // backend rejected the stored token (rotated ADMIN_TOKEN) -> force re-login
    const res = NextResponse.json({ detail: "Session expired" }, { status: 401 });
    res.cookies.set(COOKIE, "", { httpOnly: true, path: "/", maxAge: 0 });
    return res;
  }
  return new NextResponse(upstream.body, { status: upstream.status, headers: out });
}

export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE };
