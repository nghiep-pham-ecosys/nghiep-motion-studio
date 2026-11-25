"""Quality gate: checks a rendered ``<name>.<lang>.mp4`` before it can go any further.

One check per rule, each pass or fail, written to ``<name>.<lang>.gate.json``
(machine-readable) and printed as one line per check (for a person):

- ``duration``: video and audio streams present, their lengths agree, the
  video matches the render's ``.timeline.json`` and lies inside the expected
  window (10-20 minutes by default);
- ``loudness``: integrated loudness -14 LUFS within a tolerance, true peak
  under a limit (ffmpeg ``loudnorm`` analysis, which measures per EBU R128);
- ``black-frames``: no run of black frames (ffmpeg ``blackdetect``);
- ``subtitle-drift``: the ``.srt`` exists and the ``.drift.json`` written by
  ``motion-studio subtitles --check-drift`` (or by ``gate --check-drift``) is
  newer than the video and the SRT and passes the drift limits, re-evaluated
  here with this module's limits, never the file's own;
- ``fact-check``: ``factcheck.md`` next to ``scenes.json`` names a reviewer
  and a date, matches the current narration (by hash) and lists enough
  checked claims, each with a source, and none unchecked.

The gate fails closed: a check that cannot run (a missing file, an ffmpeg
error, an unreadable report) fails. Thresholds and their reasons are in
docs/spec.md, "Quality gate".
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import re
import subprocess
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable

# --- thresholds (docs/spec.md, "Quality gate") -----------------------------------------------------------------------

MIN_DURATION_S = 600.0  # 10 minutes
MAX_DURATION_S = 1200.0  # 20 minutes
STREAM_TOLERANCE_S = 0.1  # audio vs video length, and video vs timeline; AAC frames are 21 ms, a frame is 33 ms
TARGET_LUFS = -14.0
LUFS_TOLERANCE = 1.0  # LU either side of the target
MAX_TRUE_PEAK_DBTP = -1.0
BLACK_MIN_S = 0.1  # 3 frames at 30 fps: shorter runs are not visible
BLACK_PIX_TH = 0.05  # luma below 16 + 0.05 * 219 = 27 (limited range) is black; the dark theme background is 36
BLACK_PIC_TH = 0.98  # share of black pixels for a frame to count as black
FACTCHECK_NAME = "factcheck.md"
MIN_CLAIMS = 3
HASH_CHARS = 12  # how many hex characters of the narration hash factcheck.md must give


class GateError(RuntimeError):
    """A measurement could not be made (a tool failed or its output was not understood)."""


@dataclass(frozen=True)
class Limits:
    min_duration_s: float = MIN_DURATION_S
    max_duration_s: float = MAX_DURATION_S
    stream_tolerance_s: float = STREAM_TOLERANCE_S
    target_lufs: float = TARGET_LUFS
    lufs_tolerance: float = LUFS_TOLERANCE
    max_true_peak_dbtp: float = MAX_TRUE_PEAK_DBTP
    black_min_s: float = BLACK_MIN_S
    black_pix_th: float = BLACK_PIX_TH
    black_pic_th: float = BLACK_PIC_TH
    min_claims: int = MIN_CLAIMS


@dataclass
class Check:
    name: str
    ok: bool
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        return f"{'PASS' if self.ok else 'FAIL'} {self.name}: {self.summary}"


@dataclass
class GateReport:
    video: str
    lang: str
    limits: Limits
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    def summary(self) -> str:
        rows = [c.line() for c in self.checks]
        failed = [c.name for c in self.checks if not c.ok]
        verdict = "GATE PASS" if self.ok else f"GATE FAIL ({', '.join(failed)})"
        return "\n".join(rows + [f"{verdict} {self.video}"])

    def json(self) -> str:
        data = {"video": self.video, "lang": self.lang, "ok": self.ok, "limits": asdict(self.limits),
                "checks": [asdict(c) for c in self.checks]}
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def sidecar(video: Path, suffix: str) -> Path:
    """``out/x.en.mp4`` -> ``out/x.en<suffix>``."""
    return Path(video).with_suffix(suffix)


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise GateError(f"{cmd[0]} not found; install ffmpeg (6.1 or newer)") from exc


def _tail(text: str, n: int = 8) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


# --- duration ----------------------------------------------------------------------------------------------------------


def probe(video: Path, ffprobe: str = "ffprobe") -> dict[str, Any]:
    proc = _run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,duration",
                 "-of", "json", str(video)])
    if proc.returncode != 0:
        raise GateError(f"ffprobe cannot read {video}: {_tail(proc.stderr)}")
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise GateError(f"ffprobe gave no JSON for {video}") from exc


def _stream_s(info: dict[str, Any], kind: str) -> float | None:
    for stream in info.get("streams", []):
        if stream.get("codec_type") == kind:
            try:
                return float(stream["duration"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def check_duration(video: Path, limits: Limits, timeline: Path | None = None, ffprobe: str = "ffprobe") -> Check:
    timeline = timeline or sidecar(video, ".timeline.json")
    info = probe(video, ffprobe)
    v, a = _stream_s(info, "video"), _stream_s(info, "audio")
    details: dict[str, Any] = {"video_s": v, "audio_s": a, "window_s": [limits.min_duration_s, limits.max_duration_s]}
    problems = []
    if v is None:
        problems.append("no video stream")
    if a is None:
        problems.append("no audio stream")
    if v is not None and a is not None and abs(v - a) > limits.stream_tolerance_s:
        problems.append(f"audio {a:.3f} s and video {v:.3f} s differ by more than {limits.stream_tolerance_s} s")
    if v is not None and not limits.min_duration_s <= v <= limits.max_duration_s:
        problems.append(f"{v:.1f} s is outside {limits.min_duration_s:.0f}-{limits.max_duration_s:.0f} s")
    if not timeline.is_file():
        problems.append(f"no {timeline.name} (render writes it)")
    else:
        try:
            planned = float(json.loads(timeline.read_text(encoding="utf-8"))["duration_s"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            problems.append(f"unreadable {timeline.name}: {exc}")
        else:
            details["timeline_s"] = planned
            if v is not None and abs(v - planned) > limits.stream_tolerance_s:
                problems.append(f"video {v:.3f} s does not match the planned {planned:.3f} s of {timeline.name}")
    if problems:
        return Check("duration", False, "; ".join(problems), details)
    return Check("duration", True, f"{v:.2f} s (audio {a:.2f} s), inside {limits.min_duration_s:.0f}-"
                 f"{limits.max_duration_s:.0f} s, matches {timeline.name}", details)


# --- loudness ------------------------------------------------------------------------------------------------------------


def _db(text: Any) -> float:
    value = float(str(text).strip())  # "-inf" parses to -inf
    if math.isnan(value):
        raise ValueError("nan")
    return value


def measure_loudness(video: Path, ffmpeg: str = "ffmpeg") -> dict[str, float]:
    """Integrated loudness (LUFS), true peak (dBTP) and loudness range (LU) of the first audio track."""
    proc = _run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(video), "-map", "0:a:0", "-vn",
                 "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    if proc.returncode != 0:
        raise GateError(f"loudness analysis of {video} failed: {_tail(proc.stderr)}")
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr)
    if not match:
        raise GateError(f"no loudnorm report for {video}")
    data = json.loads(match.group(0))
    try:
        return {"integrated_lufs": _db(data["input_i"]), "true_peak_dbtp": _db(data["input_tp"]),
                "lra_lu": _db(data["input_lra"])}
    except (KeyError, ValueError) as exc:
        raise GateError(f"unreadable loudnorm report for {video}: {exc}") from exc


def check_loudness(video: Path, limits: Limits, ffmpeg: str = "ffmpeg") -> Check:
    m = measure_loudness(video, ffmpeg)
    i, tp = m["integrated_lufs"], m["true_peak_dbtp"]
    problems = []
    if not math.isfinite(i):
        problems.append(f"no sound (integrated {i} LUFS)")
    elif not abs(i - limits.target_lufs) <= limits.lufs_tolerance:
        problems.append(f"integrated {i:.1f} LUFS, wanted {limits.target_lufs:.0f} +/- {limits.lufs_tolerance:g} LU")
    if not tp <= limits.max_true_peak_dbtp:
        problems.append(f"true peak {tp:.1f} dBTP above {limits.max_true_peak_dbtp:g} dBTP")
    details = {k: (v if math.isfinite(v) else str(v)) for k, v in m.items()}
    if problems:
        return Check("loudness", False, "; ".join(problems), details)
    return Check("loudness", True, f"integrated {i:.1f} LUFS, true peak {tp:.1f} dBTP, range {m['lra_lu']:.1f} LU",
                 details)


# --- black frames -------------------------------------------------------------------------------------------------------

_BLACK = re.compile(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)")


def black_runs(video: Path, limits: Limits, ffmpeg: str = "ffmpeg") -> list[tuple[float, float]]:
    """Runs of black frames as (start, end) seconds."""
    vf = f"blackdetect=d={limits.black_min_s}:pix_th={limits.black_pix_th}:pic_th={limits.black_pic_th}"
    proc = _run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(video), "-map", "0:v:0", "-an", "-vf", vf,
                 "-f", "null", "-"])
    if proc.returncode != 0:
        raise GateError(f"black-frame scan of {video} failed: {_tail(proc.stderr)}")
    return [(float(a), float(b)) for a, b in _BLACK.findall(proc.stderr)]


def check_black(video: Path, limits: Limits, ffmpeg: str = "ffmpeg") -> Check:
    runs = black_runs(video, limits, ffmpeg)
    details = {"runs": [{"start_s": a, "end_s": b} for a, b in runs]}
    if runs:
        listed = ", ".join(f"{a:.2f}-{b:.2f} s" for a, b in runs[:5]) + (" ..." if len(runs) > 5 else "")
        return Check("black-frames", False, f"{len(runs)} black run(s): {listed}", details)
    return Check("black-frames", True, f"no black run of {limits.black_min_s:g} s or more", details)


# --- subtitle drift ----------------------------------------------------------------------------------------------------


def check_drift(video: Path) -> Check:
    """Read ``<name>.<lang>.drift.json`` and judge it again with the limits in subtitles.py."""
    from .subtitles import DriftReport

    srt, report = sidecar(video, ".srt"), sidecar(video, ".drift.json")
    if not srt.is_file():
        return Check("subtitle-drift", False, f"no {srt.name}")
    if not report.is_file():
        return Check("subtitle-drift", False, f"no {report.name}; run `motion-studio subtitles {video} --check-drift` "
                     "(just drift) or `gate --check-drift`")
    newest = max(video.stat().st_mtime, srt.stat().st_mtime)
    if report.stat().st_mtime < newest:
        return Check("subtitle-drift", False, f"{report.name} is older than the video or its SRT; check drift again")
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
        names = {f.name for f in fields(DriftReport)} - {"limit_s", "p90_limit_s", "worst"}
        drift = DriftReport(**{k: data[k] for k in names})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return Check("subtitle-drift", False, f"unreadable {report.name}: {exc}")
    details = {k: getattr(drift, k) for k in sorted(names)} | {"limit_s": drift.limit_s, "p90_limit_s": drift.p90_limit_s}
    return Check("subtitle-drift", drift.ok, drift.summary(), details)


def run_drift(video: Path, lang: str, recognise: Callable[[Path, str], list], model: str) -> None:
    """Measure drift with faster-whisper and write ``.drift.json`` (cues from the SRT next to the video)."""
    from .subtitles import drift, parse_srt

    srt = sidecar(video, ".srt")
    cues = parse_srt(srt.read_text(encoding="utf-8"))
    report = drift(cues, recognise(video, lang), lang, model)
    sidecar(video, ".drift.json").write_text(report.json(), encoding="utf-8")


# --- fact check ----------------------------------------------------------------------------------------------------------


def narration_hash(doc: dict[str, Any]) -> str:
    """sha256 of every scene's id and narration (all languages): what factcheck.md was written against."""
    body = [[s.get("id"), s.get("narration")] for s in doc.get("scenes", [])]
    text = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_CLAIM = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*\S)\s*$")
