"""Tests for the generated visual kinds: schema rules, adapters and real renders.

The render tests run each adapter only where its tool is installed and skip
with the reason otherwise. Set MOTION_STUDIO_REQUIRE_MANIM=1 or
MOTION_STUDIO_REQUIRE_VHS=1 (CI does) to turn such a skip into a failure.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motion_studio import cli  # noqa: E402
from motion_studio.render import (  # noqa: E402
    RenderOptions,
    ffmpeg_available,
    plan,
    render,
    with_generated_visuals,
)
from motion_studio.schema import MAX_CODE_LINES, from_dict, load, validate  # noqa: E402
from motion_studio.visuals import (  # noqa: E402
    ADAPTERS,
    Adapter,
    BuildRequest,
    VisualError,
    build_visuals,
    localize,
    missing_tools,
)
from motion_studio.visuals.manim_ce import manim_command  # noqa: E402
from motion_studio.visuals.vhs import wrap_tape  # noqa: E402
from motion_studio.theme import LIGHT  # noqa: E402

SAMPLE = ROOT / "samples" / "visuals" / "scenes.json"
REQUIRE_FFMPEG = os.environ.get("MOTION_STUDIO_REQUIRE_FFMPEG") == "1"
REQUIRE = {
    "manim": os.environ.get("MOTION_STUDIO_REQUIRE_MANIM") == "1",
    "terminal": os.environ.get("MOTION_STUDIO_REQUIRE_VHS") == "1",
}
FAST = RenderOptions(preset="ultrafast", crf=30)


def doc_with(scenes: list, languages=("en",), width=640, height=360, fps=15) -> dict:
    langs = list(languages)
    return {
        "version": 1,
        "title": {lang: "Acme visuals" for lang in langs},
        "languages": langs,
        "output": {"width": width, "height": height, "fps": fps},
        "scenes": scenes,
    }


def scene(sid: str, visual: dict, seconds: float = 2.0, langs=("en",)) -> dict:
    return {"id": sid, "narration": {lang: "Some words." for lang in langs}, "duration_s": seconds, "visual": visual}


def diagram(**extra) -> dict:
    v = {
        "kind": "diagram",
        "nodes": [
            {"id": "a", "label": "Input", "col": 0, "row": 0},
            {"id": "b", "label": {"en": "Model", "vi": "Mô hình"}, "col": 1, "row": 0},
        ],
        "edges": [{"from": "a", "to": "b", "label": "tokens"}],
    }
    v.update(extra)
    return v


class SchemaTest(unittest.TestCase):
    def errors(self, visual: dict, langs=("en", "vi"), base_dir: Path | None = None) -> list[str]:
        return validate(doc_with([scene("s", visual, langs=langs)], languages=langs), base_dir)

    def assertInvalid(self, visual: dict, fragment: str, **kw):
        errs = self.errors(visual, **kw)
        self.assertTrue(any(fragment in e for e in errs), f"{fragment!r} not in {errs}")

    def test_sample_is_valid(self):
        scenes = load(SAMPLE)
        self.assertEqual([s["visual"]["kind"] for s in scenes.scenes], ["diagram", "code", "terminal", "manim"])

    def test_valid_generated_visuals(self):
        for visual in (
            diagram(),
            diagram(steps=[["a", "b"]], title={"en": "T", "vi": "T"}),
            {"kind": "code", "src": "x.py", "highlight": [[1, 2]], "language": "python", "style": "monokai"},
            {"kind": "manim", "src": "m/scene.py", "scene": "Flow_2"},
            {"kind": "terminal", "src": "t/demo.tape", "loop": True},
        ):
            self.assertEqual(self.errors(visual), [], visual)

    def test_unknown_kind_lists_every_kind(self):
        self.assertInvalid({"kind": "hologram"}, "use title, image, video, manim, diagram, code, terminal, dialogue, illustrated or card")

    def test_errors_are_reported_against_the_declared_kind(self):
        self.assertInvalid({"kind": "diagram", "nodes": [{"id": "a", "label": "x", "col": 9, "row": 0}]}, "col")
        self.assertInvalid({"kind": "manim", "src": "m.py"}, "'scene' is a required property")

    def test_source_extensions(self):
        self.assertInvalid({"kind": "manim", "src": "m.js", "scene": "S"}, "must be a .py file")
        self.assertInvalid({"kind": "terminal", "src": "demo.sh"}, "must be a .tape file")
        self.assertInvalid({"kind": "terminal", "src": "/abs/demo.tape"}, "must be a relative path")

    def test_diagram_rules(self):
        dup = diagram()
        dup["nodes"].append({"id": "a", "label": "Again", "col": 2, "row": 0})
        self.assertInvalid(dup, "duplicate node id 'a'")
        same_cell = diagram()
        same_cell["nodes"][1]["col"] = 0
        self.assertInvalid(same_cell, "col 0, row 0 is already used")
        self.assertInvalid(diagram(edges=[{"from": "a", "to": "zz"}]), "unknown node id 'zz'")
        self.assertInvalid(diagram(edges=[{"from": "a", "to": "a"}]), "two different nodes")
        self.assertInvalid(diagram(steps=[["a"]]), "node(s) b are in no step")
        self.assertInvalid(diagram(steps=[["a"], ["a", "b"]]), "already in an earlier step")
        self.assertInvalid(diagram(steps=[["a", "b", "q"]]), "unknown node id 'q'")
        self.assertInvalid(diagram(title={"en": "Only English"}), "title: missing language(s) vi")
        self.assertInvalid(diagram(edges=[{"from": "a", "to": "b", "label": {"en": "x"}}]), "missing language(s) vi")

    def test_code_rules(self):
        self.assertInvalid({"kind": "code", "src": "x.py", "highlight": [[3, 2]]}, "first line 3 is after last line 2")
        self.assertInvalid({"kind": "code", "src": "x.py", "highlight": [[1]]}, "highlight")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "x.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
            (base / "long.py").write_text("x = 1\n" * (MAX_CODE_LINES + 1), encoding="utf-8")
            (base / "bin.py").write_bytes(b"\xff\xfe\x00")
            self.assertInvalid({"kind": "code", "src": "x.py", "highlight": [[1, 3]]}, "line 3 is past the end", base_dir=base)
            self.assertInvalid({"kind": "code", "src": "long.py"}, f"at most {MAX_CODE_LINES}", base_dir=base)
            self.assertInvalid({"kind": "code", "src": "bin.py"}, "as UTF-8 text", base_dir=base)
            self.assertInvalid({"kind": "code", "src": "gone.py"}, "file not found: gone.py", base_dir=base)
            self.assertInvalid({"kind": "terminal", "src": "gone.tape"}, "file not found: gone.tape", base_dir=base)
            self.assertInvalid({"kind": "manim", "src": "gone.py", "scene": "S"}, "file not found", base_dir=base)
            self.assertEqual(self.errors({"kind": "code", "src": "x.py", "highlight": [[1, 2]]}, base_dir=base), [])


class LocalizeTest(unittest.TestCase):
    def test_resolves_nested_text_and_keeps_the_rest(self):
        visual = diagram(title={"en": "Hi", "vi": "Chào"})
        out = localize(visual, "vi", ["en", "vi"])
        self.assertEqual(out["title"], "Chào")
        self.assertEqual([n["label"] for n in out["nodes"]], ["Input", "Mô hình"])
        self.assertEqual(out["edges"][0], {"from": "a", "to": "b", "label": "tokens"})
        self.assertEqual(visual["title"], {"en": "Hi", "vi": "Chào"}, "the input is not changed")


class TapeTest(unittest.TestCase):
    def test_header_is_added(self):
        out = wrap_tape('Type "ls"\nEnter\n', Path("/w/clip.mp4"), 1920, 1080, 30)
        self.assertTrue(out.startswith('Output "/w/clip.mp4"\nSet Width 1920\nSet Height 1080\nSet Framerate 30\n'))
        self.assertIn("Set FontSize 36", out)
        self.assertTrue(out.endswith('Type "ls"\nEnter\n'))

    def test_tape_font_size_wins(self):
        out = wrap_tape("Set FontSize 20\nType x\n", Path("c.mp4"), 1920, 1080, 30)
        self.assertNotIn("Set FontSize 36", out)

    def test_owned_settings_are_rejected(self):
        for line in ("Output demo.gif", "Set Width 800", "  set height 600", "Set Framerate 60"):
            with self.assertRaisesRegex(VisualError, "remove these lines"):
                wrap_tape(f"{line}\nType x\n", Path("c.mp4"), 640, 360, 15)


class FakeAdapter(Adapter):
    """Writes a solid-colour clip with ffmpeg; counts builds."""

    version = "test"

    def __init__(self, kind: str, paced: bool, seconds: float = 1.0):
        self.kind, self.paced, self.seconds, self.builds = kind, paced, seconds, 0

    def missing(self):
        return None

    def build(self, req: BuildRequest, out: Path, work: Path) -> None:
        self.builds += 1
        length = req.target_s if self.paced else self.seconds
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=red:s={req.width}x{req.height}:r={req.fps}",
             "-t", f"{length:.3f}", "-pix_fmt", "yuv420p", "-f", "mp4", str(out)],
            check=True,
        )


@unittest.skipUnless(ffmpeg_available() or REQUIRE_FFMPEG, "ffmpeg/ffprobe not on PATH")
class BuildVisualsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        (self.base / "demo.tape").write_text("Type x\n", encoding="utf-8")
        self.paced = FakeAdapter("diagram", paced=True)
        self.unpaced = FakeAdapter("terminal", paced=False, seconds=3.0)
        patcher = mock.patch.dict(ADAPTERS, {"diagram": self.paced, "terminal": self.unpaced})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def scenes(self):
        langs = ("en", "vi")
        doc = doc_with([
            scene("d1", diagram(), 1.0, langs),
            scene("d2", diagram(), 1.0, langs),  # same visual and length: built once
            scene("t", {"kind": "terminal", "src": "demo.tape", "loop": True, "background": "#000000"}, 1.0, langs),
        ], languages=langs)
        return from_dict(doc, self.base)

    def test_clips_replace_visuals_and_unpaced_scenes_stretch(self):
        scenes = self.scenes()
        opts = RenderOptions(visuals_dir=self.base / "cache")
        built, timeline = with_generated_visuals(scenes, "en", plan(scenes, "en", opts), opts)
        kinds = [s["visual"]["kind"] for s in built.scenes]
        self.assertEqual(kinds, ["video", "video", "video"])
        self.assertEqual(built.scenes[0]["visual"]["src"], built.scenes[1]["visual"]["src"])
        self.assertEqual(built.scenes[2]["visual"]["background"], "#000000")
        self.assertTrue(built.scenes[2]["visual"]["loop"])
        self.assertEqual([t.frames for t in timeline], [15, 15, 45], "the 3 s recording stretches its 1 s scene")
        self.assertEqual(self.paced.builds, 1)
        self.assertEqual(scenes.scenes[0]["visual"]["kind"], "diagram", "the input scenes are not changed")

    def test_cache_is_reused_and_rebuild_forces_a_build(self):
        scenes = self.scenes()
        targets = {s["id"]: 1.0 for s in scenes.scenes}
        kw = {"default_background": "#101828", "clip_length": lambda p: 3.0}
        _, first = build_visuals(scenes, "en", targets, self.base / "cache", **kw)
        _, second = build_visuals(scenes, "en", targets, self.base / "cache", **kw)
        self.assertEqual([b.cached for b in first], [False, False, False])
        self.assertEqual([b.cached for b in second], [True, True, True])
        self.assertEqual((self.paced.builds, self.unpaced.builds), (1, 1))
        build_visuals(scenes, "en", targets, self.base / "cache", rebuild=True, **kw)
        self.assertEqual((self.paced.builds, self.unpaced.builds), (2, 2))
        self.assertEqual(sorted(p.name.split("-")[0] for p in (self.base / "cache").iterdir()), ["diagram", "terminal"])

    def test_cache_key_follows_inputs(self):
        req = lambda visual, target=1.0, lang="en": BuildRequest(  # noqa: E731
            "s", lang, localize(visual, lang, ["en", "vi"]), self.base, 640, 360, 15, "#101828", target
        )
        tape = {"kind": "terminal", "src": "demo.tape"}
        key = self.unpaced.cache_key(req(tape))
        self.assertEqual(key, self.unpaced.cache_key(req(tape, target=9.0)), "an unpaced clip ignores the target")
        self.assertNotEqual(self.paced.cache_key(req(diagram())), self.paced.cache_key(req(diagram(), target=2.0)))
        self.assertNotEqual(self.paced.cache_key(req(diagram())), self.paced.cache_key(req(diagram(), lang="vi")))
        (self.base / "demo.tape").write_text("Type y\n", encoding="utf-8")
        self.assertNotEqual(key, self.unpaced.cache_key(req(tape)), "a changed source changes the key")

    def test_missing_tool_names_the_scene_and_the_reason(self):
        self.paced.missing = lambda: "no manim here"
        scenes = self.scenes()
        with self.assertRaisesRegex(VisualError, "scene 'd1': a diagram visual cannot be built here: no manim here"):
            build_visuals(scenes, "en", {s["id"]: 1.0 for s in scenes.scenes}, self.base / "cache",
                          default_background="#101828", clip_length=lambda p: 1.0)

    def test_render_with_a_fake_adapter(self):
        opts = RenderOptions(preset="ultrafast", crf=30, visuals_dir=self.base / "cache")
        result = render(self.scenes(), "en", self.base / "out.mp4", opts)
        self.assertEqual(sum(t.frames for t in result.timeline), 75)
        self.assertTrue((self.base / "out.mp4").is_file())


class ReviewFixesTest(unittest.TestCase):
    """Cache keys, paths, code line numbers, tapes and the Manim frame size."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def req(self, visual, target=2.0, lang="en", width=640, height=360):
        return BuildRequest("s", lang, visual, self.base, width, height, 15, "#101828", target)

    def test_manim_key_follows_target_and_language(self):
        (self.base / "m.py").write_text("x = 1\n", encoding="utf-8")
        adapter, visual = ADAPTERS["manim"], {"kind": "manim", "src": "m.py", "scene": "S"}
        with mock.patch.object(type(adapter), "tool_version", return_value="v"):
            key = adapter.cache_key(self.req(visual))
            self.assertNotEqual(key, adapter.cache_key(self.req(visual, target=8.0)))
            self.assertNotEqual(key, adapter.cache_key(self.req(visual, lang="vi")))

    def test_code_with_leading_blank_lines_keeps_file_numbers(self):
        (self.base / "c.py").write_text("\n\nx = 1\ny = 2\n", encoding="utf-8")
        seen = {}
        with mock.patch("motion_studio.visuals.manim_ce.render_manim", lambda *a, **kw: seen.update(kw["spec"])):
            ADAPTERS["code"].build(self.req({"kind": "code", "src": "c.py", "highlight": [[3, 4], [1, 3]]}), self.base / "o", self.base)
        self.assertEqual(seen["code"], "x = 1\ny = 2\n")
        self.assertEqual(seen["first_line"], 3)
        self.assertEqual(seen["highlight"], [[1, 2], [1, 1]])

    def test_code_ranges_must_frame_code(self):
        (self.base / "c.py").write_text("\n\nx = 1\n\ny = 2\n", encoding="utf-8")
        for rng, ok in (([1, 2], False), ([4, 4], False), ([2, 3], True), ([5, 5], True), ([6, 6], False)):
            errs = validate(doc_with([scene("s", {"kind": "code", "src": "c.py", "highlight": [rng]})]), self.base)
            self.assertEqual(errs == [], ok, (rng, errs))

    def test_tape_owned_settings_fail_validation(self):
        (self.base / "t.tape").write_text("Output demo.gif\nType x\n", encoding="utf-8")
        errs = validate(doc_with([scene("s", {"kind": "terminal", "src": "t.tape"})]), self.base)
        self.assertTrue(any("remove Output demo.gif" in e for e in errs), errs)

    def test_manim_frame_matches_the_output_aspect(self):
        cfgs = []

        def fake_run(cmd, cwd, env=None, what=""):
            cfgs.append(Path(cmd[cmd.index("-c") + 1]).read_text(encoding="utf-8"))
            clip = Path(cmd[cmd.index("--media_dir") + 1]) / "videos" / "x" / "clip.mp4"
            clip.parent.mkdir(parents=True)
            clip.write_bytes(b"x")
            return ""

        work = self.base / "work"
        work.mkdir()
        with mock.patch("motion_studio.visuals.manim_ce.run_tool", fake_run), \
                mock.patch("motion_studio.visuals.manim_ce.manim_command", return_value=["manim"]):
            ADAPTERS["diagram"].build(self.req(diagram(), width=1080, height=1920), self.base / "out.mp4", work)
        self.assertIn("frame_height = 8.0", cfgs[0])
        self.assertIn("frame_width = 4.500000", cfgs[0])
        self.assertIn("pixel_width = 1080", cfgs[0])


