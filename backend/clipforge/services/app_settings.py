"""Owner-configurable application settings with validated defaults.

Settings are grouped into sections, each validated by a Pydantic model and
stored as one JSON row. Unknown keys are rejected so a typo cannot silently
disable a safety control.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
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
    llm_rerank_clips: bool = True


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
    #: IANA time zone used for schedules, "tomorrow at 6pm" and daily limits.
    timezone: str = "UTC"
    #: A held post whose time passed while CLIPFORGE was not running is still
    #: published if it is at most this late; otherwise it waits for the owner.
    late_grace_minutes: int = Field(180, ge=5, le=10080)

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown time zone {value!r}") from exc
        return value


class SourceSettings(_Section):
    #: Download videos anyone can watch from YouTube/Instagram links with yt-dlp (never private,
    #: members-only, age-restricted, paid or DRM videos; never with a login). The owner must have
    #: the rights to reuse what is downloaded.
    public_downloads: bool = True


class ControlSettings(_Section):
    paused: bool = False
    emergency_stop: bool = False
    disabled_integrations: list[str] = Field(default_factory=list)


class ClipSettings(_Section):
    default_count: int = Field(5, ge=1, le=30)
    min_seconds: float = Field(15, ge=3, le=180)
    max_seconds: float = Field(59, ge=5, le=900)
    aspects: list[Literal["9:16", "1:1", "16:9"]] = Field(default_factory=lambda: ["9:16"])
    caption_style: Literal["bold_pop", "karaoke_fill", "clean_lower", "boxed", "kinetic"] = "bold_pop"
    burn_captions: bool = True
    languages: list[str] = Field(default_factory=lambda: ["en"])
    remove_silences: bool = True
    min_silence_to_cut_s: float = Field(0.7, ge=0.2, le=5)
    punch_in_zoom: bool = True
    loudness_lufs: float = Field(-14.0, ge=-30, le=-8)
    music_volume_db: float = Field(-22.0, ge=-40, le=-6)
    use_licensed_music: bool = False


class ShortsSettings(_Section):
    """First-3-seconds hook and loop stages (docs/plans/SHORTS_RETENTION_PLAN.md). All off by
    default: existing projects render and publish exactly as before until switched on."""

    hook_scorer: bool = False          # score the first 0-3 s of every clip
    hook_reconstruct: bool = False     # remove the runway, first line within 1.5 s, frame-one text hook
    hook_gate: bool = False            # a clip whose hook stays weak is held for review, never auto-published
    hook_threshold: float = Field(0.55, ge=0, le=1)
    max_first_line_s: float = Field(1.5, ge=0.3, le=5)
    runway_search_s: float = Field(8.0, ge=1, le=30)
    text_hook: bool = True             # (with hook_reconstruct) bold 1-5 word text on frame one, middle third
    text_hook_seconds: float = Field(1.5, ge=0.5, le=4)
    tension_first: bool = False        # open on the clip's own question when the answer comes first
    loop_seam: bool = False            # score/adjust the end so the replay flows; bridge when it can't
    loop_threshold: float = Field(0.6, ge=0, le=1)
    loop_search_s: float = Field(3.0, ge=0.5, le=10)
    prefer_max_s: float = Field(30, ge=10, le=90)
    max_s: float = Field(58, ge=15, le=180)
    min_s: float = Field(12, ge=3, le=60)
    # P1 — retention inside the clip
    rehook: bool = False               # pattern interrupt (zoom + text) where a thought resolves, 20-25 s
    rehook_from_s: float = Field(15, ge=5, le=60)
    rehook_to_s: float = Field(30, ge=10, le=90)
    rehook_min_clip_s: float = Field(20, ge=10, le=120)
    rehook_zoom: bool = True
    rehook_text: bool = True
    rehook_framing: bool = True        # framing line ("NOW THE IMPORTANT PART") when the next line isn't a hook
    kinetic_captions: bool = False     # phrase-by-phrase captions; 1-3 power words per line pop
    seo_transcript: bool = False       # export the clip's full spoken transcript (.txt) next to SRT/VTT
    packaging_variants: bool = False   # 2-3 ranked title / first-frame text variants; the best one is used
    title_min_chars: int = Field(30, ge=10, le=90)
    title_max_chars: int = Field(50, ge=20, le=95)
    first_frame_words: int = Field(2, ge=1, le=5)
    # P2 — guardrails and feedback
    originality_gate: bool = False     # compare with the last posts; repeat -> other title option, else hold
    originality_threshold: float = Field(0.85, ge=0.5, le=1)
    originality_compare_last: int = Field(10, ge=5, le=30)
    originality_streak: int = Field(5, ge=0, le=20)  # same template this many times in a row -> flag (0 = off)
    retention_loop: bool = False       # early-retention metrics, flop flags, ranking of hook/title styles
    flop_viewed_rate: float = Field(0.55, ge=0, le=1)       # "viewed" (vs swiped away) below this
    flop_first_3s_retention: float = Field(0.6, ge=0, le=2)  # audience still there at 3 s below this
    flop_avg_viewed_pct: float = Field(40, ge=0, le=200)    # average percentage viewed below this
    learn_from_retention: bool = False  # nudge title-option ranking by what kept viewers (needs >= 3 posts per style)


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
    "sources": SourceSettings,
    "control": ControlSettings,
    "clips": ClipSettings,
    "shorts": ShortsSettings,
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
