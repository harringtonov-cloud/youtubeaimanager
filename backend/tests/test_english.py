"""The channel is English: defaults, migration of old Russian settings, idea language filter, prompts."""
from app.models import Idea, Setting, Video
from app.services import director, scriptwriter
from app.settings_store import AppSettings, get_settings, update_settings


def test_defaults_are_english(db):
    s = get_settings(db)
    assert s.language == "en" and s.long_video_language == "en" and s.tts_voice == ""
    assert "English" in s.audience and not any(ord(c) > 1000 for c in s.niche + s.audience)
    assert AppSettings().language == "en"


def test_old_russian_settings_move_to_english_until_the_owner_saves(db):
    db.add(Setting(key="app_settings", value={"language": "ru", "tts_voice": "ru-RU-DmitryNeural",
                                              "niche": "абсурдные истории", "videos_per_day": 1,
                                              "publish_times": ["18:00"]}))
    db.add(Setting(key="flag:ru_defaults_v1", value=True))  # the old flag must not keep Russian alive
    db.commit()
    s = get_settings(db)
    assert s.language == "en" and s.tts_voice == "" and "абсурд" not in s.niche
    assert s.publish_times == ["18:00"]                       # unrelated settings survive
    update_settings(db, {"publish_times": ["10:00"]})          # any save persists the English channel
    assert get_settings(db).language == "en"
    update_settings(db, {"language": "ru"})                    # a later explicit choice is respected
    assert get_settings(db).language == "ru"


def test_russian_ideas_are_rejected_on_an_english_channel(db):
    db.add_all([Idea(topic="Кота зачислили в МЧС", fingerprint="f1", status="new"),
                Idea(topic="Кот выбрал сам", fingerprint="f2", status="approved"),
                Idea(topic="A cat joined the fire department", fingerprint="f3", status="new")])
    db.commit()
    update_settings(db, {"language": "en"})
    assert director.reject_wrong_language(db) == 1
    db.expire_all()
    status = {i.fingerprint: i.status for i in db.query(Idea).all()}
    assert status == {"f1": "rejected", "f2": "approved", "f3": "new"}  # owner-approved ideas stay


def test_prompts_are_english(db):
    s = get_settings(db)
    p = director._user_prompt(s, [], "", [], 4)
    assert "never in English" not in p and "A cat was officially enrolled" in p and "Кота" not in p
    v = Video(title="t")
    db.add(v)
    db.commit()
    text = scriptwriter._prompt(db, v, s)[1]["content"]
    assert "Language of voice-over and on-screen text: English" in text and "Russian" not in text
    ru = director._user_prompt(AppSettings(language="ru"), [], "", [], 4)
    assert "Кота официально зачислили" in ru and "never in English" in ru
