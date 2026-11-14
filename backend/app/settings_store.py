"""Owner-editable runtime settings, persisted in the DB (separate from env secrets)."""
from __future__ import annotations

import re
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from .models import Setting

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class AppSettings(BaseModel):
    # --- channel / content strategy ---
    niche: str = Field("абсурдные тёплые истории про официальные ситуации и необычные факты", max_length=300)
    audience: str = Field("русскоязычные зрители Shorts", max_length=300)
    language: str = Field("ru", max_length=10, description="Language of scripts, voice-over, SEO (ISO code)")
    tone: str = Field("energetic, curious, concise, no clickbait lies", max_length=300)
    forbidden_topics: str = Field(
        "politics and elections, violence, adult content, medical or financial advice, hate speech, tragedies",
        max_length=500,
    )

    # --- schedule ---
    videos_per_day: int = Field(1, ge=1, le=6)
    publish_times: list[str] = Field(default_factory=lambda: ["18:00"])
    plan_horizon_days: int = Field(3, ge=1, le=14)
    timezone: str = "UTC"

    # --- video ---
    min_video_seconds: int = Field(25, ge=10, le=170)
    max_video_seconds: int = Field(50, ge=15, le=175)
    visual_source: Literal["auto", "ai", "pexels", "pixabay", "generated"] = "auto"
    tts_provider: Optional[Literal["edge", "openai"]] = None  # None -> env TTS_PROVIDER
    tts_voice: str = ""  # empty -> sensible default for the language
    background_style: str = Field("dark-blue", max_length=40)
    music_enabled: bool = True
    music_volume: float = Field(0.12, ge=0.02, le=0.4)
    first_comment_enabled: bool = True
    sfx_enabled: bool = True   # short "вжух" whoosh on every picture change
    sfx_volume: float = Field(0.5, ge=0.05, le=1.0)
    auto_reply_enabled: bool = True          # AI replies to viewers' comments under our videos
    auto_reply_per_day: int = Field(15, ge=1, le=60)

    # --- long videos: the day after a Short is published, its story is expanded into a 16:9 video ---
    long_videos_enabled: bool = True
    long_video_minutes: int = Field(5, ge=3, le=10)

    # --- publishing ---
    auto_publish_enabled: bool = False
    require_manual_approval: bool = False
    privacy_status: Literal["public", "unlisted", "private"] = "public"
    category_id: str = "27"
    made_for_kids: bool = False
    synthetic_media_disclosure: bool = True

    # --- learning ---
    explore_ratio: float = Field(0.3, ge=0.0, le=1.0)
    trend_regions: list[str] = Field(default_factory=lambda: ["US"])
    max_attempts: int = Field(3, ge=1, le=8)

    @field_validator("publish_times")
    @classmethod
    def _check_times(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("publish_times must contain at least one HH:MM value")
        for t in v:
            if not _TIME_RE.match(t):
                raise ValueError(f"invalid time '{t}', expected HH:MM")
        return sorted(set(v))

    @field_validator("timezone")
    @classmethod
    def _check_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown timezone '{v}'") from e
        return v

    @field_validator("max_video_seconds")
    @classmethod
    def _check_max(cls, v: int, info) -> int:
        mn = info.data.get("min_video_seconds", 10)
        if v < mn:
            raise ValueError("max_video_seconds must be >= min_video_seconds")
        return v

    @model_validator(mode="after")
    def _sync_videos_per_day(self) -> "AppSettings":
        # the publish_times list is the single source of truth for the daily schedule
        self.videos_per_day = max(1, min(6, len(self.publish_times)))
        return self


_KEY = "app_settings"


def get_settings(db: Session) -> AppSettings:
    row = db.get(Setting, _KEY)
    data: dict[str, Any] = dict(row.value) if row and isinstance(row.value, dict) else {}
    if "timezone" not in data:
        from .config import get_config

        data["timezone"] = get_config().timezone
    if not get_flag(db, "ru_defaults_v1"):  # until the owner saves settings once, Russian is the default
        data.update({"language": "ru", "niche": AppSettings.model_fields["niche"].default,
                     "audience": AppSettings.model_fields["audience"].default})
    try:
        return AppSettings(**data)
    except Exception:
        # corrupted/legacy settings must never crash the pipeline -> fall back per-field
        clean = {k: v for k, v in data.items() if k in AppSettings.model_fields}
        for k in list(clean):
            try:
                AppSettings(**{k: clean[k]})
            except Exception:
                clean.pop(k)
        return AppSettings(**clean)


def update_settings(db: Session, patch: dict[str, Any]) -> AppSettings:
    current = get_settings(db).model_dump()
    current.update({k: v for k, v in patch.items() if k in AppSettings.model_fields})
    validated = AppSettings(**current)  # raises on invalid values
    row = db.get(Setting, _KEY)
    if row is None:
        row = Setting(key=_KEY, value=validated.model_dump())
        db.add(row)
    else:
        row.value = validated.model_dump()
    db.commit()
    set_flag(db, "ru_defaults_v1", True)
    return validated


def get_flag(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(Setting, f"flag:{key}")
    return row.value if row else default


def set_flag(db: Session, key: str, value: Any) -> None:
    row = db.get(Setting, f"flag:{key}")
    if row is None:
        db.add(Setting(key=f"flag:{key}", value=value))
    else:
        row.value = value
    db.commit()