@unittest.skipUnless(ffmpeg_available() or REQUIRE_FFMPEG, "ffmpeg/ffprobe not on PATH")
class RelativeCacheDirTest(unittest.TestCase):
    def test_relative_cache_dir_gives_absolute_clips(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "demo.tape").write_text("Type x\n", encoding="utf-8")
            fake = FakeAdapter("terminal", paced=False, seconds=1.0)
            scenes = from_dict(doc_with([scene("t", {"kind": "terminal", "src": "demo.tape"}, 1.0)]), base)
            cwd = os.getcwd()
            os.chdir(base)
            try:
                with mock.patch.dict(ADAPTERS, {"terminal": fake}):
                    built, _ = build_visuals(scenes, "en", {"t": 1.0}, Path("rel-cache"),
                                             default_background="#101828", clip_length=lambda p: 1.0)
            finally:
                os.chdir(cwd)
            src = Path(built.scenes[0]["visual"]["src"])
            self.assertTrue(src.is_absolute() and src.is_file(), src)


class NoGeneratedVisualsTest(unittest.TestCase):
    def test_scenes_come_back_unchanged(self):
        scenes = from_dict(doc_with([scene("t", {"kind": "title", "text": {"en": "Hi"}})]), Path("."), check_files=False)
        timeline = plan(scenes, "en", RenderOptions())
        self.assertIs(with_generated_visuals(scenes, "en", timeline, RenderOptions())[0], scenes)


