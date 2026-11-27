"""The illustrated library: original anime stills of the two lesson characters, drawn once on a local GPU.

The prompts live in ``samples/library/prompts.json``: the model of each style
(repo, pinned revision, licence, link), the five places and two styles.

* Style D (the main look) is a chibi anime figure. Each character is drawn
  once per pose (portrait, talk, listen, think, point, laugh, nod) on a plain
  background, at one pinned seed (Lan 104, Minh 204). Each place gets an empty
  background ("no humans"). A shot is a composite: the background, the cut-out
  figures with a soft shadow (Lan on the left, Minh on the right), then one
  light img2img pass (strength 0.30) that blends them in. A two-person prompt
  cannot bind clothes and hair to one person; a composite of our own figures
  can.
* Style A (mixed in) is clean anime, framed head and shoulders. Each place has
  a talk and a listen panel per character and a wider talk shot. A two-shot is
  a split screen: her panel on the left, his on the right.

``expand`` turns the spec into one ``Item`` per image, in the order the worker
draws them; ``Item.params_hash`` changes with anything that changes the pixels (the
prompt, seed, size, layout and the items it is built from). ``motion-studio
images-library`` runs ``images_worker`` in the images venv (torch, diffusers:
``just images-setup``). Images go to the library folder (default
``~/.cache/nghiep-motion-studio/library``, never git) with ``library.json``
(model, revision, licence and seed of every image) and a contact sheet.

``pick_shots`` chooses the still of each lesson line: first the style of each
part (``pick_styles``: D, and A for about 1 line in 4, switching only at a part
boundary), then a still of that style and place that shows the speaker, so the
speech bubble never sits on a still of the other character.
"""

from __future__ import annotations

import hashlib
import itertools
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
SIDES = ("her", "him")  # left to right in every two-shot
KINDS = ("figure", "background", "draw", "scene", "split")
DEFAULT_FOCUS = (0.5, 0.36)


class ImagesError(ValueError):
    """The library spec or the built library is wrong or missing; the message says what."""


def default_library() -> Path:
    return Path(os.environ.get(LIBRARY_ENV) or HOME / "library").expanduser()


def other(side: str) -> str:
    return "him" if side == "her" else "her"


@dataclass
class Item:
    """One image of the library: a source (figure, background, panel, portrait) or a shot a lesson shows."""

    id: str
    kind: str  # figure, background, draw (txt2img), scene (composite + blend), split (two panels)
    style: str
    model: str  # key of spec["models"] ("" for a split: no model of its own)
    prompt: str = ""
    negative: str = ""
    seed: int = 0
    width: int = 1344
    height: int = 768
    deps: tuple[str, ...] = ()  # scene: background then figures, left to right; split: left and right panel
    layout: tuple[tuple[float, float], ...] = ()  # scene: (centre x, height) of each figure, as shares of the frame
    place: str = ""
    shot: str = ""  # the shot name for a still a lesson shows (both-her, her-1, ...), else ""
    who: tuple[str, ...] = ()  # who is in the picture, left to right
    talk: str = ""  # whose line the shot is made for
    focus: tuple[float, float] = DEFAULT_FOCUS  # where the camera moves (a solo shot: the face)
    faces: dict[str, tuple[float, float]] = field(default_factory=dict)  # a two-shot: the face of each person
    mirror: bool = False
    params_hash: str = ""  # set by expand: what decides the pixels

    @property
    def is_shot(self) -> bool:
        return bool(self.shot)

    def file(self) -> str:
        return f"{self.id}.{'jpg' if self.is_shot else 'png'}"

    def params(self) -> list[Any]:
        return [self.kind, self.model, self.prompt, self.negative, self.seed, self.width, self.height,
                [list(x) for x in self.layout], self.mirror]


