"""End-to-end: idea -> plan -> script -> SEO -> render (real FFmpeg) -> thumbnail -> publish (YouTube mocked).

Only the network edges are faked (LLM, TTS audio source, stock search, YouTube). Everything else is real:
SQLite, the stage state machine, Celery task bodies (run eagerly), FFmpeg, Pillow, local storage.
"""
import shutil
import subprocess
from datetime import timedelta

import pytest

from app.crypto import encrypt
from app.models import (FAILED, PLANNED, PUBLISHED, READY, Idea, Publication, Video, YouTubeAccount, utcnow)
from app.services import director, ffmpeg_utils, llm, publisher, stock, subtitles, trends, tts, youtube
from app.settings_store import update_settings
from app.storage import get_storage
from app.workers import tasks

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")

IDEAS = {"ideas": [
    {"topic": "Why honey never spoils", "angle": "Tombs prove it", "hook": "This food survived 3000 years",
     "keywords": ["honey", "food science"], "category": "food", "hook_style": "shocking-fact", "potential": 9},
    {"topic": "How octopus brains work in their arms", "angle": "Distributed brain", "hook": "Most of its neurons are not in its head",
     "keywords": ["octopus", "neurons"], "category": "animals", "hook_style": "question", "potential": 8},
    {"topic": "The coldest place in the universe", "angle": "Boomerang Nebula", "hook": "Colder than space itself",
     "keywords": ["nebula", "cold"], "category": "space", "hook_style": "comparison", "potential": 7},
]}


def _script(topic):
    beats = [f"Here is a surprising fact about {topic.lower()} that most people never hear about at all.",
             "Scientists measured it carefully and the numbers were far stranger than anyone had expected before.",
             "The reason comes down to a simple trick of chemistry that nature perfected over millions of years.",
             "That means the thing you thought you understood is actually working in a completely different way.",
             "So next time you see it remember this tiny detail and follow for more strange science facts."]
    return {"title": topic, "hook": beats[0], "closing": beats[-1],
            "scenes": [{"voiceover": b, "visual_query": "nature macro", "on_screen_text": "WOW" if i == 1 else ""}
                       for i, b in enumerate(beats)]}


def fake_chat_json(messages, **kw):
    system, user = messages[0]["content"], messages[-1]["content"]
    if "creative director" in system:
        return IDEAS
    if "scriptwriter" in system:
        topic = user.split("Topic:")[1].split("\n")[0].strip()
        return _script(topic)
    if "SEO specialist" in system:
        topic = user.split("Topic:")[1].split("\n")[0].strip()
        return {"title": f"{topic}?", "description": f"All about {topic}.\n\nSubscribe!", "hashtags": ["science", "facts"],
                "tags": ["science", "facts", topic.lower()], "keywords": {"primary": "science", "secondary": ["facts"]},
                "category_id": "28", "thumbnail": {"text": "NO WAY", "subtext": "REALLY", "mood": "shock"}}
    raise AssertionError(f"unexpected LLM call: {system[:60]}")


def fake_tts(text, language, out_path, provider=None, voice=""):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=180:duration=11",
                    "-f", "wav", str(out_path)], check=True)
    return {"path": str(out_path), "duration": ffmpeg_utils.audio_duration(out_path),
            "words": subtitles.estimate_word_timings(text, 11.0), "provider": "fake", "voice": "fake"}


def fake_find_clip(query, min_duration, dest_dir, source="auto", exclude=None):
    """Pretend stock provider: a landscape clip that is shorter than the scene (exercises crop + loop)."""
    n = len(exclude or set())
    if n % 2 == 1:
        return None  # every other scene falls back to a generated background
    path = dest_dir / f"fake{n}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25:d=1.5",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return stock.Clip(path=str(path), provider="pexels", clip_id=f"pexels:{n}", page_url="https://pexels.test/v",
                      author="Tester", license="Pexels License", duration=1.5)


