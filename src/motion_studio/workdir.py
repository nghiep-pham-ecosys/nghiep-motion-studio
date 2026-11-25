"""Keep renders off the local disk: one work dir, a size cap, and cleanup after upload.

Layout (docs/spec.md, "Work dir and disk"): every video is a *job*, a folder
``<work dir>/<job>/`` named like its outputs (``output_stem``). It holds the
renders (``<job>.<lang>.mp4`` and sidecars), the generated-visuals cache
(``visuals/``), Shorts (``shorts/``), the render's temporary folders, and the
upload record ``upload.json`` written by the upload step.

Safety rules:

- Only a folder with the marker file ``.motion-studio-job`` is a job. Nothing
  else in the work dir (or anywhere else) is ever deleted, so a work dir set
  to the wrong folder by mistake loses nothing.
- Deleting never follows a symbolic link: a link is removed, never its target.
- A job being rendered holds a shared ``flock`` on its marker; cleanup takes an
  exclusive lock without waiting and skips (or refuses) a job it cannot lock.
- The small text files in ``KEEP_NAMES`` / ``KEEP_SUFFIXES`` are never deleted.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

WORK_DIR_ENV = "MOTION_STUDIO_WORK_DIR"
CAP_ENV = "MOTION_STUDIO_WORK_CAP_GB"
HOST_DRIVE_ENV = "MOTION_STUDIO_HOST_DRIVE"
CLOUD_COPY_ENV = "MOTION_STUDIO_CLOUD_COPY"
RCLONE_ENV = "MOTION_STUDIO_RCLONE"
DEFAULT_CAP_GB = 10.0
GB = 10**9

JOB_MARKER = ".motion-studio-job"
UPLOAD_RECORD = "upload.json"
UPLOAD_RECORD_VERSION = 1
KEEP_NAMES = frozenset({JOB_MARKER, UPLOAD_RECORD, "scenes.json", "factcheck.md"})
KEEP_SUFFIXES = (".srt",)
KEEP_SCRIPTS = frozenset({"script.md", "script.txt", "script.json"})  # the script, at the top of the job only
SHORTS_DIR = "shorts"


class WorkDirError(RuntimeError):
    """A cleanup was refused or a work dir setting is unsafe."""


# --- settings ---------------------------------------------------------------------------------------------------------


def default_work_dir() -> Path:
    return Path.home() / ".cache" / "nghiep-motion-studio" / "work"


def work_dir() -> Path:
    """The work dir: ``$MOTION_STUDIO_WORK_DIR``, else ``~/.cache/nghiep-motion-studio/work``. Checked for safety."""
    value = os.environ.get(WORK_DIR_ENV, "").strip()
    path = Path(value).expanduser() if value else default_work_dir()
    return check_work_dir(path)


def check_work_dir(path: Path) -> Path:
    """Refuse a work dir that is ``/``, the home folder or one of its parents (cleanup must stay in one subtree)."""
    path = Path(path).expanduser().resolve()
    home = Path.home().resolve()
    if path == Path(path.anchor) or path == home or path in home.parents:
        raise WorkDirError(f"work dir {path} is too broad; set {WORK_DIR_ENV} to a folder of its own")
    return path


def cap_bytes(value: float | None = None) -> int:
    """The size cap in bytes: ``value`` GB, else ``$MOTION_STUDIO_WORK_CAP_GB``, else 10 GB (1 GB = 10^9 bytes)."""
    if value is None:
        text = os.environ.get(CAP_ENV, "").strip()
        try:
            value = float(text) if text else DEFAULT_CAP_GB
        except ValueError as exc:
            raise WorkDirError(f"{CAP_ENV}={text!r} is not a number of GB") from exc
    if not value > 0:
        raise WorkDirError(f"the work dir cap must be more than 0 GB (got {value})")
    return int(value * GB)


def job_dir(job: str, root: Path | None = None) -> Path:
    if not job or job in (".", "..") or "/" in job or job.startswith("."):
        raise WorkDirError(f"bad job name {job!r}")
    return (root or work_dir()) / job


def is_inside(path: Path, root: Path) -> bool:
    """True when ``path`` (its parent resolved, its own name taken as is) lies under ``root``."""
    path, root = Path(path), Path(root).resolve()
    real = path.parent.resolve() / path.name
    return real != root and root in real.parents


# --- sizes ------------------------------------------------------------------------------------------------------------


def tree_size(path: Path) -> int:
    """Bytes of the regular files under ``path``, links not followed."""
    total = 0
    for dirpath, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except FileNotFoundError:
                continue
            if not os.path.islink(os.path.join(dirpath, name)):
                total += st.st_size
    return total


def last_used(path: Path) -> float:
    """The newest modification time in a job (the folder, its files and folders)."""
    newest = os.lstat(path).st_mtime
    for dirpath, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            with contextlib.suppress(FileNotFoundError):
                newest = max(newest, os.lstat(os.path.join(dirpath, name)).st_mtime)
    return newest


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1000
    return f"{n:.2f} GB"


# --- jobs and locks ---------------------------------------------------------------------------------------------------


def is_job(path: Path) -> bool:
    path = Path(path)
    return path.is_dir() and not path.is_symlink() and (path / JOB_MARKER).is_file()


def jobs(root: Path) -> list[Path]:
    """Job folders directly in ``root``, oldest first."""
    if not root.is_dir():
        return []
    found = [p for p in root.iterdir() if is_job(p)]
    return sorted(found, key=last_used)


def _open_marker(job: Path) -> int:
    return os.open(job / JOB_MARKER, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)


@contextlib.contextmanager
def active_job(path: Path) -> Iterator[Path]:
    """Create the job folder if needed and hold a shared lock on it while rendering (several renders may share it)."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    fd = _open_marker(path)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        os.utime(path / JOB_MARKER)  # marks the job as just used
        yield path
    finally:
        os.close(fd)  # closing releases the lock


