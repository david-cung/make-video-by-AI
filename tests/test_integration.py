from __future__ import annotations

from array import array
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace

from auto_video_editor.core.media import probe_media, require_ffmpeg
from auto_video_editor.core.models import StoryboardScope, Transition
from auto_video_editor.core.project import EditorProject
from auto_video_editor.core.renderer import RenderEngine
from auto_video_editor.core.storyboard import load_storyboard
from auto_video_editor.core.validator import format_report, validate_project
from auto_video_editor.ui.app import preview_shot, render_ready_prefix


def ffmpeg(*arguments: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


class IntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        require_ffmpeg()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "documentary"
        self.project = EditorProject.create(self.root)
        assets = self.root / "assets"
        ffmpeg("-f", "lavfi", "-i", "color=c=0x18324a:s=800x600", "-frames:v", "1", str(assets / "opening.png"))
        ffmpeg("-f", "lavfi", "-i", "color=c=0x5a1820:s=600x800", "-frames:v", "1", str(assets / "ending.png"))
        ffmpeg(
            "-f", "lavfi", "-i", "testsrc2=s=640x360:r=24:d=0.8",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=0.8",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(assets / "investigation.mp4"),
        )
        ffmpeg(
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
            "-c:a", "pcm_s16le", str(self.root / "source_voice.wav"),
        )
        timeline = {"duration": 4.0, "scenes": [{"scene_id": "01", "start": 0.0}], "events": []}
        (self.root / "source_timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
        storyboard = {
            "version": 1,
            "project": {"title": "Integration", "width": 640, "height": 360, "fps": 30},
            "shots": [
                {
                    "id": "001", "start": 0.0, "end": 1.2,
                    "asset_slot": "opening", "asset_type": "image",
                    "motion": {"type": "slow_push_in", "start_scale": 1.0, "end_scale": 1.08},
                    "transition_in": {"type": "cut", "duration": 0},
                    "transition_out": {"type": "cut", "duration": 0},
                    "overlay": {
                        "type": "text", "text": "A858", "position": "center", "style": "hero",
                        "start_offset": 0.1, "end_offset": 1.0
                    }
                },
                {
                    "id": "002", "start": 1.2, "end": 2.7,
                    "asset_slot": "investigation", "asset_type": "video", "source_start": 0.1,
                    "short_video_behavior": "freeze_last_frame", "motion": {"type": "static"},
                    "transition_in": {"type": "cross_dissolve", "duration": 0.3},
                    "transition_out": {"type": "cut", "duration": 0}
                },
                {
                    "id": "003", "start": 2.7, "end": 4.0,
                    "asset_slot": "ending", "asset_type": "image", "motion": {"type": "pan_right"},
                    "transition_in": {"type": "dip_to_black", "duration": 0.2},
                    "transition_out": {"type": "cut", "duration": 0}
                }
            ]
        }
        (self.root / "source_storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")
        self.project.set_input("narration", self.root / "source_voice.wav")
        self.project.set_input("timeline", self.root / "source_timeline.json")
        self.project.set_input("storyboard", self.root / "source_storyboard.json")
        for slot, filename in (
            ("opening", "opening.png"),
            ("investigation", "investigation.mp4"),
            ("ending", "ending.png"),
        ):
            self.project.assign_asset(slot, assets / filename, copy_into_project=False)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_validation_render_timing_audio_and_cache(self) -> None:
        storyboard = load_storyboard(self.project.input_path("storyboard"))
        report = validate_project(self.project, storyboard)
        self.assertTrue(report.ok, [str(issue) for issue in report.issues])

        messages: list[str] = []
        engine = RenderEngine(self.project, messages.append)
        output = engine.render(preview=False)
        info = probe_media(output)
        self.assertEqual((info.width, info.height), (640, 360))
        self.assertTrue(info.has_audio)
        self.assertLessEqual(abs(info.duration - 4.0), 0.06)

        cached_paths = [engine.shot_cache_path(shot, engine.profile(storyboard, False)) for shot in storyboard.shots]
        cached_mtimes = [path.stat().st_mtime_ns for path in cached_paths]
        messages.clear()
        engine.render(preview=False)
        self.assertIn("Shot cache: 3 reused, 0 rendered.", messages)
        self.assertEqual(cached_mtimes, [path.stat().st_mtime_ns for path in cached_paths])

        ending = self.project.asset_path("ending")
        os.utime(ending, None)
        changed_paths = [engine.shot_cache_path(shot, engine.profile(storyboard, False)) for shot in storyboard.shots]
        self.assertEqual(cached_paths[:2], changed_paths[:2])
        self.assertNotEqual(cached_paths[2], changed_paths[2])
        messages.clear()
        engine.render(preview=False)
        self.assertIn("Shot cache: 2 reused, 1 rendered.", messages)

        music_dir = self.root / "audio" / "music"
        ffmpeg(
            "-f", "lavfi", "-i", "sine=frequency=110:sample_rate=48000:duration=0.5",
            "-c:a", "pcm_s16le", str(music_dir / "ambient.wav"),
        )
        imported_storyboard = self.project.input_path("storyboard")
        data = json.loads(imported_storyboard.read_text(encoding="utf-8"))
        data["music"] = {"file": "audio/music/ambient.wav", "volume_db": -30}
        imported_storyboard.write_text(json.dumps(data), encoding="utf-8")
        messages.clear()
        mixed = engine.render(preview=False)
        self.assertTrue(probe_media(mixed).has_audio)
        self.assertIn("Shot cache: 3 reused, 0 rendered.", messages)

    def test_auto_match_ambiguity_is_not_guessed(self) -> None:
        project = EditorProject.create(Path(self.temporary.name) / "matching")
        assets = project.root / "assets"
        shutil.copy2(self.root / "assets" / "opening.png", assets / "slot.png")
        shutil.copy2(self.root / "assets" / "opening.png", assets / "slot.jpg")
        assigned, ambiguous = project.auto_match(["slot"])
        self.assertEqual(assigned, [])
        self.assertEqual(set(ambiguous["slot"]), {"slot.jpg", "slot.png"})
        self.assertIsNone(project.asset_path("slot"))

    def test_row_preview_returns_playable_video(self) -> None:
        video_path, message = preview_shot(str(self.root), "001")
        self.assertIsNotNone(video_path, message)
        self.assertIn("Shot preview ready", message)
        info = probe_media(Path(video_path))
        self.assertEqual((info.width, info.height), (640, 360))
        self.assertLessEqual(abs(info.duration - 1.2), 0.06)

    def test_range_preview_ignores_missing_assets_and_trims_audio(self) -> None:
        segmented_voice = self.root / "segmented_voice.wav"
        ffmpeg(
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=1.2",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=2.8",
            "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[out]",
            "-map", "[out]", "-c:a", "pcm_s16le", str(segmented_voice),
        )
        self.project.set_input("narration", segmented_voice)
        self.project.clear_asset("opening")
        self.project.state["inputs"].pop("timeline")
        self.project.save()
        storyboard = load_storyboard(self.project.input_path("storyboard"))
        report = validate_project(self.project, storyboard, shot_range=("002", "003"))
        self.assertTrue(report.ok, format_report(report))
        self.assertFalse(any("opening" in issue.message for issue in report.issues))
        self.assertFalse(validate_project(self.project, storyboard, shot_range=("001", "003")).ok)
        self.assertFalse(validate_project(self.project, storyboard, shot_range=("003", "002")).ok)
        output = RenderEngine(self.project).render_range("002", "003")
        info = probe_media(output)
        self.assertTrue(info.has_audio)
        self.assertLessEqual(abs(info.duration - (4.0 - 1.2)), 0.06)
        self.assertEqual((info.width, info.height), (640, 360))
        self.assertEqual(output.name, "preview_002_003.mp4")
        beginning = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", str(output), "-vn", "-t", "0.2",
                "-ac", "1", "-ar", "48000", "-f", "s16le", "-",
            ],
            check=True, capture_output=True,
        ).stdout
        samples = array("h")
        samples.frombytes(beginning)
        self.assertGreater(sum(abs(value) for value in samples) / len(samples), 100)

    def test_ready_prefix_stops_before_first_missing_asset(self) -> None:
        self.project.clear_asset("ending")
        output, message = render_ready_prefix(str(self.root), lambda *_args, **_kwargs: None)
        self.assertIsNotNone(output, message)
        self.assertIn("Range 001 → 002", message)
        self.assertLessEqual(abs(probe_media(Path(output)).duration - 2.7), 0.06)

    def test_partial_storyboard_and_boundary_transition_rules(self) -> None:
        storyboard = load_storyboard(self.project.input_path("storyboard"))
        first, second, third = storyboard.shots
        same_boundary = replace(
            storyboard,
            shots=(
                replace(first, transition_out=Transition("fade", 0.2)),
                replace(second, transition_in=Transition("fade", 0.2)),
                third,
            ),
        )
        self.assertTrue(validate_project(self.project, same_boundary).ok)
        self.assertFalse(any("Conflicting transitions" in issue.message for issue in
                             validate_project(self.project, same_boundary).issues))
        conflicting = replace(
            storyboard,
            shots=(
                replace(first, transition_out=Transition("fade", 0.2)),
                replace(second, transition_in=Transition("cross_dissolve", 0.3)),
                third,
            ),
        )
        report = validate_project(self.project, conflicting)
        self.assertTrue(report.ok, format_report(report))
        self.assertIn("Using transition_out once", format_report(report))
        self.assertEqual(
            RenderEngine._boundary_transition(conflicting.shots[0], conflicting.shots[1]),
            Transition("fade", 0.2),
        )
        partial = replace(storyboard, shots=(first, second))
        report = validate_project(self.project, partial)
        self.assertTrue(report.ok, format_report(report))
        self.assertIn(
            "INFO: Storyboard currently covers narration 00:00.00 → 00:02.70. "
            "Full narration duration: 00:04.00.",
            format_report(report),
        )
        full_scope = replace(partial, scope=StoryboardScope(type="full"))
        self.assertFalse(validate_project(self.project, full_scope).ok)
        output = RenderEngine(self.project).render_range("001", "002")
        self.assertLessEqual(abs(probe_media(output).duration - 2.7), 0.06)

    def test_full_preview_of_partial_storyboard_uses_only_covered_audio(self) -> None:
        storyboard_path = self.project.input_path("storyboard")
        data = json.loads(storyboard_path.read_text(encoding="utf-8"))
        data["shots"] = data["shots"][:2]
        data["scope"] = {"type": "segment", "narration_start": 0.0, "narration_end": 2.7}
        storyboard_path.write_text(json.dumps(data), encoding="utf-8")
        storyboard = load_storyboard(storyboard_path)
        self.assertEqual(storyboard.scope.narration_end, 2.7)
        report = validate_project(self.project, storyboard)
        self.assertTrue(report.ok, format_report(report))
        self.assertIn("Full narration duration: 00:04.00", format_report(report))
        output = RenderEngine(self.project).render(preview=True)
        self.assertLessEqual(abs(probe_media(output).duration - 2.7), 0.06)
        final = RenderEngine(self.project).render(preview=False)
        self.assertLessEqual(abs(probe_media(final).duration - 2.7), 0.06)


if __name__ == "__main__":
    unittest.main()
