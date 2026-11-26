"""Subtitles: SRT from the narration timing, optional burn-in, and a faster-whisper drift check.

Timing path (no model). ``narrate`` records when every line starts and ends
inside its scene, and ``render`` places those lines on the video clock in
``<name>.<lang>.lines.json``. A cue is that line, or a piece of it when the
line is too long for two subtitle rows. TTS clips carry some silence around
the words, so the rendered audio is read (ffmpeg, 10 ms frames) and each line
is trimmed to where speech starts and ends; a long line is split at the
pause nearest its proportional split point. A short cue is held a little
longer (up to ``min_s``) but never into the next cue.

Whisper path (optional, ``pip install -e .[subtitles]``). faster-whisper
transcribes the rendered audio with word timestamps. The narration words are
matched to the recognised words (difflib on normalised words), which gives:

- a drift report: for each cue, recognised start of its first word minus the
  cue start (the cue end is not compared: cues are held on purpose);
- an alignment when no timing file exists: each narration line starts at its
  first matched word and ends at its last one.

Burn-in renders the SRT into the picture with ffmpeg's libass ``subtitles``
filter (for Shorts and for players that do not load SRT files).
"""

from __future__ import annotations

import difflib
import json
import math
import re
import shutil
import statistics
import subprocess
import tempfile
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .theme import Theme, ass_colour, get_theme

MAX_DRIFT_S = 0.2  # the quality gate (see DriftReport)
MAX_P90_S = 0.35  # a looser limit on the 90th percentile: catches a misplaced scene, tolerates single-word noise
DEFAULT_MODEL = "base"  # the smallest model that met MAX_DRIFT_S on the sample (docs/spec.md, "Subtitles")
LANDSCAPE_CHARS = 42  # characters per row; two rows per cue
PORTRAIT_CHARS = 20
MIN_CUE_S = 1.0
DRIFT_WINDOW_S = 60.0  # cues matched together (see local_matches)
DRIFT_MARGIN_S = 10.0  # how far from its cues a recognised word may lie and still match
RECHECK_S = 1.0  # a first word matched further than this from its cue is looked up again near the cue (anchor)
ANCHOR_REACH_S = 3.0  # how far from the cue start that lookup searches; further off counts as unmatched
CUE_GAP_S = 0.04


class SubtitleError(RuntimeError):
    """Subtitles could not be made (no timing, a tool failed, or the model is missing)."""


@dataclass(frozen=True)
class Cue:
    start_s: float
    end_s: float
    text: str  # rows joined with "\n"


@dataclass(frozen=True)
class Word:
    start_s: float
    end_s: float
    text: str


# --- text layout -------------------------------------------------------------------------------------------------


def wrap(text: str, width: int) -> str:
    """Put ``text`` on one row, or on two rows broken at the space nearest the middle."""
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    spaces = [i for i, c in enumerate(text) if c == " "]
    if not spaces:
        return text
    fits = [i for i in spaces if i <= width and len(text) - i - 1 <= width]
    best = min(fits or spaces, key=lambda i: abs(len(text) / 2 - i))
    return text[:best] + "\n" + text[best + 1 :]


def pieces(text: str, width: int, rows: int = 2) -> list[str]:
    """Split a line into the fewest pieces that each fit ``rows`` rows of ``width`` characters.

    Pieces break between words, preferring a break after punctuation, and are
    about the same length.
    """
    words = text.split()
    if not words:
        return []
    cap = width * rows
    n = max(1, math.ceil(len(" ".join(words)) / cap))
    while True:
        out = _balanced(words, n)
        fits = all(" " not in p or all(len(row) <= width for row in wrap(p, width).split("\n")) for p in out)
        if (fits and rows >= 2) or (rows < 2 and all(len(p) <= cap or " " not in p for p in out)) or n >= len(words):
            return out
        n += 1


def _balanced(words: list[str], n: int) -> list[str]:
    """Greedy split into at most ``n`` pieces of about equal length; an early break after punctuation is allowed."""
    target = len(" ".join(words)) / n
    out: list[str] = []
    current: list[str] = []
    for i, word in enumerate(words):
        current.append(word)
        if len(out) == n - 1 or i == len(words) - 1:
            continue
        length = len(" ".join(current))
        longer = length + 1 + len(words[i + 1])
        if (word[-1] in ",;:" and length >= 0.6 * target) or abs(length - target) <= abs(longer - target):
            out.append(" ".join(current))
            current = []
    if current:
        out.append(" ".join(current))
    return out