def load_spec(path: str | Path = SPEC) -> dict[str, Any]:
    path = Path(path)
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImagesError(f"cannot read the library prompts {path}: {exc}") from exc
    for key in ("models", "generate", "blend", "output", "adult_negative", "characters", "places", "styles",
                "seed_base"):
        if key not in spec:
            raise ImagesError(f"{path.name}: missing {key!r}")
    if set(spec["characters"]) != set(SIDES):
        raise ImagesError(f"{path.name}: characters must be exactly 'her' and 'him'")
    for name, style in spec["styles"].items():
        if style.get("model") not in spec["models"]:
            raise ImagesError(f"{path.name}: style {name} names the model {style.get('model')!r}, which is not "
                              "under models")
        if set(style.get("characters", {})) != set(SIDES):
            raise ImagesError(f"{path.name}: style {name} must describe 'her' and 'him'")
    return spec


def model_for(spec: dict[str, Any], style: str) -> dict[str, Any]:
    """The base model of ``style``: repo, pinned revision, licence and link (with ``key``, its name in the spec)."""
    try:
        name = spec["styles"][style]["model"]
    except KeyError as exc:
        raise ImagesError(f"no style {style!r} in the library prompts") from exc
    model = spec["models"][name]
    for key in ("repo", "revision", "license", "url"):
        if not model.get(key):
            raise ImagesError(f"model {name}: missing {key!r}")
    return {"key": name, **model}


def _fill(template: str, **values: str) -> str:
    text = template.format(**values)
    return ", ".join(p.strip() for p in text.split(",") if p.strip())


def _neg(*parts: str) -> str:
    return ", ".join(p.strip().strip(",") for p in parts if p and p.strip())


def _people(sides: Sequence[str]) -> str:
    if len(sides) == 2:
        return "1girl, 1boy, adult woman, adult man"
    return "1girl, solo, adult woman" if sides[0] == "her" else "1boy, solo, adult man"


def _composite_style(spec: dict[str, Any], name: str, st: dict[str, Any], index: int) -> list[Item]:
    """Style D: figures per pose, an empty background per place, then each shot as a composite of them."""
    seeds = spec.get("seeds", {})
    seed_of = lambda iid, default: int(seeds.get(iid, default))  # noqa: E731
    q, model = st["quality"], st["model"]
    figures: list[Item] = []
    for side in SIDES:
        char = st["characters"][side]
        for pose, words in st["poses"].items():
            words = char.get("poses", {}).get(pose, words)  # a pose may read differently for one character
            iid = f"{name}-fig-{side}-{pose}"
            figures.append(Item(id=iid, kind="figure", style=name, model=model, width=1024, height=1024,
                                prompt=_fill(st["figure"], style=st["style"], tags=char["tags"], pose=words, quality=q),
                                negative=_neg(st["negative"], char["negative"]), seed=seed_of(iid, char["seed"]),
                                who=(side,)))
    backgrounds, scenes = [], []
    lay = st["layout"]
    floor = float(lay.get("floor", 22)) / 768
    for p, (place, setting) in enumerate(spec["places"].items()):
        bid = f"{name}-bg-{place}"
        backgrounds.append(Item(id=bid, kind="background", style=name, model=model, place=place,
                                prompt=_fill(st["background"], setting=setting, quality=q),
                                negative=st["background_negative"],
                                seed=seed_of(bid, int(spec["seed_base"]) + 1000 * index + 100 * p)))
        for s, (shot, info) in enumerate(st["shots"].items()):
            poses = dict(info["figures"])
            sides = tuple(x for x in SIDES if x in poses)
            if info["talk"] not in sides:
                raise ImagesError(f"style {name} shot {shot}: the speaker {info['talk']} is not in the picture")
            if len(sides) == 1 and place in info.get("alt", {}):
                poses[sides[0]] = info["alt"][place]
            mode = "two" if len(sides) == 2 else "solo"
            layout = tuple((float(lay[mode][x][0]), float(lay[mode][x][1])) for x in sides)
            faces = {x: (round(cx, 4), round(1 - floor - h + float(lay.get("face", 0.33)) * h, 4))
                     for x, (cx, h) in zip(sides, layout)}
            for x in sides:
                if poses[x] not in st["poses"]:
                    raise ImagesError(f"style {name} shot {shot}: unknown pose {poses[x]!r}")
            sid = f"{name}-{place}-{shot}"
            scenes.append(Item(id=sid, kind="scene", style=name, model=model, place=place, shot=shot, who=sides,
                               talk=info["talk"], deps=(bid, *(f"{name}-fig-{x}-{poses[x]}" for x in sides)),
                               layout=layout, faces=faces, focus=faces[info["talk"]],
                               prompt=_fill(st["blend"], people=_people(sides), setting=setting, quality=q),
                               negative=st["blend_negative"],
                               seed=seed_of(sid, int(spec["seed_base"]) + 1000 * index + 100 * p + s + 1)))
    return figures + backgrounds + scenes


