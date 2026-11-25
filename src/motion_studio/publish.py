"""Hand a finished video to YouTube as a private, scheduled draft (issue 12).

The upload itself runs in GitHub Actions in lab-to-prod (workflow ``youtube-upload.yml``), where the YouTube
secrets live. This module does the two local halves (docs/spec.md, "Upload to YouTube"):

``stage`` (``motion-studio stage-upload``)
    Refuses unless the video's ``.gate.json`` passed, with the default limits and newer than the video. Builds the
    metadata (title, description with chapter timestamps from the timeline and the ``chapter`` fields, tags,
    language, publish time, the "made with AI" label), then puts the mp4, its SRT and ``manifest.json`` (plus the
    Shorts, if asked) on a DRAFT release of lab-to-prod. Draft releases and their assets are visible only to people
    with push access. Writes ``<job>/<job>.<lang>.staged.json`` with the release tag and the manifest.

``finish`` (``motion-studio finish-upload``)
    Reads ``result.json`` of the workflow run (its artifact ``youtube-upload-<tag>``, or a file), writes
    ``<job>/upload.json`` in the format of "Work dir and disk" (merged with an earlier record of the job), then runs
    ``clean --after-upload`` once every finished video of the job is uploaded.

Every ``gh`` call goes through :class:`Gh`, so tests use a fake. Nothing here calls YouTube, runs a workflow or
publishes a release.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from . import workdir
from .gate import Limits, sidecar
from .schema import load
from .shorts import chapter_starts

REPO_ENV = "MOTION_STUDIO_RELEASE_REPO"
GH_ENV = "MOTION_STUDIO_GH"
DEFAULT_REPO = "nghiep-pham-ecosys/lab-to-prod"
WORKFLOW = "youtube-upload.yml"
MANIFEST = "manifest.json"
RESULT = "result.json"
VERSION = 1
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{6,64}")
GATE_CHECKS = ("duration", "loudness", "black-frames", "subtitle-drift", "fact-check")
MAX_ASSET_BYTES = 2 * 2**30  # GitHub release assets: under 2 GiB each
MAX_TITLE = 100  # characters
MAX_DESCRIPTION = 5000  # bytes
MAX_TAGS = 500  # characters, counting quotes around tags with spaces and the commas
MIN_CHAPTERS = 3  # YouTube shows chapters only with 3 or more, the first at 0:00, each at least 10 s
MIN_CHAPTER_S = 10
AI_NOTE = ("Made with AI: the narration is a synthetic (text-to-speech) voice and the visuals are generated from "
           "code; the script was drafted with AI assistance and fact-checked by me.")


class PublishError(RuntimeError):
    """Staging or finishing was refused; the message says why. Nothing was half-done on the remote side."""


# --- gh -------------------------------------------------------------------------------------------------------------


@dataclass
class Gh:
    """Runs ``gh`` (``$MOTION_STUDIO_GH`` names another binary). ``run`` is swapped for a fake in tests."""

    binary: str | None = None
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        cmd = [self.binary or os.environ.get(GH_ENV) or "gh", *args]
        self.calls.append(cmd)
        try:
            proc = self.run(cmd, capture_output=True, text=True, check=False, timeout=6 * 3600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PublishError(f"gh {args[0]} {args[1] if len(args) > 1 else ''} failed: {exc}") from exc
        if check and proc.returncode != 0:
            tail = "\n".join((proc.stderr or "").strip().splitlines()[-6:])
            raise PublishError(f"gh {' '.join(args[:2])} failed (exit {proc.returncode}): {tail}")
        return proc


def repo_name(repo: str | None = None) -> str:
    name = repo or os.environ.get(REPO_ENV, "").strip() or DEFAULT_REPO
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
        raise PublishError(f"repository {name!r} must look like owner/name")
    return name


# --- checks ---------------------------------------------------------------------------------------------------------


def parse_time(text: str, what: str = "publish time") -> _dt.datetime:
    try:
        when = _dt.datetime.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise PublishError(f"{what} {text!r} is not an ISO 8601 time such as 2026-12-01T19:00:00+07:00") from exc
    if when.tzinfo is None:
        raise PublishError(f"{what} {text!r} needs a UTC offset, such as +07:00")
    return when


def check_gate(video: Path, allow_limits: bool = False) -> dict[str, Any]:
    """The video's ``.gate.json`` must exist, be newer than the video, name it, and pass every check (fail closed)."""
    report = sidecar(video, ".gate.json")
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublishError(f"no readable gate report {report.name}: run `motion-studio gate` first ({exc})") from exc
    problems = []
    if not isinstance(data, dict):
        raise PublishError(f"{report.name} is not a gate report")
    if Path(str(data.get("video", ""))).name != video.name:
        problems.append(f"it is the report of {data.get('video')!r}, not {video.name}")
    if report.stat().st_mtime < video.stat().st_mtime:
        problems.append("it is older than the video: run the gate again")
    checks = data.get("checks") if isinstance(data.get("checks"), list) else []
    passed = {c.get("name") for c in checks if isinstance(c, dict) and c.get("ok") is True}
    failed = [c.get("name") for c in checks if not (isinstance(c, dict) and c.get("ok") is True)]
    if data.get("ok") is not True or failed:
        problems.append(f"the gate failed ({', '.join(map(str, failed)) or 'ok is not true'})")
    missing = [name for name in GATE_CHECKS if name not in passed]
    if missing:
        problems.append(f"checks missing from the report: {', '.join(missing)}")
    if not allow_limits and data.get("limits") != asdict(Limits()):
        problems.append("the gate ran with changed limits; run it with the defaults "
                        "(--allow-gate-limits is for a test upload only)")
    if problems:
        raise PublishError(f"{video.name}: refused, nothing staged: {report.name}: " + "; ".join(problems))
    return data


