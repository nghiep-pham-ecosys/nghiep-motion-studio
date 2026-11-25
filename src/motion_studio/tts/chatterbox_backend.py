"""The part of the ``clone-en`` engine that imports Chatterbox, torch and huggingface_hub.

It runs inside the Chatterbox venv (through ``chatterbox_worker``) or in-process
when ``chatterbox`` is importable. Weights download once, at a pinned revision,
into ``models_dir`` (default ``~/.cache/nghiep-motion-studio/hf``); later runs
read them from there without the network.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from .base import TTSError
from .chatterbox_tts import MODELS, ModelSpec


class ChatterboxBackend:
    def __init__(self, device: str, models_dir: Path, ref_wav: Path) -> None:
        import torch  # noqa: F401  (fail here, with a clear message, if the venv is broken)

        self.device = self._pick_device(device)
        self.models_dir = Path(models_dir)
        self.ref_wav = Path(ref_wav)
        self._kind: str | None = None
        self._model: Any = None

    @staticmethod
    def _pick_device(device: str) -> str:
        import torch

        if device in ("", "auto"):
            return "cuda" if torch.cuda.is_available() else "cpu"
        return device

    def _download(self, spec: ModelSpec) -> Path:
        from huggingface_hub import snapshot_download

        args = dict(repo_id=spec.repo, revision=spec.revision, cache_dir=str(self.models_dir),
                    allow_patterns=list(spec.files))
        try:
            return Path(snapshot_download(local_files_only=True, **args))
        except Exception:  # not cached yet: download once
            return Path(snapshot_download(**args))

    def _load(self, voice: str) -> Any:
        spec = MODELS.get(voice)
        if spec is None:
            raise TTSError(f"clone-en: no voice {voice!r}")
        if self._kind == spec.kind:
            return self._model
        self.close()  # one model in memory at a time
        path = self._download(spec)
        if spec.kind == "turbo":
            from chatterbox.tts_turbo import ChatterboxTurboTTS as Model
        else:
            from chatterbox.tts import ChatterboxTTS as Model
        model = Model.from_local(path, self.device)
        # The reference is read once per model; every line then reuses its conditioning.
        if spec.kind == "turbo":
            model.prepare_conditionals(str(self.ref_wav), exaggeration=0.0, norm_loudness=True)
        else:
            model.prepare_conditionals(str(self.ref_wav), exaggeration=0.5)
        self._model, self._kind = model, spec.kind
        return model

    def _seed(self, seed: int | None) -> None:
        if seed is None:
            return
        import numpy as np
        import torch

        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)

    def generate(self, voice: str, text: str, *, seed: int | None, temperature: float) -> tuple[Any, int]:
        import torch

        model = self._load(voice)
        self._seed(seed)
        with torch.inference_mode():
            if self._kind == "turbo":
                wav = model.generate(text, temperature=temperature, exaggeration=0.0, cfg_weight=0.0)
            else:
                wav = model.generate(text, temperature=temperature)
        return wav.squeeze(0).detach().cpu().numpy(), int(model.sr)

    def close(self) -> None:
        self._model, self._kind = None, None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
