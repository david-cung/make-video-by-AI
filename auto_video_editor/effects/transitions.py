from __future__ import annotations


FFMPEG_TRANSITIONS = {
    "fade": "fade",
    "cross_dissolve": "dissolve",
    "dip_to_black": "fadeblack",
}


def ffmpeg_transition(name: str) -> str:
    return FFMPEG_TRANSITIONS[name]

