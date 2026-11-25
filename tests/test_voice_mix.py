"""Voice per scene or speaker, the voice_mix policy, and the English clone engine (clone-en).

Everything here runs without a model: the mock engine, a fake Chatterbox
backend in a real worker process, and stand-in adapters. The one real-model
test runs only when the Chatterbox venv and weights exist and
MOTION_STUDIO_REAL_TTS=1 is set.
"""

import copy
import importlib.machinery
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motion_studio import cli  # noqa: E402
from motion_studio.narrate import MIX, check_timing, narrate, resample, save_scenes, split_lines, timing_path  # noqa: E402
from motion_studio.schema import ScenesError, from_dict, load, validate  # noqa: E402
from motion_studio.tts import TTSError, get_adapter, read_wav, write_wav  # noqa: E402
from motion_studio.tts.chatterbox_tts import MODELS, ChatterboxTTS, default_models_dir, default_venv  # noqa: E402
from motion_studio.tts.mock import MockTTS  # noqa: E402
from motion_studio.voice_mix import VoiceChoice, mix_errors, plan_voices, scene_speakers  # noqa: E402
from motion_studio.voices_page import Candidate, default_candidates, run_candidates  # noqa: E402

SAMPLE = ROOT / "samples" / "voice-mix" / "scenes.json"
FAKE_BACKEND = '''
import array, math


class Fake:
    def __init__(self, device, models_dir, ref_wav):
        print("library noise on stdout")  # must not break the protocol
        self.ref = ref_wav

    def generate(self, voice, text, *, seed, temperature):
        print("more noise")
        if "Boom" in text:
            raise RuntimeError(f"cannot read {self.ref}")
        n = 2400 if voice == "clone" else 4800
        return [0.25 * math.sin(i / 10) for i in range(n)], 24000

    def close(self):
        pass


class Broken:
    def __init__(self, device, models_dir, ref_wav):
        raise RuntimeError(f"weights missing next to {ref_wav}")
'''


def doc(**extra):
    """A two-language, four-scene document with mock voices (title cards, so no audio is needed)."""
    base = {
        "version": 1,
        "title": {"en": "Mix", "vi": "Trộn"},
        "languages": ["en", "vi"],
        "speakers": {
            "owner": {"en": {"engine": "mock", "voice": "tone-220"}, "vi": {"engine": "mock", "voice": "tone-220"}},
            "guest": {"en": {"engine": "mock", "voice": "tone-330"}},
        },
        "scenes": [
            {"id": f"s{n}", "duration_s": 1, "narration": {"en": f"Line {n} here. Second line.", "vi": f"Câu {n} đây."},
             "visual": {"kind": "title", "text": {"en": "T", "vi": "T"}}}
            for n in range(4)
        ],
    }
    base["scenes"][2]["chapter"] = {"en": "Two", "vi": "Hai"}
    base.update(extra)
    return base


def write_ref(path: Path, seconds: float, rate: int = 16000) -> Path:
    import math

    write_wav(path, [0.3 * math.sin(2 * math.pi * 180 * i / rate) for i in range(int(seconds * rate))], rate)
    return path


