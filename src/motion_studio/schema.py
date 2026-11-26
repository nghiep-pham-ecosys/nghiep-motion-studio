"""Load and validate scenes.json.

Validation has two layers:

1. The JSON Schema in ``schema/scenes.schema.json`` (shape and types).
2. Cross-field rules a JSON Schema cannot express well: every localized field
   covers exactly the declared languages, scene ids are unique, a scene without
   ``duration_s`` has audio for every language, diagrams and code highlights
   are consistent, speakers and ``voice_mix`` name defined speakers and known
   TTS engines, and referenced files exist.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 30
MAX_CODE_LINES = 40  # more lines than this are unreadable at 1080p; split the scene
FILE_KINDS = ("image", "video", "manim", "code", "terminal")  # visual kinds whose src must exist
_TAPE_OWNED = re.compile(r"^\s*(Output\b|Set\s+(Width|Height|Framerate)\b)", re.IGNORECASE)


def tape_owned_lines(tape: str) -> list[str]:
    """Lines of a VHS tape that set what the renderer owns (output file, size, frame rate)."""
    return [line.strip() for line in tape.splitlines() if _TAPE_OWNED.match(line)]


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


def visual_kinds() -> list[str]:
    """The visual kinds, in the order of the schema's oneOf."""
    schema = load_schema()
    refs = schema["$defs"]["scene"]["properties"]["visual"]["oneOf"]
    return [schema["$defs"][r["$ref"].rsplit("/", 1)[1]]["properties"]["kind"]["const"] for r in refs]


def _kind_list() -> str:
    kinds = visual_kinds()
    return ", ".join(kinds[:-1]) + " or " + kinds[-1]


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
                out.append(f"{where}: must be an object with kind {_kind_list()}")
                continue
            if "kind" not in err.instance:
                out.append(f"{where}/kind: required ({_kind_list()})")
                continue
            kind = err.instance["kind"]
            branch = [e for e in err.context if _branch_kind(e) == kind]
            if branch:
                out.extend(_message(e) for e in branch)
                continue
            out.append(f"{where}: unknown visual kind {kind!r} (use {_kind_list()})")
            continue
        out.append(_message(err))
    return out


def _message(err: Any) -> str:
    where = _where(err.absolute_path)
    if err.validator == "pattern" and err.validator_value == load_schema()["$defs"]["path"]["pattern"]:
        return f"{where}: must be a relative path (got {err.instance!r})"
    if err.validator == "pattern" and err.validator_value.startswith("\\.") and err.validator_value.endswith("(?![\\s\\S])"):
        ext = err.validator_value[1:].split("(", 1)[0]
        return f"{where}: must be a {ext} file (got {err.instance!r})"
    return f"{where}: {err.message}"


def _branch_kind(err: Any) -> str | None:
    ref = err.schema_path[0] if err.schema_path else None
    kinds = visual_kinds()
    return kinds[ref] if isinstance(ref, int) and 0 <= ref < len(kinds) else None


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
        elif visual["kind"] == "diagram":
            errs += _diagram_errors(visual, languages, f"{where}/visual")
        elif visual["kind"] == "code":
            errs += _code_errors(visual, languages, f"{where}/visual", base_dir)
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
            if visual["kind"] in FILE_KINDS:
                if not (base_dir / visual["src"]).is_file():
                    errs.append(f"{where}/visual/src: file not found: {visual['src']}")
                elif visual["kind"] == "terminal":
                    errs += _tape_errors(visual, f"{where}/visual", base_dir)
            if visual["kind"] == "dialogue":
                for key, rel in [("scenery", visual["scenery"]), *((f"cast/{k}", v) for k, v in visual["cast"].items())]:
                    if not (base_dir / rel).is_file():
                        errs.append(f"{where}/visual/{key}: file not found: {rel}")
            for lang, rel in audio.items():
                if not (base_dir / rel).is_file():
                    errs.append(f"{where}/audio/{lang}: file not found: {rel}")
    return errs


def _check_label(value: Any, languages: list[str], where: str) -> list[str]:
    return _check_localized(value, languages, where) if isinstance(value, dict) else []


