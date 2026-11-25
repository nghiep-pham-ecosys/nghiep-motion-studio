"""Shorts: cut 45-60 s vertical clips (9:16) from a rendered long video.

How it works (docs/spec.md, "Shorts"):

1. Cut points. Every scene boundary of ``<name>.<lang>.timeline.json`` is a
   candidate; a boundary where the chapter changes (``chapter`` in
   scenes.json) is a *chapter mark*. Between two narration lines of
   ``<name>.<lang>.lines.json`` (or the cues of ``<name>.<lang>.srt``), the
   end of a sentence is a candidate too. Each candidate is moved into the
   silence around it, read from the video's own audio (10 ms energy frames,
   as for subtitles), and snapped to a whole frame; a candidate with no
   silence near it is dropped, so a clip never starts or ends inside a word.
2. Windows. Every pair of cut points 45-60 s apart is a window. The best set
   of non-overlapping windows is chosen (most windows first, up to the count
   asked for, then the best marks: a chapter start beats a scene start, which
   beats a sentence). Fewer windows than asked are reported, never padded.
3. Clips. Each window is cut from the clean video (never the ``.burned.mp4``):
   the 16:9 frame is placed full width on a 1080x1920 frame in the theme's
   background, the chapter title above it and large subtitles below it, all
   inside the area the Shorts player leaves free (top 15 % and bottom 25 %
   stay clear). The audio of the window goes through the same two-pass
   ``loudnorm`` as a render (``render.normalize_mix``), so each clip meets the
   quality gate's loudness targets, and the result is measured again.

Clips go to ``<job>/shorts/<name>-<n>.<lang>.mp4`` with an ``.srt`` each and a
``<name>.<lang>.shorts.json`` report, so ``clean --after-upload`` covers them.
"""

from __future__ import annotations

import bisect
import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .gate import LUFS_TOLERANCE, measure_loudness
from .render import (
    MAX_TRUE_PEAK_DBTP, SAMPLE_RATE, TARGET_LUFS, RenderError, RenderOptions, _run, count_video_frames, normalize_mix,
)
from .subtitles import PORTRAIT_CHARS, Activity, Cue, activity, cues_from_lines, parse_srt, to_srt, video_size, wrap
from .theme import Theme, ass_colour, get_theme

MIN_S = 45.0
MAX_S = 60.0
COUNT = 3
WIDTH, HEIGHT = 1080, 1920  # 9:16
LEAD_S = 0.25  # silence kept before the first word of a clip
TAIL_S = 0.6  # silence kept after its last word
REACH_S = 0.5  # how far from a candidate to look for silence
MIN_SILENCE_S = 0.1  # a shorter gap is a breath inside speech, not a place to cut
SHORTS_DIR = "shorts"
_SENTENCE_END = re.compile(r"[.!?…。！？][\"'”’)\]]*$")

# Layout as fractions of the frame height (Shorts keeps its own buttons and text in the top ~15 % and bottom ~25 %).
FRAME_TOP = 0.29  # top of the 16:9 frame
HEADER_CENTRE = 0.215  # centre of the chapter title
HEADER_SIZE = 1 / 24  # chapter title font size (80 px at 1920)
SUBTITLE_GAP = 0.035  # between the frame and the first subtitle row
SUBTITLE_SIZE = 1 / 30  # subtitle font size (64 px at 1920)
SIDE_MARGIN = 0.12  # subtitle side margins, as a share of the width (the right edge holds the Shorts buttons)
BORDER = 1 / 270  # frame outline, as a share of the width (4 px at 1080)
HEADER_CHARS = 22
RANK = {"chapter": 3, "end": 3, "scene": 2, "sentence": 1}


class ShortsError(RuntimeError):
    """Shorts could not be cut (a missing sidecar, a mismatched scenes.json, or an ffmpeg failure)."""


@dataclass(frozen=True)
class Mark:
    """A place to cut: a clip that starts here starts at ``start_frame``; one that ends here ends at ``end_frame``."""

    t: float  # the boundary on the video clock
    kind: str  # chapter, scene, sentence or end (the end of the video)
    start_frame: int
    end_frame: int


@dataclass(frozen=True)
class Window:
    start_frame: int
    end_frame: int
    fps: int
    start_kind: str
    end_kind: str
    chapter: str | None = None

    @property
    def start_s(self) -> float:
        return self.start_frame / self.fps

    @property
    def end_s(self) -> float:
        return self.end_frame / self.fps

    @property
    def duration_s(self) -> float:
        return (self.end_frame - self.start_frame) / self.fps


