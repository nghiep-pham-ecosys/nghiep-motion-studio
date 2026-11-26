"""Tests for the lesson parser, the scene builder (parts, roles, pauses) and the lesson outputs. Synthetic data only."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motion_studio import lesson as L  # noqa: E402
from motion_studio.gate import narration_hash  # noqa: E402
from motion_studio.pipeline import load_topic, load_voices, resolve_cast  # noqa: E402
from motion_studio.schema import from_dict  # noqa: E402
from motion_studio.visuals.dialogue import hold_seconds  # noqa: E402

LESSON = """# Lesson 2030-01-02: a test lesson, from the post "Acme ships a widget"

### (a) Words

| Word | Meaning | Example |
| --- | --- | --- |
| widget | món đồ nhỏ | Acme ships a widget. |
| gadget | thiết bị | The gadget is new. |

### (b) Script

`/` short, `//` long, **bold** = stress.

> Every day / Acme ships **one widget**. //
>
> It is small, / and it works. // Customers like it

**Hard words:** *widget* /ˈwɪdʒ.ɪt/ · end**ed**.

### (c) Model answer

Question: *"How do you ship a widget?"* Frame: **cách làm → đánh đổi**.

> **Approach:** I test it first. Then I ship it.
>
> **Trade-off:** Tests cost time.

Follow-up: *"How do you know it works?"* → gợi ý: đo lỗi; hỏi khách hàng.

### (d) IELTS Speaking

Band 7 notes in Vietnamese.

**Part 1** (short answers)

> **Q: Do you like widgets?**
> Yes, I do. They are useful.
>
> **Q: Why?**
> Because they save time.

**Part 2** — cue card

*Describe a widget you use. You should say: what it is, and why you like it.*

> I'd like to talk about a widget.
>
> It is small. I use it every day.

**Part 3** (discussion)

> **Q: Will widgets change work?**
> I think so. They already do.

**Useful phrases:** to my surprise · it saves time · every day

### (e) IELTS Writing

*Prompt:* **Some people think widgets make work easier. To what extent do you agree?**

> Widgets are everywhere. Many people use them.
>
> In conclusion, widgets help.

**Why band 7:**

