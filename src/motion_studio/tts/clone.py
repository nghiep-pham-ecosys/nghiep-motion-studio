"""Cloned voice adapter: speaks in the voice of a local reference recording.

The clone is made by VieNeu-TTS v3 Turbo (zero-shot, no transcript needed).
Only clone a voice you have the rights to (your own). The reference file stays
local: pass its path at run time, never commit it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import TTSError, VoiceInfo
from .vieneu_tts import CLONE_VOICE, VieNeuTTS, check_reference


class CloneTTS(VieNeuTTS):
    engine = "clone"

    def __init__(self, ref_wav: str | Path | None = None, **options: Any) -> None:
        if not ref_wav:
            raise TTSError("the clone engine needs ref_wav (a clean 6-8 s WAV of the voice to clone)")
        check_reference(Path(ref_wav))  # before loading the model
        super().__init__(ref_wav=ref_wav, **options)
        self._check_ref()  # fail fast: this engine has no other voice

    def voices(self) -> list[VoiceInfo]:
        return [v for v in super().voices() if v.id == CLONE_VOICE]
