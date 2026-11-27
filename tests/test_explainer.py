"""Tests for the explainer builder (``motion-studio explainer``) and the ``card`` visual.

Synthetic sources only (Example Co handbooks and made-up numbers), plus the committed pilots, built read-only.
"""

import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from motion_studio import explainer as E  # noqa: E402
from motion_studio.gate import Limits, check_factcheck, narration_hash  # noqa: E402
from motion_studio.pipeline import load_topic  # noqa: E402
from motion_studio.render import count_video_frames, ffmpeg_available  # noqa: E402
from motion_studio.schema import from_dict, validate  # noqa: E402
from motion_studio.theme import LIGHT  # noqa: E402
from motion_studio.visuals import ADAPTERS, GENERATED_KINDS, BuildRequest  # noqa: E402
from motion_studio.visuals import card as C  # noqa: E402
from motion_studio.visuals import dialogue as D  # noqa: E402
from test_dialogue import tone_burst  # noqa: E402
from test_images import fake_library  # noqa: E402

HAVE_FFMPEG = ffmpeg_available()
REQUIRE_FFMPEG = os.environ.get("MOTION_STUDIO_REQUIRE_FFMPEG") == "1"

POST = """---
date: 2026-11-30
series: ai-engineering
part: 9
title: "Example Co search: rank 12 of 40, then 2"
work: example-search
---

## linkedin

12 of 40. That is where the plain index ranked the right page at Example Co.

Adding the title moved it to rank 2. A top 3 now includes it.

## x

12 of 40, then 2.
"""

SCRIPT = '''"""A toy search."""


def add_title(title, text):
    return f"{title}. {text}"


def score(words, page):
    return sum(1 for w in words if w in page)
'''

EXPECTED = "40 pages\nplain: rank 12\nwith title: rank 2\n"

WORK = """# ledger
repos:
  public: [example]
entries:
  - id: example-search
    repo: example
    ref: https://example.com/example/pull/7
    merged: 2026-11-20
    what: >-
      A toy search over 40 pages
      with the title added to each page.
  - id: other
    repo: example
    ref: https://example.com/example/pull/8
    merged: 2026-11-21
    what: Something else.
"""

TALK = """<!-- a synthetic talk -->
Title: Example Co search, explained

## The problem {cafe}
Lan: The plain index ranked the right page 12th of 40.
Minh: That is far from the top.
Lan: Why? Because the page never says the words of the question.

## The code {desk}
[code toy.py 4-5 | add_title(): the fix]
Minh: The fix is two lines.
Lan: It puts the title in front of the text,
  and nothing else changes.
[chart Rank of the right page | max 40 | Plain = 12 (rank 12) | With title = 2 (rank 2)]
Minh: From rank 12 to rank 2.

## Quiz
[card Question | Which rank after the fix?]
Lan: Which rank after the fix? [pause 6]
Minh: Rank 2.
"""

LESSON = """# Lesson 2026-11-30: Example queues

## Concept
A queue holds work for up to 14 days.

## Worked example
Example Co puts 3 workers behind one queue.

## Edge cases
- A message seen twice: the timeout is too short.

## Quiz
1. How many workers read the queue?
   Answer: 3 workers.
2. How long can a message wait?
   Answer: Up to 14 days.
"""


def write_post(base: Path, post: str = POST, work: str = WORK) -> Path:
    folder = base / "content" / "2026-W49" / "2026-11-30-example"
    folder.mkdir(parents=True)
    (folder / "post.md").write_text(post, encoding="utf-8")
    (folder / "toy.py").write_text(SCRIPT, encoding="utf-8")
    (folder / "toy.expected").write_text(EXPECTED, encoding="utf-8")
    (base / "content" / "work.yaml").write_text(work, encoding="utf-8")
    return folder


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.lib = fake_library(self.base / "lib")
        self.folder = write_post(self.base)
        self.source = E.load_source(self.folder)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def build(self, talk: str = TALK) -> E.Built:
        return E.build_scenes(self.source, E.parse_talk(talk), self.lib)


