"""Tests for Shorts: marks, window choice, layout, and cutting real clips from synthetic long videos.

Media is synthetic: an ffmpeg lavfi test pattern with a tone that is on during
"lines" and off between them, plus hand-written timeline, lines and scenes
files, all in a temp folder removed after each test.
"""

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motion_studio import cli, workdir  # noqa: E402
from motion_studio.gate import LUFS_TOLERANCE  # noqa: E402
from motion_studio.render import MAX_TRUE_PEAK_DBTP, TARGET_LUFS, count_video_frames, ffmpeg_available  # noqa: E402
from motion_studio.shorts import (  # noqa: E402
    Mark,
    ShortOptions,
    ShortsError,
    ass_text,
    ass_time,
    candidates,
    chapter_starts,
    clip_lines,
    layout,
    make_shorts,
    pick,
    place,
    short_name,
    silence_around,
)
from motion_studio.subtitles import Activity, parse_srt  # noqa: E402

SCENE_S = 20  # every synthetic scene: five 3.3 s lines, 0.7 s apart, then a pause before the next scene
FPS = 30


def has_libass() -> bool:
    if not ffmpeg_available():
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return " ass " in out


def line_spans(scenes: int) -> list[tuple[int, float, float]]:
    return [(k, SCENE_S * k + 4 * m + 0.2, SCENE_S * k + 4 * m + 3.5) for k in range(scenes) for m in range(5)]


def timeline_doc(scenes: int) -> dict:
    return {
        "lang": "en", "width": 160, "height": 90, "fps": FPS, "duration_s": float(SCENE_S * scenes),
        "scenes": [{"id": f"s{k}", "index": k, "start_frame": SCENE_S * FPS * k, "frames": SCENE_S * FPS,
                    "start_s": float(SCENE_S * k), "duration_s": float(SCENE_S)} for k in range(scenes)],
    }


def lines_doc(scenes: int) -> list[dict]:
    return [{"scene": f"s{k}", "text": f"Line {i % 5} of part {k} explains one more step.", "start_s": a, "end_s": b}
            for i, (k, a, b) in enumerate(line_spans(scenes))]


def scenes_doc(scenes: int) -> dict:
    """A chapter every two scenes."""
    out = []
    for k in range(scenes):
        scene = {"id": f"s{k}", "narration": {"en": "A line."}, "duration_s": SCENE_S,
                 "visual": {"kind": "title", "text": {"en": "x"}}}
        if k % 2 == 0:
            scene["chapter"] = {"en": f"Part {k // 2}"}
        out.append(scene)
    return {"version": 1, "title": {"en": "Demo"}, "languages": ["en"], "scenes": out}


def activity_for(scenes: int) -> Activity:
    frames = SCENE_S * scenes * 100
    voiced = [False] * frames
    for _, a, b in line_spans(scenes):
        for i in range(int(a * 100), int(b * 100)):
            voiced[i] = True
    return Activity(voiced)


def make_video(folder: Path, scenes: int, name: str = "demo.en.mp4") -> Path:
    """A 160x90 test pattern with a tone during every line, plus its timeline and lines sidecars."""
    seconds = SCENE_S * scenes
    gate = "+".join(f"between(t,{a},{b})" for _, a, b in line_spans(scenes))
    video = folder / name
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=s=160x90:r={FPS}:d={seconds}",
        "-f", "lavfi", "-i", f"sine=f=440:r=48000:d={seconds},volume='2*({gate})':eval=frame,aformat=channel_layouts=stereo",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(video),
    ], check=True)
    video.with_suffix(".timeline.json").write_text(json.dumps(timeline_doc(scenes)), encoding="utf-8")
    video.with_suffix(".lines.json").write_text(json.dumps(lines_doc(scenes)), encoding="utf-8")
    return video