class PlanVoicesTest(unittest.TestCase):
    def test_narrator_policy_with_a_second_speaker(self):
        d = doc(voice_mix={"policy": "narrator", "narrator": "owner"})
        d["scenes"][1]["speaker"] = "guest"
        choices = plan_voices(d, "en")
        self.assertEqual([c.voice for c in choices], ["tone-220", "tone-330", "tone-220", "tone-220"])
        self.assertEqual([c.speaker for c in choices], ["owner", "guest", "owner", "owner"])
        self.assertEqual([c.source for c in choices], ["voice_mix", "speaker", "voice_mix", "voice_mix"])

    def test_alternate_chapters_turns_at_each_chapter(self):
        d = doc(voice_mix={"policy": "alternate-chapters", "speakers": ["owner", "guest"]})
        d["scenes"][3]["chapter"] = {"en": "Three", "vi": "Ba"}
        self.assertEqual(scene_speakers(d), ["owner", "owner", "guest", "owner"])
        d["scenes"][1]["speaker"] = "guest"  # an explicit speaker does not move the rotation
        self.assertEqual(scene_speakers(d), ["owner", "guest", "guest", "owner"])

    def test_scene_voice_beats_speaker_and_default_fills_gaps(self):
        d = doc(voice_mix={"policy": "narrator", "narrator": "guest"})
        d["scenes"][0]["voice"] = {"vi": {"engine": "mock", "voice": "tone-330"}}
        # guest has no vi voice: scene 0 uses its own, the others fall back to the default
        choices = plan_voices(d, "vi", ("mock", "tone-220"))
        self.assertEqual(choices[0], VoiceChoice("mock", "tone-330", "guest", "scene"))
        self.assertEqual(choices[1], VoiceChoice("mock", "tone-220", "guest", "default"))

    def test_no_voice_names_every_scene(self):
        with self.assertRaisesRegex(ValueError, r"s0, s1, s2, s3.*--engine and --voice"):
            plan_voices(doc(), "en")
        d = doc(voice_mix={"policy": "narrator", "narrator": "guest"})
        with self.assertRaisesRegex(ValueError, "speaker 'guest' has no vi voice"):
            plan_voices(d, "vi")

    def test_without_mix_fields_the_default_is_used(self):
        d = doc()
        del d["speakers"]
        self.assertEqual({c.source for c in plan_voices(d, "en", ("mock", "tone-220"))}, {"default"})


class SchemaTest(unittest.TestCase):
    def test_sample_is_valid_and_mixes_voices(self):
        scenes = load(SAMPLE)
        en = plan_voices(scenes.doc, "en")
        self.assertEqual([(c.engine, c.voice) for c in en],
                         [("clone-en", "clone"), ("kokoro", "af_heart"), ("clone-en", "clone"), ("kokoro", "bm_george")])
        vi = plan_voices(scenes.doc, "vi")
        self.assertEqual([c.speaker for c in vi], ["owner", "guest", "owner", "owner"])
        self.assertEqual(vi[3], VoiceChoice("vieneu", "clone", "owner", "voice_mix"))

    def test_cross_field_errors(self):
        d = doc(voice_mix={"policy": "narrator", "narrator": "nobody", "speakers": ["owner", "guest"]})
        d["scenes"][0]["speaker"] = "ghost"
        d["speakers"]["guest"]["fr"] = {"engine": "mock", "voice": "x"}
        d["scenes"][1]["voice"] = {"en": {"engine": "espeak", "voice": "x"}}
        errs = validate(d)
        joined = "\n".join(errs)
        self.assertIn("/scenes/0/speaker: unknown speaker 'ghost'", joined)
        self.assertIn("/voice_mix: unknown speaker 'nobody'", joined)
        self.assertIn("/voice_mix/speakers: only the alternate-chapters policy", joined)
        self.assertIn("/speakers/guest: language(s) fr not listed", joined)
        self.assertIn("/scenes/1/voice/en/engine: unknown TTS engine 'espeak'", joined)

    def test_policy_needs_its_field(self):
        self.assertIn("/voice_mix: the narrator policy needs narrator (a speaker id)",
                      validate(doc(voice_mix={"policy": "narrator"})))
        self.assertIn("/voice_mix: the alternate-chapters policy needs speakers (two or more speaker ids)",
                      validate(doc(voice_mix={"policy": "alternate-chapters"})))
        self.assertEqual(mix_errors(doc(voice_mix={"policy": "alternate-chapters", "speakers": ["owner", "guest"]})), [])

    def test_shapes_are_checked_by_the_schema(self):
        bad = [
            doc(voice_mix={"policy": "random"}),
            doc(voice_mix={"policy": "alternate-chapters", "speakers": ["owner"]}),
            doc(speakers={"Owner": {"en": {"engine": "mock", "voice": "tone-220"}}}),
            doc(speakers={"owner": {"en": {"engine": "mock"}}}),
            doc(speakers={"owner": {"en": {"engine": "mock", "voice": "tone-220", "speed": 2}}}),
        ]
        for d in bad:
            self.assertTrue(validate(d), d)


class ResampleTest(unittest.TestCase):
    def test_upsampling_keeps_length_ratio_and_end_points(self):
        import array

        src = array.array("h", [0, 1000, 2000, 3000])
        out = resample(src, 24000, 48000)
        self.assertEqual(len(out), 8)
        self.assertEqual((out[0], out[-1]), (0, 3000))
        self.assertEqual(list(resample(src, 24000, 24000)), list(src))


