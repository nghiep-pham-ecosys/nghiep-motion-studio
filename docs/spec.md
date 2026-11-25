# Spec: long-form explainer video pipeline

Status: in progress. Built so far: the `scenes.json` schema and the ffmpeg
renderer (step 4, issue 6), the voice step with per-line timing (step 3,
issue 7) and generated visuals (step 2, issue 8). Everything else is
planned.

## Goal

Produce 3-5 videos a week, each 10-20 minutes, in English and Vietnamese,
about AI/ML and topics being studied. Production is fully automated: a
synthetic (TTS) voice, many illustrations and animations, no recorded voice
needed. Characters are original (robots first); nothing is copied from other
channels. Each video also yields a blog post and a short post (see "Link to
lab-to-prod"). Nothing is published without Nghiep's review.

## Formats

1. **Two-character dialogue** explaining one technique (e.g. RAG). One
   character asks, the other explains; scenes alternate between a character
   shot and a simple diagram.
2. **Narrated screen recording** of a notebook or code demo, with one voice,
   zooms on the relevant lines, and sentence subtitles.
3. **Animated architecture diagrams**: boxes and arrows built from a data
   description, animated step by step with narration.

## Pipeline

One `scenes.json` drives every language. Narration and on-screen text are
keyed by language code (`en`, `vi`); visuals are shared, so the English and
Vietnamese videos come from the same file and differ only in voice and text.

| # | Step | Input -> output | Free-first tool | Status |
|---|------|-----------------|-----------------|--------|
| 1 | Script | topic + outline -> `scenes.json` (EN + VI narration) | LLM inside the Claude subscription (offline/mock mode in tests) | planned |
| 2 | Visuals | `scenes.json` -> one image or clip per scene | Manim CE (diagrams from data, custom scenes, animated code), VHS (terminal recordings), SVG/code-drawn images | **built** for the generated kinds (`motion-studio visuals`, also run by `render`) |
| 3 | Voice | narration per language -> WAV per scene, written into `audio`, plus per-line timing | EN: Kokoro-82M; VI: VieNeu-TTS v3 Turbo presets or a clone of the author's own voice (all local; licences in `docs/voices.md`) | **built** (`motion-studio narrate`, `just voices`) |
| 4 | Assembly | `scenes.json` + visuals + audio -> 1080p MP4 per language | ffmpeg (title cards, fit and Ken Burns on images, clips, frame-exact audio) | **built** (`motion-studio render`) |
| 5 | Subtitles | MP4 audio + narration -> SRT per language | local speech-to-text alignment | planned |
| 6 | Quality gate | MP4 -> pass/fail | ffprobe checks: duration, loudness, sync, black frames | planned |
| 7 | Extras | MP4 + `scenes.json` -> thumbnail, description, chapters, Shorts | ffmpeg + templates | planned |

### `scenes.json`

The schema lives in `src/motion_studio/schema/scenes.schema.json` (JSON Schema
2020-12; print it with `motion-studio schema`). A working example is
`samples/three-scenes/scenes.json`.

```json
{
  "version": 1,
  "title": {"en": "RAG explained", "vi": "Giải thích RAG"},
  "languages": ["en", "vi"],
  "output": {"width": 1920, "height": 1080, "fps": 30},
  "scenes": [
    {
      "id": "intro",
      "chapter": {"en": "Intro", "vi": "Mở đầu"},
      "narration": {"en": "...", "vi": "..."},
      "duration_s": 4,
      "audio": {"en": "audio/intro.en.wav", "vi": "audio/intro.vi.wav"},
      "visual": {"kind": "image", "src": "visuals/intro.svg", "motion": "zoom-in"}
    }
  ]
}
```

Rules beyond the JSON Schema (checked by `motion-studio validate`):

- every localized field (title, narration, chapter, title-card text) covers
  exactly the languages in `languages`;
- scene ids are unique;
- a scene without `duration_s` has `audio` for every language;
- paths are relative to the folder of `scenes.json`, and the files exist.

Visual kinds: `title` (text on a solid background), `image` (PNG, JPEG or
SVG, fitted to the frame over the scene background, so transparent areas show
the background; `motion` is `none`, `zoom-in`, `zoom-out`, `pan-left` or
`pan-right`) and `video` (a clip such as a screen recording; its own audio is
dropped; `start_s` must be inside the clip; a short clip holds its last frame
unless `loop` is true, and a loop repeats from `start_s`), plus the generated
kinds below.

### Generated visuals

A generated visual is built into an mp4 clip at the output size and frame
rate, then placed like a `video` visual. A working example of each kind is in
`samples/visuals/scenes.json` (`just sample-visuals`).

| Kind | Tool | The scene gives | Timing | Example |
|------|------|-----------------|--------|---------|
| `diagram` | Manim CE (built-in template) | `nodes` on a grid (`id`, `label`, `col` 0-7, `row` 0-5, optional `color`), `edges` (`from`, `to`, optional `label`), optional `steps` (node ids revealed together) and `title` | fits the scene: nodes appear step by step, each edge with the step that shows both ends, then a pulse runs along the edges | `{"kind": "diagram", "nodes": [...], "edges": [...]}` |
| `code` | Manim CE `Code` mobject (built-in template; Pygments colours) | `src` (a code file, at most 40 lines), optional `language`, `style`, `title`, `highlight` (`[first, last]` line ranges) | fits the scene: lines are written one by one, then each range is framed while the rest dims | `{"kind": "code", "src": "code/rag.py", "highlight": [[1, 5]]}` |
| `manim` | Manim CE | `src` (a `.py` file) and `scene` (the Scene class) | its own length; the scene is stretched so the clip is never cut. The script may read `MOTION_STUDIO_TARGET_S` and `MOTION_STUDIO_LANG` | `{"kind": "manim", "src": "manim/descent.py", "scene": "Descent"}` |
| `terminal` | VHS (with ttyd and Chrome or Chromium) | `src` (a `.tape` file without `Output`, `Set Width`, `Set Height` or `Set Framerate`: the renderer sets them) | its own length; the scene is stretched so the recording is never cut | `{"kind": "terminal", "src": "tapes/demo.tape"}` |

All kinds take an optional `background`; `manim` and `terminal` also take
`loop`. `title` and every `label` are one string for all languages or text per
language; a localized diagram is built once per language.

Rules beyond the JSON Schema: diagram node ids are unique, two nodes never
share a grid cell, edges join two different existing nodes, and `steps`
(when given) holds every node exactly once; highlight ranges run forward, end
inside the code file and frame at least one non-blank line; a tape does not
set `Output`, `Set Width`, `Set Height` or `Set Framerate`. Leading blank
lines of a code file are not shown, but line numbers still match the file.

Clips are cached in `<scenes dir>/.cache/visuals/` (gitignored; change it with
`--visuals-dir`). The cache key covers the adapter and tool versions, the
visual with its text in one language, the output size, frame rate,
background, the target length (for the kinds that fit the scene, and for
`manim`, whose script can read it), the language (for `manim`) and the bytes
of the source file and of the built-in template. Files a source loads by
itself (a module a Manim script imports) are not tracked: use
`--rebuild-visuals` after changing one.

`manim` scripts and `terminal` tapes run real code (Python, shell commands)
from the repository, so they come from trusted files only. `diagram` and
`code` take data only.

Tools: `just visuals-setup` installs Manim CE into
`~/.cache/nghiep-motion-studio/visuals-venv` (Linux builds it from source, so
`libcairo2-dev`, `libpango1.0-dev` and `pkg-config` come first); `just
vhs-install` fetches VHS and ttyd release binaries from GitHub into
`~/.cache/nghiep-motion-studio/bin` and checks their sha256. Both paths change
with `MOTION_STUDIO_VISUALS_VENV` and `MOTION_STUDIO_TOOLS_BIN`, and `just`
puts them first on PATH. `motion-studio tools` (or `just tools`) reports which
kinds can run. CI installs both and renders one scene of each kind at
640x360; locally a test whose tool is missing is skipped with the reason.

Schema v1 holds one narration per scene. Dialogue (format 1) needs a speaker
per scene and a voice per character and language; those fields arrive with
the voice step.

Timing: narration is decoded once and measured from its samples. A scene
lasts `max(duration_s, audio length + 0.5 s)`, rounded up to a whole frame,
so narration is never cut. Pictures are composed in RGB and encoded as BT.709
limited-range H.264; every segment's frame count is checked after encoding. Each scene is encoded to a
video-only segment and a WAV with a matching sample count; the segments are
joined without re-encoding the video and the audio is encoded once, so audio
and video stay in sync over any number of scenes. `render` also writes a
`<name>.<lang>.timeline.json` with each scene's start and length, for the
subtitle and Shorts steps.

Character consistency (when characters are drawn): a fixed character sheet is
reused for every scene and stored next to `scenes.json`.

## Quality checks per step

1. Script: `motion-studio validate` passes; every scene has narration in every
   declared language; length matches
   the target runtime (about 150 words per minute); factual claims reviewed by
   Nghiep.
2. Images: one per scene; resolution 1920x1080; spot-check character
   consistency on a contact sheet.
3. Voice: no clipped audio; per-scene duration recorded; loudness normalised
   (target -14 LUFS for the final mix).
4. Assembly: 1920x1080, H.264 yuv420p, AAC 48 kHz stereo; audio/subtitle drift
   under 200 ms; no black frames; chapters match scene boundaries; output
   plays in a standard player. The render tests check resolution, codecs,
   frame count and audio/video end alignment.
5. Extras: thumbnail readable at small size; description lists chapters.

## Link to lab-to-prod

Each video also yields a blog post and a short post, generated from the same
`scenes.json`. Nghiep reviews both, and the video, before anything goes to
YouTube. The pipeline never publishes by itself.

## Cost and constraints

- Free tools first. If a paid API or image service is needed, state the
  estimated monthly cost and stop for Nghiep's yes.
- No publishing from automation.
- No material from any employer or client.
- Rendered output stays out of git (add to `.gitignore`); at most a small
  preview may be committed.

## First trial: 2-3 minute RAG dialogue

Plan (tracked in issue 2):

1. Hand-write or generate a `scenes.json` of about 400 words: two characters
   discuss what RAG is, retrieval, then generation.
2. Voices: Kokoro running locally, two voices.
3. Images: 6-10 original scenes drawn with SVG/code: two robot characters
   and simple diagrams.
4. Assembly with ffmpeg first (fewer dependencies), subtitles per sentence.
5. One command renders the MP4 locally.

Success: Nghiep can watch the clip and judge voice, visuals and pacing before
any long video is made.

## Tool choices and reasons

- **Kokoro (local)**: free, runs offline, Apache-2.0 licence, so the voice is
  safe to publish later. edge-tts is not used: it needs network access and is
  an unofficial client of an online service.
- **SVG/code scenes first**: free, consistent from scene to scene, and
  renderable in CI. A local image model may come later if the machine has a
  GPU; a paid image service only after a cost estimate and Nghiep's yes.
- **ffmpeg for assembly**: enough for title cards, Ken Burns, clips and
  subtitles, and it runs in CI. Animated diagrams and code come from Manim as
  clips that ffmpeg places like any other visual, so no second assembly tool
  is needed.
- **Manim for animated code, not Motion Canvas**: Motion Canvas renders
  through its browser editor and needs a Node project per video, which is too
  heavy for an unattended pipeline and for CI. Manim's `Code` mobject gives
  syntax colours, line-by-line writing and highlights from one Python tool
  that the diagrams already use.
- **Diagrams from data**: the script step (an LLM) writes nodes and edges as
  JSON, not Python, so a generated video never runs generated code.
- **VHS for terminal recordings**: a `.tape` file is a readable, reviewable
  script of keystrokes, and the recording is reproducible.
- **One `scenes.json` for every language**: one file drives the EN and VI
  videos, the blog post and the short post, and is easy to review and diff.

## Decisions (Nghiep, 2026-11-13)

- Character style: **robots first** (two original robot characters).
- Scene images: **SVG/code drawn**, no image model for the trial.
- Voice: **Kokoro, local**; edge-tts is not used.

## Decisions (Nghiep, 2026-11-25)

- Target: 3-5 videos a week, 10-20 minutes each, English and Vietnamese,
  fully automated with a TTS voice and more illustrations.
- One `scenes.json` per video, shared by EN and VI.
- Voice: Kokoro for English; a local Vietnamese TTS model for Vietnamese.
- Visuals: Manim, Motion Canvas and VHS clips plus SVG/code-drawn images.
  (Built in issue 8 with Manim for animated code instead of Motion Canvas;
  see "Tool choices and reasons".)
- Assembly: ffmpeg (Remotion is dropped).

## Decisions (2026-11-25, voice step)

- Vietnamese TTS: VieNeu-TTS v3 Turbo, the only local Vietnamese model found
  whose licence and model card allow a monetised channel. F5-TTS, viXTTS, MMS
  and the other options checked are non-commercial (see `docs/voices.md`).
- The cloned voice is made from a local reference clip of the author's own
  voice; the clip stays outside the repo. The final VI voice is chosen by ear
  on the comparison page.
- Every video description discloses the AI-generated narration.