class MarksTest(unittest.TestCase):
    def test_chapter_starts_merge_repeated_chapters(self):
        doc = scenes_doc(4)
        doc["scenes"][1]["chapter"] = {"en": "Part 0"}  # the same chapter again is no new mark
        self.assertEqual(chapter_starts(doc, timeline_doc(4), "en"), [(0.0, "Part 0"), (40.0, "Part 1")])
        self.assertEqual(chapter_starts(None, timeline_doc(4), "en"), [])

    def test_chapter_starts_refuse_another_render(self):
        with self.assertRaises(ShortsError):
            chapter_starts(scenes_doc(3), timeline_doc(4), "en")

    def test_candidates_are_scene_starts_chapters_and_sentence_ends(self):
        lines = lines_doc(2)
        lines[1]["text"] = "a clause that goes on,"  # no sentence end after it
        cands = dict(candidates(timeline_doc(2), [(0.0, "Part 0")], lines))
        self.assertEqual(cands[0.0], "chapter")
        self.assertEqual(cands[20.0], "scene")
        self.assertEqual(cands[40.0], "end")
        self.assertIn(3.85, cands)  # between line 0 and line 1
        self.assertNotIn(7.85, cands)  # after the clause
        self.assertEqual(cands[3.85], "sentence")
        self.assertFalse([t for t in cands if 19.5 < t < 20.0 or 20.0 < t < 20.5])  # folded into the scene start

    def test_cuts_land_in_silence_on_whole_frames(self):
        act = activity_for(2)
        self.assertIsNone(silence_around(act, 2.0, 40.0))  # mid-line, with no pause within reach
        run = silence_around(act, 3.85, 40.0)
        self.assertAlmostEqual(run[0], 3.5, places=2)
        self.assertAlmostEqual(run[1], 4.2, places=2)
        marks = place([(0.0, "chapter"), (2.0, "sentence"), (3.85, "sentence"), (40.0, "end")], act, FPS, 40.0)
        self.assertEqual([m.t for m in marks], [0.0, 3.85, 40.0])
        m = marks[1]
        for frame in (m.start_frame, m.end_frame):
            self.assertTrue(3.5 <= frame / FPS <= 4.2, frame)
        self.assertEqual(marks[0].start_frame, 0)
        self.assertEqual(marks[-1].end_frame, 40 * FPS)

    def test_the_video_ends_are_marks_even_with_speech_on_them(self):
        act = Activity([True] * 1000)  # 10 s of speech, no pause at all
        marks = place([(0.0, "chapter"), (5.0, "scene"), (10.0, "end")], act, FPS, 10.0)
        self.assertEqual([(m.kind, m.start_frame, m.end_frame) for m in marks],
                         [("chapter", 0, 0), ("end", 300, 300)])

    def test_clip_lines_keep_whole_lines_on_the_clip_clock(self):
        marks = place(candidates(timeline_doc(4), [], lines_doc(4)), activity_for(4), FPS, 80.0)
        w = pick(marks, FPS, count=1)[0]
        got = clip_lines(lines_doc(4), w)
        self.assertTrue(got)
        self.assertTrue(all(0 <= ln["start_s"] < ln["end_s"] <= w.duration_s for ln in got))


class PickTest(unittest.TestCase):
    def marks(self, scenes: int, chapters=None):
        return place(candidates(timeline_doc(scenes), chapters or [], lines_doc(scenes)), activity_for(scenes), FPS,
                     float(SCENE_S * scenes))

    def test_three_windows_of_45_to_60_s_without_overlap(self):
        chapters = chapter_starts(scenes_doc(10), timeline_doc(10), "en")
        wins = pick(self.marks(10, chapters), FPS, chapters=chapters)
        self.assertEqual(len(wins), 3)
        for w in wins:
            self.assertTrue(45 <= w.duration_s <= 60, w)
            self.assertIsNotNone(w.chapter)
        for a, b in zip(wins, wins[1:]):
            self.assertLessEqual(a.end_frame, b.start_frame)
        self.assertEqual((wins[0].start_frame, wins[0].start_kind, wins[0].chapter), (0, "chapter", "Part 0"))
        self.assertGreaterEqual(sum(w.start_kind == "chapter" for w in wins), 2)

    def test_a_short_video_gives_fewer_windows_not_padding(self):
        wins = pick(self.marks(4), FPS)  # 80 s: one 45-60 s window fits
        self.assertEqual(len(wins), 1)
        self.assertEqual(pick(self.marks(2), FPS), [])  # 40 s: none

    def test_more_windows_beat_better_marks(self):
        # Chapter marks at 0 and 50 allow one great window; sentence marks allow two plain ones.
        marks = [Mark(0, "chapter", 0, 0), Mark(10, "sentence", 300, 300), Mark(55, "sentence", 1650, 1650),
                 Mark(58, "chapter", 1740, 1740), Mark(103, "sentence", 3090, 3090), Mark(110, "end", 3300, 3300)]
        wins = pick(marks, FPS, count=3)
        self.assertEqual(len(wins), 2)

    def test_names_ass_and_layout(self):
        self.assertEqual(short_name(Path("rag.en.mp4"), "en", 2), "rag-2.en.mp4")
        self.assertEqual(ass_time(61.234), "0:01:01.23")
        self.assertEqual(ass_text("a {b}\\c\nd"), "a (b)⧵c\\Nd")
        lay = layout(1080, 1920, 1920, 1080)
        self.assertEqual((lay.frame_w + 2 * lay.border, lay.frame_h % 2), (1080, 0))
        self.assertGreater(lay.header_y - lay.header_size, 0.15 * 1920)  # clear of the top bar
        two_rows = lay.subtitle_y + 2 * 1.3 * lay.subtitle_size
        self.assertLess(two_rows, 0.75 * 1920)  # clear of the title and buttons at the bottom
        self.assertGreaterEqual(lay.subtitle_size, 60)  # large

    def test_options_are_checked(self):
        for bad in ({"count": 0}, {"min_s": 70}, {"width": 1920, "height": 1080}, {"theme": "neon"}):
            with self.assertRaises(ValueError):
                ShortOptions(**bad)


