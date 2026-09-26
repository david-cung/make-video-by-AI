from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from auto_video_editor.core.media import probe_media
from auto_video_editor.core.models import (
    ProjectError,
    SUPPORTED_IMAGE_EXTENSIONS,
    SUPPORTED_VIDEO_EXTENSIONS,
)
from auto_video_editor.core.project import EditorProject
from auto_video_editor.core.renderer import RenderEngine
from auto_video_editor.core.storyboard import load_storyboard, load_timeline_duration
from auto_video_editor.core.validator import format_report, validate_project


@dataclass(frozen=True)
class ShotRowView:
    shot_id: str
    start: float
    end: float
    asset_slot: str
    asset_type: str
    motion: str
    asset_path: Path | None
    status: str
    reason: str = ""


# Probing can be expensive for 100+ rows. A result stays valid until the file's
# path, size, or nanosecond modification time changes.
_asset_health_cache: dict[tuple[str, int, int, str], tuple[str, str]] = {}
_row_errors: dict[tuple[str, str], str] = {}

APP_CSS = """
#shot-list { max-height: 68vh; overflow-y: auto; border: 1px solid var(--border-color-primary); border-radius: 10px; padding: 4px; }
.shot-card { padding: 7px 8px; border-bottom: 1px solid var(--border-color-primary); align-items: center; gap: 8px; }
.shot-card:last-child { border-bottom: none; }
.shot-meta p, .shot-file p, .shot-status p { margin: 0; line-height: 1.35; }
.shot-status-ready { color: #2f9e44; font-weight: 700; }
.shot-status-missing { color: #d97706; font-weight: 700; }
.shot-status-error { color: #dc2626; font-weight: 700; }
.shot-thumb img { object-fit: cover !important; max-height: 82px !important; }
.shot-actions { gap: 5px; }
"""


def _file_path(value) -> Path | None:
    if value is None:
        return None
    if isinstance(value, str):
        return Path(value)
    return Path(value.name)


def _load(path: str) -> EditorProject:
    if not path.strip():
        raise ProjectError("Enter a project folder first.")
    return EditorProject.open(Path(path))


def _expected_extensions(asset_type: str) -> set[str]:
    if asset_type == "image":
        return SUPPORTED_IMAGE_EXTENSIONS
    if asset_type == "video":
        return SUPPORTED_VIDEO_EXTENSIONS
    return set()


def _asset_health(path: Path, asset_type: str) -> tuple[str, str]:
    if not path.is_file():
        return "ERROR", "Assigned file is missing"
    expected = _expected_extensions(asset_type)
    if path.suffix.lower() not in expected:
        return "ERROR", f"Wrong asset type: expected {asset_type}"
    try:
        stat = path.stat()
        key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, asset_type)
    except OSError as exc:
        return "ERROR", f"Cannot read asset: {exc}"
    if key in _asset_health_cache:
        return _asset_health_cache[key]
    try:
        info = probe_media(path)
        if info.width is None or info.height is None:
            result = ("ERROR", "No visual stream found")
        elif asset_type == "video" and info.duration <= 0:
            result = ("ERROR", "Video duration is invalid")
        else:
            result = ("READY", "")
    except ProjectError:
        result = ("ERROR", "Corrupt or unreadable media")
    _asset_health_cache[key] = result
    return result