# --- speech activity (stdlib + ffmpeg) -------------------------------------------------------------------------------

FRAME_S = 0.01
SILENCE_DB = 35.0  # a frame this far below the loudest frame counts as silence
MIN_PAUSE_S = 0.12


@dataclass(frozen=True)
class Activity:
    """Which 10 ms frames of a track hold speech."""

    voiced: list[bool]
    frame_s: float = FRAME_S

    def span(self, start_s: float, end_s: float) -> tuple[float, float] | None:
        """First and last voiced instant inside [start_s, end_s], or None."""
        a, b = max(0, int(start_s / self.frame_s)), min(len(self.voiced), math.ceil(end_s / self.frame_s))
        hits = [i for i in range(a, b) if self.voiced[i]]
        if not hits:
            return None
        return round(hits[0] * self.frame_s, 3), round((hits[-1] + 1) * self.frame_s, 3)

    def pauses(self, start_s: float, end_s: float, min_s: float = MIN_PAUSE_S) -> list[tuple[float, float]]:
        """Silent runs of at least ``min_s`` strictly inside [start_s, end_s]."""
        a, b = max(0, int(start_s / self.frame_s)), min(len(self.voiced), math.ceil(end_s / self.frame_s))
        out, run = [], None
        for i in range(a, b):
            if not self.voiced[i]:
                run = i if run is None else run
            elif run is not None:
                if run > a and (i - run) * self.frame_s >= min_s:
                    out.append((round(run * self.frame_s, 3), round(i * self.frame_s, 3)))
                run = None
        return out


def activity(media: Path, ffmpeg: str = "ffmpeg", rate: int = 16000) -> Activity:
    """Decode the first audio track with ffmpeg and mark the voiced 10 ms frames."""
    import array

    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(media), "-map", "0:a:0", "-ac", "1",
         "-ar", str(rate), "-f", "s16le", "-"],
        capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise SubtitleError(f"cannot decode the audio of {media}: {proc.stderr.decode(errors='replace').strip()[-400:]}")
    pcm = array.array("h")
    pcm.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    n = int(rate * FRAME_S)
    energy = []
    for i in range(0, len(pcm) - n + 1, n):
        chunk = pcm[i : i + n]
        energy.append(sum(x * x for x in chunk) / n)
    peak = max(energy, default=0.0)
    floor = max(peak * 10 ** (-SILENCE_DB / 10), 4.0)  # energy ratio; 4.0 ~ sample amplitude 2
    return Activity([e > floor for e in energy])


def snap_to_speech(words: list["Word"], act: Activity, min_voiced: float = 0.5) -> list["Word"]:
    """Move a recognised word that lies mostly in silence to the speech onset after it.

    After a pause, faster-whisper (with its VAD) often starts the next word
    where the speech before the pause ended, so the word "starts" in the
    silence, up to the length of the pause too early. A word with less than
    ``min_voiced`` of its frames voiced moves to the next voiced frame, when
    that comes before the next recognised word. Other words are left as they are.
    """
    out: list[Word] = []
    n = len(act.voiced)
    for i, w in enumerate(words):
        a = max(0, int(w.start_s / act.frame_s))
        b = min(n, max(a + 1, math.ceil(w.end_s / act.frame_s)))
        frames = act.voiced[a:b]
        if not frames or sum(frames) / len(frames) >= min_voiced:
            out.append(w)
            continue
        k = b
        while k < n and not act.voiced[k]:
            k += 1
        onset = round(k * act.frame_s, 3)
        limit = words[i + 1].start_s if i + 1 < len(words) else w.end_s + 2.0
        out.append(Word(onset, max(onset, w.end_s), w.text) if k < n and onset <= limit + 0.05 else w)
    return out


