"""The ``card`` visual: a full-frame panel (code, program output, a chart, points, a flow or a picture) with the
speaker's still as an inset, the spoken line under it and the AI disclosure chips.

The explainer video (``motion-studio explainer``) shows what Lan and Minh talk about on this card: the code that
runs, the output it printed, the measured number as a bar chart, a list of points, a small flow of boxes and
arrows, or the post's own image. The panel carries its content (code lines are copied into the visual, never
read at build time), so the clip's cache key covers it. ``source`` names where the content comes from (a file
and its lines), shown under the speaker's name.

Layout (1920 x 1080 units): the panel on the left, the speaker's still (a square crop around ``focus``, the face)
at the top right with the name chip under it, the caption across the bottom, the part chip at the top left and
the "AI voice" / "Made with AI" chips at the top right, as in the ``illustrated`` kind.

How the clip is made (ffmpeg only): the panel, chips and caption are one SVG, rasterised once; the still is
cropped and laid over it; an ``image`` panel's picture is fitted into the panel. The clip lasts narration +
0.5 s (the ``dialogue`` timing) and stretches its scene.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from ..theme import Theme
from . import dialogue as D
from .base import Adapter, BuildRequest, VisualError, run_tool, which
from .illustrated import speech_seconds

KIND = "card"
BASE_W, BASE_H = D.BASE_W, D.BASE_H
PANEL = (60, 110, 1360, 690)  # x, y, w, h
INSET = (1460, 110, 400)  # x, y, side of the speaker's still
INSET_CROP = 0.62  # share of the still's height the inset shows (around the face)
CAPTION = (60, 830, 1800, 210)
CAPTION_SIZE = 46
TITLE_SIZE = 34
PANEL_PAD = 40
MONO_EM = 0.602  # advance of DejaVu Sans Mono per font size
CODE_SIZES = (30, 28, 26, 24, 22)  # tried in order until the rows fit
MAX_CODE_ROWS = 18  # rows at the smallest size; a longer window is refused by the builder
PANEL_TYPES = ("code", "output", "chart", "points", "flow", "image")
_KEYWORDS = frozenset(
    "def return for in if else elif import from as with while class not and or is None True False lambda yield try "
    "except raise pass continue break global nonlocal assert del async await".split())
_BUILTINS = frozenset("len sum set sorted range print enumerate next max min list dict str int float tuple zip map "
                      "any all abs round open isinstance super".split())
_TOKEN = re.compile(r"(?P<comment>#.*$)"
                    r"|(?P<string>[rRbBfFuU]{0,2}(?:\"(?:[^\"\\]|\\.)*\"?|'(?:[^'\\]|\\.)*'?))"
                    r"|(?P<number>\b\d+(?:\.\d+)?\b)|(?P<word>[A-Za-z_][A-Za-z0-9_]*)|(?P<other>.)")


# --- code -----------------------------------------------------------------------------------------------------------


def _style(spec: str) -> tuple[str, bool, bool]:
    """``"bold #7c3aed"`` -> (colour, bold, italic)."""
    parts = spec.split()
    return parts[-1], "bold" in parts, "italic" in parts


def code_spans(line: str, theme: Theme) -> list[tuple[str, str, bool, bool]]:
    """One line of Python-like code as ``(text, colour, bold, italic)`` spans (a small highlighter, no Pygments)."""
    code = theme.code
    out: list[tuple[str, str, bool, bool]] = []
    after_def = False
    for m in _TOKEN.finditer(line):
        kind, text = m.lastgroup, m.group(0)
        key = "Text"
        if kind == "comment":
            key = "Comment"
        elif kind == "string":
            key = "String"
        elif kind == "number":
            key = "Number"
        elif kind == "word":
            if text in _KEYWORDS:
                key = "Keyword.Constant" if text in ("None", "True", "False") else "Keyword"
            elif after_def:
                key = "Name.Function"
            elif text in _BUILTINS:
                key = "Name.Builtin"
        elif text in "=+-*/<>%":
            key = "Operator"
        elif text in "()[]{}:,.":
            key = "Punctuation"
        colour, bold, italic = _style(code.get(key, code["Text"]))
        if out and out[-1][1:] == (colour, bold, italic):
            out[-1] = (out[-1][0] + text, colour, bold, italic)
        else:
            out.append((text, colour, bold, italic))
        if kind == "word":
            after_def = text in ("def", "class")
    return out


def wrap_spans(spans: list[tuple[str, str, bool, bool]], width: int) -> list[list[tuple[str, str, bool, bool]]]:
    """Cut a line's spans into rows of at most ``width`` characters, at a space when there is one (continuation rows
    keep the line's indent + 4)."""
    text = "".join(s[0] for s in spans)
    if len(text) <= width:
        return [spans]
    indent = len(text) - len(text.lstrip(" ")) + 4
    cuts, start, room = [], 0, width
    while len(text) - start > room:
        end = start + room
        space = text.rfind(" ", start + 1, end + 1)
        end = space + 1 if space > start + room // 3 else end
        cuts.append((start, end))
        start, room = end, max(8, width - indent)
    cuts.append((start, len(text)))
    rows: list[list[tuple[str, str, bool, bool]]] = []
    for k, (a, b) in enumerate(cuts):
        row: list[tuple[str, str, bool, bool]] = []
        pos = 0
        for chunk, *style in spans:
            lo, hi = max(a, pos), min(b, pos + len(chunk))
            if lo < hi:
                row.append((chunk[lo - pos:hi - pos], *style))
            pos += len(chunk)
        if k and row:
            row.insert(0, (" " * indent, *row[0][1:]))
        rows.append(row)
    return rows


