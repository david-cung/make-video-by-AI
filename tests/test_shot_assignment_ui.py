from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from auto_video_editor.core.project import EditorProject
from auto_video_editor.ui.app import (
    _shot_rows,
    _summary,
    assign_row_asset,
    auto_match,
    build_app,
    clear_row_asset,
)


def make_image(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (320, 180), color).save(path)


class ShotAssignmentUiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "a858_scene_01"
        self.project = EditorProject.create(self.root)
        shots = []
        for index in range(15):
            shot_id = f"{index + 1:03d}"
            slot = "shared_visual" if index in {0, 14} else f"slot_{shot_id}"
            shots.append(
                {
                    "id": shot_id,
                    "start": float(index),
                    "end": float(index + 1),
                    "asset_slot": slot,
                    "asset_type": "image",
                    "motion": {"type": "static"},
                    "transition_in": {"type": "cut", "duration": 0},
                    "transition_out": {"type": "cut", "duration": 0},
                }
            )
        source = self.root / "source_storyboard.json"
        source.write_text(
            json.dumps(
                {
                    "version": 1,
                    "project": {"title": "A858 Scene 01", "width": 1920, "height": 1080, "fps": 30},
                    "shots": shots,
                }
            ),
            encoding="utf-8",
        )
        self.project.set_input("storyboard", source)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_direct_assignment_replace_clear_persistence_and_auto_match(self) -> None:
        rows = _shot_rows(self.project)
        self.assertEqual(len(rows), 15)
        self.assertTrue(all(row.status == "MISSING" for row in rows))
        self.assertEqual(_summary(rows), "**Shots: 15** · **Ready: 0** · **Missing: 15** · **Errors: 0**")

        first = self.root / "first.png"
        make_image(first, (30, 50, 80))
        token, message = assign_row_asset(str(self.root), "shared_visual", str(first), 0)
        self.assertEqual(token, 1)
        self.assertIn("Updated 2 shot(s)", message)
        self.project = EditorProject.open(self.root)
        shared_rows = [row for row in _shot_rows(self.project) if row.asset_slot == "shared_visual"]
        self.assertEqual([row.status for row in shared_rows], ["READY", "READY"])

        shot_cache = self.root / "cache" / "shots"
        relevant_one = shot_cache / "shot_001_final_old.mp4"
        relevant_two = shot_cache / "shot_015_preview_old.mp4"
        unrelated = shot_cache / "shot_003_final_keep.mp4"
        preview_cache = self.root / "cache" / "preview_shot_001.mp4"
        for cache in (relevant_one, relevant_two, unrelated, preview_cache):
            cache.write_bytes(b"cache")

        replacement = self.root / "replacement.jpg"
        make_image(replacement, (90, 20, 30))
        _, message = assign_row_asset(str(self.root), "shared_visual", str(replacement), token)
        self.assertIn("invalidated 3 cached render(s)", message)
        self.assertFalse(relevant_one.exists())
        self.assertFalse(relevant_two.exists())
        self.assertFalse(preview_cache.exists())
        self.assertTrue(unrelated.exists())
        self.project = EditorProject.open(self.root)
        self.assertEqual(self.project.asset_path("shared_visual").suffix, ".jpg")

        wrong_type = self.root / "wrong.mp4"
        wrong_type.write_bytes(b"not a video")
        _, error = assign_row_asset(str(self.root), "shared_visual", str(wrong_type), token)
        self.assertIn("expected image", error)
        self.assertEqual(self.project.asset_path("shared_visual").suffix, ".jpg")

        second = self.root / "second.png"
        make_image(second, (20, 100, 40))
        assign_row_asset(str(self.root), "slot_002", str(second), token)
        self.project = EditorProject.open(self.root)
        copied_second = self.project.asset_path("slot_002")
        second_cache = shot_cache / "shot_002_final_old.mp4"
        second_cache.write_bytes(b"cache")
        _, message = clear_row_asset(str(self.root), "slot_002", token)
        self.assertIn("media file was preserved", message)
        self.project = EditorProject.open(self.root)
        self.assertIsNone(self.project.asset_path("slot_002"))
        self.assertTrue(copied_second.exists())
        self.assertFalse(second_cache.exists())

        reopened = EditorProject.open(self.root)
        reopened_rows = _shot_rows(reopened)
        self.assertEqual(next(row for row in reopened_rows if row.shot_id == "001").status, "READY")
        self.assertEqual(next(row for row in reopened_rows if row.shot_id == "002").status, "MISSING")

        auto_asset = self.root / "assets" / "slot_003.png"
        make_image(auto_asset, (120, 80, 20))
        _, _, _, message = auto_match(str(self.root), token)
        # The cleared slot's preserved project-owned file is also eligible for
        # explicit auto-match, along with the newly added slot_003 file.
        self.assertIn("Auto-matched 2 slot(s)", message)
        reopened = EditorProject.open(self.root)
        self.assertEqual(next(row for row in _shot_rows(reopened) if row.shot_id == "003").status, "READY")

    def test_corrupt_media_is_rejected_without_changing_state(self) -> None:
        corrupt = self.root / "corrupt.png"
        corrupt.write_bytes(b"not an image")
        _, message = assign_row_asset(str(self.root), "slot_002", str(corrupt), 0)
        self.assertTrue(
            "Corrupt or unreadable media" in message or "No visual stream found" in message,
            message,
        )
        self.assertIsNone(self.project.asset_path("slot_002"))
        row = next(row for row in _shot_rows(EditorProject.open(self.root)) if row.shot_id == "002")
        self.assertEqual(row.status, "ERROR")
        self.assertIn("visual stream", row.reason)

    def test_gradio_app_builds_with_dynamic_shot_list(self) -> None:
        app = build_app()
        try:
            self.assertEqual(type(app).__name__, "Blocks")
            self.assertGreaterEqual(len(app.config.get("dependencies", [])), 8)
        finally:
            app.close()


if __name__ == "__main__":
    unittest.main()
