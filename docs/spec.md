# Spec: long-form explainer video pipeline

Status: in progress. Built so far: the `scenes.json` schema and the ffmpeg
renderer (step 4, issue 6). Everything else is planned.

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
| 2 | Visuals | `scenes.json` -> one image or clip per scene | Manim (maths, diagrams), Motion Canvas (titles, animated code), VHS (terminal recordings), SVG/code-drawn images | planned |
| 3 | Voice | narration per language -> WAV per scene, written into `audio` | EN: Kokoro-82M (local); VI: a local Vietnamese TTS model (candidate chosen after a listening test; licence *(verify)*) | planned |
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
`pan-right`) and `video` (a clip from Manim, Motion Canvas, VHS or a screen
recording; its own audio is dropped; `start_s` must be inside the clip; a
short clip holds its last frame unless `loop` is true, and a loop repeats from
`start_s`).

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
  subtitles, and it runs in CI. Animated diagrams and code come from Manim and
  Motion Canvas as clips that ffmpeg places like any other visual, so no
  second assembly tool is needed.
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
- Assembly: ffmpeg (Remotion is dropped).