@contextlib.contextmanager
def exclusive(job: Path) -> Iterator[bool]:
    """Try to lock a job for cleanup without waiting. Yields False when a render holds it."""
    fd = _open_marker(job)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


# --- stripping a job down to its text files ---------------------------------------------------------------------------


def kept(rel: PurePosixPath) -> bool:
    """True for the files a cleanup keeps: the record, scenes.json, the script, subtitles and the job marker."""
    name = rel.name
    return (name in KEEP_NAMES or name.endswith(KEEP_SUFFIXES) or (len(rel.parts) == 1 and name in KEEP_SCRIPTS))


def strip_job(job: Path, root: Path) -> tuple[int, list[str]]:
    """Delete every file of ``job`` except the kept text files; remove folders left empty. Links are unlinked.

    Returns (bytes freed, relative paths deleted). Refuses a folder that is not a job inside ``root``.
    """
    job = Path(job)
    if not is_job(job) or not is_inside(job, root) or job.parent.resolve() != Path(root).resolve():
        raise WorkDirError(f"{job} is not a job folder of the work dir {root}; nothing deleted")
    freed, deleted = 0, []
    for dirpath, dirs, files in os.walk(job, topdown=False, followlinks=False):
        here = Path(dirpath)
        for name in files + [d for d in dirs if (here / d).is_symlink()]:
            path = here / name
            rel = PurePosixPath(path.relative_to(job).as_posix())
            if kept(rel) and not path.is_symlink():
                continue
            if not is_inside(path, job):  # cannot happen without links; checked anyway
                raise WorkDirError(f"{path} is outside {job}; stopped")
            link = path.is_symlink()
            size = 0 if link else os.lstat(path).st_size
            path.unlink()
            freed += size
            deleted.append(str(rel))
        if here != job:
            with contextlib.suppress(OSError):
                here.rmdir()  # only succeeds when empty
    return freed, sorted(deleted)


# --- the size cap -----------------------------------------------------------------------------------------------------


