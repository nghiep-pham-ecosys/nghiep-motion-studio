"""The ``illustrated`` visual: one still of the lesson library, a slow camera move to the speaker, a big speech bubble.

The scene gives the still (``image``, from the library that
``motion-studio images-library`` draws), the point the camera moves to
(``focus``: a share of width and height, at the speaker's face), who talks
(``talk``: the bubble sits on that side and its tail points at ``focus``)
and the same optional text as the ``dialogue`` kind: the speaker's name, a
part label, labels at the top right (``label``: "AI voice", ``badge``: "Made
with AI"), the caption (current sentence, with a Vietnamese line under it)
in the bubble, a board (a word card or a list) as a card in the bottom corner
on the other side, beside the bubble (faces are in the upper half, so it
never hides the listener of a two-shot), and a pause with a cue and a countdown bar.

How the clip is made (ffmpeg only):

1. The still is scaled to twice the output size and ``zoompan`` pushes in
   slowly (ease-out) and towards ``focus``, kept inside the picture, so the
   half-pixel steps do not judder.
2. The bubble, the board and the labels are one transparent SVG per scene,
   rasterised once and laid over the move.
3. During the pause the cue shows above the bubble and a bar fills inside it.

The clip lasts narration + 0.5 s, or narration + 0.25 s + the pause (as the
``dialogue`` kind), and stretches its scene.
"""

from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from ..theme import Theme
from . import dialogue as D
from .base import Adapter, BuildRequest, VisualError, run_tool, which

KIND = "illustrated"
BASE_W, BASE_H = D.BASE_W, D.BASE_H
BUBBLE_W = 1180
BUBBLE_MARGIN = 70  # from the side of the frame
BUBBLE_BOTTOM = 1040
BUBBLE_MAX_H = 360
BUBBLE_PAD = (48, 40)  # x, y inside the bubble
TAIL_H = 56
CAPTION_SIZE, SUB_SIZE = 52, 38
BAR_H = 12
CARD_GAP = 30  # between the bubble and the card
CARD_W = BASE_W - 2 * BUBBLE_MARGIN - BUBBLE_W - CARD_GAP  # the card fills the corner beside the bubble
CARD_MAX_H = 470
CUE_W, CUE_H = 360, 96
ZOOM_MAX = 0.12  # at most 12 % closer
ZOOM_PER_S = 0.012
ZOOM_MIN = 0.03
PARAGRAPH_CHARS = 160  # a board of plain text longer than this is the paragraph the bubble already shows


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float


# --- the camera move ------------------------------------------------------------------------------------------------


def zoom_amount(seconds: float) -> float:
    """How much closer the camera gets over a clip: slow, more for a longer line, never more than ``ZOOM_MAX``."""
    return min(ZOOM_MAX, ZOOM_MIN + ZOOM_PER_S * max(0.0, seconds))


def zoompan(focus: tuple[float, float], frames: int, fps: int, w: int, h: int) -> str:
    """zoompan over a 2w x 2h input: ease-out push-in towards ``focus``, the window kept inside the picture."""
    last = max(frames - 1, 1)
    amount = zoom_amount(frames / fps)
    fx, fy = (min(1.0, max(0.0, float(v))) for v in focus)
    z = f"1+{amount:.4f}*(1-pow(1-on/{last}\\,2))"
    x = f"max(0\\,min(iw-iw/zoom\\,{fx:.4f}*iw-iw/zoom/2))"
    y = f"max(0\\,min(ih-ih/zoom\\,{fy:.4f}*ih-ih/zoom/2))"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps}"


# --- layout ---------------------------------------------------------------------------------------------------------


def compact_board(board: dict[str, Any] | None) -> dict[str, Any] | None:
    """The board as a card, or None when it only repeats the paragraph around the line (the bubble shows the line)."""
    if not board or not board.get("lines"):
        return None
    lines = board["lines"]
    plain = all(line.get("style", "body") in ("body", "dim") for line in lines)
    if plain and sum(len(line["text"]) for line in lines) > PARAGRAPH_CHARS:
        return None
    return board


