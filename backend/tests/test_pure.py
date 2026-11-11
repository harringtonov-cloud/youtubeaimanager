from datetime import datetime, timezone

from app.scheduling import next_slots, slots_between
from app.services import text_utils as tu


def test_fingerprint_dedupes_rephrasings():
    assert tu.fingerprint("Why is the sky blue?") == tu.fingerprint("sky blue, why")
    assert tu.fingerprint("Почему небо голубое") == tu.fingerprint("небо голубое?")
    assert tu.fingerprint("Black holes") != tu.fingerprint("White dwarfs")


def test_similarity():
    assert tu.is_similar("How do black holes form", ["Black holes: how they form"])
    assert not tu.is_similar("Octopus brains", ["Black holes: how they form"])


def test_clamp_and_hashtag():
    out = tu.clamp("word " * 100, 60)
    assert len(out) <= 60 and out.endswith("…")
    assert tu.to_hashtag("space facts") == "#SpaceFacts"


def test_slots_timezone():
    now = datetime(2026, 11, 5, 11, 50, tzinfo=timezone.utc)  # 16:50 Asia/Tashkent
    sl = next_slots(now, 3, ["09:00", "18:00"], "Asia/Tashkent")
    assert len(sl) == 6 and sl[0] == datetime(2026, 11, 5, 13, 0, tzinfo=timezone.utc)


def test_slots_dst():
    d = slots_between(
        datetime(2026, 3, 28, tzinfo=timezone.utc), datetime(2026, 3, 31, tzinfo=timezone.utc), ["12:00"], "Europe/Berlin"
    )
    assert d[0].hour == 11 and d[-1].hour == 10


def test_estimate_word_timings_respects_speech_window():
    from app.services.subtitles import estimate_word_timings

    w = estimate_word_timings("one two three four", 4.0, start=0.5)
    assert abs(w[0]["start"] - 0.5) < 1e-6 and abs(w[-1]["end"] - 4.5) < 0.01


def test_timings_from_sentences_reanchors_each_sentence():
    from app.services.subtitles import timings_from_sentences

    w = timings_from_sentences([{"text": "Hello there world.", "start": 0.4, "end": 1.6},
                                {"text": "Second sentence here now.", "start": 3.0, "end": 5.0}])
    assert len(w) == 7
    assert abs(w[0]["start"] - 0.4) < 1e-6 and abs(w[2]["end"] - 1.6) < 0.01
    assert abs(w[3]["start"] - 3.0) < 1e-6 and abs(w[-1]["end"] - 5.0) < 0.01  # gap between sentences is kept


def test_emphasis_skips_text_that_repeats_the_voiceover():
    from app.services.video_creator import emphasis_events

    scenes = [{"on_screen_text": "Saving Lives", "voiceover": "This technology is saving lives every day."},
              {"on_screen_text": "10x faster", "voiceover": "Results arrive in seconds."}]
    ev = emphasis_events(scenes, [4.0, 4.0])
    assert [e["text"] for e in ev] == ["10x faster"]
