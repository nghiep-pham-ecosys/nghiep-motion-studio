"""Turn the narration in scenes.json into audio, one language at a time.

For each scene the narration is split into lines (sentences). Each line is
synthesised to its own WAV, then the lines are joined with a short pause into
one WAV per scene. The scene's ``audio`` entry is set to that file, and the
start and end of every line inside the scene are written to a timing file, so
later steps (subtitles, Shorts) know when each sentence is spoken.

Layout, next to scenes.json (``audio_dir`` defaults to ``audio/tts``)::

    audio/tts/<scene-id>.<lang>.wav               one file per scene (used by render)
    audio/tts/lines/<scene-id>-<n>-<key>.<lang>.wav one file per line
    audio/tts/timing.<lang>.json                  per-line timing

A line file's name carries a hash of everything that shapes its sound (text,
engine, voice, language and engine settings such as the clone reference), so
a line is synthesised again only when one of those changed, and a failed or
interrupted run can never leave a file that a later run mistakes for another
text. Scene files and the timing file are only replaced after every line of
every scene succeeded.

Each scene can have its own voice (``voice_mix.plan_voices``: the scene's
``voice``, its ``speaker``, the ``voice_mix`` policy, else the default). The
timing file records the engine, voice and speaker of every scene.
"""

from __future__ import annotations

import array
import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .schema import Scenes, ScenesError, from_dict
from .tts.base import TTSAdapter, TTSError, read_wav, write_wav
from .voice_mix import VoiceChoice

TIMING_VERSION = 1
DEFAULT_AUDIO_DIR = "audio/tts"
MIX = "mix"  # engine and voice of a timing file whose scenes use more than one voice
# A sentence ends at . ! ? … (optionally followed by closing quotes or brackets) before whitespace.
_SENTENCE_END = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s)")
# Words that end with a period but do not end a sentence (compared in lower case).
_ABBREVIATIONS = {
    "e.g.", "i.e.", "etc.", "vs.", "dr.", "mr.", "mrs.", "ms.", "prof.", "no.", "fig.", "st.", "approx.", "cf.",
    "tp.", "q.", "p.", "tx.", "v.v.", "ths.", "ts.", "pgs.", "gs.", "bs.", "ks.",
}
_ACRONYM = re.compile(r"(?:\w\.){2,}$")  # U.S., v.v.
_INITIAL = re.compile(r"^\w\.$")  # J. or A.
_LIST_NUMBER = re.compile(r"^\d+\.$")  # "1." at the start of a line


def _keeps_going(paragraph: str, start: int, match: re.Match[str]) -> bool:
    """True when the period at ``match`` belongs to an abbreviation, an initial or a list number."""
    if match.group(0) != ".":
        return False
    word = paragraph[:match.end()].split()[-1].lower()
    if word in _ABBREVIATIONS or _ACRONYM.search(word) or _INITIAL.match(word):
        return True
    return bool(_LIST_NUMBER.match(word)) and not paragraph[start:match.start()].strip(" 0123456789")


def split_lines(text: str) -> list[str]:
    """Split narration into lines: one per paragraph line and sentence, blanks dropped."""
    lines: list[str] = []
    for paragraph in text.splitlines():
        paragraph = paragraph.strip()
        start = 0
        for match in _SENTENCE_END.finditer(paragraph):
            if _keeps_going(paragraph, start, match):
                continue
            lines.append(paragraph[start:match.end()])
            start = match.end()
        lines.append(paragraph[start:])
    lines = [" ".join(line.split()) for line in lines]
    merged: list[str] = []
    for line in lines:
        if not line:
            continue
        if not re.search(r"\w", line) and merged:  # stray punctuation such as ". ." joins the line before
            merged[-1] = f"{merged[-1]} {line}"
        else:
            merged.append(line)
    if len(merged) > 1 and not re.search(r"\w", merged[0]):
        merged[1] = f"{merged[0]} {merged[1]}"
        merged.pop(0)
    return merged


@dataclass
class LineTiming:
    index: int
    text: str
    start_s: float
    end_s: float
    file: str


@dataclass
class SceneNarration:
    id: str
    file: str
    duration_s: float
    lines: list[LineTiming] = field(default_factory=list)
    engine: str = ""
    voice: str = ""
    speaker: str | None = None


@dataclass
class NarrationResult:
    lang: str
    engine: str
    voice: str
    sample_rate: int
    pause_s: float
    audio_dir: str
    scenes: list[SceneNarration]
    synthesized: int  # lines made this run
    reused: int  # lines taken from an earlier run
    seconds: float  # wall time spent in the engine

    def timing_json(self) -> str:
        data = {
            "version": TIMING_VERSION,
            "lang": self.lang,
            "engine": self.engine,
            "voice": self.voice,
            "sample_rate": self.sample_rate,
            "pause_s": self.pause_s,
            "scenes": [asdict(s) for s in self.scenes],
        }
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def timing_path(base_dir: Path, lang: str, audio_dir: str = DEFAULT_AUDIO_DIR) -> Path:
    return Path(base_dir) / audio_dir / f"timing.{lang}.json"


