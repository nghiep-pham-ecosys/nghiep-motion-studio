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
private, scheduled drafts, one command that runs every step from a topic file, the English lesson as a dialogue,
and the daily explainer of a lab-to-prod post (or a study lesson) with code, output and chart cards.

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

```sh
just images-setup && just images-library                       # once: anime stills of both characters on the local GPU
just lesson samples/lesson-2026-11-26/ai-eng-p3.lesson.md --look illustrated
```

The illustrated look shows those stills ([contact sheet](samples/library/contact-sheet.jpg)) with a slow camera move on
the speaker and a large speech bubble. Details: "Illustrated look" in the spec.

### Explainer: one post (or study lesson) and its merged work

```sh
# a first talk file from the post (TODO lines to write; the build refuses them)
just explainer ../lab-to-prod/content/2026-W48/2026-11-27-ai-eng-p4 --draft-talk my.talk.md
# the pilot: post folder + talk file -> an 8-15 min explainer and 2 Shorts (job en-2026-11-27-ai-eng-p4-explainer)
just explainer samples/explainer-2026-11-27/2026-11-27-ai-eng-p4 --talk samples/explainer-2026-11-27/ai-eng-p4.talk.md
# the second source type: a study lesson (Concept, Worked example, Edge cases, Quiz)
just explainer samples/specialty-2026-11-27/saa-sqs-sns-eventbridge.md --talk samples/specialty-2026-11-27/saa-sqs-sns-eventbridge.talk.md
```

`motion-studio explainer SOURCE --talk TALK` reads a lab-to-prod post folder (`post.md` with its `## linkedin` blog
note, `image.png`, the scripts next to it and their `.expected` output, and the entries its `work:` names in the
nearest `work.yaml`) or one lesson file, plus a talk file written for the day. Lan and Minh (the lesson characters,
preset Kokoro voices) explain the problem, the method, the code that runs, the measured number and its limit.

- The talk file: `## Section {place}` starts a chapter (places: classroom, cafe, desk, interview, exam); `Lan:` and
  `Minh:` lines are split into sentences, one scene each, and each sentence must fit one subtitle cue.
- A `[...]` line sets what the next lines show: `[code FILE A-B hl C-D | title]` and `[output FILE A-B]` (lines of a
  file next to the source, at most 16), `[image FILE]`, `[chart TITLE | max N | label = value (text) | note ...]`,
  `[points TITLE | item | ...]` and `[flow TITLE | a -> b, c -> d]` become full-frame cards (the `card` visual: the
  panel, the speaker's still as an inset, the caption, the AI chips); `[card TITLE | line ...]` and `[still]` keep the
  illustrated look. `Lan: question? [pause 8]` adds an answer pause with a cue.
- Every number in the voice or on a card must occur in the source (the post, its scripts and `.expected` files, its
  work entries, or the lesson file); the build stops on one that does not. Code and output cards are copied from
  the files. The draft `factcheck.md` lists each number with the file and line it came from, unchecked, so the gate
  fails until a person checks it (save the reviewed one as `<talk>.factcheck.md` next to the talk file).
- The job is `en-<post folder>-explainer` (or `Job:` in the talk file), the gate window 8-15 min, 2 Shorts, and the
  playlist "Lab to Prod — AI engineering in practice" (study lessons: "Study notes explained"). After the render it
  writes `<job>.en.description.txt` with the chapters, the post link and the work link.

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
