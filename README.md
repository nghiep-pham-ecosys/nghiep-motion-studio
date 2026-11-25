# nghiep-motion-studio

An automated pipeline for long-form explainer videos (English and Vietnamese)
for the lab-to-prod content lab. Design: [docs/spec.md](docs/spec.md).

Status: in progress. Built: the `scenes.json` schema, the ffmpeg renderer,
local TTS narration (Kokoro for English, VieNeu-TTS for Vietnamese and a
cloned voice; see [docs/voices.md](docs/voices.md)) and generated visuals
(Manim diagrams and scenes, animated code, VHS terminal recordings).

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

Generated visuals (optional, free tools):

```sh
sudo apt install libcairo2-dev libpango1.0-dev pkg-config   # once, to build Manim CE
just visuals-setup     # Manim CE into ~/.cache/nghiep-motion-studio/visuals-venv
just vhs-install       # VHS and ttyd into ~/.cache/nghiep-motion-studio/bin (sha256-checked); VHS also needs Chrome or Chromium
just tools             # which visual kinds can run here
just sample-visuals    # out/visuals.en.mp4, out/visuals.vi.mp4
```

Tests skip an adapter whose tool is missing and say why.

Rendered output (`out/`, `*.mp4`, `*.wav`) stays out of git.
