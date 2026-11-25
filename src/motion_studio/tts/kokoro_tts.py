"""Kokoro-82M adapter (English). Apache-2.0 code and weights; see docs/voices.md.

Needs the ``tts`` extra (kokoro, soundfile, torch). Weights download to the
Hugging Face cache (~/.cache/huggingface) on first use.
"""

from __future__ import annotations

from typing import Any

from .base import TTSAdapter, TTSError, VoiceInfo

REPO_ID = "hexgrad/Kokoro-82M"
RATE = 24000
# A small, curated set of English voices (the model has more; any valid id works via extra_voices).
VOICES = {
    "af_heart": "American English, female (Heart)",
    "af_bella": "American English, female (Bella)",
    "am_michael": "American English, male (Michael)",
    "am_fenrir": "American English, male (Fenrir)",
    "bf_emma": "British English, female (Emma)",
    "bm_george": "British English, male (George)",
}


class KokoroTTS(TTSAdapter):
    engine = "kokoro"
    licence = "Apache-2.0"

    def __init__(self, device: str | None = None, speed: float = 1.0, extra_voices: dict[str, str] | None = None, **_: Any) -> None:
        try:
            import numpy  # noqa: F401
            from kokoro import KPipeline  # noqa: F401
        except ImportError as exc:
            raise TTSError(f"kokoro is not installed ({exc}); run `just tts-setup` (see docs/voices.md)") from exc
        if not 0.5 <= speed <= 2.0:
            raise TTSError("speed must be between 0.5 and 2.0")
        self.device = None if device in (None, "", "auto") else device  # KPipeline picks CUDA when available
        self.speed = speed
        self.voice_names = {**VOICES, **(extra_voices or {})}
        self._pipelines: dict[str, Any] = {}

    def cache_tag(self) -> str:
        return f"kokoro-0.9.4;speed={self.speed}"

    def voices(self) -> list[VoiceInfo]:
        return [VoiceInfo(id=v, lang="en", label=label) for v, label in self.voice_names.items()]

    def _pipeline(self, voice: str) -> Any:
        from kokoro import KPipeline

        lang_code = "b" if voice.startswith("b") else "a"  # British or American G2P
        if lang_code not in self._pipelines:
            try:
                self._pipelines[lang_code] = KPipeline(lang_code=lang_code, repo_id=REPO_ID, device=self.device)
            except Exception as exc:  # the library raises many types while loading
                raise TTSError(f"could not load Kokoro ({lang_code}): {exc}") from exc
        return self._pipelines[lang_code]

    def generate(self, text: str, voice: str, lang: str) -> tuple[Any, int]:
        import numpy as np

        pipeline = self._pipeline(voice)
        try:
            chunks = [audio for _, _, audio in pipeline(text, voice=voice, speed=self.speed) if audio is not None]
        except Exception as exc:
            raise TTSError(f"Kokoro failed on {text[:40]!r}: {exc}") from exc
        if not chunks:
            return np.zeros(0, dtype=np.float32), RATE
        return np.concatenate([np.asarray(c, dtype=np.float32).reshape(-1) for c in chunks]), RATE

    def close(self) -> None:
        self._pipelines.clear()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
