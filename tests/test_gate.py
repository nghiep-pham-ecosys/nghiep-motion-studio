"""Tests for the quality gate: one passing and one failing input per rule, plus the CLI exit codes.

Media is synthetic: ffmpeg lavfi colour and sine sources, written to a temp folder and removed after each test.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motion_studio import cli, gate  # noqa: E402
from motion_studio.gate import (  # noqa: E402
    FACTCHECK_NAME,
    HASH_CHARS,
    Limits,
    check_black,
    check_drift,
    check_duration,
    check_factcheck,
    check_loudness,
    narration_hash,
    run_gate,
)
from motion_studio.render import ffmpeg_available  # noqa: E402
from motion_studio.subtitles import DriftReport  # noqa: E402

REQUIRE_FFMPEG = os.environ.get("MOTION_STUDIO_REQUIRE_FFMPEG") == "1"
SHORT = Limits(min_duration_s=1.0, max_duration_s=10.0)  # the test clips are seconds long, not minutes
LIGHT_BG = "0xf8fafc"  # light theme frame
DARK_BG = "0x0f172a"  # dark theme frame: must not count as black
LOUD_DB = 7.75  # gain on lavfi's 1/8-amplitude sine that gives about -14 LUFS in stereo (measured: -14.1)
QUIET_DB = -8.0  # about -30 LUFS


def make_clip(path: Path, seconds: float = 3.0, *, bg: str = LIGHT_BG, gain_db: float | None = LOUD_DB,
              audio_s: float | None = None, black: tuple[float, float] | None = None) -> Path:
    """A small 320x180 30 fps H.264/AAC clip: a solid frame, a 440 Hz tone, optionally a black run."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"color=c={bg}:s=320x180:r=30:d={seconds}"]
    if gain_db is not None:
        cmd += ["-f", "lavfi", "-i",
                f"sine=f=440:r=48000:d={audio_s or seconds},volume={gain_db}dB,aformat=channel_layouts=stereo"]
    if black:
        cmd += ["-vf", f"drawbox=c=black:t=fill:enable='between(t,{black[0]},{black[1]})'"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if gain_db is not None:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    subprocess.run(cmd + [str(path)], check=True)
    return path


def write_timeline(video: Path, seconds: float) -> None:
    video.with_suffix(".timeline.json").write_text(json.dumps({"duration_s": seconds}), encoding="utf-8")


def drift_report(median_s: float = 0.02, mean_abs_s: float = 0.05) -> DriftReport:
    return DriftReport(lang="en", model="base", cues=4, matched=4, median_s=median_s, mean_abs_s=mean_abs_s,
                       p90_abs_s=0.1, max_abs_s=0.12, trend_s=0.01)


def write_subtitles(video: Path, report: DriftReport | None = None) -> None:
    video.with_suffix(".srt").write_text("1\n00:00:00,100 --> 00:00:01,500\nAcme makes rockets.\n", encoding="utf-8")
    video.with_suffix(".drift.json").write_text((report or drift_report()).json(), encoding="utf-8")


SCENES = {
    "version": 1,
    "title": {"en": "Acme demo"},
    "languages": ["en"],
    "output": {"width": 320, "height": 180, "fps": 30},
    "scenes": [{"id": "a", "narration": {"en": "Acme makes rockets."}, "duration_s": 3,
                "visual": {"kind": "title", "text": {"en": "Acme"}}}],
}


def write_scenes(folder: Path, doc: dict | None = None) -> Path:
    path = folder / "scenes.json"
    path.write_text(json.dumps(doc or SCENES), encoding="utf-8")
    return path


def factcheck_text(doc: dict | None = None, *, claims: int = 3, unchecked: int = 0, reviewer: str = "reviewer-1",
                   source: bool = True, narration: str | None = None) -> str:
    want = narration if narration is not None else narration_hash(doc or SCENES)[:HASH_CHARS]
    rows = [f"- [x] Claim {i} about Acme" + (f" - source: https://example.com/{i}" if source else "")
            for i in range(claims)]
    rows += [f"- [ ] Claim {i} still open" for i in range(unchecked)]
    return "\n".join([
        "# Fact check: Acme demo", "", f"Reviewed-by: {reviewer}", "Date: 2026-11-25", f"Narration: {want}", "",
        *rows, "",
    ])


# --- rules that need no media ----------------------------------------------------------------------------------------


class FactCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gate-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.scenes = write_scenes(self.tmp)

    def check(self, text: str | None):
        if text is not None:
            (self.tmp / FACTCHECK_NAME).write_text(text, encoding="utf-8")
        return check_factcheck(self.scenes, Limits())

    def test_complete_file_passes(self):
        result = self.check(factcheck_text())
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(result.details["checked"], 3)

    def test_missing_file_fails(self):
        result = self.check(None)
        self.assertFalse(result.ok)
        self.assertIn("no ", result.summary)

    def test_too_few_unchecked_or_unsourced_claims_fail(self):
        for text, why in [
            (factcheck_text(claims=2), "at least 3"),
            (factcheck_text(unchecked=1), "unchecked"),
            (factcheck_text(source=False), "without a source"),
            (factcheck_text(reviewer=""), "Reviewed-by"),
        ]:
            with self.subTest(why=why):
                result = self.check(text)
                self.assertFalse(result.ok)
                self.assertIn(why, result.summary)

    def test_changed_narration_makes_the_file_stale(self):
        self.check(factcheck_text())
        doc = json.loads(json.dumps(SCENES))
        doc["scenes"][0]["narration"]["en"] = "Acme makes boats."
        write_scenes(self.tmp, doc)
        result = check_factcheck(self.scenes, Limits())
        self.assertFalse(result.ok)
        self.assertIn("does not match the current narration", result.summary)

    def test_hash_ignores_audio_paths_written_by_narrate(self):
        doc = json.loads(json.dumps(SCENES))
        doc["scenes"][0]["audio"] = {"en": "audio/tts/a.en.wav"}
        self.assertEqual(narration_hash(doc), narration_hash(SCENES))


class LoudnessLimitsTest(unittest.TestCase):
    """The loudness rule on given measurements (the media tests below measure real clips)."""

    def judge(self, i: float, tp: float):
        with mock.patch.object(gate, "measure_loudness", return_value={"integrated_lufs": i, "true_peak_dbtp": tp,
                                                                       "lra_lu": 3.0}):
            return check_loudness(Path("x.en.mp4"), Limits())

    def test_target_and_tolerance(self):
        self.assertTrue(self.judge(-14.0, -2.0).ok)
        self.assertTrue(self.judge(-13.0, -2.0).ok)
        self.assertTrue(self.judge(-15.0, -2.0).ok)
        self.assertFalse(self.judge(-12.9, -2.0).ok)
        self.assertFalse(self.judge(-16.5, -2.0).ok)

    def test_true_peak_limit(self):
        result = self.judge(-14.0, -0.5)
        self.assertFalse(result.ok)
        self.assertIn("true peak", result.summary)

    def test_silence_fails(self):
        result = self.judge(float("-inf"), float("-inf"))
        self.assertFalse(result.ok)
        self.assertIn("no sound", result.summary)
        json.dumps(result.details)  # -inf is stored as text, so the report stays valid JSON


class DriftFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gate-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.video = self.tmp / "demo.en.mp4"
        self.video.write_bytes(b"not a real video")  # only its mtime is read here

    def test_good_report_passes(self):
        write_subtitles(self.video)
        self.assertTrue(check_drift(self.video).ok)

    def test_drifting_report_fails(self):
        write_subtitles(self.video, drift_report(median_s=0.45, mean_abs_s=0.45))
        self.assertFalse(check_drift(self.video).ok)

    def test_report_cannot_loosen_its_own_limits(self):
        data = json.loads(drift_report(median_s=0.45, mean_abs_s=0.45).json())
        data.update(limit_s=5.0, p90_limit_s=5.0, ok=True)
        write_subtitles(self.video)
        self.video.with_suffix(".drift.json").write_text(json.dumps(data), encoding="utf-8")
        self.assertFalse(check_drift(self.video).ok)

    def test_missing_srt_or_report_fails(self):
        self.assertIn(".srt", check_drift(self.video).summary)
        self.video.with_suffix(".srt").write_text("", encoding="utf-8")
        self.assertIn(".drift.json", check_drift(self.video).summary)

    def test_report_older_than_the_video_fails(self):
        write_subtitles(self.video)
        old = self.video.stat().st_mtime - 60
        os.utime(self.video.with_suffix(".drift.json"), (old, old))
        result = check_drift(self.video)
        self.assertFalse(result.ok)
        self.assertIn("older", result.summary)


# --- rules on synthetic media ----------------------------------------------------------------------------------------


@unittest.skipUnless(ffmpeg_available() or REQUIRE_FFMPEG, "ffmpeg/ffprobe not on PATH")
class MediaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gate-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def clip(self, name: str = "demo.en.mp4", seconds: float = 3.0, **kw) -> Path:
        video = make_clip(self.tmp / name, seconds, **kw)
        write_timeline(video, seconds)
        return video

    def test_duration_pass_and_fail(self):
        video = self.clip()
        self.assertTrue(check_duration(video, SHORT).ok, check_duration(video, SHORT).summary)
        too_short = check_duration(video, Limits())  # the default window is 10-20 minutes
        self.assertFalse(too_short.ok)
        self.assertIn("outside 600-1200 s", too_short.summary)

    def test_duration_fails_on_short_audio_missing_audio_or_wrong_timeline(self):
        short_audio = self.clip("a.en.mp4", audio_s=2.0)
        self.assertIn("differ", check_duration(short_audio, SHORT).summary)
        silent = self.clip("b.en.mp4", gain_db=None)
        self.assertIn("no audio stream", check_duration(silent, SHORT).summary)
        wrong = self.clip("c.en.mp4")
        write_timeline(wrong, 4.0)
        self.assertIn("does not match the planned", check_duration(wrong, SHORT).summary)
        wrong.with_suffix(".timeline.json").unlink()
        self.assertIn("no c.en.timeline.json", check_duration(wrong, SHORT).summary)

    def test_loudness_pass_and_fail(self):
        loud = check_loudness(self.clip("a.en.mp4"), SHORT)
        self.assertTrue(loud.ok, loud.summary)
        self.assertAlmostEqual(loud.details["integrated_lufs"], -14.0, delta=0.5)
        quiet = check_loudness(self.clip("b.en.mp4", gain_db=QUIET_DB), SHORT)
        self.assertFalse(quiet.ok)
        self.assertIn("LUFS", quiet.summary)

    def test_black_frames_pass_and_fail(self):
        self.assertTrue(check_black(self.clip("light.en.mp4"), SHORT).ok)
        self.assertTrue(check_black(self.clip("dark.en.mp4", bg=DARK_BG), SHORT).ok)  # dark theme is not black
        broken = check_black(self.clip("broken.en.mp4", black=(1.0, 1.5)), SHORT)
        self.assertFalse(broken.ok)
        self.assertEqual(len(broken.details["runs"]), 1)
        self.assertAlmostEqual(broken.details["runs"][0]["start_s"], 1.0, delta=0.05)

    def good_video(self) -> tuple[Path, Path]:
        video = self.clip()
        write_subtitles(video)
        scenes = write_scenes(self.tmp)
        (self.tmp / FACTCHECK_NAME).write_text(factcheck_text(), encoding="utf-8")
        return video, scenes

    def test_gate_passes_a_good_clip(self):
        video, scenes = self.good_video()
        report = run_gate(video, scenes, "en", SHORT)
        self.assertTrue(report.ok, report.summary())
        self.assertEqual([c.name for c in report.checks],
                         ["duration", "loudness", "black-frames", "subtitle-drift", "fact-check"])
        self.assertTrue(json.loads(report.json())["ok"])

    def run_cli(self, *argv: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["gate", *argv])
        return code, out.getvalue() + err.getvalue()

    def test_cli_passes_then_refuses_a_failing_sample(self):
        video, scenes = self.good_video()
        code, text = self.run_cli(str(video), "--scenes", str(scenes), "--min-s", "1", "--max-s", "10")
        self.assertEqual(code, 0, text)
        self.assertIn("GATE PASS", text)
        self.assertTrue(json.loads(video.with_suffix(".gate.json").read_text(encoding="utf-8"))["ok"])

        broken = self.clip("broken.en.mp4", gain_db=QUIET_DB, black=(1.0, 1.5))
        write_subtitles(broken)
        code, text = self.run_cli(str(broken), "--scenes", str(scenes), "--min-s", "1", "--max-s", "10")
        self.assertEqual(code, 1, text)
        self.assertIn("GATE FAIL (loudness, black-frames)", text)
        report = json.loads(broken.with_suffix(".gate.json").read_text(encoding="utf-8"))
        self.assertFalse(report["ok"])
        self.assertEqual({c["name"]: c["ok"] for c in report["checks"]},
                         {"duration": True, "loudness": False, "black-frames": False, "subtitle-drift": True,
                          "fact-check": True})

    def test_cli_check_drift_measures_first(self):
        from motion_studio.subtitles import Word

        video, scenes = self.good_video()
        video.with_suffix(".drift.json").unlink()
        late = [Word(0.6, 0.8, "Acme"), Word(0.8, 1.0, "makes"), Word(1.0, 1.3, "rockets.")]  # cue starts at 0.1 s
        with mock.patch("motion_studio.subtitles.whisper_recogniser", return_value=lambda _v, _l: late):
            code, text = self.run_cli(str(video), "--scenes", str(scenes), "--min-s", "1", "--max-s", "10",
                                      "--check-drift")
        self.assertEqual(code, 1, text)
        self.assertIn("FAIL subtitle-drift", text)
        self.assertEqual(json.loads(video.with_suffix(".drift.json").read_text(encoding="utf-8"))["median_s"], 0.5)

    def test_cli_default_window_refuses_a_short_clip(self):
        video, scenes = self.good_video()
        code, text = self.run_cli(str(video), "--scenes", str(scenes))
        self.assertEqual(code, 1)
        self.assertIn("FAIL duration", text)

    def test_cli_usage_errors(self):
        video, scenes = self.good_video()
        self.assertEqual(self.run_cli(str(self.tmp / "missing.en.mp4"), "--scenes", str(scenes))[0], 1)
        odd = self.tmp / "noname.mp4"
        shutil.copyfile(video, odd)
        self.assertEqual(self.run_cli(str(odd), "--scenes", str(scenes))[0], 2)
        self.assertEqual(self.run_cli(str(video), "--scenes", str(scenes), "--min-s", "20", "--max-s", "10")[0], 2)


if __name__ == "__main__":
    unittest.main()
