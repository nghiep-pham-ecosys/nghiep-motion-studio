"""Manim scenes for the CSTR pilot: the mole balance, conversion against Damkohler number, tanks in series.

Every number is computed from the formulas of process-sim-lab's CSTR and PFR
units (X = Da / (1 + Da), X = 1 - exp(-Da), X_N = 1 - (1 + Da / N)^-N). Text
follows MOTION_STUDIO_LANG (en or vi), colours the theme JSON at
MOTION_STUDIO_THEME, and each scene paces itself to MOTION_STUDIO_TARGET_S.
"""

import json
import math
import os

from manim import (
    DOWN, LEFT, RIGHT, UP, Axes, Create, DashedLine, Dot, FadeIn, GrowFromEdge, Rectangle, Scene, Text, VGroup, Write,
    config,
)

LANG = os.environ.get("MOTION_STUDIO_LANG", "en")
TEXT = {
    "en": {
        "balance": "Mole balance on A (steady state)",
        "in_out": "in  =  out  +  reacted",
        "conv_title": "Conversion X against Damkohler number Da",
        "cstr": "CSTR: X = Da / (1 + Da)",
        "pfr": "PFR: X = 1 − e^(−Da)",
        "series_title": "N equal CSTRs in series, total Da = 5",
        "tanks": "tanks",
    },
    "vi": {
        "balance": "Cân bằng mol của A (trạng thái dừng)",
        "in_out": "vào  =  ra  +  phản ứng",
        "conv_title": "Độ chuyển hoá X theo số Damkohler Da",
        "cstr": "CSTR: X = Da / (1 + Da)",
        "pfr": "PFR: X = 1 − e^(−Da)",
        "series_title": "N bình CSTR nối tiếp, tổng Da = 5",
        "tanks": "bình",
    },
}[LANG if LANG in ("en", "vi") else "en"]


def theme():
    path = os.environ.get("MOTION_STUDIO_THEME")
    if path:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {"text": "#0f172a", "muted": "#475569", "accent": "#b45309",
            "palette": ["#0284c7", "#7c3aed", "#059669", "#db2777"], "font": "DejaVu Sans"}


def target_s(default: float) -> float:
    return float(os.environ.get("MOTION_STUDIO_TARGET_S", str(default)))


def hold(scene: Scene, used: float, target: float) -> None:
    scene.wait(max(target - used, 1 / config.frame_rate))


class Balance(Scene):
    """The balance written line by line, ending in F_A and X."""

    def construct(self):
        t, total = theme(), target_s(20)
        lines = [
            TEXT["in_out"],
            "F_A0  =  F_A  +  k · C_A · V",
            "C_A = F_A / Q ,   τ = V / Q",
            "F_A  =  F_A0 / (1 + k·τ)",
            "X  =  k·τ / (1 + k·τ)  =  Da / (1 + Da)",
        ]
        title = Text(TEXT["balance"], font=t["font"], font_size=40, color=t["muted"]).to_edge(UP, buff=0.6)
        rows = VGroup(*[Text(s, font=t["font"], font_size=46, color=t["text"]) for s in lines])
        rows.arrange(DOWN, buff=0.45, aligned_edge=LEFT).next_to(title, DOWN, buff=0.6)
        rows.set_x(0)
        rows[-1].set_color(t["accent"])
        self.play(FadeIn(title), run_time=0.6)
        step = max((total * 0.75 - 0.6) / len(rows), 0.8)
        for row in rows:
            self.play(Write(row), run_time=step)
        hold(self, 0.6 + step * len(rows), total)


def ticks(axes, t, xs, ys):
    """Tick labels drawn with Text (no LaTeX needed)."""
    out = VGroup()
    for x in xs:
        out.add(Text(f"{x:g}", font=t["font"], font_size=26, color=t["muted"]).next_to(axes.c2p(x, 0), DOWN, buff=0.2))
    for y in ys:
        out.add(Text(f"{y:.1f}", font=t["font"], font_size=26, color=t["muted"]).next_to(axes.c2p(0, y), LEFT, buff=0.2))
    return out