def trim_lines(lines: list[dict[str, Any]], act: Activity) -> list[dict[str, Any]]:
    """Move each line's start and end to where speech starts and ends inside it."""
    out = []
    for line in lines:
        span = act.span(float(line["start_s"]), float(line["end_s"]))
        entry = dict(line)
        if span is not None:
            entry["start_s"], entry["end_s"] = round(span[0], 3), round(span[1], 3)
        out.append(entry)
    return out


# --- cues ----------------------------------------------------------------------------------------------------------


def cues_from_lines(
    lines: Iterable[dict[str, Any]],
    width: int = LANDSCAPE_CHARS,
    min_s: float = MIN_CUE_S,
    gap_s: float = CUE_GAP_S,
    act: Activity | None = None,
) -> list[Cue]:
    """Turn ``[{"text", "start_s", "end_s"}, ...]`` (video clock) into cues, in time order.

    With ``act`` (the speech activity of the rendered audio), lines are
    trimmed to their speech and long lines are split at real pauses.
    """
    lines = list(lines)
    if act is not None:
        lines = trim_lines(lines, act)
    raw: list[tuple[float, float, str]] = []
    for line in sorted(lines, key=lambda ln: (float(ln["start_s"]), float(ln["end_s"]))):
        start, end = float(line["start_s"]), float(line["end_s"])
        if not (math.isfinite(start) and math.isfinite(end)) or end <= start:
            continue
        parts = pieces(str(line["text"]), width)
        if not parts:
            continue
        raw += _place(parts, start, end, act.pauses(start, end) if act is not None else [])
    cues: list[Cue] = []
    for i, (start, end, text) in enumerate(raw):
        limit = raw[i + 1][0] - gap_s if i + 1 < len(raw) else math.inf
        held = min(max(end, start + min_s), max(limit, end))
        cues.append(Cue(round(start, 3), round(held, 3), wrap(text, width)))
    return cues


def _place(parts: list[str], start: float, end: float, pauses: list[tuple[float, float]]) -> list[tuple[float, float, str]]:
    """Share [start, end] between pieces by length; move each split to the nearest pause when one is close.

    Splits are placed one after another, each over the time still left, so
    every piece keeps a positive length.
    """
    weights = [max(len(p), 1) for p in parts]
    out: list[tuple[float, float, str]] = []
    t = start
    for k, part in enumerate(parts[:-1]):
        left = sum(weights[k:])
        target = t + (end - t) * weights[k] / left
        reach = max(0.3, 0.5 * (target - t))  # half a piece: never jump to a pause one piece away
        room = 0.1 * (len(parts) - k - 1)  # keep some time for the pieces after this one
        near = [p for p in pauses if p[0] > t + 0.1 and p[1] < end - room and abs((p[0] + p[1]) / 2 - target) <= reach]
        if near:
            pause = min(near, key=lambda p: abs((p[0] + p[1]) / 2 - target))
            out.append((t, pause[0], part))
            t = pause[1]
        else:
            out.append((t, target, part))
            t = target
    out.append((t, end, parts[-1]))
    return out


def width_for(width_px: int, height_px: int) -> int:
    return PORTRAIT_CHARS if height_px > width_px else LANDSCAPE_CHARS


# --- SRT -------------------------------------------------------------------------------------------------------------


def srt_time(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: Iterable[Cue]) -> str:
    blocks = [f"{i}\n{srt_time(c.start_s)} --> {srt_time(c.end_s)}\n{c.text}\n" for i, c in enumerate(cues, 1)]
    return "\n".join(blocks)


_SRT_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{3})")


def parse_srt(text: str) -> list[Cue]:
    cues = []
    for block in re.split(r"\n\s*\n", text.strip().replace("\r\n", "\n")):
        rows = block.splitlines()
        if len(rows) < 2 or "-->" not in rows[1]:
            continue
        a, b = (_SRT_TIME.search(part) for part in rows[1].split("-->"))
        if not a or not b:
            continue
        to_s = lambda m: int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000  # noqa: E731
        cues.append(Cue(to_s(a), to_s(b), "\n".join(rows[2:])))
    return cues


# --- words, drift and alignment -----------------------------------------------------------------------------------


def normalise(word: str) -> str:
    """Lower case, NFC, letters and digits only (keeps Vietnamese diacritics)."""
    word = unicodedata.normalize("NFC", word).lower()
    return "".join(c for c in word if c.isalnum())


