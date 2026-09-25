from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_video_editor.core.models import ProjectError
from auto_video_editor.core.project import EditorProject, _ensure_directory
from auto_video_editor.ui.app import create_project


class ProjectCreationTest(unittest.TestCase):
    def test_existing_folder_preserves_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "videos"
            root.mkdir()
            unrelated = root / "notes.txt"
            unrelated.write_text("keep me", encoding="utf-8")
            (root / "assets").mkdir()
            (root / "assets" / "image.png").write_bytes(b"existing")

            project = EditorProject.create(root)
            EditorProject.create(root)

            self.assertEqual(project.root, root.resolve())
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep me")
            self.assertEqual((root / "assets" / "image.png").read_bytes(), b"existing")
            for name in ("assets", "audio", "audio/music", "audio/sfx", "output", "cache", "cache/shots", "logs"):
                self.assertTrue((root / name).is_dir(), name)

    def test_existing_file_named_assets_reports_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "videos"
            root.mkdir()
            assets = root / "assets"
            assets.write_text("keep me", encoding="utf-8")

            with self.assertRaisesRegex(ProjectError, r"Cannot create directory.*assets.*existing path is a file"):
                EditorProject.create(root)

            self.assertEqual(assets.read_text(encoding="utf-8"), "keep me")
            self.assertFalse((root / "project_state.json").exists())
            self.assertIn(f"Cannot create directory at {root.resolve() / 'assets'}", create_project(str(root))[1])

    def test_existing_file_named_audio_reports_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "videos"
            root.mkdir()
            (root / "audio").write_text("keep me", encoding="utf-8")

            with self.assertRaisesRegex(ProjectError, r"Cannot create directory.*audio.*existing path is a file"):
                EditorProject.create(root)

            self.assertEqual((root / "audio").read_text(encoding="utf-8"), "keep me")

    def test_mkdir_permission_error_logs_operation_and_path(self) -> None:
        path = Path(r"C:\code\videos\assets")
        error = PermissionError(13, "Access is denied", str(path), 5)
        with patch.object(Path, "mkdir", side_effect=error):
            with self.assertLogs("auto_video_editor.core.project", level="ERROR") as logs:
                with self.assertRaises(ProjectError) as raised:
                    _ensure_directory(path)

        self.assertIn(f"create directory at {path}", str(raised.exception))
        self.assertIn("[WinError 5]", str(raised.exception))
        self.assertIn(f"create directory at {path}", logs.output[0])


if __name__ == "__main__":
    unittest.main()
