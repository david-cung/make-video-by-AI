from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from .models import ProjectError


ProgressCallback = Callable[[str], None]


def run_ffmpeg(command: list[str], log_path: Path, description: str) -> None:
    """Run an argument-array FFmpeg command and retain detailed output in a log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{description}]\n")
        log.write("Command arguments:\n" + "\n".join(command) + "\n\n")
        log.flush()
        try:
            result = subprocess.run(command, stdout=log, stderr=log, text=True, check=False)
        except OSError as exc:
            raise ProjectError(f"Could not start FFmpeg while {description}: {exc}") from exc
    if result.returncode:
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        except OSError:
            tail = []
        detail = "\n".join(tail)
        raise ProjectError(
            f"FFmpeg failed while {description}. See {log_path}."
            + (f"\nLast log lines:\n{detail}" if detail else "")
        )
