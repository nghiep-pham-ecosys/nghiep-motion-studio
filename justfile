# nghiep-motion-studio tasks. Python is the system python3 (3.12); ffmpeg 6.1+ must be on PATH.

export PYTHONPATH := "src"
export MOTION_STUDIO_REQUIRE_FFMPEG := "1"
# Visual tools live outside the checkout (like the TTS venv), so worktrees share them and keep them:
# Manim CE in visuals_venv (`just visuals-setup`), VHS and ttyd in tools_bin (`just vhs-install`). Both come first on PATH.
visuals_venv := env_var_or_default("MOTION_STUDIO_VISUALS_VENV", home_directory() / ".cache/nghiep-motion-studio/visuals-venv")
tools_bin := env_var_or_default("MOTION_STUDIO_TOOLS_BIN", home_directory() / ".cache/nghiep-motion-studio/bin")
export PATH := visuals_venv / "bin" + ":" + tools_bin + ":" + env_var("PATH")

vhs_version := "0.12.1"
vhs_sha256 := "eb33787a5b1cebf91fe0b18bd231e2d71d36c1ff4babd99ddac1a6ab411efd10"
ttyd_version := "1.7.7"
ttyd_sha256 := "8a217c968aba172e0dbf3f34447218dc015bc4d5e59bf51db2f2cd12b7be4f55"

# Local TTS environment: outside the repo, next to the model caches.
tts_venv := env_var_or_default("MOTION_STUDIO_TTS_VENV", home_directory() / ".cache/nghiep-motion-studio/tts-venv")
tts_python := tts_venv / "bin/python"

default:
    @just --list

# Run the local checks (tests + sample validation)
ci: test validate

# Run the unit and render tests
test:
    python3 -m unittest discover -v -s tests -p 'test_*.py'