def _tokens(texts: list[str]) -> tuple[list[str], list[int], list[int]]:
    """Normalised words of every text, the index of each word's text, and its character offset in that text."""
    toks, owner, offset = [], [], []
    for i, text in enumerate(texts):
        pos = 0
        for w in text.split():
            n = normalise(w)
            if n:
                toks.append(n)
                owner.append(i)
                offset.append(pos)
            pos += len(w) + 1
    return toks, owner, offset


def line_matches(texts: list[str], words: list[Word]) -> list[list[tuple[int, int]]]:
    """For each text, its matched words as (character offset in the text, index in ``words``), in order."""
    toks, owner, offset = _tokens(texts)
    heard = [normalise(w.text) for w in words]
    keep = [i for i, h in enumerate(heard) if h]
    matcher = difflib.SequenceMatcher(a=toks, b=[heard[i] for i in keep], autojunk=False)
    out: list[list[tuple[int, int]]] = [[] for _ in texts]
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            t = block.a + k
            out[owner[t]].append((offset[t], keep[block.b + k]))
    for row in out:
        row.sort()
    return out


def match_words(texts: list[str], words: list[Word]) -> list[tuple[int | None, int | None]]:
    """For each text: the index in ``words`` of its first word and of its last word, when those words matched."""
    toks, owner, offset = _tokens(texts)
    firsts = {owner[t]: offset[t] for t in range(len(toks)) if t == 0 or owner[t - 1] != owner[t]}
    lasts = {owner[t]: offset[t] for t in range(len(toks)) if t == len(toks) - 1 or owner[t + 1] != owner[t]}
    out: list[tuple[int | None, int | None]] = []
    for i, row in enumerate(line_matches(texts, words)):
        by_offset = dict(row)
        out.append((by_offset.get(firsts.get(i, -1)), by_offset.get(lasts.get(i, -1))))
    return out