@dataclass
class ShortOptions:
    count: int = COUNT
    min_s: float = MIN_S
    max_s: float = MAX_S
    width: int = WIDTH
    height: int = HEIGHT
    theme: str | None = None  # overrides the scenes file's theme
    preset: str = "medium"
    crf: int = 20
    audio_bitrate: str = "192k"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError("count must be at least 1")
        if not 0 < self.min_s <= self.max_s:
            raise ValueError("need 0 < min_s <= max_s")
        if self.width <= 0 or self.height <= self.width or self.width % 2 or self.height % 2:
            raise ValueError("the Shorts frame must be portrait with even sides")
        if self.theme is not None:
            get_theme(self.theme)


@dataclass
class Clip:
    path: Path
    window: Window
    cues: int
    loudness: dict[str, Any] = field(default_factory=dict)  # what normalize_mix did
    measured: dict[str, float] = field(default_factory=dict)  # the finished clip, as the gate measures it

    @property
    def loudness_ok(self) -> bool:
        m = self.measured
        return bool(m) and abs(m["integrated_lufs"] - TARGET_LUFS) <= LUFS_TOLERANCE and m["true_peak_dbtp"] <= MAX_TRUE_PEAK_DBTP


@dataclass
class ShortsResult:
    video: Path
    lang: str
    wanted: int
    duration_s: float
    clips: list[Clip]
    report: Path | None = None

    @property
    def ok(self) -> bool:
        return len(self.clips) >= self.wanted and all(c.loudness_ok for c in self.clips)

    def json(self) -> str:
        data = {
            "video": self.video.name,
            "lang": self.lang,
            "wanted": self.wanted,
            "found": len(self.clips),
            "video_s": round(self.duration_s, 3),
            "clips": [
                {
                    "path": f"{SHORTS_DIR}/{c.path.name}",
                    **{k: v for k, v in asdict(c.window).items() if k != "fps"},
                    "start_s": round(c.window.start_s, 3), "end_s": round(c.window.end_s, 3),
                    "duration_s": round(c.window.duration_s, 3), "cues": c.cues,
                    "loudness": c.loudness, "measured": c.measured, "loudness_ok": c.loudness_ok,
                }
                for c in self.clips
            ],
        }
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# --- marks -----------------------------------------------------------------------------------------------------------


def chapter_starts(scenes_doc: dict[str, Any] | None, timeline: dict[str, Any], lang: str) -> list[tuple[float, str]]:
    """``(start_s, title)`` of every scene where the chapter changes. Scenes without ``chapter`` continue the last one."""
    if scenes_doc is None:
        return []
    ids = [s["id"] for s in scenes_doc["scenes"]]
    if ids != [s["id"] for s in timeline["scenes"]]:
        raise ShortsError("scenes.json does not match this render (its scene ids differ from the timeline's)")
    out: list[tuple[float, str]] = []
    current = None
    for scene, timing in zip(scenes_doc["scenes"], timeline["scenes"]):
        title = scene.get("chapter", {}).get(lang)
        if title and title != current:
            out.append((float(timing["start_s"]), title))
            current = title
    return out


def candidates(timeline: dict[str, Any], chapters: list[tuple[float, str]], lines: list[dict[str, Any]]) -> list[tuple[float, str]]:
    """Boundaries worth cutting at, with their kind, in time order (one per place, the best kind kept)."""
    duration = float(timeline["duration_s"])
    chapter_times = {round(t, 3) for t, _ in chapters}
    found: dict[float, str] = {}

    def add(t: float, kind: str) -> None:
        key = round(t, 3)
        if RANK[kind] > RANK.get(found.get(key, ""), 0):
            found[key] = kind

    for scene in timeline["scenes"]:
        t = float(scene["start_s"])
        add(t, "chapter" if round(t, 3) in chapter_times else "scene")
    add(duration, "end")
    ordered = sorted(lines, key=lambda ln: float(ln["start_s"]))
    for prev, nxt in zip(ordered, ordered[1:]):
        if _SENTENCE_END.search(str(prev["text"]).strip()):
            add((float(prev["end_s"]) + float(nxt["start_s"])) / 2, "sentence")
    scene_starts = sorted(float(s["start_s"]) for s in timeline["scenes"])
    out = []
    for t, kind in sorted(found.items()):
        if kind == "sentence":  # a sentence end next to a scene boundary is that boundary
            i = bisect.bisect_left(scene_starts, t)
            if any(abs(scene_starts[j] - t) < REACH_S for j in (i - 1, i) if 0 <= j < len(scene_starts)):
                continue
        out.append((t, kind))
    return out


