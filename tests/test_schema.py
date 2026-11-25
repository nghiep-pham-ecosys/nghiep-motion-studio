import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motion_studio import cli  # noqa: E402
from motion_studio.schema import ScenesError, from_dict, load, load_schema, validate  # noqa: E402

SAMPLE = ROOT / "samples" / "three-scenes" / "scenes.json"


def minimal() -> dict:
    return {
        "version": 1,
        "title": {"en": "Example Co explainer", "vi": "Giới thiệu Example Co"},
        "languages": ["en", "vi"],
        "scenes": [
            {
                "id": "s1",
                "narration": {"en": "Hello.", "vi": "Xin chào."},
                "duration_s": 2,
                "visual": {"kind": "title", "text": {"en": "Hello", "vi": "Xin chào"}},
            }
        ],
    }


class SchemaFileTest(unittest.TestCase):
    def test_schema_is_a_valid_draft_2020_12_schema(self):
        from jsonschema import Draft202012Validator

        Draft202012Validator.check_schema(load_schema())

    def test_sample_validates(self):
        scenes = load(SAMPLE)
        self.assertEqual(len(scenes.scenes), 3)
        self.assertEqual(scenes.languages, ["en", "vi"])
        self.assertEqual((scenes.width, scenes.height, scenes.fps), (1920, 1080, 30))

    def test_minimal_validates(self):
        self.assertEqual(validate(minimal()), [])


class SchemaErrorsTest(unittest.TestCase):
    def assertInvalid(self, doc, fragment, base_dir=None):
        errs = validate(doc, base_dir)
        self.assertTrue(errs, "expected the document to be invalid")
        self.assertTrue(any(fragment in e for e in errs), f"{fragment!r} not in {errs}")

    def test_wrong_version(self):
        doc = minimal()
        doc["version"] = 2
        self.assertInvalid(doc, "/version")

    def test_unknown_top_level_field(self):
        doc = minimal()
        doc["extra"] = True
        self.assertInvalid(doc, "Additional properties")

    def test_missing_visual(self):
        doc = minimal()
        del doc["scenes"][0]["visual"]
        self.assertInvalid(doc, "'visual' is a required property")

    def test_empty_scenes(self):
        doc = minimal()
        doc["scenes"] = []
        self.assertInvalid(doc, "/scenes")

    def test_bad_language_code(self):
        doc = minimal()
        doc["languages"] = ["english"]
        self.assertInvalid(doc, "/languages/0")

    def test_blank_narration(self):
        doc = minimal()
        doc["scenes"][0]["narration"]["en"] = "   "
        self.assertInvalid(doc, "/scenes/0/narration/en")

    def test_non_positive_duration(self):
        doc = minimal()
        doc["scenes"][0]["duration_s"] = 0
        self.assertInvalid(doc, "/scenes/0/duration_s")

    def test_unknown_visual_kind_is_named(self):
        doc = minimal()
        doc["scenes"][0]["visual"] = {"kind": "hologram", "src": "a.png"}
        self.assertInvalid(doc, "unknown visual kind 'hologram'")

    def test_error_points_at_the_declared_kind(self):
        doc = minimal()
        doc["scenes"][0]["visual"] = {"kind": "image", "src": "a.png", "motion": "spin"}
        errs = validate(doc)
        self.assertTrue(any("/scenes/0/visual/motion" in e for e in errs), errs)

    def test_bad_color(self):
        doc = minimal()
        doc["scenes"][0]["visual"]["background"] = "navy"
        self.assertInvalid(doc, "/scenes/0/visual/background")

    def test_absolute_paths_rejected(self):
        for bad in ("/etc/passwd.png", "\\\\server\\a.png", "C:/a.png"):
            doc = minimal()
            doc["scenes"][0]["visual"] = {"kind": "image", "src": bad}
            self.assertInvalid(doc, "/scenes/0/visual")

    def test_trailing_newline_does_not_slip_through_patterns(self):
        doc = minimal()
        doc["languages"] = ["en\n"]
        self.assertInvalid(doc, "/languages/0")
        doc = minimal()
        doc["scenes"][0]["id"] = "s1\n"
        self.assertInvalid(doc, "/scenes/0/id")
        doc = minimal()
        doc["scenes"][0]["visual"]["background"] = "#000000\n"
        self.assertInvalid(doc, "/scenes/0/visual/background")

    def test_non_finite_numbers_rejected(self):
        doc = minimal()
        doc["scenes"][0]["duration_s"] = float("nan")
        self.assertInvalid(doc, "/scenes/0/duration_s: must be a finite number")

    def test_missing_kind_is_named(self):
        doc = minimal()
        doc["scenes"][0]["visual"] = {"src": "a.png"}
        self.assertInvalid(doc, "/scenes/0/visual/kind: required")

    def test_path_message_is_readable(self):
        doc = minimal()
        doc["scenes"][0]["visual"] = {"kind": "image", "src": "/abs.png"}
        self.assertInvalid(doc, "must be a relative path (got '/abs.png')")

    def test_odd_output_size_rejected(self):
        doc = minimal()
        doc["output"] = {"width": 1921, "height": 1080}
        self.assertInvalid(doc, "/output/width")