def _drawn_style(spec: dict[str, Any], name: str, st: dict[str, Any]) -> list[Item]:
    """Style A: a portrait per character, talk and listen panels per place, a wide talk shot, split two-shots."""
    seeds = spec.get("seeds", {})
    mirror = set(spec.get("mirror", []))
    focus = spec.get("focus", {})
    seed_of = lambda iid, default: int(seeds.get(iid, default))  # noqa: E731
    q, model = st["quality"], st["model"]
    pw, ph = (int(v) for v in st["panel_size"])
    sw, sh = (int(v) for v in st["solo_size"])
    portraits, draws, splits = [], [], []
    for side in SIDES:
        char = st["characters"][side]
        iid = f"{name}-portrait-{side}"
        portraits.append(Item(id=iid, kind="draw", style=name, model=model, width=1024, height=1024, who=(side,),
                              prompt=_fill(st["portrait"], style=st["style"], tags=char["tags"], quality=q),
                              negative=_neg(st["negative"], char["negative"]), seed=seed_of(iid, char["seed"])))
    for place, setting in spec["places"].items():
        for side in SIDES:
            char = st["characters"][side]
            for pose, words in st["poses"].items():
                iid = f"{name}-panel-{place}-{side}-{pose}"
                draws.append(Item(id=iid, kind="draw", style=name, model=model, width=pw, height=ph, place=place,
                                  who=(side,), mirror=iid in mirror,
                                  prompt=_fill(st["panel"], style=st["style"], tags=char["tags"], pose=words,
                                               setting=setting, quality=q),
                                  negative=_neg(st["negative"], char["negative"]), seed=seed_of(iid, char["seed"])))
        for shot, info in st["shots"].items():
            sid = f"{name}-{place}-{shot}"
            talk = info["talk"]
            if "solo" in info:
                char = st["characters"][talk]
                f = tuple(float(v) for v in focus.get(sid, DEFAULT_FOCUS))
                draws.append(Item(id=sid, kind="draw", style=name, model=model, width=sw, height=sh, place=place,
                                  shot=shot, who=(talk,), talk=talk, focus=(f[0], f[1]), mirror=sid in mirror,
                                  prompt=_fill(st["solo"], style=st["style"], tags=char["tags"],
                                               pose=st["poses"][info["solo"]], setting=setting, quality=q),
                                  negative=_neg(st["negative"], char["negative"]), seed=seed_of(sid, char["seed"])))
            else:
                split = info["split"]
                faces = {"her": (0.25, DEFAULT_FOCUS[1]), "him": (0.75, DEFAULT_FOCUS[1])}
                splits.append(Item(id=sid, kind="split", style=name, model="", place=place, shot=shot, who=SIDES,
                                   talk=talk, faces=faces, focus=faces[talk], width=1920, height=1080,
                                   deps=tuple(f"{name}-panel-{place}-{x}-{split[x]}" for x in SIDES)))
    return portraits + draws + splits