def code_rows(lines: list[str], first_line: int, highlight: list[list[int]], inner_w: float, inner_h: float,
              theme: Theme, dark: bool = False) -> tuple[float, list[tuple[int | None, bool, list[tuple[str, str, bool, bool]]]]]:
    """The font size and the rows ``(line number or None, highlighted, spans)`` of a code or output panel."""
    lit = {n for a, b in highlight for n in range(a, b + 1)}
    tries = []
    for size in CODE_SIZES:
        gutter = 0.0 if dark else size * MONO_EM * 4.5
        width = max(20, int((inner_w - gutter) / (size * MONO_EM)))
        rows = []
        for k, line in enumerate(lines):
            number = first_line + k
            spans = [(line, theme.surface, False, False)] if dark else code_spans(line, theme)
            for j, row in enumerate(wrap_spans(spans, width) if line else [[]]):
                rows.append((number if j == 0 else None, number in lit, row))
        fits = len(rows) * size * 1.42 <= inner_h
        tries.append((float(size), rows, fits, len(rows) == len(lines)))
    best = next((t for t in tries if t[2] and t[3]), None) or next((t for t in tries if t[2]), None) or tries[-1]
    return best[0], best[1]


def _code_svg(panel: dict[str, Any], box: D_Box, theme: Theme, dark: bool) -> str:
    x, y, w, h = box
    size, rows = code_rows(panel.get("lines", []), int(panel.get("first_line", 1)), panel.get("highlight", []),
                           w - 2 * PANEL_PAD, h - 40, theme, dark)
    gutter = 0.0 if dark else size * MONO_EM * 4.5
    out = []
    step = size * 1.42
    top = y + 20
    for k, (number, lit, spans) in enumerate(rows):
        base = top + k * step
        if lit:
            colour = theme.accent
            out.append(f'<rect x="{x + 12:.1f}" y="{base:.1f}" width="{w - 24:.1f}" height="{step:.1f}" rx="6" '
                       f'fill="{colour}" fill-opacity="{0.38 if dark else 0.14}"/>')
        baseline = base + step * 0.74
        if number is not None and not dark:
            out.append(f'<text x="{x + PANEL_PAD + gutter - size * MONO_EM * 1.2:.1f}" y="{baseline:.1f}" '
                       f'text-anchor="end" font-family="{theme.mono}" font-size="{size:.1f}" fill="{theme.muted}">'
                       f'{number}</text>')
        tspans = []
        for text, colour, bold, italic in spans:
            attrs = (' font-weight="bold"' if bold else "") + (' font-style="italic"' if italic else "")
            tspans.append(f'<tspan fill="{colour}"{attrs}>{escape(text)}</tspan>')
        if tspans:
            out.append(f'<text x="{x + PANEL_PAD + gutter:.1f}" y="{baseline:.1f}" font-family="{theme.mono}" '
                       f'font-size="{size:.1f}" xml:space="preserve">{"".join(tspans)}</text>')
    return "".join(out)


