"""Growth features: retention-weighted winners, whoosh sound effects, AI replies to comments, prompt rules."""
import shutil
import subprocess
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.models import AnalyticsSnapshot, Publication, Video, utcnow
from app.services import comment_replies as cr
from app.services import ffmpeg_utils as ff
from app.services import images, learning, llm, seo, scriptwriter, video_creator, youtube
from app.services.youtube import QuotaExceeded, YouTubeError
from app.settings_store import AppSettings, get_flag, get_settings, set_flag, update_settings

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")


# ------------------------------------------------------------------ winners: retention first
def _pub(db, title, views, likes, retention, hours=30):
    v = Video(title=title, status="published")
    db.add(v)
    db.flush()
    p = Publication(video_id=v.id, status="published", title=title, youtube_video_id=f"yt-{title}",
                    published_at=utcnow() - timedelta(hours=hours))
    db.add(p)
    db.flush()
    db.add(AnalyticsSnapshot(publication_id=p.id, views=views, likes=likes, comments=0, avg_view_percentage=retention))
    return p


def test_winners_prefer_retention_over_raw_views(db):
    _pub(db, "Fast but shallow", 1000, 50, 30.0)
    _pub(db, "Slower but watched to the end", 600, 30, 90.0)
    db.commit()
    assert [t["title"] for t in learning.top_performers(db)][0] == "Slower but watched to the end"


def test_weak_performers_listed_in_digest(db):
    for i, (views, ret) in enumerate([(100, 20.0), (200, 40.0), (300, 60.0), (400, 80.0)]):
        _pub(db, f"V{i}", views, views // 10, ret)
    db.commit()
    weak = learning.weak_performers(db)
    assert weak and weak[-1]["title"] == "V0"
    assert "WEAK" in learning.digest(db) and "V0" in learning.digest(db)


def test_no_weak_list_with_little_data(db):
    _pub(db, "A", 100, 5, 40.0)
    _pub(db, "B", 900, 90, 80.0)
    db.commit()
    assert learning.weak_performers(db) == []


# ------------------------------------------------------------------ sound effects
def test_cut_times_scene_changes_and_inner_shots():
    durs = [3.0, 2.0, 4.0]
    t = video_creator.cut_times(durs, {2: (2, 4.35)}, total=9.0)
    # scene changes at 3.0 and 5.0 (minus the lead), one hard cut inside scene 3 at 5.0 + 4.35 / 2
    assert t == [2.9, 4.9, 7.08]


def test_cut_times_respect_min_gap_and_end():
    t = video_creator.cut_times([1.0, 0.3, 0.3, 5.0], {}, total=6.6)
    assert t == [0.9]  # cuts at 1.3 / 1.6 are closer than SFX_MIN_GAP to 0.9, and 6.3 is too close to the end
    assert all(x <= 6.6 - 0.5 for x in t)


@needs_ffmpeg
def test_whoosh_sfx_track_and_mix(tmp_path):
    def run(*a):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *a], check=True)

    run("-f", "lavfi", "-i", "testsrc2=s=320x568:r=30:d=4", "-pix_fmt", "yuv420p", str(tmp_path / "v.mp4"))
    run("-f", "lavfi", "-i", "sine=frequency=180:duration=4", str(tmp_path / "voice.wav"))
    ff.make_whoosh(tmp_path / "w.wav")
    assert 0.4 < ff.audio_duration(tmp_path / "w.wav") < 0.6
    assert ff.make_sfx_track(tmp_path / "sfx.wav", tmp_path / "w.wav", [0.5, 2.0, 3.9, 9.0], 4.0) == 2  # 3.9 / 9.0 too late
    assert ff.make_sfx_track(tmp_path / "none.wav", tmp_path / "w.wav", [], 4.0) == 0
    assert abs(ff.audio_duration(tmp_path / "sfx.wav") - 4.0) < 0.2
    ff.make_music(tmp_path / "m.wav", 4.0, 1)
    for name, kw in {"fx": {"sfx": tmp_path / "sfx.wav"},
                     "both": {"sfx": tmp_path / "sfx.wav", "music": tmp_path / "m.wav"}}.items():
        out = tmp_path / f"{name}.mp4"
        ff.final_mix(tmp_path / "v.mp4", tmp_path / "voice.wav", None, out, 4.0, **kw)
        info = ff.probe(out)
        assert info["has_audio"] and info["has_video"] and abs(info["duration"] - 4.0) < 0.2


def test_shot_count_matches_cutting_rule():
    assert [ff.shot_count(d) for d in (1.5, 3.5, 5.6, 9.0)] == [1, 1, 2, 3]


# ------------------------------------------------------------------ prompts and styles
def test_image_styles_vary_by_video_but_stay_safe():
    styles = {images.pick_style(i) for i in range(12)}
    assert len(styles) == len(images.VISUAL_STYLES) > 3
    assert all("no text" in s for s in styles)
    assert images.pick_style(7) == images.pick_style(7)
    p = images.build_prompt("q", "n", "t", "a cat on a roof", style=images.pick_style(2))
    assert "claymation" in p and "a cat on a roof" in p


def test_script_prompt_has_loop_rule_and_recent_hooks(db):
    old = Video(title="old", script={"hook": "Кота зачислили в МЧС", "voiceover": "x"})
    new = Video(title="new")
    db.add_all([old, new])
    db.commit()
    text = scriptwriter._prompt(db, new, get_settings(db))[1]["content"]
    assert "LOOP RULE" in text and "VARIETY" in text and "Кота зачислили в МЧС" in text


def test_seo_prompt_asks_a_or_b_question(db):
    v = Video(title="t", script={"hook": "h", "voiceover": "v"})
    db.add(v)
    db.commit()
    assert "A-or-B" in seo._prompt(db, v, get_settings(db))[1]["content"]


