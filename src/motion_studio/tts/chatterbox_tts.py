"""English cloned voice made by Chatterbox (Resemble AI). MIT code and weights; see docs/voices.md.

The engine ``clone-en`` speaks English in the voice of a local reference
recording (zero-shot, no transcript). Only clone a voice you have the rights
to (your own). The reference stays local: pass its path at run time, never
commit it. Every line Chatterbox makes carries its built-in Perth watermark.

Chatterbox pins torch, transformers and numpy versions that differ from the
Kokoro and VieNeu environment, so it lives in its own venv
(``just clone-en-setup``, default ``~/.cache/nghiep-motion-studio/clone-en-venv``).
When the current interpreter cannot import ``chatterbox``, the adapter runs a
small worker in that venv and talks to it over a pipe (one JSON line per
request; the audio goes through a private temp file). Either way the model
runs locally and loads on the first line.

Voices:

* ``clone``: Chatterbox-Turbo (350M), about three times faster;
* ``clone-standard``: the original Chatterbox (500M).

Only one of the two models is in memory at a time.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, IO

from .base import TTSAdapter, TTSError, VoiceInfo, read_wav
from .vieneu_tts import check_reference

ENGINE = "clone-en"
PACKAGE_VERSION = "0.1.7"  # chatterbox-tts, pinned in pyproject.toml and the justfile
# The model needs more than 5 s of reference (Turbo refuses less); it reads the first 10 s.
MIN_REF_SECONDS = 5.5
REF_ADVICE = "use a clean clip of about 10 s"
VENV_ENV = "MOTION_STUDIO_CLONE_EN_VENV"
MODELS_ENV = "MOTION_STUDIO_MODELS_DIR"
DEFAULT_HOME = Path.home() / ".cache" / "nghiep-motion-studio"
DEFAULT_BACKEND = "motion_studio.tts.chatterbox_backend:ChatterboxBackend"


@dataclass(frozen=True)
class ModelSpec:
    kind: str  # "turbo" or "standard"
    repo: str
    revision: str  # a pinned commit of the Hugging Face repo (checked 2026-11-25)
    files: tuple[str, ...]
    label: str


MODELS: dict[str, ModelSpec] = {
    "clone": ModelSpec(
        kind="turbo",
        repo="ResembleAI/chatterbox-turbo",
        revision="749d1c1a46eb10492095d68fbcf55691ccf137cd",
        files=("ve.safetensors", "t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt", "*.json", "merges.txt"),
        label="Clone of the local reference recording (Chatterbox-Turbo)",
    ),
    "clone-standard": ModelSpec(
        kind="standard",
        repo="ResembleAI/chatterbox",
        revision="5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
        files=("ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt"),
        label="Clone of the local reference recording (Chatterbox, original)",
    ),
}


def default_venv() -> Path:
    return Path(os.environ.get(VENV_ENV) or DEFAULT_HOME / "clone-en-venv").expanduser()


def default_models_dir() -> Path:
    """Model weights live next to the venvs, not in the shared Hugging Face cache."""
    return Path(os.environ.get(MODELS_ENV) or DEFAULT_HOME / "hf").expanduser()


def scrub(message: str, *folders: Path | None) -> str:
    """Remove local folders (the reference clip's, the home folder) from a message shown on a page or log."""
    for folder in folders:
        if folder is not None and str(folder) not in ("", "/", "."):
            message = message.replace(str(folder), "<local>")
    return message


class ChatterboxTTS(TTSAdapter):
    engine = ENGINE
    licence = "MIT"

    def __init__(
        self,
        ref_wav: str | Path | None = None,
        device: str = "auto",
        seed: int | None = 1234,
        temperature: float = 0.8,
        python: str | Path | None = None,
        backend: str = DEFAULT_BACKEND,
        models_dir: str | Path | None = None,
        **_: Any,
    ) -> None:
        if not ref_wav:
            raise TTSError(f"the {ENGINE} engine needs ref_wav (a clean WAV of about 10 s of your own English speech)")
        self.ref_wav = Path(ref_wav).expanduser().resolve()
        check_reference(self.ref_wav, MIN_REF_SECONDS, REF_ADVICE)  # before any model or worker starts
        self._ref_digest = hashlib.sha256(self.ref_wav.read_bytes()).hexdigest()[:16]
        if not 0.05 <= temperature <= 2.0:
            raise TTSError("temperature must be between 0.05 and 2.0")
        self.seed = seed
        self.temperature = temperature
        self.device = device or "auto"
        self.models_dir = Path(models_dir).expanduser() if models_dir else default_models_dir()
        self._backend: Any = None
        self._worker: subprocess.Popen[str] | None = None
        self._tmp: Path | None = None
        self._log: IO[str] | None = None
        if python is None and importlib.util.find_spec("chatterbox") is not None:
            self._backend = _load_backend(backend)(device=self.device, models_dir=self.models_dir, ref_wav=self.ref_wav)
            return
        python = Path(python) if python else default_venv() / "bin" / "python"
        if not python.is_file():
            raise TTSError(
                f"{ENGINE} needs Chatterbox in its own venv; run `just clone-en-setup` (see docs/voices.md)"
            )
        self._start_worker(python, backend)

    # --- worker -------------------------------------------------------------------------------------

    def _start_worker(self, python: Path, backend: str) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="motion-studio-clone-en-"))
        self._log = open(self._tmp / "worker.log", "w+", encoding="utf-8")
        src_root = Path(__file__).resolve().parents[2]  # the folder that holds the motion_studio package
        extra = [x for x in os.environ.get("PYTHONPATH", "").split(os.pathsep) if x]
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(src_root), *extra])}
        cmd = [
            str(python), "-m", "motion_studio.tts.chatterbox_worker", "--backend", backend,
            "--device", self.device, "--models-dir", str(self.models_dir), "--ref-wav", str(self.ref_wav),
        ]
        try:
            self._worker = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log, text=True,
                encoding="utf-8", env=env, cwd=self._tmp,
            )
        except OSError as exc:
            self.close()
            raise TTSError(f"could not start the {ENGINE} worker: {exc}") from exc
        hello = self._read_reply()
        if not hello.get("ok"):
            error = hello.get("error", "no reply")
            self.close()
            raise TTSError(f"{ENGINE} worker failed to start: {error}")

    def _read_reply(self) -> dict[str, Any]:
        assert self._worker is not None and self._worker.stdout is not None
        line = self._worker.stdout.readline()
        if not line:
            return {"ok": False, "error": f"the worker stopped ({self._last_log_line() or 'no log'})"}
        try:
            return json.loads(line)
        except ValueError:
            return {"ok": False, "error": f"unreadable worker reply {line[:80]!r}"}

    def _last_log_line(self) -> str:
        if self._log is None:
            return ""
        try:
            self._log.flush()
            self._log.seek(0)
            lines = [ln.strip() for ln in self._log.read().splitlines() if ln.strip()]
        except (OSError, ValueError):
            return ""
        return scrub(lines[-1][:300], self.ref_wav.parent, Path.home()) if lines else ""

    def _ask(self, request: dict[str, Any]) -> dict[str, Any]:
        assert self._worker is not None and self._worker.stdin is not None
        try:
            self._worker.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self._worker.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise TTSError(f"{ENGINE} worker is gone ({self._last_log_line() or exc})") from exc
        return self._read_reply()

    # --- adapter ------------------------------------------------------------------------------------

    def voices(self) -> list[VoiceInfo]:
        return [VoiceInfo(id=vid, lang="en", label=spec.label) for vid, spec in MODELS.items()]

    def cache_tag(self) -> str:
        revs = ",".join(f"{s.kind}@{s.revision[:12]}" for s in MODELS.values())
        return f"chatterbox-{PACKAGE_VERSION};{revs};seed={self.seed};t={self.temperature};ref={self._ref_digest}"

    def generate(self, text: str, voice: str, lang: str) -> tuple[Any, int]:
        if self._backend is not None:
            try:
                return self._backend.generate(voice, text, seed=self.seed, temperature=self.temperature)
            except TTSError:
                raise
            except Exception as exc:  # the library raises many types
                message = scrub(f"{type(exc).__name__}: {exc}", self.ref_wav.parent, Path.home())
                raise TTSError(f"Chatterbox failed on {text[:40]!r}: {message}") from exc
        if self._worker is None or self._tmp is None:
            raise TTSError(f"{ENGINE} adapter is closed")
        out = self._tmp / "line.wav"
        reply = self._ask({"voice": voice, "text": text, "seed": self.seed, "temperature": self.temperature, "out": str(out)})
        if not reply.get("ok"):
            raise TTSError(f"Chatterbox failed on {text[:40]!r}: {reply.get('error', 'no reply')}")
        try:
            samples, rate = read_wav(out)
        finally:
            out.unlink(missing_ok=True)
        return samples, rate

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None
        worker, self._worker = self._worker, None
        if worker is not None:
            try:
                if worker.stdin is not None:
                    worker.stdin.close()  # the worker exits at end of input
                worker.wait(timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                worker.kill()
                worker.wait()
            if worker.stdout is not None:
                worker.stdout.close()
        if self._log is not None:
            self._log.close()
            self._log = None
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)  # the log and any line file
            self._tmp = None

    def __del__(self) -> None:  # a forgotten close must not leave a worker holding the GPU
        try:
            self.close()
        except Exception:
            pass


def _load_backend(target: str) -> Any:
    module_name, _, cls_name = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise TTSError(f"cannot load the Chatterbox backend {target!r}: {exc}") from exc
    return getattr(module, cls_name)

