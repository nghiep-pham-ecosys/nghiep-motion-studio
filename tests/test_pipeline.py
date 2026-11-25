"""The one-command pipeline (motion-studio video): order, stop on failure, stand-ins and casting presets.

Fast: a fake runner records the step commands; only the script and scenes
steps run for real (copy, cast, validate). One test runs the real voice and
render steps with the mock TTS engine when ffmpeg is installed.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motion_studio import cli  # noqa: E402
from motion_studio.gate import narration_hash  # noqa: E402
from motion_studio.pipeline import (  # noqa: E402
    STEPS, Pipeline, PipelineError, cast_scenes, load_topic, load_voices, resolve_cast, script_markdown,
)
from motion_studio.render import ffmpeg_available  # noqa: E402
from motion_studio.tts.mock import MockTTS  # noqa: E402

REQUIRE_FFMPEG = os.environ.get("MOTION_STUDIO_REQUIRE_FFMPEG") == "1"
VOICES = {
    "version": 1,
    "narrator": {
        "en": {"engine": "mock", "voice": "tone-220", "ref_env": "TEST_REF_EN"},
        "vi": {"engine": "clone", "voice": "clone", "ref_env": "TEST_REF_VI"},
    },
    "presets": {
        "male-male": {
            "guest": {"en": [{"engine": "mock", "voice": "tone-330"}], "vi": [{"engine": "vieneu", "voice": "Minh Đức"}]},
            "stand_in": {"en": {"engine": "mock", "voice": "tone-220"}, "vi": {"engine": "vieneu", "voice": "Hải Đăng"}},
        },
        "male-female": {
            "guest": {"en": [{"engine": "kokoro", "voice": "af_heart"}, {"engine": "kokoro", "voice": "bf_emma"}],
                      "vi": [{"engine": "vieneu", "voice": "Trúc Ly"}]},
            "stand_in": {"en": {"engine": "kokoro", "voice": "am_michael"}, "vi": {"engine": "vieneu", "voice": "Minh Đức"}},
        },
    },
}


def scenes_doc(lang="en", n=3, text="This is one short line. It has two sentences."):
    return {
        "version": 1,
        "title": {lang: "A test video"},
        "languages": [lang],
        "output": {"width": 320, "height": 180, "fps": 10},
        "speakers": {"narrator": {lang: {"engine": "mock", "voice": "tone-220"}},
                     "guest": {lang: {"engine": "mock", "voice": "tone-330"}}},
        "voice_mix": {"policy": "narrator", "narrator": "narrator"},
        "scenes": [
            {
                "id": f"s{i}",
                **({"chapter": {lang: f"Part {i}"}} if i % 2 == 0 else {}),
                **({"speaker": "guest"} if i == 1 else {}),
                "narration": {lang: f"{text} Scene {i}."},
                "duration_s": 1,
                "visual": {"kind": "image", "src": "img/dot.svg"} if i == 0 else {"kind": "title", "text": {lang: f"S{i}"}},
            }
            for i in range(n)
        ],
    }


class Fixture(unittest.TestCase):
    """A topic folder with a scenes file, its fact check, an image and a voices config; a private work dir."""

    lang = "en"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ms-pipe-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.topic_dir = self.tmp / "topic"
        (self.topic_dir / "img").mkdir(parents=True)
        (self.topic_dir / "img" / "dot.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><circle cx="5" cy="5" r="4"/></svg>')
        self.doc = scenes_doc(self.lang)
        self.write_scenes(self.doc)
        (self.tmp / "voices.json").write_text(json.dumps(VOICES, ensure_ascii=False), encoding="utf-8")
        self.work = self.tmp / "work"
        env = {"MOTION_STUDIO_WORK_DIR": str(self.work), "MOTION_STUDIO_WORK_CAP_GB": "5"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.topic = self.write_topic()
        self.tts = self.tmp / "venv" / "bin" / "python"
        self.tts.parent.mkdir(parents=True)
        self.tts.write_text("")

    def write_scenes(self, doc):
        (self.topic_dir / "scenes.test.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        claims = "\n".join(f"- [x] claim {i} - source: https://example.com/{i}" for i in range(3))
        (self.topic_dir / "factcheck.test.md").write_text(
            f"Reviewed-by: test\nDate: 2026-11-25\nNarration: {narration_hash(doc)[:12]}\n\n{claims}\n", encoding="utf-8")

    def write_topic(self, **extra):
        topic = {"version": 1, "job": "demo", "scenes": "scenes.test.json", "factcheck": "factcheck.test.md",
                 "cast": {self.lang: {"preset": "male-male"}}, "gate": {"min_s": 1, "max_s": 600}, **extra}
        path = self.topic_dir / "demo.topic.json"
        path.write_text(json.dumps(topic), encoding="utf-8")
        return path

    def pipeline(self, environ=None, codes=None, **kwargs):
        calls = []
        codes = codes or {}

        def runner(argv, env):
            calls.append(list(argv))
            return codes.get(argv[3], 0)

        env = {"MOTION_STUDIO_WORK_DIR": str(self.work), "TEST_REF_EN": str(self.topic_dir / "img" / "dot.svg")}
        env.update(environ or {})
        out = []
        pipe = Pipeline(load_topic(self.topic), runner=runner, environ=env, tts_python=self.tts, python="py",
                        out=out.append, err=out.append, **kwargs)
        pipe._length_problem = lambda scenes_file, lang: None  # no audio exists with a fake runner
        with mock.patch("motion_studio.pipeline.Pipeline.preflight", return_value=[]):
            report = pipe.run()
        return report, calls, out


class OrderTest(Fixture):
    def test_steps_run_in_order_with_the_right_python(self):
        report, calls, _ = self.pipeline()
        self.assertTrue(report.ok)
        # no generated visuals: the visuals step is recorded as passed without a command
        self.assertEqual([c[3] for c in calls], ["narrate", "render", "subtitles", "gate", "shorts"])
        self.assertEqual([s.step for s in report.steps], list(STEPS))
        by_cmd = {c[3]: c for c in calls}
        self.assertEqual(by_cmd["narrate"][0], str(self.tts))
        self.assertEqual(by_cmd["subtitles"][0], str(self.tts))
        self.assertEqual(by_cmd["render"][0], "py")
        self.assertIn("--check-drift", by_cmd["subtitles"])
        self.assertEqual(by_cmd["gate"][-4:], ["--min-s", "1", "--max-s", "600"])
        self.assertEqual(by_cmd["shorts"][-2:], ["--count", "3"])
        job = self.work / "demo"
        self.assertEqual(by_cmd["render"][4], str(job / "scenes.json"))
        self.assertEqual(by_cmd["render"][by_cmd["render"].index("--jobs") + 1], "1")  # one encode at a time
        self.assertTrue((job / ".motion-studio-job").is_file())
        saved = json.loads((job / "demo.video.json").read_text())
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["outputs"]["en"]["video"], str(job / "demo.en.mp4"))

    def test_generated_visuals_get_their_own_step_after_the_voice(self):
        self.doc["scenes"][2]["visual"] = {"kind": "diagram", "nodes": [{"id": "a", "label": "A", "col": 0, "row": 0}]}
        self.write_scenes(self.doc)
        _, calls, _ = self.pipeline()
        self.assertEqual([c[3] for c in calls], ["narrate", "visuals", "render", "subtitles", "gate", "shorts"])

    def test_from_and_until_select_steps(self):
        _, calls, _ = self.pipeline(from_step="render", until_step="gate")
        self.assertEqual([c[3] for c in calls], ["render", "subtitles", "gate"])
        with self.assertRaises(PipelineError):
            self.pipeline(from_step="gate", until_step="voice")

    def test_long_lines_are_reported_by_the_script_step(self):
        self.doc["scenes"][2]["narration"]["en"] = ("This sentence goes on and on, far past what two rows of a subtitle "
                                                   "can hold, so the renderer would have to split it somewhere.")
        self.write_scenes(self.doc)
        report, _, out = self.pipeline(until_step="scenes")
        self.assertTrue(report.ok)
        self.assertTrue(any(line.startswith("WARN 1 en narration line(s)") for line in out))

    def test_job_holds_scenes_files_factcheck_and_script(self):
        self.pipeline(until_step="scenes")
        job = self.work / "demo"
        self.assertTrue((job / "img" / "dot.svg").is_file())
        self.assertTrue((job / "factcheck.md").is_file())
        script = (job / "script.md").read_text()
        self.assertIn("**Guest** (s1)", script)
        self.assertIn("## Part 0", script)
        self.assertTrue((self.topic_dir / "scenes.test.json").read_text().find('"audio"') < 0)  # the source is untouched


class StopTest(Fixture):
    def test_first_failing_step_stops_the_run(self):
        report, calls, out = self.pipeline(codes={"render": 3})
        self.assertFalse(report.ok)
        self.assertEqual(report.failed_step, "render")
        self.assertEqual([c[3] for c in calls], ["narrate", "render"])
        self.assertEqual(report.steps[-1].exit_code, 3)
        self.assertTrue(any("stopped at step render" in line and "--from render" in line for line in out))
        saved = json.loads((self.work / "demo" / "demo.video.json").read_text())
        self.assertEqual(saved["failed_step"], "render")

    def test_stale_factcheck_stops_before_any_command(self):
        (self.topic_dir / "factcheck.test.md").write_text("Reviewed-by: x\nDate: 2026-11-25\nNarration: 000000000000\n")
        report, calls, out = self.pipeline()
        self.assertEqual(report.failed_step, "scenes")
        self.assertEqual(calls, [])
        self.assertTrue(any(narration_hash(self.doc)[:12] in line for line in out))

    def test_factcheck_is_not_needed_when_the_gate_does_not_run(self):
        (self.topic_dir / "factcheck.test.md").write_text("draft\n")
        report, calls, _ = self.pipeline(until_step="render")
        self.assertTrue(report.ok)
        self.assertEqual(calls[-1][3], "render")

    def test_files_outside_the_scenes_folder_are_refused(self):
        self.doc["scenes"][0]["visual"]["src"] = "../outside.svg"
        (self.tmp / "outside.svg").write_text("<svg/>")
        self.write_scenes(self.doc)
        report, calls, out = self.pipeline()
        self.assertEqual(report.failed_step, "scenes")
        self.assertTrue(any("outside the folder" in line for line in out))

    def test_too_short_narration_stops_before_render(self):
        self.write_topic(gate={"min_s": 600, "max_s": 1200})
        calls = []
        pipe = Pipeline(load_topic(self.topic), runner=lambda argv, env: calls.append(argv) or 0,
                        environ={"TEST_REF_EN": str(self.topic_dir / "img" / "dot.svg")}, tts_python=self.tts,
                        out=lambda _: None, err=lambda _: None)
        with mock.patch("motion_studio.pipeline.Pipeline.preflight", return_value=[]), \
                mock.patch("motion_studio.pipeline.planned_seconds", return_value=312.0):
            # the fake narrate wrote no audio, so give every scene some
            original = pipe._length_problem

            def with_audio(scenes_file, lang):
                doc = json.loads(scenes_file.read_text())
                for s in doc["scenes"]:
                    s.setdefault("audio", {})[lang] = "img/dot.svg"
                scenes_file.write_text(json.dumps(doc))
                return original(scenes_file, lang)

            pipe._length_problem = with_audio
            report = pipe.run()
        self.assertEqual(report.failed_step, "render")
        self.assertIn("312.0 s, under the gate's 600 s", report.steps[-1].summary)
        self.assertEqual([c[3] for c in calls], ["narrate"])


class CastTest(Fixture):
    def test_missing_reference_stops_with_a_clear_message(self):
        with self.assertRaises(PipelineError) as ctx:
            self.pipeline(environ={"TEST_REF_EN": ""})
        msg = str(ctx.exception)
        self.assertIn("TEST_REF_EN is not set", msg)
        self.assertIn("--stand-in", msg)
        self.assertFalse((self.work / "demo").exists())

    def test_stand_in_reads_the_narrator_and_is_written_down(self):
        report, calls, out = self.pipeline(environ={"TEST_REF_EN": ""}, stand_in=True)
        self.assertTrue(report.ok)
        cast = report.cast["en"]
        self.assertTrue(cast["stand_in"])
        self.assertEqual(cast["narrator"], {"engine": "mock", "voice": "tone-220"})
        render = next(c for c in calls if c[3] == "render")
        comment = render[render.index("--metadata") + 1]
        self.assertTrue(comment.startswith("comment=stand-in narrator: mock tone-220"))
        self.assertIn("TEST_REF_EN is not set", comment)
        self.assertTrue(any("STAND-IN" in line for line in out))
        job_doc = json.loads((self.work / "demo" / "scenes.json").read_text())
        self.assertEqual(job_doc["speakers"]["narrator"]["en"], {"engine": "mock", "voice": "tone-220"})

    def test_topic_file_can_allow_the_stand_in(self):
        self.write_topic(stand_in=True)
        report, _, _ = self.pipeline(environ={"TEST_REF_EN": ""})
        self.assertTrue(report.cast["en"]["stand_in"])

    def test_owner_clone_when_the_reference_is_set(self):
        report, calls, _ = self.pipeline()
        self.assertFalse(report.cast["en"]["stand_in"])
        render = next(c for c in calls if c[3] == "render")
        self.assertIn("comment=narrator: owner's clone (mock tone-220)", render)

    def test_a_reference_path_that_does_not_exist_is_an_error(self):
        with self.assertRaises(PipelineError) as ctx:
            self.pipeline(environ={"TEST_REF_EN": str(self.tmp / "nope.wav")}, stand_in=True)
        self.assertIn("does not exist", str(ctx.exception))
        self.assertNotIn(str(self.tmp), str(ctx.exception))  # names the file, not its folder


class PresetTest(unittest.TestCase):
    def test_default_guest_is_the_first_of_the_preset(self):
        cast = resolve_cast(VOICES, {"preset": "male-female"}, "en", {"TEST_REF_EN": __file__}, False)
        self.assertEqual(cast.guest, {"engine": "kokoro", "voice": "af_heart"})
        cast = resolve_cast(VOICES, {"preset": "male-female", "guest": "bf_emma"}, "en", {"TEST_REF_EN": __file__}, False)
        self.assertEqual(cast.guest["voice"], "bf_emma")

    def test_unknown_guest_or_preset_is_named(self):
        with self.assertRaisesRegex(PipelineError, "'Thùy Dung' is not in preset male-male"):
            resolve_cast(VOICES, {"preset": "male-male", "guest": "Thùy Dung"}, "vi", {"TEST_REF_VI": __file__}, False)
        with self.assertRaisesRegex(PipelineError, "unknown casting preset 'female-female'"):
            resolve_cast(VOICES, {"preset": "female-female"}, "en", {}, True)

    def test_stand_in_per_preset(self):
        mm = resolve_cast(VOICES, {"preset": "male-male"}, "vi", {}, True)
        mf = resolve_cast(VOICES, {"preset": "male-female"}, "vi", {}, True)
        self.assertEqual(mm.narrator["voice"], "Hải Đăng")
        self.assertEqual(mf.narrator["voice"], "Minh Đức")
        self.assertNotEqual(mm.narrator, mm.guest)

    def test_vietnamese_clone_shares_the_vieneu_model_with_the_guest(self):
        cast = resolve_cast(VOICES, {"preset": "male-male"}, "vi", {"TEST_REF_VI": __file__}, False)
        self.assertEqual(cast.narrator, {"engine": "vieneu", "voice": "clone"})
        self.assertEqual(cast.owner, {"engine": "clone", "voice": "clone"})

    def test_repo_voices_config_has_both_presets(self):
        voices = load_voices(ROOT / "voices.json")
        self.assertEqual(sorted(voices["presets"]), ["male-female", "male-male"])
        self.assertEqual(voices["narrator"]["en"]["voice"], "clone-standard")
        self.assertEqual(voices["narrator"]["en"]["ref_env"], cli.REF_EN_ENV)
        self.assertEqual(voices["narrator"]["vi"]["ref_env"], cli.REF_ENV)
        mm, mf = voices["presets"]["male-male"], voices["presets"]["male-female"]
        self.assertEqual(mm["guest"]["en"][0]["voice"], "am_michael")
        self.assertEqual(mm["guest"]["vi"][0]["voice"], "Minh Đức")
        self.assertEqual([v["voice"] for v in mf["guest"]["en"]], ["af_heart", "bf_emma"])
        self.assertEqual([v["voice"] for v in mf["guest"]["vi"]], ["Trúc Ly", "Thùy Dung"])

    def test_a_stand_in_that_is_also_a_guest_is_refused(self):
        bad = json.loads(json.dumps(VOICES))
        bad["presets"]["male-male"]["stand_in"]["en"] = {"engine": "mock", "voice": "tone-330"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "voices.json"
            path.write_text(json.dumps(bad))
            with self.assertRaisesRegex(PipelineError, "one voice"):
                load_voices(path)

    def test_cast_scenes_keeps_other_speakers(self):
        doc = scenes_doc()
        doc["speakers"]["host"] = {"en": {"engine": "mock", "voice": "tone-330"}}
        cast = resolve_cast(VOICES, {"preset": "male-male"}, "en", {}, True)
        cast_scenes(doc, [cast])
        self.assertIn("host", doc["speakers"])
        self.assertEqual(doc["speakers"]["guest"]["en"], {"engine": "mock", "voice": "tone-330"})


class RenderMetadataTest(unittest.TestCase):
    def test_extra_tags_reach_the_mux_and_are_checked(self):
        from motion_studio.render import RenderOptions, mux_cmd
        from motion_studio.schema import from_dict

        doc = scenes_doc()
        doc["scenes"] = doc["scenes"][1:]
        scenes = from_dict(doc, ROOT, check_files=False)
        cmd = mux_cmd(scenes, "en", Path("x.mp4"), RenderOptions(metadata={"comment": "stand-in narrator: x"}))
        self.assertIn("comment=stand-in narrator: x", cmd)
        self.assertEqual(cmd[cmd.index("comment=stand-in narrator: x") - 1], "-metadata")
        for bad in ({"title": "x"}, {"Bad Key": "x"}, {"comment": "two\nlines"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                RenderOptions(metadata=bad)


class PilotFilesTest(unittest.TestCase):
    """The committed pilot: topic files load, scripts match their scenes, fact checks match their narration."""

    def test_pilot_topics(self):
        for lang, preset in (("en", "male-female"), ("vi", "male-male")):
            with self.subTest(lang=lang):
                topic = load_topic(ROOT / "samples" / "pilot-cstr" / f"cstr-{lang}.topic.json")
                self.assertEqual(topic.cast[lang]["preset"], preset)
                self.assertEqual(topic.voices, ROOT / "voices.json")
                doc = json.loads(topic.scenes.read_text(encoding="utf-8"))
                committed = (topic.scenes.parent / f"script.{lang}.md").read_text(encoding="utf-8")
                self.assertEqual(committed, script_markdown(doc, lang), "regenerate the script with script_markdown")
                self.assertIn(f"Narration: {narration_hash(doc)[:12]}", topic.factcheck.read_text(encoding="utf-8"))


@unittest.skipUnless(ffmpeg_available() or REQUIRE_FFMPEG, "ffmpeg not installed")
class MockRunTest(Fixture):
    """script -> scenes -> voice (mock engine) -> render for real, in subprocesses, as `just video` runs them."""

    def test_voice_and_render_with_the_mock_engine(self):
        out = io.StringIO()
        env = {"TEST_REF_EN": str(self.topic_dir / "img" / "dot.svg")}
        with mock.patch.dict(os.environ, env), redirect_stdout(out), redirect_stderr(out), \
                mock.patch("motion_studio.pipeline.default_tts_python", return_value=Path(sys.executable)):
            code = cli.main(["video", str(self.topic), "--until", "render"])
        self.assertEqual(code, 0, out.getvalue())
        job = self.work / "demo"
        video = job / "demo.en.mp4"
        self.assertTrue(video.is_file())
        self.assertTrue((job / "demo.en.srt").is_file())
        self.assertTrue(list((job / "audio" / "tts").glob("*.en.wav")))
        timing = json.loads((job / "audio" / "tts" / "timing.en.json").read_text())
        self.assertEqual({s["voice"] for s in timing["scenes"]}, {"tone-220", "tone-330"})
        import subprocess

        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=comment", "-of", "json",
                                str(video)], capture_output=True, text=True, check=True)
        self.assertIn("owner's clone", json.loads(probe.stdout)["format"]["tags"]["comment"])
        self.assertIn("planned length", out.getvalue())


if __name__ == "__main__":
    unittest.main()