@dataclass
class PruneResult:
    before: int
    after: int
    cap: int
    pruned: list[tuple[str, int]] = field(default_factory=list)  # (job, bytes freed)
    skipped_active: list[str] = field(default_factory=list)

    @property
    def over_cap(self) -> bool:
        return self.after > self.cap


def enforce_cap(root: Path, cap: int, current: Path | None = None) -> PruneResult:
    """Strip the oldest jobs until ``root`` holds at most ``cap`` bytes.

    ``current`` (the job being rendered by this process) and any job another
    process holds are never touched. Kept text files stay, so a pruned job can
    be rendered again from its scenes.json.
    """
    root = Path(root)
    total = tree_size(root) if root.is_dir() else 0
    result = PruneResult(before=total, after=total, cap=cap)
    if total <= cap:
        return result
    current_real = Path(current).resolve() if current else None
    for job in jobs(root):
        if result.after <= cap:
            break
        if current_real is not None and job.resolve() == current_real:
            continue
        with exclusive(job) as locked:
            if not locked:
                result.skipped_active.append(job.name)
                continue
            freed, deleted = strip_job(job, root)
        if deleted:
            result.pruned.append((job.name, freed))
            result.after -= freed
    return result


# --- cleanup after upload ---------------------------------------------------------------------------------------------


def final_videos(job: Path) -> list[str]:
    """Relative paths of the finished videos in a job: top-level ``*.mp4`` and every ``*.mp4`` under ``shorts/``."""
    out = []
    for path in sorted(job.glob("*.mp4")):
        if not path.name.endswith(".partial.mp4"):
            out.append(path.name)
    shorts = job / SHORTS_DIR
    if shorts.is_dir() and not shorts.is_symlink():
        for path in sorted(shorts.rglob("*.mp4")):
            if not path.name.endswith(".partial.mp4"):
                out.append(path.relative_to(job).as_posix())
    return out


def _twin(rel: str) -> str:
    """``x.en.mp4`` <-> ``x.en.burned.mp4``."""
    if rel.endswith(".burned.mp4"):
        return rel[: -len(".burned.mp4")] + ".mp4"
    return rel[: -len(".mp4")] + ".burned.mp4"


