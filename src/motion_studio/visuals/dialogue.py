"""The ``dialogue`` visual: two original characters on a background, one of them speaking the scene's narration.

The scene gives a background (``scenery``), the two characters (``cast``: an
SVG each for ``her`` and ``him``), who talks (``talk``), and optional text:
a part label, a caption (the current sentence, with a second line such as
the Vietnamese meaning), a board (a word card, a paragraph, a list) and a
pause after the narration (``pause``: ``scale`` x the narration length +
``add_s`` seconds, with a cue such as "Your turn" and a countdown bar).

How the clip is made (ffmpeg only, no Python imaging library):

1. The scene's narration audio is read once and measured per video frame
   (RMS). The speaker's mouth is closed, half open or open by that loudness,
   relative to the clip's own loud level (``mouth_levels``), so the mouth
   follows its own audio and nothing else.
2. Every frame gets a pose per character (``poses``): mouth, eyes open or
   closed (both blink now and then), a nod of the listener while the other
   one speaks, and a glow behind the speaker. Each distinct pose is drawn
   once from the character SVG by switching its groups (``pose_svg``) and
   rasterised by librsvg; poses are cached next to the clips and shared by
   every scene.
3. The background, board, caption and labels are one SVG per scene,
   rasterised once.
4. One ffmpeg run lays the pose sequences (concat demuxer, one entry per run
   of equal poses) over the background; the speaker sways gently while the
   audio lasts; during the pause the cue and a shrinking countdown bar show.

The clip has its own length (narration + tail + pause), so the scene is
stretched to it (``paced`` is False, as for Manim and VHS clips).

A character SVG has a 440 x 700 canvas and the groups ``glow``, ``head``,
``eyes-open``, ``eyes-closed``, ``mouth-0``, ``mouth-1`` and ``mouth-2``
(see ``samples/characters/``). Layout is in 1920 x 1080 units, scaled to the
output size.
"""

from __future__ import annotations

import array
import hashlib
import math
import os
import re
import subprocess
import sys
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from xml.sax.saxutils import escape

from ..theme import Theme
from .base import Adapter, BuildRequest, VisualError, run_tool, which

KIND = "dialogue"
BASE_W, BASE_H = 1920, 1080  # layout units
CHAR_W, CHAR_H = 440, 700  # character canvas
SLOTS = {"her": (20, 380), "him": (1460, 380)}  # top-left corner of each character
BOARD = (470, 96, 980, 640)  # x, y, w, h
CAPTION = (470, 756, 980, 300)
CUE = (40, 236, 400, 104)  # the cue bubble above her head
BAR = (510, 1024, 900, 14)  # countdown bar inside the caption box
REQUIRED_GROUPS = ("glow", "head", "eyes-open", "eyes-closed", "mouth-0", "mouth-1", "mouth-2")
TAIL_S = 0.5  # silence after narration without a pause (render's default tail)
CUE_GAP_S = 0.25  # the pause (cue and countdown) starts this long after the narration ends
MOUTH_HALF = 0.12  # share of the clip's loud level for a half-open mouth
MOUTH_OPEN = 0.45  # share for an open mouth
SILENT_RMS = 0.01  # frames below this (of full scale) never open the mouth
SWAY_PX = 5  # how far the speaker sways down (layout units)
NOD_PX = 8
CHAR_EM = {"regular": 0.58, "bold": 0.67}  # average advance of DejaVu Sans per font size (wrapping estimate)


@dataclass(frozen=True)
class Pose:
    mouth: int  # 0 closed, 1 half, 2 open
    eyes_closed: bool
    nod: bool
    glow: bool

    def name(self) -> str:
        return f"m{self.mouth}e{int(self.eyes_closed)}n{int(self.nod)}g{int(self.glow)}"


# --- timing ------------------------------------------------------------------------------------------------------------


def hold_seconds(pause: dict[str, Any] | None, speech_s: float) -> float:
    """The pause after the narration: ``scale`` x its length + ``add_s``."""
    if not pause:
        return 0.0
    return max(0.0, float(pause.get("scale", 0.0)) * max(0.0, speech_s) + float(pause.get("add_s", 0.0)))


