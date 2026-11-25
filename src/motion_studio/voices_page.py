"""Build a local listening page that compares voices on the same sentences.

Each candidate (engine + voice) speaks the same test lines; the page shows an
audio player per line, the licence verdict, the real-time factor and any
error. Output goes under ``out/`` (gitignored) and is never published.
"""

from __future__ import annotations

import html
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .tts.base import TTSAdapter, TTSError, read_wav

SENTENCES = {
    "en": [
        "Retrieval-augmented generation, or RAG, lets a model look things up before it answers.",
        "In this video we build a small pipeline in Python, step by step, and measure what it costs.",
        "The reactor reaches 350 kelvin after 12 minutes, so we lower the feed rate by 5 percent.",
    ],
    "vi": [
        "Xin chào các bạn, hôm nay chúng ta sẽ tìm hiểu cách một mô hình ngôn ngữ trả lời câu hỏi.",
        "RAG là viết tắt của retrieval-augmented generation: mô hình tìm tài liệu trước, rồi mới trả lời.",
        "Nhiệt độ trong bình phản ứng tăng lên 350 độ K sau 12 phút, nên ta giảm lưu lượng nạp 5 phần trăm.",
    ],
}


@dataclass
class Candidate:
    """One voice to compare. ``make`` builds the adapter (lazily, so a missing engine is one error, not a crash)."""

    key: str  # unique, file-name safe
    title: str
    engine: str
    voice: str
    lang: str
    licence: str
    usable: str  # "yes", "no" or "check"; see docs/voices.md
    make: Callable[[], TTSAdapter]
    note: str = ""
    share: str = ""  # candidates with the same non-empty share key reuse one adapter (one model load)


@dataclass
class Sample:
    text: str
    file: str = ""
    duration_s: float = 0.0
    seconds: float = 0.0
    heard: str = ""  # what a speech recogniser heard (optional check)
    wer: float | None = None  # word error rate of ``heard`` against ``text``


@dataclass
class CandidateResult:
    key: str
    title: str
    engine: str
    voice: str
    lang: str
    licence: str
    usable: str
    note: str
    samples: list[Sample] = field(default_factory=list)
    error: str = ""

    @property
    def rtf(self) -> float | None:
        audio = sum(s.duration_s for s in self.samples)
        return sum(s.seconds for s in self.samples) / audio if audio else None

    @property
    def mean_wer(self) -> float | None:
        scores = [s.wer for s in self.samples if s.wer is not None]
        return sum(scores) / len(scores) if scores else None


