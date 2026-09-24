from __future__ import annotations

from pathlib import Path

from auto_video_editor.core.media import probe_media
from auto_video_editor.core.models import ProjectError
from auto_video_editor.core.project import EditorProject
from auto_video_editor.core.renderer import RenderEngine
from auto_video_editor.core.storyboard import load_storyboard, load_timeline_duration
from auto_video_editor.core.validator import format_report, validate_project


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


def _project_view(project: EditorProject):
    storyboard_path = project.input_path("storyboard")
    if not storyboard_path or not storyboard_path.is_file():
        status = f"Project: `{project.root}`\n\nImport narration, timeline, and storyboard files."
        return status, "No storyboard loaded.", [], []
    storyboard = load_storyboard(storyboard_path)
    narration = project.input_path("narration")
    timeline = project.input_path("timeline")
    duration = None
    if timeline and timeline.is_file():
        try:
            duration = load_timeline_duration(timeline)
        except ProjectError:
            pass
    slots = list(dict.fromkeys(shot.asset_slot for shot in storyboard.shots))
    ready = sum(bool(project.asset_path(slot) and project.asset_path(slot).is_file()) for slot in slots)
    status = (
        f"Project: `{project.root}`\n\n"
        f"Duration: **{duration:.2f}s**  ·  Shots: **{len(storyboard.shots)}**  ·  "
        f"Asset slots ready: **{ready}/{len(slots)}**\n\n"
        f"Narration: {'✓' if narration and narration.is_file() else 'MISSING'}  ·  "
        f"Timeline: {'✓' if timeline and timeline.is_file() else 'MISSING'}  ·  Storyboard: ✓"
        if duration is not None else
        f"Project: `{project.root}`\n\nShots: **{len(storyboard.shots)}**  ·  Asset slots ready: **{ready}/{len(slots)}**"
    )
    lines = ["| Shot | Time | Asset slot | File | Motion | Status |", "|---|---:|---|---|---|---|"]
    for shot in storyboard.shots:
        asset = project.asset_path(shot.asset_slot)
        is_ready = bool(asset and asset.is_file())
        lines.append(
            f"| {shot.id} | {shot.start:.2f} → {shot.end:.2f} | `{shot.asset_slot}` | "
            f"{asset.name if is_ready else '—'} | {shot.motion.type} | {'READY' if is_ready else '**MISSING**'} |"
        )
    return status, "\n".join(lines), slots, [shot.id for shot in storyboard.shots]


def _updates(project: EditorProject):
    import gradio as gr

    status, shots, slots, shot_ids = _project_view(project)
    return (
        str(project.root), status, shots,
        gr.Dropdown(choices=slots, value=slots[0] if slots else None),
        gr.Dropdown(choices=shot_ids, value=shot_ids[0] if shot_ids else None),
    )


def create_project(path: str):
    try:
        if not path.strip():
            raise ProjectError("Enter a folder for the new project.")
        return _updates(EditorProject.create(Path(path)))
    except Exception as exc:
        import gradio as gr
        return path, f"Error: {exc}", "", gr.Dropdown(), gr.Dropdown()


def open_project(path: str):
    try:
        return _updates(_load(path))
    except Exception as exc:
        import gradio as gr
        return path, f"Error: {exc}", "", gr.Dropdown(), gr.Dropdown()


def import_inputs(path: str, narration, timeline, storyboard):
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
        return _updates(project)
    except Exception as exc:
        import gradio as gr
        return path, f"Error: {exc}", "", gr.Dropdown(), gr.Dropdown()


def auto_match(path: str):
    try:
        project = _load(path)
        storyboard_path = project.input_path("storyboard")
        if not storyboard_path:
            raise ProjectError("Import a storyboard first.")
        storyboard = load_storyboard(storyboard_path)
        slots = list(dict.fromkeys(shot.asset_slot for shot in storyboard.shots))
        assigned, ambiguous = project.auto_match(slots)
        result = [f"Auto-matched {len(assigned)} slot(s)."]
        for slot, choices in ambiguous.items():
            result.append(f"{slot}: multiple matches ({', '.join(choices)}); assign one manually.")
        updates = _updates(project)
        return updates[0], updates[1] + "\n\n" + "  \n".join(result), updates[2], updates[3], updates[4]
    except Exception as exc:
        import gradio as gr
        return path, f"Error: {exc}", "", gr.Dropdown(), gr.Dropdown()


def assign_asset(path: str, slot: str, asset):
    try:
        project = _load(path)
        source = _file_path(asset)
        if not slot or not source:
            raise ProjectError("Choose an asset slot and an image/video file.")
        project.assign_asset(slot, source)
        return _updates(project)
    except Exception as exc:
        import gradio as gr
        return path, f"Error: {exc}", "", gr.Dropdown(), gr.Dropdown()


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
        if not shot_id:
            raise ProjectError("Choose a shot first.")
        target = RenderEngine(_load(path)).preview_shot(shot_id)
        return str(target), f"Shot preview ready: {target.name}"
    except Exception as exc:
        return None, f"Preview error: {exc}"


def build_app():
    try:
        import gradio as gr
    except ImportError as exc:
        raise SystemExit("Gradio is not installed. Run: python3 -m pip install -r requirements.txt") from exc

    with gr.Blocks(title="Auto Documentary Video Editor") as app:
        gr.Markdown("# AUTO DOCUMENTARY VIDEO EDITOR\nReliable, storyboard-driven documentary assembly. Narration is the master timeline.")
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
        shots = gr.Markdown("No storyboard loaded.")
        auto_button = gr.Button("Auto Match Assets")
        with gr.Row():
            slot = gr.Dropdown(label="Asset slot", choices=[])
            asset = gr.File(label="Assign image/video", file_types=["image", "video"])
            assign_button = gr.Button("Assign Asset")

        gr.Markdown("## Validate and render")
        validation = gr.Textbox(label="Status / validation", lines=8)
        validate_button = gr.Button("Validate")
        with gr.Row():
            shot_id = gr.Dropdown(label="Shot", choices=[])
            shot_preview_button = gr.Button("Preview Shot")
        video = gr.Video(label="Preview / output")
        with gr.Row():
            preview_button = gr.Button("Build Preview", variant="primary")
            final_button = gr.Button("Render Final")

        project_outputs = [project_path, status, shots, slot, shot_id]
        new_button.click(create_project, [project_path], project_outputs)
        open_button.click(open_project, [project_path], project_outputs)
        import_button.click(import_inputs, [project_path, narration, timeline, storyboard], project_outputs)
        auto_button.click(auto_match, [project_path], project_outputs)
        assign_button.click(assign_asset, [project_path, slot, asset], project_outputs)
        validate_button.click(validate, [project_path], [validation])
        shot_preview_button.click(preview_shot, [project_path, shot_id], [video, validation])
        preview_button.click(
            lambda path, progress=gr.Progress(): render(path, True, progress),
            [project_path], [video, validation],
        )
        final_button.click(
            lambda path, progress=gr.Progress(): render(path, False, progress),
            [project_path], [video, validation],
        )
    return app
