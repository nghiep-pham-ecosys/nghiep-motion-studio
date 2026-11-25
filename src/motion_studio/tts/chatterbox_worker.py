"""Worker process of the ``clone-en`` engine (runs in the Chatterbox venv).

Protocol: one JSON object per line. After start the worker sends
``{"ok": true}`` (or ``{"ok": false, "error": ...}`` and exits). Each request
``{"voice", "text", "seed", "temperature", "out"}`` makes one line of audio,
written as 16-bit mono WAV to ``out``, and gets ``{"ok": true, "rate", "frames"}``
or ``{"ok": false, "error"}``. The worker exits at the end of its input.

The libraries print to stdout, so the protocol uses a private copy of the
original stdout and everything else goes to stderr. Error messages never
contain the reference clip's folder or the home folder.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import IO, Any

from .base import write_wav
from .chatterbox_tts import DEFAULT_BACKEND, _load_backend, scrub


def _reply(channel: IO[str], data: dict[str, Any]) -> None:
    channel.write(json.dumps(data, ensure_ascii=False) + "\n")
    channel.flush()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="chatterbox_worker")
    p.add_argument("--backend", default=DEFAULT_BACKEND)
    p.add_argument("--device", default="auto")
    p.add_argument("--models-dir", required=True)
    p.add_argument("--ref-wav", required=True)
    args = p.parse_args(argv)

    channel = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())  # library prints can no longer break the protocol
    sys.stdout = sys.stderr
    ref = Path(args.ref_wav)

    def clean(exc: BaseException) -> str:
        return scrub(f"{type(exc).__name__}: {exc}", ref.parent, Path.home())[:500]

    try:
        backend = _load_backend(args.backend)(device=args.device, models_dir=Path(args.models_dir), ref_wav=ref)
    except Exception as exc:
        _reply(channel, {"ok": False, "error": clean(exc)})
        return 1
    _reply(channel, {"ok": True})
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            samples, rate = backend.generate(req["voice"], req["text"], seed=req.get("seed"),
                                             temperature=float(req.get("temperature", 0.8)))
            frames = write_wav(Path(req["out"]), samples, rate)
            _reply(channel, {"ok": True, "rate": rate, "frames": frames})
        except Exception as exc:  # report and keep serving: one bad line must not kill the run
            _reply(channel, {"ok": False, "error": clean(exc)})
    backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