def bubble_layout(caption: dict[str, str], theme: Theme, talk: str, cue: bool = False
                  ) -> tuple[Box, list[D.Block]]:
    """The bubble box (on the speaker's side, bottom of the frame) and its wrapped text, shrunk to fit."""
    width = BUBBLE_W - 2 * BUBBLE_PAD[0]
    room = BUBBLE_MAX_H - 2 * BUBBLE_PAD[1] - (BAR_H + 14 if cue else 0)
    items = [("caption", caption["text"])] + ([("sub", caption["sub"])] if caption.get("sub") else [])
    scale = 1.0
    while True:
        blocks = []
        for style, text in items:
            main = style == "caption"
            size = (CAPTION_SIZE if main else SUB_SIZE) * scale
            blocks.append(D.Block(lines=D.wrap_rich(D.rich(text), size, width), size=size,
                                  color=theme.text if main else D._vi_colour(theme), bold_color=theme.accent,
                                  weight="normal", italic=not main, gap=0.0 if main else 14 * scale))
        if D._height(blocks) <= room or scale <= 0.45:
            break
        scale *= 0.92
    h = D._height(blocks) + 2 * BUBBLE_PAD[1] + (BAR_H + 14 if cue else 0)
    x = BUBBLE_MARGIN if talk == "her" else BASE_W - BUBBLE_MARGIN - BUBBLE_W
    return Box(x, BUBBLE_BOTTOM - h, BUBBLE_W, h), blocks


def tail_points(box: Box, focus: tuple[float, float]) -> tuple[tuple[float, float], ...]:
    """A triangle on the bubble's top edge that points up at the speaker (``focus``)."""
    fx = focus[0] * BASE_W
    tx = min(box.x + box.w - 110, max(box.x + 110, fx))
    lean = max(-40.0, min(40.0, (fx - tx) * 0.5))
    return (tx - 34, box.y + 2), (tx + 34, box.y + 2), (tx + lean, box.y - TAIL_H)


def card_box(talk: str, height: float) -> Box:
    """The board card: bottom corner on the listener's side, level with the bubble's bottom."""
    h = min(CARD_MAX_H, height)
    left = BASE_W - BUBBLE_MARGIN - CARD_W if talk == "her" else BUBBLE_MARGIN
    return Box(left, BUBBLE_BOTTOM - h, CARD_W, h)


def title_size(title: str, width: float, size: float = 32) -> float:
    """The card title's font size: 32, smaller when the title would run past the card."""
    return min(size, width / max(1e-6, D._width(((title, True),), 1.0)))


def card_layout(items: list[tuple[str, str]], width: float, height: float, theme: Theme) -> list[D.Block]:
    """The board's text, shrunk until it fits the card's height and its longest word fits the width."""
    blocks = D._layout(items, width, height, theme)
    def width_of(word: D.RichWord, b: D.Block) -> float:  # a bold block's words are bold even without ** marks
        return D._width(tuple((text, bold or b.weight == "bold") for text, bold in word), b.size)

    widest = max((width_of(word, b) + b.indent for b in blocks for line in b.lines for word in line), default=0.0)
    if widest <= width:
        return blocks
    scale = width / widest  # shrink once more so a long word (a big term) stays inside the card
    shrunk = []
    for b in blocks:
        size = b.size * scale
        shrunk.append(D.Block(lines=D.wrap_rich([w for line in b.lines for w in line], size, width - b.indent * scale),
                              size=size, color=b.color, bold_color=b.bold_color, weight=b.weight, italic=b.italic,
                              gap=b.gap * scale, indent=b.indent * scale, bullet=b.bullet))
    return shrunk


def cue_box(bubble: Box, talk: str) -> Box:
    """The cue sits on the bubble's top edge, at the end away from the tail."""
    x = bubble.x + bubble.w - CUE_W - 40 if talk == "her" else bubble.x + 40
    return Box(x, bubble.y - CUE_H * 0.62, CUE_W, CUE_H)


def bar_box(bubble: Box) -> Box:
    return Box(bubble.x + BUBBLE_PAD[0], bubble.y + bubble.h - BUBBLE_PAD[1] + 8, bubble.w - 2 * BUBBLE_PAD[0], BAR_H)


def _chip(x: float, y: float, text: str, theme: Theme, fill: str, colour: str, right: bool = False,
          small: bool = False) -> tuple[str, float]:
    return D.chip_svg(x, y, text, theme, fill, colour, right, small, fill_opacity=0.92)


