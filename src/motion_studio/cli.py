"""Command line: ``motion-studio validate|render|narrate|subtitles|...``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .render import RenderError, RenderOptions, plan, render, with_generated_visuals
from .schema import Scenes, ScenesError, load, load_schema
from .subtitles import DEFAULT_MODEL as DEFAULT_WHISPER_MODEL
from .theme import THEMES
from .visuals import GENERATED_KINDS, VisualError, missing_tools

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
        opts = RenderOptions(
            preset=args.preset, crf=args.crf, jobs=args.jobs, font=args.font, theme=args.theme,
            visuals_dir=args.visuals_dir, rebuild_visuals=args.rebuild_visuals,
        )
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
        except (RenderError, VisualError, ValueError, OSError) as exc:
            print(f"FAIL {exc}", file=sys.stderr)
            return 1
        print(f"OK   {result.output} ({result.width}x{result.height}, {result.fps} fps, {result.duration_s:.2f} s)")
        lines = _write_lines(scenes, lang, output, result.timeline_json())
        if args.subtitles == "none":
            for suffix in (".srt", ".burned.mp4"):  # files from an earlier render no longer match this video
                output.with_suffix(suffix).unlink(missing_ok=True)
        elif not _render_subtitles(args, output, lines, result.width, result.height, args.theme or scenes.theme):
            return 1
    return 0


def _render_subtitles(
    args: argparse.Namespace, output: Path, lines: list | None, width: int, height: int, theme: str | None = None
) -> bool:
    """Write the SRT (and the burned copy) for one render. False only when a requested burn failed."""
    from .subtitles import SubtitleError, burn, sidecar, write_srt

    srt = sidecar(output, ".srt")
    sidecar(output, ".burned.mp4").unlink(missing_ok=True)  # made again below only when asked; never an old cut
    if not lines:
        srt.unlink(missing_ok=True)
        print(f"WARN {srt.name} skipped: no narration timing for this render; run `motion-studio narrate`, "
              f"or `motion-studio subtitles {output} --scenes <scenes.json> --align`", file=sys.stderr)
        return args.subtitles != "burn"
    try:
        srt, cues = write_srt(output, lines, width, height)
        print(f"OK   {srt} ({len(cues)} cue(s))")
        if args.subtitles == "burn":
            burned = burn(output, srt, preset=args.preset, crf=args.crf, font=args.font, theme=theme)
            print(f"OK   {burned} (subtitles burned in)")
    except (SubtitleError, OSError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return False
    return True


def _write_lines(scenes: Scenes, lang: str, output: Path, timeline_text: str) -> list | None:
    """Best effort: if a narration timing file matches this render, place every line on the video clock.

    A missing or stale timing file never fails the render; a stale one removes any old lines file.
    """
    from .narrate import StaleTiming, check_timing, line_timeline, timing_path

    lines_path = output.with_suffix(".lines.json")
    tpath = timing_path(scenes.base_dir, lang)
    if not tpath.is_file():
        lines_path.unlink(missing_ok=True)
        return None
    try:
        timing = json.loads(tpath.read_text(encoding="utf-8"))
        check_timing(timing, scenes, lang)
        lines = line_timeline(timing, json.loads(timeline_text))
        lines_path.write_text(json.dumps(lines, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return lines
    except (StaleTiming, ValueError, KeyError, TypeError, OSError) as exc:
        lines_path.unlink(missing_ok=True)
        print(f"WARN {lines_path.name} skipped: {exc}; run `motion-studio narrate --lang {lang}` again", file=sys.stderr)
        return None


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


def _cmd_visuals(args: argparse.Namespace) -> int:
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
    opts = RenderOptions(jobs=args.jobs, visuals_dir=args.visuals_dir, rebuild_visuals=args.rebuild, theme=args.theme)
    for lang in langs:
        try:
            built, timeline = with_generated_visuals(scenes, lang, plan(scenes, lang, opts), opts)
        except (RenderError, VisualError, ValueError, OSError) as exc:
            print(f"FAIL {exc}", file=sys.stderr)
            return 1
        for scene, timing in zip(built.scenes, timeline):
            original = scenes.scenes[timing.index]["visual"]["kind"]
            if original in GENERATED_KINDS:
                print(f"OK   {lang} {timing.id} ({original}, {timing.duration_s:.2f} s): {scene['visual']['src']}")
    return 0


def _lang_of(video: Path) -> str | None:
    """``x.en.mp4`` -> ``en``."""
    parts = video.name.split(".")
    return parts[-2] if len(parts) >= 3 and len(parts[-2]) == 2 and parts[-2].isalpha() else None


def _narration_lines(scenes_file: str, lang: str) -> tuple[list[str], list[str]]:
    from .narrate import split_lines

    scenes = load(scenes_file)
    if lang not in scenes.languages:
        raise ScenesError([f"language {lang} is not in this file (has {', '.join(scenes.languages)})"], scenes_file)
    texts, ids = [], []
    for scene in scenes.scenes:
        for text in split_lines(scene["narration"][lang]):
            texts.append(text)
            ids.append(scene["id"])
    return texts, ids


def _cmd_subtitles(args: argparse.Namespace) -> int:
    from .subtitles import SubtitleError, burn, drift, sidecar, video_size, whisper_recogniser, write_srt

    video = Path(args.video)
    lang = args.lang or _lang_of(video)
    if not video.is_file():
        print(f"FAIL {video} not found", file=sys.stderr)
        return 1
    if not lang:
        print(f"FAIL cannot tell the language from {video.name}; pass --lang", file=sys.stderr)
        return 2
    if args.align and not args.scenes:
        print("FAIL --align needs --scenes (the narration text)", file=sys.stderr)
        return 2
    lines_path = sidecar(video, ".lines.json")
    if not args.align and not lines_path.is_file():
        print(f"FAIL no {lines_path.name}: render a narrated scenes.json, or pass --scenes and --align", file=sys.stderr)
        return 1
    words = None
    try:
        recognise = whisper_recogniser(args.model, args.device) if (args.align or args.check_drift) else None
        if args.align:
            from .subtitles import align_lines

            texts, ids = _narration_lines(args.scenes, lang)
            words = recognise(video, lang)
            lines = align_lines(texts, words, ids)
            lines_path.write_text(json.dumps(lines, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"OK   {lines_path} ({len(lines)} line(s) aligned with faster-whisper {args.model})")
        else:
            lines = json.loads(lines_path.read_text(encoding="utf-8"))
        w, h = video_size(video)
        srt, cues = write_srt(video, lines, w, h)
        print(f"OK   {srt} ({len(cues)} cue(s))")
        if args.burn:
            theme = args.theme or (load(args.scenes).theme if args.scenes else None)
            print(f"OK   {burn(video, srt, theme=theme)} (subtitles burned in)")
        if args.check_drift:
            if words is not None:
                print("NOTE drift after --align compares the cues with the words they were aligned from "
                      "(a self-consistency check only)", file=sys.stderr)
            report = drift(cues, words if words is not None else recognise(video, lang), lang, args.model)
            sidecar(video, ".drift.json").write_text(report.json(), encoding="utf-8")
            print(f"{'OK  ' if report.ok else 'FAIL'} {report.summary()}")
            if not report.ok:
                return 1
    except (SubtitleError, ScenesError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_tools(_: argparse.Namespace) -> int:
    status = 0
    for kind, why in missing_tools().items():
        if why:
            status = 1
        print(f"{'OK  ' if why is None else 'MISS'} {kind}" + (f": {why}" if why else ""))
    return status


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
    r.add_argument("--font", help="fontconfig font name for title cards and subtitles (default: the theme's)")
    r.add_argument("--theme", choices=sorted(THEMES), help="colours and fonts (default: the file's theme, else light)")
    r.add_argument("--visuals-dir", type=Path, help="cache for generated clips (default: <scenes dir>/.cache/visuals)")
    r.add_argument("--rebuild-visuals", action="store_true", help="build generated clips even when cached")
    r.add_argument("--subtitles", choices=["none", "srt", "burn"], default="srt",
                   help="srt: write <name>.<lang>.srt from the narration timing (default); "
                        "burn: also write <name>.<lang>.burned.mp4 with the subtitles drawn in")
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

    g = sub.add_parser("visuals", help="build the generated clips (manim, diagram, code, terminal) of a scenes.json")
    g.add_argument("file")
    g.add_argument("--lang", action="append", help="language to build (repeatable); default: all")
    g.add_argument("--jobs", type=_non_negative, default=0, help="parallel builds (default: 1)")
    g.add_argument("--visuals-dir", type=Path, help="cache for generated clips (default: <scenes dir>/.cache/visuals)")
    g.add_argument("--rebuild", action="store_true", help="build clips even when cached")
    g.add_argument("--theme", choices=sorted(THEMES), help="colours and fonts (default: the file's theme, else light)")
    g.set_defaults(func=_cmd_visuals)

    st = sub.add_parser("subtitles", help="make the SRT of a rendered video, burn it in, or check its drift with faster-whisper")
    st.add_argument("video", help="a rendered <name>.<lang>.mp4 (its .lines.json is used when present)")
    st.add_argument("--lang", help="language (default: from the file name)")
    st.add_argument("--scenes", help="scenes.json with the narration (needed by --align)")
    st.add_argument("--align", action="store_true", help="time the narration lines from faster-whisper word timestamps")
    st.add_argument("--burn", action="store_true", help="also write <name>.<lang>.burned.mp4")
    st.add_argument("--theme", choices=sorted(THEMES),
                    help="subtitle box colours for --burn (default: the theme of --scenes, else light)")
    st.add_argument("--check-drift", action="store_true",
                    help="compare cue starts with faster-whisper word starts; exit 1 when the drift gate fails (writes .drift.json)")
    st.add_argument("--model", default=DEFAULT_WHISPER_MODEL, help=f"faster-whisper model (default: {DEFAULT_WHISPER_MODEL})")
    st.add_argument("--device", default="cpu", help="cpu (default), cuda or auto")
    st.set_defaults(func=_cmd_subtitles)

    t = sub.add_parser("tools", help="report which visual adapters can run on this machine (exit 1 if any cannot)")
    t.set_defaults(func=_cmd_tools)

    s = sub.add_parser("schema", help="print the scenes.json JSON Schema")
    s.set_defaults(func=_cmd_schema)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
