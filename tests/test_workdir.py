"""Work dir, size cap, cleanup after upload, the optional rclone copy and `disk` (temp folders only)."""

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motion_studio import cli, workdir  # noqa: E402
from motion_studio.render import ffmpeg_available  # noqa: E402
from motion_studio.workdir import WorkDirError  # noqa: E402

SAMPLE = ROOT / "samples" / "three-scenes" / "scenes.json"
REQUIRE_FFMPEG = os.environ.get("MOTION_STUDIO_REQUIRE_FFMPEG") == "1"
MB = 10**6


class TempWorkDir(unittest.TestCase):
    """Every test runs against a temp work dir set through the environment, never the real cache."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "work"
        self.root.mkdir()
        env = mock.patch.dict(os.environ, {workdir.WORK_DIR_ENV: str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        for name in (workdir.CAP_ENV, workdir.CLOUD_COPY_ENV, workdir.RCLONE_ENV, workdir.HOST_DRIVE_ENV):
            os.environ.pop(name, None)

    def make_job(self, name: str, files: dict[str, int], age_s: float = 0) -> Path:
        """A job folder with files of the given sizes, all dated ``age_s`` seconds ago."""
        job = self.root / name
        job.mkdir()
        (job / workdir.JOB_MARKER).touch()
        for rel, size in files.items():
            path = job / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * size)
        when = 1_700_000_000 - age_s
        for dirpath, dirs, names in os.walk(job):
            for n in dirs + names:
                os.utime(os.path.join(dirpath, n), (when, when))
        os.utime(job, (when, when))
        return job

    def write_record(self, job: Path, files: list, **extra) -> Path:
        record = {"version": 1, "status": "uploaded", "job": job.name, "files": files, **extra}
        path = job / workdir.UPLOAD_RECORD
        path.write_text(json.dumps(record), encoding="utf-8")
        return path

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue() + err.getvalue()


class SettingsTest(TempWorkDir):
    def test_work_dir_comes_from_the_environment(self):
        self.assertEqual(workdir.work_dir(), self.root)

    def test_default_work_dir_is_under_the_studio_cache(self):
        os.environ.pop(workdir.WORK_DIR_ENV)
        self.assertEqual(workdir.work_dir(), (Path.home() / ".cache/nghiep-motion-studio/work").resolve())

    def test_too_broad_work_dirs_are_refused(self):
        for bad in ("/", str(Path.home()), str(Path.home().parent)):
            with self.subTest(bad=bad), self.assertRaisesRegex(WorkDirError, "too broad"):
                workdir.check_work_dir(Path(bad))

    def test_cap(self):
        self.assertEqual(workdir.cap_bytes(), 10 * 10**9)
        self.assertEqual(workdir.cap_bytes(0.5), 500 * MB)
        with mock.patch.dict(os.environ, {workdir.CAP_ENV: "2"}):
            self.assertEqual(workdir.cap_bytes(), 2 * 10**9)
        for bad in ("lots", "0", "-1"):
            with mock.patch.dict(os.environ, {workdir.CAP_ENV: bad}), self.assertRaises(WorkDirError):
                workdir.cap_bytes()

    def test_bad_job_names(self):
        for bad in ("", ".", "..", "a/b", ".hidden"):
            with self.subTest(bad=bad), self.assertRaises(WorkDirError):
                workdir.job_dir(bad, self.root)


class StripTest(TempWorkDir):
    def test_keeps_text_and_removes_the_rest(self):
        job = self.make_job("rag", {
            "rag.en.mp4": 100, "rag.en.burned.mp4": 50, "rag.en.srt": 5, "rag.en.timeline.json": 5,
            "visuals/diagram-1.mp4": 30, "shorts/one.en.mp4": 20, "shorts/one.en.srt": 3, "scenes.json": 2,
            "script.md": 2, "script.mp4": 7, ".render-abc/scene-0000.mp4": 10, "upload.json": 2,
        })
        freed, deleted = workdir.strip_job(job, self.root)
        self.assertEqual(freed, 100 + 50 + 5 + 30 + 20 + 7 + 10)
        self.assertIn("visuals/diagram-1.mp4", deleted)
        left = sorted(p.relative_to(job).as_posix() for p in job.rglob("*") if p.is_file())
        self.assertEqual(left, [workdir.JOB_MARKER, "rag.en.srt", "scenes.json", "script.md", "shorts/one.en.srt",
                                "upload.json"])
        self.assertFalse((job / "visuals").exists(), "empty folders are removed")

    def test_links_are_removed_not_followed(self):
        outside = self.base / "precious"
        outside.mkdir()
        (outside / "model.bin").write_bytes(b"m" * 10)
        job = self.make_job("rag", {"rag.en.mp4": 1})
        (job / "visuals").symlink_to(outside, target_is_directory=True)
        (job / "scenes.json").symlink_to(outside / "model.bin")  # a kept name, but a link: removed
        workdir.strip_job(job, self.root)
        self.assertTrue((outside / "model.bin").is_file())
        self.assertFalse((job / "visuals").is_symlink())
        self.assertFalse((job / "scenes.json").exists())

    def test_refuses_folders_that_are_not_jobs_of_the_work_dir(self):
        plain = self.root / "tts-venv"
        plain.mkdir()
        (plain / "big.bin").write_bytes(b"x")
        with self.assertRaises(WorkDirError):
            workdir.strip_job(plain, self.root)
        elsewhere = self.base / "other"
        elsewhere.mkdir()
        (elsewhere / workdir.JOB_MARKER).touch()
        with self.assertRaises(WorkDirError):
            workdir.strip_job(elsewhere, self.root)
        self.assertTrue((plain / "big.bin").exists())


class CapTest(TempWorkDir):
    def test_under_the_cap_nothing_happens(self):
        self.make_job("a", {"a.en.mp4": 10})
        result = workdir.enforce_cap(self.root, 100)
        self.assertEqual(result.pruned, [])

    def test_oldest_jobs_go_first_until_under_the_cap(self):
        old = self.make_job("old", {"old.en.mp4": 400, "old.en.srt": 1}, age_s=300)
        mid = self.make_job("mid", {"mid.en.mp4": 400}, age_s=200)
        new = self.make_job("new", {"new.en.mp4": 400}, age_s=100)
        result = workdir.enforce_cap(self.root, 900)
        self.assertEqual([name for name, _ in result.pruned], ["old"])
        self.assertFalse((old / "old.en.mp4").exists())
        self.assertTrue((old / "old.en.srt").exists())
        self.assertTrue((mid / "mid.en.mp4").exists())
        self.assertTrue((new / "new.en.mp4").exists())
        self.assertLessEqual(result.after, 900)

    def test_never_the_current_job_nor_one_being_rendered(self):
        current = self.make_job("current", {"c.en.mp4": 500}, age_s=900)
        busy = self.make_job("busy", {"b.en.mp4": 500}, age_s=800)
        idle = self.make_job("idle", {"i.en.mp4": 500}, age_s=100)
        with workdir.active_job(busy):  # another render holds it
            result = workdir.enforce_cap(self.root, 100, current=current)
        self.assertTrue((current / "c.en.mp4").exists())
        self.assertTrue((busy / "b.en.mp4").exists())
        self.assertFalse((idle / "i.en.mp4").exists())
        self.assertEqual(result.skipped_active, ["busy"])
        self.assertTrue(result.over_cap)

    def test_folders_without_the_marker_are_never_touched(self):
        models = self.root / "hf"
        models.mkdir()
        (models / "weights.bin").write_bytes(b"w" * 1000)
        result = workdir.enforce_cap(self.root, 10)
        self.assertTrue((models / "weights.bin").exists())
        self.assertTrue(result.over_cap)

    def test_active_job_creates_the_marker(self):
        with workdir.active_job(self.root / "fresh") as job:
            self.assertTrue(workdir.is_job(job))
            with workdir.exclusive(job) as locked:
                self.assertFalse(locked)
        with workdir.exclusive(self.root / "fresh") as locked:
            self.assertTrue(locked)


class CleanAfterUploadTest(TempWorkDir):
    FILES = {"rag.en.mp4": 100, "rag.en.burned.mp4": 100, "rag.vi.mp4": 100, "rag.en.srt": 5, "rag.vi.srt": 5,
             "rag.en.timeline.json": 3, "rag.en.gate.json": 3, "shorts/s1.en.mp4": 40, "visuals/code-1.mp4": 60,
             "scenes.json": 4, "script.md": 4}

    def uploaded(self, *paths):
        return [{"path": p, "remote_id": f"id-{i}"} for i, p in enumerate(paths)]

    def test_deletes_videos_shorts_and_intermediates_and_keeps_text(self):
        job = self.make_job("rag", self.FILES)
        record = self.write_record(job, self.uploaded("rag.en.mp4", "rag.vi.mp4", "shorts/s1.en.mp4"))
        result = workdir.clean_after_upload(record, self.root, run=self.fail_if_called)
        left = sorted(p.relative_to(job).as_posix() for p in job.rglob("*") if p.is_file())
        self.assertEqual(left, [workdir.JOB_MARKER, "rag.en.srt", "rag.vi.srt", "scenes.json", "script.md",
                                "upload.json"])
        self.assertEqual(result.freed, 100 * 3 + 40 + 60 + 6)
        data = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(data["cleaned"][0]["freed_bytes"], result.freed)
        self.assertEqual(data["cleaned"][0]["copied"], [])
        again = workdir.clean_after_upload(record, self.root)  # a second run is harmless
        self.assertEqual(again.deleted, [])

    def fail_if_called(self, *a, **k):
        raise AssertionError("rclone must not run when the cloud copy is off")

    def assert_refused(self, record, pattern):
        job = record.parent
        before = sorted(p.name for p in job.rglob("*"))
        with self.assertRaisesRegex(WorkDirError, pattern):
            workdir.clean_after_upload(record, self.root, run=self.fail_if_called)
        self.assertEqual(sorted(p.name for p in job.rglob("*")), before, "nothing deleted")

    def test_refuses_without_a_successful_upload(self):
        job = self.make_job("rag", self.FILES)
        all_files = self.uploaded("rag.en.mp4", "rag.vi.mp4", "shorts/s1.en.mp4")
        self.assert_refused(self.write_record(job, all_files, status="failed"), "status is 'failed'")
        self.assert_refused(self.write_record(job, []), "files must list")
        no_id = [dict(all_files[0], remote_id=""), *all_files[1:]]
        self.assert_refused(self.write_record(job, no_id), "no remote_id")
        record = self.write_record(job, all_files)
        data = json.loads(record.read_text())
        data["job"] = "other"
        record.write_text(json.dumps(data))
        self.assert_refused(record, "job is 'other'")
        (job / "upload.json").write_text("not json")
        self.assert_refused(job / "upload.json", "cannot read")

    def test_refuses_paths_outside_the_job(self):
        job = self.make_job("rag", self.FILES)
        files = self.uploaded("rag.en.mp4", "rag.vi.mp4", "shorts/s1.en.mp4", "../x.mp4")
        self.assert_refused(self.write_record(job, files), "relative path inside")
        files = self.uploaded("rag.en.mp4", "rag.vi.mp4", "shorts/s1.en.mp4", "/tmp/x.mp4")
        self.assert_refused(self.write_record(job, files), "relative path inside")

    def test_refuses_when_a_video_was_not_uploaded(self):
        job = self.make_job("rag", self.FILES)
        self.assert_refused(self.write_record(job, self.uploaded("rag.en.mp4", "shorts/s1.en.mp4")), "rag.vi.mp4")

    def test_refuses_when_a_named_file_is_missing(self):
        job = self.make_job("rag", self.FILES)
        files = self.uploaded("rag.en.mp4", "rag.vi.mp4", "shorts/s1.en.mp4", "rag.fr.mp4")
        self.assert_refused(self.write_record(job, files), "rag.fr.mp4")

    def test_refuses_while_the_job_is_being_rendered(self):
        job = self.make_job("rag", self.FILES)
        record = self.write_record(job, self.uploaded("rag.en.mp4", "rag.vi.mp4", "shorts/s1.en.mp4"))
        with workdir.active_job(job):
            self.assert_refused(record, "being rendered")

    def test_refuses_a_record_outside_the_work_dir(self):
        other = self.base / "elsewhere" / "rag"
        other.mkdir(parents=True)
        (other / workdir.JOB_MARKER).touch()
        (other / "rag.en.mp4").write_bytes(b"x")
        record = self.write_record(other, self.uploaded("rag.en.mp4"))
        self.assert_refused(record, "not in a job folder of the work dir")
        plain = self.root / "plain"
        plain.mkdir()
        (plain / "plain.en.mp4").write_bytes(b"x")
        self.assert_refused(self.write_record(plain, self.uploaded("plain.en.mp4")), "not in a job folder")

    def test_cli_clean(self):
        job = self.make_job("rag", self.FILES)
        record = self.write_record(job, self.uploaded("rag.en.mp4", "shorts/s1.en.mp4"))
        code, text = self.run_cli("clean", "--after-upload", str(record))
        self.assertEqual(code, 1)
        self.assertIn("rag.vi.mp4", text)
        record = self.write_record(job, self.uploaded("rag.en.mp4", "rag.vi.mp4", "shorts/s1.en.mp4"))
        code, text = self.run_cli("clean", "--after-upload", str(record))
        self.assertEqual(code, 0, text)
        self.assertIn("OK   rag: removed", text)


class CloudCopyTest(TempWorkDir):
    """The optional rclone copy, with a fake rclone that copies into a local folder."""

    def setUp(self):
        super().setUp()
        self.remote_root = self.base / "remote"
        self.fake = self.base / "bin" / "rclone"
        self.fake.parent.mkdir()
        self.fake.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import os, shutil, sys
            from pathlib import Path
            if os.environ.get("FAKE_RCLONE_FAIL"):
                sys.exit("quota exceeded")
            assert sys.argv[1:3] == ["copyto", "--checksum"], sys.argv
            src, dest = sys.argv[3], sys.argv[4]
            remote, _, rel = dest.partition(":")
            out = Path({str(self.remote_root)!r}) / remote / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out)
            """), encoding="utf-8")
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IEXEC)
        self.job = self.make_job("rag", {"rag.en.mp4": 100, "rag.en.burned.mp4": 100, "shorts/s.en.mp4": 5,
                                         "rag.en.srt": 2})
        self.record = self.write_record(self.job, [{"path": "rag.en.burned.mp4", "remote_id": "a"},
                                                   {"path": "shorts/s.en.mp4", "remote_id": "b"}])

    def test_copies_the_master_then_deletes(self):
        result = workdir.clean_after_upload(self.record, self.root, copy_to="gdrive:motion-studio",
                                            rclone=str(self.fake))
        self.assertEqual(result.copied, [{"path": "rag.en.mp4", "to": "gdrive:motion-studio/rag/rag.en.mp4"}])
        self.assertEqual((self.remote_root / "gdrive/motion-studio/rag/rag.en.mp4").stat().st_size, 100)
        self.assertFalse((self.job / "rag.en.mp4").exists())
        data = json.loads(self.record.read_text())
        self.assertEqual(data["cleaned"][0]["copied"], result.copied)

    def test_the_environment_turns_it_on(self):
        with mock.patch.dict(os.environ, {workdir.CLOUD_COPY_ENV: "gdrive:ms", workdir.RCLONE_ENV: str(self.fake)}):
            result = workdir.clean_after_upload(self.record, self.root)
        self.assertEqual(len(result.copied), 1)

    def test_a_failed_copy_deletes_nothing(self):
        with mock.patch.dict(os.environ, {"FAKE_RCLONE_FAIL": "1"}), self.assertRaisesRegex(WorkDirError, "quota"):
            workdir.clean_after_upload(self.record, self.root, copy_to="gdrive:ms", rclone=str(self.fake))
        self.assertTrue((self.job / "rag.en.mp4").exists())
        self.assertNotIn("cleaned", json.loads(self.record.read_text()))

    def test_missing_rclone_deletes_nothing(self):
        with self.assertRaisesRegex(WorkDirError, "rclone could not copy"):
            workdir.clean_after_upload(self.record, self.root, copy_to="gdrive:ms", rclone=str(self.base / "nope"))
        self.assertTrue((self.job / "rag.en.mp4").exists())

    def test_target_must_be_an_rclone_remote(self):
        for bad in ("/local/path", "no-colon", "-x:evil"):
            with self.subTest(bad=bad), self.assertRaisesRegex(WorkDirError, "rclone remote"):
                workdir.clean_after_upload(self.record, self.root, copy_to=bad, rclone=str(self.fake))
        self.assertTrue((self.job / "rag.en.mp4").exists())


