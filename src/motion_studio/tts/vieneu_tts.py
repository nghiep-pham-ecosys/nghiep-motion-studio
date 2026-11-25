"""VieNeu-TTS v3 Turbo adapter (Vietnamese, with English code-switching).

Apache-2.0 code and weights; the model card allows commercial and monetised
use of the output (see docs/voices.md for links and the check date). Needs
the ``vieneu`` package (``just tts-setup``). Weights download to the Hugging
Face cache on first use.

Two kinds of voice:

* preset voices shipped with the model (ids are their names, e.g. "Trúc Ly"),
  minus the presets in ``BLOCKED_PRESETS``;
* ``clone``: a zero-shot clone of a local reference recording (``ref_wav``).
  The engine uses at most the first 8 s of the reference, so pass a clean
  6-8 s clip. The reference never leaves the machine.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import Any

from .base import TTSAdapter, TTSError, VoiceInfo

CLONE_VOICE = "clone"
# Presets not offered (see docs/voices.md, checked 2026-11-25): the voice matches a commercial TTS voice,
# the preset is not in the model card's preset list, or the name matches a well-known public figure.
BLOCKED_PRESETS = frozenset({
    "Ngọc Huyền", "Quốc Tuấn", "Adam bựa", "Thiền Tâm Đức", "Phạm Tuyên", "Xuân Vĩnh", "Đoan Trang",
})
MODEL_MAX_REF_SECONDS = 8.0
MIN_REF_SECONDS = 3.0


_CONVERT = "convert it with: ffmpeg -i in.wav -ac 1 -c:a pcm_s16le out.wav"


def wav_seconds(path: Path) -> float:
    """Length of an integer-PCM or float WAV, read from its RIFF chunks (stdlib only)."""
    data = Path(path).read_bytes()
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    fmt = None
    data_bytes = None
    pos = 12
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt " and len(body) >= 16:
            tag, channels, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == 0xFFFE and len(body) >= 26:  # WAVE_FORMAT_EXTENSIBLE: real format in the SubFormat GUID
                tag = struct.unpack("<H", body[24:26])[0]
            fmt = (tag, channels, rate, bits)
        elif cid == b"data":
            data_bytes = min(size, len(data) - pos - 8)
        pos += 8 + size + (size & 1)
    if fmt is None or data_bytes is None:
        raise ValueError("missing fmt or data chunk")
    tag, channels, rate, bits = fmt
    if tag not in (1, 3) or not channels or not rate or bits % 8:
        raise ValueError(f"unsupported WAV format (tag {tag}, {bits} bit)")
    return data_bytes / (channels * (bits // 8) * rate)


def check_reference(path: Path) -> float:
    """Return the length of a reference WAV, or raise TTSError if it cannot serve as one.

    Messages name only the file, never its folder, so they are safe to show on a page.
    """
    path = Path(path)
    if not path.is_file():
        raise TTSError(f"reference clip not found: {path.name}")
    try:
        seconds = wav_seconds(path)
    except (OSError, ValueError, struct.error) as exc:
        raise TTSError(f"reference clip {path.name} is not a usable WAV ({exc}); {_CONVERT}") from exc
    if seconds < MIN_REF_SECONDS:
        raise TTSError(f"reference clip {path.name} is {seconds:.1f} s; use a clean clip of 6-8 s")
    return seconds


class VieNeuTTS(TTSAdapter):
    engine = "vieneu"
    licence = "Apache-2.0"

    def __init__(
        self,
        device: str = "auto",
        ref_wav: str | Path | None = None,
        seed: int | None = 1234,
        temperature: float = 0.8,
        **_: Any,
    ) -> None:
        try:
            from vieneu import Vieneu
        except ImportError as exc:
            raise TTSError(f"vieneu is not installed ({exc}); run `just tts-setup` (see docs/voices.md)") from exc
        # The reference is checked on first use of the clone voice, so a bad clip never breaks the presets.
        self.ref_wav = Path(ref_wav).resolve() if ref_wav else None
        self.ref_seconds: float | None = None
        self._ref_digest = ""
        self.seed = seed
        self.temperature = temperature
        try:
            self._tts = Vieneu(mode="v3turbo", device=device)
        except Exception as exc:  # the SDK raises many types while loading
            raise TTSError(f"could not load VieNeu-TTS: {exc}") from exc
        self._presets = [(label, vid) for label, vid in self._tts.list_preset_voices() if vid not in BLOCKED_PRESETS]

    def voices(self) -> list[VoiceInfo]:
        out = [VoiceInfo(id=vid, lang="vi", label=label.lstrip("⭐ ").strip()) for label, vid in self._presets]
        if self.ref_wav is not None:
            out.append(VoiceInfo(id=CLONE_VOICE, lang="vi", label="Clone of the local reference recording"))
        return out

    def _check_ref(self) -> None:
        if self.ref_wav is None:
            raise TTSError("the clone voice needs ref_wav (a clean 6-8 s WAV)")
        if self.ref_seconds is None:
            self.ref_seconds = check_reference(self.ref_wav)
            self._ref_digest = hashlib.sha256(self.ref_wav.read_bytes()).hexdigest()[:16]

    def cache_tag(self) -> str:
        tag = f"v3turbo;seed={self.seed};t={self.temperature}"
        if self.ref_wav is not None:
            try:
                self._check_ref()
            except TTSError:
                return tag + ";ref=invalid"
            tag += f";ref={self._ref_digest}"  # a hash of the clip's bytes, never its path
        return tag

    def _reseed(self) -> None:
        if self.seed is None:
            return
        import random

        import numpy as np

        random.seed(self.seed)
        np.random.seed(self.seed)
        try:
            import torch

            torch.manual_seed(self.seed)
        except ImportError:
            pass

    def generate(self, text: str, voice: str, lang: str) -> tuple[Any, int]:
        if voice == CLONE_VOICE:
            self._check_ref()
        self._reseed()
        try:
            if voice == CLONE_VOICE:
                audio = self._tts.infer(text, ref_audio=str(self.ref_wav), denoise=True, temperature=self.temperature)
            else:
                audio = self._tts.infer(text, voice=voice, temperature=self.temperature)
        except Exception as exc:
            raise TTSError(f"VieNeu-TTS failed on {text[:40]!r}: {exc}") from exc
        return audio, int(getattr(self._tts, "sample_rate", 48000))

    def close(self) -> None:
        self._tts = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
