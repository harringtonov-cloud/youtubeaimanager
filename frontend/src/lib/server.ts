export const COOKIE = "yam_session";

export function backendUrl(): string {
  let url = (process.env.BACKEND_URL || "http://localhost:8000").trim().replace(/\/$/, "");
  // hosting platforms may hand over a bare "host:port" (private network address)
  if (!/^https?:\/\//i.test(url)) url = `http://${url}`;
  return url;
}
