"use client";

import { useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import { ago } from "@/lib/format";
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
    if (p.get("connected")) setFlash({ ok: true, text: "YouTube channel connected." });
    else if (p.get("error")) setFlash({ ok: false, text: `Connection failed: ${p.get("error")}` });
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
  if (!data) return <div className="text-sm text-muted">Loading…</div>;

  return (
    <>
      <PageHeader title="YouTube Connection" subtitle="Google OAuth 2.0 · the refresh token is stored encrypted and never shown" />
      {flash && <div className="mb-4">{flash.ok ? <div className="rounded-lg border border-ok/30 bg-ok/10 px-3 py-2 text-sm text-ok">{flash.text}</div> : <ErrorBox>{flash.text}</ErrorBox>}</div>}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Channel">
          <div className="flex items-center gap-4">
            {data.thumbnail_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={data.thumbnail_url} alt="" className="h-14 w-14 rounded-full" />
            ) : (
              <div className="flex h-14 w-14 items-center justify-center rounded-full bg-panel2 text-xl">▶</div>
            )}
            <div className="min-w-0">
              <div className="truncate text-lg font-semibold">{data.channel_title ?? "Not connected"}</div>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <Badge tone={statusTone(data.status)}>{data.status}</Badge>
                {data.quota_blocked && <Badge tone="warn">API quota exhausted</Badge>}
              </div>
            </div>
          </div>

          {data.connected && (
            <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
              <div><dt className="text-xs text-muted">Subscribers</dt><dd>{data.subscriber_count ?? "—"}</dd></div>
              <div><dt className="text-xs text-muted">Videos</dt><dd>{data.video_count ?? "—"}</dd></div>
              <div><dt className="text-xs text-muted">Connected</dt><dd>{ago(data.connected_at)}</dd></div>
              <div><dt className="text-xs text-muted">Last verified</dt><dd>{ago(data.last_verified_at)}</dd></div>
            </dl>
          )}
          {data.last_error && <div className="mt-3"><ErrorBox>{data.last_error}</ErrorBox></div>}

          <div className="mt-5 flex flex-wrap gap-2">
            <Button variant="primary" onClick={connect} busy={busy === "connect"} disabled={!data.oauth_configured}>
              {data.status === "not_connected" ? "Connect YouTube" : "Reconnect / switch channel"}
            </Button>
            {data.status !== "not_connected" && (
              <>
                <Button busy={busy === "verify"} onClick={() => run("verify", () => api("/api/youtube/verify", { method: "POST" }).then(() => reload()), "Verification finished")}>Verify now</Button>
                <Button variant="danger" busy={busy === "disc"} onClick={() => { if (confirm("Disconnect YouTube? Auto-publishing will stop.")) run("disc", () => api("/api/youtube/disconnect", { method: "POST" }).then(() => reload()), "Disconnected"); }}>Disconnect</Button>
              </>
            )}
          </div>
          {!data.oauth_configured && <div className="mt-3"><ErrorBox>GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set on the backend.</ErrorBox></div>}
        </Card>

        <Card title="Google Cloud setup">
          <ol className="list-decimal space-y-2 pl-5 text-sm text-muted">
            <li>In Google Cloud Console enable <b className="text-ink">YouTube Data API v3</b> and <b className="text-ink">YouTube Analytics API</b>.</li>
            <li>Create an OAuth client of type <b className="text-ink">Web application</b>.</li>
            <li>Add this Authorized redirect URI:
              <code className="mt-1 block break-all rounded bg-panel2 px-2 py-1 text-xs text-ink" onClick={() => { navigator.clipboard?.writeText(data.redirect_uri); toast("ok", "Copied"); }}>{data.redirect_uri}</code>
            </li>
            <li>OAuth consent screen: add yourself as a test user, then <b className="text-ink">publish the app (In production)</b> — otherwise Google expires the token after 7 days.</li>
            <li>Put the client id/secret in the backend variables and press “Connect YouTube”.</li>
          </ol>
          <div className="mt-3 text-xs text-muted">Scopes requested: {data.scopes_required?.map((s: string) => s.split("/").pop()).join(", ")}</div>
          <div className="mt-2 text-xs text-warn">Note: videos uploaded by an unverified API project are locked to private until the project passes Google’s audit. Check the YouTube API Services audit status if uploads stay private.</div>
        </Card>
      </div>

      <Card title="Readiness check" className="mt-4" action={<Button small busy={busy === "preflight"} onClick={check}>Run live check</Button>}>
        {report ? <Preflight report={report} /> : <div className="text-sm text-muted">Checks the token, YouTube channel, LLM, worker, storage and schedule. Auto-publish can only be enabled when this passes.</div>}
      </Card>
    </>
  );
}