class SourceTests(Fixture):
    def test_post_reads_front_matter_files_and_its_work_only(self) -> None:
        s = self.source
        self.assertEqual((s.kind, s.date, s.slug), ("post", "2026-11-30", "2026-11-30-example"))
        self.assertEqual(s.title, "Example Co search: rank 12 of 40, then 2")
        self.assertEqual([w["id"] for w in s.work], ["example-search"])
        self.assertEqual(s.work[0]["what"], "A toy search over 40 pages with the title added to each page.")
        self.assertEqual(list(s.texts), ["post.md", "toy.py", "toy.expected", "work.yaml (example-search)"])
        self.assertTrue(s.url.endswith("/notes/2026-11-30-example/"))

    def test_a_post_without_work_or_with_unknown_work_is_refused(self) -> None:
        folder = self.folder
        (folder / "post.md").write_text(POST.replace("work: example-search\n", ""), encoding="utf-8")
        with self.assertRaisesRegex(E.ExplainerError, "no work:"):
            E.load_source(folder)
        (folder / "post.md").write_text(POST.replace("example-search", "missing-id"), encoding="utf-8")
        with self.assertRaisesRegex(E.ExplainerError, "missing-id"):
            E.load_source(folder)

    def test_lesson_needs_its_sections_and_quiz_answers(self) -> None:
        path = self.base / "queues.md"
        path.write_text(LESSON, encoding="utf-8")
        s = E.load_source(path)
        self.assertEqual((s.kind, s.date, s.title, s.slug), ("lesson", "2026-11-30", "Example queues", "queues"))
        self.assertEqual(E.quiz_items(E.lesson_sections(LESSON)["quiz"])[1], ("How long can a message wait?",
                                                                            "Up to 14 days."))
        path.write_text(LESSON.replace("## Edge cases", "## Notes"), encoding="utf-8")
        with self.assertRaisesRegex(E.ExplainerError, "Edge cases"):
            E.load_source(path)
        path.write_text(LESSON.replace("   Answer: 3 workers.\n", ""), encoding="utf-8")
        with self.assertRaisesRegex(E.ExplainerError, "no 'Answer:' line"):
            E.load_source(path)


class TalkTests(unittest.TestCase):
    def test_sections_places_lines_and_views(self) -> None:
        talk = E.parse_talk(TALK)
        self.assertEqual(talk.title, "Example Co search, explained")
        self.assertEqual([(s.heading, s.place) for s in talk.sections],
                         [("The problem", "cafe"), ("The code", "desk"), ("Quiz", E.ROTATION[2])])
        code = talk.sections[1].lines
        self.assertEqual(code[1].text, "It puts the title in front of the text, and nothing else changes.")
        self.assertEqual(code[0].view, ("code", "toy.py 4-5 | add_title(): the fix"))
        self.assertEqual(code[2].view[0], "chart")
        quiz = talk.sections[2].lines
        self.assertEqual((quiz[0].who, quiz[0].pause, quiz[0].text), ("her", 6.0, "Which rank after the fix?"))
        self.assertEqual(quiz[1].who, "him")

    def test_bad_talks_name_the_line(self) -> None:
        cases = {
            "Lan: before any section\n": "before the first",
            "## A\n[video x.mp4]\nLan: Hi.\n": "unknown directive",
            "## A {beach}\nLan: Hi.\n": "unknown place",
            "## A\nLan: TODO say something.\n": "TODO",
            "## A\nBob: Hi.\n": "start with",
            "## A\n## B\nLan: Hi.\n": "empty: A",
        }
        for text, message in cases.items():
            with self.subTest(text=text), self.assertRaisesRegex(E.ExplainerError, message):
                E.parse_talk(text)


