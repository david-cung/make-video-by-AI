from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import (
    ProjectError,
    SUPPORTED_IMAGE_EXTENSIONS,
    SUPPORTED_VIDEO_EXTENSIONS,
)


STATE_FILE = "project_state.json"
REQUIRED_DIRECTORIES = (
    "assets", "audio", "audio/music", "audio/sfx", "output", "cache", "cache/shots", "logs"
)
logger = logging.getLogger(__name__)


def _filesystem_error(operation: str, path: Path, exc: OSError) -> ProjectError:
    logger.error("Filesystem operation failed: %s at %s: %s", operation, path, exc, exc_info=exc)
    return ProjectError(f"Could not {operation} at {path}: {exc}")


def _ensure_directory(path: Path) -> None:
    operation = "create directory"
    try:
        if path.exists() or path.is_symlink():
            if not path.is_dir():
                kind = "a file" if path.is_file() else "a non-directory path"
                raise ProjectError(f"Cannot {operation} at {path}: existing path is {kind}.")
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _filesystem_error(operation, path, exc) from exc


def _ensure_project_directories(root: Path) -> None:
    _ensure_directory(root)
    for name in REQUIRED_DIRECTORIES:
        _ensure_directory(root / name)


@dataclass
class EditorProject:
    root: Path
    state: dict[str, Any]

    @classmethod
    def create(cls, root: Path) -> "EditorProject":
        root = root.expanduser().resolve()
        _ensure_project_directories(root)
        state_path = root / STATE_FILE
        if state_path.exists():
            return cls.open(root)
        project = cls(root, {"version": 1, "inputs": {}, "assets": {}})
        project.save()
        return project

    @classmethod
    def open(cls, root: Path) -> "EditorProject":
        root = root.expanduser().resolve()
        state_path = root / STATE_FILE
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ProjectError(f"No {STATE_FILE} found in {root}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(f"Could not open project: {exc}") from exc
        if not isinstance(state, dict):
            raise ProjectError(f"Invalid {STATE_FILE}: root must be an object.")
        _ensure_project_directories(root)
        state.setdefault("inputs", {})
        state.setdefault("assets", {})
        return cls(root, state)

    def save(self) -> None:
        target = self.root / STATE_FILE
        temporary = target.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            raise _filesystem_error("write project state", temporary, exc) from exc
        try:
            temporary.replace(target)
        except OSError as exc:
            raise _filesystem_error("replace project state", target, exc) from exc

    def resolve(self, value: str | Path) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (self.root / path).resolve()

    def relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root))
        except ValueError:
            return str(path.resolve())

    def input_path(self, name: str) -> Path | None:
        value = self.state.get("inputs", {}).get(name)
        return self.resolve(value) if value else None

    def set_input(self, name: str, source: Path, copy_into_project: bool = True) -> Path:
        if name not in {"narration", "timeline", "storyboard"}:
            raise ProjectError(f"Unknown project input: {name}")
        source = source.expanduser().resolve()
        if not source.is_file():
            raise ProjectError(f"Input file not found: {source}")
        names = {"narration": "final_voice" + source.suffix.lower(), "timeline": "timeline.json", "storyboard": "storyboard.json"}
        target = self.root / names[name] if copy_into_project else source
        if copy_into_project and source != target:
            try:
                shutil.copy2(source, target)
            except OSError as exc:
                raise ProjectError(f"Could not import {source.name}: {exc}") from exc
        self.state["inputs"][name] = self.relative(target)
        self.save()
        return target

    def asset_path(self, slot: str) -> Path | None:
        value = self.state.get("assets", {}).get(slot)
        return self.resolve(value) if value else None

    def clear_asset(self, slot: str) -> bool:
        """Remove an assignment without deleting the underlying media file."""
        if slot not in self.state.get("assets", {}):
            return False
        del self.state["assets"][slot]
        self.save()
        return True

    def invalidate_shot_cache(self, shot_ids: list[str]) -> int:
        """Delete only cached renders belonging to the supplied shot IDs."""
        removed = 0
        cache_root = self.root / "cache"
        shots_root = cache_root / "shots"
        for shot_id in set(shot_ids):
            safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", shot_id).strip("._") or "shot"
            candidates = list(shots_root.glob(f"shot_{safe_id}_*.mp4"))
            candidates.append(cache_root / f"preview_shot_{safe_id}.mp4")
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                try:
                    candidate.unlink()
                    removed += 1
                except OSError as exc:
                    raise _filesystem_error("invalidate shot cache", candidate, exc) from exc
        return removed

    def assign_asset(self, slot: str, source: Path, copy_into_project: bool = True) -> Path:
        if not slot or "/" in slot or "\\" in slot:
            raise ProjectError("Asset slot must be a simple non-empty name.")
        source = source.expanduser().resolve()
        extension = source.suffix.lower()
        if extension not in SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS:
            raise ProjectError(f"Unsupported asset format: {extension or '(none)'}")
        if not source.is_file():
            raise ProjectError(f"Asset file not found: {source}")
        target = self.root / "assets" / f"{slot}{extension}" if copy_into_project else source
        if copy_into_project and source != target:
            try:
                shutil.copy2(source, target)
            except OSError as exc:
                raise ProjectError(f"Could not import asset {source.name}: {exc}") from exc
        self.state["assets"][slot] = self.relative(target)
        self.save()
        return target

    def auto_match(self, slots: list[str]) -> tuple[list[str], dict[str, list[str]]]:
        assigned: list[str] = []
        ambiguous: dict[str, list[str]] = {}
        assets_dir = self.root / "assets"
        supported = SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS
        for slot in slots:
            if self.asset_path(slot) and self.asset_path(slot).is_file():
                continue
            matches = sorted(
                path for path in assets_dir.iterdir()
                if path.is_file() and path.stem.casefold() == slot.casefold() and path.suffix.lower() in supported
            )
            if len(matches) == 1:
                self.state["assets"][slot] = self.relative(matches[0])
                assigned.append(slot)
            elif len(matches) > 1:
                ambiguous[slot] = [path.name for path in matches]
        self.save()
        return assigned, ambiguous
