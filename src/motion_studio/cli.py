"""Command line: ``motion-studio validate|render``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .render import RenderError, RenderOptions, render
from .schema import ScenesError, load, load_schema


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

    s = sub.add_parser("schema", help="print the scenes.json JSON Schema")
    s.set_defaults(func=_cmd_schema)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
