"""Manim CE adapters: a custom Manim script, a data-driven diagram and an animated code block.

Manim is found, in order, through the ``MOTION_STUDIO_MANIM`` environment
variable (a command such as ``/path/.venv/bin/python -m manim``), the current
Python interpreter (``python -m manim``) or a ``manim`` executable on PATH.

The ``diagram`` and ``code`` kinds run the templates in ``templates/`` with a
JSON spec, so a scenes.json can ask for them with data only. Every Manim
run gets the theme as JSON at the path in ``MOTION_STUDIO_THEME`` (see
``theme.py``); the templates take all their colours and fonts from it. The ``manim``
kind runs a script from the repository: treat it as code, only from trusted
files.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import sys
from functools import lru_cache
from pathlib import Path

from ..theme import ENV_VAR as THEME_ENV
from .base import Adapter, BuildRequest, VisualError, run_tool

TEMPLATES = Path(__file__).resolve().parent / "templates"
INSTALL_HINT = (
    "install Manim CE with `pip install -e .[visuals]` (Linux needs libcairo2-dev, libpango1.0-dev and "
    "pkg-config first) or set MOTION_STUDIO_MANIM to a manim command"
)
CODE_LANGUAGES = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".sh": "bash", ".bash": "bash", ".zsh": "zsh", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".sql": "sql", ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cs": "csharp", ".rb": "ruby", ".php": "php", ".html": "html",
    ".css": "css", ".md": "markdown", ".dockerfile": "docker", ".tf": "terraform", ".ipynb": "json",
}


def manim_command() -> list[str] | None:
    """The command that runs Manim here, or None when it is not installed."""
    env = os.environ.get("MOTION_STUDIO_MANIM", "").strip()
    if env:
        return shlex.split(env)
    if importlib.util.find_spec("manim") is not None:
        return [sys.executable, "-m", "manim"]
    exe = shutil.which("manim")
    return [exe] if exe else None


@lru_cache(maxsize=8)
def _manim_version(cmd: tuple[str, ...]) -> str:
    try:
        out = run_tool([*cmd, "--version"], cwd=Path.cwd(), what="manim --version")
    except VisualError:
        return "unknown"
    return out.strip().splitlines()[-1] if out.strip() else "unknown"


def render_manim(
    script: Path, scene_class: str, req: BuildRequest, out: Path, work: Path, cwd: Path, spec: dict | None = None
) -> None:
    """Render ``scene_class`` from ``script`` to ``out`` at the request's size and frame rate."""
    cmd = manim_command()
    if cmd is None:
        raise VisualError(f"Manim CE not found: {INSTALL_HINT}")
    cfg = work / "manim.cfg"
    # -r sets the pixels only; without frame_width the scene keeps a 16:9 frame and other sizes crop it.
    cfg.write_text(
        f"[CLI]\nbackground_color = {req.background}\npixel_width = {req.width}\npixel_height = {req.height}\n"
        f"frame_height = 8.0\nframe_width = {8.0 * req.width / req.height:.6f}\n",
        encoding="utf-8",
    )
    media = work / "media"
    theme_path = work / "theme.json"
    theme_path.write_text(req.theme.json(), encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "MOTION_STUDIO_TARGET_S": f"{req.target_s:.6f}",
        "MOTION_STUDIO_LANG": req.lang,
        THEME_ENV: str(theme_path),
        "PYTHONWARNINGS": "ignore",
    })
    if spec is not None:
        spec_path = work / "spec.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        env["MOTION_STUDIO_SPEC"] = str(spec_path)
    run_tool(
        [
            *cmd, "render", "-c", str(cfg), str(script), scene_class,
            "-r", f"{req.width},{req.height}", "--fps", str(req.fps), "--format", "mp4",
            "--media_dir", str(media), "-o", "clip", "--disable_caching", "--progress_bar", "none",
            "-v", "WARNING",
        ],
        cwd=cwd, env=env, what=f"manim ({req.scene_id})",
    )
    found = [p for p in media.rglob("clip.mp4") if "partial_movie_files" not in p.parts]
    if len(found) != 1:
        raise VisualError(f"scene {req.scene_id!r}: manim wrote {len(found)} clip(s), expected 1")
    shutil.move(str(found[0]), out)


