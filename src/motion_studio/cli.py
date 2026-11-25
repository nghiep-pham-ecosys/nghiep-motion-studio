"""Command line: ``motion-studio validate|render``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .render import RenderError, RenderOptions, render
from .schema import Scenes, ScenesError, load, load_schema

REF_ENV = "MOTION_STUDIO_REF_WAV"


def output_stem(path: Path) -> str:
    """Name outputs after the folder of a scenes.json, else after the file."""
    resolved = path.resolve()
    stem = resolved.parent.name if resolved.name == "scenes.json" else resolved.stem
    return stem or "video"


def _non_negative(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def _crf(text: str) -> int:
    value = int(text)
    if not 0 <= value <= 51:
        raise argparse.ArgumentTypeError("must be between 0 and 51")
    return value


def _cmd_validate(args: argparse.Namespace) -> int:
    status = 0
    for path in args.files:
        try:
            scenes = load(path, check_files=not args.no_files)
        except (ScenesError, OSError) as exc:
            print(f"FAIL {exc}", file=sys.stderr)
            status = 1
            continue
        print(f"OK   {path}: {len(scenes.scenes)} scene(s), languages {', '.join(scenes.languages)}")
    return status


def _cmd_render(args: argparse.Namespace) -> int:
    try:
        scenes = load(args.file)
    except (ScenesError, OSError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    langs = args.lang or scenes.languages
    unknown = [lang for lang in langs if lang not in scenes.languages]
    if unknown:
        print(f"FAIL language(s) {', '.join(unknown)} not in {args.file} (has {', '.join(scenes.languages)})", file=sys.stderr)
        return 2
    try:
        opts = RenderOptions(preset=args.preset, crf=args.crf, jobs=args.jobs, font=args.font)
    except ValueError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir)
    stem = output_stem(Path(args.file))
    for lang in langs:
        output = out_dir / f"{stem}.{lang}.mp4"
        try:
            result = render(scenes, lang, output, opts)
            output.with_suffix(".timeline.json").write_text(result.timeline_json(), encoding="utf-8")
        except (RenderError, ValueError, OSError) as exc:
            print(f"FAIL {exc}", file=sys.stderr)
            return 1
        print(f"OK   {result.output} ({result.width}x{result.height}, {result.fps} fps, {result.duration_s:.2f} s)")
        _write_lines(scenes, lang, output, result.timeline_json())
    return 0


def _write_lines(scenes: Scenes, lang: str, output: Path, timeline_text: str) -> None:
    """Best effort: if a narration timing file matches this render, place every line on the video clock.

    A missing or stale timing file never fails the render; a stale one removes any old lines file.
    """
    from .narrate import StaleTiming, check_timing, line_timeline, timing_path

    lines_path = output.with_suffix(".lines.json")
    tpath = timing_path(scenes.base_dir, lang)
    if not tpath.is_file():
        lines_path.unlink(missing_ok=True)
        return
    try:
        timing = json.loads(tpath.read_text(encoding="utf-8"))
        check_timing(timing, scenes, lang)
        lines = line_timeline(timing, json.loads(timeline_text))
        lines_path.write_text(json.dumps(lines, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (StaleTiming, ValueError, KeyError, TypeError, OSError) as exc:
        lines_path.unlink(missing_ok=True)
        print(f"WARN {lines_path.name} skipped: {exc}; run `motion-studio narrate --lang {lang}` again", file=sys.stderr)


def _ref_wav(args: argparse.Namespace) -> Path | None:
    value = args.ref_wav or os.environ.get(REF_ENV) or ""
    return Path(value).expanduser() if value else None


def _cmd_narrate(args: argparse.Namespace) -> int:
    from .narrate import narrate, save_scenes, timing_path
    from .tts import TTSError, get_adapter

    try:
        scenes = load(args.file)
    except (ScenesError, OSError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    if args.lang not in scenes.languages:
        print(f"FAIL language {args.lang} not in {args.file} (has {', '.join(scenes.languages)})", file=sys.stderr)
        return 2
    options: dict[str, object] = {"device": args.device}
    ref = _ref_wav(args)
    if ref is not None and (args.engine == "clone" or args.voice == "clone"):
        options["ref_wav"] = ref
    try:
        adapter = get_adapter(args.engine, **options)
        try:
            result = narrate(scenes, args.lang, adapter, args.voice, pause_s=args.pause, force=args.force)
        finally:
            adapter.close()
        save_scenes(scenes, Path(args.file), args.lang)
    except (TTSError, ScenesError, ValueError, OSError, RuntimeError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    total = sum(s.duration_s for s in result.scenes)
    print(
        f"OK   {args.file} [{args.lang}] {result.engine}:{result.voice}: {len(result.scenes)} scene(s), "
        f"{total:.1f} s of narration, {result.synthesized} line(s) made, {result.reused} reused; timing in {timing_path(scenes.base_dir, args.lang, result.audio_dir)}"
    )
    return 0


def _cmd_voices(args: argparse.Namespace) -> int:
    from .tts import TTSError
    from .voices_page import build_page, default_candidates, mock_candidates, whisper_recogniser

    langs = tuple(args.lang or ("vi", "en"))
    if args.mock:
        cands = mock_candidates(langs)
    else:
        cands = default_candidates(ref_wav=_ref_wav(args), device=args.device, langs=langs)
    recognise = None
    if args.asr:
        try:
            recognise = whisper_recogniser(device=args.device)
        except TTSError as exc:
            print(f"FAIL {exc}", file=sys.stderr)
            return 1
    page = build_page(cands, Path(args.out_dir), recognise=recognise)
    print(f"OK   {page.resolve()}")
    return 0


def _cmd_schema(_: argparse.Namespace) -> int:
    print(json.dumps(load_schema(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="motion-studio", description="Scene-based video renderer (scenes.json -> mp4).")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="check scenes.json files against the schema")
    v.add_argument("files", nargs="+")
    v.add_argument("--no-files", action="store_true", help="skip the check that referenced files exist")
    v.set_defaults(func=_cmd_validate)

    r = sub.add_parser("render", help="render a scenes.json to one mp4 per language")
    r.add_argument("file")
    r.add_argument("--lang", action="append", help="language to render (repeatable); default: all")
    r.add_argument("-o", "--out-dir", default="out", help="output folder (default: out)")
    r.add_argument("--preset", default="medium", help="x264 preset (default: medium)")
    r.add_argument("--crf", type=_crf, default=20, help="x264 quality, lower is better (default: 20)")
    r.add_argument("--jobs", type=_non_negative, default=0, help="parallel scene encodes (default: automatic)")
    r.add_argument("--font", default="DejaVu Sans", help="fontconfig font name for title cards")
    r.set_defaults(func=_cmd_render)

    n = sub.add_parser("narrate", help="synthesise narration audio for one language and record per-line timing")
    n.add_argument("file")
    n.add_argument("--lang", required=True)
    n.add_argument("--engine", required=True, help="mock, kokoro, vieneu or clone")
    n.add_argument("--voice", required=True, help="engine voice id (clone: 'clone')")
    n.add_argument("--ref-wav", help=f"reference WAV for the clone voice (default: ${REF_ENV})")
    n.add_argument("--device", default="auto", help="auto, cuda or cpu")
    n.add_argument("--pause", type=float, default=0.3, help="silence between lines in seconds (default: 0.3)")
    n.add_argument("--force", action="store_true", help="synthesise every line again")
    n.set_defaults(func=_cmd_narrate)

    vo = sub.add_parser("voices", help="build a local listening page that compares voices")
    vo.add_argument("-o", "--out-dir", default="out/voices")
    vo.add_argument("--lang", action="append", choices=["vi", "en"], help="languages to include (default: both)")
    vo.add_argument("--ref-wav", help=f"reference WAV for the cloned voice (default: ${REF_ENV}; skipped if unset)")
    vo.add_argument("--device", default="auto")
    vo.add_argument("--mock", action="store_true", help="use the model-free test engine")
    vo.add_argument("--asr", action="store_true", help="transcribe every sample with local faster-whisper and show the word error rate")
    vo.set_defaults(func=_cmd_voices)

    s = sub.add_parser("schema", help="print the scenes.json JSON Schema")
    s.set_defaults(func=_cmd_schema)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