LXSS = textwrap.dedent("""\

    HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Lxss
        DefaultVersion    REG_DWORD    0x2

    HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Lxss\\{1111}
        DistributionName    REG_SZ    Ubuntu
        BasePath    REG_SZ    D:\\WSL\\Ubuntu
        VhdFileName    REG_SZ    ext4.vhdx

    HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Lxss\\{2222}
        DistributionName    REG_SZ    other
        BasePath    REG_SZ    \\\\?\\C:\\Users\\example\\AppData\\Local\\other
    """)


class DiskTest(TempWorkDir):
    def fake_reg(self, text, code=0):
        return lambda *a, **k: subprocess.CompletedProcess(a, code, stdout=text, stderr="")

    def test_parse_registry(self):
        distros = workdir.parse_lxss(LXSS)
        self.assertEqual(distros["Ubuntu"]["BasePath"], "D:\\WSL\\Ubuntu")
        self.assertEqual(workdir.drive_mount(distros["Ubuntu"]["BasePath"]), "/mnt/d")
        self.assertEqual(workdir.drive_mount(distros["other"]["BasePath"]), "/mnt/c")
        self.assertIsNone(workdir.drive_mount("relative\\path"))

    def test_host_drive(self):
        with mock.patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}):
            self.assertEqual(workdir.host_drive(self.fake_reg(LXSS))[0], "/mnt/d")
            self.assertEqual(workdir.host_drive(self.fake_reg("", 1))[0], "/mnt/c")
            with mock.patch.dict(os.environ, {workdir.HOST_DRIVE_ENV: "/mnt/e"}):
                self.assertEqual(workdir.host_drive(self.fake_reg(LXSS)), ("/mnt/e", f"from {workdir.HOST_DRIVE_ENV}"))
        with mock.patch.dict(os.environ, {"WSL_DISTRO_NAME": ""}):
            self.assertEqual(workdir.host_drive(self.fake_reg(LXSS))[0], "/mnt/c")

        def broken(*a, **k):
            raise FileNotFoundError("reg.exe")

        with mock.patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}):
            self.assertEqual(workdir.host_drive(broken)[0], "/mnt/c")

    def test_report(self):
        self.make_job("rag", {"rag.en.mp4": 2 * MB})
        report = workdir.disk_report(self.root, 10 * MB, drive=(str(self.base), "test"))
        text = "\n".join(report.lines())
        self.assertIn("2.00 MB of a 10.00 MB cap (20 %)", text)
        self.assertIn("rag: 2.00 MB", text)
        self.assertIn(f"host drive {self.base} (test):", text)
        self.assertIn("free of", text)
        missing = workdir.disk_report(self.root, 10 * MB, drive=(str(self.base / "nope"), "test"))
        self.assertIn("not mounted", missing.lines()[-1])

    def test_cli_disk(self):
        with mock.patch.dict(os.environ, {workdir.HOST_DRIVE_ENV: str(self.base)}):
            code, text = self.run_cli("disk")
        self.assertEqual(code, 0, text)
        self.assertIn(f"work dir  {self.root}", text)
        self.assertIn(f"host drive {self.base}", text)

    def test_cli_prune_to_cap(self):
        old = self.make_job("old", {"old.en.mp4": 2 * MB}, age_s=50)
        code, text = self.run_cli("clean", "--cap", "--cap-gb", "0.001")
        self.assertEqual(code, 0, text)
        self.assertIn("removed the renders of old", text)
        self.assertFalse((old / "old.en.mp4").exists())


@unittest.skipUnless(ffmpeg_available() or REQUIRE_FFMPEG, "ffmpeg not installed")
class RenderIntoWorkDirTest(TempWorkDir):
    def test_render_without_out_dir_goes_to_the_work_dir_and_prunes_older_jobs(self):
        old = self.make_job("old", {"old.en.mp4": 3 * MB}, age_s=500)
        with mock.patch.dict(os.environ, {workdir.CAP_ENV: "0.002"}):
            code, text = self.run_cli("render", str(SAMPLE), "--lang", "en", "--preset", "ultrafast",
                                      "--subtitles", "none")
        self.assertEqual(code, 0, text)
        job = self.root / "three-scenes"
        self.assertTrue((job / "three-scenes.en.mp4").is_file())
        self.assertTrue((job / "three-scenes.en.timeline.json").is_file())
        self.assertTrue(workdir.is_job(job))
        self.assertFalse(any(p.name.startswith(".render-") for p in job.iterdir()), "the temp folder is removed")
        self.assertFalse((old / "old.en.mp4").exists())
        self.assertIn("removed the renders of old", text)


if __name__ == "__main__":
    unittest.main()
