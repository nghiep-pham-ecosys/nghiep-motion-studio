"""The illustrated library: original 3D-cartoon stills of the two lesson characters, made once on a local GPU.

The prompts live in ``samples/library/prompts.json`` (characters, five places,
eight shot templates, the models and their licences). ``expand`` turns them
into one ``Shot`` per image: a reference portrait per character, then each
place x shot with its own seed from one seed family (``seed_base`` + 100 x
place + shot).

``motion-studio images-library`` runs ``images_worker`` in the images venv
(torch, diffusers: ``just images-setup``), one model load at a time:

1. the two text encoders encode every prompt, then leave the GPU;
2. SDXL base 1.0 draws the two portraits (no adapter);
3. the IP-Adapter image encoder encodes the portraits, then leaves the GPU;
4. SDXL with the IP-Adapter (plus, ViT-H) draws every shot with the portrait
   of the character in it (both portraits with a left/right mask in a
   two-shot), so faces, hair and clothes stay the same from shot to shot.

Images go to the library folder (default ``~/.cache/nghiep-motion-studio/library``,
never git) with ``library.json``: model, revision, licence, seed, prompt,
file and sha256 per shot, and a contact sheet to approve the characters.

``pick_shots`` chooses the shot of each lesson scene: a two-shot opens each
part, then the speaker's close-ups, the two-shot and the listener's reaction
take turns, never the same still twice in a row.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

HOME = Path(os.environ.get("MOTION_STUDIO_HOME", Path.home() / ".cache" / "nghiep-motion-studio")).expanduser()
LIBRARY_ENV = "MOTION_STUDIO_LIBRARY"
SPEC = Path(__file__).resolve().parents[2] / "samples" / "library" / "prompts.json"
MANIFEST = "library.json"
SHEET = "contact-sheet.jpg"
SIDES = ("her", "him")


class ImagesError(ValueError):
    """The library spec or the built library is wrong or missing; the message says what."""


def default_library() -> Path:
    return Path(os.environ.get(LIBRARY_ENV) or HOME / "library").expanduser()


def other(side: str) -> str:
    return "him" if side == "her" else "her"


@dataclass
class Shot:
    id: str  # e.g. classroom-her-1, or portrait-her
    place: str  # "" for a portrait
    shot: str  # the template: both-her, her-1, ..., or portrait
    who: str  # her, him or both (who is in the picture)
    talk: str  # whose line the shot is made for (her or him)
    focus: tuple[float, float]  # where the camera moves to, as a share of width and height
    prompt: str
    negative: str
    seed: int
    ip: tuple[str, ...] = ()  # the portraits it is conditioned on, left to right
    ip_scale: float = 0.0
    ip_ref: str = ""  # what the reference is (the adapter file and the face crops): a change makes the image stale
    width: int = 1344
    height: int = 768

    def key(self) -> str:
        """What decides the pixels: a changed prompt, seed or size makes the image stale."""
        blob = json.dumps([self.prompt, self.negative, self.seed, list(self.ip), self.ip_scale, self.ip_ref,
                           self.width, self.height], ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def file(self, fmt: str = "jpg") -> str:
        return f"{self.id}.{'png' if self.place == '' else fmt}"


def load_spec(path: str | Path = SPEC) -> dict[str, Any]:
    path = Path(path)
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImagesError(f"cannot read the library prompts {path}: {exc}") from exc
    for key in ("models", "generate", "output", "style", "negative", "characters", "portrait", "places", "shots",
                "seed_base"):
        if key not in spec:
            raise ImagesError(f"{path.name}: missing {key!r}")
    if set(spec["characters"]) != set(SIDES):
        raise ImagesError(f"{path.name}: characters must be exactly 'her' and 'him'")
    return spec


def _compose(*parts: str) -> str:
    return ", ".join(p.strip().rstrip(",") for p in parts if p and p.strip())


def expand(spec: dict[str, Any]) -> tuple[list[Shot], list[Shot]]:
    """(portraits, shots): every image of the library, in the order the worker draws them."""
    gen = spec["generate"]
    size = dict(width=int(gen["width"]), height=int(gen["height"]))
    chars = spec["characters"]
    portraits = [
        Shot(id=f"portrait-{side}", place="", shot="portrait", who=side, talk=side, focus=(0.5, 0.4),
             prompt=_compose(spec["portrait"].format(look=chars[side]["look"]), spec["style"]),
             negative=spec["negative"], seed=int(chars[side]["portrait_seed"]), **size)
        for side in SIDES
    ]
    shots = []
    skip = set(spec.get("skip", []))
    for p, (place, info) in enumerate(spec["places"].items()):
        fill = {"setting": info["setting"], "her_pose": info["her"], "him_pose": info["him"],
                "her_look": chars["her"]["look"], "him_look": chars["him"]["look"],
                "her_short": chars["her"]["short"], "him_short": chars["him"]["short"]}
        for s, (name, tmpl) in enumerate(spec["shots"].items()):
            if f"{place}-{name}" in skip:
                continue
            who = tmpl["who"]
            if who not in ("her", "him", "both") or tmpl["talk"] not in SIDES:
                raise ImagesError(f"shot {name}: who must be her, him or both and talk her or him")
            ip = SIDES if who == "both" else (who,)
            scale = float(gen["ip_scale_both"] if who == "both" else gen["ip_scale"])
            fx, fy = (float(v) for v in tmpl["focus"])
            ref = json.dumps([spec["models"]["ip_adapter"].get("file", ""),
                              *(chars[side].get("face") for side in ip)])
            seed = int(spec.get("seeds", {}).get(f"{place}-{name}", int(spec["seed_base"]) + 100 * p + s))
            shots.append(Shot(id=f"{place}-{name}", place=place, shot=name, who=who, talk=tmpl["talk"],
                              focus=(fx, fy), prompt=_compose(tmpl["prompt"].format(**fill), spec["style"]),
                              negative=spec["negative"], seed=seed, ip=ip, ip_scale=scale, ip_ref=ref, **size))
    ids = [s.id for s in portraits + shots]
    if len(ids) != len(set(ids)):
        raise ImagesError("two shots share an id")
    return portraits, shots


# --- the built library ---------------------------------------------------------------------------------------------


@dataclass
class Library:
    folder: Path
    shots: dict[str, dict[str, Any]] = field(default_factory=dict)  # id -> manifest entry (place shots only)

    def path(self, shot_id: str) -> Path:
        return self.folder / self.shots[shot_id]["file"]

    def has(self, place: str, shot: str) -> bool:
        return f"{place}-{shot}" in self.shots

    def places(self) -> set[str]:
        return {e["place"] for e in self.shots.values()}


def load_library(folder: str | Path | None = None, *, check_files: bool = True) -> Library:
    folder = Path(folder) if folder else default_library()
    manifest = folder / MANIFEST
    if not manifest.is_file():
        raise ImagesError(f"no {MANIFEST} in {folder}: build the library first (just images-library)")
    try:
        doc = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImagesError(f"cannot read {manifest}: {exc}") from exc
    lib = Library(folder=folder)
    for entry in doc.get("shots", []):
        if not entry.get("place"):
            continue
        if check_files and not (folder / entry["file"]).is_file():
            continue  # a shot that failed or was deleted: pick_shots falls back to another one
        lib.shots[entry["id"]] = entry
    if not lib.shots:
        raise ImagesError(f"{manifest} lists no image that exists")
    return lib


# --- which still for which line ------------------------------------------------------------------------------------


TWO_SHOT_FOCUS = {"her": (0.34, 0.42), "him": (0.66, 0.42)}  # she sits on the left, he on the right


def candidates(talk: str) -> list[str]:
    """The shots that may show a line of ``talk``, in the order they take turns."""
    return [f"{talk}-1", f"{talk}-2", f"both-{talk}", f"{other(talk)}-listen"]


def opening(talk: str) -> str:
    return f"both-{talk}"


def _with_other_two_shot(names: list[str]) -> list[str]:
    """A two-shot shows both people, so the other two-shot of the place may stand in for it."""
    out: list[str] = []
    for name in names:
        out.append(name)
        if name.startswith("both-"):
            out.append(f"both-{other(name[5:])}")
    return list(dict.fromkeys(out))


def _fallbacks(talk: str) -> list[str]:
    return [*candidates(talk), opening(other(talk)), *candidates(other(talk))]


def is_two_shot(entry: dict[str, Any]) -> bool:
    return str(entry.get("shot", "")).startswith("both-")


def focus_for(entry: dict[str, Any], talk: str) -> list[float]:
    """Where the camera moves: in a two-shot towards the one who talks (either two-shot serves both), else the
    shot's own focus."""
    focus = TWO_SHOT_FOCUS[talk] if is_two_shot(entry) else entry["focus"]
    return [round(float(v), 4) for v in focus]