def _shot_rows(project: EditorProject) -> list[ShotRowView]:
    storyboard_path = project.input_path("storyboard")
    if not storyboard_path or not storyboard_path.is_file():
        return []
    storyboard = load_storyboard(storyboard_path)
    rows: list[ShotRowView] = []
    slot_health: dict[tuple[str, str], tuple[Path | None, str, str]] = {}
    for shot in storyboard.shots:
        health_key = (shot.asset_slot, shot.asset_type)
        if health_key not in slot_health:
            asset = project.asset_path(shot.asset_slot)
            if asset is None:
                transient_error = _row_errors.get((str(project.root), shot.asset_slot))
                if transient_error:
                    slot_health[health_key] = (None, "ERROR", transient_error)
                else:
                    slot_health[health_key] = (None, "MISSING", "No asset assigned")
            else:
                status, reason = _asset_health(asset, shot.asset_type)
                slot_health[health_key] = (asset, status, reason)
        asset, status, reason = slot_health[health_key]
        rows.append(
            ShotRowView(
                shot_id=shot.id,
                start=shot.start,
                end=shot.end,
                asset_slot=shot.asset_slot,
                asset_type=shot.asset_type,
                motion=shot.motion.type,
                asset_path=asset,
                status=status,
                reason=reason,
            )
        )
    return rows


def _summary(rows: list[ShotRowView]) -> str:
    ready = sum(row.status == "READY" for row in rows)
    missing = sum(row.status == "MISSING" for row in rows)
    errors = sum(row.status == "ERROR" for row in rows)
    return f"**Shots: {len(rows)}** · **Ready: {ready}** · **Missing: {missing}** · **Errors: {errors}**"


def _project_status(project: EditorProject) -> str:
    storyboard_path = project.input_path("storyboard")
    if not storyboard_path or not storyboard_path.is_file():
        return f"Project: `{project.root}`\n\nImport narration, timeline, and storyboard files."
    rows = _shot_rows(project)
    narration = project.input_path("narration")
    timeline = project.input_path("timeline")
    duration = None
    if timeline and timeline.is_file():
        try:
            duration = load_timeline_duration(timeline)
        except ProjectError:
            pass
    duration_text = f"Duration: **{duration:.2f}s** · " if duration is not None else ""
    return (
        f"Project: `{project.root}`\n\n{duration_text}{_summary(rows)}\n\n"
        f"Narration: {'✓' if narration and narration.is_file() else 'MISSING'} · "
        f"Timeline: {'✓' if timeline and timeline.is_file() else 'MISSING'} · Storyboard: ✓"
    )


def _next_token(token: float | int | None) -> int:
    return int(token or 0) + 1


def _base_updates(project: EditorProject, token: float | int | None = 0):
    return str(project.root), _project_status(project), _next_token(token)


def create_project(path: str, token: float | int | None = 0):
    try:
        if not path.strip():
            raise ProjectError("Enter a folder for the new project.")
        return _base_updates(EditorProject.create(Path(path)), token)
    except Exception as exc:
        return path, f"Error: {exc}", _next_token(token)


def open_project(path: str, token: float | int | None = 0):
    try:
        return _base_updates(_load(path), token)
    except Exception as exc:
        return path, f"Error: {exc}", _next_token(token)


def import_inputs(path: str, narration, timeline, storyboard, token: float | int | None = 0):
    try:
        project = _load(path)
        values = {"narration": narration, "timeline": timeline, "storyboard": storyboard}
        imported = []
        for name, value in values.items():
            source = _file_path(value)
            if source:
                project.set_input(name, source)
                imported.append(name)
        if not imported:
            raise ProjectError("Choose at least one input file to import.")
        return _base_updates(project, token)
    except Exception as exc:
        return path, f"Error: {exc}", _next_token(token)


def _shot_ids_for_slot(project: EditorProject, slot: str) -> list[str]:
    storyboard_path = project.input_path("storyboard")
    if not storyboard_path:
        raise ProjectError("Import a storyboard first.")
    storyboard = load_storyboard(storyboard_path)
    return [shot.id for shot in storyboard.shots if shot.asset_slot == slot]


