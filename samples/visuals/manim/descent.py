"""A ball rolls down a loss curve: gradient descent in one picture.

A custom Manim scene can read MOTION_STUDIO_TARGET_S (the scene's planned
length) to pace itself; if the clip is longer, the renderer stretches the scene.
"""

import os

from manim import UP, Axes, Create, Dot, FadeIn, MoveAlongPath, Scene, Text, TracedPath, config


def loss(x):
    return 0.25 * (x - 1.5) ** 2 + 0.4


class Descent(Scene):
    def construct(self):
        target = float(os.environ.get("MOTION_STUDIO_TARGET_S", "5"))
        axes = Axes(
            x_range=[-3, 5, 1], y_range=[0, 5, 1], x_length=10, y_length=5.5, tips=False,
            axis_config={"color": "#94a3b8"},
        )
        curve = axes.plot(loss, x_range=[-2.6, 4.6], color="#38bdf8", stroke_width=6)
        title = Text("loss", font="DejaVu Sans", font_size=34, color="#f8fafc").next_to(axes, UP, buff=0.2)
        path = axes.plot(loss, x_range=[-2.4, 1.5])
        ball = Dot(axes.c2p(-2.4, loss(-2.4)), radius=0.14, color="#fde047")
        trail = TracedPath(ball.get_center, stroke_color="#fde047", stroke_width=4)

        self.play(Create(axes), FadeIn(title), run_time=0.8)
        self.play(Create(curve), run_time=1.0)
        self.add(trail, ball)
        roll = max(target - 2.6, 1.0)
        self.play(MoveAlongPath(ball, path), run_time=roll, rate_func=lambda t: 1 - (1 - t) ** 3)
        self.wait(max(target - 1.8 - roll, 1 / config.frame_rate))
