"""Manim template: an animated code block.

Run by the ``code`` adapter, never imported by the package. The adapter
writes a JSON spec and passes its path in ``MOTION_STUDIO_SPEC``:

    {"target_s": 8.0, "code": "...", "first_line": 1, "language": "python", "style": "monokai" | null,
     "highlight": [[1, 2], [4, 4]], "title": "..." | null}

Colours and fonts come from the theme JSON at ``MOTION_STUDIO_THEME``:
without a ``style`` the code uses the theme's token colours on its
``surface``; a ``style`` names a Pygments style instead.

``highlight`` counts from the first line of ``code`` (the adapter shifts it
when it drops leading blank lines; ``first_line`` keeps the shown numbers).

The code is written line by line, then each highlight range (1-based,
inclusive) gets a frame while the other lines dim. All motion fits inside
``target_s`` so the clip never runs past its scene.
"""

from __future__ import annotations

import json
import os

from pygments.style import Style
from pygments.token import string_to_tokentype

from manim import (
    DOWN,
    UP,
    Code,
    Create,
    FadeIn,
    LaggedStart,
    Rectangle,
    Scene,
    Text,
    Transform,
    Wait,
    Write,
    config,
)

DIM = 0.35
MAX_SCALE = 1.8
TYPE_S_PER_LINE = 0.25  # pacing when no target length is given
HIGHLIGHT_S = 1.5
MIN_HOLD_S = 0.4


def _load(var: str) -> dict:
    with open(os.environ[var], encoding="utf-8") as fh:
        return json.load(fh)


def theme_style(theme: dict) -> type[Style]:
    """A Pygments style built from the theme's token colours."""
    return type(
        "ThemeStyle",
        (Style,),
        {
            "background_color": theme["surface"],
            "highlight_color": theme["surface"],
            "line_number_color": theme["muted"],
            "line_number_background_color": theme["surface"],
            "styles": {string_to_tokentype(name): value for name, value in theme["code"].items()},
        },
    )


class CodeScene(Scene):
    def construct(self) -> None:
        spec = _load("MOTION_STUDIO_SPEC")
        theme = _load("MOTION_STUDIO_THEME")
        fw, fh = config.frame_width, config.frame_height
        top = fh / 2 - 0.4
        title = None
        if spec.get("title"):
            title = Text(spec["title"], font=theme["font"], font_size=36, color=theme["text"])
            title.scale(min(1.0, fw * 0.9 / title.width))
            title.move_to(UP * (top - title.height / 2))
            top -= title.height + 0.3

        code = Code(
            code_string=spec["code"],
            language=spec.get("language"),
            formatter_style=spec.get("style") or theme_style(theme),
            background="window",
            background_config={"stroke_color": theme["border"], "stroke_width": 2},
            add_line_numbers=True,
            line_numbers_from=int(spec.get("first_line", 1)),
            paragraph_config={"font": theme["mono"]},
        )
        avail_h = top - (-fh / 2 + 0.4)
        code.scale(min(MAX_SCALE, fw * 0.92 / code.width, avail_h / code.height))
        code.move_to(UP * ((top + (-fh / 2 + 0.4)) / 2))

        lines = list(code.code_lines)
        visible = [line for line in lines if len(line) > 0]
        highlights = [(a, b) for a, b in spec.get("highlight", [])]
        target = spec.get("target_s")
        intro_s = 0.6
        if target:
            free = max(target - MIN_HOLD_S - intro_s, 0.2)
            type_s = free * (0.55 if highlights else 1.0)
            each_hl = (free - type_s) / len(highlights) if highlights else 0.0
        else:
            type_s = TYPE_S_PER_LINE * max(len(visible), 1)
            each_hl = HIGHLIGHT_S
        used = 0.0

        intro = [FadeIn(code.background), FadeIn(code.line_numbers)]
        if title is not None:
            intro.append(FadeIn(title, shift=DOWN * 0.2))
        self.play(*intro, run_time=intro_s)
        used += intro_s
        if visible:
            self.play(LaggedStart(*[Write(line) for line in visible], lag_ratio=1.0), run_time=type_s)
        else:
            self.wait(type_s)
        used += type_s

        frame = None
        left = code.background.get_left()[0] + 0.1
        right = code.background.get_right()[0] - 0.1
        for a, b in highlights:
            group = [line for line in lines[a - 1 : b] if len(line) > 0] or [lines[a - 1]]
            y_top = max(m.get_top()[1] for m in group)
            y_bot = min(m.get_bottom()[1] for m in group)
            pad = 0.08
            box = Rectangle(
                width=right - left, height=(y_top - y_bot) + 2 * pad,
                stroke_color=theme["accent"], stroke_width=3, fill_color=theme["accent"], fill_opacity=theme["highlight_fill_opacity"],
            ).move_to([(left + right) / 2, (y_top + y_bot) / 2, 0])
            dims = [
                line.animate.set_opacity(1.0 if a - 1 <= i < b else DIM) for i, line in enumerate(lines) if len(line) > 0
            ]
            step = Create(box) if frame is None else Transform(frame, box)
            self.play(step, *dims, run_time=min(0.6, each_hl * 0.5))
            if frame is None:
                frame = box
            self.wait(max(each_hl - min(0.6, each_hl * 0.5), 1 / config.frame_rate))
            used += each_hl

        hold = (target - used) if target else 1.0
        self.play(Wait(max(hold, 1 / config.frame_rate)))
