import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import importlib.util
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motion_studio import cli  # noqa: E402
from motion_studio.narrate import (  # noqa: E402
    StaleTiming,
    check_timing,
    line_timeline,
    narrate,
    save_scenes,
    split_lines,
    timing_path,
)
from motion_studio.render import RenderOptions, ffmpeg_available, render  # noqa: E402
from motion_studio.schema import load  # noqa: E402
from motion_studio.tts import ENGINES, TTSError, adapter_class, get_adapter, read_wav, write_wav  # noqa: E402
from motion_studio.tts.mock import MockTTS  # noqa: E402
from motion_studio.tts.vieneu_tts import check_reference  # noqa: E402
from motion_studio.voices_page import (  # noqa: E402
    build_page,
    mock_candidates,
    render_page,
    run_candidates,
    word_error_rate,
)

SAMPLE_DIR = ROOT / "samples" / "three-scenes"


def copy_sample(tmp: Path) -> Path:
    dst = tmp / "three-scenes"
    shutil.copytree(SAMPLE_DIR, dst)
    return dst / "scenes.json"


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


class SplitLinesTest(unittest.TestCase):
    def test_sentences_and_paragraph_lines(self):
        text = "First one. Second one!  Third?\nFourth line without a stop\n\n  Fifth… done."
        self.assertEqual(
            split_lines(text),
            ["First one.", "Second one!", "Third?", "Fourth line without a stop", "Fifth…", "done."],
        )

    def test_vietnamese_and_quotes(self):
        text = 'Mô hình tìm tài liệu trước. "Rồi mới trả lời." Hết!'
        self.assertEqual(split_lines(text), ["Mô hình tìm tài liệu trước.", '"Rồi mới trả lời."', "Hết!"])

    def test_decimals_and_abbreviations_stay_whole(self):
        self.assertEqual(split_lines("It costs 3.5 dollars, i.e.cheap."), ["It costs 3.5 dollars, i.e.cheap."])

    def test_blank(self):
        self.assertEqual(split_lines("  \n "), [])

    def test_abbreviations_initials_and_list_numbers_do_not_split(self):
        self.assertEqual(split_lines("Chúng ta đến TP. Hồ Chí Minh. Rồi về."), ["Chúng ta đến TP. Hồ Chí Minh.", "Rồi về."])
        self.assertEqual(split_lines("Use a vector DB, e.g. Chroma. Then query it."), ["Use a vector DB, e.g. Chroma.", "Then query it."])
        self.assertEqual(split_lines("Dr. Smith met J. Doe in the U.S. today. Bye."), ["Dr. Smith met J. Doe in the U.S. today.", "Bye."])
        self.assertEqual(split_lines("1. Load the data.\n2. Train."), ["1. Load the data.", "2. Train."])

    def test_stray_punctuation_joins_a_neighbour(self):
        self.assertEqual(split_lines("Wait . . . then go."), ["Wait . . .", "then go."])  # no punctuation-only lines
        self.assertEqual(split_lines("… Start here."), ["… Start here."])


class WavAndRegistryTest(unittest.TestCase):
    def test_write_and_read_wav_round_trip_with_clipping(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a" / "x.wav"
            frames = write_wav(path, [0.0, 0.5, -0.5, 2.0, -2.0], 16000)
            data, rate = read_wav(path)
        self.assertEqual((frames, rate), (5, 16000))
        self.assertEqual(list(data), [0, 16384, -16384, 32767, -32768])

    def test_nested_rows_are_flattened(self):
        class Rows(list):
            def tolist(self):
                return [list(self)]

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(write_wav(Path(tmp) / "x.wav", Rows([0.1, 0.2, 0.3]), 8000), 3)

    def test_unknown_engine(self):
        with self.assertRaisesRegex(TTSError, "unknown TTS engine 'nope'"):
            get_adapter("nope")

    def test_registry_lists_the_engines(self):
        self.assertEqual(sorted(ENGINES), ["clone", "kokoro", "mock", "vieneu"])

    def test_importing_tts_does_not_import_heavy_libraries(self):
        code = (
            "import sys; sys.path.insert(0, 'src'); import motion_studio.tts, motion_studio.narrate, "
            "motion_studio.voices_page; bad = [m for m in ('torch', 'kokoro', 'vieneu', 'numpy') if m in sys.modules]; "
            "print(','.join(bad))"
        )
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), "")

    def test_missing_engine_package_is_a_clear_error(self):
        try:
            import kokoro  # noqa: F401
        except ImportError:
            with self.assertRaisesRegex(TTSError, "tts-setup|not installed|extra packages"):
                get_adapter("kokoro")
        else:
            self.skipTest("kokoro is installed here")

    def test_adapter_class_for_mock(self):
        self.assertIs(adapter_class("mock"), MockTTS)