class Mock48(MockTTS):
    engine = "mock48"

    def generate(self, text, voice, lang):
        samples, rate = super().generate(text, voice, lang)
        return resample(samples, rate, 48000), 48000


class NarrateMixTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "mix" / "scenes.json"
        self.path.parent.mkdir()
        d = doc(voice_mix={"policy": "narrator", "narrator": "owner"})
        d["scenes"][1]["speaker"] = "guest"
        self.path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_scene_gets_its_voice_and_the_timing_records_it(self):
        scenes = load(self.path)
        choices = plan_voices(scenes.doc, "en")
        result = narrate(scenes, "en", {"mock": MockTTS()}, choices=choices)
        self.assertEqual((result.engine, result.voice), ("mock", MIX))
        self.assertEqual(len(result.scenes[0].lines), 2)
        self.assertEqual([s.voice for s in result.scenes], ["tone-220", "tone-330", "tone-220", "tone-220"])
        save_scenes(scenes, self.path, "en")
        timing = json.loads(timing_path(scenes.base_dir, "en").read_text(encoding="utf-8"))
        self.assertEqual(timing["voice"], MIX)
        self.assertEqual([s["speaker"] for s in timing["scenes"]], ["owner", "guest", "owner", "owner"])
        check_timing(timing, load(self.path), "en")
        again = narrate(load(self.path), "en", {"mock": MockTTS()}, choices=choices)
        self.assertEqual(again.synthesized, 0, "unchanged voices reuse every line")
        swapped = [VoiceChoice("mock", "tone-330")] + choices[1:]
        third = narrate(load(self.path), "en", {"mock": MockTTS()}, choices=swapped)
        self.assertEqual(third.synthesized, len(split_lines("Line 0 here. Second line.")), "only the re-voiced scene is made again")

    def test_engines_with_different_rates_are_joined_at_the_highest(self):
        scenes = load(self.path)
        choices = [VoiceChoice("mock", "tone-220"), VoiceChoice("mock48", "tone-330")] * 2
        result = narrate(scenes, "en", {"mock": MockTTS(), "mock48": Mock48()}, choices=choices, pause_s=0.2)
        self.assertEqual(result.sample_rate, 48000)
        self.assertEqual(result.engine, MIX)
        for s in result.scenes:
            data, rate = read_wav(scenes.base_dir / s.file)
            self.assertEqual(rate, 48000)
            self.assertAlmostEqual(len(data) / rate, s.duration_s, places=6)
            self.assertAlmostEqual(s.lines[1].start_s - s.lines[0].end_s, 0.2, places=3)

    def test_bad_arguments(self):
        scenes = load(self.path)
        with self.assertRaisesRegex(ValueError, "no adapter for engine"):
            narrate(scenes, "en", {"mock": MockTTS()}, choices=[VoiceChoice("kokoro", "af_heart")] * 4)
        with self.assertRaisesRegex(ValueError, "4 scene"):
            narrate(scenes, "en", {"mock": MockTTS()}, choices=[VoiceChoice("mock", "tone-220")])
        with self.assertRaisesRegex(ValueError, "pass one adapter and a voice"):
            narrate(scenes, "en", MockTTS())

    def test_cli_narrates_a_mix_without_engine_flags(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["narrate", str(self.path), "--lang", "en"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("mock:tone-220, mock:tone-330", out.getvalue())
        self.assertEqual(load(self.path).scenes[1]["audio"]["en"], "audio/tts/s1.en.wav")

    def test_cli_needs_a_default_when_a_scene_has_no_voice(self):
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.main(["narrate", str(self.path), "--lang", "vi"])  # guest has no vi voice
        self.assertEqual(code, 2)
        self.assertIn("speaker 'guest' has no vi voice", err.getvalue())
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["narrate", str(self.path), "--lang", "vi", "--engine", "mock", "--voice", "tone-330"]), 0)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["narrate", str(self.path), "--lang", "vi", "--engine", "mock"]), 2)

    def test_engine_options_give_each_clone_its_reference(self):
        args = cli.build_parser().parse_args(["narrate", "x", "--lang", "en", "--ref-wav", "/a/vi.wav", "--ref-wav-en", "/a/en.wav"])
        self.assertEqual(cli.engine_options("clone-en", {"clone"}, args)["ref_wav"], Path("/a/en.wav"))
        self.assertEqual(cli.engine_options("vieneu", {"clone", "Trúc Ly"}, args)["ref_wav"], Path("/a/vi.wav"))
        self.assertNotIn("ref_wav", cli.engine_options("vieneu", {"Trúc Ly"}, args))
        self.assertNotIn("ref_wav", cli.engine_options("kokoro", {"af_heart"}, args))
        only_vi = cli.build_parser().parse_args(["narrate", "x", "--lang", "en", "--ref-wav", "/a/vi.wav"])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(cli.REF_EN_ENV, None)
            self.assertEqual(cli.engine_options("clone-en", {"clone"}, only_vi)["ref_wav"], Path("/a/vi.wav"))


