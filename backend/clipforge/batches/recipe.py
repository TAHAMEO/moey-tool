"""What to do with every video of a batch: the same choices as a chat request, made once."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class BatchSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: now: each clip goes out as soon as it's ready · slots: one clip per posting time, day after day
    mode: Literal["now", "slots"] = "now"
    times: list[str] = Field(default_factory=lambda: ["18:00"], min_length=1, max_length=8)
    start_date: date | None = None  # first day with slots (default: the next free slot from now)

    @field_validator("times")
    @classmethod
    def _times(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            match = TIME_RE.match(item.strip())
            if not match:
                raise ValueError(f"Times look like 18:00 (got {item!r})")
            cleaned.append(f"{int(match.group(1)):02d}:{match.group(2)}")
        return sorted(set(cleaned))


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Plain-language instruction, read like a chat message ("3 Shorts under 40 seconds about pricing").
    instruction: str = Field("", max_length=2000)
    count: int | None = Field(None, ge=1, le=30)
    min_seconds: float | None = Field(None, ge=3, le=180)
    max_seconds: float | None = Field(None, ge=5, le=900)
    platforms: list[Literal["youtube", "instagram"]] = Field(default_factory=lambda: ["youtube", "instagram"], min_length=1)
    aspects: list[Literal["9:16", "1:1", "16:9"]] | None = None
    caption_style: Literal["bold_pop", "karaoke_fill", "clean_lower", "boxed", "kinetic"] | None = None
    focus: str | None = Field(None, max_length=200)
    #: drafts: just make clips · approve: ask before posting · auto: post without asking
    publish: Literal["drafts", "approve", "auto"] = "approve"
    privacy: Literal["private", "unlisted", "public"] | None = None  # YouTube visibility for this batch
    schedule: BatchSchedule = Field(default_factory=BatchSchedule)

    def to_request(self, db: Session, *, batch_id: str) -> dict[str, Any]:
        """Project request parameters, exactly like a chat request would produce."""
        from ..brain import intents
        from ..brain.chat import _request_params
        from ..services import app_settings

        clips = app_settings.get_section(db, "clips")
        parsed = intents.parse(self.instruction, has_attachments=True).to_dict() if self.instruction.strip() else {}
        params = _request_params(parsed, clips)
        explicit = {"count": self.count, "min_seconds": self.min_seconds, "max_seconds": self.max_seconds,
                    "aspects": self.aspects, "caption_style": self.caption_style, "focus": self.focus}
        params.update({k: v for k, v in explicit.items() if v is not None})
        params["platforms"] = list(self.platforms)
        params["publish_requested"] = self.publish != "drafts"
        params["auto_publish"] = self.publish == "auto"
        params["privacy"] = self.privacy
        params["batch_id"] = batch_id
        params["schedule_slots"] = self.publish != "drafts" and self.schedule.mode == "slots"
        params["placeholder_title"] = False
        return params

    def summary(self) -> str:
        parts = [f"{self.count} clip(s) each" if self.count else "clips"]
        parts.append("for " + " and ".join("YouTube" if p == "youtube" else "Instagram" for p in self.platforms))
        parts.append({"drafts": "kept as drafts", "approve": "posted after you approve",
                      "auto": "posted automatically"}[self.publish])
        if self.publish != "drafts" and self.schedule.mode == "slots":
            parts.append("one per slot at " + ", ".join(self.schedule.times))
        return ", ".join(parts)
