from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from auto_video_editor.effects.image_motion import image_motion_filter
from auto_video_editor.effects.transitions import ffmpeg_transition

from .ffmpeg_builder import run_ffmpeg
from .media import probe_media, require_ffmpeg
from .models import ProjectError, Shot, Storyboard, Transition
from .project import EditorProject
from .storyboard import load_storyboard, load_timeline_duration
from .validator import format_report, validate_project


CACHE_VERSION = 3
Progress = Callable[[str], None]


@dataclass(frozen=True)
class RenderProfile:
    name: str
    width: int
    height: int
    fps: int
    preset: str
    crf: int


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "shot"


def _font_path() -> Path | None:
    candidates = (
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    return next((path for path in candidates if path.is_file()), None)


class RenderEngine:
    def __init__(self, project: EditorProject, progress: Progress | None = None):
        self.project = project
        self.progress = progress or (lambda _: None)
        self.log_path = project.root / "logs" / "latest_render.log"

    def _say(self, message: str) -> None:
        self.progress(message)

    def load_storyboard(self) -> Storyboard:
        path = self.project.input_path("storyboard")
        if path is None:
            raise ProjectError("Storyboard JSON is missing.")
        return load_storyboard(path)

    def profile(self, storyboard: Storyboard, preview: bool) -> RenderProfile:
        if not preview:
            return RenderProfile(
                "final", storyboard.project.width, storyboard.project.height,
                storyboard.project.fps, "medium", 18,
            )
        aspect = storyboard.project.width / storyboard.project.height
        width = min(960, storyboard.project.width)
        height = round(width / aspect)
        height -= height % 2
        width -= width % 2
        return RenderProfile("preview", width, height, storyboard.project.fps, "veryfast", 25)

    @staticmethod
    def frame_bounds(shot: Shot, fps: int) -> tuple[int, int]:
        return round(shot.start * fps), round(shot.end * fps)

    def _cache_key(self, shot: Shot, asset: Path, profile: RenderProfile) -> str:
        stat = asset.stat()
        start_frame, end_frame = self.frame_bounds(shot, profile.fps)
        payload = {
            "cache_version": CACHE_VERSION,
            "asset": str(asset.resolve()),
            "asset_size": stat.st_size,
            "asset_mtime_ns": stat.st_mtime_ns,
            "shot": asdict(shot),
            "profile": asdict(profile),
            "start_frame": start_frame,
            "end_frame": end_frame,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def _overlay_image(self, shot: Shot, profile: RenderProfile) -> Path | None:
        overlay = shot.overlay
        if not overlay:
            return None
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:
            raise ProjectError(
                "Text overlays require Pillow. Install project dependencies with: "
                "python3 -m pip install -r requirements.txt"
            ) from exc
        payload = json.dumps(
            {"overlay": asdict(overlay), "width": profile.width, "height": profile.height, "version": 1},
            sort_keys=True,
        )
        key = hashlib.sha256(payload.encode()).hexdigest()[:20]
        folder = self.project.root / "cache" / "overlays"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"overlay_{key}.png"
        if target.is_file():
            return target
        sizes = {
            "hero": max(32, round(profile.height * 0.105)),
            "subtitle": max(24, round(profile.height * 0.050)),
            "label": max(20, round(profile.height * 0.038)),
        }
        font_path = _font_path()
        try:
            font = ImageFont.truetype(str(font_path), sizes[overlay.style]) if font_path else ImageFont.load_default()
        except OSError:
            font = ImageFont.load_default()
        canvas = Image.new("RGBA", (profile.width, profile.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        stroke = max(1, round(profile.height * 0.003))
        bbox = draw.multiline_textbbox((0, 0), overlay.text, font=font, stroke_width=stroke, align="center")
        text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        margin_x, margin_y = round(profile.width * 0.06), round(profile.height * 0.08)
        if overlay.position == "center":
            x, y = (profile.width - text_width) // 2, (profile.height - text_height) // 2
        elif overlay.position == "top":
            x, y = (profile.width - text_width) // 2, margin_y
        elif overlay.position == "bottom":
            x, y = (profile.width - text_width) // 2, profile.height - text_height - margin_y
        elif overlay.position == "top_left":
            x, y = margin_x, margin_y
        else:
            x, y = margin_x, profile.height - text_height - margin_y
        padding = max(8, round(profile.height * 0.015))
        if overlay.style in {"subtitle", "label"}:
            draw.rounded_rectangle(
                (x - padding, y - padding, x + text_width + padding, y + text_height + padding),
                radius=max(4, padding // 2), fill=(0, 0, 0, 115),
            )
        draw.multiline_text(
            (x, y), overlay.text, font=font, fill=(245, 245, 245, 255),
            stroke_width=stroke, stroke_fill=(0, 0, 0, 205), align="center",
        )
        canvas.save(target)
        return target

    def shot_cache_path(self, shot: Shot, profile: RenderProfile) -> Path:
        asset = self.project.asset_path(shot.asset_slot)
        if asset is None or not asset.is_file():
            raise ProjectError(f'Shot {shot.id}: Missing asset for slot "{shot.asset_slot}".')
        key = self._cache_key(shot, asset, profile)
        folder = self.project.root / "cache" / "shots"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"shot_{_safe_id(shot.id)}_{profile.name}_{key[:16]}.mp4"

    def render_shot(self, shot: Shot, profile: RenderProfile, force: bool = False) -> tuple[Path, bool]:
        asset = self.project.asset_path(shot.asset_slot)
        if asset is None or not asset.is_file():
            raise ProjectError(f'Shot {shot.id}: Missing asset for slot "{shot.asset_slot}".')
        target = self.shot_cache_path(shot, profile)
        if target.is_file() and not force:
            return target, True
        start_frame, end_frame = self.frame_bounds(shot, profile.fps)
        frames = end_frame - start_frame
        if frames <= 0:
            raise ProjectError(f"Shot {shot.id}: duration is shorter than one output frame.")
        duration = frames / profile.fps
        command = ["ffmpeg", "-hide_banner", "-y", "-loglevel", "warning"]
        if shot.asset_type == "image":
            command += ["-loop", "1", "-framerate", str(profile.fps), "-i", str(asset)]
            filters = [image_motion_filter(shot.motion, profile.width, profile.height, profile.fps, frames)]
        else:
            if shot.short_video_behavior == "loop":
                command += ["-stream_loop", "-1"]
            if shot.source_start:
                command += ["-ss", f"{shot.source_start:.6f}"]
            command += ["-i", str(asset)]
            filters = [
                f"scale={profile.width}:{profile.height}:force_original_aspect_ratio=increase",
                f"crop={profile.width}:{profile.height}",
                f"fps={profile.fps}",
            ]
            if shot.short_video_behavior == "freeze_last_frame":
                filters.append(f"tpad=stop_mode=clone:stop_duration={duration:.6f}")
        base_filters = filters + [f"trim=duration={duration:.9f}", "setpts=PTS-STARTPTS", "setsar=1"]
        overlay_image = self._overlay_image(shot, profile)
        output_options: list[str]
        if overlay_image:
            command += ["-loop", "1", "-framerate", str(profile.fps), "-i", str(overlay_image)]
            overlay = shot.overlay
            assert overlay is not None
            start = overlay.start_offset
            end = overlay.end_offset if overlay.end_offset is not None else duration
            fade = min(0.3, max((end - start) / 4, 0.05))
            fade_out = max(start, end - fade)
            complex_filter = (
                f"[0:v]{','.join(base_filters)}[base];"
                f"[1:v]format=rgba,fade=t=in:st={start:.6f}:d={fade:.6f}:alpha=1,"
                f"fade=t=out:st={fade_out:.6f}:d={fade:.6f}:alpha=1,"
                f"trim=duration={duration:.9f},setpts=PTS-STARTPTS[title];"
                f"[base][title]overlay=0:0:enable='between(t,{start:.6f},{end:.6f})',format=yuv420p[outv]"
            )
            output_options = ["-filter_complex", complex_filter, "-map", "[outv]"]
        else:
            output_options = ["-vf", ",".join(base_filters + ["format=yuv420p"])]
        command += [
            "-an", *output_options,
            "-frames:v", str(frames),
            "-r", str(profile.fps),
            "-c:v", "libx264",
            "-preset", profile.preset,
            "-crf", str(profile.crf),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(target),
        ]
        run_ffmpeg(command, self.log_path, f"rendering shot {shot.id}")
        return target, False

    @staticmethod
    def _boundary_transition(previous: Shot, current: Shot) -> Transition:
        if current.transition_in.type != "cut":
            return current.transition_in
        return previous.transition_out

    def _assemble_visuals(
        self, storyboard: Storyboard, clips: list[Path], profile: RenderProfile, target: Path
    ) -> None:
        command = ["ffmpeg", "-hide_banner", "-y", "-loglevel", "warning"]
        for clip in clips:
            command += ["-i", str(clip)]
        filters: list[str] = []
        for index in range(len(clips)):
            filters.append(f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS[v{index}]")
        current_label = "v0"
        total_frames = self.frame_bounds(storyboard.shots[0], profile.fps)[1] - self.frame_bounds(storyboard.shots[0], profile.fps)[0]
        for index in range(1, len(clips)):
            transition = self._boundary_transition(storyboard.shots[index - 1], storyboard.shots[index])
            output_label = f"joined{index}"
            if transition.type == "cut" or transition.duration <= 0:
                filters.append(f"[{current_label}][v{index}]concat=n=2:v=1:a=0[{output_label}]")
            else:
                next_frames = self.frame_bounds(storyboard.shots[index], profile.fps)
                next_duration_frames = next_frames[1] - next_frames[0]
                transition_frames = max(1, round(transition.duration * profile.fps))
                transition_frames = min(transition_frames, next_duration_frames)
                transition_duration = transition_frames / profile.fps
                offset = total_frames / profile.fps
                padded = f"padded{index}"
                filters.append(
                    f"[{current_label}]tpad=stop_mode=clone:stop_duration={transition_duration:.9f}[{padded}]"
                )
                filters.append(
                    f"[{padded}][v{index}]xfade=transition={ffmpeg_transition(transition.type)}:"
                    f"duration={transition_duration:.9f}:offset={offset:.9f}[{output_label}]"
                )
            current_label = output_label
            bounds = self.frame_bounds(storyboard.shots[index], profile.fps)
            total_frames += bounds[1] - bounds[0]
        filters.append(f"[{current_label}]fps={profile.fps},trim=end_frame={total_frames},setpts=PTS-STARTPTS[outv]")
        command += [
            "-filter_complex", ";".join(filters),
            "-map", "[outv]",
            "-an",
            "-frames:v", str(total_frames),
            "-r", str(profile.fps),
            "-c:v", "libx264",
            "-preset", profile.preset,
            "-crf", str(profile.crf),
            "-pix_fmt", "yuv420p",
            str(target),
        ]
        run_ffmpeg(command, self.log_path, "building visual timeline")

    def _mux_audio(
        self, storyboard: Storyboard, visual: Path, target: Path, duration: float, profile: RenderProfile
    ) -> None:
        narration = self.project.input_path("narration")
        if narration is None:
            raise ProjectError("Narration audio is missing.")
        command = [
            "ffmpeg", "-hide_banner", "-y", "-loglevel", "warning",
            "-i", str(visual), "-i", str(narration),
        ]
        audio_map = "1:a:0"
        if storyboard.music:
            file_value = storyboard.music.get("file")
            if not file_value:
                raise ProjectError('Music configuration requires a "file".')
            music_path = self.project.resolve(str(file_value))
            if not music_path.is_file():
                raise ProjectError(f"Music file not found: {music_path}")
            volume_db = float(storyboard.music.get("volume_db", -24))
            fade_duration = min(2.0, duration / 4)
            command += ["-stream_loop", "-1", "-i", str(music_path)]
            audio_filter = (
                f"[1:a]atrim=0:{duration:.9f},asetpts=PTS-STARTPTS[narr];"
                f"[2:a]atrim=0:{duration:.9f},asetpts=PTS-STARTPTS,volume={volume_db}dB,"
                f"afade=t=in:st=0:d={fade_duration:.3f},"
                f"afade=t=out:st={max(0.0, duration - fade_duration):.6f}:d={fade_duration:.3f}[music];"
                f"[narr][music]amix=inputs=2:duration=first:normalize=0[aout]"
            )
            command += ["-filter_complex", audio_filter]
            audio_map = "[aout]"
        command += [
            "-map", "0:v:0", "-map", audio_map,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration:.9f}",
            "-movflags", "+faststart",
            str(target),
        ]
        run_ffmpeg(command, self.log_path, "muxing narration")

    def render(self, preview: bool = False, force: bool = False) -> Path:
        require_ffmpeg()
        self.log_path.write_text("Auto Documentary Video Editor render log\n", encoding="utf-8")
        storyboard = self.load_storyboard()
        self._say("Validating project...")
        report = validate_project(self.project, storyboard)
        if not report.ok:
            raise ProjectError(format_report(report))
        profile = self.profile(storyboard, preview)
        self._say(f"{len(storyboard.shots)} shots ready. Rendering at {profile.width}x{profile.height}...")
        clips: list[Path] = []
        cache_hits = 0
        for index, shot in enumerate(storyboard.shots, start=1):
            self._say(f"Rendering shot {index}/{len(storyboard.shots)} ({shot.id})...")
            clip, cache_hit = self.render_shot(shot, profile, force=force)
            clips.append(clip)
            cache_hits += int(cache_hit)
        self._say(f"Shot cache: {cache_hits} reused, {len(clips) - cache_hits} rendered.")
        output_dir = self.project.root / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        visual = self.project.root / "cache" / f"assembled_{profile.name}.mp4"
        self._say("Building timeline...")
        self._assemble_visuals(storyboard, clips, profile, visual)
        timeline = self.project.input_path("timeline")
        if timeline is None:
            raise ProjectError("Timeline JSON is missing.")
        duration = load_timeline_duration(timeline)
        target = output_dir / ("preview.mp4" if preview else "final.mp4")
        self._say("Muxing narration...")
        self._mux_audio(storyboard, visual, target, duration, profile)
        result = probe_media(target)
        tolerance = max(0.05, 1 / profile.fps + 0.02)
        if abs(result.duration - duration) > tolerance:
            raise ProjectError(
                f"Rendered duration {result.duration:.3f}s differs from narration timeline "
                f"{duration:.3f}s by more than {tolerance:.3f}s."
            )
        self._say(f"Complete: {target}")
        return target

    def preview_shot(self, shot_id: str) -> Path:
        storyboard = self.load_storyboard()
        shot = next((item for item in storyboard.shots if item.id == shot_id), None)
        if shot is None:
            raise ProjectError(f"Shot not found: {shot_id}")
        profile = self.profile(storyboard, preview=True)
        clip, _ = self.render_shot(shot, profile)
        target = self.project.root / "cache" / f"preview_shot_{_safe_id(shot.id)}.mp4"
        if clip != target:
            shutil.copy2(clip, target)
        return target