def expand(spec: dict[str, Any]) -> list[Item]:
    """Every image of the library, in the order the worker draws them (sources before the shots built from them)."""
    items: list[Item] = []
    for index, (name, st) in enumerate(spec["styles"].items()):
        if "figure" in st:
            items += _composite_style(spec, name, st, index)
        else:
            items += _drawn_style(spec, name, st)
    order = {"figure": 0, "background": 1, "draw": 2, "scene": 3, "split": 4}
    items.sort(key=lambda it: order[it.kind])  # stable: the spec's order within a kind
    by_id: dict[str, Item] = {}
    adult = [t.strip() for t in spec["adult_negative"].split(",") if t.strip()]
    gen = json.dumps([spec["generate"], spec["blend"]], sort_keys=True)
    for it in items:
        if it.id in by_id:
            raise ImagesError(f"two images share the id {it.id}")
        missing = [d for d in it.deps if d not in by_id]
        if missing:
            raise ImagesError(f"{it.id} is built from {', '.join(missing)}, which the spec does not draw")
        if it.kind != "split" and not all(t in it.negative for t in adult):
            raise ImagesError(f"{it.id}: the negative prompt must keep the adult-only tags ({spec['adult_negative']})")
        revision = spec["models"][it.model]["revision"] if it.model else ""
        blob = json.dumps([it.params(), revision, gen, [by_id[d].params_hash for d in it.deps]], ensure_ascii=False)
        it.params_hash = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
        by_id[it.id] = it
    return items


def shots_of(items: Iterable[Item]) -> list[Item]:
    return [it for it in items if it.is_shot]


# --- the built library ---------------------------------------------------------------------------------------------


@dataclass
class Library:
    folder: Path
    shots: dict[str, dict[str, Any]] = field(default_factory=dict)  # id -> manifest entry (shots only)
    styles: dict[str, dict[str, Any]] = field(default_factory=dict)  # name -> {"name", "share", ...}

    def path(self, shot_id: str) -> Path:
        return self.folder / self.shots[shot_id]["file"]

    def shot_id(self, style: str, place: str, shot: str) -> str:
        return f"{style}-{place}-{shot}"

    def has(self, style: str, place: str, shot: str) -> bool:
        return self.shot_id(style, place, shot) in self.shots

    def at(self, style: str, place: str) -> dict[str, dict[str, Any]]:
        """The shots of ``style`` in ``place``, by shot name."""
        return {e["shot"]: e for e in self.shots.values() if e["style"] == style and e["place"] == place}

    def places(self, style: str | None = None) -> set[str]:
        return {e["place"] for e in self.shots.values() if style is None or e["style"] == style}

    def main_style(self) -> str:
        names = sorted({e["style"] for e in self.shots.values()})
        return max(names, key=lambda s: float(self.styles.get(s, {}).get("share", 0.0)))


def load_library(folder: str | Path | None = None, *, check_files: bool = True) -> Library:
    folder = Path(folder) if folder else default_library()
    manifest = folder / MANIFEST
    if not manifest.is_file():
        raise ImagesError(f"no {MANIFEST} in {folder}: build the library first (just images-library)")
    try:
        doc = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImagesError(f"cannot read {manifest}: {exc}") from exc
    if doc.get("version") != 2:
        raise ImagesError(f"{manifest} is not a version 2 library: build it again (just images-library)")
    lib = Library(folder=folder, styles=doc.get("styles", {}))
    for entry in doc.get("shots", []):
        if check_files and not (folder / entry["file"]).is_file():
            continue  # a shot that failed or was deleted: pick_shots uses another one
        lib.shots[entry["id"]] = entry
    if not lib.shots:
        raise ImagesError(f"{manifest} lists no image that exists")
    return lib


# --- which style for which part, which still for which line ---------------------------------------------------------


def shows(entry: dict[str, Any], side: str) -> bool:
    return side in entry.get("who", ())


def is_two_shot(entry: dict[str, Any]) -> bool:
    return len(entry.get("who", ())) == 2


def focus_for(entry: dict[str, Any], talk: str) -> list[float]:
    """Where the camera moves and the bubble's tail points: the face of the one who talks."""
    if not shows(entry, talk):
        raise ImagesError(f"{entry['id']} does not show {talk}: the bubble would sit on the other character")
    faces = entry.get("faces") or {}
    focus = faces.get(talk) or entry["focus"]
    return [round(float(v), 4) for v in focus]


def _parts(lines: Sequence[tuple[str, str, str]]) -> list[tuple[str, str, list[str]]]:
    """(part, place, talks) per part, in order; a part is a run of lines with the same part name."""
    out: list[tuple[str, str, list[str]]] = []
    for part, place, talk in lines:
        if not out or out[-1][0] != part:
            out.append((part, place, []))
        out[-1][2].append(talk)
    return out


