"""VHS adapter: a terminal recording from a ``.tape`` file.

VHS (https://github.com/charmbracelet/vhs) types the tape's commands into a
real shell (through ttyd and a headless Chrome or Chromium) and records the
screen. The tape runs real commands with its own folder as the working
directory: treat it as code, only from trusted files.

The renderer owns the output: a tape must not set ``Output``, ``Set Width``,
``Set Height`` or ``Set Framerate``; the adapter adds them, plus a font size
scaled to the frame and the theme's terminal colours (``Set Theme``) unless
the tape sets its own.

VHS needs ``vhs``, ``ttyd`` and ``ffmpeg`` on PATH (``just vhs-install``
fetches the first two from their GitHub releases, checked by sha256) and a
Chrome or Chromium on PATH. Without a browser VHS would download one by
itself, so the adapter reports it missing instead.
"""

from __future__ import annotations

import os
import re
import shutil
from functools import lru_cache
from pathlib import Path

from ..schema import tape_owned_lines
from ..theme import LIGHT, Theme
from .base import Adapter, BuildRequest, VisualError, run_tool

# The names go-rod (the browser driver inside VHS) looks up on PATH.
BROWSERS = ("chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge")
_FONT_SIZE = re.compile(r"^\s*Set\s+FontSize\b", re.IGNORECASE)
_THEME = re.compile(r"^\s*Set\s+Theme\b", re.IGNORECASE)


def vhs_command() -> str | None:
    return os.environ.get("MOTION_STUDIO_VHS", "").strip() or shutil.which("vhs")


def browser() -> str | None:
    for name in BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    return None


@lru_cache(maxsize=4)
def _vhs_version(cmd: str) -> str:
    try:
        return run_tool([cmd, "--version"], cwd=Path.cwd(), what="vhs --version").strip() or "unknown"
    except VisualError:
        return "unknown"


def wrap_tape(tape: str, out: Path, width: int, height: int, fps: int, theme: Theme = LIGHT) -> str:
    """Return ``tape`` with the renderer's Output, size and theme settings in front.

    Raises VisualError if the tape sets what the renderer owns.
    """
    owned = tape_owned_lines(tape)
    if owned:
        raise VisualError(
            "remove these lines from the tape (the renderer sets output, size and frame rate): " + "; ".join(owned)
        )
    head = [
        f'Output "{out}"',
        f"Set Width {width}",
        f"Set Height {height}",
        f"Set Framerate {fps}",
    ]
    if not any(_FONT_SIZE.match(line) for line in tape.splitlines()):
        head.append(f"Set FontSize {max(12, height // 30)}")
    if not any(_THEME.match(line) for line in tape.splitlines()):
        head.append(f"Set Theme {theme.vhs_theme()}")
    return "\n".join(head) + "\n\n" + tape.rstrip("\n") + "\n"


class TerminalAdapter(Adapter):
    """``{"kind": "terminal", "src": "tapes/demo.tape"}``: a VHS terminal recording.

    The clip is not cut: a scene lasts at least as long as the recording.
    """

    kind = "terminal"

    def missing(self) -> str | None:
        gaps = []
        if not vhs_command():
            gaps.append("vhs (run `just vhs-install` or set MOTION_STUDIO_VHS)")
        if not shutil.which("ttyd"):
            gaps.append("ttyd (run `just vhs-install`)")
        if not shutil.which("ffmpeg"):
            gaps.append("ffmpeg")
        if not browser():
            gaps.append(f"a Chrome or Chromium on PATH as one of: {', '.join(BROWSERS)}")
        return ("VHS needs " + ", ".join(gaps)) if gaps else None

    def tool_version(self) -> str:
        cmd = vhs_command()
        return _vhs_version(cmd) if cmd else ""

    def build(self, req: BuildRequest, out: Path, work: Path) -> None:
        tape_path = req.source()
        assert tape_path is not None
        try:
            tape = tape_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise VisualError(f"scene {req.scene_id!r}: cannot read {req.visual['src']}: {exc}") from exc
        clip = work / "clip.mp4"
        try:
            wrapped = wrap_tape(tape, clip, req.width, req.height, req.fps, req.theme)
        except VisualError as exc:
            raise VisualError(f"scene {req.scene_id!r}: {req.visual['src']}: {exc}") from exc
        wrapped_path = work / "scene.tape"
        wrapped_path.write_text(wrapped, encoding="utf-8")
        cmd = vhs_command()
        assert cmd is not None
        run_tool([cmd, str(wrapped_path)], cwd=tape_path.parent, what=f"vhs ({req.scene_id})")
        if not clip.is_file():
            raise VisualError(f"scene {req.scene_id!r}: vhs finished without writing {clip.name}")
        shutil.move(str(clip), out)