def _line_key(engine: str, tag: str, voice: str, lang: str, text: str) -> str:
    payload = json.dumps([engine, tag, voice, lang, text], ensure_ascii=False).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _check_audio_dir(audio_dir: str) -> None:
    parts = Path(audio_dir).parts
    if not parts or Path(audio_dir).is_absolute() or ".." in parts:
        raise ValueError("audio_dir must be a relative folder inside the scenes folder")


def resample(samples: array.array, src: int, dst: int) -> array.array:
    """Linear resampling of 16-bit samples (used only to upsample a line to the highest rate of a language)."""
    if src == dst or not samples:
        return array.array("h", samples)
    n = max(1, int(round(len(samples) * dst / src)))
    step = (len(samples) - 1) / max(1, n - 1)
    out = array.array("h", bytes(2 * n))
    last = len(samples) - 1
    for i in range(n):
        x = i * step
        j = int(x)
        frac = x - j
        a = samples[j]
        b = samples[j + 1] if j < last else a
        out[i] = int(round(a + (b - a) * frac))
    return out


def narrate(
    scenes: Scenes,
    lang: str,
    adapter: TTSAdapter | Mapping[str, TTSAdapter],
    voice: str | None = None,
    *,
    choices: Sequence[VoiceChoice] | None = None,
    audio_dir: str = DEFAULT_AUDIO_DIR,
    pause_s: float = 0.3,
    force: bool = False,
) -> NarrationResult:
    """Synthesise every scene's ``lang`` narration, write the scene files and the timing file.

    One voice: pass an adapter and ``voice``. A voice mix: pass ``choices`` (one
    VoiceChoice per scene, see ``voice_mix.plan_voices``) and the adapters by
    engine name. When engines differ in sample rate, lines are upsampled to the
    highest rate of the language.

    ``scenes.doc`` is updated in memory to point at the new scene files; save it
    with ``save_scenes``. Nothing is replaced on disk until every line of every
    scene has been synthesised.
    """
    if lang not in scenes.languages:
        raise ValueError(f"language {lang!r} is not in this file's languages {scenes.languages}")
    if not 0 <= pause_s <= 5:
        raise ValueError("pause_s must be between 0 and 5 seconds")
    _check_audio_dir(audio_dir)
    adapters: dict[str, TTSAdapter] = (
        {adapter.engine: adapter} if isinstance(adapter, TTSAdapter) else dict(adapter)
    )
    if choices is None:
        if voice is None or not isinstance(adapter, TTSAdapter):
            raise ValueError("pass one adapter and a voice, or choices (one per scene) with adapters by engine")
        choices = [VoiceChoice(adapter.engine, voice)] * len(scenes.scenes)
    choices = list(choices)
    if len(choices) != len(scenes.scenes):
        raise ValueError(f"got {len(choices)} voice choice(s) for {len(scenes.scenes)} scene(s)")
    missing = sorted({c.engine for c in choices} - set(adapters))
    if missing:
        raise ValueError(f"no adapter for engine(s) {', '.join(missing)}")
    base = scenes.base_dir
    lines_dir = base / audio_dir / "lines"
    tags = {engine: a.cache_tag() for engine, a in adapters.items()}

    synthesized = reused = 0
    engine_seconds = 0.0
    used: set[str] = set()
    voiced: list[tuple[dict[str, Any], VoiceChoice, list[tuple[str, str, array.array, int]]]] = []
    for scene, choice in zip(scenes.scenes, choices):
        sid = scene["id"]
        tts = adapters[choice.engine]
        texts = split_lines(scene["narration"][lang])
        if not texts:
            raise ValueError(f"scene {sid!r}: no {lang} narration to speak")
        clips: list[tuple[str, str, array.array, int]] = []
        for n, text in enumerate(texts):
            key = _line_key(tts.engine, tags[choice.engine], choice.voice, lang, text)
            rel = f"{audio_dir}/lines/{sid}-{n:03d}-{key}.{lang}.wav"
            path = base / rel
            if path.is_file() and not force:
                reused += 1
            else:
                started = time.perf_counter()
                tts.synthesize(text, choice.voice, lang, path)  # atomic write: never a half file under this name
                engine_seconds += time.perf_counter() - started
                synthesized += 1
            used.add(path.name)
            samples, line_rate = read_wav(path)
            clips.append((text, rel, samples, line_rate))
        voiced.append((scene, choice, clips))

    rate = max(r for _, _, clips in voiced for *_, r in clips)
    gap = array.array("h", bytes(2 * int(round(pause_s * rate))))
    pending: list[tuple[dict[str, Any], VoiceChoice, str, array.array, list[LineTiming]]] = []
    for scene, choice, clips in voiced:
        joined = array.array("h")
        timings: list[LineTiming] = []
        for n, (text, rel, samples, line_rate) in enumerate(clips):
            if n:
                joined.extend(gap)
            start = len(joined)
            joined.extend(resample(samples, line_rate, rate))
            timings.append(LineTiming(index=n, text=text, start_s=start / rate, end_s=len(joined) / rate, file=rel))
        pending.append((scene, choice, f"{audio_dir}/{scene['id']}.{lang}.wav", joined, timings))

    # Commit: drop the old timing first, so a crash below leaves no timing that disagrees with the audio.
    tpath = timing_path(base, lang, audio_dir)
    tpath.unlink(missing_ok=True)
    results: list[SceneNarration] = []
    for scene, choice, scene_rel, joined, timings in pending:
        write_wav(base / scene_rel, joined, rate)
        results.append(SceneNarration(
            id=scene["id"], file=scene_rel, duration_s=len(joined) / rate, lines=timings,
            engine=choice.engine, voice=choice.voice, speaker=choice.speaker,
        ))
        scene.setdefault("audio", {})[lang] = scene_rel
    engines = {c.engine for c in choices}
    voice_names = {c.voice for c in choices}
    engine = engines.pop() if len(engines) == 1 else MIX
    voice_name = voice_names.pop() if len(voice_names) == 1 and engine != MIX else MIX
    result = NarrationResult(
        lang=lang, engine=engine, voice=voice_name, sample_rate=rate, pause_s=pause_s, audio_dir=audio_dir,
        scenes=results, synthesized=synthesized, reused=reused, seconds=engine_seconds,
    )
    _write_text_atomic(tpath, result.timing_json())
    # Remove line files of this language that no scene uses any more.
    for old in lines_dir.glob(f"*.{lang}.wav"):
        if old.name not in used:
            old.unlink(missing_ok=True)
    return result


