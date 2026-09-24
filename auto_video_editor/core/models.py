from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}
SUPPORTED_ASSET_TYPES = {"image", "video"}
SUPPORTED_MOTIONS = {
    "static",
    "slow_push_in",
    "slow_pull_out",
    "pan_left",
    "pan_right",
    "pan_up",
    "pan_down",
}
SUPPORTED_TRANSITIONS = {"cut", "fade", "cross_dissolve", "dip_to_black"}
SUPPORTED_OVERLAY_STYLES = {"hero", "subtitle", "label"}
SUPPORTED_OVERLAY_POSITIONS = {"center", "top", "bottom", "top_left", "bottom_left"}


class ProjectError(RuntimeError):
    """An error suitable for display in the UI."""


@dataclass(frozen=True)
class ProjectSettings:
    title: str = "Untitled"
    width: int = 1920
    height: int = 1080
    fps: int = 30


@dataclass(frozen=True)
class Motion:
    type: str = "static"
    start_scale: float | None = None
    end_scale: float | None = None


@dataclass(frozen=True)
class Transition:
    type: str = "cut"
    duration: float = 0.0


@dataclass(frozen=True)
class TextOverlay:
    type: str
    text: str
    position: str = "center"
    style: str = "label"
    start_offset: float = 0.0
    end_offset: float | None = None


@dataclass(frozen=True)
class Shot:
    id: str
    start: float
    end: float
    asset_slot: str
    asset_type: str
    motion: Motion = field(default_factory=Motion)
    transition_in: Transition = field(default_factory=Transition)
    transition_out: Transition = field(default_factory=Transition)
    source_start: float = 0.0
    short_video_behavior: str = "freeze_last_frame"
    source_audio: bool = False
    overlay: TextOverlay | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Storyboard:
    version: int
    project: ProjectSettings
    shots: tuple[Shot, ...]
    music: dict[str, Any] | None = None
    sfx: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration: float
    width: int | None
    height: int | None
    fps: float | None
    has_audio: bool
    format_name: str


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    message: str
    shot_id: str | None = None

    def __str__(self) -> str:
        prefix = f"Shot {self.shot_id}: " if self.shot_id else ""
        return f"{prefix}{self.message}"


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [item for item in self.issues if item.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [item for item in self.issues if item.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, message: str, shot_id: str | None = None) -> None:
        self.issues.append(ValidationIssue("error", message, shot_id))

    def add_warning(self, message: str, shot_id: str | None = None) -> None:
        self.issues.append(ValidationIssue("warning", message, shot_id))

