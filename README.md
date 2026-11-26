# nghiep-motion-studio

An automated pipeline for long-form explainer videos (English and Vietnamese)
for the lab-to-prod content lab. Design: [docs/spec.md](docs/spec.md).

Status: in progress. Built: the `scenes.json` schema, the ffmpeg renderer,
local TTS narration (Kokoro for English, VieNeu-TTS for Vietnamese, a
cloned voice in both languages with VieNeu and Chatterbox, and a voice per
scene or speaker with a `voice_mix` policy; see [docs/voices.md](docs/voices.md)),
generated visuals (Manim diagrams and scenes, animated code, VHS terminal
recordings), subtitles, loudness normalisation, a quality gate for rendered videos, Shorts cut from
each video, a work dir with a size cap that is cleaned after upload, the hand-off to YouTube as
private, scheduled drafts, and one command that runs every step from a topic file.

## One command

```sh
just video samples/pilot-cstr/cstr-en.topic.json              # script -> scenes -> voice -> visuals -> render -> subtitles -> gate -> shorts
just video samples/pilot-cstr/cstr-vi.topic.json --stand-in   # a preset voice reads the narrator's lines while no reference clip is set
```

A topic file names the script (a scenes.json with the narration), its `factcheck.md` and a casting preset from
[`voices.json`](voices.json). The run stops at the first failing step and says how to resume (`--from <step>`); the
video, its gate report and Shorts land in `~/.cache/nghiep-motion-studio/work/<job>/`. No LLM or paid API is called.
The narrator is the owner's cloned voice: set `MOTION_STUDIO_REF_WAV_EN` (English) and `MOTION_STUDIO_REF_WAV`
(Vietnamese), or pass `--stand-in`, which is written into the video's metadata. Details: "One command" in
[docs/spec.md](docs/spec.md).

```sh
just lesson samples/lesson-2026-11-26/ai-eng-p3.lesson.md     # one English lesson -> a 20-30 min dialogue video, an m4a, Shorts
```

A lesson is played by two original animated characters ([`samples/characters/`](samples/characters/sheet.svg)) with
preset AI voices; the speaker's mouth follows its own audio. Details: "English lesson as a dialogue" in the spec.

## Quick start

Needs Python 3.12, `jsonschema` and ffmpeg 6.1+ on PATH.

```sh
python3 -m pip install -e .
motion-studio validate samples/three-scenes/scenes.json
motion-studio render samples/three-scenes/scenes.json   # <work dir>/three-scenes/three-scenes.en.mp4 and .vi.mp4
python3 -m unittest discover -s tests
```

Without installing: `PYTHONPATH=src python3 -m motion_studio ...`, or `just ci`
and `just sample`.

`render` normalises each video's audio to -14 LUFS and -2 dBTP true peak
with a two-pass ffmpeg `loudnorm`, the targets the quality gate checks. A
silent render (no narration) is left as it is and reported with a `WARN`
line; `--no-loudnorm` skips the step for drafts (see "Loudness" in
[docs/spec.md](docs/spec.md)).

Frames and visuals use the light theme by default; set `"theme": "dark"` in
`scenes.json` or pass `--theme dark` for the dark one (see "Theme" in
[docs/spec.md](docs/spec.md)).

Generated visuals (optional, free tools):

```sh
sudo apt install libcairo2-dev libpango1.0-dev pkg-config   # once, to build Manim CE
just visuals-setup     # Manim CE into ~/.cache/nghiep-motion-studio/visuals-venv
just vhs-install       # VHS and ttyd into ~/.cache/nghiep-motion-studio/bin (sha256-checked); VHS also needs Chrome or Chromium
just tools             # which visual kinds can run here
just sample-visuals    # <work dir>/visuals/visuals.en.mp4 and .vi.mp4
```

Tests skip an adapter whose tool is missing and say why.

Quality gate (every rendered video, before review):

```sh
just drift ~/.cache/nghiep-motion-studio/work/demo/demo.en.mp4   # subtitle drift report (faster-whisper, TTS venv)
just gate ~/.cache/nghiep-motion-studio/work/demo/demo.en.mp4 samples/demo/scenes.json   # duration, loudness, black frames, drift, fact check
```

The gate exits 1 when any check fails and writes `demo.en.gate.json` next to the video.
It needs a `factcheck.md` next to `scenes.json`; thresholds and the file
format are in "Quality gate" in [docs/spec.md](docs/spec.md).

Shorts (three 45-60 s vertical clips per video, cut at chapter marks and sentence ends; see "Shorts" in [docs/spec.md](docs/spec.md)):

```sh
just shorts ~/.cache/nghiep-motion-studio/work/demo/demo.en.mp4 --scenes samples/demo/scenes.json   # <job>/shorts/demo-1.en.mp4 ...
```

Each clip is 1080x1920 with the chapter title, large burned-in subtitles and audio normalised to -14 LUFS.
A video too short for three clips is reported (exit 1), never padded.

Upload to YouTube (private and scheduled; see "Upload to YouTube" in [docs/spec.md](docs/spec.md)):

```sh
just stage-upload ~/.cache/nghiep-motion-studio/work/demo/demo.en.mp4 --scenes samples/demo/scenes.json --publish-at 2026-12-01T19:00:00+07:00 --shorts
# after the batch is approved, lab-to-prod's youtube-upload workflow uploads it (private, publishAt)
just finish-upload ~/.cache/nghiep-motion-studio/work/demo/demo.en.staged.json   # upload.json, then the cleanup
```

`stage-upload` refuses a video whose quality gate did not pass. It puts the mp4, its SRT and the metadata (title,
description with chapters, tags, the "made with AI" label) on a draft release, which is not public. This repository
holds no YouTube token.

Disk (renders stay off the local disk; see "Work dir and disk" in [docs/spec.md](docs/spec.md)):

```sh
just disk                       # work dir size per job, free space on the Windows drive of the WSL disk
just prune                      # strip the oldest jobs to the cap (MOTION_STUDIO_WORK_CAP_GB, default 10)
just clean-uploaded <work dir>/<job>/upload.json   # after upload: delete videos, Shorts, intermediates
```

Renders go to `~/.cache/nghiep-motion-studio/work/<job>/` (set
`MOTION_STUDIO_WORK_DIR` to move it; `render -o <folder>` renders elsewhere).
Only `upload.json`, `scenes.json`, the script, `factcheck.md` and `.srt` files
survive a cleanup. Copying the master mp4 to Google Drive with rclone first is
optional and off (`--copy-to gdrive:<folder>` or `MOTION_STUDIO_CLOUD_COPY`).

Rendered output (`out/`, `*.mp4`, `*.wav`) stays out of git.
