"""Command line: ``motion-studio video|validate|render|narrate|subtitles|gate|shorts|stage-upload|...``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .gate import MAX_DURATION_S as GATE_MAX_S, MIN_DURATION_S as GATE_MIN_S
from .render import RenderError, RenderOptions, plan, render, with_generated_visuals
from .schema import Scenes, ScenesError, load, load_schema
from .subtitles import DEFAULT_MODEL as DEFAULT_WHISPER_MODEL
from .theme import THEMES
from .visuals import GENERATED_KINDS, VisualError, missing_tools
from . import workdir
from .workdir import WorkDirError

REF_ENV = "MOTION_STUDIO_REF_WAV"
REF_EN_ENV = "MOTION_STUDIO_REF_WAV_EN"


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


def _tag(text: str) -> tuple[str, str]:
    key, sep, value = text.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError("use KEY=VALUE")
    return key.strip(), value


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
            visuals_dir=args.visuals_dir, rebuild_visuals=args.rebuild_visuals, normalize_audio=not args.no_loudnorm,
            metadata=dict(args.metadata or []),
        )
    except ValueError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    stem = output_stem(Path(args.file))
    try:
        if args.out_dir is not None:
            return _render_langs(args, scenes, langs, opts, Path(args.out_dir), stem)
        root, cap = workdir.work_dir(), workdir.cap_bytes()
        with workdir.active_job(workdir.job_dir(stem, root)) as job:
            _prune(root, cap, job)
            try:
                return _render_langs(args, scenes, langs, opts, job, stem)
            finally:
                _prune(root, cap, job)
    except (WorkDirError, OSError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


def _prune(root: Path, cap: int, current: Path | None) -> None:
    """Hold the work dir to its cap, oldest jobs first; never the current job or one another render holds."""
    result = workdir.enforce_cap(root, cap, current)
    for name, freed in result.pruned:
        print(f"NOTE work dir over its {workdir.human(cap)} cap: removed the renders of {name} ({workdir.human(freed)})")
    if result.skipped_active:
        print(f"NOTE skipped job(s) being rendered: {', '.join(result.skipped_active)}", file=sys.stderr)
    if result.over_cap:
        print(f"WARN work dir {root} holds {workdir.human(result.after)}, over its {workdir.human(cap)} cap, "
              "with only active jobs left", file=sys.stderr)


def _render_langs(args: argparse.Namespace, scenes: Scenes, langs: list[str], opts: RenderOptions, out_dir: Path,
                  stem: str) -> int:
    """Render every language into ``out_dir``; intermediates and the visuals cache go there too."""
    if opts.visuals_dir is None:
        opts.visuals_dir = out_dir / "visuals"
    opts.temp_dir = out_dir
    for lang in langs:
        output = out_dir / f"{stem}.{lang}.mp4"
        try:
            result = render(scenes, lang, output, opts)
            output.with_suffix(".timeline.json").write_text(result.timeline_json(), encoding="utf-8")
        except (RenderError, VisualError, ValueError, OSError) as exc:
            print(f"FAIL {exc}", file=sys.stderr)
            return 1
        print(f"OK   {result.output} ({result.width}x{result.height}, {result.fps} fps, {result.duration_s:.2f} s)")
        _print_loudness(output, result.loudness)
        lines = _write_lines(scenes, lang, output, result.timeline_json())
        if args.subtitles == "none":
            for suffix in (".srt", ".burned.mp4"):  # files from an earlier render no longer match this video
                output.with_suffix(suffix).unlink(missing_ok=True)
        elif not _render_subtitles(args, output, lines, result.width, result.height, args.theme or scenes.theme):
            return 1
    return 0


def _print_loudness(output: Path, loudness: dict) -> None:
    """One line on what happened to the mix; a mix the gate will refuse is a WARN."""
    status = loudness.get("status")
    if status == "normalised":
        print(f"OK   {output.name} loudness: {loudness['input_i']} -> {loudness['output_i']} LUFS, true peak "
              f"{loudness['output_tp']} dBTP ({loudness['mode']}; target {loudness['target_lufs']:g} LUFS)")
    elif status == "silent":
        print(f"WARN {output.name} loudness not normalised: the audio is silent (no narration for this language); "
              "the gate's loudness check will fail", file=sys.stderr)
    elif status == "off":
        print(f"WARN {output.name} loudness not normalised (--no-loudnorm); the gate's loudness check may fail",
              file=sys.stderr)


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


def _ref_wav_en(args: argparse.Namespace) -> Path | None:
    """The reference for the English clone: its own clip if given, else the general one."""
    value = getattr(args, "ref_wav_en", None) or os.environ.get(REF_EN_ENV) or ""
    return Path(value).expanduser() if value else _ref_wav(args)


def engine_options(engine: str, voices: set[str], args: argparse.Namespace) -> dict[str, object]:
    """Options for one engine of a narration run: the device, and a reference clip where a clone needs one."""
    options: dict[str, object] = {"device": args.device}
    if engine == "clone-en":
        ref = _ref_wav_en(args)
    elif engine == "clone" or "clone" in voices:
        ref = _ref_wav(args)
    else:
        ref = None
    if ref is not None:
        options["ref_wav"] = ref
    return options


def _cmd_narrate(args: argparse.Namespace) -> int:
    from .narrate import narrate, save_scenes, timing_path
    from .tts import TTSAdapter, TTSError, get_adapter
    from .voice_mix import plan_voices

    try:
        scenes = load(args.file)
    except (ScenesError, OSError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    if args.lang not in scenes.languages:
        print(f"FAIL language {args.lang} not in {args.file} (has {', '.join(scenes.languages)})", file=sys.stderr)
        return 2
    if bool(args.engine) != bool(args.voice):
        print("FAIL pass --engine and --voice together (the default voice)", file=sys.stderr)
        return 2
    try:
        choices = plan_voices(scenes.doc, args.lang, (args.engine, args.voice) if args.engine else None)
    except ValueError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    wanted: dict[str, set[str]] = {}
    for choice in choices:
        wanted.setdefault(choice.engine, set()).add(choice.voice)
    adapters: dict[str, TTSAdapter] = {}
    try:
        try:
            for engine, voices in wanted.items():
                adapters[engine] = get_adapter(engine, **engine_options(engine, voices, args))
            result = narrate(scenes, args.lang, adapters, choices=choices, pause_s=args.pause, force=args.force)
        finally:
            for adapter in adapters.values():
                adapter.close()
        save_scenes(scenes, Path(args.file), args.lang)
    except (TTSError, ScenesError, ValueError, OSError, RuntimeError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    total = sum(s.duration_s for s in result.scenes)
    voices_used = ", ".join(sorted({f"{s.engine}:{s.voice}" for s in result.scenes}))
    print(
        f"OK   {args.file} [{args.lang}] {voices_used}: {len(result.scenes)} scene(s), "
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
        cands = default_candidates(ref_wav=_ref_wav(args), device=args.device, langs=langs, ref_wav_en=_ref_wav_en(args))
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
    try:
        if args.visuals_dir is not None:
            return _build_visuals(scenes, langs, opts)
        root, cap = workdir.work_dir(), workdir.cap_bytes()
        with workdir.active_job(workdir.job_dir(output_stem(Path(args.file)), root)) as job:
            opts.visuals_dir = job / "visuals"
            _prune(root, cap, job)
            return _build_visuals(scenes, langs, opts)
    except (WorkDirError, OSError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


def _build_visuals(scenes: Scenes, langs: list[str], opts: RenderOptions) -> int:
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


def _cmd_gate(args: argparse.Namespace) -> int:
    from .gate import Limits, run_drift, run_gate, sidecar

    video = Path(args.video)
    lang = args.lang or _lang_of(video)
    if not video.is_file():
        print(f"FAIL {video} not found", file=sys.stderr)
        return 1
    if not lang:
        print(f"FAIL cannot tell the language from {video.name}; pass --lang", file=sys.stderr)
        return 2
    limits = Limits(min_duration_s=args.min_s, max_duration_s=args.max_s)
    if limits.min_duration_s > limits.max_duration_s:
        print("FAIL --min-s is larger than --max-s", file=sys.stderr)
        return 2
    if args.check_drift:
        from .subtitles import SubtitleError, whisper_recogniser

        try:
            run_drift(video, lang, whisper_recogniser(args.model, args.device), args.model)
        except (SubtitleError, OSError, ValueError) as exc:
            print(f"WARN drift not measured: {exc}", file=sys.stderr)  # the drift check below then fails
    report = run_gate(video, Path(args.scenes), lang, limits)
    out = Path(args.report) if args.report else sidecar(video, ".gate.json")
    out.write_text(report.json(), encoding="utf-8")
    print(report.summary())
    print(f"     report: {out}")
    return 0 if report.ok else 1


def _cmd_shorts(args: argparse.Namespace) -> int:
    from .shorts import ShortOptions, ShortsError, make_shorts

    video = Path(args.video)
    if video.name.endswith(".burned.mp4"):
        print(f"FAIL {video.name} already has subtitles burned in; pass the clean <name>.<lang>.mp4", file=sys.stderr)
        return 1
    lang = args.lang or _lang_of(video)
    if not lang:
        print(f"FAIL cannot tell the language from {video.name}; pass --lang", file=sys.stderr)
        return 2
    try:
        opts = ShortOptions(count=args.count, min_s=args.min_s, max_s=args.max_s, theme=args.theme,
                            preset=args.preset, crf=args.crf)
    except ValueError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    scenes_file = Path(args.scenes) if args.scenes else video.parent / "scenes.json"
    doc, theme = None, None
    try:
        if args.scenes or scenes_file.is_file():
            scenes = load(scenes_file)
            doc, theme = scenes.doc, scenes.theme
        else:
            print(f"NOTE no scenes.json given or next to {video.name}: every scene start counts as a mark, "
                  "but no chapter marks or titles (pass --scenes)", file=sys.stderr)
        parent = video.resolve().parent
        if workdir.is_job(parent) and not args.dry_run:
            with workdir.active_job(parent):
                result = make_shorts(video, lang, doc, opts, theme)
        else:
            result = make_shorts(video, lang, doc, opts, theme, dry_run=args.dry_run)
    except (ShortsError, ScenesError, WorkDirError, OSError, ValueError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    for clip in result.clips:
        w = clip.window
        where = f"{w.start_s:.2f}-{w.end_s:.2f} s ({w.duration_s:.2f} s, {w.start_kind} -> {w.end_kind})"
        title = f" [{w.chapter}]" if w.chapter else ""
        if args.dry_run:
            print(f"OK   {clip.path.name}: {where}{title}")
            continue
        m = clip.measured
        print(f"{'OK  ' if clip.loudness_ok else 'FAIL'} {clip.path}: {where}{title}, {clip.cues} cue(s), "
              f"{m['integrated_lufs']:.1f} LUFS, true peak {m['true_peak_dbtp']:.1f} dBTP")
    if len(result.clips) < result.wanted:
        print(f"FAIL only {len(result.clips)} of {result.wanted} Shorts windows of {opts.min_s:g}-{opts.max_s:g} s "
              f"fit {video.name} ({result.duration_s:.1f} s); nothing is padded", file=sys.stderr)
        return 1
    if not args.dry_run and not result.ok:
        print("FAIL a Short is outside the gate's loudness targets", file=sys.stderr)
        return 1
    if result.report:
        print(f"OK   report: {result.report}")
    return 0


def _cmd_clean(args: argparse.Namespace) -> int:
    try:
        root = workdir.work_dir()
        if args.after_upload:
            result = workdir.clean_after_upload(Path(args.after_upload), root, copy_to=args.copy_to)
            for item in result.copied:
                print(f"OK   copied {item['path']} -> {item['to']}")
            print(f"OK   {result.job}: removed {len(result.deleted)} file(s), {workdir.human(result.freed)}; "
                  "kept the upload record, subtitles, scenes.json and the script")
            return 0
        cap = workdir.cap_bytes(args.cap_gb)
        _prune(root, cap, None)
        print(f"OK   work dir {root}: {workdir.human(workdir.tree_size(root) if root.is_dir() else 0)} "
              f"(cap {workdir.human(cap)})")
        return 0
    except (WorkDirError, OSError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


def _cmd_stage_upload(args: argparse.Namespace) -> int:
    from .publish import PublishError, build, stage

    try:
        text = Path(args.description_file).read_text(encoding="utf-8") if args.description_file else None
        tags = [t for t in (args.tags or "").split(",") if t.strip()]
        common = dict(text=text, tags=tags, shorts=args.shorts, shorts_every_days=args.shorts_every_days,
                      allow_gate_limits=args.allow_gate_limits)
        if args.dry_run:
            tag, manifest, files = build(Path(args.video), Path(args.scenes), args.publish_at, **common)
            print(json.dumps(manifest, indent=2, ensure_ascii=False))
            print(f"OK   dry run: {len(files)} file(s) would go to the draft release {tag}; nothing staged")
            return 0
        staged = stage(Path(args.video), Path(args.scenes), args.publish_at, repo=args.repo, **common)
    except (PublishError, ScenesError, WorkDirError, OSError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    for item in staged.manifest["items"]:
        print(f"OK   {item['key']}: {item['title']!r}, private until {item['publish_at']}")
    print(f"OK   staged {len(staged.files)} file(s) and manifest.json on the draft release {staged.tag} "
          f"of {staged.repo} (not public)")
    print(f"     record: {staged.record}")
    print(f"     next, after the batch is approved: gh workflow run youtube-upload.yml --repo {staged.repo} "
          f"-f tag={staged.tag} -f live=true; then motion-studio finish-upload {staged.record}")
    return 0


def _cmd_finish_upload(args: argparse.Namespace) -> int:
    from .publish import PublishError, finish

    try:
        done = finish(Path(args.staged), result_file=Path(args.result) if args.result else None, run_id=args.run,
                      clean=not args.no_clean, copy_to=args.copy_to)
    except (PublishError, WorkDirError, OSError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    for entry in done.files:
        print(f"OK   {entry['path']} -> {entry['remote_id']} (private until {entry['publish_at']})")
    print(f"OK   upload record: {done.record}")
    if done.cleaned:
        print(f"OK   {done.cleaned.job}: removed {len(done.cleaned.deleted)} file(s), {workdir.human(done.cleaned.freed)}")
    if done.note:
        print(f"NOTE {done.note}")
    return 0


def _cmd_video(args: argparse.Namespace) -> int:
    from .pipeline import STEPS, Pipeline, PipelineError, load_topic

    try:
        topic = load_topic(args.topic)
        pipe = Pipeline(topic, langs=args.lang, stand_in=args.stand_in,
                        tts_python=Path(args.tts_python).expanduser() if args.tts_python else None,
                        jobs=args.jobs, from_step=args.from_step or STEPS[0], until_step=args.until or STEPS[-1])
        report = pipe.run(dry_run=args.dry_run)
    except (PipelineError, WorkDirError, OSError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    return 0 if report.ok else 1


def _cmd_disk(_: argparse.Namespace) -> int:
    try:
        report = workdir.disk_report(workdir.work_dir(), workdir.cap_bytes())
    except (WorkDirError, OSError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    print("\n".join(report.lines()))
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

    from .pipeline import STEPS

    vd = sub.add_parser("video", help="one command from a topic file to a gated video and its Shorts: "
                        f"{' -> '.join(STEPS)}; stops at the first failing step")
    vd.add_argument("topic", help="a topic file (JSON; see docs/spec.md, 'One command')")
    vd.add_argument("--lang", action="append", help="language to make (repeatable); default: the topic file's")
    vd.add_argument("--stand-in", action="store_true",
                    help="when the narrator's reference clip is not set, read the narrator's lines with the casting "
                         "preset's stand-in voice (written into the video's metadata and the report) instead of stopping")
    vd.add_argument("--from", dest="from_step", choices=STEPS,
                    help="skip the steps before this one (script and scenes always run); for a re-run after a fix")
    vd.add_argument("--until", choices=STEPS, help="stop after this step")
    vd.add_argument("--jobs", type=int, default=1,
                    help="parallel scene encodes in the render step (default: 1; each 1080p encode can use ~1.3 GB of RAM)")
    vd.add_argument("--tts-python", help="python of the TTS venv for the voice and subtitles steps "
                    "(default: $MOTION_STUDIO_TTS_VENV/bin/python, else ~/.cache/nghiep-motion-studio/tts-venv)")
    vd.add_argument("--dry-run", action="store_true", help="check the topic and the casting, print the steps; run nothing")
    vd.set_defaults(func=_cmd_video)

    v = sub.add_parser("validate", help="check scenes.json files against the schema")
    v.add_argument("files", nargs="+")
    v.add_argument("--no-files", action="store_true", help="skip the check that referenced files exist")
    v.set_defaults(func=_cmd_validate)

    r = sub.add_parser("render", help="render a scenes.json to one mp4 per language")
    r.add_argument("file")
    r.add_argument("--lang", action="append", help="language to render (repeatable); default: all")
    r.add_argument("-o", "--out-dir",
                   help=f"output folder (default: <work dir>/<name>, where the work dir is ${workdir.WORK_DIR_ENV} "
                        "or ~/.cache/nghiep-motion-studio/work, held to its size cap)")
    r.add_argument("--preset", default="medium", help="x264 preset (default: medium)")
    r.add_argument("--crf", type=_crf, default=20, help="x264 quality, lower is better (default: 20)")
    r.add_argument("--jobs", type=_non_negative, default=0, help="parallel scene encodes (default: automatic)")
    r.add_argument("--font", help="fontconfig font name for title cards and subtitles (default: the theme's)")
    r.add_argument("--theme", choices=sorted(THEMES), help="colours and fonts (default: the file's theme, else light)")
    r.add_argument("--visuals-dir", type=Path, help="cache for generated clips (default: <output folder>/visuals)")
    r.add_argument("--rebuild-visuals", action="store_true", help="build generated clips even when cached")
    r.add_argument("--no-loudnorm", action="store_true",
                   help="keep the audio mix as it is, without loudness normalisation (drafts; the gate needs it on)")
    r.add_argument("--metadata", type=_tag, action="append", metavar="KEY=VALUE",
                   help="extra mp4 tag (repeatable), e.g. comment=stand-in narrator; the title tag comes from scenes.json")
    r.add_argument("--subtitles", choices=["none", "srt", "burn"], default="srt",
                   help="srt: write <name>.<lang>.srt from the narration timing (default); "
                        "burn: also write <name>.<lang>.burned.mp4 with the subtitles drawn in")
    r.set_defaults(func=_cmd_render)

    n = sub.add_parser("narrate", help="synthesise narration audio for one language and record per-line timing")
    n.add_argument("file")
    n.add_argument("--lang", required=True)
    n.add_argument("--engine", help="default engine for scenes that scenes.json gives no voice: mock, kokoro, vieneu, clone or clone-en")
    n.add_argument("--voice", help="default voice id of that engine (clone and clone-en: 'clone')")
    n.add_argument("--ref-wav", help=f"reference WAV for the cloned voice (default: ${REF_ENV})")
    n.add_argument("--ref-wav-en", help=f"reference WAV for the English clone (default: ${REF_EN_ENV}, else --ref-wav)")
    n.add_argument("--device", default="auto", help="auto, cuda or cpu")
    n.add_argument("--pause", type=float, default=0.3, help="silence between lines in seconds (default: 0.3)")
    n.add_argument("--force", action="store_true", help="synthesise every line again")
    n.set_defaults(func=_cmd_narrate)

    vo = sub.add_parser("voices", help="build a local listening page that compares voices")
    vo.add_argument("-o", "--out-dir", default="out/voices")
    vo.add_argument("--lang", action="append", choices=["vi", "en"], help="languages to include (default: both)")
    vo.add_argument("--ref-wav", help=f"reference WAV for the cloned voice (default: ${REF_ENV}; skipped if unset)")
    vo.add_argument("--ref-wav-en", help=f"reference WAV for the English clone (default: ${REF_EN_ENV}, else --ref-wav; skipped if unset)")
    vo.add_argument("--device", default="auto")
    vo.add_argument("--mock", action="store_true", help="use the model-free test engine")
    vo.add_argument("--asr", action="store_true", help="transcribe every sample with local faster-whisper and show the word error rate")
    vo.set_defaults(func=_cmd_voices)

    g = sub.add_parser("visuals", help="build the generated clips (manim, diagram, code, terminal) of a scenes.json")
    g.add_argument("file")
    g.add_argument("--lang", action="append", help="language to build (repeatable); default: all")
    g.add_argument("--jobs", type=_non_negative, default=0, help="parallel builds (default: 1)")
    g.add_argument("--visuals-dir", type=Path, help="cache for generated clips (default: <work dir>/<name>/visuals)")
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

    gt = sub.add_parser("gate", help="quality gate for a rendered video: duration, loudness, black frames, "
                        "subtitle drift, fact check; exit 1 when any check fails (writes .gate.json)")
    gt.add_argument("video", help="a rendered <name>.<lang>.mp4 (its .timeline.json, .srt and .drift.json are read)")
    gt.add_argument("--scenes", required=True, help="the scenes.json it was rendered from (factcheck.md sits next to it)")
    gt.add_argument("--lang", help="language (default: from the file name)")
    gt.add_argument("--min-s", type=float, default=GATE_MIN_S, help=f"shortest allowed length in seconds (default: {GATE_MIN_S:g})")
    gt.add_argument("--max-s", type=float, default=GATE_MAX_S, help=f"longest allowed length in seconds (default: {GATE_MAX_S:g})")
    gt.add_argument("--check-drift", action="store_true",
                    help="measure drift with faster-whisper first (needs the subtitles extra); else .drift.json is read")
    gt.add_argument("--model", default=DEFAULT_WHISPER_MODEL, help=f"faster-whisper model for --check-drift (default: {DEFAULT_WHISPER_MODEL})")
    gt.add_argument("--device", default="cpu", help="cpu (default), cuda or auto")
    gt.add_argument("--report", help="where to write the JSON report (default: <name>.<lang>.gate.json)")
    gt.set_defaults(func=_cmd_gate)

    sh = sub.add_parser("shorts", help="cut 45-60 s vertical Shorts (9:16, large subtitles) from a rendered video, "
                        "at chapter marks and sentence ends; exit 1 when fewer than --count fit")
    sh.add_argument("video", help="a rendered clean <name>.<lang>.mp4 (its .timeline.json and .lines.json or .srt are read)")
    sh.add_argument("--scenes", help="the scenes.json it was rendered from, for chapter marks and titles "
                    "(default: scenes.json next to the video, if any)")
    sh.add_argument("--lang", help="language (default: from the file name)")
    sh.add_argument("--count", type=int, default=3, help="how many Shorts (default: 3)")
    sh.add_argument("--min-s", type=float, default=45.0, help="shortest Short in seconds (default: 45)")
    sh.add_argument("--max-s", type=float, default=60.0, help="longest Short in seconds (default: 60)")
    sh.add_argument("--theme", choices=sorted(THEMES), help="colours and fonts (default: the file's theme, else light)")
    sh.add_argument("--preset", default="medium", help="x264 preset (default: medium)")
    sh.add_argument("--crf", type=_crf, default=20, help="x264 quality, lower is better (default: 20)")
    sh.add_argument("--dry-run", action="store_true", help="print the windows only; cut nothing")
    sh.set_defaults(func=_cmd_shorts)

    c = sub.add_parser("clean", help="free disk space in the work dir: after an upload, or down to the size cap")
    mode = c.add_mutually_exclusive_group(required=True)
    mode.add_argument("--after-upload", metavar="UPLOAD_JSON",
                      help="<work dir>/<name>/upload.json: delete that job's videos, Shorts and intermediates "
                           "once the record says every video was uploaded")
    mode.add_argument("--cap", action="store_true", help="remove the oldest jobs' renders until the work dir fits its cap")
    c.add_argument("--cap-gb", type=float, help=f"size cap in GB for --cap (default: ${workdir.CAP_ENV}, else "
                   f"{workdir.DEFAULT_CAP_GB:g})")
    c.add_argument("--copy-to", metavar="REMOTE:PATH",
                   help=f"with --after-upload: copy the master mp4s there with rclone first (default: "
                        f"${workdir.CLOUD_COPY_ENV}; off when unset)")
    c.set_defaults(func=_cmd_clean)

    su = sub.add_parser("stage-upload", help="put a gated video (and its Shorts) on a draft release of lab-to-prod "
                        "for the youtube-upload workflow; private and scheduled, never public")
    su.add_argument("video", help="the clean <work dir>/<job>/<job>.<lang>.mp4 whose .gate.json passed")
    su.add_argument("--scenes", required=True, help="the scenes.json it was rendered from (title and chapters)")
    su.add_argument("--publish-at", required=True,
                    help="when YouTube makes it public, ISO 8601 with offset, e.g. 2026-12-01T19:00:00+07:00")
    su.add_argument("--description-file", help="text for the top of the description (chapters and the AI label follow)")
    su.add_argument("--tags", help="comma-separated tags")
    su.add_argument("--shorts", action="store_true", help="also stage the Shorts cut from this video")
    su.add_argument("--shorts-every-days", type=float, default=1.0,
                    help="Short n goes public n times this many days after the video (default: 1)")
    su.add_argument("--repo", help="owner/name of the release repository (default: $MOTION_STUDIO_RELEASE_REPO, "
                    "else nghiep-pham-ecosys/lab-to-prod)")
    su.add_argument("--allow-gate-limits", action="store_true", help="accept a gate run with changed limits (test uploads only)")
    su.add_argument("--dry-run", action="store_true", help="check and print the manifest; call nothing")
    su.set_defaults(func=_cmd_stage_upload)

    fu = sub.add_parser("finish-upload", help="after the youtube-upload workflow: write <job>/upload.json from its "
                        "result and clean the job once every video is uploaded")
    fu.add_argument("staged", help="the <job>.<lang>.staged.json written by stage-upload")
    fu.add_argument("--result", help="a result.json already downloaded (default: the workflow run's artifact, via gh)")
    fu.add_argument("--run", help="the workflow run id (default: the newest successful live run for the tag)")
    fu.add_argument("--no-clean", action="store_true", help="write the upload record only")
    fu.add_argument("--copy-to", metavar="REMOTE:PATH", help=f"copy the master mp4s with rclone before the cleanup "
                    f"(default: ${workdir.CLOUD_COPY_ENV}; off when unset)")
    fu.set_defaults(func=_cmd_finish_upload)

    d = sub.add_parser("disk", help="print the work dir size and the free space on the Windows drive of the WSL disk")
    d.set_defaults(func=_cmd_disk)

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