@pytest.fixture
def env(db, monkeypatch):
    queued = []
    monkeypatch.setattr(tasks, "enqueue_stage", lambda vid, stage, countdown=0: queued.append((vid, stage)))
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    monkeypatch.setattr(trends, "collect_signals", lambda db, regions, limit=40: [])
    monkeypatch.setattr(tts, "synthesize", fake_tts)
    monkeypatch.setattr(stock, "find_clip", fake_find_clip)
    uploads, thumbs = [], []
    monkeypatch.setattr(youtube, "upload_video", lambda db, path, snippet, status, on_progress=None:
                        (uploads.append((snippet, status)), f"YT{len(uploads)}")[1])
    monkeypatch.setattr(youtube, "set_thumbnail", lambda db, vid, path: thumbs.append(vid))
    monkeypatch.setattr(youtube, "find_recent_upload", lambda db, title, since: None)
    db.add(YouTubeAccount(channel_id="UC1", channel_title="Chan", refresh_token_enc=encrypt("rt"),
                          status="connected", uploads_playlist_id="UU1"))
    db.commit()
    update_settings(db, {"publish_times": ["10:00", "18:00"], "visual_source": "auto", "auto_publish_enabled": True})
    return {"queued": queued, "uploads": uploads, "thumbs": thumbs}


def _fresh(db, vid):
    db.commit()
    db.expire_all()
    return db.get(Video, vid)


def _run(vid, stage):
    res = tasks.run_stage.apply(args=[vid, stage])
    assert res.successful(), res.result
    return res.result


def test_full_pipeline_from_idea_to_published_video(db, env):
    # --- Director: ideas are created once, never duplicated
    assert director.generate_ideas(db) == 3
    assert director.generate_ideas(db) == 0
    assert db.query(Idea).count() == 3

    # --- planning fills free slots, one video per idea, no double-booking
    ids = director.plan_content(db)
    assert len(ids) == 3
    assert director.plan_content(db) == []  # nothing new until slots free up / ideas arrive
    vids = [_fresh(db, i) for i in ids]
    assert len({v.scheduled_at for v in vids}) == 3 and all(v.status == PLANNED for v in vids)
    vid = ids[0]

    # --- Scriptwriter -> SEO -> Video Creator -> Thumbnail (each stage chains to the next)
    assert _run(vid, "script") == "ok" and _fresh(db, vid).status == "scripted"
    v = _fresh(db, vid)
    assert v.script["word_count"] >= 50 and v.content_hash and len(v.script["scenes"]) == 5
    assert _run(vid, "seo") == "ok"
    v = _fresh(db, vid)
    assert v.status == "seo_ready" and v.seo["category_id"] == "28" and v.seo["description"].endswith("#Shorts")
    assert _run(vid, "render") == "ok"
    v = _fresh(db, vid)
    assert v.status == "rendered" and v.video_key and v.subtitle_key
    path = get_storage().path_for(v.video_key)
    info = ffmpeg_utils.probe(path)
    assert (info["width"], info["height"]) == (1080, 1920) and info["has_audio"] and info["vcodec"] == "h264"
    assert 11 <= info["duration"] <= 13 and abs(info["duration"] - v.duration_seconds) < 0.1
    kinds = [c["provider"] for c in v.assets["clips"]]
    assert "pexels" in kinds and "generated" in kinds  # licensed clip + licence-free fallback, both recorded
    assert all(c.get("license") for c in v.assets["clips"])
    assert "surprising" in get_storage().path_for(v.subtitle_key).read_text()  # SRT holds the narration
    assert _run(vid, "thumbnail") == "ok"
    v = _fresh(db, vid)
    assert v.status == READY and get_storage().exists(v.thumbnail_key)
    assert [st for v_, st in env["queued"] if v_ == vid] == ["seo", "render", "thumbnail"]

    # --- nothing is due yet (slot is in the future)
    assert publisher.due_videos(db) == []

    # --- due -> publish exactly once
    v.scheduled_at = utcnow() - timedelta(minutes=2)
    db.commit()
    assert publisher.due_videos(db) == [vid]
    assert _run(vid, "publish") == "ok"
    v = _fresh(db, vid)
    pub = db.query(Publication).filter_by(video_id=vid).one()
    assert v.status == PUBLISHED and pub.youtube_video_id == "YT1" and pub.url.endswith("/shorts/YT1")
    assert pub.status == "published" and pub.thumbnail_set and env["thumbs"] == ["YT1"]
    snippet, status = env["uploads"][0]
    assert snippet["title"] == v.seo["title"] and snippet["categoryId"] == "28"
    assert status["privacyStatus"] == "public" and status["containsSyntheticMedia"] is True

    # --- redelivered task / manual reset must NOT upload again
    v.status = READY
    db.commit()
    assert _run(vid, "publish") == "ok"
    assert len(env["uploads"]) == 1 and _fresh(db, vid).status == PUBLISHED
    assert db.query(Publication).count() == 1