def _diagram_errors(visual: dict[str, Any], languages: list[str], where: str) -> list[str]:
    errs: list[str] = []
    if "title" in visual:
        errs += _check_label(visual["title"], languages, f"{where}/title")
    ids: dict[str, int] = {}
    cells: dict[tuple[int, int], int] = {}
    for i, node in enumerate(visual["nodes"]):
        w = f"{where}/nodes/{i}"
        if node["id"] in ids:
            errs.append(f"{w}/id: duplicate node id {node['id']!r} (first used at {where}/nodes/{ids[node['id']]})")
        else:
            ids[node["id"]] = i
        cell = (node["col"], node["row"])
        if cell in cells:
            errs.append(f"{w}: col {cell[0]}, row {cell[1]} is already used by {where}/nodes/{cells[cell]}")
        else:
            cells[cell] = i
        errs += _check_label(node["label"], languages, f"{w}/label")
    for i, edge in enumerate(visual.get("edges", [])):
        w = f"{where}/edges/{i}"
        for end in ("from", "to"):
            if edge[end] not in ids:
                errs.append(f"{w}/{end}: unknown node id {edge[end]!r}")
        if edge["from"] == edge["to"]:
            errs.append(f"{w}: an edge must join two different nodes")
        if "label" in edge:
            errs += _check_label(edge["label"], languages, f"{w}/label")
    if "steps" in visual:
        seen: set[str] = set()
        for i, step in enumerate(visual["steps"]):
            for j, nid in enumerate(step):
                if nid not in ids:
                    errs.append(f"{where}/steps/{i}/{j}: unknown node id {nid!r}")
                elif nid in seen:
                    errs.append(f"{where}/steps/{i}/{j}: node {nid!r} is already in an earlier step")
                seen.add(nid)
        never = [nid for nid in ids if nid not in seen]
        if never:
            errs.append(f"{where}/steps: node(s) {', '.join(never)} are in no step, so they would never appear")
    return errs


def _code_errors(visual: dict[str, Any], languages: list[str], where: str, base_dir: Path | None) -> list[str]:
    errs: list[str] = []
    if "title" in visual:
        errs += _check_label(visual["title"], languages, f"{where}/title")
    ranges = visual.get("highlight", [])
    for i, (first, last) in enumerate(ranges):
        if first > last:
            errs.append(f"{where}/highlight/{i}: first line {first} is after last line {last}")
    if base_dir is None or not (base_dir / visual["src"]).is_file():
        return errs  # the file check reports a missing file
    try:
        text = (base_dir / visual["src"]).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return errs + [f"{where}/src: cannot read {visual['src']} as UTF-8 text ({exc.__class__.__name__})"]
    # Count as the code block does: split on newlines only, trailing newlines dropped.
    file_lines = text.rstrip("\n").split("\n") if text.strip("\n") else []
    lines = len(file_lines)
    if lines == 0:
        errs.append(f"{where}/src: {visual['src']} is empty")
    if lines > MAX_CODE_LINES:
        errs.append(f"{where}/src: {visual['src']} has {lines} lines; at most {MAX_CODE_LINES} are readable on screen")
    for i, (first, last) in enumerate(ranges):
        if last > lines:
            errs.append(f"{where}/highlight/{i}: line {last} is past the end of {visual['src']} ({lines} lines)")
        elif first <= last and not any(line.strip() for line in file_lines[first - 1 : last]):
            errs.append(f"{where}/highlight/{i}: lines {first}-{last} are blank; frame lines with code")
    return errs


def _tape_errors(visual: dict[str, Any], where: str, base_dir: Path) -> list[str]:
    try:
        owned = tape_owned_lines((base_dir / visual["src"]).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{where}/src: cannot read {visual['src']} as UTF-8 text ({exc.__class__.__name__})"]
    if owned:
        return [f"{where}/src: remove {'; '.join(owned)} from {visual['src']} (the renderer sets output, size and frame rate)"]
    return []


def validate(doc: Any, base_dir: Path | None = None) -> list[str]:
    """Return every problem with ``doc``; an empty list means it is valid."""
    bad = _non_finite(doc)
    if bad:
        return [f"{p}: must be a finite number" for p in bad]
    errs = schema_errors(doc)
    if errs:
        return errs
    from .tts import ENGINES  # a registry of names; no engine is imported
    from .voice_mix import mix_errors

    return semantic_errors(doc, base_dir) + mix_errors(doc, set(ENGINES))


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

    @property
    def theme(self) -> str | None:
        """The theme named in the file (None: the default)."""
        return self.doc.get("theme")

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