def timestamp(seconds: float) -> str:
    """``75.4`` -> ``1:15``; ``3725`` -> ``1:02:05`` (YouTube chapter format)."""
    total = int(math.floor(seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def chapters(scenes_doc: dict[str, Any], timeline: dict[str, Any], lang: str, title: str) -> list[tuple[int, str]]:
    """Chapter starts in whole seconds, checked against YouTube's rules (raise PublishError when they fail)."""
    marks = [(int(math.floor(t)), name) for t, name in chapter_starts(scenes_doc, timeline, lang)]
    if not marks or marks[0][0] > 0:
        marks.insert(0, (0, title))  # YouTube needs the first chapter at 0:00
    duration = float(timeline["duration_s"])
    problems = []
    if len(marks) < MIN_CHAPTERS:
        problems.append(f"{len(marks)} chapter(s); YouTube shows chapters only from {MIN_CHAPTERS}")
    ends = [t for t, _ in marks[1:]] + [duration]
    for (start, name), end in zip(marks, ends):
        if end - start < MIN_CHAPTER_S:
            problems.append(f"chapter {name!r} at {timestamp(start)} is shorter than {MIN_CHAPTER_S} s")
    if problems:
        raise PublishError("chapters do not meet YouTube's rules: " + "; ".join(problems)
                           + " (set `chapter` on fewer, longer stretches of scenes in scenes.json)")
    return marks


def describe(text: str, marks: list[tuple[int, str]] | None, lang_title: str | None = None) -> str:
    parts = [text.strip()]
    if lang_title:
        parts.append(f"From the video \"{lang_title}\".")
    if marks:
        parts.append("Chapters\n" + "\n".join(f"{timestamp(t)} {name}" for t, name in marks))
    parts.append(AI_NOTE)
    return "\n\n".join(p for p in parts if p)


def check_text(title: str, description: str, tags: list[str], what: str) -> None:
    problems = []
    if not title.strip() or len(title) > MAX_TITLE or re.search(r"[<>]", title):
        problems.append(f"title must be 1-{MAX_TITLE} characters without < or > (got {len(title)})")
    if len(description.encode("utf-8")) > MAX_DESCRIPTION or re.search(r"[<>]", description):
        problems.append(f"description must be at most {MAX_DESCRIPTION} bytes without < or >")
    length = sum(len(t) + (2 if " " in t else 0) for t in tags) + max(len(tags) - 1, 0)
    if length > MAX_TAGS or any(not t.strip() or re.search(r"[<>,]", t) for t in tags):
        problems.append(f"tags must be words or phrases without < > or commas, {MAX_TAGS} characters in all")
    if problems:
        raise PublishError(f"{what}: " + "; ".join(problems))


def _asset(path: Path) -> Path:
    if not path.is_file():
        raise PublishError(f"{path} is missing")
    if not NAME_RE.fullmatch(path.name):
        raise PublishError(f"{path.name}: asset names must be letters, digits, '.', '_' or '-'")
    if path.stat().st_size >= MAX_ASSET_BYTES:
        raise PublishError(f"{path.name} is 2 GiB or more; a release asset must be smaller")
    return path


# --- stage ----------------------------------------------------------------------------------------------------------


@dataclass
class Staged:
    tag: str
    repo: str
    record: Path
    manifest: dict[str, Any]
    files: list[Path]


def video_parts(video: Path) -> tuple[str, str]:
    """``<job>/<job>.<lang>.mp4`` -> ``(job, lang)``; anything else is refused."""
    m = re.fullmatch(r"(.+)\.([a-z]{2})\.mp4", video.name)
    if not m or video.name.endswith(".burned.mp4"):
        raise PublishError(f"{video.name}: pass the clean <job>.<lang>.mp4 (captions go up from the .srt)")
    job, lang = m.groups()
    if video.resolve().parent.name != job or not workdir.is_job(video.resolve().parent):
        raise PublishError(f"{video} is not in its job folder <work dir>/{job}/ (render without -o)")
    if not NAME_RE.fullmatch(job):
        raise PublishError(f"job name {job!r} must be letters, digits, '.', '_' or '-'")
    return job, lang


def short_items(job: Path, stem: str, lang: str, title: str, text: str, tags: list[str], when: _dt.datetime,
                every_days: float) -> tuple[list[dict[str, Any]], list[Path]]:
    report = job / workdir.SHORTS_DIR / f"{stem}.{lang}.shorts.json"
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublishError(f"no readable Shorts report {report.name}: run `motion-studio shorts` first ({exc})") from exc
    clips = data.get("clips") or []
    if not clips or data.get("found") != data.get("wanted") or not all(c.get("loudness_ok") is True for c in clips):
        raise PublishError(f"{report.name}: the Shorts cut did not pass (fewer clips than wanted, or loudness off)")
    items, files = [], []
    for n, clip in enumerate(clips, 1):
        rel = str(clip.get("path", ""))
        mp4 = _asset(job / rel)
        srt = _asset(mp4.with_suffix(".srt"))
        if mp4.stat().st_mtime < (job / f"{stem}.{lang}.mp4").stat().st_mtime:
            raise PublishError(f"{rel} is older than the video: cut the Shorts again")
        head = (clip.get("chapter") or title).strip()
        short_title = f"{head[: MAX_TITLE - len(' #Shorts')]} #Shorts"
        desc = describe(text, None, title)
        check_text(short_title, desc, tags, rel)
        items.append({"key": mp4.name[: -len(".mp4")], "kind": "short", "path": rel, "video": mp4.name,
                      "captions": srt.name, "title": short_title, "description": desc, "tags": tags,
                      "language": lang, "publish_at": (when + _dt.timedelta(days=every_days * n)).isoformat(),
                      "synthetic_media": True})
        files += [mp4, srt]
    return items, files


def build(video: Path, scenes_file: Path, publish_at: str, text: str | None = None, tags: list[str] | None = None,
          shorts: bool = False, shorts_every_days: float = 1.0, allow_gate_limits: bool = False,
          now: _dt.datetime | None = None) -> tuple[str, dict[str, Any], list[Path]]:
    """Check everything and build ``(tag, manifest, files)``; touches nothing outside this machine."""
    video = Path(video)
    job_name, lang = video_parts(video)
    job = video.resolve().parent
    when = parse_time(publish_at)
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if when <= now:
        raise PublishError(f"publish time {publish_at} is in the past")
    check_gate(video, allow_gate_limits)
    scenes = load(scenes_file)
    if lang not in scenes.languages:
        raise PublishError(f"{scenes_file} has no {lang} narration")
    try:
        timeline = json.loads(sidecar(video, ".timeline.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublishError(f"cannot read the render's timeline: {exc}") from exc
    title = scenes.doc["title"][lang].strip()
    tags = [t.strip() for t in (tags or []) if t.strip()]
    if text is None:
        text = f"{title}. An explainer from the lab-to-prod content lab."
    try:
        marks = chapters(scenes.doc, timeline, lang, title)
    except KeyError as exc:
        raise PublishError(f"the timeline does not match scenes.json: {exc}") from exc
    desc = describe(text, marks)
    check_text(title, desc, tags, video.name)
    srt = _asset(sidecar(video, ".srt"))
    items = [{"key": f"{job_name}.{lang}", "kind": "video", "path": video.name, "video": video.name,
              "captions": srt.name, "title": title, "description": desc, "tags": tags, "language": lang,
              "publish_at": when.isoformat(), "synthetic_media": True}]
    files = [_asset(job / video.name), srt]
    if shorts:
        more, more_files = short_items(job, job_name, lang, title, text, tags, when, shorts_every_days)
        items += more
        files += more_files
    tag = f"yt-{job_name}-{lang}"
    return tag, {"version": VERSION, "tag": tag, "job": job_name, "items": items}, files


def _release(gh: Gh, tag: str, repo: str) -> dict[str, Any] | None:
    proc = gh(["release", "view", tag, "--repo", repo, "--json", "isDraft,assets"], check=False)
    if proc.returncode != 0:
        if "not found" in (proc.stderr or "").lower():
            return None
        raise PublishError(f"gh release view {tag} failed (exit {proc.returncode}): {(proc.stderr or '').strip()[-300:]}")
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise PublishError(f"gh release view {tag} gave no JSON") from exc


def stage(video: Path, scenes_file: Path, publish_at: str, gh: Gh | None = None, repo: str | None = None,
          text: str | None = None, tags: list[str] | None = None, shorts: bool = False, shorts_every_days: float = 1.0,
          allow_gate_limits: bool = False, now: _dt.datetime | None = None) -> Staged:
    """Build the manifest and put the batch on a draft release; write ``<job>.<lang>.staged.json`` on success."""
    gh = gh or Gh()
    repo = repo_name(repo)
    tag, manifest, files = build(video, scenes_file, publish_at, text, tags, shorts, shorts_every_days,
                                 allow_gate_limits, now)
    job = Path(video).resolve().parent
    with workdir.active_job(job):  # the cap or a cleanup must not delete the files while they go up
        release = _release(gh, tag, repo)
        if release is None:
            gh(["release", "create", tag, "--repo", repo, "--draft", "--title", f"youtube {tag} (draft, not public)",
                "--notes", "Staged by motion-studio for the youtube-upload workflow. A draft: not public. "
                           "Deleted after the upload."])
            release = _release(gh, tag, repo)
        if not release or release.get("isDraft") is not True:
            raise PublishError(f"release {tag} in {repo} is not a draft; refusing to put videos on it")
        names = {a.get("name") for a in release.get("assets") or [] if isinstance(a, dict)}
        if RESULT in names:
            raise PublishError(f"release {tag} holds {RESULT}: an upload of it has started. Run the workflow again "
                               "to finish it, or delete the draft to start over")
        with tempfile.TemporaryDirectory(dir=job, prefix=".stage-") as tmp:
            mf = Path(tmp) / MANIFEST
            mf.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            gh(["release", "upload", tag, *map(str, files), "--repo", repo, "--clobber"])
            gh(["release", "upload", tag, str(mf), "--repo", repo, "--clobber"])  # last: the batch is whole now
    stem = Path(video).name[: -len(".mp4")]
    record = job / f"{stem}.staged.json"
    data = {"version": VERSION, "status": "staged", "repo": repo, "tag": tag, "workflow": WORKFLOW,
            "staged_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), "manifest": manifest}
    _write_json(record, data)
    return Staged(tag, repo, record, manifest, files)


# --- finish ---------------------------------------------------------------------------------------------------------


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_staged(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublishError(f"cannot read {path}: {exc}") from exc
    manifest = data.get("manifest") if isinstance(data, dict) else None
    if not isinstance(manifest, dict) or data.get("version") != VERSION or not isinstance(data.get("tag"), str) \
            or manifest.get("tag") != data["tag"] or not isinstance(manifest.get("items"), list):
        raise PublishError(f"{path} is not a staging record of motion-studio")
    if manifest.get("job") != Path(path).resolve().parent.name:
        raise PublishError(f"{path} belongs to job {manifest.get('job')!r}, not the folder it is in")
    return data


def fetch_result(gh: Gh, repo: str, tag: str, run_id: str | None = None) -> dict[str, Any]:
    """``result.json`` from the artifact of the newest successful live run of the workflow for ``tag``."""
    if run_id is None:
        proc = gh(["run", "list", "--repo", repo, "--workflow", WORKFLOW, "--limit", "100",
                   "--json", "databaseId,displayTitle,status,conclusion"])
        try:
            runs = json.loads(proc.stdout)
        except ValueError as exc:
            raise PublishError("gh run list gave no JSON") from exc
        title = f"youtube upload live {tag}"
        done = [r for r in runs if r.get("displayTitle") == title and r.get("status") == "completed"]
        ok = [r for r in done if r.get("conclusion") == "success"]
        if not ok:
            state = f"the newest run ended {done[0].get('conclusion')}" if done else "no finished live run"
            raise PublishError(f"no successful upload of {tag} yet ({state}); run the workflow with live=true")
        run_id = str(ok[0]["databaseId"])
    with tempfile.TemporaryDirectory(prefix="motion-studio-result-") as tmp:
        gh(["run", "download", str(run_id), "--repo", repo, "--name", f"youtube-upload-{tag}", "--dir", tmp])
        return read_json(Path(tmp) / RESULT)


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublishError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PublishError(f"{path} is not a JSON object")
    return data


def uploaded_files(staged: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    """Check the workflow's result against the staged manifest; one upload-record entry per item."""
    manifest = staged["manifest"]
    if result.get("tag") != staged["tag"] or result.get("job") != manifest["job"]:
        raise PublishError(f"the result is for {result.get('tag')!r} of job {result.get('job')!r}, "
                           f"not {staged['tag']!r} of {manifest['job']!r}")
    if result.get("complete") is not True:
        raise PublishError(f"the upload of {staged['tag']} is not complete; run the workflow again")
    got = result.get("items") if isinstance(result.get("items"), dict) else {}
    out, problems = [], []
    for item in manifest["items"]:
        entry = got.get(item["key"])
        vid = entry.get("video_id") if isinstance(entry, dict) else None
        if not isinstance(vid, str) or not VIDEO_ID_RE.fullmatch(vid):
            problems.append(f"{item['key']} has no video id")
            continue
        if entry.get("path") != item["path"]:
            problems.append(f"{item['key']} came back for {entry.get('path')!r}, staged as {item['path']!r}")
            continue
        rel = PurePosixPath(item["path"])
        if rel.is_absolute() or ".." in rel.parts:
            problems.append(f"{item['key']}: bad path {item['path']!r}")
            continue
        out.append({"path": item["path"], "remote_id": vid, "platform": "youtube", "url": entry.get("url"),
                    "publish_at": entry.get("publish_at"), "captions": entry.get("captions"), "release": staged["tag"]})
    if problems:
        raise PublishError("the result does not match the staged batch: " + "; ".join(problems))
    return out


def write_record(job: Path, files: list[dict[str, Any]]) -> Path:
    """Write ``<job>/upload.json`` (version 1, status uploaded), keeping the entries of earlier uploads of the job."""
    record = job / workdir.UPLOAD_RECORD
    data: dict[str, Any] = {"version": workdir.UPLOAD_RECORD_VERSION, "status": "uploaded", "job": job.name, "files": []}
    if record.exists():
        old = read_json(record)
        if old.get("version") != workdir.UPLOAD_RECORD_VERSION or old.get("job") != job.name \
                or not isinstance(old.get("files"), list):
            raise PublishError(f"{record} exists but is not an upload record of {job.name}; nothing written")
        data = {**old, "status": "uploaded"}
    by_path = {e["path"]: e for e in data["files"] if isinstance(e, dict) and isinstance(e.get("path"), str)}
    for entry in files:
        by_path[entry["path"]] = entry
    data["files"] = sorted(by_path.values(), key=lambda e: e["path"])
    _write_json(record, data)
    return record


@dataclass
class Finished:
    record: Path
    files: list[dict[str, Any]]
    cleaned: workdir.CleanResult | None = None
    waiting: list[str] = field(default_factory=list)  # finished videos of the job not uploaded yet
    note: str | None = None


def finish(staged_path: Path, gh: Gh | None = None, result_file: Path | None = None, run_id: str | None = None,
           clean: bool = True, root: Path | None = None, copy_to: str | None = None) -> Finished:
    gh = gh or Gh()
    staged_path = Path(staged_path)
    staged = read_staged(staged_path)
    result = read_json(result_file) if result_file else fetch_result(gh, repo_name(staged.get("repo")), staged["tag"], run_id)
    files = uploaded_files(staged, result)
    job = staged_path.resolve().parent
    record = write_record(job, files)
    out = Finished(record, files)
    if not clean:
        out.note = "cleanup skipped (--no-clean)"
        return out
    root = Path(root) if root is not None else workdir.work_dir()
    if job.parent != root.resolve():
        out.note = f"{job} is not in the work dir {root}; nothing deleted"
        return out
    named = {e["path"] for e in read_json(record)["files"] if isinstance(e, dict)}
    out.waiting = [rel for rel in workdir.final_videos(job) if rel not in named and workdir._twin(rel) not in named]
    if out.waiting:
        out.note = "videos kept until they are uploaded too: " + ", ".join(out.waiting)
        return out
    try:
        out.cleaned = workdir.clean_after_upload(record, root, copy_to=copy_to)
    except workdir.WorkDirError as exc:
        raise PublishError(str(exc)) from exc
    return out
