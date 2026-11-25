"""Generated visuals: build Manim, diagram, code and terminal clips, then hand them to the renderer.

A scene whose visual kind is one of ``GENERATED_KINDS`` is built into an mp4
clip (cached by content) and replaced, in a copy of the scenes, by a plain
``video`` visual that points at the clip. The renderer then places it like
any other clip.

Paced kinds (``diagram``, ``code``) fit their motion inside the scene's
planned length. The others (``manim``, ``terminal``) have their own length;
a scene is stretched so such a clip is never cut.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ..schema import Scenes
from .base import Adapter, BuildRequest, VisualError
from .manim_ce import CodeAdapter, DiagramAdapter, ManimAdapter
from .vhs import TerminalAdapter

ADAPTERS: dict[str, Adapter] = {a.kind: a for a in (ManimAdapter(), DiagramAdapter(), CodeAdapter(), TerminalAdapter())}
GENERATED_KINDS = frozenset(ADAPTERS)

__all__ = [
    "ADAPTERS", "GENERATED_KINDS", "Adapter", "BuildRequest", "BuiltVisual", "VisualError",
    "build_visuals", "default_cache_dir", "localize", "missing_tools",
]


@dataclass(frozen=True)
class BuiltVisual:
    scene_id: str
    kind: str
    clip: Path
    cached: bool  # True when the clip came from the cache


def default_cache_dir(scenes: Scenes) -> Path:
    return scenes.base_dir / ".cache" / "visuals"


def localize(value: Any, lang: str, languages: Iterable[str]) -> Any:
    """Resolve every ``{lang: text}`` object inside ``value`` to the text for ``lang``."""
    langs = set(languages)
    if isinstance(value, dict):
        if value and set(value) <= langs and all(isinstance(v, str) for v in value.values()):
            return value[lang]
        return {k: localize(v, lang, langs) for k, v in value.items()}
    if isinstance(value, list):
        return [localize(v, lang, langs) for v in value]
    return value


def missing_tools(kinds: Iterable[str] | None = None) -> dict[str, str | None]:
    """For each adapter kind, why it cannot run here (None when it can)."""
    return {k: ADAPTERS[k].missing() for k in (kinds if kinds is not None else ADAPTERS)}


def build_visuals(
    scenes: Scenes,
    lang: str,
    targets: dict[str, float],
    cache_dir: Path,
    *,
    default_background: str,
    clip_length: Callable[[Path], float],
    jobs: int = 1,
    rebuild: bool = False,
) -> tuple[Scenes, list[BuiltVisual]]:
    """Build every generated visual for ``lang`` and return scenes that point at the clips.

    ``targets`` maps a scene id to its planned length in seconds.
    """
    cache_dir = Path(cache_dir).resolve()  # tools run in other folders, and the clip path must be absolute
    jobs_by_key: dict[str, tuple[Adapter, BuildRequest, Path]] = {}
    per_scene: list[tuple[int, Adapter, BuildRequest, Path]] = []
    for i, scene in enumerate(scenes.scenes):
        visual = scene["visual"]
        adapter = ADAPTERS.get(visual["kind"])
        if adapter is None:
            continue
        why = adapter.missing()
        if why:
            raise VisualError(f"scene {scene['id']!r}: a {adapter.kind} visual cannot be built here: {why}")
        req = BuildRequest(
            scene_id=scene["id"],
            lang=lang,
            visual=localize(visual, lang, scenes.languages),
            base_dir=scenes.base_dir,
            width=scenes.width,
            height=scenes.height,
            fps=scenes.fps,
            background=visual.get("background", default_background),
            target_s=float(targets[scene["id"]]),
        )
        out = Path(cache_dir) / f"{adapter.kind}-{adapter.cache_key(req)}.mp4"
        per_scene.append((i, adapter, req, out))
        jobs_by_key.setdefault(str(out), (adapter, req, out))
    if not per_scene:
        return scenes, []

    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    todo = [job for job in jobs_by_key.values() if rebuild or not job[2].is_file()]
    fresh = {str(job[2]) for job in todo}

    def build_one(job: tuple[Adapter, BuildRequest, Path]) -> None:
        adapter, req, out = job
        work = Path(tempfile.mkdtemp(prefix=f"build-{adapter.kind}-", dir=cache_dir))
        try:
            tmp = work / "out.mp4"
            adapter.build(req, tmp, work)
            if not tmp.is_file() or tmp.stat().st_size == 0:
                raise VisualError(f"scene {req.scene_id!r}: the {adapter.kind} adapter wrote no clip")
            os.replace(tmp, out)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        list(pool.map(build_one, todo))

    doc = dict(scenes.doc)
    doc["scenes"] = [dict(s) for s in scenes.scenes]
    built: list[BuiltVisual] = []
    for i, adapter, req, out in per_scene:
        scene = doc["scenes"][i]
        original = scene["visual"]
        scene["visual"] = {"kind": "video", "src": str(out), "background": req.background}
        if original.get("loop"):
            scene["visual"]["loop"] = True
        if not adapter.paced:
            length = clip_length(out)
            if length > req.target_s:
                scene["duration_s"] = max(float(scene.get("duration_s", 0.0)), length)
        built.append(BuiltVisual(scene_id=req.scene_id, kind=adapter.kind, clip=out, cached=str(out) not in fresh))
    return Scenes(doc=doc, base_dir=scenes.base_dir), built
