from __future__ import annotations

from auto_video_editor.core.models import Motion


def _scale_defaults(motion: Motion) -> tuple[float, float]:
    defaults = {
        "static": (1.0, 1.0),
        "slow_push_in": (1.0, 1.08),
        "slow_pull_out": (1.08, 1.0),
        "pan_left": (1.08, 1.08),
        "pan_right": (1.08, 1.08),
        "pan_up": (1.08, 1.08),
        "pan_down": (1.08, 1.08),
    }
    base_start, base_end = defaults[motion.type]
    return motion.start_scale or base_start, motion.end_scale or base_end


def image_motion_filter(motion: Motion, width: int, height: int, fps: int, frames: int) -> str:
    """Build a smooth, frame-indexed cover/crop motion filter."""
    start_scale, end_scale = _scale_defaults(motion)
    progress = f"on/{max(frames - 1, 1)}"
    zoom = f"{start_scale:.6f}+({end_scale - start_scale:.6f})*{progress}"
    x = "(iw-iw/zoom)/2"
    y = "(ih-ih/zoom)/2"
    if motion.type == "pan_left":
        x = f"(iw-iw/zoom)*(1-{progress})"
    elif motion.type == "pan_right":
        x = f"(iw-iw/zoom)*{progress}"
    elif motion.type == "pan_up":
        y = f"(ih-ih/zoom)*(1-{progress})"
    elif motion.type == "pan_down":
        y = f"(ih-ih/zoom)*{progress}"
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"zoompan=z='{zoom}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps}"
    )