def silence_around(act: Activity, t: float, duration: float, reach: float = REACH_S) -> tuple[float, float] | None:
    """The silent run at ``t``, or the nearest one within ``reach``, as (start, end) seconds; None if there is none.

    Audio past the end of the track counts as silent; the run is clipped to [0, duration].
    """
    n = len(act.voiced)
    step = act.frame_s
    voiced = lambda i: 0 <= i < n and act.voiced[i]  # noqa: E731
    centre = int(t / step)
    best = None
    for d in range(0, int(reach / step) + 1):
        for i in (centre - d, centre + d):
            if i >= 0 and i * step <= duration and not voiced(i):
                best = i
                break
        if best is not None:
            break
    if best is None:
        return None
    a = best
    while a > 0 and not voiced(a - 1):
        a -= 1
    b = best + 1
    while b * step < duration and not voiced(b):
        b += 1
    start, end = max(0.0, a * step), min(duration, b * step)
    if end - start < MIN_SILENCE_S and not (start <= 0.0 or end >= duration):
        return None
    return start, end


def place(cands: list[tuple[float, str]], act: Activity, fps: int, duration: float) -> list[Mark]:
    """Move each candidate into its silence and onto whole frames; drop one that has no silence near it."""
    total = round(duration * fps)
    marks = []
    for t, kind in cands:
        if t <= 0 or kind == "end":  # the ends of the video cut nothing, even when speech runs right up to them
            marks.append(Mark(t, kind, 0 if t <= 0 else total, total if kind == "end" else 0))
            continue
        run = silence_around(act, t, duration)
        if run is None:
            continue
        a, b = run
        lo, hi = max(0, math.ceil(a * fps - 1e-6)), min(total, math.floor(b * fps + 1e-6))
        if lo > hi:
            continue
        start = min(hi, max(lo, round((b - LEAD_S) * fps)))
        end = min(hi, max(lo, round((a + TAIL_S) * fps)))
        marks.append(Mark(t, kind, start, end))
    return marks


def pick(marks: list[Mark], fps: int, count: int = COUNT, min_s: float = MIN_S, max_s: float = MAX_S,
         chapters: list[tuple[float, str]] | None = None) -> list[Window]:
    """The best set of non-overlapping windows: as many as fit (up to ``count``), then the best-scoring marks.

    Score of a window: 10 x the rank of its start mark + 3 x the rank of its end
    mark + its share of ``max_s`` (longer is a little better).
    """
    lo, hi = math.ceil(min_s * fps - 1e-6), math.floor(max_s * fps + 1e-6)
    wins: list[tuple[float, Window]] = []
    for s in marks:
        if s.kind == "end":
            continue
        for e in marks:
            if e.t <= s.t:
                continue
            length = e.end_frame - s.start_frame
            if lo <= length <= hi:
                w = Window(s.start_frame, e.end_frame, fps, s.kind, e.kind)
                wins.append((10 * RANK[s.kind] + 3 * RANK[e.kind] + length / hi, w))
    wins.sort(key=lambda sw: (sw[1].end_frame, sw[1].start_frame))
    ends = [w.end_frame for _, w in wins]
    neg = -math.inf
    # best[k][i]: the best total of k windows among wins[:i + 1]; choice[k][i]: the window that ends that set, or None.
    best = [[0.0] * len(wins)] + [[neg] * len(wins) for _ in range(count)]
    choice: list[list[int | None]] = [[None] * len(wins) for _ in range(count + 1)]
    for k in range(1, count + 1):
        for i, (score, w) in enumerate(wins):
            j = bisect.bisect_right(ends, w.start_frame) - 1  # the last window that ends at or before this start
            before = (best[k - 1][j] if j >= 0 else (0.0 if k == 1 else neg))
            take = score + before if before > neg else neg
            keep = best[k][i - 1] if i else neg
            if take > keep:
                best[k][i], choice[k][i] = take, i
            else:
                best[k][i], choice[k][i] = keep, choice[k][i - 1] if i else None
    k = next((k for k in range(count, 0, -1) if wins and best[k][-1] > neg), 0)
    chosen: list[Window] = []
    i = len(wins) - 1
    while k > 0 and i >= 0:
        idx = choice[k][i]
        assert idx is not None
        w = wins[idx][1]
        chosen.append(w)
        k -= 1
        i = bisect.bisect_right(ends, w.start_frame) - 1
    chosen.reverse()
    return [_with_chapter(w, chapters or []) for w in chosen]


