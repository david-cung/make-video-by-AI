from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gradio as gr
from gradio.blocks import SessionState
from gradio.utils import get_upload_folder
from PIL import Image

from auto_video_editor.core.project import EditorProject
from auto_video_editor.ui.app import (
    _shot_rows,
    _summary,
    _thumbnail_for_asset,
    _video_for_ui,
    assign_row_asset,
    auto_match,
    build_app,
    clear_row_asset,
    range_overview,
    sync_range_controls,
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
            labels = {
                component["props"].get("value")
                for component in app.config["components"]
                if component["type"] == "button"
            }
            self.assertIn("Build Range Preview", labels)
            self.assertIn("Build Ready Prefix", labels)
        finally:
            app.close()

    def test_range_controls_show_duration_and_ready_count(self) -> None:
        first, last, summary = sync_range_controls(str(self.root), None, None)
        self.assertEqual(first["value"], "001")
        self.assertEqual(last["value"], "015")
        self.assertEqual(len(first["choices"]), 15)
        self.assertIn("Duration:** 15.00s", summary)
        image = self.root / "first.png"
        make_image(image, (30, 50, 80))
        assign_row_asset(str(self.root), "shared_visual", str(image))
        summary = range_overview(str(self.root), "001", "003")
        self.assertIn("Ready:** 1/3", summary)

    def test_project_change_populates_range_dropdowns(self) -> None:
        app = build_app()
        state = SessionState(app)

        async def exercise() -> None:
            event = next(
                fn for fn in app.fns.values()
                if fn.fn is sync_range_controls
            )
            result = await app.process_api(
                event, [str(self.root), None, None], state=state, session_hash="range-test"
            )
            first, last, summary = result["data"]
            self.assertEqual(first["value"], "001")
            self.assertEqual(last["value"], "015")
            self.assertIn("Duration:** 15.00s", summary)

        try:
            asyncio.run(exercise())
        finally:
            app.close()

    def test_thumbnail_is_cached_by_gradio_without_serving_the_original_file(self) -> None:
        image_path = self.root / "assets" / "opening_computer.png"
        make_image(image_path, (20, 40, 60))
        thumbnail = _thumbnail_for_asset(image_path)
        self.assertLessEqual(thumbnail.width, 145)
        self.assertLessEqual(thumbnail.height, 82)
        with gr.Blocks() as app:
            app.has_launched = True  # Exercise Gradio's runtime file-path check.
            component = gr.Image(value=thumbnail, interactive=False)
        try:
            cached_image = Path(component.value["path"])
            self.assertTrue(cached_image.is_file())
            self.assertNotEqual(cached_image.resolve(), image_path.resolve())
        finally:
            app.close()

    def test_video_from_external_project_is_copied_into_gradio_allowed_temp(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as external_project:
            video_path = Path(external_project) / "preview.mp4"
            video_path.write_bytes(b"example video output")
            with patch.object(Path, "cwd", return_value=Path("/outside-project")):
                ui_path = Path(_video_for_ui(video_path))
            self.assertNotEqual(ui_path, video_path)
            self.assertTrue(ui_path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()))
            self.assertEqual(ui_path.read_bytes(), video_path.read_bytes())

    def test_gradio_row_upload_updates_visible_status_and_shot_list(self) -> None:
        app = build_app()
        state = SessionState(app)

        async def exercise() -> None:
            initial = await app.process_api(
                0, [str(self.root), 0, "All", 1, 50], state=state, session_hash="upload-test"
            )
            initial_thumb = next(
                component for component in initial["render_config"]["components"]
                if component["props"].get("key") == "thumb-001-shared_visual"
            )
            self.assertEqual(initial_thumb["type"], "image")
            self.assertIsNone(initial_thumb["props"]["value"])
            upload_event = next(
                fn for fn in state.blocks_config.fns.values()
                if fn.rendered_in is not None and fn.fn.__name__ == "upload_for_row"
            )
            upload_folder = Path(get_upload_folder())
            upload_folder.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=upload_folder) as temporary:
                image_path = Path(temporary) / "chosen.png"
                make_image(image_path, (10, 30, 50))
                file_data = {
                    "path": str(image_path), "orig_name": image_path.name,
                    "size": image_path.stat().st_size, "meta": {"_type": "gradio.FileData"},
                }
                uploaded = await app.process_api(
                    upload_event, [str(self.root), file_data, 0],
                    state=state, session_hash="upload-test",
                )
            visible_status, token, message = uploaded["data"]
            self.assertIn("Ready: 2", visible_status)
            self.assertIsNone(token)  # Gradio keeps gr.State values on the server.
            state_ids = uploaded["changed_state_ids"]
            self.assertEqual(len(state_ids), 1)
            self.assertIn((state_ids[0], "change"), app.config["dependencies"][0]["targets"])
            self.assertIn("Assigned", message)
            refreshed = await app.process_api(
                0, [str(self.root), state[state_ids[0]], "All", 1, 50],
                state=state, session_hash="upload-test"
            )
            self.assertTrue(
                any("Ready: 2" in str(component) for component in refreshed["render_config"]["components"])
            )
            refreshed_thumb = next(
                component for component in refreshed["render_config"]["components"]
                if component["props"].get("key") == "thumb-001-shared_visual"
            )
            self.assertEqual(refreshed_thumb["type"], "image")
            self.assertIsInstance(refreshed_thumb["props"]["value"], dict)
            self.assertTrue(all(
                isinstance(component["props"]["value"], str)
                for component in refreshed["render_config"]["components"]
                if component["type"] == "markdown"
            ))
            preview_buttons = [
                component for component in refreshed["render_config"]["components"]
                if component["type"] == "button" and component["props"].get("value") == "Preview"
            ]
            self.assertEqual(sum(bool(button["props"].get("interactive")) for button in preview_buttons), 2)

        try:
            asyncio.run(exercise())
        finally:
            app.close()


if __name__ == "__main__":
    unittest.main()