# --- chart, points, flow --------------------------------------------------------------------------------------------

D_Box = tuple[float, float, float, float]


def chart_layout(bars: list[dict[str, Any]], scale_max: float | None, width: float) -> list[tuple[str, float, str]]:
    """``(label, bar length, value text)`` per bar; the longest bar (or ``scale_max``) fills ``width``."""
    top = float(scale_max) if scale_max else max((float(b["value"]) for b in bars), default=1.0)
    top = top if top > 0 else 1.0
    return [(b["label"], max(0.0, float(b["value"])) / top * width, b.get("text") or f"{b['value']:g}")
            for b in bars]


def _chart_svg(panel: dict[str, Any], box: D_Box, theme: Theme) -> str:
    x, y, w, h = box
    bars = panel.get("bars", [])
    label_w = 430.0
    area_x = x + PANEL_PAD + label_w
    area_w = w - 2 * PANEL_PAD - label_w - 40
    note_h = 60 if panel.get("note") else 0
    slot = (h - 40 - note_h) / max(1, len(bars))
    bar_h = min(96.0, slot * 0.55)
    out = []
    if panel.get("max"):
        tx = area_x + area_w
        out.append(f'<line x1="{tx:.1f}" y1="{y + 20:.1f}" x2="{tx:.1f}" y2="{y + h - 20 - note_h:.1f}" '
                   f'stroke="{theme.border}" stroke-width="3" stroke-dasharray="10 8"/>')
        out.append(f'<text x="{tx - 10:.1f}" y="{y + 44:.1f}" text-anchor="end" font-family="{theme.font}" font-size="26" '
                   f'fill="{theme.muted}">{escape(_num(panel["max"]))}</text>')
    for k, (label, length, text) in enumerate(chart_layout(bars, panel.get("max"), area_w)):
        cy = y + 20 + slot * k + slot / 2
        colour = theme.palette[k % len(theme.palette)]
        size = 36.0
        lines = D.wrap_rich(D.rich(label), size, label_w - 30)
        for j, line in enumerate(lines[:2]):
            ly = cy + size * 0.36 + (j - (min(2, len(lines)) - 1) / 2) * size * 1.2
            out.append(f'<text x="{x + PANEL_PAD:.1f}" y="{ly:.1f}" font-family="{theme.font}" font-size="{size}" '
                       f'fill="{theme.text}">{escape(" ".join("".join(t for t, _ in wd) for wd in line))}</text>')
        out.append(f'<rect x="{area_x:.1f}" y="{cy - bar_h / 2:.1f}" width="{max(6.0, length):.1f}" height="{bar_h:.1f}" '
                   f'rx="10" fill="{colour}"/>')
        out.append(f'<text x="{area_x + max(6.0, length) + 18:.1f}" y="{cy + 15:.1f}" font-family="{theme.font}" '
                   f'font-size="42" font-weight="bold" fill="{theme.text}">{escape(text)}</text>')
    if panel.get("note"):
        out.append(f'<text x="{x + PANEL_PAD:.1f}" y="{y + h - 30:.1f}" font-family="{theme.font}" font-size="30" '
                   f'font-style="italic" fill="{theme.muted}">{escape(panel["note"])}</text>')
    return "".join(out)


def _num(value: Any) -> str:
    return f"{float(value):g}"


