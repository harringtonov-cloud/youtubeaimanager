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
