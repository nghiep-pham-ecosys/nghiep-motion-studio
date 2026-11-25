"""Render a scenes.json to an mp4 with ffmpeg, one language at a time.

How it works:

1. Narration audio is decoded once to 48 kHz stereo WAV in a work folder and
   measured from its sample count (container headers can be estimates).
2. Plan: each scene gets a whole number of video frames. With narration the
   scene lasts ``max(duration_s, audio length + tail)``, rounded up to the next
   frame, so narration is never cut.
3. Each scene is encoded to a video-only segment (H.264, BT.709) and a PCM WAV
   whose sample count matches the frame count exactly. Sample counts come from
   the cumulative frame position, so audio and video stay in sync over any
   number of scenes. Every segment's frame count is checked after encoding.
4. The audio segments are joined into one mix and normalised with a two-pass
   ffmpeg ``loudnorm`` (pass 1 measures, pass 2 applies the measurement with
   ``linear=true``) to ``TARGET_LUFS`` integrated and ``TARGET_TRUE_PEAK_DBTP``
   true peak, keeping the mix's exact sample count. A silent mix is left as it
   is and reported (``RenderResult.loudness``).
5. The video segments are joined with the concat demuxer (video copied, not
   re-encoded) and the mix is encoded once to AAC.

Colours and fonts come from the theme (``theme.py``): the file's ``theme``
field, or ``RenderOptions.theme`` when set. A visual's own ``background`` or
``color`` wins over the theme.

Pictures are composed in RGB (so pad and text colours are exact and
transparent image areas show the scene background) and converted to BT.709
limited-range YUV once, at the end of each scene's filter chain.

Every ffmpeg call runs with the work folder as its current directory, so
filter arguments only ever name simple local files and need no escaping.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .schema import Scenes
from .theme import Theme, get_theme
from .visuals import GENERATED_KINDS, build_visuals, default_cache_dir

SAMPLE_RATE = 48000
ZOOM = 1.15  # how far the camera moves in on an image with motion
LANG_639_2 = {"en": "eng", "vi": "vie", "fr": "fra", "de": "deu", "es": "spa", "ja": "jpn", "zh": "zho"}
_FONT_FORBIDDEN = set(":'\\,;[]=")
# Loudness of the final mix (docs/spec.md, "Loudness"). The quality gate (gate.py) checks the same numbers.
TARGET_LUFS = -14.0  # integrated, EBU R128 / ITU-R BS.1770
MAX_TRUE_PEAK_DBTP = -1.0  # the highest true peak a finished video may have
TRUE_PEAK_HEADROOM_DB = 1.0  # loudnorm aims this far under the limit: the AAC encode raises peaks
TARGET_TRUE_PEAK_DBTP = MAX_TRUE_PEAK_DBTP - TRUE_PEAK_HEADROOM_DB
TARGET_LRA_LU = 11.0  # a mix with a wider loudness range gets loudnorm's dynamic mode instead of one gain
SILENCE_LUFS = -70.0  # the BS.1770 absolute gate: a mix this quiet has nothing to measure or normalise
_BT709_OUT = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


class RenderError(RuntimeError):
    """An ffmpeg or ffprobe call failed, or its output was not what the plan asked for."""


@dataclass
class RenderOptions:
    preset: str = "medium"
    crf: int = 20
    audio_bitrate: str = "192k"
    tail_s: float = 0.5
    font: str | None = None  # title-card font; None = the theme's
    theme: str | None = None  # overrides the scenes file's theme when set
    jobs: int = 0  # parallel scene encodes; 0 = automatic
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    keep_workdir: Path | None = None  # set to keep segments for debugging
    temp_dir: Path | None = None  # parent of the render's temporary folder (segments, mix); None = the system temp
    visuals_dir: Path | None = None  # cache for generated clips; default <scenes dir>/.cache/visuals
    rebuild_visuals: bool = False  # build generated clips even when cached
    normalize_audio: bool = True  # two-pass loudnorm on the final mix; False keeps the mix as it is (drafts)
    metadata: dict[str, str] = field(default_factory=dict)  # extra container tags, e.g. {"comment": "..."}

    def __post_init__(self) -> None:
        if self.font is not None and (not self.font or any(c in _FONT_FORBIDDEN for c in self.font)):
            raise ValueError(
                f"font name {self.font!r} must be non-empty and avoid the characters {''.join(sorted(_FONT_FORBIDDEN))}"
            )
        if not (isinstance(self.tail_s, (int, float)) and math.isfinite(self.tail_s) and self.tail_s >= 0):
            raise ValueError("tail_s must be a finite number >= 0")
        if self.theme is not None:
            get_theme(self.theme)  # raises ValueError for an unknown name
        if self.jobs < 0:
            raise ValueError("jobs must be >= 0 (0 = automatic)")
        if not 0 <= self.crf <= 51:
            raise ValueError("crf must be between 0 and 51")
        for key, value in self.metadata.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", key) or key == "title":
                raise ValueError(f"metadata key {key!r} must be a lower-case tag name other than title")
            if not isinstance(value, str) or "\n" in value or len(value) > 500:
                raise ValueError(f"metadata {key!r} must be one line of at most 500 characters")


@dataclass(frozen=True)
class SceneTiming:
    id: str
    index: int
    start_frame: int
    frames: int
    start_s: float
    duration_s: float
    samples: int


@dataclass
class SceneJob:
    """Everything needed to encode one scene's video segment."""

    cmd: list[str]
    files: dict[str, str] = field(default_factory=dict)  # helper text files to write first
    pre: list[list[str]] = field(default_factory=list)  # commands to run before cmd


