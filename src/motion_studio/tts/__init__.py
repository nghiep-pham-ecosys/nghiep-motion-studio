"""Text-to-speech adapters.

Engines are imported lazily: ``get_adapter("kokoro")`` imports the Kokoro
module (and torch) only when it is asked for. The ``mock`` engine needs
nothing and is what CI uses.
"""

from __future__ import annotations

from importlib import import_module

from .base import LineAudio, TTSAdapter, TTSError, VoiceInfo, read_wav, write_wav

ENGINES: dict[str, str] = {
    "mock": "motion_studio.tts.mock:MockTTS",
    "kokoro": "motion_studio.tts.kokoro_tts:KokoroTTS",
    "vieneu": "motion_studio.tts.vieneu_tts:VieNeuTTS",
    "clone": "motion_studio.tts.clone:CloneTTS",
    "clone-en": "motion_studio.tts.chatterbox_tts:ChatterboxTTS",
}


def adapter_class(engine: str) -> type[TTSAdapter]:
    try:
        target = ENGINES[engine]
    except KeyError:
        raise TTSError(f"unknown TTS engine {engine!r} (known: {', '.join(sorted(ENGINES))})") from None
    module_name, _, cls_name = target.partition(":")
    try:
        module = import_module(module_name)
    except ImportError as exc:
        raise TTSError(
            f"engine {engine!r} needs extra packages that are not installed ({exc}); see docs/voices.md"
        ) from exc
    return getattr(module, cls_name)


def get_adapter(engine: str, **options: object) -> TTSAdapter:
    """Create an adapter. ``options`` go to the engine (for example ``device``, ``ref_wav``)."""
    return adapter_class(engine)(**options)


__all__ = [
    "ENGINES",
    "LineAudio",
    "TTSAdapter",
    "TTSError",
    "VoiceInfo",
    "adapter_class",
    "get_adapter",
    "read_wav",
    "write_wav",
]
