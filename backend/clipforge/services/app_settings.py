"""Owner-configurable application settings with validated defaults.

Settings are grouped into sections, each validated by a Pydantic model and
stored as one JSON row. Unknown keys are rejected so a typo cannot silently
disable a safety control.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from ..db.models import AppSetting
from ..security.audit import audit


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AISettings(_Section):
    local_first: bool = True
    allow_paid_apis: bool = True
    daily_budget_usd: float = Field(2.0, ge=0)
    monthly_budget_usd: float = Field(20.0, ge=0)
    per_job_budget_usd: float = Field(0.5, ge=0)
    consensus_max_providers: int = Field(2, ge=1, le=5)
    cloud_transcription_allowed: bool = False
    llm_rerank_clips: bool = True
    vision_analysis: Literal["off", "local", "allow_cloud"] = "local"


class PublishingSettings(_Section):
    policy: Literal["approval_required", "auto_publish", "draft_only"] = "approval_required"
    platforms: list[Literal["youtube", "instagram"]] = Field(default_factory=lambda: ["youtube", "instagram"])
    default_privacy: Literal["private", "unlisted", "public"] = "private"
    max_per_day_youtube: int = Field(3, ge=0, le=50)
    max_per_day_instagram: int = Field(3, ge=0, le=25)
    upload_youtube_captions: bool = True
    instagram_share_to_feed: bool = True
    youtube_category_id: str = "22"
    youtube_playlist_id: str | None = None
    made_for_kids: bool = False


class ControlSettings(_Section):
    paused: bool = False
    emergency_stop: bool = False
    disabled_integrations: list[str] = Field(default_factory=list)


class ClipSettings(_Section):
    default_count: int = Field(5, ge=1, le=30)
    min_seconds: float = Field(15, ge=3, le=180)
    max_seconds: float = Field(59, ge=5, le=900)
    aspects: list[Literal["9:16", "1:1", "16:9"]] = Field(default_factory=lambda: ["9:16"])
    caption_style: Literal["bold_pop", "karaoke_fill", "clean_lower", "boxed"] = "bold_pop"
    burn_captions: bool = True
    languages: list[str] = Field(default_factory=lambda: ["en"])
    remove_silences: bool = True
    min_silence_to_cut_s: float = Field(0.7, ge=0.2, le=5)
    punch_in_zoom: bool = True
    loudness_lufs: float = Field(-14.0, ge=-30, le=-8)
    music_volume_db: float = Field(-22.0, ge=-40, le=-6)
    use_licensed_music: bool = False


class BrandSettings(_Section):
    accent_color: str = Field("#FFD400", pattern=r"^#[0-9A-Fa-f]{6}$")
    watermark_library_item_id: str | None = None
    watermark_position: Literal["top_left", "top_right", "bottom_left", "bottom_right"] = "top_right"
    channel_name: str = ""


class AnalyticsSettings(_Section):
    collection_offsets_hours: list[float] = Field(default_factory=lambda: [1, 24, 72, 168, 672])


class RetentionSettings(_Section):
    source_media_days: int = Field(30, ge=1, le=3650)
    work_files_days: int = Field(7, ge=1, le=365)
    live_buffer_minutes: int = Field(120, ge=5, le=1440)
    keep_renders_days: int = Field(365, ge=1, le=3650)


class UISettings(_Section):
    show_diagnostics: bool = False


SECTIONS: dict[str, type[_Section]] = {
    "ai": AISettings,
    "publishing": PublishingSettings,
    "control": ControlSettings,
    "clips": ClipSettings,
    "brand": BrandSettings,
    "analytics": AnalyticsSettings,
    "retention": RetentionSettings,
    "ui": UISettings,
}


class SettingsError(ValueError):
    pass


def get_section(db: Session, name: str) -> Any:
    model = SECTIONS[name]
    row = db.get(AppSetting, name)
    stored = row.value if row is not None and isinstance(row.value, dict) else {}
    known = {k: v for k, v in stored.items() if k in model.model_fields}
    try:
        return model(**known)
    except ValidationError:
        return model()


def get_all(db: Session) -> dict[str, dict[str, Any]]:
    return {name: get_section(db, name).model_dump() for name in SECTIONS}


def update_section(db: Session, name: str, patch: dict[str, Any], *, user_id: uuid.UUID | None = None,
                   ip: str | None = None) -> Any:
    if name not in SECTIONS:
        raise SettingsError(f"Unknown settings section {name!r}")
    current = get_section(db, name).model_dump()
    merged = {**current, **patch}
    try:
        validated = SECTIONS[name](**merged)
    except ValidationError as exc:
        raise SettingsError(exc.errors(include_url=False)[0].get("msg", "Invalid value")) from exc
    row = db.get(AppSetting, name)
    if row is None:
        row = AppSetting(key=name, value=validated.model_dump(), updated_by=user_id)
        db.add(row)
    else:
        row.value = validated.model_dump()
        row.updated_by = user_id
    changed = {k: v for k, v in validated.model_dump().items() if current.get(k) != v}
    audit(db, "settings.update", user_id=user_id, target_type="settings", target_id=name, details=changed, ip=ip)
    db.flush()
    return validated