class MockAdapterTest(unittest.TestCase):
    def test_length_grows_with_text_and_is_deterministic(self):
        tts = MockTTS()
        with tempfile.TemporaryDirectory() as tmp:
            short = tts.synthesize("Hi.", "tone-220", "en", Path(tmp) / "a.wav")
            long = tts.synthesize("A much longer sentence than the first one.", "tone-220", "en", Path(tmp) / "b.wav")
            again = tts.synthesize("Hi.", "tone-220", "en", Path(tmp) / "c.wav")
            self.assertEqual((Path(tmp) / "a.wav").read_bytes(), (Path(tmp) / "c.wav").read_bytes())
        self.assertGreater(long.duration_s, short.duration_s)
        self.assertEqual(short.duration_s, again.duration_s)
        self.assertEqual(short.sample_rate, 24000)

    def test_unknown_voice_and_empty_text(self):
        tts = MockTTS()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(TTSError, "no vi voice 'x'"):
                tts.synthesize("Xin chào.", "x", "vi", Path(tmp) / "a.wav")
            with self.assertRaisesRegex(TTSError, "empty"):
                tts.synthesize("  ", "tone-220", "vi", Path(tmp) / "a.wav")


class ReferenceCheckTest(unittest.TestCase):
    def test_reference_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with self.assertRaisesRegex(TTSError, "not found"):
                check_reference(tmp / "missing.wav")
            write_wav(tmp / "short.wav", [0.0] * 16000, 16000)
            with self.assertRaisesRegex(TTSError, "use a clean clip"):
                check_reference(tmp / "short.wav")
            (tmp / "junk.wav").write_bytes(b"not a wav")
            with self.assertRaisesRegex(TTSError, "not a usable WAV.*ffmpeg"):
                check_reference(tmp / "junk.wav")
            write_wav(tmp / "ok.wav", [0.0] * (16000 * 7), 16000)
            self.assertAlmostEqual(check_reference(tmp / "ok.wav"), 7.0)

    def test_float_and_extensible_wavs_are_measured(self):
        import struct

        def wav(tag, bits, seconds, rate=16000, channels=1, extensible=False):
            data = bytes(int(rate * seconds) * channels * bits // 8)
            if extensible:
                fmt = struct.pack("<HHIIHHHHI", 0xFFFE, channels, rate, rate * channels * bits // 8,
                                  channels * bits // 8, bits, 22, bits, 4) + struct.pack("<H", tag) + bytes(14)
            else:
                fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * channels * bits // 8, channels * bits // 8, bits)
            body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
            return b"RIFF" + struct.pack("<I", len(body)) + body

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "f32.wav").write_bytes(wav(3, 32, 6.5))
            (tmp / "ext.wav").write_bytes(wav(3, 32, 7.0, channels=2, extensible=True))
            (tmp / "alaw.wav").write_bytes(wav(6, 8, 7.0))
            self.assertAlmostEqual(check_reference(tmp / "f32.wav"), 6.5)
            self.assertAlmostEqual(check_reference(tmp / "ext.wav"), 7.0)
            with self.assertRaisesRegex(TTSError, "unsupported WAV format"):
                check_reference(tmp / "alaw.wav")

    def test_messages_do_not_reveal_the_folder(self):
        with self.assertRaises(TTSError) as ctx:
            check_reference(Path("/some/private/folder/my-clip.wav"))
        self.assertIn("my-clip.wav", str(ctx.exception))
        self.assertNotIn("private", str(ctx.exception))


class NarrateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = copy_sample(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_narrate_writes_scene_audio_and_line_timing(self):
        scenes = load(self.path)
        tts = MockTTS()
        result = narrate(scenes, "vi", tts, "tone-330", pause_s=0.25)
        self.assertEqual(result.synthesized, sum(len(split_lines(s["narration"]["vi"])) for s in scenes.scenes))
        for scene, narr in zip(scenes.scenes, result.scenes):
            self.assertEqual(scene["audio"]["vi"], f"audio/tts/{scene['id']}.vi.wav")
            path = scenes.base_dir / narr.file
            self.assertAlmostEqual(wav_seconds(path), narr.duration_s, places=6)
            lines = narr.lines
            self.assertEqual([ln.text for ln in lines], split_lines(scene["narration"]["vi"]))
            self.assertEqual(lines[0].start_s, 0.0)
            self.assertAlmostEqual(lines[-1].end_s, narr.duration_s, places=6)
            for a, b in zip(lines, lines[1:]):
                self.assertAlmostEqual(b.start_s - a.end_s, 0.25, places=3)
            for ln in lines:
                self.assertAlmostEqual(ln.end_s - ln.start_s, wav_seconds(scenes.base_dir / ln.file), places=6)
        save_scenes(scenes, self.path, "vi")
        timing = json.loads(timing_path(scenes.base_dir, "vi").read_text(encoding="utf-8"))
        self.assertEqual((timing["engine"], timing["voice"], timing["lang"]), ("mock", "tone-330", "vi"))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["scenes"][0]["audio"], {"vi": "audio/tts/intro.vi.wav"})
        reloaded = load(self.path)
        self.assertEqual(reloaded.scenes[1]["audio"]["vi"], "audio/tts/pipeline.vi.wav")
        check_timing(timing, reloaded, "vi")  # fresh timing matches

    def test_unchanged_lines_are_reused_and_changed_ones_remade(self):
        scenes = load(self.path)
        tts = MockTTS()
        first = narrate(scenes, "en", tts, "tone-220")
        calls = tts.calls
        second = narrate(load(self.path), "en", tts, "tone-220")
        self.assertEqual((second.synthesized, second.reused), (0, first.synthesized))
        self.assertEqual(tts.calls, calls)
        doc = load(self.path)
        doc.scenes[0]["narration"]["en"] = "A brand new first line. " + doc.scenes[0]["narration"]["en"]
        third = narrate(doc, "en", tts, "tone-220")
        self.assertGreaterEqual(third.synthesized, 1)
        other_voice = narrate(load(self.path), "en", tts, "tone-330")
        self.assertEqual(other_voice.reused, 0, "a new voice makes every line again")
        forced = narrate(load(self.path), "en", tts, "tone-330", force=True)
        self.assertEqual(forced.reused, 0)

    def test_failed_run_never_leaves_audio_under_a_wrong_key(self):
        class FailOn(MockTTS):
            def __init__(self, bad):
                super().__init__()
                self.bad = bad

            def generate(self, text, voice, lang):
                if self.bad in text:
                    raise TTSError("simulated engine failure")
                return super().generate(text, voice, lang)

        original = load(self.path)
        first_text = split_lines(original.scenes[0]["narration"]["en"])[0]
        narrate(original, "en", MockTTS(), "tone-220")
        good_line = [p for p in (original.base_dir / "audio/tts/lines").glob("intro-000-*.en.wav")]
        self.assertEqual(len(good_line), 1)
        good_frames = len(read_wav(good_line[0])[0])
        timing_before = timing_path(original.base_dir, "en").read_text(encoding="utf-8")
        scene_wav = original.base_dir / "audio/tts/intro.en.wav"
        scene_bytes = scene_wav.read_bytes()

        edited = load(self.path)
        edited.scenes[0]["narration"]["en"] = "A much longer replacement sentence for the very first line. Boom here."
        with self.assertRaises(TTSError):
            narrate(edited, "en", FailOn("Boom"), "tone-220")
        self.assertEqual(scene_wav.read_bytes(), scene_bytes, "scene audio is untouched by a failed run")
        self.assertEqual(timing_path(original.base_dir, "en").read_text(encoding="utf-8"), timing_before)

        again = narrate(load(self.path), "en", MockTTS(), "tone-220")  # the original text
        self.assertEqual(again.synthesized, 0)
        line0 = again.scenes[0].lines[0]
        self.assertEqual(line0.text, first_text)
        self.assertEqual(len(read_wav(original.base_dir / line0.file)[0]), good_frames)
        leftovers = [p.name for p in (original.base_dir / "audio/tts/lines").glob("*.en.wav")]
        self.assertEqual(len(leftovers), again.reused, "line files of the failed run are cleaned up")

    def test_engine_settings_are_part_of_the_cache_key(self):
        class Tagged(MockTTS):
            def __init__(self, tag):
                super().__init__()
                self.tag = tag

            def cache_tag(self):
                return self.tag

        narrate(load(self.path), "en", Tagged("ref=aaa"), "tone-220")
        second = narrate(load(self.path), "en", Tagged("ref=bbb"), "tone-220")
        self.assertEqual(second.reused, 0, "a new reference clip makes every line again")
        self.assertNotIn("ref=bbb", timing_path(load(self.path).base_dir, "en").read_text(encoding="utf-8"))

    def test_hand_made_audio_next_to_scenes_is_not_touched(self):
        base = self.path.parent
        own = base / "audio" / "intro.en.wav"
        write_wav(own, [0.2] * 2400, 24000)
        before = own.read_bytes()
        narrate(load(self.path), "en", MockTTS(), "tone-220")
        self.assertEqual(own.read_bytes(), before)

    def test_save_keeps_edits_made_while_narrating(self):
        scenes = load(self.path)
        narrate(scenes, "vi", MockTTS(), "tone-220")
        disk = json.loads(self.path.read_text(encoding="utf-8"))
        disk["scenes"][2]["visual"]["background"] = "#123456"  # edited meanwhile
        disk["scenes"][0]["audio"] = {"en": "audio/tts/intro.en.wav"}  # another language's entry
        write_wav(self.path.parent / "audio/tts/intro.en.wav", [0.0] * 240, 24000)
        self.path.write_text(json.dumps(disk, ensure_ascii=False), encoding="utf-8")
        save_scenes(scenes, self.path, "vi")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["scenes"][2]["visual"]["background"], "#123456")
        self.assertEqual(saved["scenes"][0]["audio"], {"en": "audio/tts/intro.en.wav", "vi": "audio/tts/intro.vi.wav"})

    def test_save_refuses_when_the_narration_changed_meanwhile(self):
        from motion_studio.schema import ScenesError

        scenes = load(self.path)
        narrate(scenes, "vi", MockTTS(), "tone-220")
        disk = json.loads(self.path.read_text(encoding="utf-8"))
        disk["scenes"][1]["narration"]["vi"] = "Một câu khác hẳn."
        self.path.write_text(json.dumps(disk, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ScenesError, "narration changed while narrating"):
            save_scenes(scenes, self.path, "vi")
        self.assertNotIn("audio", json.loads(self.path.read_text(encoding="utf-8"))["scenes"][0])

    def test_stale_timing_is_detected(self):
        scenes = load(self.path)
        narrate(scenes, "en", MockTTS(), "tone-220")
        save_scenes(scenes, self.path, "en")
        timing = json.loads(timing_path(scenes.base_dir, "en").read_text(encoding="utf-8"))
        fresh = load(self.path)
        check_timing(timing, fresh, "en")
        fresh.scenes[1]["narration"]["en"] = "Edited after voicing."
        with self.assertRaisesRegex(StaleTiming, "narration changed"):
            check_timing(timing, fresh, "en")
        fresh = load(self.path)
        write_wav(fresh.resolve(fresh.scenes[0]["audio"]["en"]), [0.0] * 100, 24000)
        with self.assertRaisesRegex(StaleTiming, "audio length differs"):
            check_timing(timing, fresh, "en")
        fresh = load(self.path)
        del fresh.scenes[2]["audio"]
        with self.assertRaisesRegex(StaleTiming, "differ"):
            check_timing(timing, fresh, "en")

    def test_bad_arguments(self):
        scenes = load(self.path)
        with self.assertRaises(ValueError):
            narrate(scenes, "fr", MockTTS(), "tone-220")
        with self.assertRaises(ValueError):
            narrate(scenes, "en", MockTTS(), "tone-220", pause_s=-1)
        with self.assertRaises(ValueError):
            narrate(scenes, "en", MockTTS(), "tone-220", audio_dir="../escape")
        with self.assertRaises(TTSError):
            narrate(scenes, "en", MockTTS(), "no-such-voice")

    def test_line_timeline_adds_scene_starts(self):
        timing = {"scenes": [{"id": "a", "lines": [{"text": "x", "start_s": 0.0, "end_s": 1.0}]},
                             {"id": "b", "lines": [{"text": "y", "start_s": 0.5, "end_s": 1.5}]}]}
        tl = {"scenes": [{"id": "a", "start_s": 0.0}, {"id": "b", "start_s": 2.0}]}
        self.assertEqual(
            line_timeline(timing, tl),
            [{"scene": "a", "text": "x", "start_s": 0.0, "end_s": 1.0}, {"scene": "b", "text": "y", "start_s": 2.5, "end_s": 3.5}],
        )
        with self.assertRaises(StaleTiming):
            line_timeline({"scenes": [{"id": "zz", "lines": []}]}, tl)

    @unittest.skipUnless(ffmpeg_available(), "ffmpeg not installed")
    def test_narrated_sample_renders_with_audio_length_scenes_and_line_clock(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(["narrate", str(self.path), "--lang", "en", "--engine", "mock", "--voice", "tone-220"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("line(s) made", out.getvalue())
        scenes = load(self.path)
        timing = json.loads(timing_path(scenes.base_dir, "en").read_text(encoding="utf-8"))
        result = render(scenes, "en", Path(self.tmp.name) / "n.mp4", RenderOptions(preset="ultrafast", crf=30))
        for t, scene_timing in zip(result.timeline, timing["scenes"]):
            declared = scenes.scenes[t.index].get("duration_s", 0)
            self.assertGreaterEqual(t.duration_s, max(declared, scene_timing["duration_s"] + 0.5) - 1e-9)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = cli.main(["render", str(self.path), "--lang", "en", "-o", self.tmp.name, "--preset", "ultrafast"])
        self.assertEqual(code, 0)
        lines_path = Path(self.tmp.name) / "three-scenes.en.lines.json"
        lines = json.loads(lines_path.read_text(encoding="utf-8"))
        self.assertEqual(len(lines), sum(len(s["lines"]) for s in timing["scenes"]))
        self.assertEqual([ln["start_s"] for ln in lines], sorted(ln["start_s"] for ln in lines))
        # Narration edited after voicing: the render still succeeds, the stale lines file is removed with a warning.
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        doc["scenes"][0]["narration"]["en"] = "Changed after voicing."
        self.path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = cli.main(["render", str(self.path), "--lang", "en", "-o", self.tmp.name, "--preset", "ultrafast"])
        self.assertEqual(code, 0)
        self.assertIn("WARN three-scenes.en.lines.json skipped", err.getvalue())
        self.assertFalse(lines_path.exists())


class VoicesPageTest(unittest.TestCase):
    def test_mock_page_has_a_player_per_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = build_page(mock_candidates(), Path(tmp))
            text = page.read_text(encoding="utf-8")
            results = json.loads((Path(tmp) / "results.json").read_text(encoding="utf-8"))
            files = [s["file"] for r in results for s in r["samples"]]
            self.assertTrue(all((Path(tmp) / f).is_file() for f in files))
        self.assertEqual(text.count("<audio "), 4 * 3)
        self.assertIn("<h2>Vietnamese</h2>", text)
        self.assertTrue(all(r["rtf"] is not None for r in results))

    def test_a_failing_engine_is_reported_not_raised(self):
        from motion_studio.voices_page import Candidate

        def broken():
            raise TTSError("model missing")

        cands = [Candidate(key="bad", title="Broken <engine>", engine="x", voice="v", lang="vi", licence="?",
                           usable="no", make=broken)] + mock_candidates(("vi",))
        with tempfile.TemporaryDirectory() as tmp:
            results = run_candidates(cands, Path(tmp))
        self.assertIn("model missing", results[0].error)
        self.assertEqual(len(results[1].samples), 3)
        html_text = render_page(results)
        self.assertIn("Broken &lt;engine&gt;", html_text)
        self.assertIn("not usable (licence)", html_text)

    def test_failed_build_is_recorded_once_per_share_and_others_still_run(self):
        from motion_studio.voices_page import Candidate

        builds = []

        def broken():
            builds.append(1)
            raise RuntimeError("weights missing")

        cands = [Candidate(key=f"bad{i}", title="x", engine="x", voice="v", lang="vi", licence="?", usable="no",
                           make=broken, share="broken") for i in range(3)] + mock_candidates(("vi",))
        with tempfile.TemporaryDirectory() as tmp:
            results = run_candidates(cands, Path(tmp))
        self.assertEqual(len(builds), 1)
        self.assertTrue(all("weights missing" in r.error for r in results[:3]))
        self.assertTrue(all(len(r.samples) == 3 for r in results[3:]))

    def test_warm_up_is_not_timed(self):
        import time as _time
        from motion_studio.voices_page import Candidate

        class SlowFirst(MockTTS):
            def generate(self, text, voice, lang):
                if self.calls == 0:
                    _time.sleep(0.5)
                return super().generate(text, voice, lang)

        cand = Candidate(key="slow", title="slow", engine="mock", voice="tone-220", lang="en", licence="n/a",
                         usable="check", make=SlowFirst)
        with tempfile.TemporaryDirectory() as tmp:
            res = run_candidates([cand], Path(tmp))[0]
            self.assertFalse(any(p.name.startswith(".warmup") for p in (Path(tmp) / "audio").iterdir()))
        self.assertLess(max(s.seconds for s in res.samples), 0.4)

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy comes with the vieneu extra; CI installs the base package only")
    def test_bad_reference_breaks_only_the_clone(self):
        import types
        from motion_studio.voices_page import default_candidates

        class FakeVieneu:
            sample_rate = 48000

            def __init__(self, mode, device):
                pass

            def list_preset_voices(self):
                return [(n, n) for n in ("Trúc Ly", "Minh Đức", "Thùy Dung")]

            def infer(self, text, voice=None, ref_audio=None, denoise=True, temperature=0.8):
                return [0.1] * 4800

        saved = sys.modules.get("vieneu")
        sys.modules["vieneu"] = types.SimpleNamespace(Vieneu=FakeVieneu)
        try:
            cands = default_candidates(ref_wav=Path("/private/place/too-short.wav"), langs=("vi",))
            with tempfile.TemporaryDirectory() as tmp:
                results = run_candidates(cands, Path(tmp))
                page = render_page(results)
        finally:
            if saved is None:
                sys.modules.pop("vieneu", None)
            else:
                sys.modules["vieneu"] = saved
        self.assertEqual([r.voice for r in results], ["Trúc Ly", "Minh Đức", "Thùy Dung", "clone"])
        self.assertTrue(all(len(r.samples) == 3 and not r.error for r in results[:3]))
        self.assertIn("not found", results[3].error)
        self.assertNotIn("/private/place", page)

    def test_word_error_rate(self):
        self.assertEqual(word_error_rate("Xin chào, các bạn!", "xin chào các bạn"), 0.0)
        self.assertAlmostEqual(word_error_rate("một hai ba bốn", "một ba bốn năm"), 0.5)
        self.assertEqual(word_error_rate("", "anything"), 1.0)

    def test_asr_check_fills_heard_and_wer(self):
        def fake_recogniser(path, lang):
            self.assertTrue(Path(path).is_file())
            return "something else entirely" if lang == "en" else "Xin chào"

        with tempfile.TemporaryDirectory() as tmp:
            build_page(mock_candidates(), Path(tmp), sentences={"vi": ["Xin chào."], "en": ["Hello there."]},
                       recognise=fake_recogniser)
            results = json.loads((Path(tmp) / "results.json").read_text(encoding="utf-8"))
            text = (Path(tmp) / "index.html").read_text(encoding="utf-8")
        by_lang = {r["lang"]: r for r in results}
        self.assertEqual(by_lang["vi"]["mean_wer"], 0.0)
        self.assertGreater(by_lang["en"]["mean_wer"], 0.5)
        self.assertIn("heard: “Xin chào”", text)
        self.assertIn("ASR word error rate", text)

    def test_cli_voices_mock(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with redirect_stdout(out):
                code = cli.main(["voices", "--mock", "--lang", "en", "-o", tmp])
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / "index.html").is_file())


class FakeEngineTest(unittest.TestCase):
    """Exercise the real adapters against stand-in libraries, so CI covers their logic without models."""

    def setUp(self):
        import types

        self.calls = []
        calls = self.calls

        class FakeVieneu:
            sample_rate = 48000

            def __init__(self, mode, device):
                calls.append(("init", mode, device))

            def list_preset_voices(self):
                return [("⭐ Trúc Ly — Nữ · Bắc", "Trúc Ly"), ("Ngọc Huyền — Nữ · Bắc", "Ngọc Huyền"),
                        ("Minh Đức — Nam · Bắc", "Minh Đức")]

            def infer(self, text, voice=None, ref_audio=None, denoise=True, temperature=0.8):
                calls.append(("infer", text, voice, ref_audio))
                return [0.1] * 4800

        self.modules = {"vieneu": types.SimpleNamespace(Vieneu=FakeVieneu)}
        self.saved = {k: sys.modules.get(k) for k in self.modules}
        sys.modules.update(self.modules)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def test_vieneu_presets_and_clone(self):
        from motion_studio.tts.vieneu_tts import VieNeuTTS

        with tempfile.TemporaryDirectory() as tmp:
            ref = Path(tmp) / "ref.wav"
            write_wav(ref, [0.0] * (16000 * 7), 16000)
            tts = VieNeuTTS(device="auto", ref_wav=ref, seed=None)
            self.assertEqual([v.id for v in tts.voices()], ["Trúc Ly", "Minh Đức", "clone"])
            self.assertEqual(tts.voices()[0].label, "Trúc Ly — Nữ · Bắc")
            line = tts.synthesize("Xin chào.", "clone", "vi", Path(tmp) / "a.wav")
            self.assertAlmostEqual(line.duration_s, 0.1)
            self.assertEqual(line.sample_rate, 48000)
            tts.synthesize("Xin chào.", "Minh Đức", "vi", Path(tmp) / "b.wav")
        self.assertEqual(self.calls[0], ("init", "v3turbo", "auto"))
        self.assertEqual(self.calls[1][2:], (None, str(ref.resolve())))
        self.assertEqual(self.calls[2][2:], ("Minh Đức", None))

    def test_clone_engine_needs_a_reference_and_offers_only_the_clone(self):
        from motion_studio.tts.clone import CloneTTS

        with self.assertRaisesRegex(TTSError, "needs ref_wav"):
            CloneTTS()
        with tempfile.TemporaryDirectory() as tmp:
            ref = Path(tmp) / "ref.wav"
            write_wav(ref, [0.0] * (16000 * 7), 16000)
            self.assertEqual([v.id for v in get_adapter("clone", ref_wav=ref, seed=None).voices()], ["clone"])
            self.assertEqual([v.id for v in get_adapter("vieneu", seed=None).voices()], ["Trúc Ly", "Minh Đức"], "blocked presets are not offered")

    def test_engine_errors_become_tts_errors(self):
        from motion_studio.tts.vieneu_tts import VieNeuTTS

        tts = VieNeuTTS(seed=None)
        tts._tts.infer = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("CUDA out of memory"))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(TTSError, "CUDA out of memory"):
                tts.synthesize("Xin chào.", "Minh Đức", "vi", Path(tmp) / "a.wav")

    def test_kokoro_auto_device_is_left_to_the_library(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy not installed (CI); runs in the TTS environment")
        import types

        seen = {}

        class FakePipeline:
            def __init__(self, lang_code, repo_id, device):
                seen["device"], seen["lang"] = device, lang_code

            def __call__(self, text, voice, speed):
                yield None, None, [0.0] * 2400

        saved = sys.modules.get("kokoro")
        sys.modules["kokoro"] = types.SimpleNamespace(KPipeline=FakePipeline)
        try:
            from motion_studio.tts.kokoro_tts import KokoroTTS

            with tempfile.TemporaryDirectory() as tmp:
                line = KokoroTTS(device="auto").synthesize("Hello.", "bf_emma", "en", Path(tmp) / "a.wav")
        finally:
            if saved is None:
                sys.modules.pop("kokoro", None)
            else:
                sys.modules["kokoro"] = saved
        self.assertEqual((seen["device"], seen["lang"]), (None, "b"))
        self.assertAlmostEqual(line.duration_s, 0.1)


if __name__ == "__main__":
    unittest.main()