def _points_svg(panel: dict[str, Any], box: D_Box, theme: Theme) -> str:
    x, y, w, h = box
    width, height = w - 2 * PANEL_PAD, h - 50
    items = panel.get("items", [])
    scale = 1.0
    while True:
        blocks = []
        for n, text in enumerate(items):
            size = 44 * scale
            blocks.append(D.Block(lines=D.wrap_rich(D.rich(text), size, width - size * 0.9), size=size,
                                  color=theme.text, bold_color=theme.accent, gap=0.0 if n == 0 else 26 * scale,
                                  indent=size * 0.9, bullet=True))
        if D._height(blocks) <= height or scale <= 0.5:
            break
        scale *= 0.93
    return D._blocks_svg(blocks, x + PANEL_PAD, y + 24, theme.font)


def flow_boxes(columns: list[list[str]], box: D_Box) -> list[list[tuple[float, float, float, float]]]:
    """The box of every node: columns spread across the panel, nodes of a column stacked and centred."""
    x, y, w, h = box
    n = max(1, len(columns))
    gap = 90.0
    col_w = (w - 2 * PANEL_PAD - gap * (n - 1)) / n
    out = []
    for c, nodes in enumerate(columns):
        cx = x + PANEL_PAD + c * (col_w + gap)
        node_h = min(150.0, (h - 60 - 40 * (len(nodes) - 1)) / max(1, len(nodes)))
        total = len(nodes) * node_h + 40 * (len(nodes) - 1)
        top = y + (h - total) / 2
        out.append([(cx, top + k * (node_h + 40), col_w, node_h) for k in range(len(nodes))])
    return out


def _flow_svg(panel: dict[str, Any], box: D_Box, theme: Theme) -> str:
    columns = panel.get("columns", [])
    boxes = flow_boxes(columns, box)
    out = ['<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
           f'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{theme.muted}"/></marker></defs>']
    for c in range(len(boxes) - 1):
        for (ax, ay, aw, ah) in boxes[c]:
            for (bx, by, bw, bh) in boxes[c + 1]:
                out.append(f'<line x1="{ax + aw:.1f}" y1="{ay + ah / 2:.1f}" x2="{bx - 6:.1f}" y2="{by + bh / 2:.1f}" '
                           f'stroke="{theme.muted}" stroke-width="4" marker-end="url(#arrow)"/>')
    for c, (nodes, rects) in enumerate(zip(columns, boxes)):
        colour = theme.palette[c % len(theme.palette)]
        for text, (bx, by, bw, bh) in zip(nodes, rects):
            out.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="20" fill="{colour}" '
                       f'fill-opacity="0.14" stroke="{colour}" stroke-width="4"/>')
            size = 36.0
            bold = [tuple((s, True) for s, _ in wd) for wd in D.rich(text)]
            lines = D.wrap_rich(bold, size, bw - 30)
            while (len(lines) * size * 1.25 > bh - 16 or any(D._width(wd, size) > bw - 30 for ln in lines for wd in ln)) \
                    and size > 20:
                size -= 2
                lines = D.wrap_rich(bold, size, bw - 30)
            total = len(lines) * size * 1.25
            for j, line in enumerate(lines):
                ty = by + (bh - total) / 2 + size * (1.0 + 1.25 * j)
                words = " ".join("".join(t for t, _ in wd) for wd in line)
                out.append(f'<text x="{bx + bw / 2:.1f}" y="{ty:.1f}" text-anchor="middle" font-family="{theme.font}" '
                           f'font-size="{size:.1f}" font-weight="bold" fill="{theme.text}">{escape(words)}</text>')
    return "".join(out)


# --- the whole frame ------------------------------------------------------------------------------------------------


def panel_inner(panel: dict[str, Any]) -> D_Box:
    """The panel's content box, under its title."""
    x, y, w, h = PANEL
    top = 92 if panel.get("title") else 10
    return (x, y + top, w, h - top)