- **Task Response:** rõ quan điểm.
- Muốn lên band 8: ví dụ cụ thể hơn.
"""


def build(text: str = LESSON) -> tuple[L.Lesson, dict]:
    lesson = L.parse_lesson(text)
    return lesson, L.build_scenes(lesson)


def scenes_of(doc: dict, part: str) -> list[dict]:
    return [s for s in doc["scenes"] if s["id"].startswith(part + "-") and not s["id"].startswith(part + "-again")]


class ParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lesson = L.parse_lesson(LESSON)

    def test_header_and_source(self) -> None:
        self.assertEqual(self.lesson.date, "2030-01-02")
        self.assertEqual(self.lesson.source, "Acme ships a widget")

    def test_words_table(self) -> None:
        self.assertEqual([w.term for w in self.lesson.words], ["widget", "gadget"])
        self.assertEqual(self.lesson.words[0].gloss, "món đồ nhỏ")
        self.assertEqual(self.lesson.words[1].example, "The gadget is new.")

    def test_pause_marks_and_bold(self) -> None:
        first, second = self.lesson.script
        self.assertEqual([(p.text, p.mark) for p in first],
                         [("Every day", "/"), ("Acme ships **one widget**.", "//")])
        self.assertEqual([p.mark for p in second], ["/", "//", "//"])  # a paragraph without a final mark ends long
        self.assertEqual(second[-1].text, "Customers like it")
        self.assertEqual(L.plain(first[1].text), "Acme ships one widget.")

    def test_script_notes(self) -> None:
        self.assertEqual(self.lesson.script_notes[0][0], "Hard words")

    def test_model_answer_and_follow_up(self) -> None:
        self.assertEqual(self.lesson.question, "How do you ship a widget?")
        self.assertEqual(self.lesson.frame, ["cách làm", "đánh đổi"])
        self.assertEqual(self.lesson.answer, [("Approach", "I test it first. Then I ship it."),
                                              ("Trade-off", "Tests cost time.")])
        self.assertEqual(self.lesson.follow_up, "How do you know it works?")
        self.assertEqual(self.lesson.hint, "đo lỗi; hỏi khách hàng.")

    def test_ielts_speaking(self) -> None:
        self.assertEqual([qa.question for qa in self.lesson.part1], ["Do you like widgets?", "Why?"])
        self.assertEqual(self.lesson.part1[0].answer, "Yes, I do. They are useful.")
        self.assertTrue(self.lesson.cue_card.startswith("Describe a widget you use."))
        self.assertEqual(self.lesson.part2, ["I'd like to talk about a widget.", "It is small. I use it every day."])
        self.assertEqual(self.lesson.part3[0].answer, "I think so. They already do.")
        self.assertEqual(self.lesson.phrases, ["to my surprise", "it saves time", "every day"])

    def test_essay(self) -> None:
        self.assertTrue(self.lesson.prompt.startswith("Some people think widgets"))
        self.assertEqual(self.lesson.essay, ["Widgets are everywhere. Many people use them.", "In conclusion, widgets help."])
        self.assertEqual(self.lesson.essay_notes, [("Task Response", "rõ quan điểm."), ("", "Muốn lên band 8: ví dụ cụ thể hơn.")])

    def test_missing_section_is_named(self) -> None:
        with self.assertRaisesRegex(L.LessonError, r"\(e\)"):
            L.parse_lesson(LESSON.split("### (e)")[0])

    def test_bad_words_row(self) -> None:
        with self.assertRaisesRegex(L.LessonError, "3 cells"):
            L.parse_lesson(LESSON.replace("| gadget | thiết bị | The gadget is new. |", "| gadget | thiết bị |"))

    def test_translations_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.vi.tsv"
            path.write_text("# comment\nAcme ships a widget.\tAcme giao một món đồ.\n", encoding="utf-8")
            self.assertEqual(L.read_translations(path), {"Acme ships a widget.": "Acme giao một món đồ."})
            path.write_text("no tab here\n", encoding="utf-8")
            with self.assertRaises(L.LessonError):
                L.read_translations(path)


class BuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lesson, self.doc = build()

    def test_valid_scenes_with_ten_parts_in_order(self) -> None:
        chapters = [s["chapter"]["en"] for s in self.doc["scenes"] if "chapter" in s]
        self.assertEqual(chapters, [c for _, c, _ in L.PARTS])
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "characters").mkdir()
            for name in L.CHARACTER_FILES:
                (base / "characters" / name).write_text((ROOT / "samples" / "characters" / name).read_text())
            from_dict(self.doc, base)  # schema + files

    def test_every_scene_names_a_character_voice_never_the_clone(self) -> None:
        speakers = self.doc["speakers"]
        for scene in self.doc["scenes"]:
            self.assertIn(scene["speaker"], speakers)
            self.assertEqual(scene["visual"]["talk"], scene["speaker"].split("-")[0])
            self.assertEqual(scene["visual"]["label"], "AI voice")
        voices = {v["en"]["voice"] for v in speakers.values()}
        self.assertEqual(voices, {"af_heart", "am_michael", "af_heart@0.8", "am_michael@0.8"})
        self.assertTrue(all(v["en"]["engine"] == "kokoro" for v in speakers.values()))

    def test_roles_match_the_look(self) -> None:
        d = self.doc
        talk = lambda part, tag: {s["visual"]["talk"] for s in scenes_of(d, part) if f"-{tag}-" in s["id"]}  # noqa: E731
        self.assertEqual(talk("words", "word"), {"her"})  # she teaches
        self.assertEqual(talk("words", "repeat"), {"him"})  # he repeats
        self.assertEqual(talk("recap", "word"), {"her"})
        self.assertEqual(talk("recap", "repeat"), {"him"})
        self.assertEqual(talk("listen", "sentence"), {"him"})  # his story
        self.assertEqual(talk("shadowing", "phrase"), {"him"})
        self.assertEqual(talk("answer", "question"), {"her"})  # she interviews
        self.assertEqual(talk("answer", "answer"), {"him"})  # he answers
        self.assertEqual(talk("follow-up", "question"), {"her"})
        self.assertEqual(talk("follow-up", "answer"), {"him"})
        self.assertEqual(talk("speaking", "question"), {"her"})  # examiner
        self.assertEqual(talk("speaking", "answer"), {"him"})  # candidate
        self.assertEqual(talk("writing", "essay"), {"him"})  # he reads the essay
        self.assertEqual(talk("writing", "note"), {"her"})  # she explains the notes
        # shadowing: she gives the pause cue (the cue bubble is drawn at her side) on every phrase
        self.assertTrue(all(s["visual"]["pause"]["cue"] == "Your turn" for s in scenes_of(d, "shadowing")
                            if "-phrase-" in s["id"]))

    def test_backgrounds_per_part(self) -> None:
        for key, _, scenery in L.PARTS:
            for s in scenes_of(self.doc, key):
                self.assertEqual(s["visual"]["scenery"], f"characters/{scenery}.svg")

    def test_pauses_scale_with_marks(self) -> None:
        phrases = [s for s in scenes_of(self.doc, "shadowing") if "-phrase-" in s["id"]]
        short = next(s for s in phrases if s["narration"]["en"] == "Every day")["visual"]["pause"]
        long = next(s for s in phrases if s["narration"]["en"] == "Acme ships one widget.")["visual"]["pause"]
        for speech in (0.5, 1.0, 2.0, 4.0):  # whatever the phrase's length, // waits longer than /
            self.assertLess(hold_seconds(short, speech), hold_seconds(long, speech))
            self.assertGreater(hold_seconds(short, speech), speech)  # long enough to repeat the phrase
        self.assertTrue(all(s["speaker"] == "him-slow" for s in phrases))  # shadowing is slow

    def test_repeat_silences_after_words_and_sentences(self) -> None:
        for part, tag in (("words", "repeat"), ("words", "slow"), ("answer", "repeat"), ("speaking", "repeat")):
            got = [s for s in scenes_of(self.doc, part) if f"-{tag}-" in s["id"]]
            self.assertTrue(got, (part, tag))
            for s in got:
                pause = s["visual"]["pause"]
                self.assertEqual(pause["cue"], "Your turn")
                self.assertGreaterEqual(hold_seconds(pause, 1.0), 1.5)
        # listening parts have no pause
        self.assertFalse(any("pause" in s["visual"] for s in scenes_of(self.doc, "listen")))
        prep = [s for s in scenes_of(self.doc, "speaking") if s["visual"].get("pause", {}).get("add_s") == 60]
        self.assertEqual(len(prep), 1)  # Part 2: one minute to prepare

    def test_words_show_meaning_and_example_normal_then_slow(self) -> None:
        words = scenes_of(self.doc, "words")
        first = [s for s in words if s["id"].endswith("-1") and "-line-" not in s["id"]]
        self.assertEqual([s["id"].rsplit("-", 1)[0] for s in first],
                         ["words-word", "words-repeat", "words-example", "words-slow"])
        self.assertEqual(first[0]["visual"]["caption"]["sub"], "món đồ nhỏ")
        self.assertEqual(first[2]["speaker"], "her")
        self.assertEqual(first[3]["speaker"], "him-slow")
        self.assertEqual(first[2]["narration"]["en"], first[3]["narration"]["en"])

    def test_bold_words_reach_the_screen_not_the_voice(self) -> None:
        scene = next(s for s in scenes_of(self.doc, "shadowing") if "Acme ships" in s["narration"]["en"])
        self.assertIn("**one widget**", scene["visual"]["caption"]["text"])
        self.assertNotIn("*", scene["narration"]["en"])

    def test_translations_add_the_vietnamese_line(self) -> None:
        lesson = L.parse_lesson(LESSON, {"Then I ship it.": "Rồi tôi giao nó."})
        doc = L.build_scenes(lesson)
        hits = [s for s in doc["scenes"] if s["visual"]["caption"].get("sub") == "Rồi tôi giao nó."]
        self.assertTrue(hits)


class SourceAndOutputTests(unittest.TestCase):
    def write_lesson(self, tmp: Path, factcheck: bool) -> Path:
        folder = tmp / "lessons" / "day"
        folder.mkdir(parents=True)
        (tmp / "lessons" / "characters").mkdir()
        for name in L.CHARACTER_FILES:
            (tmp / "lessons" / "characters" / name).write_text((ROOT / "samples" / "characters" / name).read_text())
        path = folder / "acme.lesson.md"
        path.write_text(LESSON, encoding="utf-8")
        if factcheck:
            (folder / "acme.factcheck.md").write_text("Reviewed-by: test\n", encoding="utf-8")
        return path

    def test_write_source_makes_a_topic_the_pipeline_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_lesson(Path(tmp), factcheck=False)
            src = L.write_source(path, Path(tmp) / "out")
            topic = load_topic(src.topic)
            self.assertEqual(topic.job, "en-2030-01-02-acme")
            self.assertEqual((topic.min_s, topic.max_s), L.GATE_WINDOW)
            voices = load_voices(topic.voices)
            cast = resolve_cast(voices, topic.cast["en"], "en", {}, allow_stand_in=False)  # no clip needed
            self.assertFalse(cast.stand_in)
            self.assertEqual((cast.narrator["voice"], cast.guest["voice"]), ("af_heart", "am_michael"))
            self.assertIn("AI voices", cast.note())
            self.assertNotIn("clone (", cast.note())
            self.assertFalse(src.factcheck_ready)
            text = (src.folder / "factcheck.md").read_text(encoding="utf-8")
            doc = json.loads(src.scenes.read_text(encoding="utf-8"))
            self.assertIn(f"Narration: {narration_hash(doc)[:12]}", text)
            self.assertIn("- [ ] widget means: món đồ nhỏ", text)  # unchecked: the gate stops until a person checks
            self.assertNotIn("playlist", json.loads(src.topic.read_text(encoding="utf-8")))

    def test_reviewed_factcheck_is_copied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_lesson(Path(tmp), factcheck=True)
            src = L.write_source(path, Path(tmp) / "out")
            self.assertTrue(src.factcheck_ready)
            self.assertEqual((src.folder / "factcheck.md").read_text(encoding="utf-8"), "Reviewed-by: test\n")

    def fake_timeline(self, doc: dict, seconds: float = 30.0) -> dict:
        scenes, t = [], 0.0
        for s in doc["scenes"]:
            scenes.append({"id": s["id"], "start_s": t, "duration_s": seconds})
            t += seconds
        return {"duration_s": t, "scenes": scenes}

    def test_description_chapters_match_the_parts(self) -> None:
        lesson, doc = build()
        timeline = self.fake_timeline(doc)
        starts = {s["id"]: s["start_s"] for s in timeline["scenes"]}
        want = [(int(starts[s["id"]]), s["chapter"]["en"]) for s in doc["scenes"] if "chapter" in s]
        self.assertEqual(L.chapter_marks(doc, timeline), want)
        text = L.description(lesson, doc, timeline)
        self.assertIn("Chapters\n0:00 Intro\n", text)
        self.assertIn("AI voice", text)
        self.assertIn("Made with AI", text)
        for t, name in want:
            self.assertIn(f"{t // 60}:{t % 60:02d} {name}" if t < 3600 else name, text)

    def test_ffmetadata_has_one_chapter_per_part(self) -> None:
        meta = L.ffmetadata("T", [(0, "Intro"), (40, "Words")], 100.0, "AI voice")
        self.assertEqual(meta.count("[CHAPTER]"), 2)
        self.assertIn("START=40000\nEND=100000\ntitle=Words", meta)

    def test_bilingual_srt_adds_the_vi_line_once_per_scene(self) -> None:
        _, doc = build()
        timeline = self.fake_timeline(doc, 10.0)
        word = next(i for i, s in enumerate(doc["scenes"]) if s["id"] == "words-word-1")
        t = word * 10.0
        srt = (f"1\n00:{int(t // 60):02d}:{t % 60:06.3f} --> 00:{int(t // 60):02d}:{t % 60 + 1:06.3f}\nwidget.\n"
               .replace(".", ",", 1).replace(".", ","))
        srt = srt.replace("widget,", "widget.")
        text, added = L.bilingual_srt(srt, doc, timeline)
        self.assertEqual(added, 1)
        self.assertIn("widget.\nmón đồ nhỏ", text)


class PilotLessonTests(unittest.TestCase):
    """The committed pilot lesson parses and builds (its own content, read-only)."""

    def test_pilot_builds(self) -> None:
        path = ROOT / "samples" / "lesson-2026-11-26" / "ai-eng-p3.lesson.md"
        lesson = L.load_lesson(path)
        self.assertEqual(len(lesson.words), 12)
        doc = L.build_scenes(lesson)
        self.assertEqual(sum(1 for s in doc["scenes"] if "chapter" in s), 10)
        reviewed = path.with_name("ai-eng-p3.factcheck.md")
        if reviewed.is_file():  # the reviewed fact check names the narration this builder makes
            self.assertIn(f"Narration: {narration_hash(doc)[:12]}", reviewed.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
