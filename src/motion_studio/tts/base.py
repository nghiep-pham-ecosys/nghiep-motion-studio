"""The TTS adapter interface and WAV helpers (stdlib only).

An adapter turns one line of text into mono 16-bit PCM audio. Engines that
need heavy libraries (torch, model weights) import them lazily inside their
module, so ``import motion_studio.tts`` stays cheap and CI never needs them.
"""

from __future__ import annotations

import array
import os
import sys
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Iterable, Sequence


class TTSError(RuntimeError):
    """A TTS engine is missing, misconfigured or failed."""


@dataclass(frozen=True)
class LineAudio:
    path: Path
    duration_s: float
    sample_rate: int


@dataclass(frozen=True)
class VoiceInfo:
    """One selectable voice of an engine."""

    id: str  # engine-specific voice id
    lang: str
    label: str


class TTSAdapter(ABC):
    """Base class of every engine adapter."""

    engine: ClassVar[str] = ""
    licence: ClassVar[str] = ""  # short label; docs/voices.md has the details and links

    @abstractmethod
    def voices(self) -> list[VoiceInfo]:
        """Voices this adapter offers (may depend on its configuration)."""

    @abstractmethod
    def generate(self, text: str, voice: str, lang: str) -> tuple[Sequence[float], int]:
        """Synthesise ``text``; return mono samples in [-1, 1] and their sample rate."""

    def synthesize(self, text: str, voice: str, lang: str, out_path: Path) -> LineAudio:
        """Synthesise ``text`` to a mono 16-bit WAV at ``out_path``."""
        if not text.strip():
            raise TTSError("cannot synthesise empty text")
        known = {v.id for v in self.voices() if v.lang == lang}
        if voice not in known:
            raise TTSError(f"{self.engine}: no {lang} voice {voice!r} (has {', '.join(sorted(known)) or 'none'})")
        samples, rate = self.generate(text, voice, lang)
        frames = write_wav(out_path, samples, rate)
        if frames == 0:
            raise TTSError(f"{self.engine}: voice {voice!r} returned no audio for {text[:40]!r}")
        return LineAudio(path=Path(out_path), duration_s=frames / rate, sample_rate=rate)

    def cache_tag(self) -> str:
        """Settings that change the sound for the same text and voice (reference clip, seed, speed).

        Part of the narration cache key, so changing them makes every line again.
        Never return a local path: the key is written next to the audio.
        """
        return ""

    def close(self) -> None:
        """Free model memory (optional)."""


def _to_int16(samples: Iterable[float]) -> array.array:
    tolist = getattr(samples, "tolist", None)  # numpy arrays and torch tensors convert fast
    values = tolist() if callable(tolist) else samples
    if values and isinstance(values[0], list):  # (1, n) or (n, 1) shapes
        values = [v for row in values for v in row]
    out = array.array("h")
    for v in values:
        x = int(round(float(v) * 32767.0))
        out.append(32767 if x > 32767 else -32768 if x < -32768 else x)
    return out


def write_wav(path: Path, samples: Iterable[float], rate: int) -> int:
    """Write mono float samples as 16-bit PCM. Returns the number of frames."""
    data = samples if isinstance(samples, array.array) and samples.typecode == "h" else _to_int16(samples)
    if sys.byteorder == "big":
        data = array.array("h", data)
        data.byteswap()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(rate))
            w.writeframes(data.tobytes())
        os.replace(tmp, path)  # never leave a half-written file under the real name
    finally:
        tmp.unlink(missing_ok=True)
    return len(data)


def read_wav(path: Path) -> tuple[array.array, int]:
    """Read a mono 16-bit WAV (as written by write_wav)."""
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise TTSError(f"{path}: expected mono 16-bit PCM")
        rate = w.getframerate()
        data = array.array("h", w.readframes(w.getnframes()))
    if sys.byteorder == "big":
        data.byteswap()
    return data, rate
