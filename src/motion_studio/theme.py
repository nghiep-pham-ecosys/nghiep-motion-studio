"""Colour themes for video frames and generated visuals.

One theme sets every colour and font the renderer and the visual adapters
use: the frame background, title-card text, diagram and code colours, the
VHS terminal palette and the burned-in subtitle box. ``light`` is the
default; ``dark`` is kept as an option. A scenes.json picks one with the
top-level ``theme`` field, and ``motion-studio render --theme`` overrides it.

Every text colour meets WCAG 2.x AA (contrast ratio of at least 4.5:1)
against the surface it is drawn on, including the tinted fills of diagram
nodes and code highlights; ``tests/test_theme.py`` checks each pair.

The Manim templates and custom Manim scripts receive the theme as JSON (the
path is in ``MOTION_STUDIO_THEME``); the keys are those of ``Theme.as_dict``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

DEFAULT_THEME = "light"
ENV_VAR = "MOTION_STUDIO_THEME"  # path of the theme JSON a Manim script can read

# Opacities shared by the templates; the contrast test composites with them.
NODE_FILL_OPACITY = 0.16  # diagram node fill (stroke at full colour)
HIGHLIGHT_FILL_OPACITY = 0.12  # code highlight frame fill

# VHS colour names, in the order VHS documents them.
TERMINAL_KEYS = (
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
    "brightBlack", "brightRed", "brightGreen", "brightYellow", "brightBlue", "brightMagenta", "brightCyan", "brightWhite",
)


@dataclass(frozen=True)
class Theme:
    """Every colour and font of one look. Colours are ``#rrggbb``."""

    name: str
    background: str  # frame background (title cards, image pads, Manim and VHS backgrounds)
    surface: str  # panels drawn on the frame: the code window
    border: str  # panel outline (non-text)
    text: str  # titles, node labels, code plain text
    muted: str  # edge labels, arrows, axes, line numbers
    accent: str  # highlight frame, pulse, the ball in the sample (non-text)
    palette: tuple[str, ...]  # diagram node and chart colours (strokes; fills at NODE_FILL_OPACITY)
    font: str
    mono: str
    code: dict[str, str]  # Pygments token type name -> "[bold|italic ]#rrggbb", drawn on ``surface``
    terminal: dict[str, str]  # VHS theme: the 16 ANSI colours plus background, foreground, selection, cursor
    subtitle_text: str
    subtitle_box: str
    subtitle_box_opacity: float

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["palette"] = list(self.palette)
        data["node_fill_opacity"] = NODE_FILL_OPACITY
        data["highlight_fill_opacity"] = HIGHLIGHT_FILL_OPACITY
        return data

    def json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def vhs_theme(self) -> str:
        """The terminal palette as a one-line VHS ``Set Theme`` JSON value."""
        return json.dumps({"name": f"motion-studio-{self.name}", **self.terminal}, separators=(",", ":"))


LIGHT = Theme(
    name="light",
    background="#f8fafc",
    surface="#ffffff",
    border="#94a3b8",
    text="#0f172a",
    muted="#475569",
    accent="#b45309",
    palette=("#0284c7", "#7c3aed", "#059669", "#ea580c", "#db2777", "#a16207"),
    font="DejaVu Sans",
    mono="DejaVu Sans Mono",
    code={
        "Text": "#0f172a",
        "Comment": "italic #475569",
        "Comment.Preproc": "#475569",
        "Keyword": "bold #7c3aed",
        "Keyword.Constant": "#1d4ed8",
        "Keyword.Type": "#0e7490",
        "Operator": "#be123c",
        "Operator.Word": "bold #7c3aed",
        "Punctuation": "#334155",
        "Name": "#0f172a",
        "Name.Builtin": "#0e7490",
        "Name.Builtin.Pseudo": "italic #0e7490",
        "Name.Function": "#1d4ed8",
        "Name.Class": "bold #1d4ed8",
        "Name.Decorator": "#a21caf",
        "Name.Exception": "#be123c",
        "Name.Namespace": "#0f766e",
        "Name.Tag": "#be123c",
        "Name.Attribute": "#854d0e",
        "Name.Variable": "#9a3412",
        "String": "#047857",
        "String.Doc": "italic #047857",
        "String.Escape": "#9a3412",
        "String.Interpol": "#9a3412",
        "Number": "#9a3412",
        "Generic.Deleted": "#be123c",
        "Generic.Inserted": "#047857",
        "Generic.Heading": "bold #0f172a",
        "Generic.Subheading": "bold #475569",
        "Generic.Prompt": "bold #475569",
        "Generic.Output": "#334155",
        "Error": "#be123c",
    },
    terminal={
        "black": "#0f172a", "red": "#b91c1c", "green": "#15803d", "yellow": "#a16207",
        "blue": "#1d4ed8", "magenta": "#a21caf", "cyan": "#0e7490", "white": "#475569",
        "brightBlack": "#334155", "brightRed": "#dc2626", "brightGreen": "#047857", "brightYellow": "#b45309",
        "brightBlue": "#2563eb", "brightMagenta": "#9333ea", "brightCyan": "#0f766e", "brightWhite": "#1e293b",
        "background": "#f8fafc", "foreground": "#0f172a", "selection": "#bfdbfe", "cursor": "#334155",
    },
    subtitle_text="#0f172a",
    subtitle_box="#f8fafc",
    subtitle_box_opacity=0.85,
)