def _serves(library: Library, style: str, place: str, talks: Iterable[str]) -> bool:
    shots = library.at(style, place)
    return all(any(shows(e, t) for e in shots.values()) for t in set(talks))


def pick_styles(lines: Sequence[tuple[str, str, str]], library: Library) -> list[str]:
    """The style of each line: the main style, and the other style for whole parts, close to its share of lines.

    The first part sets the main look and two mixed parts never follow each
    other. A part can take a style only if that style has a still of the
    place for each speaker of the part. Among the allowed sets of parts, the
    one whose share of lines is closest to the style's share wins (then fewer
    parts, then the earlier ones): the choice is the same on every run.
    """
    parts = _parts(lines)
    main = library.main_style()
    mixed = sorted(s for s in {e["style"] for e in library.shots.values()} if s != main)
    styles = [main] * len(parts)
    total = sum(len(t) for _, _, t in parts) or 1
    for style in mixed:
        target = float(library.styles.get(style, {}).get("share", 0.0))
        eligible = [i for i, (_, place, talks) in enumerate(parts)
                    if i > 0 and styles[i] == main and _serves(library, style, place, talks)]
        best: tuple[float, int, tuple[int, ...]] | None = None
        for n in range(len(eligible) + 1):
            for combo in itertools.combinations(eligible, n):
                if any(b - a == 1 for a, b in zip(combo, combo[1:])):
                    continue
                share = sum(len(parts[i][2]) for i in combo) / total
                score = (round(abs(share - target), 9), n, combo)
                if best is None or score < best:
                    best = score
        for i in (best[2] if best else ()):
            styles[i] = style
    out: list[str] = []
    for style, (_, _, talks) in zip(styles, parts):
        out += [style] * len(talks)
    return out


def rotation(talk: str) -> list[str]:
    """The speaker's shots in the order they take turns (only those the library has are used)."""
    return [f"{talk}-1", f"both-{talk}", f"{talk}-2", f"both-{talk}-2", f"{talk}-3"]


def opening(talk: str) -> str:
    return f"both-{talk}"


def pick_shots(lines: Sequence[tuple[str, str, str]], library: Library) -> list[str]:
    """The shot id for each ``(part, place, talk)`` line.

    Every still shows the speaker (a solo shot of them, or a two-shot), so the
    bubble always sits on its speaker. A part opens on the speaker's two-shot
    (the scene is set); then the speaker's shots take turns, and a still never
    follows itself, so every line starts on a fresh picture.
    """
    styles = pick_styles(lines, library)
    out: list[str] = []
    turn: dict[tuple[str, str], int] = {}
    previous_part, previous = None, None
    for (part, place, talk), style in zip(lines, styles):
        if place not in library.places(style):
            raise ImagesError(f"the library has no {style} images of the place {place!r} "
                              f"(has: {', '.join(sorted(library.places(style)))})")
        shots = library.at(style, place)
        mine = {name for name, e in shots.items() if shows(e, talk)}
        if not mine:
            raise ImagesError(f"the library has no {style} still of {talk} in {place!r}")
        base = [n for n in rotation(talk) if n in mine]
        rest = sorted(mine - set(base))
        if part != previous_part:
            order = [n for n in [opening(talk)] if n in mine] + base + rest
        else:
            k = turn.get((part, talk), 0)
            turn[(part, talk)] = k + 1
            order = (base[k % len(base):] + base[:k % len(base)] if base else []) + rest
        order = list(dict.fromkeys(order))
        choice = next((n for n in order if library.shot_id(style, place, n) != previous), order[0])
        shot_id = library.shot_id(style, place, choice)
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


def item_dict(item: Item) -> dict[str, Any]:
    d = asdict(item)
    for k in ("deps", "who", "focus"):
        d[k] = list(d[k])
    d["layout"] = [list(x) for x in item.layout]
    d["faces"] = {k: list(v) for k, v in item.faces.items()}
    return d
