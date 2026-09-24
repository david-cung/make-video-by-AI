from __future__ import annotations

from pathlib import Path

from .media import probe_media, require_ffmpeg
from .models import (
    SUPPORTED_ASSET_TYPES,
    SUPPORTED_IMAGE_EXTENSIONS,
    SUPPORTED_MOTIONS,
    SUPPORTED_OVERLAY_POSITIONS,
    SUPPORTED_OVERLAY_STYLES,
    SUPPORTED_TRANSITIONS,
    SUPPORTED_VIDEO_EXTENSIONS,
    ProjectError,
    Storyboard,
    ValidationReport,
)
from .project import EditorProject
from .storyboard import load_timeline_duration


def validate_project(project: EditorProject, storyboard: Storyboard, probe_assets: bool = True) -> ValidationReport:
    report = ValidationReport()
    try:
        require_ffmpeg()
    except ProjectError as exc:
        report.add_error(str(exc))

    settings = storyboard.project
    if settings.width < 16 or settings.height < 16 or settings.width % 2 or settings.height % 2:
        report.add_error("Project width and height must be even integers of at least 16 pixels.")
    if not 1 <= settings.fps <= 120:
        report.add_error("Project fps must be between 1 and 120.")
    if storyboard.version != 1:
        report.add_error(f"Unsupported storyboard version {storyboard.version}; expected 1.")
    if not storyboard.shots:
        report.add_error("Storyboard contains no shots.")

    narration = project.input_path("narration")
    timeline = project.input_path("timeline")
    if narration is None or not narration.is_file():
        report.add_error("Narration audio is missing.")
        narration_duration = None
    else:
        try:
            narration_info = probe_media(narration)
            narration_duration = narration_info.duration
            if not narration_info.has_audio:
                report.add_error("Narration file does not contain an audio stream.")
        except ProjectError as exc:
            narration_duration = None
            report.add_error(str(exc))
    if timeline is None or not timeline.is_file():
        report.add_error("Timeline JSON is missing.")
        timeline_duration = None
    else:
        try:
            timeline_duration = load_timeline_duration(timeline)
        except ProjectError as exc:
            timeline_duration = None
            report.add_error(str(exc))
    tolerance = max(0.05, 1.0 / max(settings.fps, 1))
    if narration_duration and timeline_duration and abs(narration_duration - timeline_duration) > tolerance:
        report.add_error(
            f"Timeline duration ({timeline_duration:.3f}s) differs from narration "
            f"({narration_duration:.3f}s) by more than {tolerance:.3f}s."
        )

    seen_ids: set[str] = set()
    previous = None
    for shot in storyboard.shots:
        if not shot.id.strip():
            report.add_error("Shot ID cannot be empty.")
        if not shot.asset_slot.strip():
            report.add_error("Asset slot cannot be empty.", shot.id)
        if shot.id in seen_ids:
            report.add_error(f'Duplicate shot ID "{shot.id}".', shot.id)
        seen_ids.add(shot.id)
        if shot.start < 0:
            report.add_error("Start time must be >= 0.", shot.id)
        if shot.end <= shot.start:
            report.add_error("End time must be greater than start time.", shot.id)
        if previous:
            delta = shot.start - previous.end
            previous_end_frame = round(previous.end * settings.fps)
            current_start_frame = round(shot.start * settings.fps)
            if current_start_frame < previous_end_frame:
                report.add_error(f"Overlaps previous shot by {-delta:.3f}s.", shot.id)
            elif current_start_frame > previous_end_frame:
                report.add_error(f"Leaves a {delta:.3f}s timeline gap before this shot.", shot.id)
            if (
                previous.transition_out.type != "cut"
                and shot.transition_in.type != "cut"
                and previous.transition_out != shot.transition_in
            ):
                report.add_error(
                    "Conflicting non-cut transitions are declared on both sides of this boundary.", shot.id
                )
        previous = shot
        if shot.asset_type not in SUPPORTED_ASSET_TYPES:
            report.add_error(f'Unsupported asset type "{shot.asset_type}".', shot.id)
        if shot.motion.type not in SUPPORTED_MOTIONS:
            report.add_error(f'Unsupported motion type "{shot.motion.type}".', shot.id)
        for scale_name, scale in (("start_scale", shot.motion.start_scale), ("end_scale", shot.motion.end_scale)):
            if scale is not None and not 1.0 <= scale <= 4.0:
                report.add_error(f"motion.{scale_name} must be between 1.0 and 4.0.", shot.id)
        if shot.transition_in.type not in SUPPORTED_TRANSITIONS:
            report.add_error(f'Unsupported transition "{shot.transition_in.type}".', shot.id)
        if shot.transition_out.type not in SUPPORTED_TRANSITIONS:
            report.add_error(f'Unsupported transition "{shot.transition_out.type}".', shot.id)
        for transition_name, transition in (("transition_in", shot.transition_in), ("transition_out", shot.transition_out)):
            if transition.duration < 0:
                report.add_error(f"{transition_name} duration cannot be negative.", shot.id)
            if transition.type != "cut" and transition.duration <= 0:
                report.add_error(f"{transition_name} requires a positive duration.", shot.id)
            if transition.duration > shot.duration:
                report.add_error(f"{transition_name} duration exceeds the shot duration.", shot.id)
        if shot.source_start < 0:
            report.add_error("source_start cannot be negative.", shot.id)
        if shot.short_video_behavior not in {"freeze_last_frame", "loop"}:
            report.add_error('short_video_behavior must be "freeze_last_frame" or "loop".', shot.id)
        if shot.source_audio:
            report.add_warning("source_audio is reserved for a later version; the visual asset will be muted.", shot.id)
        if shot.overlay:
            if shot.overlay.type != "text":
                report.add_error(f'Unsupported overlay type "{shot.overlay.type}".', shot.id)
            if shot.overlay.style not in SUPPORTED_OVERLAY_STYLES:
                report.add_error(f'Unsupported overlay style "{shot.overlay.style}".', shot.id)
            if shot.overlay.position not in SUPPORTED_OVERLAY_POSITIONS:
                report.add_error(f'Unsupported overlay position "{shot.overlay.position}".', shot.id)
            overlay_end = shot.overlay.end_offset if shot.overlay.end_offset is not None else shot.duration
            if shot.overlay.start_offset < 0 or overlay_end <= shot.overlay.start_offset or overlay_end > shot.duration + tolerance:
                report.add_error("Text overlay offsets must fall inside the shot and end after start.", shot.id)

        asset = project.asset_path(shot.asset_slot)
        if asset is None or not asset.is_file():
            report.add_error(f'Missing asset for slot "{shot.asset_slot}".', shot.id)
            continue
        extension = asset.suffix.lower()
        expected = SUPPORTED_IMAGE_EXTENSIONS if shot.asset_type == "image" else SUPPORTED_VIDEO_EXTENSIONS
        if extension not in expected:
            report.add_error(
                f'Asset "{asset.name}" does not match declared type "{shot.asset_type}".', shot.id
            )
            continue
        if probe_assets:
            try:
                info = probe_media(asset)
                if shot.asset_type == "video" and info.duration <= shot.source_start:
                    report.add_error(
                        f"source_start {shot.source_start:.3f}s is beyond the source duration {info.duration:.3f}s.", shot.id
                    )
                if info.width is None or info.height is None:
                    report.add_error(f'Asset "{asset.name}" contains no video/image stream.', shot.id)
            except ProjectError as exc:
                report.add_error(str(exc), shot.id)

    if storyboard.shots:
        first = storyboard.shots[0]
        if round(first.start * settings.fps) != 0:
            report.add_error(f"Timeline starts with a {first.start:.3f}s gap.", first.id)
        end = storyboard.shots[-1].end
        master_duration = narration_duration or timeline_duration
        if master_duration:
            if round(end * settings.fps) > round(master_duration * settings.fps):
                report.add_error(
                    f"Storyboard ends at {end:.3f}s, beyond narration duration {master_duration:.3f}s."
                )
            elif round(end * settings.fps) < round(master_duration * settings.fps):
                report.add_error(
                    f"Storyboard ends {master_duration - end:.3f}s before the narration."
                )
        if first.transition_in.type != "cut":
            report.add_warning("The first shot's transition_in has no preceding shot and is ignored.", first.id)
        last = storyboard.shots[-1]
        if last.transition_out.type != "cut":
            report.add_warning("The last shot's transition_out has no following shot and is ignored.", last.id)

    if storyboard.music:
        music_file = storyboard.music.get("file")
        if not isinstance(music_file, str) or not music_file.strip():
            report.add_error('Music configuration requires a non-empty "file".')
        elif not project.resolve(music_file).is_file():
            report.add_error(f"Music file not found: {project.resolve(music_file)}")
        try:
            volume = float(storyboard.music.get("volume_db", -24))
            if not -60 <= volume <= 0:
                report.add_error("Music volume_db must be between -60 and 0.")
        except (TypeError, ValueError):
            report.add_error("Music volume_db must be a number.")
    if storyboard.sfx:
        report.add_warning("SFX entries are reserved for a later version and will not be rendered.")
    return report


def format_report(report: ValidationReport) -> str:
    if not report.issues:
        return "Validation passed."
    lines = ["Validation failed:" if report.errors else "Validation passed with warnings:"]
    for issue in report.issues:
        lines.append(f"- {issue.severity.upper()}: {issue}")
    return "\n".join(lines)