DARK = Theme(
    name="dark",
    background="#0f172a",
    surface="#1e293b",
    border="#64748b",
    text="#f8fafc",
    muted="#cbd5e1",
    accent="#fde047",
    palette=("#38bdf8", "#a78bfa", "#34d399", "#fb923c", "#f472b6", "#facc15"),
    font="DejaVu Sans",
    mono="DejaVu Sans Mono",
    code={
        "Text": "#f1f5f9",
        "Comment": "italic #b0bccc",
        "Comment.Preproc": "#b0bccc",
        "Keyword": "bold #c4b5fd",
        "Keyword.Constant": "#93c5fd",
        "Keyword.Type": "#67e8f9",
        "Operator": "#fda4af",
        "Operator.Word": "bold #c4b5fd",
        "Punctuation": "#cbd5e1",
        "Name": "#f1f5f9",
        "Name.Builtin": "#67e8f9",
        "Name.Builtin.Pseudo": "italic #67e8f9",
        "Name.Function": "#93c5fd",
        "Name.Class": "bold #93c5fd",
        "Name.Decorator": "#f0abfc",
        "Name.Exception": "#fda4af",
        "Name.Namespace": "#5eead4",
        "Name.Tag": "#fda4af",
        "Name.Attribute": "#fde68a",
        "Name.Variable": "#fdba74",
        "String": "#6ee7b7",
        "String.Doc": "italic #6ee7b7",
        "String.Escape": "#fdba74",
        "String.Interpol": "#fdba74",
        "Number": "#fdba74",
        "Generic.Deleted": "#fda4af",
        "Generic.Inserted": "#6ee7b7",
        "Generic.Heading": "bold #f1f5f9",
        "Generic.Subheading": "bold #cbd5e1",
        "Generic.Prompt": "bold #cbd5e1",
        "Generic.Output": "#e2e8f0",
        "Error": "#fda4af",
    },
    terminal={
        "black": "#94a3b8", "red": "#f87171", "green": "#4ade80", "yellow": "#facc15",
        "blue": "#60a5fa", "magenta": "#e879f9", "cyan": "#22d3ee", "white": "#e2e8f0",
        "brightBlack": "#cbd5e1", "brightRed": "#fca5a5", "brightGreen": "#86efac", "brightYellow": "#fde047",
        "brightBlue": "#93c5fd", "brightMagenta": "#f0abfc", "brightCyan": "#67e8f9", "brightWhite": "#f8fafc",
        "background": "#0f172a", "foreground": "#f1f5f9", "selection": "#334155", "cursor": "#cbd5e1",
    },
    subtitle_text="#ffffff",
    subtitle_box="#0f172a",
    subtitle_box_opacity=0.8,
)

THEMES: dict[str, Theme] = {t.name: t for t in (LIGHT, DARK)}


def get_theme(name: str | None = None) -> Theme:
    """The theme called ``name`` (``light`` when None)."""
    key = name or DEFAULT_THEME
    try:
        return THEMES[key]
    except KeyError:
        raise ValueError(f"unknown theme {key!r} (use {' or '.join(THEMES)})") from None


def code_color(spec: str) -> str:
    """``"bold #7c3aed"`` -> ``"#7c3aed"``."""
    return next(part for part in spec.split() if part.startswith("#"))


def ass_colour(color: str, opacity: float = 1.0) -> str:
    """``#rrggbb`` and an opacity as an ASS colour ``&HAABBGGRR`` (alpha 00 is opaque)."""
    r, g, b = color[1:3], color[3:5], color[5:7]
    alpha = round((1.0 - opacity) * 255)
    return f"&H{alpha:02X}{b}{g}{r}".upper()


# WCAG 2.x contrast ------------------------------------------------------------------------------------------------

def _rgb(color: str) -> tuple[float, float, float]:
    return tuple(int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def blend(top: str, bottom: str, opacity: float) -> str:
    """``top`` at ``opacity`` over an opaque ``bottom``, as ``#rrggbb``."""
    t, b = _rgb(top), _rgb(bottom)
    return "#" + "".join(f"{round((opacity * x + (1 - opacity) * y) * 255):02x}" for x, y in zip(t, b))


def relative_luminance(color: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio of two opaque colours (1 to 21)."""
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def text_pairs(theme: Theme) -> list[tuple[str, str, str]]:
    """Every (what, text colour, background colour) pair the theme draws, backgrounds already composited."""
    bg, surface = theme.background, theme.surface
    pairs = [
        ("title text on background", theme.text, bg),
        ("muted text on background", theme.muted, bg),
        ("line numbers on surface", theme.muted, surface),
        ("line numbers in a highlight", theme.muted, blend(theme.accent, surface, HIGHLIGHT_FILL_OPACITY)),
        ("terminal foreground", theme.terminal["foreground"], theme.terminal["background"]),
    ]
    for color in theme.palette:
        pairs.append((f"node label on {color} fill", theme.text, blend(color, bg, NODE_FILL_OPACITY)))
    highlight = blend(theme.accent, surface, HIGHLIGHT_FILL_OPACITY)
    for token, spec in theme.code.items():
        pairs.append((f"code {token} on surface", code_color(spec), surface))
        pairs.append((f"code {token} in a highlight", code_color(spec), highlight))
    for key in TERMINAL_KEYS:
        pairs.append((f"terminal {key}", theme.terminal[key], theme.terminal["background"]))
    for under in ("#000000", "#ffffff", bg):  # the box is translucent: check it over any frame
        box = blend(theme.subtitle_box, under, theme.subtitle_box_opacity)
        pairs.append((f"subtitle on its box over {under}", theme.subtitle_text, box))
    return pairs


def graphic_pairs(theme: Theme) -> list[tuple[str, str, str]]:
    """Non-text parts that must stand out from the background (WCAG 1.4.11: at least 3:1)."""
    pairs = [("accent on background", theme.accent, theme.background), ("arrows on background", theme.muted, theme.background)]
    pairs += [(f"node stroke {c}", c, theme.background) for c in theme.palette]
    return pairs