def _validate_row_upload(project: EditorProject, slot: str, source: Path) -> list[str]:
    storyboard_path = project.input_path("storyboard")
    if not storyboard_path:
        raise ProjectError("Import a storyboard first.")
    matching = [shot for shot in load_storyboard(storyboard_path).shots if shot.asset_slot == slot]
    if not matching:
        raise ProjectError(f'Asset slot "{slot}" is not present in the storyboard.')
    declared_types = {shot.asset_type for shot in matching}
    unsupported = declared_types - {"image", "video"}
    if unsupported:
        raise ProjectError(f"Unsupported storyboard asset type: {', '.join(sorted(unsupported))}")
    if len(declared_types) > 1:
        raise ProjectError(f'Asset slot "{slot}" is used with conflicting asset types.')
    asset_type = next(iter(declared_types))
    if source.suffix.lower() not in _expected_extensions(asset_type):
        raise ProjectError(f'Wrong asset type for "{slot}": expected {asset_type}, got {source.suffix or "unknown"}.')
    status, reason = _asset_health(source, asset_type)
    if status != "READY":
        raise ProjectError(reason)
    return [shot.id for shot in matching]


def assign_row_asset(path: str, slot: str, asset, token: float | int | None = 0):
    try:
        project = _load(path)
        source = _file_path(asset)
        if source is None:
            raise ProjectError("Choose or drop an image/video file.")
        shot_ids = _validate_row_upload(project, slot, source)
        assigned = project.assign_asset(slot, source)
        invalidated = project.invalidate_shot_cache(shot_ids)
        _row_errors.pop((str(project.root), slot), None)
        return (
            _next_token(token),
            f'Assigned **{assigned.name}** to `{slot}`. Updated {len(shot_ids)} shot(s); '
            f"invalidated {invalidated} cached render(s).",
        )
    except Exception as exc:
        try:
            project = _load(path)
            if project.asset_path(slot) is None:
                _row_errors[(str(project.root), slot)] = str(exc)
        except Exception:
            pass
        return _next_token(token), f"Assignment error: {exc}"


def clear_row_asset(path: str, slot: str, token: float | int | None = 0):
    try:
        project = _load(path)
        shot_ids = _shot_ids_for_slot(project, slot)
        existed = project.clear_asset(slot)
        invalidated = project.invalidate_shot_cache(shot_ids)
        _row_errors.pop((str(project.root), slot), None)
        if not existed:
            raise ProjectError(f'No assignment exists for "{slot}".')
        return (
            _next_token(token),
            f'Cleared `{slot}` from {len(shot_ids)} shot(s); invalidated {invalidated} cached render(s). '
            "The media file was preserved.",
        )
    except Exception as exc:
        return _next_token(token), f"Clear error: {exc}"


def auto_match(path: str, token: float | int | None = 0):
    try:
        project = _load(path)
        storyboard_path = project.input_path("storyboard")
        if not storyboard_path:
            raise ProjectError("Import a storyboard first.")
        storyboard = load_storyboard(storyboard_path)
        slots = list(dict.fromkeys(shot.asset_slot for shot in storyboard.shots))
        assigned, ambiguous = project.auto_match(slots)
        invalidated = 0
        for assigned_slot in assigned:
            _row_errors.pop((str(project.root), assigned_slot), None)
            invalidated += project.invalidate_shot_cache(
                [shot.id for shot in storyboard.shots if shot.asset_slot == assigned_slot]
            )
        result = [f"Auto-matched {len(assigned)} slot(s); invalidated {invalidated} cached render(s)."]
        for slot, choices in ambiguous.items():
            result.append(f"{slot}: multiple matches ({', '.join(choices)}); assign one in its row.")
        return str(project.root), _project_status(project), _next_token(token), "\n".join(result)
    except Exception as exc:
        return path, f"Error: {exc}", _next_token(token), f"Auto-match error: {exc}"


def validate(path: str):
    try:
        project = _load(path)
        storyboard_path = project.input_path("storyboard")
        if not storyboard_path:
            raise ProjectError("Import a storyboard first.")
        report = validate_project(project, load_storyboard(storyboard_path))
        return format_report(report)
    except Exception as exc:
        return f"Validation error: {exc}"


