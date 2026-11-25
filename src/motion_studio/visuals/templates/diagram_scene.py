"""Manim template: an animated box-and-arrow diagram built from data.

Run by the ``diagram`` adapter, never imported by the package. The adapter
writes a JSON spec and passes its path in ``MOTION_STUDIO_SPEC``:

    {"target_s": 6.0, "title": "..." | null,
     "nodes": [{"id": "a", "label": "Query", "col": 0, "row": 0, "color": "#38bdf8"}],
     "edges": [{"from": "a", "to": "b", "label": "..." | null}],
     "steps": [["a"], ["b", "c"]]}

Colours and fonts come from the theme JSON at ``MOTION_STUDIO_THEME``: node
colours cycle through its ``palette`` unless a node sets ``color``.

Labels arrive already resolved to one language. Nodes appear step by step;
an edge appears with the step that shows both of its ends. Then a light
pulse runs along the edges in order, and the last frame holds. All motion
fits inside ``target_s`` so the clip never runs past its scene.
"""

from __future__ import annotations

import json
import os

import numpy as np
from manim import (
    DOWN,
    UP,
    Arrow,
    Create,
    FadeIn,
    GrowArrow,
    LaggedStart,
    RoundedRectangle,
    Scene,
    ShowPassingFlash,
    Text,
    VGroup,
    Wait,
    config,
)

STEP_S = 1.2  # pacing when no target length is given
MIN_HOLD_S = 0.4


def _load(var: str) -> dict:
    with open(os.environ[var], encoding="utf-8") as fh:
        return json.load(fh)


THEME = _load("MOTION_STUDIO_THEME")
FONT = THEME["font"]
TEXT = THEME["text"]
MUTED = THEME["muted"]
PULSE = THEME["accent"]
PALETTE = THEME["palette"]
NODE_FILL = THEME["node_fill_opacity"]


def _fit_text(text: str, max_w: float, max_h: float, size: float, color: str = TEXT) -> Text:
    mob = Text(text, font=FONT, font_size=size, color=color)
    scale = min(1.0, max_w / max(mob.width, 1e-6), max_h / max(mob.height, 1e-6))
    return mob.scale(scale)


def _edge_label(text: str, max_w: float, max_h: float, cap: float = 1.0) -> VGroup | Text:
    """A label that fits ``max_w``; a long one is split over two lines before it is shrunk."""
    label: VGroup | Text = Text(text, font=FONT, font_size=24, color=MUTED)
    words = text.split()
    if label.width > max_w and len(words) > 1:
        cut = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
        lines = [Text(" ".join(part), font=FONT, font_size=24, color=MUTED) for part in (words[:cut], words[cut:])]
        label = VGroup(*lines).arrange(DOWN, buff=0.12)
    return label.scale(min(cap, max_w / max(label.width, 1e-6), max_h / max(label.height, 1e-6)))


def _border_point(center: np.ndarray, half_w: float, half_h: float, toward: np.ndarray) -> np.ndarray:
    """Where the ray from ``center`` toward ``toward`` leaves a box of the given half sizes."""
    d = toward - center
    dx, dy = abs(d[0]), abs(d[1])
    if dx < 1e-9 and dy < 1e-9:
        return center
    t = min(half_w / dx if dx > 1e-9 else np.inf, half_h / dy if dy > 1e-9 else np.inf)
    return center + d * t