class SemanticErrorsTest(unittest.TestCase):
    def assertInvalid(self, doc, fragment, base_dir=None):
        errs = validate(doc, base_dir)
        self.assertTrue(any(fragment in e for e in errs), f"{fragment!r} not in {errs}")

    def test_missing_language_in_narration(self):
        doc = minimal()
        del doc["scenes"][0]["narration"]["vi"]
        self.assertInvalid(doc, "/scenes/0/narration: missing language(s) vi")

    def test_missing_language_in_title_card(self):
        doc = minimal()
        del doc["scenes"][0]["visual"]["text"]["en"]
        self.assertInvalid(doc, "/scenes/0/visual/text: missing language(s) en")

    def test_missing_language_in_title(self):
        doc = minimal()
        del doc["title"]["vi"]
        self.assertInvalid(doc, "/title: missing language(s) vi")

    def test_undeclared_language(self):
        doc = minimal()
        doc["scenes"][0]["narration"]["fr"] = "Bonjour."
        self.assertInvalid(doc, "language(s) fr not listed")

    def test_chapter_must_cover_languages(self):
        doc = minimal()
        doc["scenes"][0]["chapter"] = {"en": "Intro"}
        self.assertInvalid(doc, "/scenes/0/chapter: missing language(s) vi")

    def test_duplicate_ids(self):
        doc = minimal()
        doc["scenes"].append(copy.deepcopy(doc["scenes"][0]))
        self.assertInvalid(doc, "duplicate id 's1'")

    def test_scene_needs_duration_or_audio(self):
        doc = minimal()
        del doc["scenes"][0]["duration_s"]
        self.assertInvalid(doc, "no duration_s and no audio for en, vi")

    def test_audio_for_every_language_replaces_duration(self):
        doc = minimal()
        del doc["scenes"][0]["duration_s"]
        doc["scenes"][0]["audio"] = {"en": "a.en.wav", "vi": "a.vi.wav"}
        self.assertEqual(validate(doc), [])

    def test_partial_audio_without_duration(self):
        doc = minimal()
        del doc["scenes"][0]["duration_s"]
        doc["scenes"][0]["audio"] = {"en": "a.en.wav"}
        self.assertInvalid(doc, "no duration_s and no audio for vi")

    def test_missing_files_reported_when_checking(self):
        doc = minimal()
        doc["scenes"][0]["visual"] = {"kind": "image", "src": "nope.png"}
        doc["scenes"][0]["audio"] = {"en": "nope.wav"}
        with tempfile.TemporaryDirectory() as tmp:
            errs = validate(doc, Path(tmp))
        self.assertTrue(any("visual/src: file not found: nope.png" in e for e in errs), errs)
        self.assertTrue(any("audio/en: file not found: nope.wav" in e for e in errs), errs)
        self.assertEqual(validate(doc), [], "without base_dir, files are not checked")

    def test_from_dict_raises_with_every_error(self):
        doc = minimal()
        del doc["scenes"][0]["narration"]["vi"]
        del doc["title"]["en"]
        with self.assertRaises(ScenesError) as ctx:
            from_dict(doc, Path("."))
        self.assertEqual(len(ctx.exception.errors), 2)


class LoadAndCliTest(unittest.TestCase):
    def test_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenes.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ScenesError) as ctx:
                load(path)
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_nan_in_file_is_not_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenes.json"
            path.write_text(json.dumps(minimal()).replace('"duration_s": 2', '"duration_s": NaN'), encoding="utf-8")
            with self.assertRaisesRegex(ScenesError, "NaN is not a JSON number"):
                load(path)

    def test_non_utf8_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenes.json"
            path.write_bytes(b"\xff\xfe{}")
            with self.assertRaisesRegex(ScenesError, "not UTF-8"):
                load(path)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cli_validate_ok(self):
        code, out, _ = self.run_cli("validate", str(SAMPLE))
        self.assertEqual(code, 0)
        self.assertIn("3 scene(s)", out)

    def test_cli_validate_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenes.json"
            doc = minimal()
            del doc["scenes"][0]["narration"]["vi"]
            path.write_text(json.dumps(doc), encoding="utf-8")
            code, _, err = self.run_cli("validate", str(path))
        self.assertEqual(code, 1)
        self.assertIn("missing language(s) vi", err)

    def test_cli_schema_prints_json(self):
        code, out, _ = self.run_cli("schema")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["title"], "scenes.json")


if __name__ == "__main__":
    unittest.main()
