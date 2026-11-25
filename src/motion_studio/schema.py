"""Load and validate scenes.json.

Validation has two layers:

1. The JSON Schema in ``schema/scenes.schema.json`` (shape and types).
2. Cross-field rules a JSON Schema cannot express well: every localized field
   covers exactly the declared languages, scene ids are unique, a scene without
   ``duration_s`` has audio for every language, and referenced files exist.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 30


class ScenesError(ValueError):
    """Raised when a scenes.json file is invalid. ``errors`` lists every problem."""

    def __init__(self, errors: list[str], source: str = "scenes.json") -> None:
        self.errors = list(errors)
        self.source = source
        lines = "\n".join(f"  - {e}" for e in self.errors)
        super().__init__(f"{source} is invalid ({len(self.errors)} problem(s)):\n{lines}")


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    """Return the scenes.json JSON Schema shipped with the package."""
    text = resources.files("motion_studio").joinpath("schema/scenes.schema.json").read_text(encoding="utf-8")
    return json.loads(text)


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _where(path: Any) -> str:
    parts = [str(p) for p in path]
    return "/" + "/".join(parts) if parts else "(root)"


def schema_errors(doc: Any) -> list[str]:
    """Return JSON Schema errors as readable strings, sorted by location."""
    errors = sorted(_validator().iter_errors(doc), key=lambda e: [str(p) for p in e.absolute_path])
    out = []
    for err in errors:
        where = _where(err.absolute_path)
        if err.validator == "oneOf" and err.context:
            # The only oneOf is the visual: report against the declared kind instead of "not valid under any".
            if not isinstance(err.instance, dict):
                out.append(f"{where}: must be an object with kind title, image or video")
                continue
            if "kind" not in err.instance:
                out.append(f"{where}/kind: required (title, image or video)")
                continue
            kind = err.instance["kind"]
            branch = [e for e in err.context if _branch_kind(e) == kind]
            if branch:
                out.extend(_message(e) for e in branch)
                continue
            out.append(f"{where}: unknown visual kind {kind!r} (use title, image or video)")
            continue
        out.append(_message(err))
    return out


def _message(err: Any) -> str:
    where = _where(err.absolute_path)
    if err.validator == "pattern" and err.validator_value == load_schema()["$defs"]["path"]["pattern"]:
        return f"{where}: must be a relative path (got {err.instance!r})"
    return f"{where}: {err.message}"


def _branch_kind(err: Any) -> str | None:
    ref = err.schema_path[0] if err.schema_path else None
    branches = {0: "title", 1: "image", 2: "video"}
    return branches.get(ref) if isinstance(ref, int) else None


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a JSON number")


def _non_finite(value: Any, where: str = "") -> list[str]:
    """Paths of NaN or infinite floats (possible when a dict is built in Python)."""
    if isinstance(value, float) and not math.isfinite(value):
        return [where or "(root)"]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _non_finite(v, f"{where}/{k}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _non_finite(v, f"{where}/{i}")]
    return []


def _check_localized(value: dict[str, str], languages: list[str], where: str) -> list[str]:
    errs = []
    missing = [lang for lang in languages if lang not in value]
    extra = [lang for lang in value if lang not in languages]
    if missing:
        errs.append(f"{where}: missing language(s) {', '.join(missing)}")
    if extra:
        errs.append(f"{where}: language(s) {', '.join(extra)} not listed in /languages")
    return errs


def semantic_errors(doc: dict[str, Any], base_dir: Path | None = None) -> list[str]:
    """Cross-field checks. Assumes ``doc`` already passed the JSON Schema.

    With ``base_dir`` set, also checks that every referenced file exists.
    """
    errs: list[str] = []
    languages = doc["languages"]
    errs += _check_localized(doc["title"], languages, "/title")
    seen: dict[str, int] = {}
    for i, scene in enumerate(doc["scenes"]):
        where = f"/scenes/{i}"
        sid = scene["id"]
        if sid in seen:
            errs.append(f"{where}/id: duplicate id {sid!r} (first used at /scenes/{seen[sid]})")
        else:
            seen[sid] = i
        errs += _check_localized(scene["narration"], languages, f"{where}/narration")
        if "chapter" in scene:
            errs += _check_localized(scene["chapter"], languages, f"{where}/chapter")
        visual = scene["visual"]
        if visual["kind"] == "title":
            errs += _check_localized(visual["text"], languages, f"{where}/visual/text")
        audio = scene.get("audio", {})
        extra = [lang for lang in audio if lang not in languages]
        if extra:
            errs.append(f"{where}/audio: language(s) {', '.join(extra)} not listed in /languages")
        if "duration_s" not in scene:
            missing = [lang for lang in languages if lang not in audio]
            if missing:
                errs.append(
                    f"{where}: no duration_s and no audio for {', '.join(missing)}; set duration_s or add audio"
                )
        if base_dir is not None:
            if visual["kind"] in ("image", "video"):
                if not (base_dir / visual["src"]).is_file():
                    errs.append(f"{where}/visual/src: file not found: {visual['src']}")
            for lang, rel in audio.items():
                if not (base_dir / rel).is_file():
                    errs.append(f"{where}/audio/{lang}: file not found: {rel}")
    return errs


def validate(doc: Any, base_dir: Path | None = None) -> list[str]:
    """Return every problem with ``doc``; an empty list means it is valid."""
    bad = _non_finite(doc)
    if bad:
        return [f"{p}: must be a finite number" for p in bad]
    errs = schema_errors(doc)
    if errs:
        return errs
    return semantic_errors(doc, base_dir)


@dataclass(frozen=True)
class Scenes:
    """A validated scenes.json and the folder its relative paths resolve against."""

    doc: dict[str, Any]
    base_dir: Path

    @property
    def languages(self) -> list[str]:
        return list(self.doc["languages"])

    @property
    def scenes(self) -> list[dict[str, Any]]:
        return self.doc["scenes"]

    @property
    def width(self) -> int:
        return int(self.doc.get("output", {}).get("width", DEFAULT_WIDTH))

    @property
    def height(self) -> int:
        return int(self.doc.get("output", {}).get("height", DEFAULT_HEIGHT))

    @property
    def fps(self) -> int:
        return int(self.doc.get("output", {}).get("fps", DEFAULT_FPS))

    def resolve(self, rel: str) -> Path:
        return (self.base_dir / rel).resolve()


def from_dict(doc: Any, base_dir: Path, *, check_files: bool = True, source: str = "scenes.json") -> Scenes:
    """Validate ``doc`` and wrap it. Raises ScenesError listing every problem."""
    base_dir = Path(base_dir).resolve()
    errs = validate(doc, base_dir if check_files else None)
    if errs:
        raise ScenesError(errs, source)
    return Scenes(doc=doc, base_dir=base_dir)


def load(path: str | Path, *, check_files: bool = True) -> Scenes:
    """Read, parse and validate a scenes.json file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ScenesError([f"not UTF-8 text: {exc}"], str(path)) from exc
    try:
        doc = json.loads(text, parse_constant=_reject_constant)
    except ValueError as exc:  # JSONDecodeError is a ValueError
        raise ScenesError([f"not valid JSON: {exc}"], str(path)) from exc
    return from_dict(doc, path.parent, check_files=check_files, source=str(path))
