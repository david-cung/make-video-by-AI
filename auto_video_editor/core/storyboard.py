from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Motion, ProjectError, ProjectSettings, Shot, Storyboard, TextOverlay, Transition


def _number(value: Any, name: str, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProjectError(f'Field "{name}" must be a number.')
    return float(value)


def _integer(value: Any, name: str, default: int) -> int:
    number = _number(value, name, float(default))
    if not number.is_integer():
        raise ProjectError(f'Field "{name}" must be an integer.')
    return int(number)


def _transition(value: Any, field_name: str) -> Transition:
    if value is None:
        return Transition()
    if not isinstance(value, dict):
        raise ProjectError(f'Field "{field_name}" must be an object.')
    return Transition(
        type=str(value.get("type", "cut")),
        duration=_number(value.get("duration"), f"{field_name}.duration", 0.0),
    )


def load_storyboard(path: Path) -> Storyboard:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectError(f"Storyboard not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError(f"Could not read storyboard JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectError("Storyboard root must be a JSON object.")

    project_data = data.get("project", {})
    if not isinstance(project_data, dict):
        raise ProjectError('Field "project" must be an object.')
    project = ProjectSettings(
        title=str(project_data.get("title", "Untitled")),
        width=_integer(project_data.get("width"), "project.width", 1920),
        height=_integer(project_data.get("height"), "project.height", 1080),
        fps=_integer(project_data.get("fps"), "project.fps", 30),
    )

    raw_shots = data.get("shots")
    if not isinstance(raw_shots, list):
        raise ProjectError('Field "shots" must be an array.')
    shots: list[Shot] = []
    for index, item in enumerate(raw_shots):
        if not isinstance(item, dict):
            raise ProjectError(f"Shot at index {index} must be an object.")
        try:
            motion_data = item.get("motion") or {"type": "static"}
            if not isinstance(motion_data, dict):
                raise ProjectError('Field "motion" must be an object.')
            motion = Motion(
                type=str(motion_data.get("type", "static")),
                start_scale=(
                    _number(motion_data.get("start_scale"), "motion.start_scale")
                    if motion_data.get("start_scale") is not None
                    else None
                ),
                end_scale=(
                    _number(motion_data.get("end_scale"), "motion.end_scale")
                    if motion_data.get("end_scale") is not None
                    else None
                ),
            )
            overlay_data = item.get("overlay")
            overlay = None
            if overlay_data is not None:
                if not isinstance(overlay_data, dict):
                    raise ProjectError('Field "overlay" must be an object.')
                overlay = TextOverlay(
                    type=str(overlay_data.get("type", "text")),
                    text=str(overlay_data.get("text", "")),
                    position=str(overlay_data.get("position", "center")),
                    style=str(overlay_data.get("style", "label")),
                    start_offset=_number(overlay_data.get("start_offset"), "overlay.start_offset", 0.0),
                    end_offset=(
                        _number(overlay_data.get("end_offset"), "overlay.end_offset")
                        if overlay_data.get("end_offset") is not None
                        else None
                    ),
                )
            shots.append(
                Shot(
                    id=str(item["id"]),
                    start=_number(item.get("start"), "start"),
                    end=_number(item.get("end"), "end"),
                    asset_slot=str(item["asset_slot"]),
                    asset_type=str(item["asset_type"]).lower(),
                    motion=motion,
                    transition_in=_transition(item.get("transition_in"), "transition_in"),
                    transition_out=_transition(item.get("transition_out"), "transition_out"),
                    source_start=_number(item.get("source_start"), "source_start", 0.0),
                    short_video_behavior=str(item.get("short_video_behavior", "freeze_last_frame")),
                    source_audio=bool(item.get("source_audio", False)),
                    overlay=overlay,
                )
            )
        except KeyError as exc:
            raise ProjectError(f"Shot at index {index} is missing required field {exc}.") from exc

    music = data.get("music")
    if music is not None and not isinstance(music, dict):
        raise ProjectError('Field "music" must be an object.')
    sfx = data.get("sfx", [])
    if not isinstance(sfx, list):
        raise ProjectError('Field "sfx" must be an array.')
    return Storyboard(
        version=_integer(data.get("version"), "version", 1),
        project=project,
        shots=tuple(shots),
        music=music,
        sfx=tuple(sfx),
    )


def load_timeline_duration(path: Path) -> float:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        duration = data["duration"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
            raise ValueError("duration must be a positive number")
        return float(duration)
    except FileNotFoundError as exc:
        raise ProjectError(f"Timeline not found: {path}") from exc
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ProjectError(f"Invalid timeline JSON: {exc}") from exc
