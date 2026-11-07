"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { ago } from "@/lib/format";
import { Badge, Button, Card, Empty, ErrorBox, Field, PageHeader, Table, inputCls, statusTone, useAction } from "@/components/ui";

const FILTERS = [
  { key: "new,approved", label: "Pool" },
  { key: "used", label: "Used" },
  { key: "rejected", label: "Rejected" },
  { key: "", label: "All" },
];

export default function DirectorPage() {
  const [filter, setFilter] = useState("new,approved");
  const ideas = useApi<any>(`/api/ideas?limit=100${filter ? `&status=${filter}` : ""}`, { every: 20000 });
  const state = useApi<any>("/api/director/state", { every: 60000 });
  const { busy, run } = useAction();
  const [topic, setTopic] = useState("");
  const [angle, setAngle] = useState("");

  const reloadAll = () => { ideas.reload(); state.reload(); };

  async function addIdea(e: React.FormEvent) {
    e.preventDefault();
    const r = await run("add", () => api("/api/ideas", { method: "POST", body: { topic, angle } }), "Idea added to the pool");
    if (r) { setTopic(""); setAngle(""); reloadAll(); }
  }

  const counts = state.data?.idea_counts ?? {};
  const insight = state.data?.insight;

  return (
    <>
      <PageHeader
        title="AI Director"
        subtitle="Researches trends, scores ideas against past results and keeps the content plan full"
        actions={
          <Button variant="primary" busy={busy === "run"} onClick={() => run("run", () => api("/api/director/run", { method: "POST" }), "Director started — new ideas will appear in a minute")}>
            Find new ideas
          </Button>
        }
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="What the Director learned" className="lg:col-span-2">
          {insight ? (
            <>
              <p className="whitespace-pre-line text-sm leading-relaxed">{insight.summary}</p>
              <div className="mt-2 text-xs text-muted">Updated {ago(insight.created_at)}</div>
            </>
          ) : (
            <Empty>No analytics review yet. After the first videos collect views, the Director will write its conclusions here.</Empty>
          )}
        </Card>
        <Card title="Idea pool">
          <div className="flex flex-wrap gap-2">
            {["new", "approved", "used", "rejected"].map((s) => (
              <Badge key={s} tone={statusTone(s)}>{s}: {counts[s] ?? 0}</Badge>
            ))}
          </div>
          <form onSubmit={addIdea} className="mt-4 space-y-2">
            <Field label="Add your own topic">
              <input className={inputCls} value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. Why octopuses have three hearts" minLength={5} required />
            </Field>
            <input className={inputCls} value={angle} onChange={(e) => setAngle(e.target.value)} placeholder="Angle (optional)" />
            <Button type="submit" busy={busy === "add"} className="w-full">Add to pool</Button>
          </form>
        </Card>
      </div>

      {state.data?.performance?.length > 0 && (
        <Card title="Performance memory (what gets more views)" className="mt-4">
          <Table head={["Type", "Key", "Videos", "Avg views", "Retention", "Score"]}>
            {state.data.performance.slice(0, 14).map((p: any) => (
              <tr key={p.kind + p.key}>
                <td className="px-3 py-2 text-muted">{p.kind}</td>
                <td className="px-3 py-2">{p.key}</td>
                <td className="px-3 py-2">{p.videos}</td>
                <td className="px-3 py-2">{p.avg_views}</td>
                <td className="px-3 py-2">{p.avg_retention}%</td>
                <td className="px-3 py-2"><Badge tone={p.score >= 60 ? "ok" : p.score >= 35 ? "warn" : "bad"}>{p.score}</Badge></td>
              </tr>
            ))}
          </Table>
        </Card>
      )}

      <Card
        title="Ideas"
        className="mt-4"
        action={
          <div className="flex gap-1">
            {FILTERS.map((f) => (
              <button key={f.label} onClick={() => setFilter(f.key)} className={`rounded-md px-2.5 py-1 text-xs ${filter === f.key ? "bg-brand/25 text-white" : "text-muted hover:text-ink"}`}>{f.label}</button>
            ))}
          </div>
        }
      >
        {ideas.error && <ErrorBox>{ideas.error}</ErrorBox>}
        {ideas.data && ideas.data.items.length === 0 && <Empty>No ideas here yet. Press “Find new ideas”.</Empty>}
        <ul className="divide-y divide-line">
          {ideas.data?.items.map((i: any) => (
            <li key={i.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <div className="font-medium">{i.topic}</div>
                {i.angle && <div className="mt-0.5 text-sm text-muted">{i.angle}</div>}
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <Badge tone={statusTone(i.status)}>{i.status}</Badge>
                  {i.category && <Badge>{i.category}</Badge>}
                  {i.hook_style && <Badge>{i.hook_style}</Badge>}
                  <Badge tone="info">score {i.score}</Badge>
                  <span className="text-xs text-muted">{i.source} · {ago(i.created_at)}</span>
                </div>
              </div>
              {i.status !== "used" && (
                <div className="flex shrink-0 gap-2">
                  {i.status !== "approved" && <Button small onClick={() => run(`a${i.id}`, () => api(`/api/ideas/${i.id}`, { method: "PATCH", body: { status: "approved" } }).then(reloadAll))} busy={busy === `a${i.id}`}>Approve</Button>}
                  {i.status !== "rejected" && <Button small variant="danger" onClick={() => run(`r${i.id}`, () => api(`/api/ideas/${i.id}`, { method: "PATCH", body: { status: "rejected" } }).then(reloadAll))} busy={busy === `r${i.id}`}>Reject</Button>}
                  {i.status !== "rejected" && !i.video_id && <Button small variant="primary" busy={busy === `v${i.id}`} onClick={() => run(`v${i.id}`, () => api(`/api/videos/from-idea/${i.id}`, { method: "POST" }).then(reloadAll), "Video production started")}>Make video</Button>}
                </div>
              )}
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}
