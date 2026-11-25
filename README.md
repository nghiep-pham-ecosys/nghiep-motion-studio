# nghiep-motion-studio

An automated pipeline for long-form explainer videos (English and Vietnamese)
for the lab-to-prod content lab. Design: [docs/spec.md](docs/spec.md).

Status: in progress. Built: the `scenes.json` schema, the ffmpeg renderer,
local TTS narration (Kokoro for English, VieNeu-TTS for Vietnamese, a
cloned voice in both languages with VieNeu and Chatterbox, and a voice per
scene or speaker with a `voice_mix` policy; see [docs/voices.md](docs/voices.md)),
generated visuals (Manim diagrams and scenes, animated code, VHS terminal
recordings), subtitles, loudness normalisation and a quality gate for rendered videos.

## Quick start

Needs Python 3.12, `jsonschema` and ffmpeg 6.1+ on PATH.

```sh
python3 -m pip install -e .
motion-studio validate samples/three-scenes/scenes.json
motion-studio render samples/three-scenes/scenes.json -o out   # out/three-scenes.en.mp4, out/three-scenes.vi.mp4
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
just sample-visuals    # out/visuals.en.mp4, out/visuals.vi.mp4
```

Tests skip an adapter whose tool is missing and say why.

Quality gate (every rendered video, before review):

```sh
just drift out/demo.en.mp4                               # subtitle drift report (faster-whisper, TTS venv)
just gate out/demo.en.mp4 samples/demo/scenes.json      # duration, loudness, black frames, drift, fact check
```

The gate exits 1 when any check fails and writes `out/demo.en.gate.json`.
It needs a `factcheck.md` next to `scenes.json`; thresholds and the file
format are in "Quality gate" in [docs/spec.md](docs/spec.md).

Rendered output (`out/`, `*.mp4`, `*.wav`) stays out of git.
