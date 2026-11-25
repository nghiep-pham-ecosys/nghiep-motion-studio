# nghiep-motion-studio tasks. Python is the system python3 (3.12); ffmpeg 6.1+ must be on PATH.

export PYTHONPATH := "src"
export MOTION_STUDIO_REQUIRE_FFMPEG := "1"

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

# Validate every sample scenes.json
validate:
    python3 -m motion_studio validate samples/*/scenes.json

# Render the 3-scene sample (EN and VI) into out/
sample:
    python3 -m motion_studio render samples/three-scenes/scenes.json -o out

# Create the local TTS environment (CUDA torch, Kokoro, VieNeu-TTS); model weights download on first use
tts-setup:
    [ -x "{{tts_python}}" ] || uv venv -p 3.12 "{{tts_venv}}"
    uv pip install -p "{{tts_python}}" torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128
    uv pip install -p "{{tts_python}}" "kokoro==0.9.4" "misaki[en]==0.9.4" "vieneu==3.8.4" "transformers==4.57.6" "soundfile>=0.12" "numpy>=1.26" "faster-whisper==1.2.1"
    uv pip install -p "{{tts_python}}" "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"

# Build the voice comparison page in out/voices (set MOTION_STUDIO_REF_WAV to a clean 6-8 s WAV to add the cloned voice)
voices:
    "{{tts_python}}" -m motion_studio voices -o out/voices --asr

# Narrate one language of a scenes.json in place (writes audio/tts/ next to it), e.g. just narrate out/demo/scenes.json vi vieneu "Trúc Ly"
narrate file lang engine voice:
    "{{tts_python}}" -m motion_studio narrate "{{file}}" --lang {{lang}} --engine {{engine}} --voice "{{voice}}"
