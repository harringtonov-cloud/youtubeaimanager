"""Long videos: planning (one per day, a day after the Short), chaptered script, per-format guards, Shorts-only learning."""
from datetime import timedelta

import pytest

from app.models import AnalyticsSnapshot, Publication, Video, READY, utcnow
from app.services import director, learning, llm, long_script, long_video, publisher, seo, youtube
from app.settings_store import AppSettings, update_settings


@pytest.fixture(autouse=True)
def _env(db, monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(youtube, "is_connected", lambda d: True)
    monkeypatch.setattr(youtube, "quota_blocked", lambda d: False)


def _short(db, title="Кота зачислили в МЧС", hours_ago=3, views=None, script=True):
    v = Video(title=title, status="published", script={"voiceover": f"{title}. Дальше было смешнее.", "hook": title}
              if script else None)
    db.add(v)
    db.flush()
    p = Publication(video_id=v.id, status="published", youtube_video_id=f"YT{v.id}", title=title,
                    published_at=utcnow() - timedelta(hours=hours_ago))
    db.add(p)
    db.flush()
    if views is not None:
        db.add(AnalyticsSnapshot(publication_id=p.id, views=views, likes=1, comments=0, avg_view_percentage=50.0))
    db.commit()
    return v


# ------------------------------------------------------------------ planning
def test_plan_creates_one_long_video_a_day_after_the_short(db):
    short = _short(db, hours_ago=3)
    ids = long_video.plan_long_videos(db)
    assert len(ids) == 1
    v = db.get(Video, ids[0])
    assert v.format == "long" and v.parent_video_id == short.id and v.idea_id is None and v.status == "planned"
    assert timedelta(hours=20) < v.scheduled_at - utcnow() < timedelta(hours=22)
    assert long_video.plan_long_videos(db) == []  # one per day


def test_plan_waits_for_the_short_to_be_live_for_an_hour(db):
    _short(db, hours_ago=0.2)
    assert long_video.plan_long_videos(db) == []


def test_plan_ignores_old_shorts_unscripted_and_disabled(db):
    _short(db, "Old", hours_ago=24 * 5)
    _short(db, "No script", hours_ago=3, script=False)
    assert long_video.plan_long_videos(db) == []
    _short(db, "Fresh", hours_ago=3)
    update_settings(db, {"long_videos_enabled": False})
    assert long_video.plan_long_videos(db) == []


def test_plan_picks_the_most_viewed_short_and_never_the_same_twice(db):
    _short(db, "Quiet", hours_ago=5, views=10)
    star = _short(db, "Star", hours_ago=6, views=5000)
    _short(db, "Newest", hours_ago=2, views=50)
    first = db.get(Video, long_video.plan_long_videos(db)[0])
    assert first.parent_video_id == star.id
    first.created_at = utcnow() - timedelta(hours=25)  # a day passes
    db.commit()
    second = db.get(Video, long_video.plan_long_videos(db)[0])
    assert second.parent_video_id != star.id  # the Star already has its long version


def test_long_videos_do_not_take_shorts_slots(db):
    from app.services.director import _taken_times

    short = _short(db)
    long_video.plan_long_videos(db)
    assert _taken_times(db, utcnow() + timedelta(days=3)) == []  # the long one is scheduled ~21h ahead, but is ignored
    assert short.id


# ------------------------------------------------------------------ script
def _fake_llm(chapter_words=30, scenes_per=3, chapters=4):
    def fake(messages, **kw):
        user = messages[-1]["content"]
        if "Plan the long video" in user:
            return {"title": "Полная история кота", "chapters": [
                {"title": f"Часть {i + 1}", "summary": "Что-то смешное."} for i in range(chapters)]}
        n = int(user.split("Write chapter ")[1].split(" ")[0])
        return {"scenes": [{"voiceover": " ".join(f"слово{n}{k}{i}" for i in range(chapter_words)) + ".",
                            "visual_query": "cat uniform", "image_prompt": "a cat in uniform", "on_screen_text": ""}
                           for k in range(scenes_per)]}
    return fake


def test_plan_sizes():
    p = long_script.plan(5, "ru")
    assert p["words"] == 690 and p["chapters"] == 6 and p["scenes_per_chapter"] == 4
    assert long_script.plan(3, "ru")["chapters"] == 4 and long_script.plan(10, "ru")["chapters"] == 10


def test_clean_scenes_drops_empty_and_sanitises():
    out = long_script.clean_scenes([{"voiceover": "  Привет   мир ", "visual_query": "cat!! roof", "image_prompt": "a cat <b>"},
                                    {"voiceover": ""}, "junk", {"voiceover": "ещё"}])
    assert [s["voiceover"] for s in out] == ["Привет мир", "ещё"]
    assert out[0]["visual_query"] == "cat roof" and "<" not in out[0]["image_prompt"]


def test_write_long_script_builds_chapters(db, monkeypatch):
    short = _short(db)
    update_settings(db, {"long_video_minutes": 3, "language": "ru"})
    v = Video(format="long", parent_video_id=short.id, title="x")
    db.add(v)
    db.commit()
    monkeypatch.setattr(llm, "chat_json", _fake_llm())
    long_script.write_long_script(db, v)
    db.refresh(v)
    assert v.script["format"] == "long" and len(v.script["scenes"]) == 12 and len(v.script["chapters"]) == 4
    assert v.script["chapters"][1]["scene"] == 3 and v.title == "Полная история кота" and v.content_hash
    assert v.script["word_count"] == 360


def test_long_video_is_in_its_own_language(db, monkeypatch):
    from app.settings_store import language_for

    short = _short(db)
    update_settings(db, {"long_video_minutes": 3, "language": "ru"})
    s = AppSettings(language="ru")
    assert s.long_video_language == "en"
    assert language_for(s, Video(format="long")) == "en" and language_for(s, Video(format="short")) == "ru"
    v = Video(format="long", parent_video_id=short.id)
    db.add(v)
    db.commit()
    prompts = []
    inner = _fake_llm()
    monkeypatch.setattr(llm, "chat_json", lambda m, **k: (prompts.append(m[-1]["content"]), inner(m, **k))[1])
    long_script.write_long_script(db, v)
    db.refresh(v)
    assert v.script["language"] == "en" and all("Language" in p and "English" in p for p in prompts)
    # the Short's own (Russian) story is passed as source material, but never as the language to write in
    assert any("Кота зачислили в МЧС" in p for p in prompts)


def test_short_version_link_follows_the_language(db):
    short = _short(db)
    v = Video(format="long", parent_video_id=short.id)
    db.add(v)
    db.commit()
    assert "Short version: https://youtube.com/shorts/YT" in seo._with_short_link(db, v, "Text\n\n#a", "en")
    assert "Короткая версия: " in seo._with_short_link(db, v, "Text\n\n#a", "ru")


def test_write_long_script_rejects_a_too_short_story(db, monkeypatch):
    short = _short(db)
    update_settings(db, {"long_video_minutes": 3, "language": "ru"})
    v = Video(format="long", parent_video_id=short.id)
    db.add(v)
    db.commit()
    monkeypatch.setattr(llm, "chat_json", _fake_llm(chapter_words=10, scenes_per=1))
    with pytest.raises(llm.TransientLLMError):
        long_script.write_long_script(db, v)


def test_write_long_script_needs_the_parent_story(db):
    from app.stages import PermanentError

    v = Video(format="long", parent_video_id=None)
    db.add(v)
    db.commit()
    with pytest.raises(PermanentError):
        long_script.write_long_script(db, v)


# ------------------------------------------------------------------ metadata, guards, learning
def test_seo_for_long_video_has_no_shorts_markers():
    s = AppSettings()
    data = {"title": "Полная история", "description": "Рассказ " * 400, "hashtags": ["история", "Shorts"],
            "tags": ["история", "shorts", "кот"]}
    out = seo.sanitize(data, s, "x", long=True)
    assert "#Shorts" not in out["description"] and "shorts" not in [t.lower() for t in out["tags"]]
    assert 900 < len(out["description"]) <= 1520
    short = seo.sanitize(data, s, "x")
    assert short["description"].endswith("#Shorts") and "shorts" in short["tags"]


def test_publish_guard_counts_formats_separately(db):
    update_settings(db, {"auto_publish_enabled": True, "publish_times": ["18:00"]})  # 1 Short a day (+1 extra)
    for i in range(2):  # two Shorts published in the last 24 h
        v = Video(title=f"s{i}", status="published")
        db.add(v)
        db.flush()
        db.add(Publication(video_id=v.id, status="published", youtube_video_id=f"S{i}"))
    def ready(title, fmt):
        v = Video(title=title, format=fmt, status=READY, video_key="k", seo={"title": title},
                  scheduled_at=utcnow() - timedelta(minutes=1))
        db.add(v)
        db.commit()
        return v
    short_due = ready("short due", "short")
    assert publisher.due_videos(db) == []               # Shorts allowance is used up
    short_due.status = "cancelled"
    long_due = ready("long due", "long")
    assert publisher.due_videos(db) == [long_due.id]    # ... but a long video is a separate allowance


def test_learning_uses_shorts_only(db):
    for fmt, views, ret in (("short", 100, 40.0), ("short", 900, 80.0), ("long", 99999, 99.0)):
        v = Video(title=f"{fmt}{views}", status="published", format=fmt)
        db.add(v)
        db.flush()
        p = Publication(video_id=v.id, status="published", title=f"{fmt}{views}", youtube_video_id=f"y{fmt}{views}",
                        published_at=utcnow() - timedelta(hours=30))
        db.add(p)
        db.flush()
        db.add(AnalyticsSnapshot(publication_id=p.id, views=views, likes=1, comments=0, avg_view_percentage=ret))
    db.commit()
    assert [t["title"] for t in learning.top_performers(db, n=5)] == ["short900", "short100"]


# ------------------------------------------------------------------ buttons: make a Short / make a long video from an idea
@pytest.fixture
def queued(monkeypatch):
    from app.workers import tasks

    q = []
    monkeypatch.setattr(tasks, "enqueue_stage", lambda vid, stage, countdown=0: q.append((vid, stage)))
    return q


def _idea(db, topic="Why honey never spoils", status="approved"):
    from app.models import Idea
    from app.services.text_utils import fingerprint

    i = Idea(topic=topic, angle="Tombs prove it", hook="This food survived 3000 years", status=status,
             fingerprint=fingerprint(topic))
    db.add(i)
    db.commit()
    return i


def test_make_long_video_from_an_idea(db, client, queued):
    idea = _idea(db)
    r = client.post(f"/api/videos/from-idea/{idea.id}", json={"format": "long"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "long"
    v = db.get(Video, body["id"])
    db.refresh(idea)
    assert v.source_idea_id == idea.id and v.idea_id is None and v.approved and v.parent_video_id is None
    assert timedelta(minutes=30) < v.scheduled_at - utcnow() < timedelta(minutes=50) and idea.status == "used"
    assert queued == [(v.id, "script")]
    assert client.post(f"/api/videos/from-idea/{idea.id}", json={"format": "long"}).status_code == 409
    items = client.get("/api/ideas").json()["items"]
    assert items[0]["long_video_id"] == v.id and items[0]["video_id"] is None


def test_the_same_idea_can_also_become_a_short(db, client, queued):
    idea = _idea(db)
    assert client.post(f"/api/videos/from-idea/{idea.id}", json={"format": "long"}).status_code == 200
    r = client.post(f"/api/videos/from-idea/{idea.id}")  # no body = a Short (the old behaviour)
    assert r.status_code == 200 and r.json()["format"] == "short"
    assert db.get(Video, r.json()["id"]).approved
    assert client.post(f"/api/videos/from-idea/{idea.id}", json={"format": "short"}).status_code == 409
    item = client.get("/api/ideas").json()["items"][0]
    assert item["video_id"] and item["long_video_id"]


def test_make_video_validates_input(db, client, queued):
    idea = _idea(db, status="rejected")
    assert client.post(f"/api/videos/from-idea/{idea.id}", json={"format": "long"}).status_code == 400
    ok = _idea(db, "Another topic about octopuses")
    assert client.post(f"/api/videos/from-idea/{ok.id}", json={"format": "movie"}).status_code == 422
    assert client.post("/api/videos/from-idea/9999", json={"format": "long"}).status_code == 404
    assert queued == []


def test_long_script_can_start_from_an_idea(db, monkeypatch):
    idea = _idea(db)
    update_settings(db, {"long_video_minutes": 3})
    v = Video(format="long", source_idea_id=idea.id, title=idea.topic)
    db.add(v)
    db.commit()
    prompts = []
    inner = _fake_llm()
    monkeypatch.setattr(llm, "chat_json", lambda m, **k: (prompts.append(m[-1]["content"]), inner(m, **k))[1])
    long_script.write_long_script(db, v)
    db.refresh(v)
    assert len(v.script["scenes"]) == 12 and v.script["format"] == "long"
    assert "about this idea" in prompts[0] and "This food survived 3000 years" in prompts[0]
    assert "Short we already published" not in prompts[1] and "accurate facts" in prompts[1]


def test_auto_planner_skips_a_short_whose_idea_already_has_a_long_video(db):
    from app.models import Idea

    idea = _idea(db, "Cat joined the fire brigade")
    short = _short(db, "Cat joined the fire brigade")
    short.idea_id = idea.id
    db.add(Video(format="long", source_idea_id=idea.id, title="made by hand", created_at=utcnow() - timedelta(hours=30)))
    db.commit()
    assert long_video.plan_long_videos(db) == []  # not blocked by the one-a-day rule: the idea is simply taken
    assert db.query(Video).filter(Video.format == "long").count() == 1 and Idea


# ------------------------------------------------------------------ the 50 MB storage limit
def test_rate_cap_for_a_five_minute_video_stays_under_the_limit():
    from app.services import ffmpeg_utils as ff

    args = ff.rate_cap_args(300, 44 * 1024 * 1024, 96_000)
    maxrate = int(args[args.index("-maxrate") + 1])
    assert maxrate * 300 / 8 + 96_000 * 300 / 8 < 44 * 1024 * 1024  # video + audio fit
    assert 800_000 < maxrate < 1_200_000


def test_final_mix_respects_max_bytes(tmp_path):
    import shutil
    import subprocess

    from app.services import ffmpeg_utils as ff

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=8",
                    "-vf", "noise=alls=40:allf=t", "-pix_fmt", "yuv420p", str(tmp_path / "v.mp4")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=180:duration=8",
                    str(tmp_path / "a.wav")], check=True)
    limit = 600_000
    ff.final_mix(tmp_path / "v.mp4", tmp_path / "a.wav", None, tmp_path / "o.mp4", 8.0, max_bytes=limit)
    assert (tmp_path / "o.mp4").stat().st_size <= limit * 1.1
    ff.final_mix(tmp_path / "v.mp4", tmp_path / "a.wav", None, tmp_path / "free.mp4", 8.0)
    assert (tmp_path / "free.mp4").stat().st_size > limit * 3  # without the cap the same video is far larger