def clip_seconds(visual: dict[str, Any], speech_s: float, target_s: float = 0.0) -> float:
    """Length of the clip, at least ``target_s``: narration + tail, or narration + a short gap + the pause."""
    hold = hold_seconds(visual.get("pause"), speech_s)
    return max(float(target_s), speech_s + (CUE_GAP_S + hold if hold > 0 else TAIL_S))


# --- the mouth follows the audio ---------------------------------------------------------------------------------------


def _pcm16(path: Path) -> tuple[array.array, int]:
    """Mono 16-bit samples of a WAV (stdlib for 16-bit PCM WAV, else ffmpeg)."""
    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() == 2:
                channels, rate = w.getnchannels(), w.getframerate()
                data = array.array("h", w.readframes(w.getnframes()))
                if sys.byteorder == "big":
                    data.byteswap()
                if channels > 1:
                    data = array.array("h", (int(sum(data[i:i + channels]) / channels)
                                             for i in range(0, len(data), channels)))
                return data, rate
    except (wave.Error, EOFError):
        pass
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path), "-vn",
                           "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True, check=False)
    if proc.returncode != 0:
        raise VisualError(f"cannot read the audio {path}: {proc.stderr.decode(errors='replace')[-300:]}")
    data = array.array("h", proc.stdout)
    if sys.byteorder == "big":
        data.byteswap()
    return data, 16000


def envelope(path: Path, fps: int) -> list[float]:
    """RMS of each video frame of the audio, as a share of full scale (0..1)."""
    data, rate = _pcm16(path)
    out = []
    n = len(data)
    frame = 0
    while True:
        a, b = round(frame * rate / fps), round((frame + 1) * rate / fps)
        if a >= n:
            break
        chunk = data[a:min(b, n)]
        out.append(math.sqrt(sum(s * s for s in chunk) / len(chunk)) / 32768.0 if chunk else 0.0)
        frame += 1
    return out


def mouth_levels(env: Sequence[float]) -> list[int]:
    """0 closed, 1 half, 2 open per frame, from the frame's loudness against the clip's loud level.

    The loud level is the 95th percentile of the voiced frames, so one peak
    does not keep the mouth half shut. Single-frame flickers are smoothed.
    """
    voiced = sorted(e for e in env if e >= SILENT_RMS)
    if not voiced:
        return [0] * len(env)
    loud = voiced[min(len(voiced) - 1, int(0.95 * len(voiced)))]
    levels = []
    for e in env:
        if e < SILENT_RMS:
            levels.append(0)
        elif e >= MOUTH_OPEN * loud:
            levels.append(2)
        elif e >= MOUTH_HALF * loud:
            levels.append(1)
        else:
            levels.append(0)
    for i in range(1, len(levels) - 1):
        if levels[i - 1] == levels[i + 1] != levels[i]:
            levels[i] = levels[i - 1]
    return levels