class DiagramScene(Scene):
    def construct(self) -> None:
        spec = _load("MOTION_STUDIO_SPEC")
        fw, fh = config.frame_width, config.frame_height
        top = fh / 2 - 0.4
        title = None
        if spec.get("title"):
            title = _fit_text(spec["title"], fw * 0.9, fh * 0.1, 40)
            title.move_to(UP * (top - title.height / 2))
            top -= title.height + 0.4
        bottom = -fh / 2 + 0.4

        nodes = spec["nodes"]
        cols = max(n["col"] for n in nodes) + 1
        rows = max(n["row"] for n in nodes) + 1
        cell_w = (fw - 0.8) / cols
        cell_h = (top - bottom) / rows
        box_w = min(cell_w * (0.62 if spec["edges"] else 0.75), 4.0)
        box_h = min(cell_h * 0.5, 1.5, box_w * 0.6)

        boxes: dict[str, VGroup] = {}
        geom: dict[str, tuple[np.ndarray, float, float]] = {}
        for i, node in enumerate(nodes):
            x = -fw / 2 + 0.4 + cell_w * (node["col"] + 0.5)
            y = top - cell_h * (node["row"] + 0.5)
            center = np.array([x, y, 0.0])
            color = node.get("color") or PALETTE[i % len(PALETTE)]
            rect = RoundedRectangle(
                width=box_w, height=box_h, corner_radius=min(0.2, box_h / 4),
                stroke_color=color, stroke_width=4, fill_color=color, fill_opacity=NODE_FILL,
            ).move_to(center)
            label = Text(node["label"], font=FONT, font_size=30, color=TEXT).move_to(center)
            boxes[node["id"]] = VGroup(rect, label)
            geom[node["id"]] = (center, box_w / 2 + 0.08, box_h / 2 + 0.08)

        # One text size for every node: the largest that fits the longest label.
        fit = min(
            min(1.0, box_w * 0.86 / max(g[1].width, 1e-6), box_h * 0.7 / max(g[1].height, 1e-6)) for g in boxes.values()
        )
        for group in boxes.values():
            group[1].scale(fit)

        arrows: list[tuple[dict, VGroup]] = []
        for edge in spec["edges"]:
            (ca, hwa, hha), (cb, hwb, hhb) = geom[edge["from"]], geom[edge["to"]]
            start = _border_point(ca, hwa, hha, cb)
            end = _border_point(cb, hwb, hhb, ca)
            arrow = Arrow(start, end, buff=0, color=MUTED, stroke_width=5, max_tip_length_to_length_ratio=0.2)
            group = VGroup(arrow)
            if edge.get("label"):
                mid = (start + end) / 2
                direction = end - start
                normal = np.array([-direction[1], direction[0], 0.0])
                normal = normal / (np.linalg.norm(normal) or 1.0)
                if normal[1] < 0:
                    normal = -normal
                length = float(np.linalg.norm(direction))
                across = abs(direction[0]) >= abs(direction[1])  # mostly horizontal: the gap bounds the width
                # Never larger than the node text (30 pt scaled by ``fit``; edge text starts at 24 pt).
                text = _edge_label(edge["label"], length * 0.95 if across else cell_w * 0.4, 0.8, cap=fit * 30 / 24 * 0.85)
                clearance = abs(normal[0]) * text.width / 2 + abs(normal[1]) * text.height / 2 + 0.12
                text.move_to(mid + normal * clearance)
                group.add(text)
            arrows.append((edge, group))

        steps = spec.get("steps") or [[n["id"]] for n in nodes]
        target = spec.get("target_s")
        intro_s = 0.8 if title is not None else 0.0
        pulse_s = 1.0 if arrows else 0.0
        if target:
            budget = max(target - MIN_HOLD_S - intro_s - pulse_s, 0.3 * len(steps))
            if budget + intro_s + pulse_s + MIN_HOLD_S > target:  # very short scene: drop the extras
                intro_s = pulse_s = 0.0
                budget = max(target - MIN_HOLD_S, 0.1 * len(steps))
            step_s = budget / len(steps)
        else:
            step_s = STEP_S
        used = 0.0

        if title is not None and intro_s:
            self.play(FadeIn(title, shift=DOWN * 0.2), run_time=intro_s)
            used += intro_s
        elif title is not None:
            self.add(title)

        shown: set[str] = set()
        pending = list(arrows)
        for step in steps:
            shown.update(step)
            ready = [g for e, g in pending if e["from"] in shown and e["to"] in shown]
            pending = [(e, g) for e, g in pending if not (e["from"] in shown and e["to"] in shown)]
            anims = [LaggedStart(*[Create(boxes[sid][0]) for sid in step], lag_ratio=0.15)]
            anims += [FadeIn(boxes[sid][1]) for sid in step]
            anims += [GrowArrow(g[0]) for g in ready] + [FadeIn(t) for g in ready for t in g[1:]]
            self.play(*anims, run_time=step_s)
            used += step_s

        if pulse_s:
            flashes = [
                ShowPassingFlash(g[0].copy().set_color(PULSE).set_stroke(width=9), time_width=0.6) for _, g in arrows
            ]
            self.play(LaggedStart(*flashes, lag_ratio=0.5), run_time=pulse_s)
            used += pulse_s

        hold = (target - used) if target else 1.0
        self.play(Wait(max(hold, 1 / config.frame_rate)))
