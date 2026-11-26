# Spec: long-form explainer video pipeline

Status: in progress. Built so far: the `scenes.json` schema and the ffmpeg
renderer (step 4, issue 6), the voice step with per-line timing (step 3,
issue 7), generated visuals (step 2, issue 8), subtitles (step 5,
issue 9), the quality gate (step 6, issue 10), the work dir that keeps
renders off the local disk (issue 22), Shorts (part of step 7, issue 11) and
the upload to YouTube as private, scheduled drafts (step 8, issue 12), and
one command from a topic file to a gated video with its Shorts (`motion-studio
video`, issue 13). Everything else is planned.

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
| 1 | Script | topic + outline -> `scenes.json` (EN + VI narration) | written in a Claude Code session (inside the subscription); `motion-studio video` reads it from the topic file and calls no LLM | **built** as an input (topic file, see "One command") |
| 2 | Visuals | `scenes.json` -> one image or clip per scene | Manim CE (diagrams from data, custom scenes, animated code), VHS (terminal recordings), SVG/code-drawn images | **built** for the generated kinds (`motion-studio visuals`, also run by `render`) |
| 3 | Voice | narration per language -> WAV per scene, written into `audio`, plus per-line timing | EN: Kokoro-82M or a Chatterbox clone of the author's own voice; VI: VieNeu-TTS v3 Turbo presets or a clone of the author's own voice; a voice per scene or speaker (all local; licences in `docs/voices.md`) | **built** (`motion-studio narrate`, `just voices`) |
| 4 | Assembly | `scenes.json` + visuals + audio -> 1080p MP4 per language | ffmpeg (title cards, fit and Ken Burns on images, clips, frame-exact audio, two-pass `loudnorm` on the final mix) | **built** (`motion-studio render`) |
| 5 | Subtitles | MP4 audio + narration -> SRT per language | narration timing, local speech-to-text alignment | **built** (`render`, `motion-studio subtitles`) |
| 6 | Quality gate | MP4 + sidecars + `factcheck.md` -> pass/fail and `.gate.json` | ffprobe and ffmpeg (`loudnorm` analysis, `blackdetect`), the drift report: duration, loudness, black frames, subtitle drift, fact check | **built** (`motion-studio gate`, `just gate`) |
| 7 | Extras | MP4 + `scenes.json` -> thumbnail, description, chapters, Shorts | ffmpeg + templates | Shorts **built** (`motion-studio shorts`, `just shorts`); description and chapters **built** (`stage-upload`); the thumbnail planned |
| 8 | Upload | gated MP4 + SRT (+ Shorts) -> private, scheduled YouTube videos, `upload.json` | a draft GitHub release + the `youtube-upload` workflow in lab-to-prod (YouTube Data API, free quota) | **built** (`motion-studio stage-upload`, `finish-upload`) |

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

### Theme

One theme sets every colour and font: the frame background (title cards,
image pads, Manim and VHS backgrounds), title text, diagram node colours and
labels, code token colours, the VHS terminal palette and the burned-in
subtitle box. The themes live in `src/motion_studio/theme.py`: `light`
(the default: slate-50 background, slate-900 text) and `dark` (slate-900
background, slate-50 text). A scenes.json picks one with the top-level
`"theme": "light" | "dark"`; `render --theme` and `visuals --theme` override
it. A visual's own `background` (and a title card's `color`) still wins.

Every text colour meets WCAG 2.x AA (at least 4.5:1) against what it is drawn
on, with tinted surfaces composited first: node labels on their 16 % node
fill, code tokens and line numbers on the code window and inside the 12 %
highlight fill, the 16 ANSI terminal colours on the terminal background, and
subtitle text on its translucent box over a black, a white and the theme
frame. Node strokes, arrows and the accent reach at least 3:1 (WCAG 1.4.11).
`tests/test_theme.py` checks every pair. Lines dimmed by a code highlight are
deliberately below that while the highlight lasts.

### Generated visuals

A generated visual is built into an mp4 clip at the output size and frame
rate, then placed like a `video` visual. A working example of each kind is in
`samples/visuals/scenes.json` (`just sample-visuals`).