def poses(levels: Sequence[int], frames: int, fps: int, talk: str) -> dict[str, list[Pose]]:
    """A pose per frame for each character: the speaker's mouth from ``levels``, blinks, the listener's nods."""
    blink_len = max(1, round(0.13 * fps))
    period = {"her": max(blink_len + 1, round(3.7 * fps)), "him": max(blink_len + 1, round(4.3 * fps))}
    phase = {"her": round(1.1 * fps), "him": round(2.3 * fps)}
    nod_period, nod_len = max(2, round(2.6 * fps)), max(1, round(0.33 * fps))
    recent = max(1, round(0.5 * fps))
    out: dict[str, list[Pose]] = {"her": [], "him": []}
    for i in range(frames):
        speaking_lately = any(levels[j] > 0 for j in range(max(0, i - recent), min(i + 1, len(levels))))
        for name in ("her", "him"):
            talker = name == talk
            mouth = levels[i] if talker and i < len(levels) else 0
            blink = (i + phase[name]) % period[name] < blink_len
            nod = (not talker) and speaking_lately and (i + nod_period // 3) % nod_period < nod_len
            out[name].append(Pose(mouth=mouth, eyes_closed=blink, nod=nod, glow=talker))
    return out


def runs(items: Iterable[Any]) -> list[tuple[Any, int]]:
    """Run-length encoding: ``[a, a, b]`` -> ``[(a, 2), (b, 1)]``."""
    out: list[list[Any]] = []
    for item in items:
        if out and out[-1][0] == item:
            out[-1][1] += 1
        else:
            out.append([item, 1])
    return [(a, n) for a, n in out]


# --- character poses ---------------------------------------------------------------------------------------------------


def _group_tag(text: str, gid: str) -> re.Match[str]:
    match = re.search(rf'<g\b[^>]*\bid="{re.escape(gid)}"[^>]*>', text)
    if not match:
        raise VisualError(f"the character SVG has no group id=\"{gid}\" (needs {', '.join(REQUIRED_GROUPS)})")
    return match


def _set_attr(text: str, gid: str, name: str, value: str | None) -> str:
    match = _group_tag(text, gid)
    tag = re.sub(rf'\s{name}="[^"]*"', "", match.group(0))
    if value is not None:
        tag = tag[:-1].rstrip("/") + f' {name}="{value}"' + (">" if not tag.endswith("/>") else "/>")
    return text[:match.start()] + tag + text[match.end():]


def check_character(text: str) -> None:
    for gid in REQUIRED_GROUPS:
        _group_tag(text, gid)


def pose_svg(text: str, pose: Pose) -> str:
    """The character SVG with only the groups of ``pose`` shown."""
    shown = {
        "glow": pose.glow, "eyes-open": not pose.eyes_closed, "eyes-closed": pose.eyes_closed,
        "mouth-0": pose.mouth == 0, "mouth-1": pose.mouth == 1, "mouth-2": pose.mouth == 2,
    }
    for gid, on in shown.items():
        text = _set_attr(text, gid, "display", "inline" if on else "none")
    return _set_attr(text, "head", "transform", f"translate(0 {NOD_PX}) rotate(2 220 360)" if pose.nod else None)


# --- text layout -------------------------------------------------------------------------------------------------------


Segment = tuple[str, bool]  # text and bold
RichWord = tuple[Segment, ...]


def rich(text: str) -> list[RichWord]:
    """Words of ``text`` as bold/plain segments: ``**x**`` marks bold (accent colour), also inside a word
    (``post**s**``); single ``*`` (italic in Markdown) is dropped."""
    words: list[RichWord] = []
    bold = False
    for token in text.split():
        segs: list[Segment] = []
        for k, piece in enumerate(token.split("**")):
            if k:
                bold = not bold
            piece = piece.replace("*", "")
            if piece:
                segs.append((piece, bold))
        if segs:
            words.append(tuple(segs))
    return words


def _width(word: RichWord, size: float) -> float:
    return sum(len(t) * size * CHAR_EM["bold" if b else "regular"] for t, b in word)


def wrap_rich(words: list[RichWord], size: float, max_w: float) -> list[list[RichWord]]:
    lines: list[list[RichWord]] = []
    current: list[RichWord] = []
    width = 0.0
    space = size * 0.32
    for word in words:
        w = _width(word, size)
        if current and width + space + w > max_w:
            lines.append(current)
            current, width = [], 0.0
        width += (space if current else 0.0) + w
        current.append(word)
    if current:
        lines.append(current)
    return lines


@dataclass
class Block:
    """One paragraph of text in a panel: style, size, colours and the wrapped lines."""

    lines: list[list[RichWord]]
    size: float
    color: str
    bold_color: str
    weight: str = "normal"
    italic: bool = False
    gap: float = 0.0  # space before the block
    indent: float = 0.0
    bullet: bool = False


def _tspans(line: list[RichWord], block: Block) -> str:
    parts = []
    for n, word in enumerate(line):
        for k, (text, bold) in enumerate(word):
            text = escape(text) + (" " if n < len(line) - 1 and k == len(word) - 1 else "")
            if bold:
                parts.append(f'<tspan font-weight="bold" fill="{block.bold_color}">{text}</tspan>')
            else:
                parts.append(text)
    return "".join(parts)


def _blocks_svg(blocks: list[Block], x: float, y: float, font: str) -> str:
    out = []
    for block in blocks:
        y += block.gap
        for k, line in enumerate(block.lines):
            y += block.size * 1.0
            style = ' font-style="italic"' if block.italic else ""
            bullet = ""
            if block.bullet and k == 0:
                bullet = (f'<circle cx="{x + block.size * 0.3:.1f}" cy="{y - block.size * 0.33:.1f}" '
                          f'r="{block.size * 0.14:.1f}" fill="{block.bold_color}"/>')
            out.append(f'{bullet}<text x="{x + block.indent:.1f}" y="{y:.1f}" font-family="{font}" '
                       f'font-size="{block.size:.1f}" font-weight="{block.weight}" fill="{block.color}"{style} '
                       f'xml:space="preserve">{_tspans(line, block)}</text>')
            y += block.size * 0.34
    return "".join(out)


def _height(blocks: list[Block]) -> float:
    return sum(b.gap + len(b.lines) * b.size * 1.34 for b in blocks)


def _vi_colour(theme: Theme) -> str:
    return "#0369a1" if theme.name == "light" else "#7dd3fc"


BOARD_STYLES = {  # style -> (size, weight, colour key, italic, gap before)
    "big": (84, "bold", "text", False, 6),
    "vi": (40, "normal", "vi", True, 10),
    "body": (34, "normal", "text", False, 14),
    "dim": (32, "normal", "muted", False, 12),
    "bullet": (32, "normal", "text", False, 10),
    "small": (28, "normal", "muted", False, 10),
}


def _layout(items: list[tuple[str, str]], width: float, height: float, theme: Theme) -> list[Block]:
    """Wrap ``(style, text)`` items into blocks that fit ``width`` x ``height``, shrinking the text when needed."""
    colours = {"text": theme.text, "muted": theme.muted, "vi": _vi_colour(theme)}
    scale = 1.0
    while True:
        blocks = []
        for n, (style, text) in enumerate(items):
            size, weight, colour, italic, gap = BOARD_STYLES.get(style, BOARD_STYLES["body"])
            size *= scale
            indent = size * 0.9 if style == "bullet" else 0.0
            blocks.append(Block(lines=wrap_rich(rich(text), size, width - indent), size=size, color=colours[colour],
                                bold_color=theme.accent, weight=weight, italic=italic,
                                gap=0.0 if n == 0 else gap * scale, indent=indent, bullet=style == "bullet"))
        if _height(blocks) <= height or scale <= 0.45:
            return blocks
        scale *= 0.92


CHIP = (30, 52)  # font size and height of a label chip (part label, speaker's name)
AI_CHIP = (20, 36)  # the AI disclosure chips ("AI voice", "Made with AI"): smaller, top right
AI_CHIP_OPACITY = 0.55  # and fainter, but always shown


def chip_svg(x: float, y: float, text: str, theme: Theme, fill: str, colour: str, right: bool = False,
             small: bool = False, fill_opacity: float = 1.0) -> tuple[str, float]:
    """A rounded label (SVG) and its width; ``small`` is the faint AI disclosure chip."""
    size, height = AI_CHIP if small else CHIP
    pad = 22 * size / CHIP[0]
    w = _width(((text, True),), size) + 2 * pad
    if right:
        x -= w
    opacity = f' fill-opacity="{fill_opacity}"' if fill_opacity < 1 else ""
    svg = (f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{height}" rx="{height / 2:g}" fill="{fill}"{opacity}/>'
           f'<text x="{x + pad:.1f}" y="{y + height / 2 + size * 0.37:.1f}" font-family="{theme.font}" '
           f'font-size="{size}" font-weight="bold" fill="{colour}">{escape(text)}</text>')
    if small:
        svg = f'<g opacity="{AI_CHIP_OPACITY}">{svg}</g>'
    return svg, w


def _chip(x: float, y: float, text: str, theme: Theme, fill: str, colour: str, right: bool = False,
          small: bool = False) -> str:
    return chip_svg(x, y, text, theme, fill, colour, right, small)[0]


def _inner_svg(text: str) -> str:
    """The content of an SVG file without its outer ``<svg>`` element (for embedding a background)."""
    start = re.search(r"<svg\b[^>]*>", text)
    end = text.rfind("</svg>")
    if not start or end < start.end():
        raise VisualError("the scenery is not an SVG file")
    return re.sub(r"<!--.*?-->", "", text[start.end():end], flags=re.S)


def scene_svg(visual: dict[str, Any], scenery: str, theme: Theme, width: int, height: int) -> str:
    """Background, part label, AI voice label, board and caption of one scene, as one SVG."""
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {BASE_W} {BASE_H}"><g>{_inner_svg(scenery)}</g>']
    if visual.get("part"):
        parts.append(_chip(40, 28, visual["part"], theme, theme.text, theme.background))
    if visual.get("label"):
        parts.append(_chip(BASE_W - 40, 36, visual["label"], theme, theme.accent, "#ffffff", right=True,
                           small=True))
    board = visual.get("board")
    if board:
        x, y, w, h = BOARD
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="28" fill="{theme.surface}" '
                     f'stroke="{theme.border}" stroke-width="4"/>')
        inner_y = y + 30
        if board.get("title"):
            parts.append(f'<text x="{x + 44}" y="{y + 62}" font-family="{theme.font}" font-size="32" '
                         f'font-weight="bold" fill="{theme.accent}">{escape(board["title"])}</text>')
            parts.append(f'<rect x="{x + 44}" y="{y + 80}" width="{w - 88}" height="3" fill="{theme.border}"/>')
            inner_y = y + 96
        items = [(line.get("style", "body"), line["text"]) for line in board.get("lines", [])]
        blocks = _layout(items, w - 88, y + h - 30 - inner_y, theme)
        parts.append(_blocks_svg(blocks, x + 44, inner_y, theme.font))
    caption = visual.get("caption")
    if caption:
        x, y, w, h = CAPTION
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="28" fill="{theme.surface}" '
                     f'fill-opacity="0.96" stroke="{theme.border}" stroke-width="4"/>')
        items = [("caption", caption["text"])] + ([("caption-sub", caption["sub"])] if caption.get("sub") else [])
        blocks = _caption_layout(items, w - 88, h - 70, theme)
        top = y + 26 + max(0.0, (h - 70 - _height(blocks)) / 2)
        parts.append(_blocks_svg(blocks, x + 44, top, theme.font))
    parts.append("</svg>")
    return "".join(parts)