def test_publishing_is_gated_by_owner_settings(db, env):
    director.generate_ideas(db)
    vid = director.plan_content(db)[0]
    v = _fresh(db, vid)
    v.status, v.scheduled_at = READY, utcnow() - timedelta(minutes=1)
    v.video_key, v.seo = "videos/x/video.mp4", {"title": "T"}
    db.commit()

    update_settings(db, {"auto_publish_enabled": False})
    assert publisher.due_videos(db) == []                       # auto-publish off -> never publishes by itself
    assert _run(vid, "publish") == "deferred" and _fresh(db, vid).status == READY and not env["uploads"]

    v = _fresh(db, vid)
    v.approved = True                                           # owner approves this one video
    db.commit()
    assert publisher.due_videos(db) == [vid]

    update_settings(db, {"auto_publish_enabled": True, "require_manual_approval": True})
    v.approved = False
    db.commit()
    assert publisher.due_videos(db) == []                       # manual-approval mode needs approval


def test_publisher_adopts_upload_from_crashed_attempt(db, env, monkeypatch):
    director.generate_ideas(db)
    vid = director.plan_content(db)[0]
    for stage in ("script", "seo", "render", "thumbnail"):
        _run(vid, stage)
    v = _fresh(db, vid)
    v.scheduled_at = utcnow() - timedelta(minutes=1)
    # simulate: a previous attempt started the upload, YouTube got the file, then the worker crashed
    db.add(Publication(video_id=vid, status="uploading", attempt_started_at=utcnow() - timedelta(minutes=5),
                       upload_attempts=1, title=v.title))
    db.commit()
    monkeypatch.setattr(youtube, "find_recent_upload", lambda db, title, since: "ALREADY_THERE")
    assert _run(vid, "publish") == "ok"
    pub = db.query(Publication).filter_by(video_id=vid).one()
    db.refresh(pub)
    assert pub.youtube_video_id == "ALREADY_THERE" and env["uploads"] == []  # no duplicate upload


def test_transient_upload_error_retries_then_succeeds(db, env, monkeypatch):
    director.generate_ideas(db)
    vid = director.plan_content(db)[0]
    for stage in ("script", "seo", "render", "thumbnail"):
        _run(vid, stage)
    v = _fresh(db, vid)
    v.scheduled_at = utcnow() - timedelta(minutes=1)
    db.commit()
    attempts = []

    def flaky(db_, path, snippet, status, on_progress=None):
        attempts.append(1)
        if len(attempts) == 1:
            raise youtube.YouTubeError("backend error", 503)
        return "YT_OK"

    monkeypatch.setattr(youtube, "upload_video", flaky)
    monkeypatch.setattr(youtube, "find_recent_upload", lambda db, title, since: None)
    tasks.run_stage.apply(args=[vid, "publish"])
    pub = db.query(Publication).filter_by(video_id=vid).one()
    db.refresh(pub)
    assert len(attempts) == 2 and pub.youtube_video_id == "YT_OK" and _fresh(db, vid).status == PUBLISHED


def test_permanent_error_fails_video_and_dashboard_retry_works(db, env, client, monkeypatch):
    director.generate_ideas(db)
    vid = director.plan_content(db)[0]
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: (_ for _ in ()).throw(llm.PermanentError("bad key")))
    assert _run(vid, "script") == "failed"
    v = _fresh(db, vid)
    assert v.status == FAILED and v.failed_stage == "script" and "bad key" in v.error
    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    assert client.post(f"/api/videos/{vid}/retry").json() == {"ok": True, "stage": "script"}
    assert _run(vid, "script") == "ok" and _fresh(db, vid).status == "scripted"
