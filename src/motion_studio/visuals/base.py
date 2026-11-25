"""The visual adapter interface.

An adapter turns one generated visual (a Manim script, a diagram, a code
block, a VHS tape) into a video clip at the output size and frame rate. The
renderer then places that clip like any ``video`` visual.

Clips are cached by content: the key covers the adapter and tool versions,
the visual (with its text resolved to one language), the output size, frame
rate, background and target length, and the bytes of every input file (the
source and any built-in template). A changed input gives a new key, so a
stale clip is not reused. Files a source pulls in by itself (a module a
Manim script imports, a file a tape reads) are not tracked: rebuild with
``--rebuild-visuals`` after changing one.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class VisualError(RuntimeError):
    """A visual could not be built (tool missing, tool failed or bad input)."""


@dataclass(frozen=True)
class BuildRequest:
    """Everything an adapter needs to build one clip."""

    scene_id: str
    lang: str
    visual: dict[str, Any]  # localized fields already resolved to ``lang``
    base_dir: Path
    width: int
    height: int
    fps: int
    background: str
    target_s: float  # the scene's planned length; templates fit their motion inside it

    def source(self) -> Path | None:
        """The visual's source file, if it has one."""
        src = self.visual.get("src")
        return (self.base_dir / src).resolve() if src else None


class Adapter:
    """Base class. Subclasses set ``kind`` and ``version`` and implement ``missing`` and ``build``."""

    kind: str = ""
    version: str = "1"  # bump when the adapter's output changes for the same input
    localized: tuple[str, ...] = ()  # visual fields that may be {lang: text}
    paced: bool = False  # True when the clip is made to fit ``target_s`` exactly
    reads_run: bool = False  # True when the build sees ``target_s`` and ``lang`` (so both key the cache)

    def missing(self) -> str | None:
        """Why this adapter cannot run here (None when it can)."""
        raise NotImplementedError

    def tool_version(self) -> str:
        """A version string for the external tool; part of the cache key."""
        return ""

    def inputs(self, req: BuildRequest) -> list[Path]:
        """Files whose bytes decide the clip (part of the cache key): the source, plus any template."""
        src = req.source()
        return [src] if src is not None else []

    def build(self, req: BuildRequest, out: Path, work: Path) -> None:
        """Write the clip for ``req`` to ``out`` (mp4). ``work`` is an empty scratch folder."""
        raise NotImplementedError

    def cache_key(self, req: BuildRequest) -> str:
        payload = {
            "kind": self.kind,
            "adapter": self.version,
            "tool": self.tool_version(),
            "visual": req.visual,
            "size": [req.width, req.height, req.fps],
            "background": req.background,
            "target_s": round(req.target_s, 6) if (self.paced or self.reads_run) else None,
            "lang": req.lang if self.reads_run else None,
            "inputs": [_file_digest(p) for p in self.inputs(req)],
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:20]


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise VisualError(f"cannot read {path}: {exc}") from exc


def run_tool(cmd: list[str], cwd: Path, env: dict[str, str] | None = None, what: str = "") -> str:
    """Run an external tool; raise VisualError with the last lines of its output on failure."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise VisualError(f"{cmd[0]} not found") from exc
    if proc.returncode != 0:
        lines = (proc.stderr.strip() or proc.stdout.strip()).splitlines()
        tail = "\n".join(lines[-25:])
        raise VisualError(f"{what or cmd[0]} failed ({proc.returncode}): {' '.join(cmd)}\n{tail}")
    return proc.stdout


def which(name: str) -> str | None:
    return shutil.which(name)