def frame_svg(visual: dict[str, Any], theme: Theme, width: int, height: int) -> str:
    """Background, panel, inset frame, name, source note, caption and chips of one card scene."""
    panel = visual["panel"]
    kind = panel["type"]
    dark = kind == "output"
    x, y, w, h = PANEL
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {BASE_W} {BASE_H}">',
             '<defs><filter id="shadow" x="-10%" y="-10%" width="120%" height="140%">'
             '<feDropShadow dx="0" dy="6" stdDeviation="10" flood-color="#0f172a" flood-opacity="0.18"/></filter></defs>',
             f'<rect width="{BASE_W}" height="{BASE_H}" fill="{theme.background}"/>']
    fill = theme.text if dark else theme.surface
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="28" fill="{fill}" stroke="{theme.border}" '
                 f'stroke-width="3" filter="url(#shadow)"/>')
    if panel.get("title"):
        size = min(TITLE_SIZE, (w - 2 * PANEL_PAD) / max(1e-6, D._width(((panel["title"], True),), 1.0)))
        parts.append(f'<text x="{x + PANEL_PAD}" y="{y + 58}" font-family="{theme.font}" font-size="{size:.1f}" '
                     f'font-weight="bold" fill="{theme.surface if dark else theme.accent}">{escape(panel["title"])}</text>')
        parts.append(f'<rect x="{x + PANEL_PAD}" y="{y + 78}" width="{w - 2 * PANEL_PAD}" height="3" '
                     f'fill="{theme.muted if dark else theme.border}"/>')
    inner = panel_inner(panel)
    if kind in ("code", "output"):
        parts.append(_code_svg(panel, inner, theme, dark))
    elif kind == "chart":
        parts.append(_chart_svg(panel, inner, theme))
    elif kind == "points":
        parts.append(_points_svg(panel, inner, theme))
    elif kind == "flow":
        parts.append(_flow_svg(panel, inner, theme))
    ix, iy, side = INSET
    parts.append(f'<rect x="{ix - 8}" y="{iy - 8}" width="{side + 16}" height="{side + 16}" rx="26" '
                 f'fill="{theme.accent}" filter="url(#shadow)"/>')
    talk_y = iy + side + 30
    if visual.get("name"):
        svg, cw = D.chip_svg(0, 0, visual["name"], theme, theme.accent, "#ffffff")
        parts.append(f'<g transform="translate({ix + (side - cw) / 2:.1f},{talk_y})">{svg}</g>')
    if panel.get("source"):
        blocks = [D.Block(lines=D.wrap_rich(D.rich(panel["source"]), 26, side), size=26, color=theme.muted,
                          bold_color=theme.muted, italic=True)]
        parts.append(D._blocks_svg(blocks, ix, talk_y + 80, theme.font))
    if visual.get("part"):
        parts.append(D.chip_svg(40, 28, visual["part"], theme, theme.text, theme.background)[0])
    right = BASE_W - 40
    for key, chip_fill, colour in (("label", theme.accent, "#ffffff"), ("badge", theme.text, theme.background)):
        if visual.get(key):
            svg, cw = D.chip_svg(right, 36, visual[key], theme, chip_fill, colour, right=True, small=True,
                                 fill_opacity=0.92)
            parts.append(svg)
            right -= cw + 10
    caption = visual.get("caption")
    if caption:
        cx, cy, cw, ch = CAPTION
        parts.append(f'<rect x="{cx}" y="{cy}" width="{cw}" height="{ch}" rx="28" fill="{theme.surface}" '
                     f'stroke="{theme.border}" stroke-width="3" filter="url(#shadow)"/>')
        items = [("caption", caption["text"])] + ([("caption-sub", caption["sub"])] if caption.get("sub") else [])
        scale = 1.0
        while True:
            blocks = [D.Block(lines=D.wrap_rich(D.rich(text), (CAPTION_SIZE if s == "caption" else 34) * scale, cw - 96),
                              size=(CAPTION_SIZE if s == "caption" else 34) * scale,
                              color=theme.text if s == "caption" else D._vi_colour(theme), bold_color=theme.accent,
                              italic=s != "caption", gap=0.0 if s == "caption" else 12 * scale) for s, text in items]
            if D._height(blocks) <= ch - 50 or scale <= 0.5:
                break
            scale *= 0.92
        top = cy + 18 + max(0.0, (ch - 40 - D._height(blocks)) / 2)
        parts.append(D._blocks_svg(blocks, cx + 48, top, theme.font))
    parts.append("</svg>")
    return "".join(parts)


