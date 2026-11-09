"use client";

import { useEffect, useState } from "react";
import { api, ApiError, useApi } from "@/lib/api";
import Preflight, { PreflightReport } from "@/components/Preflight";
import { Badge, Button, Card, ErrorBox, Field, PageHeader, inputCls, useAction, useToast } from "@/components/ui";

type S = Record<string, any>;

const LANGS = ["en", "ru", "es", "de", "fr", "pt", "it", "tr", "uk", "pl", "uz", "kk", "ar", "hi", "id", "ja", "ko", "zh", "nl"];

function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="flex cursor-pointer items-start gap-3">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-1 h-4 w-4 accent-[#6d5efc]" />
      <span>
        <span className="block text-sm">{label}</span>
        {hint && <span className="block text-xs text-muted">{hint}</span>}
      </span>
    </label>
  );
}

export default function SettingsPage() {
  const { data, error, reload } = useApi<any>("/api/settings");
  const [form, setForm] = useState<S | null>(null);
  const [times, setTimes] = useState("");
  const [regions, setRegions] = useState("");
  const [report, setReport] = useState<PreflightReport | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const { busy, run } = useAction();
  const toast = useToast();

  useEffect(() => {
    if (data && !form) {
      setForm(data.settings);
      setTimes(data.settings.publish_times.join(", "));
      setRegions(data.settings.trend_regions.join(", "));
    }
  }, [data, form]);

  if (error && !data) return <ErrorBox>{error}</ErrorBox>;
  if (!data || !form) return <div className="text-sm text-muted">Loading…</div>;

  const set = (k: string, v: any) => setForm((f) => ({ ...(f as S), [k]: v }));
  const csv = (s: string) => s.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean);

  async function save(patch?: S) {
    setFormError(null);
    const body = patch ?? { ...form, publish_times: csv(times), trend_regions: csv(regions).map((r) => r.toUpperCase()) };
    try {
      const r = await api("/api/settings", { method: "PUT", body });
      setForm(r.settings);
      setTimes(r.settings.publish_times.join(", "));
      toast("ok", "Settings saved");
      return true;
    } catch (e: any) {
      if (e instanceof ApiError && e.status === 409 && (e.detail as any)?.report) {
        setReport((e.detail as any).report);
        toast("bad", "Auto-publish was NOT enabled: some checks failed");
      } else {
        setFormError(e.message);
        toast("bad", e.message);
      }
      return false;
    }
  }

  async function toggleAutoPublish(on: boolean) {
    // enabling is validated server-side by a live readiness check; the server is the source of truth
    const ok = await run("auto", () => save({ auto_publish_enabled: on }));
    if (ok) { setReport(null); reload(); }
  }

  const secrets = data.secrets as Record<string, boolean>;
  const required = ["ADMIN_TOKEN", "SECRET_KEY", "DATABASE_URL", "REDIS_URL", "LLM_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"];

  return (
    <>
      <PageHeader title="AI Settings" subtitle="Strategy, schedule and publishing rules. Secrets live in environment variables, never here." actions={<Button variant="primary" busy={busy === "save"} onClick={() => run("save", save)}>Save settings</Button>} />
      {formError && <div className="mb-4"><ErrorBox>{formError}</ErrorBox></div>}

      <Card title="Autonomous publishing" className="mb-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="max-w-xl text-sm">
            <div className="flex items-center gap-2 font-medium">Auto-publish <Badge tone={form.auto_publish_enabled ? "ok" : "warn"}>{form.auto_publish_enabled ? "ON" : "OFF"}</Badge></div>
            <p className="mt-1 text-muted">When ON, ready videos are uploaded to YouTube at their scheduled time without any action from you. Turning it ON runs a live check of the YouTube connection, AI, worker and storage; it is refused if anything fails. It pauses itself if YouTube access is lost.</p>
          </div>
          {form.auto_publish_enabled ? (
            <Button variant="danger" busy={busy === "auto"} onClick={() => toggleAutoPublish(false)}>Turn OFF</Button>
          ) : (
            <Button variant="primary" busy={busy === "auto"} onClick={() => toggleAutoPublish(true)}>Check & turn ON</Button>
          )}
        </div>
        {report && <div className="mt-4"><Preflight report={report} /></div>}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Channel strategy">
          <div className="space-y-3">
            <Field label="Niche / topics"><textarea className={inputCls} rows={2} value={form.niche} onChange={(e) => set("niche", e.target.value)} /></Field>
            <Field label="Audience"><input className={inputCls} value={form.audience} onChange={(e) => set("audience", e.target.value)} /></Field>
            <Field label="Tone of voice"><input className={inputCls} value={form.tone} onChange={(e) => set("tone", e.target.value)} /></Field>
            <Field label="Forbidden topics" hint="The Director and Scriptwriter are told to avoid these"><textarea className={inputCls} rows={2} value={form.forbidden_topics} onChange={(e) => set("forbidden_topics", e.target.value)} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Language">
                <select className={inputCls} value={form.language} onChange={(e) => set("language", e.target.value)}>
                  {LANGS.map((l) => <option key={l} value={l}>{l}</option>)}
                </select>
              </Field>
              <Field label="Explore ratio (0–1)" hint="Share of new topics vs. proven ones">
                <input type="number" min={0} max={1} step={0.05} className={inputCls} value={form.explore_ratio} onChange={(e) => set("explore_ratio", Number(e.target.value))} />
              </Field>
            </div>
            <Field label="Trend regions" hint="Country codes, comma-separated, e.g. US, GB"><input className={inputCls} value={regions} onChange={(e) => setRegions(e.target.value)} /></Field>
          </div>
        </Card>

        <Card title="Schedule">
          <div className="space-y-3">
            <Field label="Publish times (HH:MM, comma-separated)" hint={`One video per time, every day. Videos per day = number of times (${csv(times).length}).`}>
              <input className={inputCls} value={times} onChange={(e) => setTimes(e.target.value)} placeholder="09:00, 18:00" />
            </Field>
            <Field label="Timezone (IANA)" hint="e.g. Asia/Tashkent, Europe/Moscow, America/New_York"><input className={inputCls} value={form.timezone} onChange={(e) => set("timezone", e.target.value)} /></Field>
            <Field label="Plan ahead (days)"><input type="number" min={1} max={14} className={inputCls} value={form.plan_horizon_days} onChange={(e) => set("plan_horizon_days", Number(e.target.value))} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Min length (sec)"><input type="number" min={10} max={170} className={inputCls} value={form.min_video_seconds} onChange={(e) => set("min_video_seconds", Number(e.target.value))} /></Field>
              <Field label="Max length (sec)"><input type="number" min={15} max={175} className={inputCls} value={form.max_video_seconds} onChange={(e) => set("max_video_seconds", Number(e.target.value))} /></Field>
            </div>
            <Field label="Max attempts per stage" hint="Transient errors are retried with backoff, then the video is marked failed">
              <input type="number" min={1} max={8} className={inputCls} value={form.max_attempts} onChange={(e) => set("max_attempts", Number(e.target.value))} />
            </Field>
          </div>
        </Card>

        <Card title="Video production">
          <div className="space-y-3">
            <Field label="Visual source" hint="Only licensed footage (Pexels/Pixabay) or generated backgrounds are used">
              <select className={inputCls} value={form.visual_source} onChange={(e) => set("visual_source", e.target.value)}>
                {["auto", "pexels", "pixabay", "generated"].map((v) => <option key={v}>{v}</option>)}
              </select>
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Voice provider" hint="Empty = environment default">
                <select className={inputCls} value={form.tts_provider ?? ""} onChange={(e) => set("tts_provider", e.target.value || null)}>
                  <option value="">default</option><option value="edge">edge (free)</option><option value="openai">openai-compatible</option>
                </select>
              </Field>
              <Field label="Voice name" hint="Empty = automatic for the language"><input className={inputCls} value={form.tts_voice} onChange={(e) => set("tts_voice", e.target.value)} /></Field>
            </div>
          </div>
        </Card>

        <Card title="Publishing rules">
          <div className="space-y-3">
            <Field label="Visibility">
              <select className={inputCls} value={form.privacy_status} onChange={(e) => set("privacy_status", e.target.value)}>
                <option value="public">public</option><option value="unlisted">unlisted</option><option value="private">private</option>
              </select>
            </Field>
            <Field label="Default category ID" hint="27 = Education, 28 = Science & Technology"><input className={inputCls} value={form.category_id} onChange={(e) => set("category_id", e.target.value)} /></Field>
            <Toggle checked={form.require_manual_approval} onChange={(v) => set("require_manual_approval", v)} label="Require my approval before publishing" hint="Videos wait until you press Approve in Video Studio" />
            <Toggle checked={form.synthetic_media_disclosure} onChange={(v) => set("synthetic_media_disclosure", v)} label="Disclose AI-generated content" hint="Adds a note to the description and sets YouTube’s altered/synthetic content flag" />
            <Toggle checked={form.made_for_kids} onChange={(v) => set("made_for_kids", v)} label="Made for kids" hint="Leave off unless your channel targets children (disables comments etc.)" />
          </div>
        </Card>
      </div>

      <Card title="Environment secrets (status only)" className="mt-4">
        <div className="flex flex-wrap gap-2">
          {Object.entries(secrets).map(([k, v]) => (
            <Badge key={k} tone={v ? "ok" : required.includes(k) ? "bad" : "mute"}>{v ? "✓" : "✗"} {k}</Badge>
          ))}
        </div>
        <p className="mt-3 text-xs text-muted">LLM endpoint: {data.env.llm_base_url} · model {data.env.llm_model} · TTS {data.env.tts_provider} · storage {data.env.storage_backend}. Change these in Render (yam-api → Environment) and in GitHub Secrets.</p>
      </Card>
    </>
  );
}
