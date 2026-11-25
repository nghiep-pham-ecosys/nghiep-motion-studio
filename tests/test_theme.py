"""Themes: WCAG contrast of every text pair, and that frames, visuals, tapes and subtitles use the theme."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motion_studio.render import RenderOptions, ffmpeg_available, plan, render, scene_video_job  # noqa: E402
from motion_studio.schema import from_dict, validate  # noqa: E402
from motion_studio.subtitles import burn_cmd  # noqa: E402
from motion_studio.theme import (  # noqa: E402
    DARK,
    DEFAULT_THEME,
    LIGHT,
    TERMINAL_KEYS,
    THEMES,
    ass_colour,
    blend,
    contrast_ratio,
    get_theme,
    graphic_pairs,
    text_pairs,
)
from motion_studio.visuals import ADAPTERS, BuildRequest  # noqa: E402
from motion_studio.visuals.vhs import wrap_tape  # noqa: E402

AA_TEXT = 4.5  # WCAG 2.x AA, normal text
AA_GRAPHIC = 3.0  # WCAG 2.x 1.4.11, non-text contrast


def doc(visual: dict, theme: str | None = None) -> dict:
    d = {
        "version": 1,
        "title": {"en": "Acme demo"},
        "languages": ["en"],
        "output": {"width": 320, "height": 180, "fps": 10},
        "scenes": [{"id": "a", "narration": {"en": "Words."}, "duration_s": 0.5, "visual": visual}],
    }
    if theme is not None:
        d["theme"] = theme
    return d


TITLE = {"kind": "title", "text": {"en": "Hello"}}


class ContrastTest(unittest.TestCase):
    def test_reference_values(self):
        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"), 21.0, places=6)
        self.assertAlmostEqual(contrast_ratio("#ffffff", "#ffffff"), 1.0, places=6)
        self.assertAlmostEqual(contrast_ratio("#777777", "#ffffff"), 4.48, places=2)  # the classic just-fails grey
        self.assertEqual(blend("#000000", "#ffffff", 0.5), "#808080")

    def test_every_text_pair_meets_aa_in_both_themes(self):
        for theme in THEMES.values():
            pairs = text_pairs(theme)
            self.assertGreater(len(pairs), 60)
            for what, fg, bg in pairs:
                with self.subTest(theme=theme.name, pair=what):
                    ratio = contrast_ratio(fg, bg)
                    self.assertGreaterEqual(ratio, AA_TEXT, f"{fg} on {bg} is {ratio:.2f}:1")

    def test_graphics_stand_out(self):
        for theme in THEMES.values():
            for what, fg, bg in graphic_pairs(theme):
                with self.subTest(theme=theme.name, part=what):
                    self.assertGreaterEqual(contrast_ratio(fg, bg), AA_GRAPHIC)


class ThemeDataTest(unittest.TestCase):
    def test_light_is_default_and_dark_is_kept(self):
        self.assertEqual(DEFAULT_THEME, "light")
        self.assertIs(get_theme(None), LIGHT)
        self.assertIs(get_theme("dark"), DARK)
        with self.assertRaisesRegex(ValueError, "unknown theme 'neon'"):
            get_theme("neon")
        self.assertGreater(contrast_ratio(LIGHT.background, "#000000"), 15, "light means a light frame")
        self.assertLess(contrast_ratio(DARK.background, "#000000"), 1.5, "dark means a dark frame")

    def test_colours_and_fonts_are_well_formed(self):
        for theme in THEMES.values():
            data = theme.as_dict()
            for key in ("background", "surface", "border", "text", "muted", "accent", "subtitle_text", "subtitle_box"):
                self.assertRegex(data[key], r"^#[0-9a-f]{6}$", f"{theme.name}.{key}")
            self.assertEqual(set(theme.terminal), set(TERMINAL_KEYS) | {"background", "foreground", "selection", "cursor"})
            for font in (theme.font, theme.mono):
                RenderOptions(font=font)  # a theme font is safe in an ffmpeg filter
            self.assertEqual(json.loads(theme.json())["node_fill_opacity"], 0.16)
            self.assertIn("Text", theme.code)

    def test_code_tokens_are_pygments_tokens(self):
        try:
            from pygments.style import Style
            from pygments.token import STANDARD_TYPES, string_to_tokentype
        except ImportError:
            self.skipTest("pygments is not installed")
        for theme in THEMES.values():
            for name in theme.code:
                with self.subTest(theme=theme.name, token=name):
                    self.assertIn(string_to_tokentype(name), STANDARD_TYPES)
            styles = {string_to_tokentype(k): v for k, v in theme.code.items()}
            built = type("S", (Style,), {"background_color": theme.surface, "styles": styles})
            self.assertEqual(built.style_for_token(string_to_tokentype("Comment"))["color"], theme.code["Comment"].split("#")[1])

    def test_ass_colour(self):
        self.assertEqual(ass_colour("#112233"), "&H00332211")
        self.assertEqual(ass_colour("#112233", 0.0), "&HFF332211")
        self.assertEqual(ass_colour("#f8fafc", 0.85), "&H26FCFAF8")


class FrameCommandTest(unittest.TestCase):
    def vf(self, d: dict, opts: RenderOptions | None = None) -> str:
        opts = opts or RenderOptions()
        scenes = from_dict(d, Path("."), check_files=False)
        timing = plan(scenes, "en", opts)[0]
        cmd = scene_video_job(scenes, scenes.scenes[0], "en", timing, "v.mp4", opts).cmd
        return " ".join(cmd)

    def test_title_card_is_light_by_default(self):
        line = self.vf(doc(TITLE))
        self.assertIn("color=c=0xf8fafc", line)
        self.assertIn("fontcolor=0x0f172a", line)
        self.assertIn("font=DejaVu Sans", line)

    def test_dark_from_the_file_and_override_from_options(self):
        self.assertIn("color=c=0x0f172a", self.vf(doc(TITLE, "dark")))
        self.assertIn("fontcolor=0xf8fafc", self.vf(doc(TITLE, "dark")))
        self.assertIn("color=c=0xf8fafc", self.vf(doc(TITLE, "dark"), RenderOptions(theme="light")))

    def test_a_visual_colour_wins(self):
        line = self.vf(doc({**TITLE, "background": "#123456", "color": "#abcdef"}))
        self.assertIn("color=c=0x123456", line)
        self.assertIn("fontcolor=0xabcdef", line)

    def test_unknown_theme_option(self):
        with self.assertRaisesRegex(ValueError, "unknown theme"):
            RenderOptions(theme="neon")

    def test_schema_takes_light_or_dark_only(self):
        self.assertEqual(validate(doc(TITLE, "dark")), [])
        self.assertEqual(validate(doc(TITLE, "light")), [])
        errs = validate(doc(TITLE, "sepia"))
        self.assertTrue(any(e.startswith("/theme") for e in errs), errs)


class VisualThemeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_tape_gets_the_theme_unless_it_sets_one(self):
        out = wrap_tape("Type x\n", Path("c.mp4"), 640, 360, 15)
        line = next(ln for ln in out.splitlines() if ln.startswith("Set Theme "))
        value = json.loads(line[len("Set Theme "):])
        self.assertEqual(value["background"], LIGHT.terminal["background"])
        self.assertIn('"background":"#0f172a"', wrap_tape("Type x\n", Path("c.mp4"), 640, 360, 15, DARK))
        own = wrap_tape('Set Theme "Dracula"\nType x\n', Path("c.mp4"), 640, 360, 15)
        self.assertEqual(sum(ln.startswith("Set Theme") for ln in own.splitlines()), 1)

    def test_theme_is_part_of_every_cache_key(self):
        (self.base / "t.tape").write_text("Type x\n", encoding="utf-8")
        visuals = {
            "terminal": {"kind": "terminal", "src": "t.tape"},
            "diagram": {"kind": "diagram", "nodes": [{"id": "a", "label": "A", "col": 0, "row": 0}]},
        }
        for kind, visual in visuals.items():
            adapter = ADAPTERS[kind]
            req = lambda theme: BuildRequest("s", "en", visual, self.base, 640, 360, 15, theme.background, 2.0, theme)  # noqa: E731
            self.assertNotEqual(adapter.cache_key(req(LIGHT)), adapter.cache_key(req(DARK)), kind)

    def test_templates_take_colours_from_the_theme_only(self):
        templates = ROOT / "src" / "motion_studio" / "visuals" / "templates"
        for path in templates.glob("*.py"):
            code = path.read_text(encoding="utf-8")
            body = code.split('"""', 2)[2]  # skip the docstring, whose example may show a colour
            self.assertNotRegex(body, r"#[0-9a-fA-F]{6}", f"{path.name} hard-codes a colour")
            self.assertIn("MOTION_STUDIO_THEME", body)