def inset_filter(focus: tuple[float, float], side_px: int) -> str:
    """Crop a square around ``focus`` (the face) from the still and scale it to the inset, kept inside the picture."""
    fx, fy = (min(1.0, max(0.0, float(v))) for v in focus)
    crop = f"ih*{INSET_CROP}"
    cx = f"max(0\\,min(iw-{crop}\\,{fx:.4f}*iw-{crop}/2))"
    cy = f"max(0\\,min(ih-{crop}\\,{fy:.4f}*ih-{crop}/2))"
    return f"crop=w={crop}:h={crop}:x={cx}:y={cy},scale={side_px}:{side_px}:flags=lanczos,setsar=1"


class CardAdapter(Adapter):
    kind = KIND
    version = "1"
    paced = False  # narration + tail, like the dialogue kinds
    reads_run = True

    def missing(self) -> str | None:
        if not (which("ffmpeg") and which("ffprobe")):
            return "ffmpeg and ffprobe must be on PATH"
        return None

    def inputs(self, req: BuildRequest) -> list[Path]:
        files = [(req.base_dir / req.visual["image"]).resolve()]
        if req.visual["panel"].get("src"):
            files.append((req.base_dir / req.visual["panel"]["src"]).resolve())
        return files + ([Path(req.audio)] if req.audio is not None else [])

    def build(self, req: BuildRequest, out: Path, work: Path) -> None:
        visual, fps, w, h = req.visual, req.fps, req.width, req.height
        still = (req.base_dir / visual["image"]).resolve()
        if not still.is_file():
            raise VisualError(f"scene {req.scene_id!r}: the still {visual['image']} is missing")
        panel = visual["panel"]
        if panel.get("type") not in PANEL_TYPES:
            raise VisualError(f"scene {req.scene_id!r}: unknown panel type {panel.get('type')!r}")
        speech_s = speech_seconds(Path(req.audio), fps) if req.audio is not None else 0.0
        length = D.clip_seconds(visual, speech_s, req.target_s)
        frames = max(1, math.ceil(length * fps - 1e-6))
        talk = visual.get("talk", "her")
        focus = tuple(visual.get("focus", (0.35 if talk == "her" else 0.65, 0.4)))
        background = D._rasterise(frame_svg(visual, req.theme, w, h), work / "frame.png", work)
        sx, sy = w / BASE_W, h / BASE_H
        ix, iy, side = INSET
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
               "-loop", "1", "-framerate", str(fps), "-i", str(background),
               "-loop", "1", "-framerate", str(fps), "-i", str(still)]
        graph = ["[0]format=rgba[bg]", f"[1]{inset_filter(focus, max(2, round(side * sx)))},format=rgba[in]",
                 f"[bg][in]overlay=x={round(ix * sx)}:y={round(iy * sy)}:format=auto[v0]"]
        last = "v0"
        if panel["type"] == "image":
            picture = (req.base_dir / panel["src"]).resolve()
            if not picture.is_file():
                raise VisualError(f"scene {req.scene_id!r}: the panel picture {panel['src']} is missing")
            bx, by, bw, bh = panel_inner(panel)
            pw, ph = max(2, round((bw - 2 * PANEL_PAD) * sx)), max(2, round((bh - 30) * sy))
            ox, oy = round((bx + PANEL_PAD) * sx), round(by * sy)
            cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(picture)]
            graph += [f"[2]scale={pw}:{ph}:force_original_aspect_ratio=decrease:flags=lanczos,setsar=1,format=rgba[pic]",
                      f"[{last}][pic]overlay=x='{ox}+({pw}-w)/2':y='{oy}+({ph}-h)/2':format=auto[v1]"]
            last = "v1"
        graph.append(f"[{last}]format=yuv420p[v]")
        cmd += ["-filter_complex", ";".join(graph), "-map", "[v]", "-frames:v", str(frames), "-r", str(fps), "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(out)]
        run_tool(cmd, cwd=work, what=f"the card clip of scene {req.scene_id!r}")