def pick_shots(lines: Sequence[tuple[str, str, str]], library: Library) -> list[str]:
    """The shot id for each ``(part, place, talk)`` line.

    The first line of a part gets the two-shot of its speaker (the scene is
    set). Then the speaker's close-up, a second angle, the two-shot and the
    listener's reaction take turns per speaker and part, and a still never
    follows itself, so every line starts on a fresh picture.
    """
    out: list[str] = []
    turn: dict[tuple[str, str], int] = {}
    previous_part, previous = None, None
    for part, place, talk in lines:
        if place not in library.places():
            raise ImagesError(f"the library has no images of the place {place!r} (has: {', '.join(sorted(library.places()))})")
        if part != previous_part:
            order = [opening(talk), *candidates(talk)]
        else:
            k = turn.get((part, talk), 0)
            base = candidates(talk)
            order = base[k % len(base):] + base[:k % len(base)]
            turn[(part, talk)] = k + 1
        order = _with_other_two_shot(order + [s for s in _fallbacks(talk) if s not in order])
        available = [s for s in order if library.has(place, s)]
        choice = next((s for s in available if f"{place}-{s}" != previous), available[0] if available else None)
        if choice is None:
            raise ImagesError(f"the library has no shot for a line of {talk} in {place!r}")
        shot_id = f"{place}-{choice}"
        out.append(shot_id)
        previous_part, previous = part, shot_id
    return out


