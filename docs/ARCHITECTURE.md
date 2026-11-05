# Architecture

```
                      ┌──────────────────────────┐
  Browser ──────────▶ │ Next.js Dashboard (Vercel)│
                      │  • httpOnly cookie auth   │
                      │  • /api/proxy → backend   │
                      └────────────┬─────────────┘
                                   │ Bearer ADMIN_TOKEN (server-side only)
                      ┌────────────▼─────────────┐        ┌──────────────┐
                      │ FastAPI backend (Railway) │◀──────▶│  PostgreSQL  │
                      │  REST API, OAuth callback │        └──────────────┘
                      └────────────┬─────────────┘
                                   │ enqueue
                      ┌────────────▼─────────────┐        ┌──────────────┐
                      │ Redis (broker, locks)     │◀──────▶│ Celery worker│
                      └───────────────────────────┘        │  + beat      │
                                                           └──────┬───────┘
        ┌───────────────┬───────────────┬──────────────┬─────────┴───────┐
        ▼               ▼               ▼              ▼                 ▼
   LLM (OpenAI-    Trends sources   Pexels/Pixabay   TTS (edge /     YouTube Data +
   compatible /    (YouTube, Google (licensed stock) OpenAI)         Analytics APIs
   OpenRouter)     Trends, Wikipedia)       │            │
                                            └─── FFmpeg ─┴──▶ MP4 9:16 + thumbnail ─▶ Storage (local / S3)
```

## Pipeline (per video)

State machine stored in `videos.status`:

```
planned ─▶ scripted ─▶ seo_ready ─▶ rendered ─▶ ready ─▶ publishing ─▶ published
   │          │            │           │          │           │
   └──────────┴────────────┴───────────┴──────────┴───────────┴──▶ failed (retry from last good stage)
```

| Stage       | Component            | Output                                  |
|-------------|----------------------|-----------------------------------------|
| planned     | AI Director          | idea + scheduled slot                   |
| scripted    | AI Scriptwriter      | hook, scenes, voice-over text           |
| seo_ready   | AI SEO Manager       | title, description, hashtags, tags      |
| rendered    | Video Creator        | 1080x1920 MP4 + SRT/ASS subtitles       |
| ready       | Thumbnail Creator    | 1280x720 JPG                            |
| published   | YouTube Publisher    | YouTube video id, URL                   |

## Reliability

* **Restart safety** — every stage is idempotent and the state lives in Postgres. Tasks use
  `acks_late` + `reject_on_worker_lost`. A *watchdog* task re-dispatches videos whose stage lock
  has expired, so a crash mid-render resumes automatically.
* **Stage locks** — a row-level compare-and-set (`processing_stage`, `locked_at`) prevents two
  workers from running the same stage on the same video.
* **Retries** — transient errors (HTTP 5xx, 429, network) use exponential backoff; persistent
  failures mark the video `failed` after `max_attempts`, visible in the dashboard with a Retry button.
* **No duplicate publishing** — (1) unique `idea.fingerprint`; (2) unique `videos.content_hash`;
  (3) unique `publications.video_id`; (4) before any upload retry the uploads playlist is checked
  for an already-uploaded copy; (5) `publishing` is entered via compare-and-set.
* **Auto-publish gate** — disabled until the owner activates it; activation runs a live check
  (YouTube token valid, channel reachable, LLM configured, settings complete). If the YouTube token
  is revoked later, auto-publish is paused automatically and an error is logged.

## Learning loop

`Analytics` pulls per-video metrics → `TopicPerformance` aggregates by topic/keyword/format/hook style
→ the **AI Director** and **Scriptwriter** receive a performance digest in their prompts
(what worked, what to avoid) → next plan. Exploration vs. exploitation is controlled by the
`explore_ratio` setting so the channel doesn't collapse into one topic.

## Content policy

Only original or licensed material is used: AI-written scripts, TTS voice-over, Pexels/Pixabay
footage (license metadata stored per video), procedurally generated backgrounds as fallback. The
system never downloads third-party videos for re-upload. Videos are flagged as AI-generated
content where the API allows (`containsSyntheticMedia`).