_SOURCE = re.compile(r"https?://\S+|\bsource:\s*\S", re.IGNORECASE)


def check_factcheck(scenes_file: Path, limits: Limits) -> Check:
    path = Path(scenes_file).parent / FACTCHECK_NAME
    try:
        doc = json.loads(Path(scenes_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Check("fact-check", False, f"cannot read {scenes_file}: {exc}")
    want = narration_hash(doc)[:HASH_CHARS]
    if not path.is_file():
        return Check("fact-check", False, f"no {path}", {"narration": want})
    text = path.read_text(encoding="utf-8")
    field_of = lambda key: (m.group(1).strip() if (m := re.search(rf"^{key}:[ \t]*(.*)$", text, re.M | re.I)) else "")  # noqa: E731
    problems = []
    reviewer, date, narration = field_of("Reviewed-by"), field_of("Date"), field_of("Narration")
    if not reviewer:
        problems.append("no 'Reviewed-by:' line")
    try:
        _dt.date.fromisoformat(date)
    except ValueError:
        problems.append("no 'Date: YYYY-MM-DD' line")
    if narration.lower()[:HASH_CHARS] != want:
        problems.append(f"'Narration: {narration or '(missing)'}' does not match the current narration ({want}); "
                        "check the claims again")
    claims = [m.groups() for line in text.splitlines() if (m := _CLAIM.match(line))]
    checked = [c for box, c in claims if box.lower() == "x"]
    unchecked = [c for box, c in claims if box == " "]
    no_source = [c for c in checked if not _SOURCE.search(c)]
    if len(checked) < limits.min_claims:
        problems.append(f"{len(checked)} checked claim(s), at least {limits.min_claims} needed")
    if unchecked:
        problems.append(f"{len(unchecked)} unchecked claim(s)")
    if no_source:
        problems.append(f"{len(no_source)} claim(s) without a source")
    details = {"file": str(path), "narration": want, "reviewer": reviewer, "date": date, "checked": len(checked),
               "unchecked": len(unchecked), "without_source": len(no_source)}
    if problems:
        return Check("fact-check", False, "; ".join(problems), details)
    return Check("fact-check", True, f"{len(checked)} claim(s) checked with sources, reviewed {date}", details)


# --- the gate ------------------------------------------------------------------------------------------------------------


def _guard(name: str, fn: Callable[[], Check]) -> Check:
    """Run one check; any error fails it (the gate fails closed)."""
    try:
        return fn()
    except (GateError, OSError, ValueError, KeyError, TypeError) as exc:
        return Check(name, False, f"could not run: {exc}")


def run_gate(video: Path, scenes_file: Path, lang: str, limits: Limits | None = None, *, ffmpeg: str = "ffmpeg",
             ffprobe: str = "ffprobe") -> GateReport:
    video, limits = Path(video), limits or Limits()
    checks = [
        _guard("duration", lambda: check_duration(video, limits, ffprobe=ffprobe)),
        _guard("loudness", lambda: check_loudness(video, limits, ffmpeg)),
        _guard("black-frames", lambda: check_black(video, limits, ffmpeg)),
        _guard("subtitle-drift", lambda: check_drift(video)),
        _guard("fact-check", lambda: check_factcheck(Path(scenes_file), limits)),
    ]
    return GateReport(video=str(video), lang=lang, limits=limits, checks=checks)