def render(path: str, preview: bool, progress):
    try:
        project = _load(path)
        messages: list[str] = []

        def update(message: str) -> None:
            messages.append(message)
            progress(None, desc=message)

        target = RenderEngine(project, progress=update).render(preview=preview)
        return str(target), "\n".join(messages)
    except Exception as exc:
        return None, f"Render error:\n{exc}"


def preview_shot(path: str, shot_id: str):
    try:
        target = RenderEngine(_load(path)).preview_shot(shot_id)
        return str(target), f"Shot preview ready: {target.name}"
    except Exception as exc:
        return None, f"Preview error: {exc}"


def _filter_rows(rows: list[ShotRowView], mode: str) -> list[ShotRowView]:
    return rows if mode == "All" else [row for row in rows if row.status == mode.upper()]


def build_app():
    try:
        import gradio as gr
    except ImportError as exc:
        raise SystemExit("Gradio is not installed. Run: python3 -m pip install -r requirements.txt") from exc

    with gr.Blocks(title="Auto Documentary Video Editor") as app:
        gr.Markdown("# AUTO DOCUMENTARY VIDEO EDITOR\nReliable, storyboard-driven documentary assembly. Narration is the master timeline.")
        refresh_token = gr.Number(value=0, visible=False)
        project_path = gr.Textbox(label="Project folder", placeholder="/path/to/my-documentary")
        with gr.Row():
            new_button = gr.Button("New Project", variant="primary")
            open_button = gr.Button("Open Project")
        status = gr.Markdown("No project open.")

        gr.Markdown("## Project inputs")
        with gr.Row():
            narration = gr.File(label="Narration WAV/audio", file_types=["audio"])
            timeline = gr.File(label="timeline.json", file_types=[".json"])
            storyboard = gr.File(label="storyboard.json", file_types=[".json"])
        import_button = gr.Button("Import Selected Inputs")

        gr.Markdown("## Shot list")
        with gr.Row():
            auto_button = gr.Button("Auto Match Assets", variant="primary", size="sm")
            row_filter = gr.Dropdown(
                choices=["All", "Missing", "Ready", "Error"], value="All", label="Show", scale=1
            )
            page = gr.Number(value=1, minimum=1, precision=0, label="Page", scale=1)
            page_size = gr.Dropdown(choices=[25, 50, 100, 200], value=50, label="Rows per page", scale=1)
        row_message = gr.Markdown()
        validation = gr.Textbox(label="Status / validation", lines=7)
        video = gr.Video(label="Shot preview / rendered output")

        with gr.Column(elem_id="shot-list"):
            @gr.render(inputs=[project_path, refresh_token, row_filter, page, page_size])
            def render_shot_list(path: str, _token, mode: str, page_value: float, size_value: int):
                if not path.strip():
                    gr.Markdown("Open a project to see its shots.")
                    return
                try:
                    project = _load(path)
                    all_rows = _shot_rows(project)
                except Exception as exc:
                    gr.Markdown(f"**Could not load shot list:** {exc}")
                    return
                if not all_rows:
                    gr.Markdown("No storyboard loaded.")
                    return
                gr.Markdown(_summary(all_rows), elem_classes="shot-summary")
                filtered = _filter_rows(all_rows, mode or "All")
                size = int(size_value or 50)
                page_number = max(1, int(page_value or 1))
                page_count = max(1, (len(filtered) + size - 1) // size)
                page_number = min(page_number, page_count)
                start = (page_number - 1) * size
                visible_rows = filtered[start:start + size]
                gr.Markdown(
                    f"Showing {start + 1 if visible_rows else 0}–{start + len(visible_rows)} of "
                    f"{len(filtered)} filtered shots · page {page_number}/{page_count}"
                )
                if not visible_rows:
                    gr.Markdown(f"No {mode.lower()} shots.")
                    return
                for row in visible_rows:
                    row_key = f"{row.shot_id}-{row.asset_slot}"
                    with gr.Row(elem_classes="shot-card", key=f"row-{row_key}"):
                        gr.Markdown(
                            f"**{row.shot_id}**  \n{row.start:.2f} → {row.end:.2f}  \n`{row.asset_slot}`",
                            scale=2, min_width=150, elem_classes="shot-meta",
                        )
                        if row.asset_path and row.status != "MISSING" and row.asset_type == "image" and row.asset_path.is_file():
                            gr.Image(
                                value=str(row.asset_path), type="filepath", interactive=False,
                                show_label=False, container=False, height=82, width=145,
                                buttons=[], elem_classes="shot-thumb", key=f"thumb-{row_key}",
                            )
                        elif row.asset_path and row.asset_type == "video":
                            gr.Markdown(
                                f"🎬 **VIDEO**  \n{row.asset_path.name}", scale=2, min_width=145,
                                elem_classes="shot-file",
                            )
                        else:
                            gr.Markdown("_No asset_", scale=2, min_width=145, elem_classes="shot-file")
                        filename = row.asset_path.name if row.asset_path else "—"
                        gr.Markdown(
                            f"**{filename}**  \nMotion: `{row.motion}`", scale=2, min_width=160,
                            elem_classes="shot-file",
                        )
                        status_class = f"shot-status-{row.status.lower()}"
                        reason = f"  \n{row.reason}" if row.reason else ""
                        gr.Markdown(
                            f"**{row.status}**{reason}", scale=1, min_width=120,
                            elem_classes=["shot-status", status_class],
                        )
                        with gr.Column(scale=2, min_width=180, elem_classes="shot-actions"):
                            upload = gr.UploadButton(
                                "Replace" if row.asset_path else "Upload",
                                file_types=["image", "video"], file_count="single", type="filepath",
                                size="sm", variant="primary" if not row.asset_path else "secondary",
                                key=f"upload-{row_key}",
                            )
                            with gr.Row():
                                preview_button = gr.Button(
                                    "Preview", size="sm", interactive=row.status == "READY",
                                    key=f"preview-{row_key}",
                                )
                                clear_button = gr.Button(
                                    "Clear", size="sm", interactive=row.asset_path is not None,
                                    key=f"clear-{row_key}",
                                )

                        def upload_for_row(project_value, upload_value, token_value, slot=row.asset_slot):
                            return assign_row_asset(project_value, slot, upload_value, token_value)

                        def clear_for_row(project_value, token_value, slot=row.asset_slot):
                            return clear_row_asset(project_value, slot, token_value)

                        def preview_for_row(project_value, shot=row.shot_id):
                            return preview_shot(project_value, shot)

                        upload.upload(
                            upload_for_row, [project_path, upload, refresh_token],
                            [refresh_token, row_message], key=f"upload-event-{row_key}",
                        )
                        clear_button.click(
                            clear_for_row, [project_path, refresh_token],
                            [refresh_token, row_message], key=f"clear-event-{row_key}",
                        )
                        preview_button.click(
                            preview_for_row, [project_path], [video, validation],
                            key=f"preview-event-{row_key}",
                        )

        gr.Markdown("## Validate and render")
        validate_button = gr.Button("Validate")
        with gr.Row():
            preview_button = gr.Button("Build Preview", variant="primary")
            final_button = gr.Button("Render Final")

        base_outputs = [project_path, status, refresh_token]
        new_button.click(create_project, [project_path, refresh_token], base_outputs)
        open_button.click(open_project, [project_path, refresh_token], base_outputs)
        import_button.click(
            import_inputs, [project_path, narration, timeline, storyboard, refresh_token], base_outputs
        )
        auto_button.click(
            auto_match, [project_path, refresh_token], [project_path, status, refresh_token, row_message]
        )
        validate_button.click(validate, [project_path], [validation])
        preview_button.click(
            lambda path, progress=gr.Progress(): render(path, True, progress),
            [project_path], [video, validation],
        )
        final_button.click(
            lambda path, progress=gr.Progress(): render(path, False, progress),
            [project_path], [video, validation],
        )
    return app
