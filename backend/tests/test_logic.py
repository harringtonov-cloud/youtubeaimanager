"""Unit tests for pure logic: JSON extraction, subtitles, script/SEO validation, analytics math, timings."""
import httpx
import pytest

from app.services import analytics, llm, scriptwriter, seo, subtitles, video_creator
from app.settings_store import AppSettings
from app.stages import PermanentError


# ------------------------------------------------------------------ llm
def test_extract_json_variants():
    assert llm.extract_json('{"a": 1}') == {"a": 1}
    assert llm.extract_json('```json\n{"a": [1,2,]}\n```') == {"a": [1, 2]}
    assert llm.extract_json('Sure! Here you go:\n{"ideas": [{"t": "x"}]}\nHope it helps') == {"ideas": [{"t": "x"}]}
    assert llm.extract_json("[1, 2, 3]") == [1, 2, 3]
    with pytest.raises(ValueError):
        llm.extract_json("no json here")


def _resp(status, **kw):
    return httpx.Response(status, request=httpx.Request("POST", "http://x"), **kw)


def test_llm_error_classification(monkeypatch):
    def call(status, **kw):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp(status, **kw))
        return llm._call_once("m", [], 0.5, False, None, 5)

    with pytest.raises(llm.TransientLLMError):
        call(429, json={"error": {"message": "slow down"}})
    with pytest.raises(llm.TransientLLMError):
        call(503, text="down")
    with pytest.raises(PermanentError):
        call(401, json={"error": {"message": "bad key"}})
    with pytest.raises(PermanentError):
        call(402, json={"error": {"message": "no credits"}})
    with pytest.raises(llm.TransientLLMError):  # empty answer is retried
        call(200, json={"choices": [{"message": {"content": "  "}}]})
    assert call(200, json={"choices": [{"message": {"content": "hi"}}]}) == "hi"


def test_llm_json_mode_fallback(monkeypatch):
    seen = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append("response_format" in json)
        if "response_format" in json:
            return _resp(400, text="response_format is not supported by this model")
        return _resp(200, json={"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert llm._call_once("m", [], 0.5, True, None, 5) == "{}"
    assert seen == [True, False]


# ------------------------------------------------------------------ subtitles
def test_chunking_and_srt():
    words = subtitles.estimate_word_timings("Honey never spoils, and that is a fact. Wow.", 6.0)
    assert words[0]["start"] == 0 and abs(words[-1]["end"] - 6.0) < 0.01
    chunks = subtitles.chunk_words(words)
    assert all(1 <= len(c) <= subtitles.MAX_WORDS_PER_CHUNK for c in chunks)
    assert all(len(" ".join(w["text"] for w in c)) <= subtitles.MAX_CHARS_PER_LINE + 8 for c in chunks)
    srt = subtitles.to_srt(chunks)
    assert srt.startswith("1\n00:00:00,000 --> ") and "Honey" in srt


def test_ass_is_safe_and_styled():
    chunks = subtitles.chunk_words(subtitles.estimate_word_timings("Braces {evil} and \\ slashes work", 3.0))
    ass = subtitles.to_ass(chunks, emphasis=[{"text": "BIG {x}", "start": 0.2, "end": 2.0}])
    assert "PlayResX: 1080" in ass and "Style: Cap," in ass and "Style: Emph," in ass
    body = ass.split("[Events]")[1]
    assert "{evil}" not in body and "(evil)" in body  # user text cannot inject ASS override tags
    assert ass.count("Dialogue:") >= len(chunks)


# ------------------------------------------------------------------ script validation
def _scenes(n_words_each, n=5):
    return [{"voiceover": " ".join(["word"] * n_words_each) + ".", "visual_query": "city skyline!!",
             "on_screen_text": "wow"} for _ in range(n)]


def test_script_validation():
    s = AppSettings()
    ok, problem = scriptwriter.validate({"title": "T", "scenes": _scenes(16)}, s)
    assert not problem and ok["word_count"] == 80 and ok["scenes"][0]["visual_query"] == "city skyline"
    assert scriptwriter.validate({"scenes": _scenes(3)}, s)[1].startswith("voice-over is too short")
    assert "too long" in scriptwriter.validate({"scenes": _scenes(60)}, s)[1]
    assert scriptwriter.validate({"scenes": _scenes(16, n=2)}, s)[1].startswith("too few scenes")
    assert scriptwriter.validate("garbage", s)[1]


# ------------------------------------------------------------------ SEO sanitising
def test_seo_sanitize_limits():
    s = AppSettings()
    out = seo.sanitize(
        {"title": "A" * 200 + "<script>", "description": "Great #stuff here\n\n\n\nmore", "hashtags": ["Science Facts", "#Shorts", "x"],
         "tags": ["tag" + str(i) * 10 for i in range(100)], "category_id": "999",
         "keywords": {"primary": "honey", "secondary": ["a", "b"]}, "thumbnail": {"text": "HONEY NEVER SPOILS!!!!!!!!!!!!!!", "mood": "weird"}},
        s, "fallback")
    assert len(out["title"]) <= seo.TITLE_MAX and "<" not in out["title"]
    assert out["category_id"] == "27"  # invalid id -> settings default
    assert out["hashtags"][-1] == "#Shorts" and out["hashtags"].count("#Shorts") == 1
    assert sum(len(t) + 1 for t in out["tags"]) <= 500 and "shorts" in [t.lower() for t in out["tags"]]
    assert "#stuff" not in out["description"].split("\n\n#")[0] and out["description"].endswith("#Shorts")
    assert out["thumbnail"]["mood"] == "curious" and len(out["thumbnail"]["text"]) <= 24


# ------------------------------------------------------------------ analytics math
def test_percentile_scores():
    assert analytics.percentile_scores([]) == []
    assert analytics.percentile_scores([5]) == [50.0]
    assert analytics.percentile_scores([10, 20, 30]) == [0.0, 50.0, 100.0]
    assert analytics.percentile_scores([5, 5, 5]) == [50.0, 50.0, 50.0]


def test_video_scores_rank_best_highest():
    rows = [{"views": 100, "retention": 40, "engagement": 0.02},
            {"views": 5000, "retention": 85, "engagement": 0.09},
            {"views": 900, "retention": 60, "engagement": 0.04}]
    sc = analytics.video_scores(rows)
    assert sc[1] > sc[2] > sc[0] and sc[1] == pytest.approx(100.0) and sc[0] == 0.0


# ------------------------------------------------------------------ timeline
def test_scene_durations_sum_and_minimum():
    words = [{"text": f"w{i}", "start": i * 0.4, "end": i * 0.4 + 0.3} for i in range(30)]
    d = video_creator.scene_durations(["a b c d e", "f g h i j k l", "m n o p", "q " * 12], words, 12.0)
    assert abs(sum(d) - 12.0) < 1e-6 and all(x > 0.9 for x in d)
    d = video_creator.scene_durations(["one two", "three four five six"], [], 10.0)  # no word timings
    assert abs(d[0] - 10 / 3) < 0.01 and abs(sum(d) - 10.0) < 1e-6