class NumberTests(Fixture):
    def test_numbers_and_names(self) -> None:
        self.assertEqual(E.numbers_in("BM25 put it 14th of 28, top-3, k1 is 1.5, 3,000 a day, on 2026-11-16."),
                         ["2026-11-16", "14", "28", "3", "1.5", "3000"])
        self.assertEqual(E.numbers_in("SAA-C03, EC2, S3 and a v2 API"), [])

    def test_each_number_names_the_line_that_shares_its_words(self) -> None:
        index = E.number_index(self.source.texts)
        found = E.best_place("2", "With title, rank 2.", index)
        self.assertEqual((found.name, found.line), ("toy.expected", 3))
        self.assertIsNone(E.best_place("99", "rank 99", index))

    def test_a_number_without_a_source_is_refused(self) -> None:
        for bad in ("Lan: It took 77 hours.\n", "[points Facts | rank 13]\nLan: Hi.\n",
                    "[chart Ranks | Plain = 13]\nLan: Hi.\n", "[card Facts | 99 pages]\nLan: Hi.\n"):
            with self.subTest(bad=bad), self.assertRaisesRegex(E.ExplainerError, "no source"):
                self.build("## A\n" + bad)

    def test_code_and_output_lines_are_their_own_source(self) -> None:
        built = self.build("## A\n[output toy.expected 1-3]\nLan: The output, line by line.\n")
        self.assertEqual(built.doc["scenes"][0]["visual"]["panel"]["lines"], EXPECTED.splitlines())
        self.assertIn("Output card: toy.expected lines 1-3 equal a fresh run of its script - source: toy.expected:1",
                      built.file_claims)


class BuilderTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.built = self.build()
        self.doc = self.built.doc

    def test_one_scene_per_sentence_and_a_chapter_per_section(self) -> None:
        scenes = self.doc["scenes"]
        self.assertEqual([s["chapter"]["en"] for s in scenes if "chapter" in s], ["The problem", "The code", "Quiz"])
        self.assertEqual([s["id"] for s in scenes[:4]], ["the-problem-1", "the-problem-2", "the-problem-3",
                                                         "the-problem-4"])
        self.assertEqual(scenes[2]["narration"]["en"], "Why?")
        self.assertEqual(self.doc["title"]["en"], "Example Co search, explained")

    def test_panels_make_cards_and_plain_lines_stay_illustrated(self) -> None:
        kinds = [(s["id"], s["visual"]["kind"]) for s in self.doc["scenes"]]
        self.assertEqual({k for i, k in kinds if i.startswith("the-problem")}, {"illustrated"})
        self.assertEqual({k for i, k in kinds if i.startswith("the-code")}, {"card"})
        code = self.doc["scenes"][4]["visual"]["panel"]
        self.assertEqual(code, {"type": "code", "title": "add_title(): the fix", "first_line": 4, "highlight": [],
                                "lines": SCRIPT.splitlines()[3:5], "source": "toy.py, lines 4-5"})
        chart = self.doc["scenes"][-3]["visual"]["panel"]
        self.assertEqual(chart["bars"], [{"label": "Plain", "value": 12.0, "text": "rank 12"},
                                         {"label": "With title", "value": 2.0, "text": "rank 2"}])
        self.assertEqual(chart["max"], 40.0)
        quiz = self.doc["scenes"][-2]["visual"]
        self.assertEqual(quiz["kind"], "illustrated")
        self.assertEqual(quiz["board"]["title"], "Question")
        self.assertEqual(quiz["pause"], {"scale": 0.0, "add_s": 6.0, "cue": "Answer aloud"})

    def test_ai_chips_on_every_scene_and_preset_voices_only(self) -> None:
        for scene in self.doc["scenes"]:
            v = scene["visual"]
            self.assertEqual((v["label"], v["badge"]), ("AI voice", "Made with AI"))
            self.assertEqual(v["talk"], scene["speaker"])
            self.assertEqual(v["name"], {"her": "Lan", "him": "Minh"}[v["talk"]])
        voices = {k: v["en"] for k, v in self.doc["speakers"].items()}
        self.assertEqual(voices, {"her": {"engine": "kokoro", "voice": "af_heart"},
                                  "him": {"engine": "kokoro", "voice": "am_michael"}})

    def test_scenes_are_valid_with_their_files(self) -> None:
        base = self.base / "job"
        (base / "library").mkdir(parents=True)
        for scene in self.doc["scenes"]:
            (base / scene["visual"]["image"]).write_bytes(b"")
        from_dict(self.doc, base)

    def test_long_sentences_and_pauses_on_cards_are_refused(self) -> None:
        long = "Lan: " + "This sentence goes on and on, " * 4 + "and ends.\n"
        with self.assertRaisesRegex(E.ExplainerError, "too long for one subtitle cue"):
            self.build("## A\n" + long)
        with self.assertRaisesRegex(E.ExplainerError, "needs the still look"):
            self.build("## A\n[points Facts | rank 12]\nLan: Answer now. [pause 5]\n")

    def test_code_panels_check_their_range_and_file(self) -> None:
        for bad, message in (("toy.py 1-40", "show at most"), ("toy.py 5-4", "backwards"), ("toy.py 8-12", "has 9 lines"),
                             ("../work.yaml 1-2", "no file"), ("toy.py 4-5 hl 7-7", "outside")):
            with self.subTest(bad=bad), self.assertRaisesRegex(E.ExplainerError, message):
                self.build(f"## A\n[code {bad}]\nLan: Look.\n")