def _words(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text.lower())
    return re.sub(r"[^\w\s]", " ", text).split()


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Word-level edit distance divided by the reference length (punctuation and case ignored)."""
    ref, hyp = _words(reference), _words(hypothesis)
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / max(1, len(ref))


Recogniser = Callable[[Path, str], str]


def whisper_recogniser(model: str = "large-v3-turbo", device: str = "auto") -> Recogniser:
    """A local faster-whisper recogniser (MIT; weights download to the Hugging Face cache)."""
    try:
        import numpy as np
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TTSError(f"the ASR check needs faster-whisper ({exc}); run `just tts-setup`") from exc
    if device == "auto":
        try:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
    asr = WhisperModel(model, device=device, compute_type="float16" if device == "cuda" else "int8")

    def recognise(path: Path, lang: str) -> str:
        data, rate = read_wav(path)
        audio = np.asarray(data, dtype=np.float32) / 32768.0
        if rate != 16000:  # linear resampling is enough for a speech check
            n = int(len(audio) * 16000 / rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        segments, _ = asr.transcribe(audio, language=lang, beam_size=5)
        return " ".join(seg.text.strip() for seg in segments)

    return recognise


def check_with_asr(results: list[CandidateResult], out_dir: Path, recognise: Recogniser) -> None:
    """Fill ``heard`` and ``wer`` for every sample."""
    for r in results:
        for s in r.samples:
            s.heard = recognise(Path(out_dir) / s.file, r.lang)
            s.wer = word_error_rate(s.text, s.heard)


def run_candidates(candidates: list[Candidate], out_dir: Path, sentences: dict[str, list[str]] | None = None) -> list[CandidateResult]:
    """Synthesise every test line with every candidate. Errors are recorded, not raised.

    Candidates with the same ``share`` key use one adapter; if building it fails,
    the error is recorded for each of them and the build is not retried. An
    adapter is closed after the last candidate that shares it, so only one model
    is in memory at a time when candidates of one share are listed together.
    Each candidate first speaks one untimed warm-up line, so the real-time
    factor leaves out model loading, downloads and GPU warm-up.
    """
    sentences = sentences or SENTENCES
    out_dir = Path(out_dir)
    results: list[CandidateResult] = []
    adapters: dict[str, TTSAdapter] = {}
    failed: dict[str, str] = {}
    last_use = {(cand.share or cand.key): n for n, cand in enumerate(candidates)}
    try:
        for n_cand, cand in enumerate(candidates):
            _close_finished(adapters, last_use, n_cand)
            res = CandidateResult(cand.key, cand.title, cand.engine, cand.voice, cand.lang, cand.licence, cand.usable, cand.note)
            results.append(res)
            share = cand.share or cand.key
            if share in failed:
                res.error = failed[share]
                continue
            try:
                adapter = adapters.get(share)
                if adapter is None:
                    adapter = cand.make()
                    adapters[share] = adapter
            except Exception as exc:  # any engine may fail to load; the page still shows the others
                failed[share] = res.error = f"{type(exc).__name__}: {exc}"
                continue
            try:
                warm = out_dir / "audio" / f".warmup-{cand.key}.wav"
                adapter.synthesize(sentences[cand.lang][0], cand.voice, cand.lang, warm)
                warm.unlink(missing_ok=True)
                for n, text in enumerate(sentences[cand.lang]):
                    rel = f"audio/{cand.key}-{n}.wav"
                    started = time.perf_counter()
                    line = adapter.synthesize(text, cand.voice, cand.lang, out_dir / rel)
                    res.samples.append(Sample(text=text, file=rel, duration_s=line.duration_s, seconds=time.perf_counter() - started))
            except Exception as exc:
                res.error = f"{type(exc).__name__}: {exc}"
    finally:
        for adapter in adapters.values():
            adapter.close()
    return results


def _close_finished(adapters: dict[str, TTSAdapter], last_use: dict[str, int], index: int) -> None:
    """Close the adapters that no candidate from ``index`` on still needs."""
    for share in [s for s in adapters if last_use[s] < index]:
        adapters.pop(share).close()


_CSS = """
:root { --bg:#f8fafc; --fg:#0f172a; --muted:#475569; --card:#ffffff; --line:#e2e8f0; --ok:#15803d; --no:#b91c1c; --check:#a16207; }
@media (prefers-color-scheme: dark) { :root { --bg:#0b1120; --fg:#e2e8f0; --muted:#94a3b8; --card:#111827; --line:#1f2937; --ok:#4ade80; --no:#f87171; --check:#facc15; } }
* { box-sizing: border-box; }
body { margin:0; padding:24px 16px; font:16px/1.5 system-ui, sans-serif; background:var(--bg); color:var(--fg); }
main { max-width:960px; margin:0 auto; }
h1 { font-size:1.6rem; margin:0 0 4px; } h2 { font-size:1.2rem; margin:32px 0 8px; }
p.lead { color:var(--muted); margin:0 0 16px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin:12px 0; }
.card h3 { margin:0 0 4px; font-size:1.05rem; }
.meta { color:var(--muted); font-size:.9rem; margin-bottom:8px; }
.tag { display:inline-block; padding:0 8px; border-radius:999px; border:1px solid currentColor; font-size:.8rem; margin-left:6px; }
.yes { color:var(--ok); } .no { color:var(--no); } .check { color:var(--check); }
.line { margin:10px 0; } .line p { margin:0 0 4px; }
audio { width:100%; max-width:520px; }
.err { color:var(--no); white-space:pre-wrap; font-family:ui-monospace, monospace; font-size:.85rem; }
"""

_USABLE = {"yes": "usable on a monetised channel", "no": "not usable (licence)", "check": "licence to check"}
_LANG = {"vi": "Vietnamese", "en": "English"}


def render_page(results: list[CandidateResult], title: str = "Voice comparison") -> str:
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body><main>",
        f"<h1>{html.escape(title)}</h1>",
        "<p class='lead'>Local listening test. Every voice reads the same lines. RTF = synthesis time / audio length "
        "(below 1 is faster than real time). The optional ASR check transcribes each line with a local speech "
        "recogniser; its word error rate is rough (a number read as words counts as an error). "
        "Licence details: docs/voices.md.</p>",
    ]
    for lang in ("vi", "en"):
        group = [r for r in results if r.lang == lang]
        if not group:
            continue
        parts.append(f"<h2>{_LANG.get(lang, lang)}</h2>")
        for r in group:
            rtf = f"RTF {r.rtf:.2f}" if r.rtf is not None else "RTF n/a"
            if r.mean_wer is not None:
                rtf += f" · ASR word error rate {r.mean_wer:.0%}"
            parts.append("<section class='card'>")
            parts.append(
                f"<h3>{html.escape(r.title)}<span class='tag {r.usable}'>{_USABLE.get(r.usable, r.usable)}</span></h3>"
                f"<div class='meta'>engine <code>{html.escape(r.engine)}</code> · voice <code>{html.escape(r.voice)}</code>"
                f" · licence {html.escape(r.licence)} · {rtf}</div>"
            )
            if r.note:
                parts.append(f"<div class='meta'>{html.escape(r.note)}</div>")
            for s in r.samples:
                parts.append(
                    f"<div class='line'><p>{html.escape(s.text)}</p>"
                    f"<audio controls preload='none' src='{html.escape(s.file)}'></audio>"
                    f"<div class='meta'>{s.duration_s:.1f} s audio, {s.seconds:.1f} s to make"
                    + (f" · heard: “{html.escape(s.heard)}” (WER {s.wer:.0%})" if s.wer is not None else "")
                    + "</div></div>"
                )
            if r.error:
                parts.append(f"<div class='err'>{html.escape(r.error)}</div>")
            parts.append("</section>")
    parts.append("</main></body></html>\n")
    return "".join(parts)


def build_page(
    candidates: list[Candidate],
    out_dir: Path,
    sentences: dict[str, list[str]] | None = None,
    recognise: Recogniser | None = None,
) -> Path:
    """Run every candidate and write ``index.html`` and ``results.json`` into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = run_candidates(candidates, out_dir, sentences)
    if recognise is not None:
        check_with_asr(results, out_dir, recognise)
    page = out_dir / "index.html"
    page.write_text(render_page(results), encoding="utf-8")
    data = [{**asdict(r), "rtf": r.rtf, "mean_wer": r.mean_wer} for r in results]
    (out_dir / "results.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return page


VI_PRESETS = [
    ("Trúc Ly", "VieNeu preset: female, northern accent, natural reading"),
    ("Minh Đức", "VieNeu preset: male, northern accent, news reading"),
    ("Thùy Dung", "VieNeu preset: female, southern accent, news reading"),
]
EN_VOICES = ["af_heart", "am_michael", "bf_emma"]
EN_CLONES = [
    ("clone", "Cloned voice, English (Chatterbox-Turbo)", "Zero-shot clone by Chatterbox-Turbo (350M), the faster model."),
    ("clone-standard", "Cloned voice, English (Chatterbox)", "Zero-shot clone by the original Chatterbox (500M)."),
]


def default_candidates(
    ref_wav: Path | None = None,
    device: str = "auto",
    langs: tuple[str, ...] = ("vi", "en"),
    ref_wav_en: Path | None = None,
) -> list[Candidate]:
    """The standard comparison: 3 VieNeu presets, the clone, 3 Kokoro voices and the two English clones.

    The clones appear only when a reference clip is given; the English clones use
    ``ref_wav_en``, else ``ref_wav``.
    """
    from .tts import get_adapter

    cands: list[Candidate] = []
    if "vi" in langs:
        def make_vieneu() -> TTSAdapter:
            return get_adapter("vieneu", device=device)

        def make_clone() -> TTSAdapter:
            return get_adapter("clone", device=device, ref_wav=ref_wav)

        for name, note in VI_PRESETS:
            cands.append(Candidate(
                key=f"vieneu-{len(cands)}", title=name, engine="vieneu", voice=name, lang="vi",
                licence="Apache-2.0 (code, weights)", usable="yes", make=make_vieneu, share="vieneu",
                note=note + ". Training data is not public; the author grants commercial use.",
            ))
        if ref_wav is not None:
            cands.append(Candidate(
                key="clone", title="Cloned voice (local reference)", engine="clone", voice="clone", lang="vi",
                licence="Apache-2.0 (code, weights)", usable="yes", make=make_clone, share="clone",
                note="Zero-shot clone made by VieNeu-TTS v3 Turbo from the reference clip; usable because the voice is your own.",
            ))
    if "en" in langs:
        def make_kokoro() -> TTSAdapter:
            return get_adapter("kokoro", device=device)

        for voice in EN_VOICES:
            cands.append(Candidate(
                key=f"kokoro-{voice}", title=f"Kokoro {voice}", engine="kokoro", voice=voice, lang="en",
                licence="Apache-2.0 (code, weights)", usable="yes", make=make_kokoro, share="kokoro",
                note="Voice provenance is not documented per voice; see docs/voices.md.",
            ))
        ref_en = ref_wav_en or ref_wav
        if ref_en is not None:
            def make_clone_en() -> TTSAdapter:
                return get_adapter("clone-en", device=device, ref_wav=ref_en)

            for voice, title, note in EN_CLONES:
                cands.append(Candidate(
                    key=f"clone-en-{voice}", title=title, engine="clone-en", voice=voice, lang="en",
                    licence="MIT (code, weights)", usable="yes", make=make_clone_en, share="clone-en",
                    note=note + " Usable because the voice is your own; every line carries the Perth watermark.",
                ))
    return cands


def mock_candidates(langs: tuple[str, ...] = ("vi", "en")) -> list[Candidate]:
    """Model-free candidates for tests and CI."""
    from .tts.mock import MockTTS

    shared: dict[str, MockTTS] = {}

    def make() -> TTSAdapter:
        return shared.setdefault("m", MockTTS())

    return [
        Candidate(key=f"mock-{lang}-{p}", title=f"Mock {p} Hz", engine="mock", voice=f"tone-{p}", lang=lang,
                  licence="n/a", usable="check", make=make, share="mock")
        for lang in langs for p in (220, 330)
    ]