def save_scenes(scenes: Scenes, path: Path, lang: str) -> Scenes:
    """Write the new ``audio[lang]`` entries into scenes.json without losing other changes.

    The file is read again from disk and only ``audio[lang]`` is copied over, so
    edits made while the engine was running (and other languages' entries) are
    kept. If a scene was removed or its ``lang`` narration changed meanwhile, the
    narration is stale and nothing is written.
    """
    path = Path(path)
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScenesError([f"cannot re-read before saving: {exc}"], str(path)) from exc
    ours = {s["id"]: s for s in scenes.doc["scenes"]}
    problems = []
    for scene in current.get("scenes", []):
        sid = scene.get("id")
        if sid not in ours:
            continue
        if scene.get("narration", {}).get(lang) != ours[sid]["narration"][lang]:
            problems.append(f"scene {sid!r}: {lang} narration changed while narrating; run narrate again")
            continue
        scene.setdefault("audio", {})[lang] = ours[sid]["audio"][lang]
    missing = set(ours) - {s.get("id") for s in current.get("scenes", [])}
    problems += [f"scene {sid!r} was removed while narrating; run narrate again" for sid in sorted(missing)]
    if problems:
        raise ScenesError(problems, str(path))
    checked = from_dict(current, scenes.base_dir, source=str(path))
    _write_text_atomic(path, json.dumps(checked.doc, indent=2, ensure_ascii=False) + "\n")
    return checked


class StaleTiming(ValueError):
    """The timing file no longer matches the scenes or their audio."""


def check_timing(timing: dict[str, Any], scenes: Scenes, lang: str) -> None:
    """Raise StaleTiming unless ``timing`` describes exactly the audio ``scenes`` points at now."""
    by_id = {s["id"]: s for s in timing.get("scenes", [])}
    voiced = [s for s in scenes.scenes if lang in s.get("audio", {})]
    if [s["id"] for s in voiced] != [s["id"] for s in timing.get("scenes", [])]:
        raise StaleTiming("the scenes with audio differ from the scenes in the timing file")
    for scene in voiced:
        entry = by_id[scene["id"]]
        if scene["audio"][lang] != entry.get("file"):
            raise StaleTiming(f"scene {scene['id']!r} uses {scene['audio'][lang]}, the timing file {entry.get('file')}")
        if [ln["text"] for ln in entry.get("lines", [])] != split_lines(scene["narration"][lang]):
            raise StaleTiming(f"scene {scene['id']!r}: the narration changed after it was voiced")
        try:
            data, rate = read_wav(scenes.resolve(scene["audio"][lang]))
        except (OSError, TTSError, EOFError) as exc:
            raise StaleTiming(f"scene {scene['id']!r}: cannot read its audio ({exc})") from exc
        if abs(len(data) / rate - float(entry.get("duration_s", -1))) > 0.002:
            raise StaleTiming(f"scene {scene['id']!r}: its audio length differs from the timing file")


def line_timeline(timing: dict[str, Any], render_timeline: dict[str, Any]) -> list[dict[str, Any]]:
    """Place every narration line on the video clock: scene start (from render) + line offset."""
    starts = {s["id"]: s["start_s"] for s in render_timeline["scenes"]}
    out = []
    for scene in timing["scenes"]:
        if scene["id"] not in starts:
            raise StaleTiming(f"scene {scene['id']!r} is in the timing file but not in the render timeline")
        t0 = starts[scene["id"]]
        for line in scene["lines"]:
            out.append({"scene": scene["id"], "text": line["text"],
                        "start_s": round(t0 + line["start_s"], 6), "end_s": round(t0 + line["end_s"], 6)})
    return out