class FactcheckTests(Fixture):
    def test_draft_lists_every_number_with_its_source_unchecked(self) -> None:
        built = self.build()
        text = E.draft_factcheck(self.source, built)
        self.assertIn(f"Narration: {narration_hash(built.doc)[:12]}", text)
        self.assertIn("- [ ] The work behind the post: example-search in example, merged 2026-11-20", text)
        self.assertIn("source: https://example.com/example/pull/7", text)
        self.assertIn('- [ ] "The plain index ranked the right page 12th of 40." (the-problem-1) - source: '
                      "12 at post.md:11, 40 at post.md:11", text)
        self.assertIn("- [ ] Code card: toy.py lines 4-5, shown unchanged - source: toy.py:4", text)
        claims = [ln for ln in text.splitlines() if ln.startswith("- [")]
        self.assertTrue(claims and all(ln.startswith("- [ ]") and " - source: " in ln for ln in claims))

    def test_the_gate_refuses_the_draft_until_it_is_reviewed(self) -> None:
        built = self.build()
        job = self.base / "job"
        job.mkdir()
        (job / "scenes.json").write_text(json.dumps(built.doc), encoding="utf-8")
        draft = E.draft_factcheck(self.source, built)
        (job / "factcheck.md").write_text(draft, encoding="utf-8")
        check = check_factcheck(job / "scenes.json", Limits())
        self.assertFalse(check.ok)
        self.assertIn("unchecked claim", check.summary)
        reviewed = draft.replace("Reviewed-by:", "Reviewed-by: Example Reviewer").replace("- [ ]", "- [x]")
        (job / "factcheck.md").write_text(reviewed, encoding="utf-8")
        self.assertTrue(check_factcheck(job / "scenes.json", Limits()).ok)


class WriteSourceTests(Fixture):
    def test_writes_a_topic_the_pipeline_reads(self) -> None:
        talk = self.base / "example.talk.md"
        talk.write_text(TALK, encoding="utf-8")
        out = self.base / "out"
        written = E.write_source(self.folder, talk, out, library=self.lib.folder)
        self.assertEqual(written.job, "en-2026-11-30-example-explainer")
        topic = json.loads(written.topic.read_text(encoding="utf-8"))
        self.assertEqual((topic["shorts"], topic["gate"]), (2, {"min_s": 480.0, "max_s": 900.0}))
        self.assertEqual(topic["playlist"], "lab-to-prod-ai-engineering")
        voices = json.loads((out / "voices.json").read_text(encoding="utf-8"))
        self.assertEqual(voices["playlists"]["presets"]["lab-to-prod-ai-engineering"]["title"],
                         "Lab to Prod — AI engineering in practice")
        self.assertTrue((out / "script.md").read_text(encoding="utf-8").startswith("# Example Co search, explained"))
        self.assertFalse(written.factcheck_ready)
        loaded = load_topic(written.topic)
        self.assertEqual(loaded.job, written.job)
        reviewed = self.base / "example.factcheck.md"
        reviewed.write_text("Reviewed-by: someone\n", encoding="utf-8")
        self.assertTrue(E.write_source(self.folder, talk, out, library=self.lib.folder).factcheck_ready)
        self.assertEqual((out / "factcheck.md").read_text(encoding="utf-8"), "Reviewed-by: someone\n")

    def test_lesson_job_name_and_playlist(self) -> None:
        path = self.base / "queues.md"
        path.write_text(LESSON, encoding="utf-8")
        talk = self.base / "queues.talk.md"
        talk.write_text("## Concept\nLan: A queue holds work for up to 14 days.\nMinh: With 3 workers.\n",
                        encoding="utf-8")
        written = E.write_source(path, talk, self.base / "out2", library=self.lib.folder)
        self.assertEqual(written.job, "en-2026-11-30-queues-explainer")
        self.assertEqual(json.loads(written.topic.read_text(encoding="utf-8"))["playlist"], "study-notes-explained")
        with self.assertRaisesRegex(E.ExplainerError, "ending in -explainer"):
            E.job_name(E.load_source(path), E.parse_talk("Job: en-queues\n## A\nLan: Hi.\n"))

    def test_draft_talk_has_the_cards_and_refuses_to_build_until_written(self) -> None:
        draft = E.draft_talk(self.source)
        self.assertIn("[code toy.py 4-5 | add_title()]", draft)
        self.assertIn("[output toy.expected 1-3]", draft)
        self.assertIn("Lan: 12 of 40.", draft)
        with self.assertRaisesRegex(E.ExplainerError, "TODO"):
            E.parse_talk(draft)
        path = self.base / "queues.md"
        path.write_text(LESSON, encoding="utf-8")
        self.assertIn("Lan: Question 2. How long can a message wait? [pause 8]\nMinh: Up to 14 days.",
                      E.draft_talk(E.load_source(path)))


