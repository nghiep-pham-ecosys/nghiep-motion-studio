"""A deterministic stand-in engine for tests and CI (no model, stdlib only).

Each line becomes a soft tone whose length grows with the text, so timing code
can be tested without downloading a model.
"""

from __future__ import annotations

import array
import math

from .base import TTSAdapter, VoiceInfo

RATE = 24000
SECONDS_PER_CHAR = 0.045
MIN_SECONDS = 0.4


class MockTTS(TTSAdapter):
    engine = "mock"
    licence = "n/a (test tone)"

    def __init__(self, **_: object) -> None:
        self.calls = 0

    def voices(self) -> list[VoiceInfo]:
        return [
            VoiceInfo(id=f"tone-{pitch}", lang=lang, label=f"Test tone {pitch} Hz ({lang})")
            for lang in ("en", "vi")
            for pitch in (220, 330)
        ]

    def generate(self, text: str, voice: str, lang: str) -> tuple[array.array, int]:
        self.calls += 1
        pitch = int(voice.split("-")[1])
        n = int(RATE * max(MIN_SECONDS, SECONDS_PER_CHAR * len(text.strip())))
        fade = min(n // 4, RATE // 50)
        out = array.array("h", bytes(2 * n))
        for i in range(n):
            env = min(1.0, i / fade, (n - 1 - i) / fade) if fade else 1.0
            out[i] = int(8000 * env * math.sin(2 * math.pi * pitch * i / RATE))
        return out, RATE