def overlay_svg(visual: dict[str, Any], theme: Theme, width: int, height: int) -> str:
    """Labels, the board card and the speech bubble of one scene, on a transparent canvas."""
    talk = visual.get("talk", "her")
    focus = tuple(visual.get("focus", (0.35 if talk == "her" else 0.65, 0.4)))
    cue = bool((visual.get("pause") or {}).get("cue"))
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {BASE_W} {BASE_H}">',
             '<defs><filter id="shadow" x="-10%" y="-10%" width="120%" height="140%">'
             '<feDropShadow dx="0" dy="6" stdDeviation="10" flood-color="#0f172a" flood-opacity="0.28"/>'
             '</filter></defs>']
    if visual.get("part"):
        parts.append(_chip(40, 28, visual["part"], theme, theme.text, theme.background)[0])
    right = BASE_W - 40
    for key, fill, colour in (("label", theme.accent, "#ffffff"), ("badge", theme.text, theme.background)):
        if visual.get(key):
            svg, w = _chip(right, 36, visual[key], theme, fill, colour, right=True, small=True)
            parts.append(svg)
            right -= w + 10
    board = compact_board(visual.get("board"))
    if board:
        inner_top = 96 if board.get("title") else 36
        items = [(line.get("style", "body"), line["text"]) for line in board.get("lines", [])]
        blocks = card_layout(items, CARD_W - 80, CARD_MAX_H - inner_top - 34, theme)
        box = card_box(talk, inner_top + D._height(blocks) + 34)
        parts.append(f'<rect x="{box.x}" y="{box.y}" width="{box.w}" height="{box.h:.1f}" rx="28" fill="{theme.surface}" '
                     f'fill-opacity="0.94" stroke="{theme.border}" stroke-width="3" filter="url(#shadow)"/>')
        if board.get("title"):
            size = title_size(board["title"], box.w - 80)
            parts.append(f'<text x="{box.x + 40}" y="{box.y + 60}" font-family="{theme.font}" font-size="{size:.1f}" '
                         f'font-weight="bold" fill="{theme.accent}">{escape(board["title"])}</text>')
            parts.append(f'<rect x="{box.x + 40}" y="{box.y + 78}" width="{box.w - 80}" height="3" fill="{theme.border}"/>')
        parts.append(D._blocks_svg(blocks, box.x + 40, box.y + inner_top, theme.font))
    caption = visual.get("caption")
    if caption:
        box, blocks = bubble_layout(caption, theme, talk, cue)
        tail = " ".join(f"{px:.1f},{py:.1f}" for px, py in tail_points(box, focus))
        parts.append(f'<g filter="url(#shadow)"><polygon points="{tail}" fill="{theme.surface}"/>'
                     f'<rect x="{box.x}" y="{box.y:.1f}" width="{box.w}" height="{box.h:.1f}" rx="40" '
                     f'fill="{theme.surface}"/></g>')
        top = box.y + BUBBLE_PAD[1] - 8
        parts.append(D._blocks_svg(blocks, box.x + BUBBLE_PAD[0], top, theme.font))
        if visual.get("name"):
            nx = box.x + 40 if talk == "her" else box.x + box.w - 40
            svg, _ = _chip(nx, box.y - 30, visual["name"], theme, theme.accent, "#ffffff", right=talk != "her")
            parts.append(svg)
    parts.append("</svg>")
    return "".join(parts)


def cue_svg(visual: dict[str, Any], text: str, theme: Theme, width: int, height: int) -> str:
    """The pause cue on the bubble's edge and the empty track of the countdown bar."""
    talk = visual.get("talk", "her")
    bubble, _ = bubble_layout(visual.get("caption") or {"text": " "}, theme, talk, True)
    c, bar = cue_box(bubble, talk), bar_box(bubble)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {BASE_W} {BASE_H}">'
            f'<rect x="{c.x:.1f}" y="{c.y:.1f}" width="{c.w}" height="{c.h}" rx="{c.h / 2}" fill="{theme.accent}"/>'
            f'<text x="{c.x + c.w / 2:.1f}" y="{c.y + c.h / 2 + 16:.1f}" text-anchor="middle" font-family="{theme.font}" '
            f'font-size="{46 if len(text) <= 14 else 34}" font-weight="bold" fill="#ffffff">{escape(text)}</text>'
            f'<rect x="{bar.x:.1f}" y="{bar.y:.1f}" width="{bar.w:.1f}" height="{bar.h}" rx="{bar.h / 2}" '
            f'fill="{theme.border}" fill-opacity="0.6"/></svg>')