@dataclass
class DriftReport:
    """How far cue starts sit from the recognised word starts.

    Drift is the systematic offset, not the noise of one word: Whisper's word
    starts are themselves off by up to about 300 ms on single words (a breath
    before a word, an acronym), so the gate uses the median offset, the mean
    absolute offset and the change between the first and second half of the
    video, plus a looser limit on the 90th percentile so one scene that is
    consistently off still fails. The worst single cues are listed so a
    person can check them.
    """

    lang: str
    model: str
    cues: int
    matched: int
    median_s: float  # signed: positive = speech starts after the cue
    mean_abs_s: float
    p90_abs_s: float
    max_abs_s: float
    trend_s: float  # mean signed offset of the second half minus the first half
    limit_s: float = MAX_DRIFT_S
    p90_limit_s: float = MAX_P90_S
    worst: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(
            self.matched >= max(1, (self.cues + 1) // 2)
            and abs(self.median_s) <= self.limit_s
            and self.mean_abs_s <= self.limit_s
            and abs(self.trend_s) <= self.limit_s
            and self.p90_abs_s <= self.p90_limit_s
        )

    def summary(self) -> str:
        ms = lambda v: f"{v * 1000:.0f} ms"  # noqa: E731
        return (
            f"drift {self.lang} ({self.model}): median {ms(self.median_s)}, mean |d| {ms(self.mean_abs_s)}, "
            f"p90 {ms(self.p90_abs_s)}, max {ms(self.max_abs_s)}, trend {ms(self.trend_s)} over "
            f"{self.matched}/{self.cues} cue(s) (limits {ms(self.limit_s)}, p90 {ms(self.p90_limit_s)})"
        )

    def json(self) -> str:
        return json.dumps({**asdict(self), "ok": self.ok}, indent=2, ensure_ascii=False) + "\n"


def local_matches(cues: list[Cue], words: list[Word], window_s: float = DRIFT_WINDOW_S,
                  margin_s: float = DRIFT_MARGIN_S) -> list[tuple[int | None, int | None]]:
    """``match_words`` per stretch of ``window_s`` of cues, against the words heard within ``margin_s`` of it.

    A video that says the same sentences several times (a lesson: listen, listen again, repeat after me) makes
    one global match pair a cue with the same words minutes away. Matching near the cue's own time avoids that;
    a cue whose speech is further off than ``margin_s`` finds no words and counts as unmatched, so a large
    offset still fails the gate (at least half the cues must match).
    """
    out: list[tuple[int | None, int | None]] = [(None, None)] * len(cues)
    order = sorted(range(len(cues)), key=lambda k: cues[k].start_s)
    i = 0
    while i < len(order):
        t0 = cues[order[i]].start_s
        j = i
        while j < len(order) and cues[order[j]].start_s < t0 + window_s:
            j += 1
        group = order[i:j]
        lo, hi = t0 - margin_s, max(cues[k].end_s for k in group) + margin_s
        near = [k for k, w in enumerate(words) if lo <= w.start_s <= hi]
        found = match_words([cues[k].text.replace("\n", " ") for k in group], [words[k] for k in near])
        for k, (first, last) in zip(group, found):
            out[k] = (near[first] if first is not None else None, near[last] if last is not None else None)
        i = j
    return out


def anchor(cue: Cue, words: list[Word], reach_s: float = ANCHOR_REACH_S) -> int | None:
    """The recognised word within ``reach_s`` of the cue start that best starts the cue's text, or None.

    A candidate equals the cue's first word; it scores one more for each of the next three words that also
    match, and the closest of the best wins. Used when the sequence match put a cue's first word on the same
    word said elsewhere ("It", "and" or a repeated sentence a few seconds away).
    """
    toks = [t for t in (normalise(w) for w in cue.text.split()) if t]
    if not toks:
        return None
    heard = [normalise(w.text) for w in words]
    best: tuple[tuple[int, float], int] | None = None
    for k, w in enumerate(words):
        if abs(w.start_s - cue.start_s) > reach_s or heard[k] != toks[0]:
            continue
        following = [h for h in heard[k + 1:k + 8] if h][:3]
        score = 1 + sum(1 for a, b in zip(toks[1:4], following) if a == b)
        key = (score, -abs(w.start_s - cue.start_s))
        if best is None or key > best[0]:
            best = (key, k)
    return best[1] if best else None


def drift(cues: list[Cue], words: list[Word], lang: str = "", model: str = "") -> DriftReport:
    """Compare each cue start with the recognised start of its first word (matched near the cue, see local_matches
    and anchor)."""
    pairs = []
    for cue, (first, last) in zip(cues, local_matches(cues, words)):
        if first is None or abs(words[first].start_s - cue.start_s) > RECHECK_S:
            first = anchor(cue, words)
        pairs.append((first, last))
    rows = []
    for cue, (first, _) in zip(cues, pairs):
        if first is not None:
            rows.append({"start_s": cue.start_s, "text": cue.text.replace("\n", " "),
                         "drift_s": round(float(words[first].start_s) - cue.start_s, 3)})
    signed = [r["drift_s"] for r in rows]
    absd = sorted(abs(d) for d in signed)
    half = len(signed) // 2
    trend = statistics.fmean(signed[half:]) - statistics.fmean(signed[:half]) if half else 0.0
    pick = lambda q: absd[min(len(absd) - 1, math.ceil(q * len(absd)) - 1)] if absd else 0.0  # noqa: E731
    return DriftReport(
        lang=lang, model=model, cues=len(cues), matched=len(rows),
        median_s=round(statistics.median(signed), 3) if signed else 0.0,
        mean_abs_s=round(statistics.fmean(absd), 3) if absd else 0.0,
        p90_abs_s=pick(0.9), max_abs_s=absd[-1] if absd else 0.0, trend_s=round(trend, 3),
        worst=sorted(rows, key=lambda r: -abs(r["drift_s"]))[:5],
    )


def align_lines(texts: list[str], words: list[Word], scene_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Time each narration line from the recognised words.

    A line starts at its first matched word, moved back by the characters
    before that word at the line's own speaking rate (and ends the same way),
    never before the previous line ends. A line with no match fills the gap
    between its neighbours. ``write_srt`` then trims every line to the speech
    in the audio, which removes most of the estimate's error.
    """
    rows = line_matches(texts, words)
    spans: list[tuple[float, float] | None] = []
    for text, row in zip(texts, rows):
        if not row:
            spans.append(None)
            continue
        (c0, w0), (c1, w1) = row[0], row[-1]
        t0, t1 = float(words[w0].start_s), float(words[w1].end_s)
        chars_after = max(len(text) - c1 - len(text[c1:].split(" ", 1)[0]), 0)
        rate = (t1 - t0) / max(c1 + len(text[c1:].split(" ", 1)[0]) - c0, 1)  # seconds per character
        rate = min(max(rate, 0.03), 0.15)
        spans.append((t0 - c0 * rate, t1 + chars_after * rate))
    if not any(spans):
        raise SubtitleError("the recognised speech matches none of the narration; check the language")
    # A run of unmatched lines shares the gap between its matched neighbours by length.
    i = 0
    while i < len(texts):
        if spans[i] is not None:
            i += 1
            continue
        j = i
        while j < len(texts) and spans[j] is None:
            j += 1
        lo = spans[i - 1][1] + CUE_GAP_S if i else 0.0  # type: ignore[index]
        hi = spans[j][0] - CUE_GAP_S if j < len(texts) else lo + 2.0 * (j - i)  # type: ignore[index]
        hi = max(hi, lo + 0.2 * (j - i))
        sizes = [max(len(texts[k]), 1) for k in range(i, j)]
        t = lo
        for k, size in zip(range(i, j), sizes):
            step = (hi - lo) * size / sum(sizes)
            spans[k] = (t, t + step - (CUE_GAP_S if k < j - 1 else 0.0))
            t += step
        i = j
    out: list[dict[str, Any]] = []
    prev_end = 0.0
    for i, text in enumerate(texts):
        span = spans[i]
        assert span is not None
        start = max(span[0], prev_end + CUE_GAP_S if i else 0.0)
        end = max(span[1], start + 0.2)
        entry = {"text": text, "start_s": round(start, 3), "end_s": round(end, 3)}
        if scene_ids:
            entry["scene"] = scene_ids[i]
        out.append(entry)
        prev_end = end
    return out


Recogniser = Callable[[Path, str], list[Word]]
# Silero VAD (bundled with faster-whisper) cuts the silence out before recognition. Without it, or with the
# default 400 ms padding, Whisper gives the silence before a sentence to its first word, so word starts come
# out early by up to half a second and the drift check would measure Whisper, not the subtitles.
VAD_PARAMETERS = {"speech_pad_ms": 30, "min_silence_duration_ms": 150}


def decode_16k(media: Path, ffmpeg: str = "ffmpeg") -> Any:
    """The first audio track as 16 kHz mono float32 samples (numpy), decoded by ffmpeg.

    faster-whisper can decode files itself through PyAV, but PyAV releases
    change its API; ffmpeg is already required, so it decodes here.
    """
    import numpy as np

    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(media), "-map", "0:a:0", "-ac", "1",
         "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise SubtitleError(f"cannot decode the audio of {media}: {proc.stderr.decode(errors='replace').strip()[-400:]}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def whisper_recogniser(model: str = DEFAULT_MODEL, device: str = "cpu") -> Recogniser:
    """A local faster-whisper recogniser with word timestamps (MIT; weights go to the Hugging Face cache).

    CPU with int8 is the default: the small models run faster than real time
    there, need no CUDA libraries and leave the GPU to the TTS step.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SubtitleError(f"faster-whisper is not installed ({exc}); run `pip install -e .[subtitles]`") from exc
    if device == "auto":
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:  # noqa: BLE001 - any probe failure means CPU
            device = "cpu"
    try:
        asr = WhisperModel(model, device=device, compute_type="int8_float16" if device == "cuda" else "int8")
    except (RuntimeError, ValueError, OSError) as exc:
        raise SubtitleError(f"cannot load faster-whisper model {model!r} on {device}: {exc}") from exc

    def recognise(audio: Path, lang: str) -> list[Word]:
        try:
            segments, _ = asr.transcribe(
                decode_16k(audio), language=lang, word_timestamps=True, beam_size=5, vad_filter=True,
                vad_parameters=VAD_PARAMETERS, condition_on_previous_text=False,
            )
            words = [Word(float(w.start), float(w.end), str(w.word)) for seg in segments for w in (seg.words or [])]
            return snap_to_speech(words, activity(audio))
        except SubtitleError:
            raise
        except RuntimeError as exc:  # for example missing CUDA libraries, found only at the first encode
            raise SubtitleError(f"faster-whisper failed on {device}: {exc}; try --device cpu") from exc

    return recognise


# --- files next to a render -------------------------------------------------------------------------------------------


def sidecar(video: Path, suffix: str) -> Path:
    """``out/x.en.mp4`` -> ``out/x.en<suffix>`` (for example ``.srt``, ``.lines.json``)."""
    return Path(video).with_suffix(suffix)


def video_size(video: Path, ffprobe: str = "ffprobe") -> tuple[int, int]:
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
         str(video)],
        capture_output=True, text=True, check=False,
    )
    try:
        w, h = (int(v) for v in proc.stdout.strip().split("x")[:2])
    except ValueError as exc:
        raise SubtitleError(f"cannot read the size of {video}: {proc.stderr.strip() or proc.stdout!r}") from exc
    return w, h


