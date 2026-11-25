"""Tests for subtitles: layout, SRT, speech activity, drift and alignment maths, burn-in and the CLI hooks.

The timing path (narration timing -> SRT) runs everywhere ffmpeg is installed.
The faster-whisper path runs only when faster-whisper and numpy are importable.
"""

import array
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motion_studio import cli  # noqa: E402
from motion_studio.render import ffmpeg_available  # noqa: E402
from motion_studio.subtitles import (  # noqa: E402
    LANDSCAPE_CHARS,
    PORTRAIT_CHARS,
    Activity,
    Cue,
    SubtitleError,
    Word,
    activity,
    align_lines,
    burn_cmd,
    cues_from_lines,
    drift,
    match_words,
    normalise,
    parse_srt,
    pieces,
    srt_time,
    to_srt,
    trim_lines,
    width_for,
    wrap,
)

SAMPLE = ROOT / "samples" / "three-scenes"
HAS_WHISPER = all(importlib.util.find_spec(m) is not None for m in ("faster_whisper", "numpy"))


def has_libass() -> bool:
    if not ffmpeg_available():
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return " subtitles " in out


class LayoutTest(unittest.TestCase):
    def test_wrap_balances_two_rows(self):
        self.assertEqual(wrap("short line", 42), "short line")
        rows = wrap("Each scene has narration per language, a visual and a duration,", 42).split("\n")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(len(r) <= 42 for r in rows))
        self.assertLess(abs(len(rows[0]) - len(rows[1])), 12)

    def test_wrap_keeps_a_single_long_word(self):
        self.assertEqual(wrap("x" * 60, 42), "x" * 60)

    def test_pieces_fit_two_rows_and_keep_every_word(self):
        text = " ".join(f"word{i}," if i % 7 == 6 else f"word{i}" for i in range(60))
        parts = pieces(text, 42)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 84 for p in parts))
        self.assertEqual(" ".join(parts), text)

    def test_portrait_rows_are_narrower(self):
        self.assertEqual(width_for(1920, 1080), LANDSCAPE_CHARS)
        self.assertEqual(width_for(1080, 1920), PORTRAIT_CHARS)


class CueTest(unittest.TestCase):
    def test_lines_become_cues_in_order_and_short_cues_are_held_without_overlap(self):
        lines = [
            {"text": "Next.", "start_s": 8.0, "end_s": 9.0},
            {"text": "Short.", "start_s": 7.3, "end_s": 7.6},
            {"text": "Hello there.", "start_s": 1.0, "end_s": 3.0},
        ]
        cues = cues_from_lines(lines)
        self.assertEqual([c.text for c in cues], ["Hello there.", "Short.", "Next."])
        self.assertEqual(cues[1].start_s, 7.3)
        self.assertAlmostEqual(cues[1].end_s, 7.96, places=3, msg="held until just before the next cue")
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a.end_s, b.start_s)

    def test_long_line_is_split_at_the_pause_near_the_split_point(self):
        text = "An animated code block is written line by line, then the retrieval step and the answer are framed."
        act = Activity([True] * 1000)
        for i in range(330, 360):  # a 300 ms pause at 3.3-3.6 s
            act.voiced[i] = False
        cues = cues_from_lines([{"text": text, "start_s": 0.0, "end_s": 7.0}], act=act)
        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[0].end_s, 3.3)
        self.assertAlmostEqual(cues[1].start_s, 3.6)
        self.assertEqual(" ".join(c.text.replace("\n", " ") for c in cues), text)

    def test_many_pieces_never_lose_text_or_go_backwards(self):
        text = " ".join(["alpha beta gamma delta,"] * 20)
        act = Activity([i % 50 > 5 for i in range(3000)])  # a 60 ms dip every 0.5 s, plus longer pauses below
        for start in (400, 900, 1500, 2100):
            for i in range(start, start + 20):
                act.voiced[i] = False
        cues = cues_from_lines([{"text": text, "start_s": 0.0, "end_s": 30.0}], act=act)
        self.assertEqual(" ".join(c.text.replace("\n", " ") for c in cues), text)
        for c in cues:
            self.assertGreater(c.end_s, c.start_s)
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a.end_s, b.start_s)

    def test_a_split_never_jumps_to_a_pause_a_piece_away(self):
        text = " ".join(f"word{i}" for i in range(48))
        act = Activity([True] * 1300)
        for i in range(700, 715):  # one pause at 7.0-7.15 s in a 12 s line
            act.voiced[i] = False
        cues = cues_from_lines([{"text": text, "start_s": 0.0, "end_s": 12.0}], width=20, act=act)
        lengths = [c.end_s - c.start_s for c in cues]
        self.assertLess(max(lengths) / min(lengths), 1.6, lengths)

    def test_portrait_pieces_wrap_into_rows_that_fit(self):
        for p in pieces("internationalisation documentation responsibilities considerations", 20):
            self.assertTrue(all(len(r) <= 20 for r in wrap(p, 20).split("\n")) or " " not in p, p)

    def test_empty_or_reversed_lines_are_skipped(self):
        self.assertEqual(cues_from_lines([{"text": "x", "start_s": 2, "end_s": 2}, {"text": " ", "start_s": 0, "end_s": 1}]), [])