# --- the adapter ----------------------------------------------------------------------------------------------------


def speech_seconds(path: Path, fps: int) -> float:
    """Length of the narration, counted in whole video frames as the dialogue kind does."""
    try:
        with wave.open(str(path), "rb") as w:
            seconds = w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError):
        data, rate = D._pcm16(path)
        seconds = len(data) / float(rate)
    return math.ceil(seconds * fps - 1e-9) / fps


class IllustratedAdapter(Adapter):
    kind = KIND
    version = "4"  # 2: the board card sits in the bottom corner; 3: a long card title shrinks; 4: small AI chips
    paced = False  # narration + tail + pause
    reads_run = True

    def missing(self) -> str | None:
        if not (which("ffmpeg") and which("ffprobe")):
            return "ffmpeg and ffprobe must be on PATH"
        return None

    def inputs(self, req: BuildRequest) -> list[Path]:
        files = [(req.base_dir / req.visual["image"]).resolve()]
        return files + ([Path(req.audio)] if req.audio is not None else [])

    def build(self, req: BuildRequest, out: Path, work: Path) -> None:
        visual, fps, w, h = req.visual, req.fps, req.width, req.height
        image = (req.base_dir / visual["image"]).resolve()
        if not image.is_file():
            raise VisualError(f"scene {req.scene_id!r}: the still {visual['image']} is missing")
        talk = visual.get("talk", "her")
        focus = tuple(visual.get("focus", (0.35 if talk == "her" else 0.65, 0.4)))
        speech_s = speech_seconds(Path(req.audio), fps) if req.audio is not None else 0.0
        length = D.clip_seconds(visual, speech_s, req.target_s)
        frames = max(1, math.ceil(length * fps - 1e-6))
        overlay = D._rasterise(overlay_svg(visual, req.theme, w, h), work / "overlay.png", work)
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(image),
               "-loop", "1", "-framerate", str(fps), "-i", str(overlay)]
        graph = [f"[0]scale={2 * w}:{2 * h}:force_original_aspect_ratio=increase:flags=lanczos,"
                 f"crop={2 * w}:{2 * h},setsar=1,{zoompan(focus, frames, fps, w, h)},format=rgba[kb]",
                 "[1]format=rgba[ov]", "[kb][ov]overlay=0:0:format=auto[v0]"]
        last = "v0"
        cue = (visual.get("pause") or {}).get("cue")
        hold = D.hold_seconds(visual.get("pause"), speech_s)
        if cue and hold > 0:
            start = speech_s + D.CUE_GAP_S
            span = max(0.5, frames / fps - start - D.CUE_GAP_S)
            png = D._rasterise(cue_svg(visual, cue, req.theme, w, h), work / "cue.png", work)
            cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(png)]
            bubble, _ = bubble_layout(visual.get("caption") or {"text": " "}, req.theme, talk, True)
            bar = bar_box(bubble)
            sx, sy = w / BASE_W, h / BASE_H
            bx, by = round(bar.x * sx), round(bar.y * sy)
            bw, bh = max(2, round(bar.w * sx)), max(2, round(bar.h * sy))
            accent = "0x" + req.theme.accent.lstrip("#")
            graph += [
                "[2]format=rgba[cue]",
                f"[{last}][cue]overlay=0:0:enable='gte(t\\,{start:.3f})':format=auto[q0]",
                f"color=c={accent}:s={bw}x{bh}:r={fps},format=rgba[bar]",
                f"color=c=0x000000@0:s={bw}x{bh}:r={fps},format=rgba[track]",
                f"[track][bar]overlay=x='-{bw}+{bw}*min(1\\,max(0\\,(t-{start:.3f})/{span:.3f}))':eval=frame[cd]",
                f"[q0][cd]overlay=x={bx}:y={by}:enable='gte(t\\,{start:.3f})':format=auto[q1]",
            ]
            last = "q1"
        graph.append(f"[{last}]format=yuv420p[v]")
        cmd += ["-filter_complex", ";".join(graph), "-map", "[v]", "-frames:v", str(frames), "-r", str(fps), "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(out)]
        run_tool(cmd, cwd=work, what=f"the illustrated clip of scene {req.scene_id!r}")