# --- running the worker --------------------------------------------------------------------------------------------


def images_python() -> Path:
    venv = os.environ.get("MOTION_STUDIO_IMAGES_VENV") or str(HOME / "images-venv")
    return Path(venv).expanduser() / "bin" / "python"


def worker_cmd(python: Path, spec: Path, out: Path, *, only: Iterable[str] = (), force: bool = False,
               sheet_only: bool = False, candidates: int = 0) -> list[str]:
    cmd = [str(python), "-m", "motion_studio.images_worker", "--spec", str(spec), "--out", str(out)]
    for shot in only:
        cmd += ["--only", shot]
    if force:
        cmd.append("--force")
    if sheet_only:
        cmd.append("--sheet-only")
    if candidates:
        cmd += ["--candidates", str(candidates)]
    return cmd


def run_worker(cmd: list[str]) -> int:
    """Run the GPU worker with this package on its path and the model cache under ~/.cache (no token needed)."""
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("HF_HOME", str(HOME / "hf"))
    env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    try:
        return subprocess.run(cmd, env=env, check=False).returncode
    except FileNotFoundError as exc:
        print(f"FAIL {cmd[0]} not found: run `just images-setup` first ({exc})", file=sys.stderr)
        return 1


def shot_dict(shot: Shot) -> dict[str, Any]:
    d = asdict(shot)
    d["focus"] = list(shot.focus)
    d["ip"] = list(shot.ip)
    return d