def write_srt(video: Path, lines: list[dict[str, Any]], width_px: int, height_px: int,
              use_audio: bool = True) -> tuple[Path, list[Cue]]:
    """Write ``<name>.srt`` next to ``video``; with ``use_audio`` the cues follow the speech in its audio track."""
    act = activity(video) if use_audio else None
    cues = cues_from_lines(lines, width_for(width_px, height_px), act=act)
    if not cues:
        raise SubtitleError("no narration lines to subtitle")
    path = sidecar(video, ".srt")
    path.write_text(to_srt(cues), encoding="utf-8")
    return path, cues


def burn_cmd(video: Path, srt_name: str, out: Path, width_px: int, height_px: int, *, font: str | None = None,
             theme: Theme | str | None = None, preset: str = "medium", crf: int = 20, ffmpeg: str = "ffmpeg") -> list[str]:
    """ffmpeg command that draws ``srt_name`` (a file in the working folder) into the picture.

    libass scales SRT text to a 288-line canvas, so the size below follows the
    frame height; portrait frames get a smaller size and narrower rows. Text
    sits on a translucent box in the theme's subtitle colours (BorderStyle 3:
    libass fills the box with OutlineColour), so it stays readable on any frame.
    """
    theme = theme if isinstance(theme, Theme) else get_theme(theme)
    portrait = height_px > width_px
    box = ass_colour(theme.subtitle_box, theme.subtitle_box_opacity)
    style = ",".join([
        f"FontName={font or theme.font}", f"FontSize={14 if portrait else 18}",
        f"PrimaryColour={ass_colour(theme.subtitle_text)}", f"OutlineColour={box}", f"BackColour={box}",
        "BorderStyle=3", "Outline=2", "Shadow=0", "Alignment=2",
        f"MarginV={70 if portrait else 18}", "WrapStyle=2",
    ])
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(video),
        "-vf", f"subtitles=filename={srt_name}:charenc=UTF-8:force_style='{style}'",
        "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
        "-c:a", "copy", "-movflags", "+faststart", str(out),
    ]


def burn(video: Path, srt: Path, out: Path | None = None, **kw: Any) -> Path:
    """Write ``<name>.burned.mp4`` with the subtitles drawn in. The clean video is kept."""
    video, srt = Path(video).resolve(), Path(srt).resolve()
    out = Path(out).resolve() if out else sidecar(video, ".burned.mp4")
    w, h = video_size(video)
    # The partial file is written next to the output (the work dir), not to the system temp on the WSL disk.
    with tempfile.TemporaryDirectory(prefix=".burn-", dir=out.parent) as tmp:
        # The filter argument names a plain local file, so paths need no escaping.
        shutil.copyfile(srt, Path(tmp) / "subs.srt")
        partial = Path(tmp) / "burned.mp4"
        proc = subprocess.run(burn_cmd(video, "subs.srt", partial, w, h, **kw), cwd=tmp, capture_output=True, text=True,
                              check=False)
        if proc.returncode != 0:
            tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
            raise SubtitleError(f"burning subtitles into {video.name} failed:\n{tail}")
        shutil.move(str(partial), out)
    return out
