"use client";

import Link from "next/link";
import { api, useApi } from "@/lib/api";
import { ago, dt, STATUS_LABEL } from "@/lib/format";
import { Badge, Button, Card, Dot, Empty, ErrorBox, PageHeader, Stat, statusTone, useAction } from "@/components/ui";

const JOBS: { name: string; label: string }[] = [
  { name: "generate_ideas", label: "Find ideas now" },
  { name: "tick", label: "Run planner now" },
  { name: "sync_analytics", label: "Sync analytics" },
];

export default function Overview() {
  const { data, error, reload } = useApi<any>("/api/overview", { every: 10000 });
  const { busy, run } = useAction();

  if (error && !data) return <ErrorBox>{error}</ErrorBox>;
  if (!data) return <div className="text-sm text-muted">Loading…</div>;
  if (!data.ai || !data.server || !data.queue || !data.counts || !data.youtube) {
    return <ErrorBox>Unexpected reply from the backend: {JSON.stringify(data).slice(0, 300)}</ErrorBox>;
  }

  const { ai, server, youtube, queue, counts } = data;
  const modeTone = ai.mode === "autonomous" ? "ok" : ai.mode === "manual" ? "warn" : "bad";

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="Live state of the autonomous pipeline"
        actions={JOBS.map((j) => (
          <Button key={j.name} small busy={busy === j.name} onClick={() => run(j.name, () => api(`/api/jobs/run/${j.name}`, { method: "POST" }).then(reload), `${j.label}: queued`)}>
            {j.label}
          </Button>
        ))}
      />

      {ai.mode === "manual" && (
        <div className="mb-4 rounded-xl border border-warn/30 bg-warn/10 p-4 text-sm text-warn">
          Auto-publish is OFF. Videos are produced but not uploaded. Connect YouTube and enable auto-publish in{" "}
          <Link href="/settings" className="underline">AI Settings</Link> once the checks pass.
        </div>
      )}
      {ai.mode === "paused" && (
        <div className="mb-4 rounded-xl border border-bad/30 bg-bad/10 p-4 text-sm text-bad">
          Auto-publish is paused: YouTube connection needs attention. Open{" "}
          <Link href="/youtube" className="underline">YouTube Connection</Link>.
        </div>
      )}
      {ai.mode === "unconfigured" && (
        <div className="mb-4 rounded-xl border border-bad/30 bg-bad/10 p-4 text-sm text-bad">
          The AI is idle: <code>LLM_API_KEY</code> is not set on the backend.
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="AI status" value={<Badge tone={modeTone}>{ai.mode}</Badge>} hint={ai.label} />
        <Stat label="Videos created" value={counts.videos_created} hint={`${counts.videos_rendered} rendered`} />
        <Stat label="Published" value={counts.videos_published} hint={ai.next_publication_at ? `next: ${dt(ai.next_publication_at, { short: true })}` : "nothing scheduled"} />
        <Stat label="Ideas in pool" value={counts.ideas_new} hint={`${counts.ideas_total} total`} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="Server">
          <ul className="space-y-2 text-sm">
            <li className="flex items-center justify-between"><span>API / Database</span><span className="flex items-center gap-2"><Dot on={server.database} />{server.database ? "ok" : "down"}</span></li>
            <li className="flex items-center justify-between"><span>Redis</span><span className="flex items-center gap-2"><Dot on={server.redis} />{server.redis ? "ok" : "down"}</span></li>
            <li className="flex items-center justify-between"><span>Worker</span><span className="flex items-center gap-2"><Dot on={server.worker_online} />{server.worker_online ? `online (${Math.round(server.worker_heartbeat_age_s)}s)` : "offline"}</span></li>
            <li className="flex items-center justify-between"><span>Storage</span><span className="text-muted">{server.storage}</span></li>
            <li className="flex items-center justify-between"><span>Version</span><span className="text-muted">{server.version}</span></li>
          </ul>
        </Card>

        <Card title="YouTube" action={<Link href="/youtube" className="text-xs text-muted hover:text-ink">Manage →</Link>}>
          {youtube.connected ? (
            <div className="space-y-1 text-sm">
              <div className="flex items-center gap-2"><Dot on /> <b>{youtube.channel_title}</b></div>
              <div className="text-muted">{youtube.subscriber_count ?? "—"} subscribers</div>
              <div className="text-xs text-muted">verified {ago(youtube.last_verified_at)}</div>
            </div>
          ) : (
            <div className="space-y-2 text-sm">
              <Badge tone={statusTone(youtube.status)}>{youtube.status}</Badge>
              {youtube.last_error && <div className="text-xs text-bad">{youtube.last_error}</div>}
              <div><Link href="/youtube" className="text-brand underline">Connect your channel</Link></div>
            </div>
          )}
        </Card>

        <Card title="Task queue">
          <ul className="space-y-2 text-sm">
            <li className="flex justify-between"><span>In progress</span><b>{queue.processing}</b></li>
            <li className="flex justify-between"><span>Waiting for next stage</span><b>{queue.waiting}</b></li>
            <li className="flex justify-between"><span>Ready to publish</span><b>{queue.ready}</b></li>
            <li className="flex justify-between"><span>Failed</span><b className={queue.failed ? "text-bad" : ""}>{queue.failed}</b></li>
            <li className="flex justify-between text-muted"><span>Broker messages</span><span>{queue.broker_length ?? "n/a"}</span></li>
          </ul>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Recent errors">
          {data.recent_errors.length === 0 ? (
            <Empty>No errors in the last 3 days</Empty>
          ) : (
            <ul className="space-y-2">
              {data.recent_errors.map((e: any) => (
                <li key={e.id} className="rounded-lg border border-bad/20 bg-bad/5 px-3 py-2 text-sm">
                  <div className="flex justify-between text-xs text-muted"><span>{e.component}</span><span>{ago(e.ts)}</span></div>
                  <div className="mt-0.5 break-words text-bad">{e.message}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Latest actions" action={<Link href="/logs" className="text-xs text-muted hover:text-ink">All logs →</Link>}>
          {data.recent_actions.length === 0 ? (
            <Empty>Nothing has happened yet</Empty>
          ) : (
            <ul className="space-y-1.5">
              {data.recent_actions.map((a: any) => (
                <li key={a.id} className="flex gap-3 text-sm">
                  <span className="w-16 shrink-0 text-xs text-muted">{ago(a.ts)}</span>
                  <span className="shrink-0 text-xs uppercase text-muted">{a.component}</span>
                  <span className="min-w-0 break-words">{a.message}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Pipeline by status" className="mt-4">
        <div className="flex flex-wrap gap-2">
          {Object.entries(queue.by_status as Record<string, number>).map(([k, v]) => (
            <Badge key={k} tone={statusTone(k)}>{STATUS_LABEL[k] ?? k}: {v}</Badge>
          ))}
          {Object.keys(queue.by_status).length === 0 && <span className="text-sm text-muted">No videos yet</span>}
        </div>
      </Card>
    </>
  );
}
