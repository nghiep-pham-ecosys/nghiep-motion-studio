# nghiep-motion-studio

An automated pipeline for long-form explainer videos (English and Vietnamese)
for the lab-to-prod content lab. Design: [docs/spec.md](docs/spec.md).

Status: in progress. Built: the `scenes.json` schema, the ffmpeg renderer and
local TTS narration (Kokoro for English, VieNeu-TTS for Vietnamese and a
cloned voice; see [docs/voices.md](docs/voices.md)).

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

Rendered output (`out/`, `*.mp4`, `*.wav`) stays out of git.
