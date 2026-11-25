"""One command from a topic file to a gated video and its Shorts (``motion-studio video``).

A *topic file* (JSON, ``schema/topic.schema.json``) names the script (a
scenes.json with the narration), its fact check and the casting. The pipeline
copies them into the job folder of the work dir and runs the existing
commands in order, stopping at the first step that fails:

    script -> scenes -> voice -> visuals -> render -> subtitles -> gate -> shorts

* ``script``: read the topic file and the scenes file, write ``script.md``
  (the narration as a readable script) into the job, estimate the length.
  No LLM is called: the script is written beforehand (P5, no paid API).
* ``scenes``: copy scenes.json, its files and factcheck.md into the job, cast
  the voices from the voices config (``narrator`` and ``guest`` speakers),
  validate, and check the fact check against the narration early.
* ``voice``: ``narrate`` per language, in the TTS venv (one process per
  language, so each model is closed before the next language starts).
* ``visuals``: build the generated clips. It runs after the voice step
  because a clip is fitted to its scene, and a narrated scene lasts as long as
  its audio; built earlier, every clip would be built again by ``render``.
* ``render``: check the planned length against the gate's window first, then
  ``render`` (writes the mp4, the timeline, the lines and the SRT).
* ``subtitles``: measure the drift of the SRT against faster-whisper (TTS
  venv). It needs the rendered audio, so it follows ``render``.
* ``gate`` and ``shorts``: the quality gate, then the Shorts.

The narrator is the owner's cloned voice. Its reference clip is named by an
environment variable (never by a file in the repo). When it is unset the run
stops, unless ``--stand-in`` (or ``"stand_in": true``) asks for the preset's
stand-in voice; that is then written into the video's metadata (``comment``)
and the run report.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator

from . import workdir
from .gate import MAX_DURATION_S, MIN_DURATION_S, Limits, check_factcheck
from .narrate import split_lines
from .schema import FILE_KINDS, Scenes, ScenesError, load
from .visuals import GENERATED_KINDS, missing_tools

STEPS = ("script", "scenes", "voice", "visuals", "render", "subtitles", "gate", "shorts")
SETUP_STEPS = ("script", "scenes")  # always run: they make the job folder that every later step reads
VOICES_NAME = "voices.json"
NARRATOR, GUEST = "narrator", "guest"
WORDS_PER_MINUTE = 150  # the spec's planning rate; a real voice is read again from its audio before render
REPORT_SUFFIX = ".video.json"
VIENEU_FAMILY = ("vieneu", "clone")  # one VieNeu model serves both: the clone is the voice "clone" of vieneu

Runner = Callable[[Sequence[str], Mapping[str, str]], int]


class PipelineError(RuntimeError):
    """A topic file, voices config or step input is wrong; the message says what to change."""


# --- topic file and voices config ------------------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    text = resources.files("motion_studio").joinpath(f"schema/{name}").read_text(encoding="utf-8")
    return Draft202012Validator(json.loads(text))


def _read_json(path: Path, schema: str, what: str) -> dict[str, Any]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PipelineError(f"{what} {path} not found") from None
    except (OSError, ValueError) as exc:
        raise PipelineError(f"cannot read {what} {path}: {exc}") from exc
    errors = sorted(_validator(schema).iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        lines = "\n".join(f"  - /{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in errors)
        raise PipelineError(f"{what} {path} is invalid:\n{lines}")
    return doc


@dataclass
class Topic:
    path: Path
    job: str
    scenes: Path
    factcheck: Path
    cast: dict[str, dict[str, str]]
    voices: Path
    languages: list[str] | None = None
    assets: list[str] = field(default_factory=list)
    stand_in: bool = False
    shorts: int = 3
    min_s: float = MIN_DURATION_S
    max_s: float = MAX_DURATION_S
    topic: str = ""


def find_voices_config(start: Path) -> Path | None:
    """The nearest voices.json in ``start`` or a parent folder."""
    for folder in [start, *start.parents]:
        candidate = folder / VOICES_NAME
        if candidate.is_file():
            return candidate
    return None


def load_topic(path: str | Path) -> Topic:
    path = Path(path).resolve()
    doc = _read_json(path, "topic.schema.json", "topic file")
    base = path.parent
    gate = doc.get("gate", {})
    if "voices" in doc:
        voices = base / doc["voices"]
    else:
        found = find_voices_config(base)
        if found is None:
            raise PipelineError(f"no {VOICES_NAME} next to {path.name} or in a parent folder; set \"voices\" in the topic file")
        voices = found
    topic = Topic(
        path=path, job=doc["job"], scenes=base / doc["scenes"], factcheck=base / doc["factcheck"], cast=doc["cast"],
        voices=voices, languages=doc.get("languages"), assets=list(doc.get("assets", [])),
        stand_in=bool(doc.get("stand_in", False)), shorts=int(doc.get("shorts", 3)),
        min_s=float(gate.get("min_s", MIN_DURATION_S)), max_s=float(gate.get("max_s", MAX_DURATION_S)),
        topic=doc.get("topic", ""),
    )
    if topic.min_s > topic.max_s:
        raise PipelineError(f"{path.name}: gate min_s {topic.min_s:g} is larger than max_s {topic.max_s:g}")
    for what, file in (("scenes", topic.scenes), ("factcheck", topic.factcheck), ("voices", topic.voices)):
        if not file.is_file():
            raise PipelineError(f"{path.name}: {what} file {file} not found")
    return topic


def load_voices(path: str | Path) -> dict[str, Any]:
    """Read and check the voices config: a stand-in never shares a voice with a guest of its preset."""
    doc = _read_json(Path(path), "voices.schema.json", "voices config")
    problems = []
    for name, preset in doc["presets"].items():
        for lang, stand_in in preset["stand_in"].items():
            if stand_in in preset["guest"].get(lang, []):
                problems.append(f"preset {name}: the {lang} stand-in {stand_in['voice']!r} is also a guest voice, "
                                "so a dialogue would have one voice")
    if problems:
        raise PipelineError(f"voices config {path} is invalid:\n" + "\n".join(f"  - {p}" for p in problems))
    return doc


@dataclass
class Cast:
    """Who reads a language: the narrator (owner's clone, or a stand-in) and the guest."""

    lang: str
    preset: str
    narrator: dict[str, str]
    guest: dict[str, str]
    stand_in: bool = False
    reason: str = ""  # why the stand-in reads (which variable is unset)
    owner: dict[str, str] = field(default_factory=dict)  # the narrator voice the stand-in replaces

    def note(self) -> str:
        """One line for the video's metadata and the report."""
        if not self.stand_in:
            return f"narrator: owner's clone ({self.narrator['engine']} {self.narrator['voice']})"
        return (f"stand-in narrator: {self.narrator['engine']} {self.narrator['voice']} reads the narrator's lines "
                f"in place of the owner's clone ({self.reason})")


def _voice(entry: Mapping[str, str]) -> dict[str, str]:
    return {"engine": entry["engine"], "voice": entry["voice"]}


def resolve_cast(voices: Mapping[str, Any], cast: Mapping[str, str], lang: str, environ: Mapping[str, str],
                 allow_stand_in: bool) -> Cast:
    """Pick the narrator and guest voices of one language. Raises PipelineError with what to do."""
    name = cast["preset"]
    preset = voices["presets"].get(name)
    if preset is None:
        raise PipelineError(f"unknown casting preset {name!r} for {lang} (the voices config has "
                            f"{', '.join(sorted(voices['presets']))})")
    options = preset["guest"].get(lang)
    if not options:
        raise PipelineError(f"preset {name} has no {lang} guest voice")
    wanted = cast.get("guest")
    if wanted is None:
        guest = options[0]
    else:
        matches = [o for o in options if o["voice"] == wanted]
        if not matches:
            raise PipelineError(f"{lang} guest voice {wanted!r} is not in preset {name} (choose "
                                f"{', '.join(repr(o['voice']) for o in options)})")
        guest = matches[0]
    owner = voices["narrator"].get(lang)
    if owner is None:
        raise PipelineError(f"the voices config has no {lang} narrator")
    narrator, stand_in, reason = _voice(owner), False, ""
    env_name = owner.get("ref_env")
    if env_name:
        value = environ.get(env_name, "").strip()
        if value and not Path(value).expanduser().is_file():
            raise PipelineError(f"{env_name} names a file that does not exist ({Path(value).name}); fix the path")
        if not value:
            reason = f"{env_name} is not set"
            fallback = preset["stand_in"].get(lang)
            if not allow_stand_in:
                hint = f"pass --stand-in to read with {fallback['engine']} {fallback['voice']} in the narrator's place" \
                    if fallback else f"preset {name} has no {lang} stand-in voice"
                raise PipelineError(
                    f"{reason}: the {lang} narrator is the owner's clone ({owner['engine']} {owner['voice']}), which "
                    f"needs a reference clip. Record one and set {env_name}, or {hint}")
            if fallback is None:
                raise PipelineError(f"{reason} and preset {name} has no {lang} stand-in voice")
            narrator, stand_in = _voice(fallback), True
    if narrator["engine"] == "clone" and guest["engine"] == "vieneu":
        narrator = {"engine": "vieneu", "voice": narrator["voice"]}  # one VieNeu model reads both voices
    return Cast(lang=lang, preset=name, narrator=narrator, guest=_voice(guest), stand_in=stand_in, reason=reason,
                owner=_voice(owner))


# --- script and scenes -------------------------------------------------------------------------------------------------


def count_words(text: str) -> int:
    return len(re.findall(r"\w+(?:[-'’]\w+)*", text))


def estimate_seconds(doc: Mapping[str, Any], lang: str) -> float:
    words = sum(count_words(s["narration"][lang]) for s in doc["scenes"])
    return words * 60.0 / WORDS_PER_MINUTE


def long_lines(doc: Mapping[str, Any], lang: str) -> list[str]:
    """Narration lines too long for one subtitle cue (two rows of 42 characters).

    ``render`` splits such a line at the nearest pause, which can land a cue a
    clause early or late and fail the drift check; short sentences avoid it.
    """
    from .subtitles import LANDSCAPE_CHARS, pieces

    return [line for s in doc["scenes"] for line in split_lines(s["narration"][lang])
            if len(pieces(line, LANDSCAPE_CHARS)) > 1]


def script_markdown(doc: Mapping[str, Any], lang: str) -> str:
    """The narration of one language as a readable script (chapters, then who says what)."""
    out = [f"# {doc['title'][lang]}", ""]
    words = sum(count_words(s["narration"][lang]) for s in doc["scenes"])
    out += [f"{len(doc['scenes'])} scenes, {words} words (about {words / WORDS_PER_MINUTE:.1f} min at "
            f"{WORDS_PER_MINUTE} words per minute).", ""]
    for scene in doc["scenes"]:
        if "chapter" in scene:
            out += [f"## {scene['chapter'][lang]}", ""]
        who = scene.get("speaker", NARRATOR).capitalize()
        lines = " ".join(split_lines(scene["narration"][lang]))
        out += [f"**{who}** ({scene['id']}): {lines}", ""]
    return "\n".join(out).rstrip() + "\n"


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def referenced_files(doc: Mapping[str, Any]) -> list[str]:
    """Files a scenes.json names: visual sources and hand-made audio (narration audio is made by the voice step)."""
    found: list[str] = []
    for scene in doc["scenes"]:
        visual = scene["visual"]
        if visual["kind"] in FILE_KINDS and "src" in visual:
            found.append(visual["src"])
        for rel in scene.get("audio", {}).values():
            found.append(rel)
    return list(dict.fromkeys(found))


def _carry_audio(doc: dict[str, Any], old_file: Path, langs: Sequence[str]) -> int:
    """Keep the narration audio of an earlier run for scenes whose text did not change (``--from`` after voice)."""
    try:
        old = json.loads(old_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    before = {s.get("id"): s for s in old.get("scenes", []) if isinstance(s, dict)}
    carried = 0
    for scene in doc["scenes"]:
        prev = before.get(scene["id"])
        if not prev:
            continue
        for lang in langs:
            rel = prev.get("audio", {}).get(lang)
            same = prev.get("narration", {}).get(lang) == scene["narration"].get(lang)
            if rel and same and (old_file.parent / rel).is_file() and lang not in scene.get("audio", {}):
                scene.setdefault("audio", {})[lang] = rel
                carried += 1
    return carried


def cast_scenes(doc: dict[str, Any], casts: Sequence[Cast]) -> None:
    """Write the ``narrator`` and ``guest`` speakers of every cast language into ``doc`` (other speakers stay)."""
    speakers = doc.setdefault("speakers", {})
    for cast in casts:
        speakers.setdefault(NARRATOR, {})[cast.lang] = dict(cast.narrator)
        speakers.setdefault(GUEST, {})[cast.lang] = dict(cast.guest)
    doc.setdefault("voice_mix", {"policy": "narrator", "narrator": NARRATOR})


def prepare_job(topic: Topic, job: Path, casts: Sequence[Cast], langs: Sequence[str]) -> tuple[Path, list[str]]:
    """Copy the script, its files and the fact check into ``job``; cast the voices. Returns (scenes.json, notes)."""
    src_dir = topic.scenes.parent
    doc = json.loads(topic.scenes.read_text(encoding="utf-8"))
    notes: list[str] = []
    for rel in [*referenced_files(doc), *topic.assets]:
        src = src_dir / rel
        if not _inside(src, src_dir):
            raise PipelineError(f"{rel} lies outside the folder of {topic.scenes.name}; keep the files of a video "
                                "under its scenes file's folder so the job folder holds everything")
        if not src.is_file():
            raise PipelineError(f"{rel} (named by {topic.scenes.name}) not found")
        dst = job / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    cast_scenes(doc, casts)
    target = job / "scenes.json"
    if target.is_file():
        carried = _carry_audio(doc, target, langs)
        if carried:
            notes.append(f"kept the narration audio of {carried} unchanged scene line(s) from the last run")
    tmp = target.with_name(".scenes.json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    shutil.copy2(topic.factcheck, job / "factcheck.md")
    return target, notes


def planned_seconds(scenes: Scenes, lang: str, tail_s: float = 0.5) -> float:
    """The render's length from the narration audio and duration_s (clips that stretch a scene can only add)."""
    from .render import RenderOptions, plan, wav_length

    def length(path: Path) -> float:
        try:
            return wav_length(path)
        except Exception:  # not a plain WAV: ask ffprobe, as render does
            from .render import probe_duration

            return probe_duration(path)

    timeline = plan(scenes, lang, RenderOptions(tail_s=tail_s), audio_length=length)
    return sum(t.duration_s for t in timeline)


# --- the run -----------------------------------------------------------------------------------------------------------


@dataclass
class StepResult:
    step: str
    lang: str | None
    ok: bool
    seconds: float
    summary: str = ""
    command: list[str] | None = None
    exit_code: int | None = None


@dataclass
class Report:
    version: int
    topic: str
    job: str
    job_dir: str
    languages: list[str]
    cast: dict[str, dict[str, Any]]
    started: str
    steps: list[StepResult] = field(default_factory=list)
    finished: str = ""
    ok: bool = False
    failed_step: str | None = None
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n"


def default_runner(argv: Sequence[str], env: Mapping[str, str]) -> int:
    """Run one step command; its output goes straight to the terminal."""
    sys.stdout.flush()
    return subprocess.run(list(argv), env=dict(env), check=False).returncode


def default_tts_python(environ: Mapping[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    venv = environ.get("MOTION_STUDIO_TTS_VENV", "").strip()
    base = Path(venv).expanduser() if venv else Path.home() / ".cache" / "nghiep-motion-studio" / "tts-venv"
    return base / "bin" / "python"


def _src_root() -> Path:
    return Path(__file__).resolve().parents[1]


class Pipeline:
    """Run the steps of one topic. ``runner`` executes a step command (tests pass a fake)."""

    def __init__(
        self,
        topic: Topic,
        *,
        langs: Sequence[str] | None = None,
        stand_in: bool = False,
        tts_python: Path | None = None,
        python: str | None = None,
        runner: Runner = default_runner,
        environ: Mapping[str, str] | None = None,
        jobs: int = 1,
        from_step: str = "script",
        until_step: str = "shorts",
        out: Callable[[str], None] = print,
        err: Callable[[str], None] = lambda text: print(text, file=sys.stderr),
    ) -> None:
        if from_step not in STEPS or until_step not in STEPS:
            raise PipelineError(f"steps are {', '.join(STEPS)}")
        if STEPS.index(from_step) > STEPS.index(until_step):
            raise PipelineError(f"--from {from_step} comes after --until {until_step}")
        self.topic = topic
        self.environ = dict(os.environ if environ is None else environ)
        self.stand_in = stand_in or topic.stand_in
        self.tts_python = Path(tts_python) if tts_python else default_tts_python(self.environ)
        self.python = python or sys.executable
        self.runner = runner
        if jobs < 1:
            raise PipelineError("jobs must be 1 or more")
        self.jobs = jobs  # parallel scene encodes of render; 1 keeps memory low (each 1080p encode can take ~1.3 GB)
        self.from_step, self.until_step = from_step, until_step
        self.out, self.err = out, err
        self.requested_langs = list(langs) if langs else None
        self.voices = load_voices(topic.voices)

    # which steps run
    def selected(self, step: str) -> bool:
        i = STEPS.index(step)
        if i > STEPS.index(self.until_step):
            return False
        return step in SETUP_STEPS or i >= STEPS.index(self.from_step)

    def env(self) -> dict[str, str]:
        env = dict(self.environ)
        src = str(_src_root())
        env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return env

    def _langs(self, doc: Mapping[str, Any]) -> list[str]:
        have = doc["languages"]
        langs = self.requested_langs or self.topic.languages or list(have)
        unknown = [lang for lang in langs if lang not in have]
        if unknown:
            raise PipelineError(f"language(s) {', '.join(unknown)} not in {self.topic.scenes.name} (has {', '.join(have)})")
        if self.topic.languages and self.requested_langs:
            outside = [lang for lang in self.requested_langs if lang not in self.topic.languages]
            if outside:
                raise PipelineError(f"language(s) {', '.join(outside)} not in the topic file's languages")
        uncast = [lang for lang in langs if lang not in self.topic.cast]
        if uncast:
            raise PipelineError(f"the topic file casts no voices for {', '.join(uncast)}; add them to \"cast\"")
        return langs

    def plan(self) -> tuple[list[str], list[Cast]]:
        """Languages and casting, checked before anything runs."""
        try:
            doc = json.loads(self.topic.scenes.read_text(encoding="utf-8"))
            if not isinstance(doc.get("languages"), list) or not isinstance(doc.get("scenes"), list):
                raise ValueError("no languages or scenes list")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PipelineError(f"cannot read the script {self.topic.scenes}: {exc}") from exc
        langs = self._langs(doc)
        casts = [resolve_cast(self.voices, self.topic.cast[lang], lang, self.environ, self.stand_in) for lang in langs]
        return langs, casts

    def preflight(self, scenes_doc: Mapping[str, Any]) -> list[str]:
        """Tools the selected steps need; each problem is one line."""
        problems = []
        if (self.selected("voice") or self.selected("subtitles")) and not self.tts_python.is_file():
            problems.append(f"the TTS venv python {self.tts_python} is missing; run `just tts-setup` "
                            "(the voice and subtitles steps run there)")
        needs_ffmpeg = any(self.selected(s) for s in ("render", "gate", "shorts", "visuals"))
        if needs_ffmpeg and not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            problems.append("ffmpeg and ffprobe must be on PATH")
        kinds = {s["visual"]["kind"] for s in scenes_doc["scenes"]} & set(GENERATED_KINDS)
        if kinds and (self.selected("visuals") or self.selected("render")):
            for kind, why in missing_tools().items():
                if kind in kinds and why:
                    problems.append(f"visual kind {kind}: {why}")
        return problems

    # step commands
    def _module(self, python: Path | str, *args: str) -> list[str]:
        return [str(python), "-m", "motion_studio", *args]

    def video(self, job: Path, lang: str) -> Path:
        return job / f"{self.topic.job}.{lang}.mp4"

    def commands(self, step: str, job: Path, scenes_file: Path, lang: str, cast: Cast) -> list[str]:
        video = str(self.video(job, lang))
        if step == "voice":
            return self._module(self.tts_python, "narrate", str(scenes_file), "--lang", lang)
        if step == "visuals":
            return self._module(self.python, "visuals", str(scenes_file), "--lang", lang)
        if step == "render":
            return self._module(self.python, "render", str(scenes_file), "--lang", lang, "--jobs", str(self.jobs),
                                "--metadata", f"comment={cast.note()}")
        if step == "subtitles":
            return self._module(self.tts_python, "subtitles", video, "--check-drift")
        if step == "gate":
            cmd = self._module(self.python, "gate", video, "--scenes", str(scenes_file))
            if (self.topic.min_s, self.topic.max_s) != (MIN_DURATION_S, MAX_DURATION_S):
                cmd += ["--min-s", f"{self.topic.min_s:g}", "--max-s", f"{self.topic.max_s:g}"]
            return cmd
        if step == "shorts":
            return self._module(self.python, "shorts", video, "--scenes", str(scenes_file), "--count",
                                str(self.topic.shorts))
        raise PipelineError(f"step {step} has no command")

    def run(self, dry_run: bool = False) -> Report:
        langs, casts = self.plan()
        root = workdir.work_dir()
        job = workdir.job_dir(self.topic.job, root)
        report = Report(
            version=1, topic=str(self.topic.path.name), job=self.topic.job, job_dir=str(job), languages=langs,
            cast={c.lang: asdict(c) for c in casts}, started=_now(),
        )
        for cast in casts:
            line = f"{cast.lang} cast ({cast.preset}): narrator {cast.narrator['engine']} {cast.narrator['voice']}, " \
                   f"guest {cast.guest['engine']} {cast.guest['voice']}"
            if cast.stand_in:
                self.err(f"WARN {line}; STAND-IN narrator ({cast.reason})")
            else:
                self.out(f"NOTE {line}")
        if dry_run:
            return self._dry_run(report, job, langs, casts)
        with workdir.active_job(job):
            pruned = workdir.enforce_cap(root, workdir.cap_bytes(), job)
            for name, freed in pruned.pruned:
                self.out(f"NOTE work dir over its cap: removed the renders of {name} ({workdir.human(freed)})")
            self._run_steps(report, job, langs, casts)
        return report

    def _dry_run(self, report: Report, job: Path, langs: list[str], casts: list[Cast]) -> Report:
        scenes_file = job / "scenes.json"
        for step in STEPS:
            if not self.selected(step):
                continue
            if step in SETUP_STEPS:
                self.out(f"PLAN {step}: into {job}")
                continue
            for lang, cast in zip(langs, casts):
                self.out(f"PLAN {step} {lang}: {' '.join(self.commands(step, job, scenes_file, lang, cast))}")
        report.ok = True
        return report

    def _record(self, report: Report, job: Path, result: StepResult) -> None:
        report.steps.append(result)
        where = f" {result.lang}" if result.lang else ""
        line = f"{'OK  ' if result.ok else 'FAIL'} step {result.step}{where} ({result.seconds:.1f} s)"
        if result.summary:
            line += f": {result.summary}"
        (self.out if result.ok else self.err)(line)
        self._save(report, job)

    def _save(self, report: Report, job: Path) -> None:
        path = job / f"{self.topic.job}{REPORT_SUFFIX}"
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(report.json(), encoding="utf-8")
        os.replace(tmp, path)

    def _fail(self, report: Report, job: Path, step: str) -> Report:
        report.ok, report.failed_step, report.finished = False, step, _now()
        self._save(report, job)
        resume = f"--from {step}" if step not in SETUP_STEPS else ""
        self.err(f"FAIL stopped at step {step}; nothing after it ran. Fix the cause and run the same command again"
                 + (f" (add {resume} to skip the steps that passed)" if resume else ""))
        self.err(f"     report: {job / (self.topic.job + REPORT_SUFFIX)}")
        return report

    def _run_steps(self, report: Report, job: Path, langs: list[str], casts: list[Cast]) -> Report:
        # script
        started = time.monotonic()
        try:
            doc = json.loads(self.topic.scenes.read_text(encoding="utf-8"))
            # One script.md for every language: the cleanup keeps it (see workdir.KEEP_SCRIPTS).
            (job / "script.md").write_text("\n".join(script_markdown(doc, lang) for lang in langs), encoding="utf-8")
            estimate = ", ".join(f"{lang} about {estimate_seconds(doc, lang) / 60:.1f} min" for lang in langs)
            for lang in langs:
                too_long = long_lines(doc, lang)
                if too_long:
                    self.err(f"WARN {len(too_long)} {lang} narration line(s) need more than one subtitle cue, e.g. "
                             f"{too_long[0][:60]!r}; shorter sentences keep the subtitle drift check green")
            problems = self.preflight(doc)
            if problems:
                raise PipelineError("; ".join(problems))
            self._record(report, job, StepResult("script", None, True, time.monotonic() - started,
                                                 f"{len(doc['scenes'])} scenes, {estimate} at {WORDS_PER_MINUTE} wpm"))
        except (PipelineError, OSError, ValueError, KeyError, TypeError) as exc:
            self._record(report, job, StepResult("script", None, False, time.monotonic() - started, str(exc)))
            return self._fail(report, job, "script")
        # scenes
        started = time.monotonic()
        try:
            scenes_file, notes = prepare_job(self.topic, job, casts, langs)
            scenes = load(scenes_file)
            summary = f"{scenes_file} validated, " + "; ".join(c.note() for c in casts)
            if notes:
                summary += "; " + "; ".join(notes)
            if self.selected("gate"):
                fact = check_factcheck(scenes_file, Limits())
                if not fact.ok:
                    raise PipelineError(f"factcheck.md: {fact.summary}")
            self._record(report, job, StepResult("scenes", None, True, time.monotonic() - started, summary))
        except (PipelineError, ScenesError, OSError, ValueError) as exc:
            self._record(report, job, StepResult("scenes", None, False, time.monotonic() - started, str(exc)))
            return self._fail(report, job, "scenes")
        generated = any(s["visual"]["kind"] in GENERATED_KINDS for s in scenes.scenes)
        env = self.env()
        for step in STEPS[2:]:
            if not self.selected(step):
                continue
            if step == "visuals" and not generated:
                self._record(report, job, StepResult(step, None, True, 0.0, "no generated visuals"))
                continue
            for lang, cast in zip(langs, casts):
                started = time.monotonic()
                if step == "render":
                    problem = self._length_problem(scenes_file, lang)
                    if problem:
                        self._record(report, job, StepResult(step, lang, False, time.monotonic() - started, problem))
                        return self._fail(report, job, step)
                cmd = self.commands(step, job, scenes_file, lang, cast)
                self.out(f"==> {step} {lang}: {' '.join(cmd)}")
                code = self.runner(cmd, env)
                result = StepResult(step, lang, code == 0, time.monotonic() - started, "" if code == 0 else
                                    f"`{' '.join(cmd[1:4])} ...` exited {code}", cmd, code)
                self._record(report, job, result)
                if code != 0:
                    return self._fail(report, job, step)
        report.ok, report.finished = True, _now()
        report.outputs = self._outputs(job, langs)
        self._save(report, job)
        for lang in langs:
            outs = report.outputs[lang]
            self.out(f"OK   {lang}: {outs['video']}" + (f" ({outs['duration_s']:.1f} s)" if outs.get("duration_s") else ""))
            for short in outs.get("shorts", []):
                self.out(f"     short: {short}")
        self.out(f"OK   report: {job / (self.topic.job + REPORT_SUFFIX)}")
        return report

    def _length_problem(self, scenes_file: Path, lang: str) -> str | None:
        """Stop before a long render whose length the gate would refuse anyway (only when the gate will run)."""
        gate = self.selected("gate")
        try:
            scenes = load(scenes_file)
            if gate and not all(lang in s.get("audio", {}) for s in scenes.scenes):
                return f"some scenes have no {lang} narration audio; run the voice step (drop --from)"
            seconds = planned_seconds(scenes, lang)
        except (ScenesError, OSError, ValueError) as exc:
            return f"cannot plan the {lang} render: {exc}"
        if not gate:
            self.out(f"NOTE {lang} planned length {seconds:.1f} s (the gate does not run)")
            return None
        if seconds < self.topic.min_s:
            words = sum(count_words(s["narration"][lang]) for s in scenes.scenes)
            rate = words / seconds if seconds > 0 else WORDS_PER_MINUTE / 60  # this voice's own pace
            missing = (self.topic.min_s - seconds) * rate
            return (f"the narrated video would last {seconds:.1f} s, under the gate's {self.topic.min_s:g} s; "
                    f"lengthen the script (about {missing:.0f} more words) and update factcheck.md")
        if seconds > self.topic.max_s:
            return f"the narrated video would last {seconds:.1f} s, over the gate's {self.topic.max_s:g} s; shorten the script"
        self.out(f"NOTE {lang} planned length {seconds:.1f} s (gate window {self.topic.min_s:g}-{self.topic.max_s:g} s)")
        return None

    def _outputs(self, job: Path, langs: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for lang in langs:
            video = self.video(job, lang)
            entry: dict[str, Any] = {"video": str(video)}
            timeline = video.with_suffix(".timeline.json")
            try:
                entry["duration_s"] = round(float(json.loads(timeline.read_text(encoding="utf-8"))["duration_s"]), 3)
            except (OSError, ValueError, KeyError, TypeError):
                pass
            for key, suffix in (("srt", ".srt"), ("gate", ".gate.json")):
                path = video.with_suffix(suffix)
                if path.is_file():
                    entry[key] = str(path)
            shorts = sorted((job / workdir.SHORTS_DIR).glob(f"{self.topic.job}-*.{lang}.mp4"))
            entry["shorts"] = [str(p) for p in shorts]
            out[lang] = entry
        return out


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
