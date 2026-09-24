from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .models import MediaInfo, ProjectError


def require_ffmpeg() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise ProjectError(
            f"Required program{'s are' if len(missing) > 1 else ' is'} missing: {', '.join(missing)}. "
            "Install FFmpeg and ensure both ffmpeg and ffprobe are on PATH."
        )


def _fps(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    try:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return None


def probe_media(path: Path) -> MediaInfo:
    if not path.is_file():
        raise ProjectError(f"Media file not found: {path}")
    require_ffmpeg()
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,format_name:stream=codec_type,width,height,avg_frame_rate",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
    except (subprocess.CalledProcessError, json.JSONDecodeError, TypeError, ValueError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise ProjectError(f"Could not inspect media {path.name}: {detail}") from exc
    streams = data.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    return MediaInfo(
        path=path,
        duration=duration,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        fps=_fps(video.get("avg_frame_rate")) if video else None,
        has_audio=any(stream.get("codec_type") == "audio" for stream in streams),
        format_name=str(data.get("format", {}).get("format_name", "")),
    )

