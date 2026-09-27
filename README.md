# Auto Documentary Video Editor (V1)

A local, storyboard-driven assembly engine for restrained YouTube documentary videos. It does not make creative decisions: `storyboard.json` specifies them, and direct FFmpeg commands execute them. Narration is always the master timeline.

## Requirements

- Python 3.11 or newer
- FFmpeg and ffprobe on `PATH`
- Gradio and Pillow (Pillow rasterizes text overlays portably when FFmpeg lacks `drawtext`)

Check the media tools:

```bash
ffmpeg -version
ffprobe -version
```

On macOS, FFmpeg can be installed with `brew install ffmpeg`. On Debian/Ubuntu, use `sudo apt install ffmpeg`.

## Install and run

From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 app.py
```

The app opens in a local browser. It does not upload project media.

## Project workflow

1. Enter a project folder and select **New Project** (or open a folder containing `project_state.json`).
2. Import the finished narration, `timeline.json`, and `storyboard.json`.
3. Put visual files in `assets/` and select **Auto Match Assets**, or upload/replace an asset directly in its shot row.
4. Each row immediately shows its thumbnail/video badge and `READY`, `MISSING`, or `ERROR` status. Use **Clear** to remove an assignment without deleting its media file.
5. Select **Preview** in any ready row to inspect that shot.
6. Use **Build Preview** for a fast, lower-resolution timeline review.
7. Use **Render Final** for the project resolution and final encode.

The created layout is:

```text
project/
  final_voice.wav
  timeline.json
  storyboard.json
  project_state.json
  assets/
  audio/music/
  audio/sfx/
  cache/shots/
  logs/latest_render.log
  output/preview.mp4
  output/final.mp4
```

Assignments are stored as relative paths in `project_state.json` when the file is inside the project. Selecting an asset never modifies `storyboard.json`.

The shot list is scrollable and paginated (25–200 rows per page), with filters for missing, ready, and error rows. If several shots reuse one `asset_slot`, assigning, replacing, or clearing it in any row updates every corresponding row. Replacing or clearing a slot invalidates only cached renders for shots that reference that slot.

## `timeline.json`

The renderer reads its existing top-level positive `duration`; it never derives narration timing from text. The duration must agree with the probed narration duration within one frame (or 50 ms, whichever is larger).

## Storyboard schema

See [`examples/storyboard.example.json`](examples/storyboard.example.json) for a complete example.

Top-level fields:

- `version`: must be `1`.
- `project`: `title`, even `width`/`height`, and integer `fps` (1–120).
- `shots`: ordered, contiguous shot objects covering the narration timeline.
- `music` (optional): `{"file": "audio/music/ambient.mp3", "volume_db": -24}`.
- `sfx` (reserved): accepted structurally for forward compatibility but not mixed in V1.

Required shot fields are `id`, `start`, `end`, `asset_slot`, and `asset_type` (`image` or `video`). Optional fields:

- `motion`: one of `static`, `slow_push_in`, `slow_pull_out`, `pan_left`, `pan_right`, `pan_up`, `pan_down`. Push/pull may override `start_scale` and `end_scale`.
- `source_start`: source trim point for video, default `0`.
- `short_video_behavior`: `freeze_last_frame` (default) or `loop`. Video is never silently sped up.
- `transition_in` / `transition_out`: `{type, duration}`.
- `overlay`: a text object with `text`, `position`, `style`, `start_offset`, and `end_offset`.

Text styles are `hero`, `subtitle`, and `label`. Positions are `center`, `top`, `bottom`, `top_left`, and `bottom_left`. Hero text uses restrained fades; text is rendered in the editor, not baked into generated images.

All visual video audio is muted. The `source_audio` field is recognized but intentionally ignored with a validation warning in V1.

## Asset matching

Auto-match compares `asset_slot` with the filename stem, case-insensitively. Supported images are PNG, JPG/JPEG, and WEBP. Supported videos are MP4, MOV, and WEBM. If exactly one supported file matches, it is assigned. If several match (for example `hex_data.png` and `hex_data.mp4`), no choice is made and the ambiguity is shown.

All visuals use cover scaling and center crop; nothing is stretched. Video sources are probed with ffprobe for dimensions, duration, rate, and audio streams.

## Transitions and timing

- `cut`: immediate boundary; default.
- `fade`: an FFmpeg fade blend from the held outgoing frame into the incoming shot.
- `cross_dissolve`: a dissolve from the held outgoing frame into the incoming shot.
- `dip_to_black`: FFmpeg's fade-through-black blend.

A non-cut transition occupies the first `duration` seconds of the incoming shot. The preceding composed frame is extended only as the outgoing transition handle; the total timeline is not shortened. If `transition_in` is a cut, the preceding shot's `transition_out` controls the boundary.

Visual frame boundaries are calculated from absolute timestamps: `round(start × fps)` and `round(end × fps)`. Adjacent frame spans therefore telescope, avoiding cumulative duration rounding. The narration is never trimmed internally, stretched, normalized, or retimed. The muxed output is constrained to the declared narration duration, and ffprobe verifies the result.

## Preview and final output

Preview uses a maximum width of 960 pixels, preserves aspect ratio and project FPS, and encodes H.264 with `veryfast`/CRF 25. Final uses the project dimensions (default 1920×1080), project FPS (default 30), H.264 `medium`/CRF 18, and AAC at 192 kb/s. Both are YouTube-compatible `yuv420p` MP4 files.

Optional music loops to narration duration, defaults to -24 dB, and receives short fades. It is mixed below the unchanged narration. Automatic music or SFX selection is not included.

## Cache

Each normalized shot is cached independently. Its SHA-256 cache key includes:

- resolved asset path, size, and nanosecond modification time;
- all shot fields, including motion and overlay;
- output dimensions, FPS, profile, and absolute frame bounds;
- renderer cache version.

Changing one asset or shot creates only that shot's new cache file. Timeline assembly and audio mux are rebuilt, while unrelated cached shots are reused. Old cache variants are retained so changes can be reversed cheaply; they may be deleted manually while the app is closed.

## Tests

The integration test generates placeholder images, a short video (including source audio), and narration entirely with FFmpeg. It verifies validation, image/video rendering, motion, text, transitions, freeze-last-frame, narration mux, output dimensions, duration tolerance, cache reuse, selective invalidation, and ambiguous matching.

```bash
python3 -m unittest discover -s tests -v
```

## Troubleshooting

- **FFmpeg/ffprobe missing:** install FFmpeg and restart the terminal so both commands are on `PATH`.
- **Invalid JSON:** validate JSON syntax and required storyboard fields; the UI reports the field or shot.
- **Missing asset:** assign the reported `asset_slot`, or add one uniquely named supported file to `assets/` and auto-match again.
- **Project folder outside the app directory:** the UI builds small thumbnails in memory and places video previews in a temporary directory that Gradio can serve. Your original media stays in the project folder.
- **Timeline mismatch:** ensure the top-level timeline duration came from the same finished narration file.
- **Render failure:** read `logs/latest_render.log`; the UI shows a concise error plus the last relevant log lines.
- **Corrupt/unsupported media:** transcode it to a supported still format or H.264 MP4 and reassign it.
- **Disk write failure:** verify free space and write permission on the project folder.

## V1 boundaries

There are no LLM calls, autonomous editing, asset generation, captioning, tracking, cloud services, or upload features. SFX entries are reserved but not rendered. Focal-point crop controls and intentional source-video audio are deferred.