class PilotTests(unittest.TestCase):
    """The committed pilots build: every number of their talks has a source (fake library, so no GPU images)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = fake_library(Path(self.tmp.name) / "lib")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def check(self, source: Path, talk: Path, job: str) -> dict:
        src, t = E.load_source(source), E.load_talk(talk)
        built = E.build_scenes(src, t, self.lib)
        self.assertEqual(E.job_name(src, t), job)
        chapters = [s for s in built.doc["scenes"] if "chapter" in s]
        self.assertGreaterEqual(len(chapters), 6)
        words = sum(len(s["narration"]["en"].split()) for s in built.doc["scenes"])
        self.assertTrue(1200 <= words <= 2000, words)  # about 8-15 min with the pauses
        reviewed = talk.with_name(talk.name.replace(".talk.md", ".factcheck.md"))
        if reviewed.is_file():  # a reviewed fact check names the narration this builder makes
            self.assertIn(f"Narration: {narration_hash(built.doc)[:12]}", reviewed.read_text(encoding="utf-8"))
        return built.doc

    def test_post_pilot(self) -> None:
        folder = ROOT / "samples" / "explainer-2026-11-27"
        doc = self.check(folder / "2026-11-27-ai-eng-p4", folder / "ai-eng-p4.talk.md", "en-2026-11-27-ai-eng-p4-explainer")
        panels = {s["visual"]["panel"]["type"] for s in doc["scenes"] if s["visual"]["kind"] == "card"}
        self.assertEqual(panels, {"code", "output", "chart", "points", "flow", "image"})

    def test_lesson_pilot(self) -> None:
        folder = ROOT / "samples" / "specialty-2026-11-27"
        self.check(folder / "saa-sqs-sns-eventbridge.md", folder / "saa-sqs-sns-eventbridge.talk.md",
                   "en-2026-11-27-saa-explainer")


class CardLayoutTests(unittest.TestCase):
    def test_code_rows_wrap_long_lines_at_a_space(self) -> None:
        line = "def bm25(docs, query, k1=1.5, b=0.75):  # " + "a long comment " * 8
        size, rows = C.code_rows([line, "    return 0"], 10, [[11, 11]], 900, 600, LIGHT)
        self.assertEqual(size, 30.0)
        self.assertEqual(rows[0][0], 10)
        self.assertIsNone(rows[1][0])  # a continuation row has no line number
        self.assertTrue("".join(t for t, *_ in rows[1][2]).startswith("    "))
        self.assertEqual([(n, lit) for n, lit, _ in rows if n], [(10, False), (11, True)])
        text = "".join(t for _, _, spans in rows[:-1] for t, *_ in spans)
        self.assertEqual(text.replace("    ", " ").split(), line.split())

    def test_highlighter_colours_keywords_strings_and_numbers(self) -> None:
        spans = C.code_spans('def f(x): return "a" + 1  # note', LIGHT)
        colour = {t.strip(): c for t, c, *_ in spans}
        self.assertEqual(colour["def"], C._style(LIGHT.code["Keyword"])[0])
        self.assertEqual(colour['"a"'], C._style(LIGHT.code["String"])[0])
        self.assertEqual(colour["1"], C._style(LIGHT.code["Number"])[0])
        self.assertEqual(colour["# note"], C._style(LIGHT.code["Comment"])[0])
        self.assertEqual(colour["f"], C._style(LIGHT.code["Name.Function"])[0])

    def test_chart_scales_to_max_and_flow_spreads_columns(self) -> None:
        bars = C.chart_layout([{"label": "a", "value": 14, "text": "14 of 28"}, {"label": "b", "value": 4}], 28, 700)
        self.assertEqual(bars, [("a", 350.0, "14 of 28"), ("b", 100.0, "4")])
        boxes = C.flow_boxes([["q"], ["a", "b", "c"]], (0, 0, 1000, 600))
        self.assertEqual(len(boxes[1]), 3)
        self.assertLess(boxes[0][0][0] + boxes[0][0][2], boxes[1][0][0])

    def test_frame_shows_the_chips_name_source_and_caption(self) -> None:
        v = {"kind": "card", "image": "x.jpg", "talk": "him", "name": "Minh", "part": "2 · Code",
             "label": "AI voice", "badge": "Made with AI", "caption": {"text": "Two **lines**."},
             "panel": {"type": "points", "title": "Facts", "items": ["one"], "source": "toy.py, lines 4-5"}}
        svg = C.frame_svg(v, LIGHT, 1920, 1080)
        for text in ("AI voice", "Made with AI", "Minh", "2 · Code", "toy.py, lines 4-5", "Facts", "lines"):
            self.assertIn(text, svg)

    def test_schema_knows_the_card(self) -> None:
        doc = {"version": 1, "title": {"en": "t"}, "languages": ["en"], "scenes": [
            {"id": "a", "narration": {"en": "Hi."}, "duration_s": 1,
             "visual": {"kind": "card", "image": "s.jpg", "talk": "her",
                        "panel": {"type": "chart", "bars": [{"label": "x", "value": 2}], "max": 4}}}]}
        self.assertEqual(validate(doc), [])
        doc["scenes"][0]["visual"]["panel"]["type"] = "table"
        self.assertTrue(validate(doc))
        self.assertIn("card", GENERATED_KINDS)
        self.assertIsInstance(ADAPTERS["card"], C.CardAdapter)


@unittest.skipUnless(HAVE_FFMPEG or REQUIRE_FFMPEG, "ffmpeg is not installed")
class CardClipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        (self.base / "lib").mkdir()
        for name in ("still.jpg", "picture.png"):
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc2=s=640x360:d=1", "-frames:v", "1", str(self.base / "lib" / name)], check=True)
        self.wav = self.base / "line.wav"
        tone_burst(self.wav, 0.2, 0.6, 0.2)  # 1.0 s

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def clip(self, panel: dict) -> Path:
        v = {"kind": "card", "image": "lib/still.jpg", "talk": "her", "focus": [0.3, 0.4], "name": "Lan",
             "caption": {"text": "Hello."}, "panel": panel}
        req = BuildRequest(scene_id="s", lang="en", visual=v, base_dir=self.base, width=320, height=180, fps=10,
                           background=LIGHT.background, target_s=1.0, theme=LIGHT, audio=self.wav)
        work = self.base / f"w-{panel['type']}"
        work.mkdir()
        out = self.base / f"{panel['type']}.mp4"
        C.CardAdapter().build(req, out, work)
        self.assertEqual(count_video_frames(out), math.ceil(D.clip_seconds(v, 1.0, 1.0) * 10 - 1e-6))
        return out

    def test_code_and_image_cards_render(self) -> None:
        self.clip({"type": "code", "title": "toy.py", "lines": SCRIPT.splitlines()[3:5], "first_line": 4,
                   "highlight": [[5, 5]]})
        self.clip({"type": "image", "src": "lib/picture.png", "title": "The post"})


if __name__ == "__main__":
    unittest.main()