class SrtTest(unittest.TestCase):
    def test_times_round_and_carry(self):
        self.assertEqual(srt_time(0), "00:00:00,000")
        self.assertEqual(srt_time(59.9996), "00:01:00,000")
        self.assertEqual(srt_time(3725.5), "01:02:05,500")
        self.assertEqual(srt_time(-1), "00:00:00,000")

    def test_round_trip_keeps_text_and_times(self):
        cues = [Cue(0.5, 2.0, "Xin chào.\nĐây là phụ đề."), Cue(2.1, 3.25, "Second")]
        text = to_srt(cues)
        self.assertTrue(text.startswith("1\n00:00:00,500 --> 00:00:02,000\nXin chào.\nĐây là phụ đề.\n\n2\n"))
        self.assertEqual(parse_srt(text), cues)
        self.assertEqual(parse_srt(text.replace("\n", "\r\n")), cues)


class ActivityTest(unittest.TestCase):
    def test_span_pauses_and_trim(self):
        act = Activity([False] * 50 + [True] * 100 + [False] * 30 + [True] * 50 + [False] * 20)
        self.assertEqual(act.span(0.0, 3.0), (0.5, 2.3))
        self.assertEqual(act.pauses(0.0, 3.0), [(1.5, 1.8)], "leading and trailing silence are not pauses")
        self.assertEqual(act.pauses(0.0, 3.0, min_s=0.5), [])
        self.assertIsNone(act.span(2.4, 2.5))
        lines = [{"text": "a", "start_s": 0.0, "end_s": 1.7, "scene": "s"}, {"text": "b", "start_s": 2.35, "end_s": 2.5}]
        trimmed = trim_lines(lines, act)
        self.assertEqual((trimmed[0]["start_s"], trimmed[0]["end_s"], trimmed[0]["scene"]), (0.5, 1.5, "s"))
        self.assertEqual(trimmed[1], lines[1], "a line with no speech keeps its times")

    @unittest.skipUnless(ffmpeg_available(), "ffmpeg not installed")
    def test_activity_reads_the_audio_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.wav"
            rate = 16000
            pcm = array.array("h", bytes(2 * rate // 2))  # 0.5 s silence
            pcm.extend(array.array("h", [8000 if (i // 20) % 2 else -8000 for i in range(rate)]))  # 1 s tone
            pcm.extend(array.array("h", bytes(2 * rate // 2)))
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(pcm.tobytes())
            span = activity(path).span(0.0, 2.0)
            self.assertAlmostEqual(span[0], 0.5, delta=0.011)
            self.assertAlmostEqual(span[1], 1.5, delta=0.011)


def words(*items):
    return [Word(a, b, t) for a, b, t in items]


class MatchDriftAlignTest(unittest.TestCase):
    def test_normalise_keeps_vietnamese_letters(self):
        self.assertEqual(normalise("Việt,"), "việt")
        self.assertEqual(normalise("ffmpeg."), "ffmpeg")
        self.assertEqual(normalise("—"), "")

    def test_first_and_last_words_are_matched(self):
        heard = words((0.5, 0.8, " Hello"), (0.8, 1.0, " there."), (2.0, 2.3, " General"), (2.3, 2.7, " Example"))
        self.assertEqual(match_words(["Hello there.", "General Acme"], heard), [(0, 1), (2, None)])

    def test_drift_gate(self):
        cues = [Cue(0.5, 1.0, "one two"), Cue(2.0, 3.0, "three four"), Cue(4.0, 5.0, "five six"), Cue(6.0, 7.0, "seven")]
        good = words((0.55, 0.7, "one"), (0.7, 0.9, "two"), (2.3, 2.5, "three"), (2.5, 2.7, "four"),
                     (3.95, 4.2, "five"), (4.2, 4.5, "six"), (6.05, 6.5, "seven"))
        report = drift(cues, good, "en", "test")
        self.assertTrue(report.ok, report)
        self.assertEqual((report.matched, report.max_abs_s, report.median_s), (4, 0.3, 0.05))
        self.assertEqual(report.worst[0]["text"], "three four")
        late = words(*[(w.start_s + 0.5, w.end_s + 0.5, w.text) for w in good])
        self.assertFalse(drift(cues, late).ok, "a systematic 0.5 s offset fails")
        growing = words(*[(w.start_s + 0.15 * i, w.end_s + 0.15 * i, w.text) for i, w in enumerate(good)])
        self.assertFalse(drift(cues, growing).ok, "drift that grows over the video fails")
        self.assertFalse(drift(cues, words((9, 9.5, "zebra"))).ok, "no matched cue fails")
        ten = [Cue(float(i), i + 0.5, f"w{i}") for i in range(10)]
        one_scene_off = words(*[(i + (0.6 if i < 3 else 0.0), i + 0.4, f"w{i}") for i in range(10)])
        report = drift(ten, one_scene_off)
        self.assertLessEqual(abs(report.median_s), 0.2)
        self.assertFalse(report.ok, "three cues 0.6 s late fail the p90 limit")
        json.loads(report.json())

    def test_align_uses_the_first_matched_word_when_the_first_word_was_missed(self):
        texts = ["FFmpeg joins the clips.", "Then it writes one file."]
        heard = words((1.30, 1.6, " joins"), (1.6, 1.8, " the"), (1.8, 2.3, " clips."),
                      (3.0, 3.2, " Then"), (3.2, 3.4, " it"), (3.4, 3.7, " writes"), (3.7, 3.9, " one"), (3.9, 4.3, " file."))
        lines = align_lines(texts, heard, ["a", "b"])
        self.assertLess(lines[0]["start_s"], 1.3, "moved back for the unheard first word")
        self.assertGreater(lines[0]["start_s"], 0.5)
        self.assertEqual((lines[1]["start_s"], lines[1]["end_s"], lines[1]["scene"]), (3.0, 4.3, "b"))

    def test_align_shares_the_gap_between_several_unmatched_lines(self):
        heard = words((0.0, 0.5, "alpha"), (3.0, 3.5, "delta"), (3.5, 4.0, "epsilon"))
        lines = align_lines(["alpha", "beta", "gamma", "delta epsilon"], heard)
        self.assertEqual(lines[3]["start_s"], 3.0, "the matched line after the run keeps its time")
        self.assertLess(lines[1]["end_s"], lines[2]["start_s"])
        self.assertLessEqual(lines[2]["end_s"], 3.0)

    def test_align_fills_an_unmatched_line_and_refuses_no_match(self):
        heard = words((0.0, 0.5, "alpha"), (3.0, 3.5, "gamma"))
        lines = align_lines(["alpha", "beta", "gamma"], heard)
        self.assertTrue(lines[0]["end_s"] <= lines[1]["start_s"] < lines[1]["end_s"] <= lines[2]["start_s"])
        with self.assertRaises(SubtitleError):
            align_lines(["alpha"], words((0, 1, "zzz")))


class BurnCommandTest(unittest.TestCase):
    def test_portrait_gets_its_own_size_and_margin(self):
        land = burn_cmd(Path("v.mp4"), "subs.srt", Path("o.mp4"), 1920, 1080)
        port = burn_cmd(Path("v.mp4"), "subs.srt", Path("o.mp4"), 1080, 1920)
        vf = lambda c: c[c.index("-vf") + 1]  # noqa: E731
        self.assertIn("subtitles=filename=subs.srt", vf(land))
        self.assertIn("FontSize=18", vf(land))
        self.assertIn("FontSize=14", vf(port))
        self.assertIn("MarginV=70", vf(port))
        self.assertIn("copy", land[land.index("-c:a") + 1])


@unittest.skipUnless(ffmpeg_available(), "ffmpeg not installed")
class CliSubtitlesTest(unittest.TestCase):
    """Narrate the sample with the mock engine, render, and check the SRT against the narration timing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        shutil.copytree(SAMPLE, self.base / "three-scenes")
        self.scenes = self.base / "three-scenes" / "scenes.json"
        self.out = self.base / "out"

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def narrate(self, lang="en"):
        code, out, err = self.run_cli("narrate", str(self.scenes), "--lang", lang, "--engine", "mock", "--voice", "tone-220")
        self.assertEqual(code, 0, out + err)

    def render(self, *extra):
        return self.run_cli("render", str(self.scenes), "--lang", "en", "-o", str(self.out), "--preset", "ultrafast",
                            "--crf", "35", *extra)

    def test_render_writes_srt_that_follows_the_narration(self):
        self.narrate()
        code, out, err = self.render()
        self.assertEqual(code, 0, out + err)
        lines = json.loads((self.out / "three-scenes.en.lines.json").read_text(encoding="utf-8"))
        cues = parse_srt((self.out / "three-scenes.en.srt").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cues), len(lines))
        starts = [c.start_s for c in cues]
        for line in lines:  # every line opens a cue within one 10 ms frame (+ the mock tone's fade-in)
            self.assertLess(min(abs(s - line["start_s"]) for s in starts), 0.03, line)
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a.end_s, b.start_s)
        self.assertFalse((self.out / "three-scenes.en.burned.mp4").exists())

    @unittest.skipUnless(has_libass(), "ffmpeg has no subtitles filter (libass)")
    def test_burn_writes_a_second_video_with_text_drawn_in(self):
        self.narrate()
        code, out, err = self.render("--subtitles", "burn")
        self.assertEqual(code, 0, out + err)
        clean, burned = self.out / "three-scenes.en.mp4", self.out / "three-scenes.en.burned.mp4"
        self.assertTrue(burned.is_file())
        cue = parse_srt((self.out / "three-scenes.en.srt").read_text(encoding="utf-8"))[0]
        t = f"{(cue.start_s + cue.end_s) / 2:.2f}"

        def bottom(video):
            return subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", t, "-i", str(video), "-frames:v", "1", "-vf",
                 "crop=iw:ih/5:0:ih*4/5,scale=64:16,format=gray", "-f", "rawvideo", "-"],
                capture_output=True, check=True,
            ).stdout

        diff = sum(abs(a - b) for a, b in zip(bottom(clean), bottom(burned)))
        self.assertGreater(diff, 2000, "subtitle text changes the bottom of the frame")

        def frames(video):
            return subprocess.run(
                ["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                 "stream=nb_read_packets", "-of", "csv=p=0", str(video)], capture_output=True, text=True, check=True,
            ).stdout.strip()

        self.assertEqual(frames(clean), frames(burned))

    def test_no_timing_warns_for_srt_and_fails_for_burn(self):
        code, _, err = self.render()
        self.assertEqual(code, 0)
        self.assertIn("WARN three-scenes.en.srt skipped", err)
        self.assertFalse((self.out / "three-scenes.en.srt").exists())
        code, _, _ = self.render("--subtitles", "burn")
        self.assertEqual(code, 1)
        code, _, err = self.render("--subtitles", "none")
        self.assertEqual(code, 0)
        self.assertNotIn(".srt", err)

    def test_rerender_removes_subtitle_files_that_no_longer_match(self):
        self.narrate()
        self.assertEqual(self.render()[0], 0)
        srt, burned = self.out / "three-scenes.en.srt", self.out / "three-scenes.en.burned.mp4"
        burned.write_bytes(b"old cut")
        self.assertEqual(self.render()[0], 0)
        self.assertTrue(srt.is_file())
        self.assertFalse(burned.exists(), "an srt-only render removes an old burned video")
        self.assertEqual(self.render("--subtitles", "none")[0], 0)
        self.assertFalse(srt.exists(), "--subtitles none removes the old srt")

    def test_subtitles_command_rebuilds_srt_from_lines(self):
        self.narrate()
        self.assertEqual(self.render("--subtitles", "none")[0], 0)
        video = self.out / "three-scenes.en.mp4"
        self.assertFalse(video.with_suffix(".srt").exists())
        code, out, err = self.run_cli("subtitles", str(video))
        self.assertEqual(code, 0, out + err)
        self.assertTrue(video.with_suffix(".srt").is_file())

    def test_subtitles_command_argument_errors(self):
        video = self.base / "x.en.mp4"
        self.assertEqual(self.run_cli("subtitles", str(video))[0], 1, "missing video")
        video.write_bytes(b"")
        self.assertEqual(self.run_cli("subtitles", str(video), "--align")[0], 2, "--align needs --scenes")
        code, _, err = self.run_cli("subtitles", str(video))
        self.assertEqual(code, 1)
        self.assertIn("no x.en.lines.json", err)
        odd = self.base / "clip.mp4"
        odd.write_bytes(b"")
        self.assertEqual(self.run_cli("subtitles", str(odd))[0], 2, "language not in the name")


class SnapTest(unittest.TestCase):
    def test_a_word_placed_in_a_pause_moves_to_the_next_onset(self):
        from motion_studio.subtitles import Activity, snap_to_speech

        # speech 0-1.0 s, silence 1.0-2.3 s, speech 2.3-3.0 s (10 ms frames)
        act = Activity([True] * 100 + [False] * 130 + [True] * 70)
        words = [Word(0.5, 1.0, "fixed."), Word(1.0, 1.2, "The"), Word(2.36, 2.8, "volumetric")]
        snapped = snap_to_speech(words, act)
        self.assertEqual(snapped[0], words[0])
        self.assertEqual((snapped[1].start_s, snapped[1].end_s, snapped[1].text), (2.3, 2.3, "The"))
        self.assertEqual(snapped[2], words[2])

    def test_a_voiced_word_or_a_far_onset_is_kept(self):
        from motion_studio.subtitles import Activity, snap_to_speech

        act = Activity([True] * 100 + [False] * 200 + [True] * 50)
        early = [Word(1.0, 1.2, "a"), Word(1.5, 1.8, "b"), Word(3.1, 3.4, "c")]  # "a": the next word starts before 3.0 s
        self.assertEqual(snap_to_speech(early, act)[0], early[0])
        self.assertEqual(snap_to_speech([Word(0.2, 0.9, "x")], act), [Word(0.2, 0.9, "x")])


@unittest.skipUnless(HAS_WHISPER and ffmpeg_available(), "faster-whisper and numpy are not installed (pip install -e .[subtitles])")
class WhisperTest(unittest.TestCase):
    """Loads the tiny model (downloads about 75 MB to the Hugging Face cache on first use)."""

    def test_recogniser_runs_on_a_short_file(self):
        from motion_studio.subtitles import whisper_recogniser

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quiet.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(bytes(2 * 16000))
            heard = whisper_recogniser("tiny", "cpu")(path, "en")
        self.assertIsInstance(heard, list)
        self.assertTrue(all(isinstance(w, Word) and isinstance(w.start_s, float) for w in heard))


if __name__ == "__main__":
    unittest.main()