| Kind | Tool | The scene gives | Timing | Example |
|------|------|-----------------|--------|---------|
| `diagram` | Manim CE (built-in template) | `nodes` on a grid (`id`, `label`, `col` 0-7, `row` 0-5, optional `color`), `edges` (`from`, `to`, optional `label`), optional `steps` (node ids revealed together) and `title` | fits the scene: nodes appear step by step, each edge with the step that shows both ends, then a pulse runs along the edges | `{"kind": "diagram", "nodes": [...], "edges": [...]}` |
| `code` | Manim CE `Code` mobject (built-in template; theme colours, or the Pygments style named by `style`) | `src` (a code file, at most 40 lines), optional `language`, `style`, `title`, `highlight` (`[first, last]` line ranges) | fits the scene: lines are written one by one, then each range is framed while the rest dims | `{"kind": "code", "src": "code/rag.py", "highlight": [[1, 5]]}` |
| `manim` | Manim CE | `src` (a `.py` file) and `scene` (the Scene class) | its own length; the scene is stretched so the clip is never cut. The script may read `MOTION_STUDIO_TARGET_S`, `MOTION_STUDIO_LANG` and `MOTION_STUDIO_THEME` (path of the theme JSON) | `{"kind": "manim", "src": "manim/descent.py", "scene": "Descent"}` |
| `terminal` | VHS (with ttyd and Chrome or Chromium) | `src` (a `.tape` file without `Output`, `Set Width`, `Set Height` or `Set Framerate`: the renderer sets them, plus the theme's colours unless the tape has `Set Theme`) | its own length; the scene is stretched so the recording is never cut | `{"kind": "terminal", "src": "tapes/demo.tape"}` |

All kinds take an optional `background`; `manim` and `terminal` also take
`loop`. `title` and every `label` are one string for all languages or text per
language; a localized diagram is built once per language.

Rules beyond the JSON Schema: diagram node ids are unique, two nodes never
share a grid cell, edges join two different existing nodes, and `steps`
(when given) holds every node exactly once; highlight ranges run forward, end
inside the code file and frame at least one non-blank line; a tape does not
set `Output`, `Set Width`, `Set Height` or `Set Framerate`. Leading blank
lines of a code file are not shown, but line numbers still match the file.

`render` and `visuals` cache clips in `<job folder>/visuals/` (see "Work dir
and disk"; change it with `--visuals-dir`). Called as a library,
`render()` keeps its old default, `<scenes dir>/.cache/visuals/` (gitignored). The cache key covers the adapter and tool versions, the
visual with its text in one language, the output size, frame rate,
background, theme, the target length (for the kinds that fit the scene, and for
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

### Voices per scene (issue 17)

Schema v1 holds one narration per scene, and each scene can have its own
voice, so dialogue (format 1) and a narrator with a second speaker work from
one file:

- `speakers` (top level) names voices: per language, a TTS `engine` and one
  of its `voice` ids;
- a scene's `speaker` picks one of them; a scene's own `voice` (per
  language) wins over its speaker;
- `voice_mix` (top level) covers the scenes that name no speaker:
  `{"policy": "narrator", "narrator": "owner"}` gives them all to one
  speaker; `{"policy": "alternate-chapters", "speakers": ["owner", "guest"]}`
  lets the speakers take turns, starting a new turn at every scene with a
  `chapter`;
- anything left uses `narrate --engine --voice`; without them `narrate`
  names every scene without a voice.

`motion-studio validate` checks that every speaker named by a scene or by
`voice_mix` exists, that the policy has its field (`narrator` or two or more
`speakers`), that voice languages are in `languages` and that every engine
is known. Example: `samples/voice-mix/scenes.json` (the owner's clone
narrates, a preset voice asks the question, one scene picks its own voice).
Clone reference clips are passed at run time, never written into
scenes.json.

Timing: narration is decoded once and measured from its samples. A scene
lasts `max(duration_s, audio length + 0.5 s)`, rounded up to a whole frame,
so narration is never cut. Pictures are composed in RGB and encoded as BT.709
limited-range H.264; every segment's frame count is checked after encoding. Each scene is encoded to a
video-only segment and a WAV with a matching sample count; the segments are
joined without re-encoding the video and the audio is encoded once, so audio
and video stay in sync over any number of scenes. `render` also writes a
`<name>.<lang>.timeline.json` with each scene's start and length, for the
subtitle and Shorts steps.

### Loudness (issue 24)

`render` normalises the final mix of each language, so a narrated video
meets the `loudness` check of the quality gate without a manual step:

1. The scene WAVs are joined into one mix (48 kHz stereo, the planned sample
   count).
2. Pass 1 measures the mix with ffmpeg
   `loudnorm=I=-14:TP=-2:LRA=11:print_format=json`.
3. Pass 2 applies the measured `input_i`, `input_tp`, `input_lra`,
   `input_thresh` and `target_offset` (`measured_I`, `measured_TP`,
   `measured_LRA`, `measured_thresh`, `offset`) with `linear=true`, resamples
   back to 48 kHz and pads or trims to the exact sample count, so audio and
   video still end together. The mix is then encoded once to AAC.

| Target | Value | Why |
|--------|-------|-----|
| integrated | -14 LUFS | the gate's target (see "Quality gate") |
| true peak | -2 dBTP | the gate allows at most -1 dBTP on the finished AAC file; the encode raises peaks, so `loudnorm` aims 1 dB lower |
| loudness range | 11 LU | `linear=true` applies one gain when the mix's range is at most this and the gain keeps the true peak under the target; otherwise `loudnorm` falls back to its dynamic mode (a gentle compressor and true-peak limiter), which still lands on -14 LUFS |

The targets live in one place, `render.py` (`TARGET_LUFS`,
`MAX_TRUE_PEAK_DBTP`); `gate.py` imports them, so the renderer and the gate
cannot drift apart. The gate's tolerance (+/- 1 LU) stays in `gate.py`.

What happened is printed after each render (input and output loudness, true
peak, `linear` or `dynamic`) and stored under `loudness` in
`<name>.<lang>.timeline.json`. A mix with no sound (no narration in this
language, so pass 1 reads `-inf` or at most -70 LUFS, the BS.1770 absolute
gate) is not normalised: the render still succeeds, joins the silence
unchanged, prints a `WARN ... the audio is silent` line and records
`"status": "silent"`; the gate then fails `loudness` ("no sound"), as it
should for a video that will be published. `render --no-loudnorm` keeps the
mix as it is (quick drafts; `"status": "off"`).

Character consistency (when characters are drawn): a fixed character sheet is
reused for every scene and stored next to `scenes.json`.

## One command (issue 13)

`motion-studio video <topic file>` (or `just video <topic file>`) makes one
video with no hand step: it copies the script into a job folder of the work
dir and runs the existing commands in order, stopping at the first step that
fails. Example: `samples/pilot-cstr/` (a dialogue on the CSTR unit of
process-sim-lab, one topic file and one scenes file per language).

| # | Step | What runs | Python |
|---|------|-----------|--------|
| 1 | `script` | read the topic file and the scenes file; write `<job>/script.md` (who says what, by chapter) and print the length at 150 words per minute; check the tools the later steps need | system |
| 2 | `scenes` | copy the scenes file (as `scenes.json`), every file it names and `factcheck.md` into `<job>/`; cast the voices; validate; check the fact check against the narration (when the gate will run) | system |
| 3 | `voice` | `narrate <job>/scenes.json --lang <lang>`, one process per language, so each model is closed before the next | TTS venv |
| 4 | `visuals` | `visuals` (skipped when no scene has a generated visual) | system |
| 5 | `render` | first the planned length from the narration audio: outside the gate's window it stops here, with how many words to add or cut; then `render --metadata comment=<cast>` | system |
| 6 | `subtitles` | `subtitles <video> --check-drift` (faster-whisper) | TTS venv |
| 7 | `gate` | `gate <video> --scenes <job>/scenes.json` | system |
| 8 | `shorts` | `shorts <video> --scenes <job>/scenes.json --count <n>` | system |

The issue listed visuals before voice and subtitles before render. Here the
voice comes first because a generated clip is fitted to its scene, and a
narrated scene lasts as long as its audio: clips built before narration would
all be built again by `render`. The drift check needs the rendered audio, so
it follows `render` (which writes the SRT from the narration timing).

**Topic file** (JSON, `src/motion_studio/schema/topic.schema.json`; paths
relative to the topic file):

```json
{
  "version": 1,
  "job": "pilot-cstr-en",
  "topic": "The CSTR unit of process-sim-lab",
  "scenes": "scenes.en.json",
  "factcheck": "factcheck.en.md",
  "languages": ["en"],
  "cast": {"en": {"preset": "male-female", "guest": "af_heart"}},
  "shorts": 3
}
```

Optional: `assets` (extra files to copy, such as a module a Manim script
imports), `voices` (default: the nearest `voices.json` up from the topic
file), `stand_in` (see below), `gate` (`min_s`, `max_s`: a changed window is
for samples and tests; `stage-upload` refuses that gate report). The script
is the scenes file itself: its narration is written beforehand (in a Claude
Code session, P5: no paid API), and `video` never calls an LLM. Write short
sentences: a line that does not fit one subtitle cue (two rows of 42
characters) is split at a pause the renderer guesses, and in the pilot such
splits put cues up to 1.5 s off, which fails the drift check; the `script` step
warns about every long line. A sentence must not end in a single capital letter
(`... into B.`): the narration splitter reads it as an initial and joins the
next sentence to it. Every file the
scenes file names must lie under its folder, so the job folder holds
everything it needs. The source folder is never written to: narration audio,
clips and renders go to the job.

**Casting.** `voices.json` (repository root, `schema/voices.schema.json`)
holds the narrator per language and two presets:

| Preset | Guest EN | Guest VI | Stand-in narrator EN / VI |
|--------|----------|----------|---------------------------|
| `male-male` | Kokoro `am_michael` | VieNeu `Minh Đức` | Kokoro `bm_george` / VieNeu `Hải Đăng` |
| `male-female` | Kokoro `af_heart` (or `bf_emma`) | VieNeu `Trúc Ly` (or `Thùy Dung`) | Kokoro `am_michael` / VieNeu `Minh Đức` |

The narrator is the owner's clone: English `clone-en` with the voice
`clone-standard` (the original Chatterbox, not Turbo), Vietnamese `clone`.
The `scenes` step writes two speakers into the job's scenes.json:
`narrator` (read by every scene without a speaker, through the `narrator`
policy of `voice_mix`) and `guest` (scenes with `"speaker": "guest"`). A
Vietnamese clone next to a VieNeu guest is cast as `vieneu` with the voice
`clone`, so one model reads both voices. A preset's stand-in never equals one
of its guest voices (checked when the file is read).

**Stand-in narrator.** The clone engines read their reference clip from
`MOTION_STUDIO_REF_WAV_EN` (English) and `MOTION_STUDIO_REF_WAV`
(Vietnamese), named by `ref_env` in `voices.json`; the clip never enters the
repository or the job. When the variable is unset, `video` stops before
anything runs and says which variable to set. With `--stand-in` (or
`"stand_in": true` in the topic file) the preset's stand-in voice reads the
narrator's lines instead; this is printed as a `WARN`, written into the mp4's
`comment` tag (`stand-in narrator: kokoro am_michael reads the narrator's
lines in place of the owner's clone (MOTION_STUDIO_REF_WAV_EN is not set)`)
and into the run report. A variable that names a missing file is an error,
stand-in or not.

**Report and re-runs.** `<job>/<job>.video.json` records the cast, every step
(command, exit code, seconds, summary), the failed step and the outputs
(video, SRT, gate report, Shorts); it is written after each step. On a failure
the run prints the step and how to resume: fix the cause and run the same
command with `--from <step>` (script and scenes always run; narration audio of
unchanged scenes is kept, and narrate and the visuals cache reuse what did not
change). `--until <step>` stops early, `--lang` makes one language,
`--dry-run` prints the cast and the commands. The render step encodes one
scene at a time (`--jobs 1`, the default of `video`): on the 10 GB laptop two
parallel 1080p encodes (about 1.3 GB each, an image with a camera move is
scaled to 2160 lines first) took available memory down to 1.3 GB next to other
work; `--jobs N` allows more. The run holds the job's render
lock, so the size cap never strips the job while it runs.

**Pilot (2026-11-25).** Both pilots ran with `--stand-in`: no reference clip
was set on the machine, so a preset voice read the narrator's lines (the mp4
`comment` tag and the report say so). Nghiep reviews both before anything is
staged.

| Video | Cast | Length | Gate | Shorts | Step times |
|-------|------|--------|------|--------|------------|
| `pilot-cstr-en.en.mp4` | male-female: stand-in Kokoro `am_michael`, guest `af_heart` | 799.3 s | passed: -14.3 LUFS, true peak -1.6 dBTP, drift median 10 ms, p90 50 ms, 22 claims | 3 (59.1, 58.4, 55.3 s) | voice 75 s, visuals 341 s, render 276 s, drift 37 s, gate 33 s, Shorts 97 s |
| `pilot-cstr-vi.vi.mp4` | male-male: stand-in VieNeu `Hải Đăng`, guest `Minh Đức` | 638.3 s | passed: -14.2 LUFS, true peak -1.4 dBTP, drift median 30 ms, p90 120 ms, 25 claims | 3 (54.7, 55.6, 50.6 s) | voice 26 s (27 new lines, 187 cached), visuals 139 s, render 237 s, drift 55 s, gate 29 s, Shorts 126 s |

What the pilot taught: the first English render failed the drift check
(p90 1390 ms) because of long sentences split into several cues and Whisper's
word starts after pauses (see "Subtitles"); short sentences and
`snap_to_speech` fixed both. The first Vietnamese run stopped at the length
check (555.7 s): the Vietnamese pilot runs at about 230 syllables per minute of video, the English one at
about 130 words, so a 10-minute Vietnamese script needs about 2,400 words
(syllables), and the hint now uses the voice's own pace.

## Work dir and disk (issue 22)

The laptop has about 30 GB free on each Windows drive, and the WSL disk lives
on one of them. A 15-minute 1080p video with its intermediates is roughly
0.3-1 GB, so renders go to one work dir with a size cap and are deleted after
upload.

**Layout.** The work dir is `$MOTION_STUDIO_WORK_DIR`, else
`~/.cache/nghiep-motion-studio/work` (outside every checkout; `/`, the home
folder and its parents are refused). Each video is a *job* folder named like
its outputs (the folder of `scenes.json`, else the file's stem):

```
<work dir>/<job>/
  .motion-studio-job          marker and lock (only folders with it are ever cleaned)
  <job>.<lang>.mp4            render, plus .timeline.json, .lines.json, .srt,
                              .burned.mp4, .drift.json, .gate.json
  visuals/                    generated-clip cache
  shorts/                     Shorts (step 7): <job>-<n>.<lang>.mp4 and .srt, <job>.<lang>.shorts.json
  .render-*/  .burn-*/        temporary folders of a running render or burn
  shorts/.shorts-*/           temporary folder of a running Shorts cut
  upload.json                 the upload record (written by the upload step, issue 12)
```

`render` without `-o` writes there; `-o <folder>` still renders anywhere
else (that folder gets the same layout but no cap and no cleanup).
Narration audio (`narrate`) stays next to `scenes.json` under `audio/`,
since scenes.json paths are relative to its folder.

**Size cap.** `$MOTION_STUDIO_WORK_CAP_GB`, default 10 GB (10^9 bytes). Before
and after each render (and `visuals` build), and on `motion-studio clean
--cap` (`just prune`), the oldest jobs (newest file time) are stripped until
the work dir fits. A job is never touched while a render holds it: `render`
holds a shared `flock` on the marker, and cleanup takes an exclusive lock
without waiting and skips a job it cannot lock (so the job being rendered
right now is safe, in this process or another). A job over the cap on its own
gives a `WARN` line.

**What is kept.** Stripping a job (by the cap or after upload) deletes every
file except: `upload.json`, `scenes.json`, `factcheck.md`, `script.md` (or `.txt`, `.json`), every
`.srt` and the marker. Folders left empty are removed. A symbolic link is
removed, never followed, so nothing outside the job is deleted. A stripped job
can be rendered again from its scenes.json.

**After upload.** The upload step (issue 12) writes `<job>/upload.json` and
then runs `motion-studio clean --after-upload <job>/upload.json` (`just
clean-uploaded <record>`):

```json
{
  "version": 1,
  "status": "uploaded",
  "job": "rag",
  "files": [
    {"path": "rag.en.mp4", "remote_id": "<id the platform returned>"},
    {"path": "shorts/rag-1.en.mp4", "remote_id": "<id>"}
  ]
}
```

It refuses, and deletes nothing, unless: the record is a regular file in a
job folder directly inside the work dir; `version` is 1, `status` is
`uploaded` and `job` is that folder's name; every entry has a relative `path`
inside the job and a non-empty `remote_id`; every named file exists (first
clean only); every finished video in the job (top-level `*.mp4`, `shorts/**/
*.mp4`) is named, or is the `.burned.mp4` or clean twin of a named one; and no
render holds the job. Then the job is stripped and a `cleaned` entry (time,
bytes freed, files deleted, cloud copies) is added to the record. Running it
again is harmless.

**Optional cloud copy (off by default).** With `--copy-to REMOTE:PATH` or
`$MOTION_STUDIO_CLOUD_COPY` (for example `gdrive:motion-studio`, a Google
Drive remote set up with `rclone config`, free tier), the master videos
(top-level `<job>.<lang>.mp4`, not the burned copies) are copied first with
`rclone copyto --checksum` to `<remote>/<job>/<name>`; rclone checks size and,
where the remote supports it, the hash. Any copy failure, or a missing
rclone, stops the cleanup before anything is deleted. `$MOTION_STUDIO_RCLONE`
names the rclone binary (tests use a fake). No remote is configured in the
repository.

**`just disk`** (`motion-studio disk`) prints the work dir size against the
cap, each job's size and last use, and the free space on the Windows drive
that holds the WSL disk. The drive comes from `$MOTION_STUDIO_HOST_DRIVE`
(for example `/mnt/d`), else from this distro's `BasePath` in the Windows
registry (`reg.exe query HKCU\...\Lxss /s`, read-only), else `/mnt/c`.

The WSL disk file grows as files are written and does not shrink by itself
when they are deleted: deleted space is reused inside it, so the cap bounds
its growth, but it hands no space back to Windows unless sparse mode is on
(`wsl --manage <distro> --set-sparse true`) or the disk is compacted.

## Shorts (issue 11)

Each long video gives up to three Shorts. `motion-studio shorts
<name>.<lang>.mp4 [--scenes scenes.json]` (or `just shorts <video> --scenes
<scenes.json>`) reads the clean render with its `.timeline.json` and
`.lines.json` (else its `.srt`) and writes into the job folder:

```
<job>/shorts/<job>-<n>.<lang>.mp4     n = 1..3, in video order
<job>/shorts/<job>-<n>.<lang>.srt     the cues of that clip, on its own clock
<job>/shorts/<job>.<lang>.shorts.json windows, marks, chapter, cue count and loudness of each clip
```

`clean --after-upload` treats these mp4s like the main videos (see "Work dir
and disk"), and a new cut first removes the clips of an earlier one.

**Where a clip may start and end.** Candidates are every scene boundary of
the timeline (a *chapter mark* where the scene's `chapter` differs from the
one before it, so chapters come from `scenes.json`; no new field is needed),
the start and end of the video, and every gap between two narration lines
where the first line ends a sentence (`.`, `!`, `?`, `…`). Each candidate is
moved into the silence around it, read from the video's audio in 10 ms energy
frames (as for subtitles): a clip starts 0.25 s before the first word after
the pause and ends 0.6 s after the last word before it, on whole frames. A
candidate with no pause of at least 0.1 s within 0.5 s is dropped, so a clip
never cuts a word. Without `--scenes` (and no `scenes.json` next to the
video) every scene start still counts, but there are no chapter marks or
titles.

**Which windows.** Every pair of marks 45-60 s apart is a window. The chosen
set never overlaps and holds as many windows as fit, up to `--count` (3);
among sets of that size it has the best marks: a window scores 10 x its start
mark (chapter 3, scene 2, sentence 1) + 3 x its end mark (chapter or end of
video 3, scene 2, sentence 1), plus a little for length. When fewer windows
fit (a video under about 2.5 minutes), the ones found are cut, the command
prints `FAIL only N of 3 Shorts windows ...` and exits 1: nothing is padded
or looped. `--dry-run` prints the windows without cutting.

**Picture.** 1080x1920 (9:16) on the theme's background (`--theme`, else the
file's theme, else light). The 16:9 frame is scaled to the full width (1072
px plus a 4 px outline in the theme's border colour), not cropped, so
diagrams and code stay whole; its top is at 29 % of the height. The chapter
title of the window's start (80 px, two rows of at most 22 characters) sits
above it, and the subtitles below it: bold 64 px text, at most two rows of
20 characters, on the theme's subtitle box, side margins of 12 % of the width.
Everything stays out of the top 15 % and the bottom 25 %, where the Shorts
player draws its own title, buttons and progress bar. Cues are rebuilt from
the narration lines whose middle lies in the window, moved to the clip's
clock and trimmed to the speech in the clip's audio, then drawn by libass from
an ASS file at the frame's own resolution.

**Sound.** The window's audio is cut sample-exact (48 kHz stereo) and goes
through the same two-pass `loudnorm` as a render (`render.normalize_mix`), so
every clip lands on -14 LUFS with its true peak under -1 dBTP, whatever the
loudness of that part of the long video. The finished clip is measured again
with the gate's analysis; a clip outside the gate's tolerance (+/- 1 LU) is a
`FAIL` line and exit 1. Video and audio have the same length (whole frames,
checked after encoding).

Checked on 2026-11-25 with a 147.6 s mock-narrated render (12 title-card
scenes, chapters every three scenes, 1920x1080): three clips of 47.2, 47.4 and
48.8 s, each 1080x1920 with audio, at -14.07, -14.01 and -14.03 LUFS and a
true peak of -12.0 to -12.4 dBTP.

## Upload to YouTube (issue 12)

Uploads are automatic but stay private until Nghiep approves the weekly
batch. The YouTube secrets live only in lab-to-prod (GitHub Secrets), so the
upload runs there, in GitHub Actions; this repository never holds or reads a
token. Three steps:

1. **Stage** (`motion-studio stage-upload <job>.<lang>.mp4 --scenes
   scenes.json --publish-at 2026-12-01T19:00:00+07:00 [--shorts] [--tags
   a,b] [--description-file f]`, or `just stage-upload`). It refuses, and
   calls nothing, unless the video is the clean `<job>.<lang>.mp4` in its job
   folder and its `.gate.json` names it, is newer than it, says `ok`, lists
   all five checks as passed and used the default limits
   (`--allow-gate-limits` is for a test upload only). The publish time needs
   a UTC offset and must be in the future. Then it builds the metadata:
   - title: `title` of scenes.json in that language (at most 100 characters,
     no `<` or `>`);
   - description: the text of `--description-file` (else a one-line default),
     then `Chapters` with one `M:SS Name` line per chapter, then the "Made
     with AI" label (at most 5,000 bytes). Chapters start where the scene's
     `chapter` changes (as for Shorts), in whole seconds from the render's
     `.timeline.json`; the first starts at 0:00 (the video title when the
     first scene has no chapter). YouTube shows chapters only when there are
     at least 3 and each is at least 10 s long, so anything else is refused;
   - tags (at most 500 characters), language (`defaultLanguage` and
     `defaultAudioLanguage`), `publish_at`, and `synthetic_media: true`
     (YouTube's altered or synthetic content flag; the voice is TTS).
   With `--shorts`, each clip of the video's Shorts report (all clips found
   and inside the loudness targets, newer than the video) is an item too:
   title `<chapter, else video title> #Shorts`, its own SRT, public
   `--shorts-every-days` (1) x n days after the video.
   Every item (the video and its Shorts) carries the same `playlist` block
   `{title, description, privacy}`: `--playlist <title>` (a preset's title
   keeps that preset's description), else the `playlist` preset named by the
   topic file (`--topic`), else the preset of the video's language in
   voices.json (`playlists.by_language`: EN `Process Simulation Lab — Python
   from scratch`, VI `Mô phỏng quá trình hoá học bằng Python`). The preset
   `english-for-engineers` (`English for engineers — learn with me`) is for
   the lesson videos. lab-to-prod finds the playlist by exact title among the
   channel's playlists, creates it when missing (public by default), and adds
   each video once.
   The mp4s, SRTs and `manifest.json` go up as assets of the **draft**
   release `yt-<job>-<lang>` of lab-to-prod (`gh release create --draft`,
   `gh release upload --clobber`; manifest last). Draft releases and their
   assets are visible only to people with push access; a release that is not
   a draft, or a draft that already holds a `result.json` (an upload has
   started), is refused. The job holds its render lock meanwhile, so the cap
   cannot delete the files. `<job>/<job>.<lang>.staged.json` records the
   repository, the tag and the manifest. `--dry-run` prints the manifest and
   calls nothing.
2. **Upload**, after Nghiep approves the batch: the orchestrator runs
   lab-to-prod's `youtube-upload` workflow with the tag (`live=true`; without
   it the workflow is a dry run). It uploads every item as **private** with
   `publishAt` (YouTube makes it public then), adds the SRT as captions,
   logs the quota of every call, keeps `result.json` (video ids) on the draft
   so a re-run skips what is up, saves it as the artifact
   `youtube-upload-<tag>` and deletes the draft only when every item is up.
   Details: lab-to-prod `publish/README.md`.
3. **Finish** (`motion-studio finish-upload <job>/<job>.<lang>.staged.json`,
   or `just finish-upload`). It reads `result.json` from the artifact of the
   newest successful live run for the tag (`gh run list`, `gh run
   download`; or `--run ID`, or `--result FILE`), checks that it is
   complete and matches the staged items, and writes `<job>/upload.json` in
   the format of "Work dir and disk", with `platform`, `url`, `publish_at`,
   `captions` and `release` on each file. An earlier record of the job is
   merged (the English and Vietnamese videos are uploaded one after the
   other). Then it runs `clean --after-upload`, but only once every finished
   video of the job is in the record; until then it says which videos wait
   and deletes nothing. `--no-clean` writes the record only.

Every `gh` call goes through one wrapper (`$MOTION_STUDIO_GH` names the
binary), so the tests use a fake gh. `$MOTION_STUDIO_RELEASE_REPO` (or
`--repo`) changes the repository, `nghiep-pham-ecosys/lab-to-prod` by default.

## Quality checks per step

1. Script: `motion-studio validate` passes; every scene has narration in every
   declared language; length matches
   the target runtime (about 150 words per minute); factual claims reviewed by
   Nghiep.
2. Images: one per scene; resolution 1920x1080; spot-check character
   consistency on a contact sheet.
3. Voice: no clipped audio; per-scene duration recorded; loudness normalised
   (target -14 LUFS for the final mix, done by `render`, see "Loudness").
4. Assembly: 1920x1080, H.264 yuv420p, AAC 48 kHz stereo; audio/subtitle drift
   under 200 ms; no black frames; chapters match scene boundaries; output
   plays in a standard player. The render tests check resolution, codecs,
   frame count and audio/video end alignment.
5. Extras: thumbnail readable at small size; description lists chapters.

## Link to lab-to-prod

Each video also yields a blog post and a short post, generated from the same
`scenes.json`. Nghiep reviews both, and the video, before anything goes to
YouTube. The pipeline never publishes by itself: it stages a draft release,
and the upload (private, scheduled) runs only when the batch is approved
(see "Upload to YouTube").

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

## Decisions (2026-11-25, issue 17)

- English clone: Chatterbox (Resemble AI), MIT code and weights, local, with
  its Perth watermark on every line. XTTS-v2 and F5-TTS are non-commercial;
  OpenVoice V2 stays the fallback (see `docs/voices.md`). Two models are
  offered (`clone`: Turbo, `clone-standard`); Nghiep picks by ear before the
  English clone is used in a video.
- Chatterbox runs in its own venv and, from the other commands, as a worker
  process, because its pinned libraries clash with the Kokoro and VieNeu venv.

## Subtitles (issue 9)

`render` writes `<name>.<lang>.srt` from the narration timing by default
(`--subtitles none|srt|burn`); `burn` also writes `<name>.<lang>.burned.mp4`
with the text drawn in by libass on a translucent box in the theme's
subtitle colours (portrait frames get larger text, 20 characters per row,
placed above the Shorts buttons; `subtitles --burn` takes the theme from
`--theme` or `--scenes`, else light). No model is needed:
each narration line is trimmed to the speech in the rendered audio (10 ms
energy frames) and a long line is split at its nearest real pause.
`motion-studio subtitles <video> [--burn] [--align --scenes scenes.json]
[--check-drift]` rebuilds the SRT, aligns lines with faster-whisper when no
timing exists, or measures drift (`just drift <video>`).

Drift is the systematic offset between cue starts and faster-whisper word
starts. The gate: median offset, mean absolute offset and first-to-second
half change each at most 200 ms, and the 90th percentile at most 350 ms, so
one scene that is consistently off still fails. Single words can be off by up
to about 300 ms in Whisper itself: on the sample, the two worst English cues
(a breath before "then", the acronym "ffmpeg") start exactly where the audio
energy rises from silence, and large-v3-turbo puts those words in the same
wrong place. Whisper runs with Silero VAD and 30 ms padding; with the default
400 ms padding, word starts come out up to 0.5 s early.

Measured on 2026-11-25. The sample is the narration of
`samples/three-scenes` and `samples/visuals` (7 scenes) as title-card
scenes, narrated with `just narrate <file> en kokoro af_heart` and
`just narrate <file> vi vieneu "Trúc Ly"`, rendered at 1280x720, then
`just drift <video>` per language:

| Model | EN median / mean abs / p90 / max | VI median / mean abs / p90 / max | Gate | Time for 45 s audio (CPU int8) |
|-------|----------------------------------|----------------------------------|------|-------------------------------|
| tiny | 100 / 120 / 390 / 390 ms | 100 / 113 / 210 / 210 ms | fails (EN p90) | 2.4 s |
| base (default) | 10 / 70 / 230 / 230 ms | 25 / 43 / 90 / 90 ms | passes | 3.1 s |

After a pause, faster-whisper often starts the next word where the speech
before the pause ended, so a word "starts" inside the silence (seen on the
CSTR pilot: 1.0-1.5 s early after the 1.2 s gaps between Kokoro sentences). The
recogniser therefore moves a word with less than half of its 10 ms frames
voiced to the next speech onset, when that comes before the next recognised
word (`subtitles.snap_to_speech`); every other word keeps Whisper's time.

`base` is the smallest model that passes, and `tiny` also carries a +100 ms
bias of its own. `--align` without narration timing gave cue starts within
220 ms (EN) and 80 ms (VI) of the timing path on the same videos. Licences
(checked 2026-11-25): faster-whisper MIT, Whisper models MIT,
Systran/faster-whisper-tiny and -base MIT. Models live in
`~/.cache/huggingface`, never in the repo.

## Quality gate (issue 10)

A broken render must never reach YouTube. `motion-studio gate
<name>.<lang>.mp4 --scenes scenes.json` (or `just gate <video> <scenes>`)
runs five checks and exits 1 if any fails. It writes
`<name>.<lang>.gate.json` (every check with `ok`, a summary and the
measured values, plus the limits used) and prints one `PASS`/`FAIL` line per
check. The gate fails closed: a check that cannot run (a missing file, an
ffmpeg error, an unreadable report) fails. Only a video with a passing
`.gate.json` goes on to Nghiep's review.

| Check | Passes when | Why this limit |
|-------|-------------|----------------|
| `duration` | the video has a video and an audio stream whose lengths differ by at most 0.1 s; the video length matches `<name>.<lang>.timeline.json` (written by `render`) within 0.1 s; and it is between 600 and 1200 s | the target is 10-20 minute videos; `--min-s` and `--max-s` change the window (for samples and Shorts). 0.1 s is three frames at 30 fps and covers AAC's 21 ms frames; a cut or a lost scene is longer than that |
| `loudness` | integrated loudness is -14 LUFS +/- 1 LU and the true peak is at most -1.0 dBTP (ffmpeg `loudnorm` analysis, which measures per EBU R128 / ITU-R BS.1770) | YouTube's playback normalisation turns videos louder than about -14 LUFS down (a third-party measurement; YouTube does not publish the number) and does not turn quieter ones up, so a quieter video sounds weak next to others. +/- 1 LU is the usual delivery tolerance; -1 dBTP leaves room for the lossy re-encode, which raises peaks. Silence fails |
| `black-frames` | ffmpeg `blackdetect` finds no run of 0.1 s or more where 98 % of the pixels have luma below 5 % of the range (`d=0.1:pix_th=0.05:pic_th=0.98`) | no scene is meant to be black; three frames is the shortest flash a viewer notices. The ffmpeg default `pix_th=0.10` (luma 38 in limited range) flags the dark theme background (slate-900, luma 36) as black on a title card; 0.05 (luma 27) does not, and pure black (16) still fails |
| `subtitle-drift` | `<name>.<lang>.srt` exists, and `<name>.<lang>.drift.json` exists, is newer than the video and the SRT, and passes the drift limits in "Subtitles": median, mean absolute and first-to-second-half change at most 200 ms, 90th percentile at most 350 ms, at least half the cues matched | the limits are re-applied from the code, never read from the report, so an edited report cannot loosen them. The report comes from `just drift <video>` (faster-whisper in the TTS venv) or from `gate --check-drift` where faster-whisper is installed |
| `fact-check` | `factcheck.md` next to `scenes.json` has a `Reviewed-by:` line, a `Date: YYYY-MM-DD` line, a `Narration:` line with the first 12 hex characters of the narration hash, at least 3 checked claims (`- [x] ...`), each with a source (an `http(s)://` link or `source:`), and no unchecked claim (`- [ ] ...`) | factual claims are reviewed by a person before publishing (see "Quality checks per step"). The narration hash (sha256 over every scene's id and narration in every language) makes the file stale when the script changes, but not when `narrate` writes audio paths. A 10-minute explainer makes more than three factual claims, so fewer means the review was skipped. The gate prints the expected hash when it does not match |

One `factcheck.md` covers every language of a video, since the narration of
all languages is reviewed together. Example:

```markdown
# Fact check: RAG explained

Reviewed-by: nghiep
Date: 2026-11-25
Narration: 3f2a9c0d41be

- [x] RAG retrieves passages before generating - source: https://arxiv.org/abs/2005.11401
- [x] ...
```

The gate checks the clean `<name>.<lang>.mp4` with its sidecars; a
`.burned.mp4` copy is made from it and needs no gate of its own. `render`
normalises the final mix to the gate's loudness targets (see "Loudness"), so
a narrated render passes `loudness`; a silent one fails it.