def _with_chapter(w: Window, chapters: list[tuple[float, str]]) -> Window:
    title = None
    for t, name in chapters:
        if t <= w.start_s + REACH_S:
            title = name
    return Window(w.start_frame, w.end_frame, w.fps, w.start_kind, w.end_kind, title)


def clip_lines(lines: list[dict[str, Any]], w: Window) -> list[dict[str, Any]]:
    """The narration lines whose middle lies in the window, on the clip's clock."""
    out = []
    for line in lines:
        start, end = float(line["start_s"]), float(line["end_s"])
        if w.start_s <= (start + end) / 2 < w.end_s:
            out.append({"text": line["text"], "start_s": max(0.0, start - w.start_s),
                        "end_s": min(w.duration_s, end - w.start_s)})
    return out


# --- drawing ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    frame_w: int  # the scaled 16:9 picture, without its outline
    frame_h: int
    border: int
    frame_y: int  # top of the outline
    header_y: int  # centre of the chapter title
    header_size: int
    subtitle_y: int  # top of the first subtitle row
    subtitle_size: int
    side_margin: int


def layout(width: int, height: int, src_w: int, src_h: int) -> Layout:
    border = max(1, round(width * BORDER))
    frame_w = width - 2 * border
    frame_h = frame_w * src_h / src_w
    max_h = 0.4 * height
    if frame_h > max_h:  # a tall source: fit by height instead
        frame_w, frame_h = max_h * src_w / src_h, max_h
    even = lambda v: max(2, int(round(v / 2)) * 2)  # noqa: E731
    frame_w, frame_h = even(frame_w), even(frame_h)
    frame_y = round(FRAME_TOP * height)
    return Layout(
        width=width, height=height, frame_w=frame_w, frame_h=frame_h, border=border, frame_y=frame_y,
        header_y=round(HEADER_CENTRE * height), header_size=max(8, round(HEADER_SIZE * height)),
        subtitle_y=frame_y + frame_h + 2 * border + round(SUBTITLE_GAP * height),
        subtitle_size=max(8, round(SUBTITLE_SIZE * height)), side_margin=round(SIDE_MARGIN * width),
    )