class CloneEnWorkerTest(unittest.TestCase):
    """The real adapter and worker process, with a fake backend instead of Chatterbox."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "fake_chatterbox_backend.py").write_text(FAKE_BACKEND, encoding="utf-8")
        self.private = self.tmp / "private-folder"
        self.private.mkdir()
        self.ref = write_ref(self.private / "me.wav", 7)
        self.env = mock.patch.dict(os.environ, {"PYTHONPATH": str(self.tmp)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def adapter(self, backend="fake_chatterbox_backend:Fake"):
        return ChatterboxTTS(ref_wav=self.ref, python=sys.executable, backend=backend, models_dir=self.tmp / "models")

    def test_worker_makes_lines_and_survives_a_failed_one(self):
        tts = self.adapter()
        try:
            self.assertEqual([v.id for v in tts.voices()], ["clone", "clone-standard"])
            self.assertEqual({v.lang for v in tts.voices()}, {"en"})
            a = tts.synthesize("Hello there.", "clone", "en", self.tmp / "a.wav")
            b = tts.synthesize("Hello there.", "clone-standard", "en", self.tmp / "b.wav")
            self.assertEqual((a.sample_rate, round(a.duration_s, 3), round(b.duration_s, 3)), (24000, 0.1, 0.2))
            with self.assertRaises(TTSError) as caught:
                tts.synthesize("Boom goes the line.", "clone", "en", self.tmp / "c.wav")
            self.assertIn("cannot read", str(caught.exception))
            self.assertNotIn(str(self.private), str(caught.exception))
            tts.synthesize("Still alive.", "clone", "en", self.tmp / "d.wav")
            with self.assertRaisesRegex(TTSError, "no en voice 'af_heart'"):
                tts.synthesize("Hi.", "af_heart", "en", self.tmp / "e.wav")
            worker, scratch = tts._worker, tts._tmp
            tag = tts.cache_tag()
        finally:
            tts.close()
        self.assertIsNotNone(worker.poll(), "close stops the worker")
        self.assertFalse(scratch.exists(), "close removes the worker's temp folder")
        self.assertNotIn(str(self.private), tag)
        self.assertIn("ref=", tag)

    def test_reference_settings_change_the_cache_tag(self):
        other = write_ref(self.private / "other.wav", 8)
        first = self.adapter()
        second = ChatterboxTTS(ref_wav=other, python=sys.executable, backend="fake_chatterbox_backend:Fake")
        try:
            self.assertNotEqual(first.cache_tag(), second.cache_tag())
        finally:
            first.close()
            second.close()

    def test_a_backend_that_fails_to_load_is_one_clear_error(self):
        with self.assertRaises(TTSError) as caught:
            self.adapter("fake_chatterbox_backend:Broken")
        self.assertIn("failed to start", str(caught.exception))
        self.assertIn("weights missing", str(caught.exception))
        self.assertNotIn(str(self.private), str(caught.exception))

    def test_reference_and_setup_checks_come_first(self):
        with self.assertRaisesRegex(TTSError, "needs ref_wav"):
            ChatterboxTTS()
        short = write_ref(self.private / "short.wav", 4)
        with self.assertRaises(TTSError) as caught:
            ChatterboxTTS(ref_wav=short, python=sys.executable)
        self.assertIn("about 10 s", str(caught.exception))
        self.assertNotIn(str(self.private), str(caught.exception))
        with self.assertRaisesRegex(TTSError, "clone-en-setup"):
            ChatterboxTTS(ref_wav=self.ref, python=self.tmp / "no-venv" / "bin" / "python")

    def test_in_process_backend_when_chatterbox_is_importable(self):
        fake = types.ModuleType("chatterbox")
        fake.__spec__ = importlib.machinery.ModuleSpec("chatterbox", None)
        sys.path.insert(0, str(self.tmp))
        try:
            with mock.patch.dict(sys.modules, {"chatterbox": fake}), redirect_stdout(io.StringIO()):
                tts = get_adapter("clone-en", ref_wav=self.ref, backend="fake_chatterbox_backend:Fake")
                self.assertIsNone(tts._worker)
                line = tts.synthesize("Hello.", "clone", "en", self.tmp / "a.wav")
                with self.assertRaises(TTSError) as caught:
                    tts.synthesize("Boom.", "clone", "en", self.tmp / "b.wav")
                tts.close()
        finally:
            sys.path.remove(str(self.tmp))
            sys.modules.pop("fake_chatterbox_backend", None)
        self.assertAlmostEqual(line.duration_s, 0.1)
        self.assertNotIn(str(self.private), str(caught.exception))

    def test_models_pin_a_revision_and_live_in_the_studio_cache(self):
        for spec in MODELS.values():
            self.assertRegex(spec.revision, r"^[0-9a-f]{40}$")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MOTION_STUDIO_MODELS_DIR", None)
            os.environ.pop("MOTION_STUDIO_CLONE_EN_VENV", None)
            self.assertEqual(default_models_dir().parts[-2:], ("nghiep-motion-studio", "hf"))
            self.assertEqual(default_venv().parts[-2:], ("nghiep-motion-studio", "clone-en-venv"))


class VoicesPageCloneTest(unittest.TestCase):
    def test_english_clones_join_the_page_only_with_a_reference(self):
        without = default_candidates(langs=("en",))
        self.assertNotIn("clone-en", {c.engine for c in without})
        cands = default_candidates(langs=("en",), ref_wav=Path("/x/vi.wav"))
        clones = [c for c in cands if c.engine == "clone-en"]
        self.assertEqual([c.voice for c in clones], ["clone", "clone-standard"])
        self.assertEqual({c.share for c in clones}, {"clone-en"})
        self.assertTrue(all(c.lang == "en" and c.usable == "yes" and "MIT" in c.licence for c in clones))

    def test_an_adapter_is_closed_after_its_last_candidate(self):
        events = []

        class Tracked(MockTTS):
            def __init__(self, name):
                super().__init__()
                self.name = name
                events.append(f"open {name}")

            def close(self):
                events.append(f"close {self.name}")

        cands = [
            Candidate(key=f"{share}{n}", title="t", engine="mock", voice="tone-220", lang="en", licence="n/a",
                      usable="check", make=lambda share=share: Tracked(share), share=share)
            for share in ("a", "b") for n in range(2)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_candidates(cands, Path(tmp), sentences={"en": ["One line."]})
        self.assertEqual(events, ["open a", "close a", "open b", "close b"], "one model in memory at a time")


@unittest.skipUnless(os.environ.get("MOTION_STUDIO_REAL_TTS") == "1", "set MOTION_STUDIO_REAL_TTS=1 to run the real Chatterbox model")
class RealCloneEnTest(unittest.TestCase):
    def test_turbo_speaks_in_the_worker(self):
        python = default_venv() / "bin" / "python"
        weights = default_models_dir() / "models--ResembleAI--chatterbox-turbo"
        if not python.is_file() or not weights.is_dir():
            self.skipTest("run `just clone-en-setup` and fetch the weights once first")
        with tempfile.TemporaryDirectory() as tmp:
            ref_env = os.environ.get("MOTION_STUDIO_REF_WAV_EN") or os.environ.get("MOTION_STUDIO_REF_WAV")
            ref = Path(ref_env) if ref_env else write_ref(Path(tmp) / "ref.wav", 8)
            tts = get_adapter("clone-en", ref_wav=ref)
            try:
                line = tts.synthesize("Retrieval lets a model look things up first.", "clone", "en", Path(tmp) / "a.wav")
            finally:
                tts.close()
        self.assertEqual(line.sample_rate, 24000)
        self.assertGreater(line.duration_s, 1.0)


if __name__ == "__main__":
    unittest.main()