def _caption_layout(items: list[tuple[str, str]], width: float, height: float, theme: Theme) -> list[Block]:
    scale = 1.0
    while True:
        blocks = []
        for style, text in items:
            main = style == "caption"
            size = (44 if main else 32) * scale
            blocks.append(Block(lines=wrap_rich(rich(text), size, width), size=size,
                                color=theme.text if main else _vi_colour(theme), bold_color=theme.accent,
                                weight="normal", italic=not main, gap=0.0 if main else 12 * scale))
        if _height(blocks) <= height or scale <= 0.45:
            return blocks
        scale *= 0.92


def cue_svg(text: str, theme: Theme, width: int, height: int) -> str:
    """The pause cue: a bubble above her head and the empty track of the countdown bar (transparent elsewhere)."""
    x, y, w, h = CUE
    bx, by, bw, bh = BAR
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {BASE_W} {BASE_H}">'
            f'<path d="M{x + 150} {y + h - 2} L{x + 196} {y + h + 46} L{x + 230} {y + h - 2} Z" fill="{theme.accent}"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="30" fill="{theme.accent}"/>'
            f'<text x="{x + w / 2}" y="{y + h / 2 + 16}" text-anchor="middle" font-family="{theme.font}" '
            f'font-size="{46 if len(text) <= 14 else 34}" font-weight="bold" fill="#ffffff">{escape(text)}</text>'
            f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="{bh / 2}" fill="{theme.border}" '
            f'fill-opacity="0.45"/></svg>')