def ass_time(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, rest = divmod(cs, 360000)
    m, rest = divmod(rest, 6000)
    s, cs = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_text(text: str) -> str:
    """Cue text for an ASS event: braces would start override tags and a backslash an escape, so they are replaced."""
    text = text.replace("\\", "⧵").replace("{", "(").replace("}", ")")
    return "\\N".join(row.strip() for row in text.split("\n"))


def ass_file(cues: list[Cue], lay: Layout, theme: Theme, font: str | None = None) -> str:
    """Subtitles as ASS on the Shorts canvas: large bold text on the theme's subtitle box, top-anchored under the frame."""
    box = ass_colour(theme.subtitle_box, theme.subtitle_box_opacity)
    pad = max(2, round(lay.subtitle_size * 0.18))
    style = ",".join([
        "Short", font or theme.font, str(lay.subtitle_size), ass_colour(theme.subtitle_text), ass_colour(theme.subtitle_text),
        box, box, "-1", "0", "0", "0", "100", "100", "0", "0", "3", str(pad), "0", "8",
        str(lay.side_margin), str(lay.side_margin), str(lay.subtitle_y + pad), "1",
    ])
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {lay.width}\nPlayResY: {lay.height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: {style}\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = "".join(f"Dialogue: 0,{ass_time(c.start_s)},{ass_time(c.end_s)},Short,,0,0,0,,{ass_text(c.text)}\n" for c in cues)
    return head + events


def _hex(color: str) -> str:
    return "0x" + color.lstrip("#")


def video_filter(lay: Layout, theme: Theme, fps: int, header: bool, font: str | None = None) -> str:
    """The filter graph: picture -> outlined frame on the theme background, chapter title, subtitles, BT.709."""
    b = lay.border
    head = ""
    if header:
        head = (
            f"drawtext=textfile=header.txt:font={font or theme.font}:fontsize={lay.header_size}:"
            f"fontcolor={_hex(theme.text)}:line_spacing={lay.header_size // 4}:text_align=C:"
            f"x=(w-text_w)/2:y={lay.header_y}-text_h/2:expansion=none:fix_bounds=1,"
        )
    return (
        f"color=c={_hex(theme.background)}:s={lay.width}x{lay.height}:r={fps},format=rgb24[bg];"
        f"[0:v]setpts=PTS-STARTPTS,fps={fps},tpad=stop_mode=clone:stop_duration=1,"
        f"scale={lay.frame_w}:{lay.frame_h}:flags=lanczos,setsar=1,format=rgb24,"
        f"pad=iw+{2 * b}:ih+{2 * b}:{b}:{b}:color={_hex(theme.border)}[fr];"
        f"[bg][fr]overlay=x=(W-w)/2:y={lay.frame_y}:shortest=1,"
        f"{head}ass=subs.ass,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p[v]"
    )


# --- cutting ---------------------------------------------------------------------------------------------------------


def _ffmpeg(opts: ShortOptions) -> list[str]:
    return [opts.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]


def cut_audio_cmd(video: Path, w: Window, out_name: str, opts: ShortOptions) -> list[str]:
    n = round(w.duration_s * SAMPLE_RATE)
    return _ffmpeg(opts) + [
        "-ss", f"{w.start_s:.6f}", "-i", str(video), "-map", "0:a:0", "-vn",
        "-af", f"aresample={SAMPLE_RATE},aformat=sample_fmts=s16:sample_rates={SAMPLE_RATE}:channel_layouts=stereo,"
               f"apad=whole_len={n},atrim=end_sample={n}",
        "-c:a", "pcm_s16le", out_name,
    ]


def encode_cmd(video: Path, w: Window, lay: Layout, theme: Theme, header: bool, out: Path, opts: ShortOptions) -> list[str]:
    frames = w.end_frame - w.start_frame
    return _ffmpeg(opts) + [
        "-ss", f"{w.start_s:.6f}", "-i", str(video), "-i", "mix.wav",
        "-filter_complex", video_filter(lay, theme, w.fps, header),
        "-map", "[v]", "-map", "1:a:0", "-frames:v", str(frames), "-r", str(w.fps),
        "-c:v", "libx264", "-preset", opts.preset, "-crf", str(opts.crf), "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
        "-c:a", "aac", "-b:a", opts.audio_bitrate, "-ar", str(SAMPLE_RATE), "-ac", "2",
        "-movflags", "+faststart", "-f", "mp4", str(out),
    ]


def cut(video: Path, w: Window, lines: list[dict[str, Any]], out: Path, theme: Theme, src_size: tuple[int, int],
        opts: ShortOptions) -> Clip:
    """Cut one window into a Short at ``out`` (plus ``out.srt``). Temporary files live next to ``out``."""
    lay = layout(opts.width, opts.height, *src_size)
    with tempfile.TemporaryDirectory(prefix=".shorts-", dir=out.parent) as tmp_name:
        tmp = Path(tmp_name)
        _run(cut_audio_cmd(video, w, "cut.wav", opts), cwd=tmp)
        (tmp / "audio.ffconcat").write_text("ffconcat version 1.0\nfile 'cut.wav'\n", encoding="utf-8")
        ropts = RenderOptions(ffmpeg=opts.ffmpeg, ffprobe=opts.ffprobe)
        loudness = normalize_mix(tmp, "audio.ffconcat", round(w.duration_s * SAMPLE_RATE), "mix.wav", ropts)
        cues = cues_from_lines(clip_lines(lines, w), PORTRAIT_CHARS, act=activity(tmp / "mix.wav", opts.ffmpeg))
        cues = [c for c in cues if c.start_s < w.duration_s]
        cues = [Cue(c.start_s, min(c.end_s, w.duration_s), c.text) for c in cues]
        (tmp / "subs.ass").write_text(ass_file(cues, lay, theme), encoding="utf-8")
        header = bool(w.chapter)
        if header:
            (tmp / "header.txt").write_text(wrap(w.chapter or "", HEADER_CHARS), encoding="utf-8")
        partial = tmp / "short.mp4"
        _run(encode_cmd(video, w, lay, theme, header, partial, opts), cwd=tmp)
        got = count_video_frames(partial, opts.ffprobe)
        if got != w.end_frame - w.start_frame:
            raise ShortsError(f"{out.name}: {got} frame(s), expected {w.end_frame - w.start_frame}")
        os.replace(partial, out)
    out.with_suffix(".srt").write_text(to_srt(cues), encoding="utf-8")
    return Clip(out, w, len(cues), loudness, measure_loudness(out, opts.ffmpeg))


# --- the whole step ----------------------------------------------------------------------------------------------------


def sidecars(video: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The render timeline and the narration lines of a video (lines.json, else the SRT cues)."""
    timeline_path = video.with_suffix(".timeline.json")
    if not timeline_path.is_file():
        raise ShortsError(f"no {timeline_path.name} next to {video.name} (render writes it)")
    try:
        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
        float(timeline["duration_s"])
        int(timeline["fps"])
        [(s["id"], float(s["start_s"])) for s in timeline["scenes"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ShortsError(f"unreadable {timeline_path.name}: {exc}") from exc
    lines_path, srt_path = video.with_suffix(".lines.json"), video.with_suffix(".srt")
    try:
        if lines_path.is_file():
            lines = json.loads(lines_path.read_text(encoding="utf-8"))
        elif srt_path.is_file():
            lines = [{"text": c.text.replace("\n", " "), "start_s": c.start_s, "end_s": c.end_s}
                     for c in parse_srt(srt_path.read_text(encoding="utf-8"))]
        else:
            raise ShortsError(f"no {lines_path.name} or {srt_path.name} next to {video.name}: Shorts need subtitles; "
                              "narrate and render again, or run `motion-studio subtitles --align`")
        lines = [ln for ln in lines if float(ln["end_s"]) > float(ln["start_s"])]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ShortsError(f"unreadable narration timing for {video.name}: {exc}") from exc
    return timeline, lines


def short_name(video: Path, lang: str, n: int) -> str:
    """``rag.en.mp4`` -> ``rag-1.en.mp4``."""
    stem = video.name[: -len(f".{lang}.mp4")] if video.name.endswith(f".{lang}.mp4") else video.stem
    return f"{stem}-{n}.{lang}.mp4"


def find_windows(video: Path, lang: str, scenes_doc: dict[str, Any] | None, opts: ShortOptions,
                 ) -> tuple[list[Window], dict[str, Any], list[dict[str, Any]]]:
    timeline, lines = sidecars(video)
    fps, duration = int(timeline["fps"]), float(timeline["duration_s"])
    chapters = chapter_starts(scenes_doc, timeline, lang)
    marks = place(candidates(timeline, chapters, lines), activity(video, opts.ffmpeg), fps, duration)
    return pick(marks, fps, opts.count, opts.min_s, opts.max_s, chapters), timeline, lines


def make_shorts(video: Path, lang: str, scenes_doc: dict[str, Any] | None = None, opts: ShortOptions | None = None,
                theme: str | None = None, dry_run: bool = False) -> ShortsResult:
    """Cut up to ``opts.count`` Shorts from ``video`` into ``<video folder>/shorts/``."""
    opts = opts or ShortOptions()
    video = Path(video).resolve()
    if video.name.endswith(".burned.mp4"):
        raise ShortsError(f"{video.name} already has subtitles burned in; pass the clean video")
    if not video.is_file():
        raise ShortsError(f"{video} not found")
    windows, timeline, lines = find_windows(video, lang, scenes_doc, opts)
    result = ShortsResult(video, lang, opts.count, float(timeline["duration_s"]), [])
    if dry_run:
        result.clips = [Clip(video.parent / SHORTS_DIR / short_name(video, lang, i), w, 0)
                        for i, w in enumerate(windows, 1)]
        return result
    folder = video.parent / SHORTS_DIR
    folder.mkdir(exist_ok=True)
    stem = short_name(video, lang, 0)[: -len(f"-0.{lang}.mp4")]
    for old in folder.glob(f"{stem}-*.{lang}.*"):  # clips of an earlier cut no longer match this one
        if re.fullmatch(rf"{re.escape(stem)}-\d+\.{re.escape(lang)}\.(mp4|srt)", old.name):
            old.unlink()
    the_theme = get_theme(opts.theme or theme)
    size = video_size(video, opts.ffprobe)
    for i, w in enumerate(windows, 1):
        try:
            result.clips.append(cut(video, w, lines, folder / short_name(video, lang, i), the_theme, size, opts))
        except RenderError as exc:
            raise ShortsError(str(exc)) from exc
    result.report = folder / f"{stem}.{lang}.shorts.json"
    result.report.write_text(result.json(), encoding="utf-8")
    return result