class _ManimBase(Adapter):
    def missing(self) -> str | None:
        return None if manim_command() is not None else f"Manim CE not found: {INSTALL_HINT}"

    def tool_version(self) -> str:
        cmd = manim_command()
        return _manim_version(tuple(cmd)) if cmd else ""


class ManimAdapter(_ManimBase):
    """``{"kind": "manim", "src": "diagrams/flow.py", "scene": "Flow"}``: a custom Manim scene.

    The script runs with its own folder as the working directory and can read
    ``MOTION_STUDIO_TARGET_S`` (the scene's planned length),
    ``MOTION_STUDIO_LANG`` and ``MOTION_STUDIO_THEME`` (the path of the theme
    JSON). The clip is not cut: a scene lasts at least as long as it.
    """

    kind = "manim"
    reads_run = True  # the script sees MOTION_STUDIO_TARGET_S and MOTION_STUDIO_LANG

    def build(self, req: BuildRequest, out: Path, work: Path) -> None:
        script = req.source()
        assert script is not None
        render_manim(script, req.visual["scene"], req, out, work, cwd=script.parent)


class DiagramAdapter(_ManimBase):
    """``{"kind": "diagram", "nodes": [...], "edges": [...], "steps": [...]}``: boxes and arrows from data."""

    kind = "diagram"
    paced = True
    template = TEMPLATES / "diagram_scene.py"

    def inputs(self, req: BuildRequest) -> list[Path]:
        return [self.template]

    def build(self, req: BuildRequest, out: Path, work: Path) -> None:
        v = req.visual
        spec = {
            "target_s": req.target_s,
            "title": v.get("title"),
            "nodes": [
                {"id": n["id"], "label": n["label"], "col": n["col"], "row": n["row"], "color": n.get("color")}
                for n in v["nodes"]
            ],
            "edges": [{"from": e["from"], "to": e["to"], "label": e.get("label")} for e in v.get("edges", [])],
            "steps": v.get("steps"),
        }
        render_manim(self.template, "DiagramScene", req, out, work, cwd=work, spec=spec)


class CodeAdapter(_ManimBase):
    """``{"kind": "code", "src": "code/rag.py", "highlight": [[1, 3]]}``: code written line by line, then highlighted.

    Built with Manim's ``Code`` mobject (syntax colours from the theme, or a
    Pygments style named by ``style``) instead
    of Motion Canvas: Motion Canvas renders through a browser editor and needs
    a Node project per video, which is too heavy for an automated pipeline.
    """

    kind = "code"
    paced = True
    template = TEMPLATES / "code_scene.py"

    def inputs(self, req: BuildRequest) -> list[Path]:
        return [*super().inputs(req), self.template]

    def build(self, req: BuildRequest, out: Path, work: Path) -> None:
        v = req.visual
        src = req.source()
        assert src is not None
        try:
            text = src.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise VisualError(f"scene {req.scene_id!r}: cannot read {v['src']} as UTF-8 text: {exc}") from exc
        # Pygments drops leading and trailing newlines, so do it here and shift the numbers to match the file.
        body = text.rstrip("\n")
        skipped = len(body) - len(body.lstrip("\n"))
        spec = {
            "target_s": req.target_s,
            "code": body.lstrip("\n") + "\n",
            "first_line": skipped + 1,
            "language": v.get("language") or CODE_LANGUAGES.get(src.suffix.lower()),
            "style": v.get("style"),  # None: the theme's code colours
            "highlight": [[max(a - skipped, 1), b - skipped] for a, b in v.get("highlight", [])],
            "title": v.get("title"),
        }
        render_manim(self.template, "CodeScene", req, out, work, cwd=work, spec=spec)