# --- the adapter ---------------------------------------------------------------------------------------------------------


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


def _rasterise(svg: str, out: Path, work: Path, size: tuple[int, int] | None = None) -> Path:
    """SVG text -> PNG with ffmpeg's librsvg decoder; atomic, so a shared cache never holds half a file."""
    if out.is_file():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    src = work / f"{out.stem}.svg"
    src.write_text(svg, encoding="utf-8")
    tmp = out.with_name(f".{out.stem}.{os.getpid()}.{threading.get_ident()}.png")  # builds may run in threads
    dims = ["-width", str(size[0]), "-height", str(size[1]), "-keep_ar", "0"] if size else []
    try:
        # ffmpeg's librsvg decoder hands over cairo's premultiplied alpha as if it were straight alpha, which
        # darkens every half-transparent pixel (glow, anti-aliased edges); unpremultiply restores the colours.
        run_tool(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *dims, "-i", str(src),
                  "-vf", "unpremultiply=inplace=1", "-frames:v", "1", str(tmp)], cwd=work, what="rasterising an SVG")
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def _concat(entries: list[tuple[Path, int]], fps: int) -> str:
    lines = ["ffconcat version 1.0"]
    for path, count in entries:
        name = str(path).replace("'", "'\\''")
        lines += [f"file '{name}'", f"duration {count / fps:.6f}"]
    last = str(entries[-1][0]).replace("'", "'\\''")
    lines.append(f"file '{last}'")  # the demuxer ignores the last duration; repeat the last image
    return "\n".join(lines) + "\n"