def test_new_settings_defaults():
    s = AppSettings()
    assert s.sfx_enabled and 0.05 <= s.sfx_volume <= 1.0 and s.auto_reply_enabled and s.auto_reply_per_day == 15


# ------------------------------------------------------------------ AI replies
def test_clean_reply_rules():
    assert cr._clean_reply("Спасибо! А вы бы согласились?", "круто") == "Спасибо! А вы бы согласились?"
    assert cr._clean_reply("Заходи на www.site.ru", "x") == ""         # link
    assert cr._clean_reply("Подписывайся на канал", "x") == ""         # begging
    assert cr._clean_reply("Круто", "круто") == ""                      # parroting
    assert cr._clean_reply("", "x") == "" and cr._clean_reply("!!", "x") == ""
    assert len(cr._clean_reply("да " * 200, "x")) <= cr.MAX_REPLY_CHARS


def _thread(cid, text, author="VIEWER", replies=0, can_reply=True):
    return {"snippet": {"canReply": can_reply, "totalReplyCount": replies,
                        "topLevelComment": {"id": cid, "snippet": {"textOriginal": text, "authorDisplayName": "Ann",
                                                                    "authorChannelId": {"value": author}}}}}


@pytest.fixture
def replies_env(db, monkeypatch):
    v = Video(title="Кота оформили", status="published")
    db.add(v)
    db.flush()
    db.add(Publication(video_id=v.id, status="published", title="Кота оформили", youtube_video_id="YT1",
                       published_at=utcnow() - timedelta(hours=5)))
    db.commit()
    sent = []
    threads = [_thread("c1", "Очень смешно, жду продолжения!"), _thread("c2", "Моё же", author="OWN"),
               _thread("c3", "Уже отвечено", replies=1), _thread("c4", "смотри сайт www.spam.ru"),
               _thread("c5", "👍"), _thread("c6", "Закрыто", can_reply=False)]
    monkeypatch.setattr(youtube, "is_connected", lambda d: True)
    monkeypatch.setattr(youtube, "quota_blocked", lambda d: False)
    monkeypatch.setattr(youtube, "has_scope", lambda d, s: True)
    monkeypatch.setattr(youtube, "get_account", lambda d: SimpleNamespace(channel_id="OWN", scopes=youtube.COMMENT_SCOPE))
    monkeypatch.setattr(youtube, "list_comment_threads", lambda d, vid, max_results=30: threads)
    monkeypatch.setattr(youtube, "reply_to_comment", lambda d, pid, text: sent.append((pid, text)) or "R1")
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(cr.time, "sleep", lambda s: None)
    return sent


def test_replies_only_to_fresh_comments_of_others(db, monkeypatch, replies_env):
    asked = []

    def fake_llm(messages, **kw):
        asked.append(messages[1]["content"])
        return {"replies": [{"id": "c1", "reply": "Спасибо! Какой момент понравился больше всего?"}]}

    monkeypatch.setattr(llm, "chat_json", fake_llm)
    assert cr.process(db) == {"replied": 1}
    assert replies_env == [("c1", "Спасибо! Какой момент понравился больше всего?")]
    assert len(asked) == 1 and "c3" not in asked[0] and "c4" not in asked[0] and "c2" not in asked[0]
    assert get_flag(db, cr.F_DAY)["n"] == 1
    assert {"c1", "c4", "c5"} <= set(cr._seen(db))   # handled / never-answer comments are remembered
    assert cr.process(db) == {}                        # throttled: once per 15 minutes


def test_model_may_decline_and_is_not_asked_again(db, monkeypatch, replies_env):
    monkeypatch.setattr(llm, "chat_json", lambda m, **k: {"replies": [{"id": "c1", "reply": ""}]})
    assert cr.process(db) == {"declined": 1}
    assert replies_env == [] and "c1" in cr._seen(db)
    set_flag(db, cr.F_LAST, (utcnow() - timedelta(hours=1)).isoformat())  # next sweep is due
    monkeypatch.setattr(llm, "chat_json", lambda m, **k: pytest.fail("asked again about a declined comment"))
    assert cr.process(db) == {}


def test_daily_cap_and_switch(db, monkeypatch, replies_env):
    monkeypatch.setattr(llm, "chat_json", lambda m, **k: pytest.fail("must not call the model"))
    set_flag(db, cr.F_DAY, {"day": cr._today(), "n": 15})
    assert cr.process(db) == {"daily_cap": 1}
    set_flag(db, cr.F_LAST, "")
    update_settings(db, {"auto_reply_enabled": False})
    assert cr.process(db) == {}


def test_quota_and_missing_scope_stop_quietly(db, monkeypatch, replies_env):
    monkeypatch.setattr(llm, "chat_json", lambda m, **k: {"replies": [{"id": "c1", "reply": "Спасибо большое!"}]})
    monkeypatch.setattr(youtube, "reply_to_comment",
                        lambda *a: (_ for _ in ()).throw(QuotaExceeded("quota", 403, "quotaExceeded")))
    assert cr.process(db) == {"quota": 1}
    set_flag(db, cr.F_LAST, "")
    monkeypatch.setattr(youtube, "has_scope", lambda d, s: False)
    assert cr.process(db) == {}


def test_failed_reply_is_remembered_not_retried(db, monkeypatch, replies_env):
    monkeypatch.setattr(llm, "chat_json", lambda m, **k: {"replies": [{"id": "c1", "reply": "Спасибо большое!"}]})
    monkeypatch.setattr(youtube, "reply_to_comment",
                        lambda *a: (_ for _ in ()).throw(YouTubeError("nope", 403, "forbidden")))
    assert cr.process(db) == {"failed": 1}
    assert "c1" in cr._seen(db)