@unittest.skipUnless(has_libass(), "ffmpeg with libass is not installed")
class CutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shorts-test-"))
        self.job = self.tmp / "demo"
        self.job.mkdir()
        (self.job / workdir.JOB_MARKER).touch()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def probe(self, path: Path) -> dict:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,duration",
                              "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
        return {s["codec_type"]: s for s in json.loads(out)["streams"]}

    def test_cuts_three_shorts_into_the_job(self):
        video = make_video(self.job, 10)
        opts = ShortOptions(width=270, height=480, preset="ultrafast")
        result = make_shorts(video, "en", scenes_doc(10), opts)
        self.assertTrue(result.ok, result.json())
        self.assertEqual(len(result.clips), 3)
        for clip in result.clips:
            self.assertEqual(clip.path.parent, self.job / "shorts")
            streams = self.probe(clip.path)
            self.assertEqual((streams["video"]["width"], streams["video"]["height"]), (270, 480))
            seconds = float(streams["video"]["duration"])
            self.assertTrue(45 <= seconds <= 60, seconds)
            self.assertAlmostEqual(float(streams["audio"]["duration"]), seconds, delta=0.05)
            self.assertEqual(count_video_frames(clip.path), clip.window.end_frame - clip.window.start_frame)
            self.assertLessEqual(abs(clip.measured["integrated_lufs"] - TARGET_LUFS), LUFS_TOLERANCE)
            self.assertLessEqual(clip.measured["true_peak_dbtp"], MAX_TRUE_PEAK_DBTP)
            cues = parse_srt(clip.path.with_suffix(".srt").read_text(encoding="utf-8"))
            self.assertTrue(cues)
            self.assertTrue(all(0 <= c.start_s < c.end_s <= seconds + 0.01 for c in cues))
            self.assertTrue(all(len(row) <= 20 for c in cues for row in c.text.split("\n")))
            # Every cue starts with speech: at most a frame or two before the tone of its line in the clip.
            starts = [a - clip.window.start_s for _, a, _ in line_spans(10)]
            self.assertTrue(any(abs(cues[0].start_s - s) < 0.1 for s in starts), cues[0])
        report = json.loads(result.report.read_text(encoding="utf-8"))
        self.assertEqual((report["wanted"], report["found"]), (3, 3))
        self.assertEqual(set(workdir.final_videos(self.job)) - {"demo.en.mp4"},
                         {f"shorts/demo-{i}.en.mp4" for i in (1, 2, 3)})  # so clean --after-upload covers them

    def test_short_video_is_reported_and_not_padded(self):
        video = make_video(self.job, 4)
        (self.job / "shorts").mkdir()
        (self.job / "shorts" / "demo-3.en.mp4").write_bytes(b"old")  # from an earlier, longer cut
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["shorts", str(video), "--preset", "ultrafast"])
        self.assertEqual(code, 1)
        self.assertIn("only 1 of 3 Shorts windows", err.getvalue())
        self.assertEqual(sorted(p.name for p in (self.job / "shorts").glob("*.mp4")), ["demo-1.en.mp4"])

    def test_cli_refusals(self):
        video = make_video(self.job, 3)
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(cli.main(["shorts", str(self.job / "demo.en.burned.mp4")]), 1)
            video.with_suffix(".timeline.json").unlink()
            self.assertEqual(cli.main(["shorts", str(video), "--dry-run"]), 1)
            self.assertEqual(cli.main(["shorts", str(self.job / "noname.mp4")]), 2)
        self.assertIn("burned in", err.getvalue())
        self.assertIn("demo.en.timeline.json", err.getvalue())


if __name__ == "__main__":
    unittest.main()