class DialogueAdapter(Adapter):
    kind = KIND
    version = "2"  # 2: a smaller, fainter AI voice chip
    paced = False  # the clip has its own length: narration + tail + pause
    reads_run = True  # the build sees the target length and the language (through the audio)

    def missing(self) -> str | None:
        if not (which("ffmpeg") and which("ffprobe")):
            return "ffmpeg and ffprobe must be on PATH"
        return None

    def inputs(self, req: BuildRequest) -> list[Path]:
        files = [req.base_dir / req.visual["scenery"], *(req.base_dir / req.visual["cast"][k] for k in ("her", "him"))]
        return [p.resolve() for p in files] + ([Path(req.audio)] if req.audio is not None else [])

    def build(self, req: BuildRequest, out: Path, work: Path) -> None:
        visual, fps, w, h = req.visual, req.fps, req.width, req.height
        talk = visual.get("talk", "her")
        try:
            scenery = (req.base_dir / visual["scenery"]).read_text(encoding="utf-8")
            cast = {k: (req.base_dir / visual["cast"][k]).read_text(encoding="utf-8") for k in ("her", "him")}
        except OSError as exc:
            raise VisualError(f"scene {req.scene_id!r}: {exc}") from exc
        for text in cast.values():
            check_character(text)
        env = envelope(Path(req.audio), fps) if req.audio is not None else []
        speech_s = len(env) / fps
        levels = mouth_levels(env)
        length = clip_seconds(visual, speech_s, req.target_s)
        frames = max(1, math.ceil(length * fps - 1e-6))
        sprites = work.parent / "dialogue-sprites"
        sx, sy = w / BASE_W, h / BASE_H
        char_size = (max(2, round(CHAR_W * sx)), max(2, round(CHAR_H * sy)))
        theme_key = req.theme.json()
        lists = {}
        for name, seq in poses(levels, frames, fps, talk).items():
            entries = []
            for pose, count in runs(seq):
                key = _digest(cast[name], f"{char_size}", pose.name())
                png = _rasterise(pose_svg(cast[name], pose), sprites / f"{name}-{key}.png", work, char_size)
                entries.append((png, count))
            lists[name] = work / f"{name}.ffconcat"
            lists[name].write_text(_concat(entries, fps), encoding="utf-8")
        background = _rasterise(scene_svg(visual, scenery, req.theme, w, h), work / "background.png", work)
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
               "-loop", "1", "-framerate", str(fps), "-i", str(background),
               "-f", "concat", "-safe", "0", "-i", str(lists["her"]),
               "-f", "concat", "-safe", "0", "-i", str(lists["him"])]
        audio_end = f"{speech_s:.3f}"
        graph = [f"[0]format=rgba[bg]", f"[1]fps={fps},format=rgba[c0]", f"[2]fps={fps},format=rgba[c1]"]
        last = "bg"
        for n, name in enumerate(("her", "him")):
            x, y = SLOTS[name]
            px, py = round(x * sx), round(y * sy)
            if name == talk:
                sway = f"{SWAY_PX * sy:.2f}*(1-cos(2*PI*t/1.6))/2*lt(t\\,{audio_end})"
                ypos = f"'{py}+{sway}'"
            else:
                ypos = str(py)
            graph.append(f"[{last}][c{n}]overlay=x={px}:y={ypos}:eval=frame:format=auto[v{n}]")
            last = f"v{n}"
        cue = (visual.get("pause") or {}).get("cue")
        hold = hold_seconds(visual.get("pause"), speech_s)
        if cue and hold > 0:
            start = speech_s + CUE_GAP_S
            span = max(0.5, frames / fps - start - CUE_GAP_S)
            png = _rasterise(cue_svg(cue, req.theme, w, h), sprites / f"cue-{_digest(cue, theme_key, f'{w}x{h}')}.png",
                             work)
            cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(png)]
            bx, by, bw, bh = (round(BAR[0] * sx), round(BAR[1] * sy), max(2, round(BAR[2] * sx)),
                              max(2, round(BAR[3] * sy)))
            accent = "0x" + req.theme.accent.lstrip("#")
            graph += [
                f"[3]format=rgba[cue]",
                f"[{last}][cue]overlay=0:0:enable='gte(t\\,{start:.3f})':format=auto[q0]",
                f"color=c={accent}:s={bw}x{bh}:r={fps},format=rgba[bar]",
                f"color=c=0x000000@0:s={bw}x{bh}:r={fps},format=rgba[track]",
                f"[track][bar]overlay=x='{bw}*min(1\\,max(0\\,(t-{start:.3f})/{span:.3f}))':eval=frame[cd]",
                f"[q0][cd]overlay=x={bx}:y={by}:enable='gte(t\\,{start:.3f})':format=auto[q1]",
            ]
            last = "q1"
        graph.append(f"[{last}]format=yuv420p[v]")
        cmd += ["-filter_complex", ";".join(graph), "-map", "[v]", "-frames:v", str(frames), "-r", str(fps), "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(out)]
        run_tool(cmd, cwd=work, what=f"the dialogue clip of scene {req.scene_id!r}")


# --- the character sheet -----------------------------------------------------------------------------------------------

SHEET_POSES = (("mouth closed", Pose(0, False, False, False)), ("mouth half open", Pose(1, False, False, False)),
               ("mouth open", Pose(2, False, False, True)), ("blink and nod", Pose(0, True, True, False)))
SHEET_SCENERY = ("classroom", "interview", "exam", "desk")


def _nested(svg: str, x: float, y: float, w: float, h: float, view: tuple[int, int]) -> str:
    return (f'<svg x="{x}" y="{y}" width="{w}" height="{h}" viewBox="0 0 {view[0]} {view[1]}">'
            f"{_inner_svg(svg)}</svg>")


def character_sheet(folder: Path) -> str:
    """One SVG that shows both characters in every pose the dialogue visual uses, and the four backgrounds.

    ``samples/characters/sheet.svg`` is this function's output (a test keeps the two in step).
    """
    folder = Path(folder)
    cast = {name: (folder / f"{name}.svg").read_text(encoding="utf-8") for name in ("her", "him")}
    font = "DejaVu Sans"
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1420" viewBox="0 0 1920 1420">',
           "<!-- Character sheet of the English lessons: generated by motion_studio.visuals.dialogue.character_sheet "
           "from her.svg, him.svg and the backgrounds in this folder. -->",
           '<rect width="1920" height="1420" fill="#f8fafc"/>',
           f'<text x="60" y="70" font-family="{font}" font-size="44" font-weight="bold" fill="#0f172a">'
           "Lan (her) and Minh (him): original characters of the English lessons</text>"]
    for row, (name, label) in enumerate((("her", "Lan: teacher, interviewer, examiner (Kokoro af_heart)"),
                                         ("him", "Minh: learner, candidate (Kokoro am_michael)"))):
        y = 110 + row * 465
        out.append(f'<text x="60" y="{y + 30}" font-family="{font}" font-size="30" fill="#475569">'
                   f"{escape(label)}</text>")
        for col, (caption, pose) in enumerate(SHEET_POSES):
            x = 60 + col * 460
            out.append(_nested(pose_svg(cast[name], pose), x + 80, y + 50, 240, 382, (CHAR_W, CHAR_H)))
            out.append(f'<text x="{x + 200}" y="{y + 466}" text-anchor="middle" font-family="{font}" '
                       f'font-size="24" fill="#0f172a">{escape(caption)}</text>')
    for col, name in enumerate(SHEET_SCENERY):
        x, y = 60 + col * 460, 1080
        out.append(_nested((folder / f"{name}.svg").read_text(encoding="utf-8"), x, y, 420, 236, (BASE_W, BASE_H)))
        out.append(f'<rect x="{x}" y="{y}" width="420" height="236" fill="none" stroke="#94a3b8" stroke-width="3"/>')
        out.append(f'<text x="{x + 210}" y="{y + 276}" text-anchor="middle" font-family="{font}" font-size="24" '
                   f'fill="#0f172a">{name}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"