class ToolLookupTest(unittest.TestCase):
    def test_manim_command_from_env(self):
        with mock.patch.dict(os.environ, {"MOTION_STUDIO_MANIM": "/opt/venv/bin/python -m manim"}):
            self.assertEqual(manim_command(), ["/opt/venv/bin/python", "-m", "manim"])

    def test_every_kind_reports_a_status(self):
        self.assertEqual(set(missing_tools()), {"manim", "diagram", "code", "terminal", "dialogue", "illustrated", "card"})

    def test_cli_tools_prints_one_line_per_kind(self):
        out = io.StringIO()
        with redirect_stdout(out):
            status = cli.main(["tools"])
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 7)
        self.assertEqual(status, 0 if all(line.startswith("OK") for line in lines) else 1)


def _skip_reason(kind: str) -> str | None:
    if not ffmpeg_available():
        return "ffmpeg/ffprobe not on PATH"
    return ADAPTERS[kind].missing()


def ffprobe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


class AdapterRenderTest(unittest.TestCase):
    """Render one small scene per adapter with the real tool."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def need(self, kind: str) -> None:
        why = _skip_reason(kind)
        if why is None:
            return
        requirement = "manim" if kind in ("manim", "diagram", "code") else kind
        if REQUIRE.get(requirement):
            self.fail(f"{kind} adapter is required here but cannot run: {why}")
        self.skipTest(f"{kind} adapter cannot run here: {why}")

    def render_one(self, visual: dict, seconds: float = 2.0, langs=("en",)) -> tuple[dict, object]:
        doc = doc_with([scene("s", visual, seconds, langs=langs)], languages=langs)
        scenes = from_dict(doc, self.base)
        opts = RenderOptions(preset="ultrafast", crf=30, visuals_dir=self.base / "cache")
        result = render(scenes, langs[-1], self.base / "out.mp4", opts)
        info = ffprobe(self.base / "out.mp4")
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (640, 360))
        self.assertEqual(int(video["nb_read_frames"]), sum(t.frames for t in result.timeline))
        clips = list((self.base / "cache").glob("*.mp4"))
        self.assertEqual(len(clips), 1, clips)
        self.assert_light_corner(self.base / "out.mp4")
        return info, result

    def assert_light_corner(self, path: Path) -> None:
        """The clip's frame edge is the light theme background (Manim background, VHS terminal colours)."""
        raw = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "0.3", "-i", str(path),
             "-vf", "scale=in_color_matrix=bt709:in_range=tv,format=rgb24,crop=2:2:4:4", "-frames:v", "1", "-f", "rawvideo", "-"],
            capture_output=True, check=True,
        ).stdout
        want = tuple(int(LIGHT.background[i : i + 2], 16) for i in (1, 3, 5))
        got = tuple(raw[:3])
        self.assertTrue(all(abs(a - b) <= 6 for a, b in zip(got, want)), f"corner {got}, expected about {want}")

    def test_diagram(self):
        self.need("diagram")
        _, result = self.render_one(diagram(title={"en": "Flow", "vi": "Luồng"}), 2.0, langs=("en", "vi"))
        self.assertEqual(result.timeline[0].frames, 30, "a diagram fits its scene")

    def test_code(self):
        self.need("code")
        (self.base / "snippet.py").write_text("def f(x):\n    return x + 1\n\nprint(f(2))\n", encoding="utf-8")
        _, result = self.render_one({"kind": "code", "src": "snippet.py", "highlight": [[1, 2], [4, 4]]}, 2.0)
        self.assertEqual(result.timeline[0].frames, 30)

    def test_manim_script(self):
        self.need("manim")
        (self.base / "scene.py").write_text(
            "from manim import Circle, Create, Scene\n\n"
            "class Ring(Scene):\n"
            "    def construct(self):\n"
            "        self.play(Create(Circle()), run_time=3)\n",
            encoding="utf-8",
        )
        _, result = self.render_one({"kind": "manim", "src": "scene.py", "scene": "Ring"}, 1.0)
        self.assertEqual(result.timeline[0].frames, 45, "the 3 s clip stretches the 1 s scene")

    def test_terminal(self):
        self.need("terminal")
        (self.base / "demo.tape").write_text('Type "echo hi"\nEnter\nSleep 500ms\n', encoding="utf-8")
        _, result = self.render_one({"kind": "terminal", "src": "demo.tape"}, 0.5)
        self.assertGreater(result.timeline[0].frames, 8, "the recording stretches the short scene")


if __name__ == "__main__":
    unittest.main()