class SubtitleThemeTest(unittest.TestCase):
    def style(self, **kw) -> str:
        cmd = burn_cmd(Path("v.mp4"), "subs.srt", Path("o.mp4"), 1920, 1080, **kw)
        return cmd[cmd.index("-vf") + 1]

    def test_box_in_theme_colours(self):
        light = self.style()
        self.assertIn("BorderStyle=3", light)
        self.assertIn(f"PrimaryColour={ass_colour(LIGHT.subtitle_text)}", light)
        self.assertIn(f"OutlineColour={ass_colour(LIGHT.subtitle_box, LIGHT.subtitle_box_opacity)}", light)
        dark = self.style(theme="dark")
        self.assertIn(f"PrimaryColour={ass_colour(DARK.subtitle_text)}", dark)
        self.assertIn("FontName=Acme Sans", self.style(font="Acme Sans"))


@unittest.skipUnless(ffmpeg_available(), "ffmpeg/ffprobe not on PATH")
class RenderedFrameTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def corner(self, path: Path) -> tuple:
        raw = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
             "-vf", "scale=in_color_matrix=bt709:in_range=tv,format=rgb24,crop=2:2:4:4", "-frames:v", "1", "-f", "rawvideo", "-"],
            capture_output=True, check=True,
        ).stdout
        return tuple(raw[:3])

    def test_title_card_frames_follow_the_theme(self):
        for name in ("light", "dark"):
            theme = get_theme(name)
            out = self.base / f"{name}.mp4"
            render(from_dict(doc(TITLE, name), self.base), "en", out, RenderOptions(preset="ultrafast", crf=18))
            want = tuple(int(theme.background[i : i + 2], 16) for i in (1, 3, 5))
            got = self.corner(out)
            self.assertTrue(all(abs(a - b) <= 4 for a, b in zip(got, want)), f"{name}: corner {got}, expected {want}")


if __name__ == "__main__":
    unittest.main()