@dataclass
class RenderResult:
    output: Path
    lang: str
    width: int
    height: int
    fps: int
    timeline: list[SceneTiming] = field(default_factory=list)
    loudness: dict[str, Any] = field(default_factory=dict)  # what happened to the mix (see normalize_mix)

    @property
    def duration_s(self) -> float:
        return sum(t.frames for t in self.timeline) / self.fps

    def timeline_json(self) -> str:
        data = {
            "output": str(self.output),
            "lang": self.lang,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "duration_s": round(self.duration_s, 6),
            "scenes": [asdict(t) for t in self.timeline],
        }
        if self.loudness:
            data["loudness"] = self.loudness
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise RenderError(f"{cmd[0]} not found; install ffmpeg (6.1 or newer)") from exc
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-20:])
        raise RenderError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{tail}")
    return proc


def probe_duration(path: Path, ffprobe: str = "ffprobe") -> float:
    """Return the container duration of a media file in seconds."""
    proc = _run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)])
    try:
        value = float(proc.stdout.strip())
    except ValueError as exc:
        raise RenderError(f"could not read the duration of {path}: {proc.stdout!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise RenderError(f"{path} has no usable duration ({value})")
    return value


def count_video_frames(path: Path, ffprobe: str = "ffprobe") -> int:
    """Count the video packets in a file (0 if it has no video stream)."""
    proc = _run([
        ffprobe, "-v", "error", "-count_packets", "-select_streams", "v:0",
        "-show_entries", "stream=nb_read_packets", "-of", "default=nw=1:nk=1", str(path),
    ])
    text = proc.stdout.strip()
    return int(text) if text.isdigit() else 0


def wav_length(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def _samples_at(frame: int, fps: int) -> int:
    return round(frame * SAMPLE_RATE / fps)


def plan(
    scenes: Scenes,
    lang: str,
    opts: RenderOptions,
    audio_length: Callable[[Path], float] | None = None,
    clip_length: Callable[[Path], float] | None = None,
) -> list[SceneTiming]:
    """Compute the frame and sample budget of every scene for one language.

    ``audio_length`` and ``clip_length`` return a file's length in seconds;
    both default to the container duration from ffprobe.
    """
    if lang not in scenes.languages:
        raise ValueError(f"language {lang!r} is not in this file's languages {scenes.languages}")
    default = lambda p: probe_duration(p, opts.ffprobe)  # noqa: E731
    audio_length = audio_length or default
    clip_length = clip_length or default
    fps = scenes.fps
    timeline: list[SceneTiming] = []
    frame = 0
    for i, scene in enumerate(scenes.scenes):
        visual = scene["visual"]
        start = float(visual.get("start_s", 0))
        if visual["kind"] == "video" and start > 0:
            length = clip_length(scenes.resolve(visual["src"]))
            if start >= length:
                raise ValueError(
                    f"scene {scene['id']!r}: start_s {start:g} is at or past the end of {visual['src']} ({length:.3f} s)"
                )
        seconds = float(scene.get("duration_s", 0.0))
        audio = scene.get("audio", {}).get(lang)
        if audio is not None:
            seconds = max(seconds, audio_length(scenes.resolve(audio)) + opts.tail_s)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(f"scene {scene['id']!r} has no usable duration_s and no {lang} audio")
        frames = max(1, math.ceil(seconds * fps - 1e-6))
        samples = _samples_at(frame + frames, fps) - _samples_at(frame, fps)
        timeline.append(
            SceneTiming(
                id=scene["id"],
                index=i,
                start_frame=frame,
                frames=frames,
                start_s=frame / fps,
                duration_s=frames / fps,
                samples=samples,
            )
        )
        frame += frames
    return timeline


def resolve_theme(scenes: Scenes, opts: RenderOptions) -> Theme:
    """The theme of a render: ``opts.theme`` when set, else the file's, else light."""
    return get_theme(opts.theme or scenes.theme)


def _hex(color: str) -> str:
    return "0x" + color.lstrip("#")


def _fit(w: int, h: int, fps: int, background: str) -> str:
    """Fit the input inside w x h (square pixels, aspect kept) over a solid background, in RGB."""
    bg = _hex(background)
    return (
        "scale=trunc(iw*sar/2)*2:ih:flags=lanczos,setsar=1,"
        f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,format=rgba,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={bg}@0[fg];"
        f"color=c={bg}:s={w}x{h}:r={fps},format=rgba[bg];"
        "[bg][fg]overlay=shortest=1:format=auto,setsar=1"
    )


def _zoompan(motion: str, frames: int, w: int, h: int, fps: int) -> str:
    """Camera move over a 2w x 2h input (half-pixel steps in the output: less jitter)."""
    last = max(frames - 1, 1)
    t = f"(on/{last})"
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "zoom-in":
        z, x, y = f"1+{ZOOM - 1:.4f}*{t}", cx, cy
    elif motion == "zoom-out":
        z, x, y = f"{ZOOM:.4f}-{ZOOM - 1:.4f}*{t}", cx, cy
    elif motion == "pan-left":
        z, x, y = f"{ZOOM:.4f}", f"(iw-iw/zoom)*(1-{t})", cy
    elif motion == "pan-right":
        z, x, y = f"{ZOOM:.4f}", f"(iw-iw/zoom)*{t}", cy
    else:
        raise ValueError(f"unknown motion {motion!r}")
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps}"


def _image_input(src: Path, height: int) -> list[str]:
    # librsvg rasterises at the SVG's own size unless told otherwise.
    return ["-height", str(height), "-keep_ar", "1"] if src.suffix.lower() == ".svg" else []


def scene_video_job(
    scenes: Scenes, scene: dict[str, Any], lang: str, timing: SceneTiming, out_name: str, opts: RenderOptions
) -> SceneJob:
    """Build the ffmpeg command(s) for one scene's video segment."""
    w, h, fps, n = scenes.width, scenes.height, scenes.fps, timing.frames
    visual = scene["visual"]
    kind = visual["kind"]
    theme = resolve_theme(scenes, opts)
    background = visual.get("background", theme.background)
    head = [opts.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    job = SceneJob(cmd=list(head))
    cmd = job.cmd
    if kind == "title":
        text_name = f"title-{timing.index:04d}.txt"
        job.files[text_name] = visual["text"][lang]
        size = max(12, h // 12)
        cmd += ["-f", "lavfi", "-i", f"color=c={_hex(background)}:s={w}x{h}:r={fps}"]
        vf = (
            f"format=rgb24,drawtext=textfile={text_name}:font={opts.font or theme.font}:fontsize={size}:"
            f"fontcolor={_hex(visual.get('color', theme.text))}:line_spacing={size // 3}:text_align=C:"
            f"x=(w-text_w)/2:y=(h-text_h)/2:expansion=none:fix_bounds=1"
        )
    elif kind == "image":
        src = scenes.resolve(visual["src"])
        motion = visual.get("motion", "none")
        if motion == "none":
            cmd += ["-loop", "1", "-framerate", str(fps), *_image_input(src, h), "-i", str(src)]
            vf = _fit(w, h, fps, background)
        else:
            cmd += [*_image_input(src, 2 * h), "-i", str(src)]
            vf = f"{_fit(2 * w, 2 * h, fps, background)},{_zoompan(motion, n, w, h, fps)}"
    elif kind == "video":
        src = scenes.resolve(visual["src"])
        start = float(visual.get("start_s", 0))
        if visual.get("loop", False):
            if start > 0:
                # -ss with -stream_loop re-seeks to a keyframe on every pass, so cut the tail once (lossless).
                cut = f"loop-{timing.index:04d}.mkv"
                job.pre.append(head + ["-ss", f"{start:.3f}", "-i", str(src), "-an", "-c:v", "ffv1", cut])
                cmd += ["-stream_loop", "-1", "-i", cut]
            else:
                cmd += ["-stream_loop", "-1", "-i", str(src)]
        else:
            if start > 0:
                cmd += ["-ss", f"{start:.3f}"]
            cmd += ["-i", str(src)]
        hold = f"{n / fps + 1:.3f}"
        vf = f"setpts=PTS-STARTPTS,fps={fps},{_fit(w, h, fps, background)},tpad=stop_mode=clone:stop_duration={hold}"
    else:  # the schema makes this unreachable
        raise ValueError(f"unknown visual kind {kind!r}")
    cmd += [
        "-vf", f"{vf},scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-frames:v", str(n),
        "-r", str(fps),
        "-an",
        "-c:v", "libx264",
        "-preset", opts.preset,
        "-crf", str(opts.crf),
        "-pix_fmt", "yuv420p",
        *_BT709_OUT,
        "-video_track_timescale", str(fps * 1000),
        out_name,
    ]
    return job


def scene_audio_cmd(
    scene: dict[str, Any], lang: str, timing: SceneTiming, out_name: str, opts: RenderOptions, source: Path | None = None
) -> list[str]:
    """Return the ffmpeg command that writes one scene's narration as a WAV of exactly ``timing.samples``.

    ``source`` is the narration file (silence when None).
    """
    cmd = [opts.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if source is None:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo"]
    else:
        cmd += ["-i", str(source)]
    s = timing.samples
    af = (
        f"aresample={SAMPLE_RATE},aformat=sample_fmts=s16:sample_rates={SAMPLE_RATE}:channel_layouts=stereo,"
        f"apad=whole_len={s},atrim=end_sample={s}"
    )
    return cmd + ["-vn", "-af", af, "-c:a", "pcm_s16le", out_name]


def decode_audio_cmd(src: Path, out_name: str, opts: RenderOptions) -> list[str]:
    return [
        opts.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-vn",
        "-af", f"aresample={SAMPLE_RATE},aformat=sample_fmts=s16:sample_rates={SAMPLE_RATE}:channel_layouts=stereo",
        "-c:a", "pcm_s16le", out_name,
    ]


def _concat_list(names: list[str]) -> str:
    return "ffconcat version 1.0\n" + "".join(f"file '{n}'\n" for n in names)


def mux_cmd(scenes: Scenes, lang: str, out_path: Path, opts: RenderOptions, audio: str = "mix.wav") -> list[str]:
    cmd = [
        opts.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", "video.ffconcat",
        "-i", audio,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0",
        "-c:a", "aac", "-b:a", opts.audio_bitrate, "-ar", str(SAMPLE_RATE), "-ac", "2",
        "-metadata", f"title={scenes.doc['title'][lang]}",
    ]
    for key, value in sorted(opts.metadata.items()):
        cmd += ["-metadata", f"{key}={value}"]
    if lang in LANG_639_2:
        cmd += ["-metadata:s:a:0", f"language={LANG_639_2[lang]}"]
    return cmd + ["-movflags", "+faststart", "-f", "mp4", str(out_path)]


# --- loudness ------------------------------------------------------------------------------------------------------------

_LOUDNORM_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}")
_LOUDNORM_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "output_i", "output_tp", "target_offset")


def loudnorm_report(stderr: str) -> dict[str, Any]:
    """Parse the JSON block that ``loudnorm=print_format=json`` writes to stderr (numbers as floats, -inf kept)."""
    match = _LOUDNORM_JSON.search(stderr)
    if not match:
        raise RenderError("ffmpeg loudnorm printed no report")
    raw = json.loads(match.group(0))
    out: dict[str, Any] = {"normalization_type": raw.get("normalization_type", "")}
    for key in _LOUDNORM_KEYS:
        try:
            out[key] = float(str(raw[key]).strip())
        except (KeyError, ValueError) as exc:
            raise RenderError(f"unreadable loudnorm report ({key}): {raw.get(key)!r}") from exc
    return out


def _loudnorm_args() -> str:
    return f"I={TARGET_LUFS:g}:TP={TARGET_TRUE_PEAK_DBTP:g}:LRA={TARGET_LRA_LU:g}"


def _pcm_exact(samples: int) -> str:
    """Back to 48 kHz s16 stereo with exactly ``samples`` samples (loudnorm works at 192 kHz)."""
    return (
        f"aresample={SAMPLE_RATE},aformat=sample_fmts=s16:sample_rates={SAMPLE_RATE}:channel_layouts=stereo,"
        f"apad=whole_len={samples},atrim=end_sample={samples}"
    )


def measure_mix_cmd(concat: str, opts: RenderOptions) -> list[str]:
    """Pass 1: measure the joined mix (the report is printed at info level, so no ``-loglevel error``)."""
    return [
        opts.ffmpeg, "-hide_banner", "-nostdin", "-nostats", "-y", "-f", "concat", "-safe", "0", "-i", concat, "-vn",
        "-af", f"loudnorm={_loudnorm_args()}:print_format=json", "-f", "null", "-",
    ]


def normalize_mix_cmd(concat: str, measured: dict[str, Any], samples: int, out_name: str, opts: RenderOptions) -> list[str]:
    """Pass 2: apply the pass-1 measurement with ``linear=true`` and write the mix as a WAV of ``samples``."""
    m = measured
    af = (
        f"loudnorm={_loudnorm_args()}:measured_I={m['input_i']:.2f}:measured_TP={m['input_tp']:.2f}:"
        f"measured_LRA={m['input_lra']:.2f}:measured_thresh={m['input_thresh']:.2f}:offset={m['target_offset']:.2f}:"
        f"linear=true:print_format=json,{_pcm_exact(samples)}"
    )
    return [
        opts.ffmpeg, "-hide_banner", "-nostdin", "-nostats", "-y", "-f", "concat", "-safe", "0", "-i", concat, "-vn",
        "-af", af, "-c:a", "pcm_s16le", out_name,
    ]


def join_mix_cmd(concat: str, out_name: str, opts: RenderOptions) -> list[str]:
    """Join the scene WAVs unchanged (normalisation off, or a silent mix)."""
    return [
        opts.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", concat, "-vn", "-c:a", "pcm_s16le", out_name,
    ]


def _rounded(report: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Numbers rounded for the timeline file; -inf (silence) as text so the file stays valid JSON."""
    return {k: (round(report[k], 2) if math.isfinite(report[k]) else str(report[k])) for k in keys}


def normalize_mix(workdir: Path, concat: str, samples: int, out_name: str, opts: RenderOptions) -> dict[str, Any]:
    """Join the scene WAVs listed in ``concat`` into ``out_name``, normalised to the loudness targets.

    Returns what happened, for ``RenderResult.loudness``: ``status`` is ``normalised``, ``silent`` (nothing
    above the -70 LUFS gate: the mix is joined unchanged) or ``off`` (``opts.normalize_audio`` is False).
    """
    targets = {"target_lufs": TARGET_LUFS, "target_true_peak_dbtp": TARGET_TRUE_PEAK_DBTP, "target_lra_lu": TARGET_LRA_LU}
    if not opts.normalize_audio:
        _run(join_mix_cmd(concat, out_name, opts), cwd=workdir)
        return {"status": "off"}
    measured = loudnorm_report(_run(measure_mix_cmd(concat, opts), cwd=workdir).stderr)
    if not math.isfinite(measured["input_i"]) or measured["input_i"] <= SILENCE_LUFS:
        _run(join_mix_cmd(concat, out_name, opts), cwd=workdir)
        return {"status": "silent", **targets, **_rounded(measured, "input_i", "input_tp")}
    applied = loudnorm_report(_run(normalize_mix_cmd(concat, measured, samples, out_name, opts), cwd=workdir).stderr)
    return {
        "status": "normalised",
        "mode": applied["normalization_type"],  # "linear" (one gain) or "dynamic" (loudnorm's compressor/limiter)
        **targets,
        **_rounded(measured, "input_i", "input_tp", "input_lra"),
        **_rounded(applied, "output_i", "output_tp"),
    }


def _pool_run(jobs: int, fn: Callable[[Any], Any], items: list[Any]) -> list[Any]:
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(fn, items))


def render(scenes: Scenes, lang: str, output: str | Path, opts: RenderOptions | None = None) -> RenderResult:
    """Render one language of ``scenes`` to ``output`` (mp4). Returns the timeline."""
    opts = opts or RenderOptions()
    if lang not in scenes.languages:
        raise ValueError(f"language {lang!r} is not in this file's languages {scenes.languages}")
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    jobs = opts.jobs or max(1, min(4, (os.cpu_count() or 2) // 4))

    if opts.keep_workdir is not None:
        workdir = Path(opts.keep_workdir).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        cleanup = None
    else:
        temp_parent = Path(opts.temp_dir).resolve() if opts.temp_dir is not None else None
        if temp_parent is not None:
            temp_parent.mkdir(parents=True, exist_ok=True)
        cleanup = tempfile.TemporaryDirectory(prefix=".render-", dir=temp_parent)
        workdir = Path(cleanup.name)
    try:
        # 1. Decode each narration file once and measure it from its samples.
        decoded: dict[Path, Path] = {}
        for scene in scenes.scenes:
            rel = scene.get("audio", {}).get(lang)
            if rel is not None:
                src = scenes.resolve(rel)
                decoded.setdefault(src, workdir / f"narration-{len(decoded):04d}.wav")
        _pool_run(jobs, lambda kv: _run(decode_audio_cmd(kv[0], kv[1].name, opts), cwd=workdir), list(decoded.items()))
        lengths = {src: wav_length(dst) for src, dst in decoded.items()}

        # 2. Plan, then build generated visuals (Manim, diagram, code, VHS) and re-plan.
        timeline = plan(scenes, lang, opts, audio_length=lambda p: lengths[p])
        scenes, timeline = with_generated_visuals(scenes, lang, timeline, opts, lambda p: lengths[p])

        # 3. Encode every scene (video segment + exact-length WAV) and check frame counts.
        def encode_video(item: tuple[SceneTiming, SceneJob, str]) -> None:
            timing, job, name = item
            for pre in job.pre:
                _run(pre, cwd=workdir)
            _run(job.cmd, cwd=workdir)
            got = count_video_frames(workdir / name, opts.ffprobe)
            if got != timing.frames:
                raise RenderError(f"scene {timing.id!r}: segment has {got} frame(s), expected {timing.frames}")

        video_items, audio_cmds, video_names, audio_names = [], [], [], []
        for timing in timeline:
            scene = scenes.scenes[timing.index]
            vname, aname = f"scene-{timing.index:04d}.mp4", f"scene-{timing.index:04d}.wav"
            job = scene_video_job(scenes, scene, lang, timing, vname, opts)
            for name, content in job.files.items():
                (workdir / name).write_text(content, encoding="utf-8")
            rel = scene.get("audio", {}).get(lang)
            source = decoded[scenes.resolve(rel)] if rel is not None else None
            video_items.append((timing, job, vname))
            audio_cmds.append(scene_audio_cmd(scene, lang, timing, aname, opts, source))
            video_names.append(vname)
            audio_names.append(aname)
        _pool_run(jobs, encode_video, video_items)
        _pool_run(jobs, lambda c: _run(c, cwd=workdir), audio_cmds)

        # 4. Join the audio into one mix and normalise its loudness.
        (workdir / "audio.ffconcat").write_text(_concat_list(audio_names), encoding="utf-8")
        loudness = normalize_mix(workdir, "audio.ffconcat", sum(t.samples for t in timeline), "mix.wav", opts)

        # 5. Join the video and mux.
        (workdir / "video.ffconcat").write_text(_concat_list(video_names), encoding="utf-8")
        partial = output.with_name(output.stem + ".partial.mp4")
        try:
            _run(mux_cmd(scenes, lang, partial, opts), cwd=workdir)
            os.replace(partial, output)
        finally:
            partial.unlink(missing_ok=True)
    finally:
        if cleanup is not None:
            cleanup.cleanup()
    return RenderResult(
        output=output, lang=lang, width=scenes.width, height=scenes.height, fps=scenes.fps, timeline=timeline,
        loudness=loudness,
    )


def with_generated_visuals(
    scenes: Scenes,
    lang: str,
    timeline: list[SceneTiming],
    opts: RenderOptions,
    audio_length: Callable[[Path], float] | None = None,
) -> tuple[Scenes, list[SceneTiming]]:
    """Build the generated visuals of ``scenes`` and return scenes that use them as clips, re-planned.

    Paced kinds fit their scene; a clip with its own length (manim, terminal)
    stretches its scene so it is never cut. Scenes without generated visuals
    come back unchanged.
    """
    if not any(s["visual"]["kind"] in GENERATED_KINDS for s in scenes.scenes):
        return scenes, timeline
    theme = resolve_theme(scenes, opts)
    built, _ = build_visuals(
        scenes,
        lang,
        {t.id: t.duration_s for t in timeline},
        Path(opts.visuals_dir) if opts.visuals_dir is not None else default_cache_dir(scenes),
        default_background=theme.background,
        theme=theme,
        clip_length=lambda p: probe_duration(p, opts.ffprobe),
        jobs=opts.jobs or 1,  # one Manim/VHS build at a time unless --jobs asks for more (each uses ~0.5-1 GB)
        rebuild=opts.rebuild_visuals,
    )
    return built, plan(built, lang, opts, audio_length=audio_length)


def ffmpeg_available(opts: RenderOptions | None = None) -> bool:
    opts = opts or RenderOptions()
    return shutil.which(opts.ffmpeg) is not None and shutil.which(opts.ffprobe) is not None