class Curve(Scene):
    """X against Da for a CSTR and a PFR, with the point Da = 5 marked."""

    def construct(self):
        t, total = theme(), target_s(25)
        cstr_color, pfr_color = t["palette"][0], t["palette"][1 % len(t["palette"])]
        axes = Axes(x_range=[0, 10, 1], y_range=[0, 1, 0.2], x_length=11, y_length=5.4, tips=False,
                    axis_config={"color": t["muted"]}).shift(DOWN * 0.3 + RIGHT * 0.3)
        labels = ticks(axes, t, [0, 2, 4, 5, 6, 8, 10], [0.2, 0.4, 0.6, 0.8, 1.0])
        x_name = Text("Da", font=t["font"], font_size=30, color=t["text"]).next_to(axes.c2p(10, 0), DOWN, buff=0.55)
        title = Text(TEXT["conv_title"], font=t["font"], font_size=38, color=t["text"]).to_edge(UP, buff=0.4)
        cstr = axes.plot(lambda d: d / (1 + d), x_range=[0, 10], color=cstr_color, stroke_width=6)
        pfr = axes.plot(lambda d: 1 - math.exp(-d), x_range=[0, 10], color=pfr_color, stroke_width=6)
        key = VGroup(
            Text(TEXT["pfr"], font=t["font"], font_size=30, color=pfr_color),
            Text(TEXT["cstr"], font=t["font"], font_size=30, color=cstr_color),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.2).move_to(axes.c2p(7.4, 0.3))
        x_c, x_p = 5 / 6, 1 - math.exp(-5)
        dot_c = Dot(axes.c2p(5, x_c), color=t["accent"], radius=0.1)
        dot_p = Dot(axes.c2p(5, x_p), color=t["accent"], radius=0.1)
        lab_c = Text(f"{x_c:.3f}", font=t["font"], font_size=30, color=t["text"]).next_to(dot_c, DOWN + RIGHT, buff=0.15)
        lab_p = Text(f"{x_p:.3f}", font=t["font"], font_size=30, color=t["text"]).next_to(dot_p, DOWN + LEFT, buff=0.15)
        guide = DashedLine(axes.c2p(5, 0), axes.c2p(5, 1), color=t["muted"])
        self.play(Create(axes), FadeIn(labels), FadeIn(x_name), FadeIn(title), run_time=1.2)
        draw = max(total * 0.3, 1.5)
        self.play(Create(cstr), FadeIn(key[1]), run_time=draw)
        self.play(Create(pfr), FadeIn(key[0]), run_time=draw)
        self.play(Create(guide), FadeIn(dot_c), FadeIn(lab_c), FadeIn(dot_p), FadeIn(lab_p), run_time=1.2)
        hold(self, 2.4 + 2 * draw, total)


class Series(Scene):
    """Conversion of N equal tanks in series against the PFR limit (bars drawn by hand, no LaTeX)."""

    def construct(self):
        t, total = theme(), target_s(20)
        ns = [1, 2, 3, 5, 10]
        values = [1 - (1 + 5 / n) ** -n for n in ns]
        axes = Axes(x_range=[0, len(ns), 1], y_range=[0, 1, 0.2], x_length=10, y_length=5, tips=False,
                    axis_config={"color": t["muted"]}, x_axis_config={"include_ticks": False}).shift(DOWN * 0.3 + LEFT * 0.7)
        yl = VGroup(*[Text(f"{y:.1f}", font=t["font"], font_size=26, color=t["muted"]).next_to(axes.c2p(0, y), LEFT, buff=0.2)
                      for y in (0.2, 0.4, 0.6, 0.8, 1.0)])
        title = Text(TEXT["series_title"], font=t["font"], font_size=38, color=t["text"]).to_edge(UP, buff=0.4)
        bars, names, values_text = VGroup(), VGroup(), VGroup()
        for i, (n, v) in enumerate(zip(ns, values)):
            left, right = axes.c2p(i + 0.2, 0), axes.c2p(i + 0.8, v)
            bar = Rectangle(width=right[0] - left[0], height=right[1] - left[1], color=t["palette"][0],
                            fill_color=t["palette"][0], fill_opacity=0.35, stroke_width=4)
            bar.move_to([(left[0] + right[0]) / 2, (left[1] + right[1]) / 2, 0])
            bars.add(bar)
            names.add(Text(str(n), font=t["font"], font_size=28, color=t["text"]).next_to(axes.c2p(i + 0.5, 0), DOWN, buff=0.2))
            values_text.add(Text(f"{v:.3f}", font=t["font"], font_size=26, color=t["text"]).move_to(bar.get_top() + DOWN * 0.3))
        axis_name = Text(f"N ({TEXT['tanks']})", font=t["font"], font_size=28, color=t["muted"]).next_to(names, DOWN, buff=0.2)
        limit_y = 1 - math.exp(-5)
        limit = DashedLine(axes.c2p(0, limit_y), axes.c2p(len(ns), limit_y), color=t["accent"])
        limit_label = Text(f"PFR {limit_y:.3f}", font=t["font"], font_size=28, color=t["text"]).next_to(limit, RIGHT, buff=0.15)
        grow = max(total * 0.4, 1.5)
        self.play(Create(axes), FadeIn(yl), FadeIn(title), FadeIn(names), FadeIn(axis_name), run_time=1.0)
        self.play(*[GrowFromEdge(b, DOWN) for b in bars], run_time=grow)
        self.play(FadeIn(values_text), run_time=1.0)
        self.play(Create(limit), FadeIn(limit_label), run_time=1.0)
        hold(self, grow + 3.0, total)