# Validate every sample scenes.json (and the per-language scenes.<lang>.json of the pilot)
validate:
    python3 -m motion_studio validate samples/*/scenes*.json

# One command from a topic file to a gated video and its Shorts (script, scenes, voice, visuals, render, subtitles, gate,
# shorts), e.g. just video samples/pilot-cstr/cstr-en.topic.json --stand-in
video topic *args:
    python3 -m motion_studio video "{{topic}}" --tts-python "{{tts_python}}" {{args}}

# Render the 3-scene sample (EN and VI) into the work dir (<work dir>/three-scenes/)
sample:
    python3 -m motion_studio render samples/three-scenes/scenes.json

# Create the local TTS environment (CUDA torch, Kokoro, VieNeu-TTS); model weights download on first use
tts-setup:
    [ -x "{{tts_python}}" ] || uv venv -p 3.12 "{{tts_venv}}"
    uv pip install -p "{{tts_python}}" torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128
    uv pip install -p "{{tts_python}}" "kokoro==0.9.4" "misaki[en]==0.9.4" "vieneu==3.8.4" "transformers==4.57.6" "soundfile>=0.12" "numpy>=1.26" "faster-whisper==1.2.1"
    uv pip install -p "{{tts_python}}" "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"

# The English clone (Chatterbox) in its own venv: it pins torch, transformers and numpy apart from the TTS venv.
# chatterbox-tts is installed without its own pins (torch 2.6, gradio); the list below is what its code imports, tested together.
clone_en_venv := env_var_or_default("MOTION_STUDIO_CLONE_EN_VENV", home_directory() / ".cache/nghiep-motion-studio/clone-en-venv")
clone_en_python := clone_en_venv / "bin/python"

# Create the English clone environment (Chatterbox 0.1.7); weights download on first use into ~/.cache/nghiep-motion-studio/hf
clone-en-setup:
    [ -x "{{clone_en_python}}" ] || uv venv -p 3.12 "{{clone_en_venv}}"
    uv pip install -p "{{clone_en_python}}" torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128
    uv pip install -p "{{clone_en_python}}" --no-deps "chatterbox-tts==0.1.7"
    uv pip install -p "{{clone_en_python}}" "numpy>=1.26,<2" "librosa==0.11.0" "transformers==5.2.0" "diffusers==0.29.0" "safetensors==0.5.3" "resemble-perth==1.0.1" "s3tokenizer==0.3.0" "conformer==0.3.2" "omegaconf" "pyloudnorm" "einops" "scipy" torch==2.11.0 torchaudio==2.11.0 --extra-index-url https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match

# Build the voice comparison page in out/voices (set MOTION_STUDIO_REF_WAV to a clean 6-8 s WAV to add the cloned voices; MOTION_STUDIO_REF_WAV_EN for an English clip)
voices:
    "{{tts_python}}" -m motion_studio voices -o out/voices --asr

# Narrate one language of a scenes.json in place (writes audio/tts/ next to it), e.g. just narrate out/demo/scenes.json vi vieneu "Trúc Ly"
narrate file lang engine voice:
    "{{tts_python}}" -m motion_studio narrate "{{file}}" --lang {{lang}} --engine {{engine}} --voice "{{voice}}"

# Narrate one language with the voices scenes.json sets (speakers, voice, voice_mix), e.g. just narrate-mix out/demo/scenes.json en
narrate-mix file lang:
    "{{tts_python}}" -m motion_studio narrate "{{file}}" --lang {{lang}}

# Render the generated-visuals sample (diagram, code, terminal, manim; EN and VI) into the work dir (<work dir>/visuals/)
sample-visuals:
    python3 -m motion_studio render samples/visuals/scenes.json

# Work dir size per job and free space on the Windows drive that holds the WSL disk
disk:
    python3 -m motion_studio disk

# Strip the oldest jobs in the work dir to its size cap (MOTION_STUDIO_WORK_CAP_GB, default 10); text files stay
prune:
    python3 -m motion_studio clean --cap

# After an upload: delete the job's videos, Shorts and intermediates, e.g. just clean-uploaded ~/.cache/nghiep-motion-studio/work/rag/upload.json
clean-uploaded record:
    python3 -m motion_studio clean --after-upload "{{record}}"

# Show which visual adapters can run on this machine
tools:
    python3 -m motion_studio tools

# Install Manim CE (and jsonschema) into visuals_venv (Linux: sudo apt install libcairo2-dev libpango1.0-dev pkg-config first)
visuals-setup:
    [ -x "{{visuals_venv}}/bin/python" ] || uv venv -p /usr/bin/python3 "{{visuals_venv}}"
    uv pip install -p "{{visuals_venv}}/bin/python" "manim==0.22.1" "jsonschema>=4.10"

# Install VHS and ttyd into tools_bin from their GitHub releases, checked by sha256 (VHS also needs Chrome or Chromium)
vhs-install:
    #!/usr/bin/env bash
    set -euo pipefail
    dl="$(mktemp -d)"
    trap 'rm -rf "${dl}"' EXIT
    bin="{{tools_bin}}"
    mkdir -p "${bin}"
    tarball="vhs_{{vhs_version}}_Linux_x86_64.tar.gz"
    curl -fsSL --retry 3 -o "${dl}/${tarball}" "https://github.com/charmbracelet/vhs/releases/download/v{{vhs_version}}/${tarball}"
    echo "{{vhs_sha256}}  ${dl}/${tarball}" | sha256sum -c -
    tar -xzf "${dl}/${tarball}" -C "${dl}"
    install -m 755 "${dl}/vhs_{{vhs_version}}_Linux_x86_64/vhs" "${bin}/vhs"
    curl -fsSL --retry 3 -o "${dl}/ttyd" "https://github.com/tsl0922/ttyd/releases/download/{{ttyd_version}}/ttyd.x86_64"
    echo "{{ttyd_sha256}}  ${dl}/ttyd" | sha256sum -c -
    install -m 755 "${dl}/ttyd" "${bin}/ttyd"
    "${bin}/vhs" --version
    "${bin}/ttyd" --version

# Check subtitle drift of a rendered video with faster-whisper base (CPU int8), e.g. just drift ~/.cache/nghiep-motion-studio/work/demo/demo.en.mp4
drift video:
    "{{tts_python}}" -m motion_studio subtitles "{{video}}" --check-drift

# Quality gate for a rendered video, e.g. just gate ~/.cache/nghiep-motion-studio/work/demo/demo.en.mp4 samples/demo/scenes.json (exit 1 on any failure; writes <name>.<lang>.gate.json; run `just drift` first)
gate video scenes *args:
    python3 -m motion_studio gate "{{video}}" --scenes "{{scenes}}" {{args}}

# Cut 3 Shorts (45-60 s, 9:16, large subtitles) at chapter marks into the job's shorts/ folder, e.g. just shorts ~/.cache/nghiep-motion-studio/work/rag/rag.en.mp4 --scenes samples/rag/scenes.json
shorts video *args:
    python3 -m motion_studio shorts "{{video}}" {{args}}

# Stage a gated video on a draft release of lab-to-prod for the youtube-upload workflow (private, scheduled), e.g.
# just stage-upload ~/.cache/nghiep-motion-studio/work/rag/rag.en.mp4 --scenes samples/rag/scenes.json --publish-at 2026-12-01T19:00:00+07:00 --shorts
stage-upload video *args:
    python3 -m motion_studio stage-upload "{{video}}" {{args}}

# After the youtube-upload workflow: write the job's upload.json and clean it once every video is up, e.g.
# just finish-upload ~/.cache/nghiep-motion-studio/work/rag/rag.en.staged.json
finish-upload staged *args:
    python3 -m motion_studio finish-upload "{{staged}}" {{args}}
