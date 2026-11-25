# nghiep-motion-studio tasks. Python is the system python3 (3.12); ffmpeg 6.1+ must be on PATH.

export PYTHONPATH := "src"
export MOTION_STUDIO_REQUIRE_FFMPEG := "1"

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