def read_record(record: Path, job: Path) -> dict[str, Any]:
    """Load an upload record and check it says the upload succeeded and names its files. Raises WorkDirError."""
    try:
        data = json.loads(Path(record).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkDirError(f"cannot read the upload record {record}: {exc}") from exc
    problems = []
    if not isinstance(data, dict):
        raise WorkDirError(f"{record}: the upload record must be a JSON object")
    if data.get("version") != UPLOAD_RECORD_VERSION:
        problems.append(f"version must be {UPLOAD_RECORD_VERSION}")
    if data.get("status") != "uploaded":
        problems.append(f"status is {data.get('status')!r}, not 'uploaded'")
    if data.get("job") != job.name:
        problems.append(f"job is {data.get('job')!r}, but the record is in the job folder {job.name!r}")
    files = data.get("files")
    if not isinstance(files, list) or not files:
        problems.append("files must list every uploaded file")
        files = []
    for i, entry in enumerate(files):
        if not isinstance(entry, dict):
            problems.append(f"files[{i}] must be an object with path and remote_id")
            continue
        rel = entry.get("path")
        if not isinstance(rel, str) or not rel.endswith(".mp4"):
            problems.append(f"files[{i}].path must name an .mp4 in the job folder")
            continue
        parts = PurePosixPath(rel).parts
        if PurePosixPath(rel).is_absolute() or ".." in parts or "\\" in rel or not parts:
            problems.append(f"files[{i}].path {rel!r} must be a relative path inside the job folder")
        if not isinstance(entry.get("remote_id"), str) or not entry["remote_id"].strip():
            problems.append(f"files[{i}] ({rel}) has no remote_id: the upload is not confirmed")
    if problems:
        raise WorkDirError(f"{record}: refused, nothing deleted:\n" + "\n".join(f"  - {p}" for p in problems))
    return data


@dataclass
class CleanResult:
    job: str
    freed: int
    deleted: list[str]
    copied: list[dict[str, str]]


def cloud_copy(job: Path, masters: list[str], remote: str, rclone: str,
               run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> list[dict[str, str]]:
    """Copy each master mp4 to ``<remote>/<job>/<name>`` with ``rclone copyto``; raise WorkDirError on any failure.

    rclone compares sizes and, where the remote supports it, hashes after each transfer, so exit 0 means the copy
    is whole.
    """
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*:[^\n]*", remote):
        raise WorkDirError(f"cloud copy target {remote!r} must be an rclone remote such as 'gdrive:motion-studio'")
    copied = []
    for rel in masters:
        dest = f"{remote.rstrip('/')}/{job.name}/{rel}"
        try:
            proc = run([rclone, "copyto", "--checksum", str(job / rel), dest], capture_output=True, text=True,
                       check=False, timeout=6 * 3600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkDirError(f"rclone could not copy {rel}: {exc}; nothing deleted") from exc
        if proc.returncode != 0:
            tail = "\n".join((proc.stderr or "").strip().splitlines()[-10:])
            raise WorkDirError(f"rclone copyto {rel} -> {dest} failed (exit {proc.returncode}); nothing deleted\n{tail}")
        copied.append({"path": rel, "to": dest})
    return copied


def clean_after_upload(record: Path, root: Path | None = None, copy_to: str | None = None, rclone: str | None = None,
                       run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> CleanResult:
    """Delete a job's videos, Shorts and intermediates once its upload record says every video was uploaded.

    Refuses (and deletes nothing) unless: the record sits in a job folder of the work dir; it says
    ``"status": "uploaded"``, names its job and lists each uploaded file with a ``remote_id``; every named file
    exists (on a first clean); every finished video in the job is named (or is the burned or clean twin of a
    named one); and no render holds the job. With ``copy_to`` (an rclone remote), the master mp4s are copied
    first and any copy failure stops the cleanup. The kept text files stay, and the record gets a ``cleaned``
    entry.
    """
    root = Path(root) if root is not None else work_dir()
    record = Path(record)
    if record.is_symlink() or record.name != UPLOAD_RECORD:
        raise WorkDirError(f"{record}: the upload record must be a regular file named {UPLOAD_RECORD}")
    job = record.parent.resolve()
    if job.parent != Path(root).resolve() or not is_job(job):
        raise WorkDirError(f"{record} is not in a job folder of the work dir {root}; nothing deleted")
    with exclusive(job) as locked:
        if not locked:
            raise WorkDirError(f"{job.name} is being rendered right now; nothing deleted")
        return _clean_locked(record, job, root, copy_to, rclone, run)


def _clean_locked(record: Path, job: Path, root: Path, copy_to: str | None, rclone: str | None,
                  run: Callable[..., subprocess.CompletedProcess]) -> CleanResult:
    data = read_record(record, job)
    named = {entry["path"] for entry in data["files"]}
    first = "cleaned" not in data
    missing = sorted(rel for rel in named if not (job / rel).is_file()) if first else []
    if missing:
        raise WorkDirError(f"{record}: refused, nothing deleted: the record names files that are not in the job: "
                           + ", ".join(missing))
    unlisted = [rel for rel in final_videos(job) if rel not in named and _twin(rel) not in named]
    if unlisted:
        raise WorkDirError(f"{record}: refused, nothing deleted: these videos are not in the upload record: "
                           + ", ".join(unlisted))
    copied: list[dict[str, str]] = []
    remote = copy_to if copy_to is not None else os.environ.get(CLOUD_COPY_ENV, "").strip()
    if remote:
        masters = [rel for rel in final_videos(job) if "/" not in rel and not rel.endswith(".burned.mp4")]
        copied = cloud_copy(job, masters, remote, rclone or os.environ.get(RCLONE_ENV) or "rclone", run)
    freed, deleted = strip_job(job, root)
    data.setdefault("cleaned", [])
    if not isinstance(data["cleaned"], list):
        data["cleaned"] = [data["cleaned"]]
    data["cleaned"].append({
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "freed_bytes": freed, "deleted": deleted, "copied": copied,
    })
    tmp = record.with_name(f".{record.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, record)
    return CleanResult(job=job.name, freed=freed, deleted=deleted, copied=copied)


# --- free space on the Windows drive that holds the WSL disk ----------------------------------------------------------

LXSS_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss"


def parse_lxss(text: str) -> dict[str, dict[str, str]]:
    """``reg.exe query <Lxss> /s`` output -> {DistributionName: {value name: data}}."""
    distros: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("HKEY_"):
            current = {}
            continue
        m = re.match(r"^\s+(\S+)\s+REG_\w+\s+(.*)$", line)
        if m and current is not None:
            current[m.group(1)] = m.group(2).strip()
            if m.group(1) == "DistributionName":
                distros[m.group(2).strip()] = current
    return distros


def drive_mount(base_path: str, mnt: str = "/mnt") -> str | None:
    """``C:\\Users\\...`` or ``\\\\?\\C:\\...`` -> ``/mnt/c``."""
    m = re.match(r"^(?:\\\\\?\\)?([A-Za-z]):\\", base_path)
    return f"{mnt}/{m.group(1).lower()}" if m else None


def host_drive(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> tuple[str, str]:
    """(mount path, how it was found) for the Windows drive that holds this WSL distro's disk.

    ``$MOTION_STUDIO_HOST_DRIVE`` wins; else the distro's ``BasePath`` from the Windows registry (``reg.exe``,
    read-only); else ``/mnt/c``.
    """
    value = os.environ.get(HOST_DRIVE_ENV, "").strip()
    if value:
        return value, f"from {HOST_DRIVE_ENV}"
    distro = os.environ.get("WSL_DISTRO_NAME", "")
    if distro:
        try:
            proc = run(["reg.exe", "query", LXSS_KEY, "/s"], capture_output=True, text=True, check=False, timeout=10)
            base = parse_lxss(proc.stdout).get(distro, {}).get("BasePath", "")
            mount = drive_mount(base)
            if proc.returncode == 0 and mount:
                return mount, f"holds the WSL disk of {distro}"
        except (OSError, subprocess.TimeoutExpired):
            pass
    return "/mnt/c", "default; WSL disk location not found"


@dataclass
class DiskReport:
    root: Path
    size: int
    cap: int
    job_sizes: list[tuple[str, int, float]]  # (job, bytes, last used)
    drive: str
    drive_note: str
    drive_free: int | None
    drive_total: int | None

    def lines(self) -> list[str]:
        pct = 100 * self.size / self.cap if self.cap else 0
        out = [f"work dir  {self.root}: {human(self.size)} of a {human(self.cap)} cap ({pct:.0f} %)"]
        for name, size, used in self.job_sizes:
            out.append(f"  {name}: {human(size)} (last used {time.strftime('%Y-%m-%d %H:%M', time.localtime(used))})")
        if self.drive_free is None:
            out.append(f"host drive {self.drive} ({self.drive_note}): not mounted")
        else:
            out.append(f"host drive {self.drive} ({self.drive_note}): {human(self.drive_free)} free "
                       f"of {human(self.drive_total or 0)}")
        return out


def disk_report(root: Path, cap: int, drive: tuple[str, str] | None = None) -> DiskReport:
    root = Path(root)
    mount, note = drive or host_drive()
    try:
        usage = shutil.disk_usage(mount) if Path(mount).is_dir() else None
    except OSError:
        usage = None
    job_sizes = [(j.name, tree_size(j), last_used(j)) for j in jobs(root)]
    return DiskReport(
        root=root, size=tree_size(root) if root.is_dir() else 0, cap=cap, job_sizes=job_sizes, drive=mount,
        drive_note=note, drive_free=usage.free if usage else None, drive_total=usage.total if usage else None,
    )
