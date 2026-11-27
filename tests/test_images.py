"""Tests for the illustrated look: the library prompts, shot selection, bubble layout, the clip and the lesson look.

Synthetic images only (solid colours made by ffmpeg, or empty files where only the path matters). The GPU worker
is not tested here: torch and diffusers exist only in the images venv.
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from motion_studio import images as I  # noqa: E402
from motion_studio import lesson as L  # noqa: E402
from motion_studio.render import count_video_frames, ffmpeg_available  # noqa: E402
from motion_studio.schema import from_dict, validate  # noqa: E402
from motion_studio.theme import LIGHT  # noqa: E402
from motion_studio.visuals import ADAPTERS, GENERATED_KINDS, BuildRequest  # noqa: E402
from motion_studio.visuals import dialogue as D  # noqa: E402
from motion_studio.visuals import illustrated as V  # noqa: E402
from test_dialogue import tone_burst  # noqa: E402
from test_lesson import LESSON  # noqa: E402

HAVE_FFMPEG = ffmpeg_available()
REQUIRE_FFMPEG = os.environ.get("MOTION_STUDIO_REQUIRE_FFMPEG") == "1"
D_SHOTS = ("both-her", "both-him", "both-her-2", "both-him-2", "her-1", "her-2", "him-1", "him-2")
A_SHOTS = ("both-her", "both-him", "her-1", "him-1")
PLACES = ("classroom", "cafe", "interview", "exam", "desk")


def fake_library(folder: Path, places=PLACES, a_places=PLACES, skip=()) -> I.Library:
    """A version 2 library.json with style D in ``places`` and style A in ``a_places`` (minus ``skip``), and an empty
    file per shot."""
    folder.mkdir(parents=True, exist_ok=True)
    shots = []
    for style, names, where in (("D", D_SHOTS, places), ("A", A_SHOTS, a_places)):
        for place in where:
            for name in names:
                sid = f"{style}-{place}-{name}"
                if sid in skip:
                    continue
                (folder / f"{sid}.jpg").write_bytes(b"")
                two = name.startswith("both")
                talk = name.split("-")[1] if two else name.split("-")[0]
                faces = {"her": [0.3, 0.4], "him": [0.7, 0.4]} if two else {}
                shots.append({"id": sid, "style": style, "place": place, "shot": name, "file": f"{sid}.jpg",
                              "who": ["her", "him"] if two else [talk], "talk": talk, "faces": faces,
                              "focus": faces.get(talk) or [0.32 if talk == "her" else 0.68, 0.36],
                              "seed": 1, "model": "m", "license": "l"})
    doc = {"version": 2, "styles": {"D": {"name": "chibi", "share": 0.75}, "A": {"name": "anime", "share": 0.25}},
           "sources": [], "shots": shots}
    (folder / I.MANIFEST).write_text(json.dumps(doc), encoding="utf-8")
    return I.load_library(folder)


class SpecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = I.load_spec(I.SPEC)
        self.items = I.expand(self.spec)
        self.by_id = {it.id: it for it in self.items}

    def test_each_style_names_its_model_licence_and_pinned_revision(self) -> None:
        for style in ("D", "A"):
            model = I.model_for(self.spec, style)
            self.assertEqual(model["repo"], "cagliostrolab/animagine-xl-3.1")
            self.assertRegex(model["revision"], r"^[0-9a-f]{40}$")  # a commit, never a branch name
            self.assertIn("Open RAIL++-M", model["license"])
            self.assertTrue(model["url"].startswith("https://huggingface.co/"))
        for it in self.items:
            if it.kind != "split":
                self.assertEqual(it.model, self.spec["styles"][it.style]["model"], it.id)
        with self.assertRaisesRegex(I.ImagesError, "no style"):
            I.model_for(self.spec, "Z")

    def test_a_style_with_an_unknown_model_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = json.loads(I.SPEC.read_text(encoding="utf-8"))
            spec["styles"]["A"]["model"] = "sdxl-base"
            path = Path(tmp) / "p.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            with self.assertRaisesRegex(I.ImagesError, "sdxl-base"):
                I.load_spec(path)

    def test_style_d_figures_backgrounds_and_composites(self) -> None:
        d = self.spec["styles"]["D"]
        figures = [it for it in self.items if it.kind == "figure"]
        self.assertEqual(len(figures), 2 * len(d["poses"]))
        self.assertTrue({"portrait", "talk", "listen", "think", "point", "laugh", "nod"} <= set(d["poses"]))
        seeds = self.spec.get("seeds", {})
        for it in figures:
            self.assertEqual(it.seed, seeds.get(it.id, {"her": 104, "him": 204}[it.who[0]]), it.id)
        scenes = [it for it in self.items if it.kind == "scene"]
        self.assertEqual(len(scenes), len(PLACES) * len(D_SHOTS))
        for it in scenes:
            self.assertEqual(it.deps[0], f"D-bg-{it.place}")
            self.assertEqual([self.by_id[x].who[0] for x in it.deps[1:]], list(it.who))  # her left, him right
            self.assertIn(it.talk, it.who)
            self.assertEqual(it.focus, it.faces[it.talk])
            if len(it.who) == 2:
                self.assertLess(it.faces["her"][0], 0.5)
                self.assertGreater(it.faces["him"][0], 0.5)
            speaker_pose = next(x for x in it.deps[1:] if self.by_id[x].who[0] == it.talk).rsplit("-", 1)[1]
            self.assertNotIn(speaker_pose, ("listen", "nod"), it.id)  # the speaker is never drawn listening
        listener = self.by_id["D-cafe-both-her"].deps[2]
        self.assertEqual(listener, "D-fig-him-listen")

    def test_style_a_portraits_panels_and_split_two_shots(self) -> None:
        a = [it for it in self.items if it.style == "A"]
        self.assertEqual({it.id for it in a if "portrait" in it.id}, {"A-portrait-her", "A-portrait-him"})
        shots = [it for it in a if it.is_shot]
        self.assertEqual(len(shots), len(PLACES) * len(A_SHOTS))
        for it in shots:
            self.assertIn(it.talk, it.who)
            if it.kind == "split":
                left, right = (self.by_id[x] for x in it.deps)
                self.assertEqual((left.who, right.who), (("her",), ("him",)))
                self.assertTrue(it.deps[it.who.index(it.talk)].endswith("-talk"))
                self.assertTrue(it.deps[1 - it.who.index(it.talk)].endswith("-listen"))
        for it in a:
            if it.who == ("her",) and it.kind == "draw":
                self.assertRegex(it.prompt, r"\b(portrait|upper body)\b")  # framed head and shoulders, modest
                self.assertIn("large breasts", it.negative)

    def test_prompts_describe_adults_and_avoid_text(self) -> None:
        adult = [t.strip() for t in self.spec["adult_negative"].split(",")]
        self.assertTrue({"child", "loli", "shota", "school uniform"} <= set(adult))
        for it in self.items:
            if it.kind == "split":
                continue
            for tag in adult:
                self.assertIn(tag, it.negative, it.id)
            self.assertNotIn("{", it.prompt)
            self.assertNotIn("talking", it.prompt)  # it brings in lettering; the pose is described instead
            if it.who and it.kind != "scene":
                self.assertIn("adult " + ("woman" if it.who[0] == "her" else "man"), it.prompt)
        bad = self.spec["adult_negative"].split(", ")[0]
        spec = json.loads(json.dumps(self.spec))
        spec["styles"]["D"]["negative"] = spec["styles"]["D"]["negative"].replace(bad + ", ", "")
        spec["styles"]["D"]["characters"]["her"]["negative"] = "long hair"
        with self.assertRaisesRegex(I.ImagesError, "adult-only"):
            I.expand(spec)

    def test_a_new_seed_reaches_the_shots_built_from_it(self) -> None:
        spec = json.loads(json.dumps(self.spec))
        spec["seeds"] = {"D-fig-her-talk": 1104}
        after = {it.id: it for it in I.expand(spec)}
        self.assertEqual(after["D-fig-her-talk"].seed, 1104)
        self.assertNotEqual(after["D-cafe-both-her"].params_hash, self.by_id["D-cafe-both-her"].params_hash)
        self.assertNotEqual(after["D-cafe-her-1"].params_hash, self.by_id["D-cafe-her-1"].params_hash)
        self.assertEqual(after["D-cafe-him-1"].params_hash, self.by_id["D-cafe-him-1"].params_hash)
        spec["seeds"] = {"A-panel-cafe-him-listen": 7}
        after = {it.id: it for it in I.expand(spec)}
        self.assertNotEqual(after["A-cafe-both-her"].params_hash, self.by_id["A-cafe-both-her"].params_hash)
        self.assertEqual(after["A-cafe-both-him"].params_hash, self.by_id["A-cafe-both-him"].params_hash)

    def test_bad_spec_is_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text(json.dumps({"version": 2}), encoding="utf-8")
            with self.assertRaisesRegex(I.ImagesError, "missing"):
                I.load_spec(path)


class LibraryAndPickTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = fake_library(Path(self.tmp.name) / "lib")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_or_old_library_says_how_to_build_it(self) -> None:
        with self.assertRaisesRegex(I.ImagesError, "images-library"):
            I.load_library(Path(self.tmp.name) / "nothing")
        old = Path(self.tmp.name) / "old"
        old.mkdir()
        (old / I.MANIFEST).write_text(json.dumps({"version": 1, "shots": []}), encoding="utf-8")
        with self.assertRaisesRegex(I.ImagesError, "version 2"):
            I.load_library(old)

    def test_a_missing_file_is_left_out(self) -> None:
        (self.lib.folder / "D-cafe-him-1.jpg").unlink()
        lib = I.load_library(self.lib.folder)
        self.assertNotIn("D-cafe-him-1", lib.shots)
        self.assertIn("D-cafe-him-2", lib.shots)

    def test_styles_change_only_at_part_boundaries_near_the_share(self) -> None:
        sizes = (6, 40, 20, 30, 20, 25, 4, 80, 30, 25)
        lines = [(f"p{k}", PLACES[k % 5], "her" if j % 2 else "him") for k, n in enumerate(sizes) for j in range(n)]
        styles = I.pick_styles(lines, self.lib)
        self.assertEqual(self.lib.main_style(), "D")
        self.assertEqual(styles[0], "D")  # the first part sets the main look
        by_part = {}
        for (part, _, _), style in zip(lines, styles):
            by_part.setdefault(part, set()).add(style)
        self.assertTrue(all(len(v) == 1 for v in by_part.values()))  # one style per part
        order = [next(iter(by_part[f"p{k}"])) for k in range(len(sizes))]
        self.assertIn("A", order)
        self.assertFalse(any(a == b == "A" for a, b in zip(order, order[1:])))  # mixed in, never two parts in a row
        self.assertAlmostEqual(styles.count("A") / len(styles), 0.25, delta=0.05)
        self.assertEqual(styles, I.pick_styles(lines, self.lib))  # the same on every run

    def test_a_style_is_used_only_where_it_has_stills(self) -> None:
        lib = fake_library(Path(self.tmp.name) / "lib-a", a_places=("cafe",))
        lines = [(f"p{k}", PLACES[k % 5], "her" if j % 3 else "him") for k in range(10) for j in range(10)]
        for (_, place, _), style in zip(lines, I.pick_styles(lines, lib)):
            if style == "A":
                self.assertEqual(place, "cafe")
        only_d = fake_library(Path(self.tmp.name) / "lib-d", a_places=())
        self.assertEqual(set(I.pick_styles(lines, only_d)), {"D"})

    def test_the_bubble_always_sits_on_a_still_of_its_speaker(self) -> None:
        lines = [(f"p{k}", PLACES[k % 5], "her" if (j * 7 + k) % 3 else "him") for k in range(10) for j in range(30)]
        for (_, place, talk), sid in zip(lines, I.pick_shots(lines, self.lib)):
            entry = self.lib.shots[sid]
            self.assertIn(talk, entry["who"], sid)  # never the other character's still
            self.assertEqual(entry["place"], place)
            x = I.focus_for(entry, talk)[0]
            self.assertTrue(x < 0.5 if talk == "her" else x > 0.5, sid)  # the tail points at the speaker's side
        with self.assertRaisesRegex(I.ImagesError, "does not show him"):
            I.focus_for(self.lib.shots["D-cafe-her-1"], "him")

    def test_a_part_opens_on_the_two_shot_then_shots_take_turns(self) -> None:
        lib = fake_library(Path(self.tmp.name) / "lib-only-d", a_places=())
        lines = [("p1", "cafe", "him")] * 6 + [("p2", "cafe", "her")] + [("p2", "cafe", "him")]
        got = I.pick_shots(lines, lib)
        self.assertEqual(got[:6], ["D-cafe-both-him", "D-cafe-him-1", "D-cafe-both-him", "D-cafe-him-2",
                                   "D-cafe-both-him-2", "D-cafe-him-1"])
        self.assertEqual(got[6], "D-cafe-both-her")  # a new part opens on its two-shot
        self.assertEqual(got[7], "D-cafe-him-1")

    def test_never_the_same_still_twice_in_a_row(self) -> None:
        lines = [("p", "classroom", "her" if k % 3 else "him") for k in range(60)]
        got = I.pick_shots(lines, self.lib)
        self.assertTrue(all(a != b for a, b in zip(got, got[1:])))

    def test_falls_back_when_a_shot_is_missing(self) -> None:
        lib = fake_library(Path(self.tmp.name) / "lib2", places=("exam",), a_places=(),
                           skip=("D-exam-both-her", "D-exam-her-1", "D-exam-her-2", "D-exam-both-her-2"))
        got = I.pick_shots([("p", "exam", "her")] * 4, lib)
        self.assertTrue(all(lib.shots[s]["who"] == ["her", "him"] for s in got))  # only two-shots show her
        self.assertTrue(all(a != b for a, b in zip(got, got[1:])))
        lib = fake_library(Path(self.tmp.name) / "lib3", places=("exam",), a_places=(),
                           skip=tuple(f"D-exam-{n}" for n in D_SHOTS if "her" in n or n.startswith("both")))
        with self.assertRaisesRegex(I.ImagesError, "no D still of her"):
            I.pick_shots([("p", "exam", "her")], lib)

    def test_unknown_place_is_named(self) -> None:
        with self.assertRaisesRegex(I.ImagesError, "beach"):
            I.pick_shots([("p", "beach", "her")], self.lib)

    def test_worker_command(self) -> None:
        cmd = I.worker_cmd(Path("/v/bin/python"), Path("p.json"), Path("/lib"), only=["D-cafe-him-1"], force=True)
        self.assertEqual(cmd[:3], ["/v/bin/python", "-m", "motion_studio.images_worker"])
        self.assertIn("--force", cmd)
        self.assertEqual(cmd[cmd.index("--only") + 1], "D-cafe-him-1")


class LayoutTests(unittest.TestCase):
    def test_bubble_sits_on_the_speakers_side_inside_the_frame(self) -> None:
        her, _ = V.bubble_layout({"text": "Hello there."}, LIGHT, "her")
        him, _ = V.bubble_layout({"text": "Hello there."}, LIGHT, "him")
        self.assertLess(her.x, V.BASE_W / 2 - her.w / 2)
        self.assertGreater(him.x, V.BASE_W / 2 - him.w / 2)
        for box in (her, him):
            self.assertGreaterEqual(box.x, 0)
            self.assertLessEqual(box.x + box.w, V.BASE_W)
            self.assertAlmostEqual(box.y + box.h, V.BUBBLE_BOTTOM)

    def test_text_is_big_and_long_text_shrinks_inside_the_bubble(self) -> None:
        short, blocks = V.bubble_layout({"text": "Every day.", "sub": "Mỗi ngày."}, LIGHT, "her")
        self.assertEqual(blocks[0].size, V.CAPTION_SIZE)
        self.assertTrue(blocks[1].italic)
        long_text = " ".join(["Acme ships one small widget every single day of the week"] * 6)
        big, blocks = V.bubble_layout({"text": long_text, "sub": long_text}, LIGHT, "him", cue=True)
        self.assertLess(blocks[0].size, V.CAPTION_SIZE)
        self.assertLessEqual(big.h, V.BUBBLE_MAX_H + 1)
        self.assertGreater(big.h, short.h)
        for block in blocks:
            for line in block.lines:
                width = sum(D._width(w, block.size) for w in line) + block.size * 0.32 * (len(line) - 1)
                self.assertLessEqual(width, V.BUBBLE_W - 2 * V.BUBBLE_PAD[0] + 1)

    def test_tail_points_up_towards_the_speaker(self) -> None:
        box, _ = V.bubble_layout({"text": "Hi."}, LIGHT, "her")
        left, right, tip = V.tail_points(box, (0.2, 0.4))
        self.assertLess(tip[1], box.y)
        self.assertTrue(box.x <= left[0] < right[0] <= box.x + box.w)
        _, _, tip_far = V.tail_points(box, (0.6, 0.4))
        self.assertGreater(tip_far[0], tip[0])

    def test_board_card_on_the_other_side_and_paragraphs_dropped(self) -> None:
        card = {"title": "Word 1 of 2", "lines": [{"style": "big", "text": "widget"}, {"style": "vi", "text": "món đồ"}]}
        self.assertIs(V.compact_board(card), card)
        para = {"title": "Story", "lines": [{"style": "body", "text": "A long paragraph. " * 20}]}
        self.assertIsNone(V.compact_board(para))
        question = {"title": "Interview question", "lines": [{"style": "body", "text": "“How do you ship?”"}]}
        self.assertIs(V.compact_board(question), question)
        for talk in ("her", "him"):
            card = V.card_box(talk, 1000)
            bubble, _ = V.bubble_layout({"text": "x " * 200}, LIGHT, talk, cue=True)
            self.assertLessEqual(card.h, V.CARD_MAX_H)
            self.assertGreaterEqual(card.y, V.BASE_H / 2 - 20)  # the lower half: faces stay visible
            apart = card.x >= bubble.x + bubble.w or card.x + card.w <= bubble.x
            self.assertTrue(apart, talk)  # beside the bubble, never over it
            self.assertTrue(0 <= card.x and card.x + card.w <= V.BASE_W)
        self.assertGreater(V.card_box("her", 300).x, V.BASE_W / 2)
        self.assertLess(V.card_box("him", 300).x, V.BASE_W / 2)

    def test_a_long_title_shrinks_to_the_card(self) -> None:
        self.assertEqual(V.title_size("Word 1 of 9", V.CARD_W - 80), 32)
        size = V.title_size("Shadowing, paragraph 2 of 7", V.CARD_W - 80)
        self.assertLess(size, 32)
        self.assertLessEqual(D._width((("Shadowing, paragraph 2 of 7", True),), size), V.CARD_W - 80 + 0.01)

    def test_a_long_word_shrinks_to_the_card(self) -> None:
        width = V.CARD_W - 80
        blocks = V.card_layout([("big", "idempotency"), ("vi", "tính lũy đẳng")], width, 300, LIGHT)
        for b in blocks:
            for line in b.lines:
                bold = [tuple((x, True) for x, _ in w) for w in line] if b.weight == "bold" else line
                self.assertLessEqual(sum(D._width(w, b.size) for w in bold) + b.size * 0.32 * (len(line) - 1)
                                     + b.indent, width + 1)

    def test_overlay_has_labels_name_bubble_and_card(self) -> None:
        visual = {"kind": "illustrated", "image": "a.jpg", "talk": "him", "name": "Minh", "part": "3 · Listen",
                  "label": "AI voice", "badge": "Made with AI", "caption": {"text": "Every **day**.", "sub": "Mỗi ngày."},
                  "board": {"title": "Word", "lines": [{"style": "big", "text": "widget"}]}}
        svg = V.overlay_svg(visual, LIGHT, 1920, 1080)
        for text in ("Minh", "3 · Listen", "AI voice", "Made with AI", "Mỗi ngày.", "widget", "<polygon"):
            self.assertIn(text, svg)
        self.assertNotIn("<image", svg)  # the still is laid under the overlay by ffmpeg, not embedded

    def test_ai_chips_are_small_and_faint_but_shown(self) -> None:
        visual = {"kind": "illustrated", "image": "a.jpg", "talk": "her", "part": "3 · Listen",
                  "label": "AI voice", "badge": "Made with AI", "caption": {"text": "Hi."}}
        svg = V.overlay_svg(visual, LIGHT, 1920, 1080)
        for text in ("AI voice", "Made with AI"):
            chip = re.search(r'<g opacity="([0-9.]+)"><rect [^>]*height="(\d+)" rx="(\d+)"[^>]*/><text [^>]*'
                             r'font-size="(\d+)"[^>]*>' + re.escape(text) + "<", svg)
            self.assertIsNotNone(chip, text)
            self.assertEqual(chip.groups(), ("0.55", "36", "18", "20"))
        part = re.search(r'<rect [^>]*height="(\d+)"[^>]*/><text [^>]*font-size="(\d+)"[^>]*>3 · Listen<', svg)
        self.assertEqual(part.groups(), ("52", "30"))  # the part label keeps its size
        self.assertNotIn('opacity="0.55"><rect x="40', svg)

    def test_the_camera_move_is_slow_and_bounded(self) -> None:
        self.assertAlmostEqual(V.zoom_amount(0), V.ZOOM_MIN)
        self.assertLess(V.zoom_amount(3), V.zoom_amount(6))
        self.assertEqual(V.zoom_amount(600), V.ZOOM_MAX)
        z = V.zoompan((0.3, 0.4), 90, 30, 1920, 1080)
        self.assertIn("d=90", z)
        self.assertIn("0.3000*iw", z)
        self.assertIn("s=1920x1080", z)

    def test_schema(self) -> None:
        doc = {"version": 1, "title": {"en": "Acme"}, "languages": ["en"],
               "output": {"width": 320, "height": 180, "fps": 10},
               "scenes": [{"id": "a", "narration": {"en": "Hi."}, "duration_s": 1,
                           "visual": {"kind": "illustrated", "image": "lib/a.jpg", "talk": "her", "focus": [0.3, 0.4],
                                      "caption": {"text": "Hi."}, "pause": {"scale": 1, "add_s": 1, "cue": "Go"}}}]}
        self.assertEqual(validate(doc), [])
        doc["scenes"][0]["visual"]["focus"] = [1.5, 0]
        self.assertTrue(validate(doc))
        doc["scenes"][0]["visual"].update(focus=[0.5, 0.5], image="a.gif")
        self.assertIn(".jpg, .jpeg or .png", " ".join(validate(doc)))
        self.assertIn("illustrated", GENERATED_KINDS)
        self.assertIsInstance(ADAPTERS["illustrated"], V.IllustratedAdapter)


@unittest.skipUnless(HAVE_FFMPEG or REQUIRE_FFMPEG, "ffmpeg is not installed")
class ClipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        (self.base / "lib").mkdir()
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=s=640x360:d=1", "-frames:v", "1", str(self.base / "lib" / "still.jpg")], check=True)
        self.wav = self.base / "line.wav"
        tone_burst(self.wav, 0.2, 0.6, 0.2)  # 1.0 s

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def request(self, v: dict) -> BuildRequest:
        return BuildRequest(scene_id="s", lang="en", visual=v, base_dir=self.base, width=320, height=180, fps=10,
                            background=LIGHT.background, target_s=1.0, theme=LIGHT, audio=self.wav)

    def visual(self, **extra) -> dict:
        return {"kind": "illustrated", "image": "lib/still.jpg", "talk": "her", "focus": [0.3, 0.4], "name": "Lan",
                "caption": {"text": "Hello."}, **extra}

    def test_clip_lasts_narration_plus_tail(self) -> None:
        work = self.base / "w"
        work.mkdir()
        out = self.base / "clip.mp4"
        V.IllustratedAdapter().build(self.request(self.visual()), out, work)
        self.assertEqual(count_video_frames(out), math.ceil(D.clip_seconds(self.visual(), 1.0, 1.0) * 10 - 1e-6))

    def test_clip_with_a_pause_cue(self) -> None:
        v = self.visual(talk="him", pause={"scale": 1.0, "add_s": 0.5, "cue": "Your turn"})
        work = self.base / "w2"
        work.mkdir()
        out = self.base / "pause.mp4"
        V.IllustratedAdapter().build(self.request(v), out, work)
        self.assertEqual(count_video_frames(out), math.ceil(D.clip_seconds(v, 1.0, 1.0) * 10 - 1e-6))

    def test_cache_key_follows_the_still(self) -> None:
        adapter = V.IllustratedAdapter()
        before = adapter.cache_key(self.request(self.visual()))
        shutil.copy(self.base / "lib" / "still.jpg", self.base / "lib" / "copy.jpg")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=red:s=64x36", "-frames:v", "1", str(self.base / "lib" / "still.jpg")], check=True)
        self.assertNotEqual(before, adapter.cache_key(self.request(self.visual())))


class LessonLookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.lib = fake_library(self.base / "lib")
        self.lesson = L.parse_lesson(LESSON)
        self.doc = L.build_scenes(self.lesson, look="illustrated", library=self.lib)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_every_scene_is_an_illustrated_still_of_its_place(self) -> None:
        flat = L.build_scenes(self.lesson)
        self.assertEqual(len(flat["scenes"]), len(self.doc["scenes"]))
        for f, s in zip(flat["scenes"], self.doc["scenes"]):
            v = s["visual"]
            self.assertEqual(v["kind"], "illustrated")
            self.assertEqual(v["talk"], f["visual"]["talk"])
            self.assertEqual(v["name"], L.NAMES[v["talk"]])
            self.assertEqual((v["label"], v["badge"]), (L.AI_LABEL, L.AI_BADGE))
            self.assertEqual(v["caption"], f["visual"]["caption"])
            self.assertEqual(v.get("pause"), f["visual"].get("pause"))
            self.assertEqual(s["narration"], f["narration"])  # same voice track as the flat look
        for key, place in L.PLACES.items():
            for s in (x for x in self.doc["scenes"] if x["id"].startswith(f"{key}-")):
                if key == "listen" and s["id"].startswith("listen-again"):
                    continue
                self.assertRegex(Path(s["visual"]["image"]).name, rf"^[AD]-{place}-", (key, s["id"]))

    def test_each_still_shows_its_speaker_and_a_is_mixed_in(self) -> None:
        styles = []
        for s in self.doc["scenes"]:
            v = s["visual"]
            entry = self.lib.shots[Path(v["image"]).stem]
            self.assertIn(v["talk"], entry["who"], s["id"])
            self.assertEqual(v["focus"], I.focus_for(entry, v["talk"]))
            styles.append(entry["style"])
        self.assertEqual(styles[0], "D")
        self.assertGreater(styles.count("A"), 0)
        self.assertGreater(styles.count("D"), styles.count("A"))

    def test_write_source_copies_only_the_stills_used(self) -> None:
        folder = self.base / "lessons"
        folder.mkdir()
        path = folder / "acme.lesson.md"
        path.write_text(LESSON, encoding="utf-8")  # no characters folder needed for this look
        src = L.write_source(path, self.base / "out", look="illustrated", library=self.lib.folder)
        doc = json.loads(src.scenes.read_text(encoding="utf-8"))
        used = L.stills_used(doc)
        self.assertEqual(sorted(p.name for p in (src.folder / "library").iterdir()), sorted(Path(u).name for u in used))
        self.assertFalse((src.folder / "characters").exists())
        from_dict(doc, src.folder)  # schema + files
        self.assertEqual(json.loads(src.topic.read_text(encoding="utf-8"))["job"], "en-2030-01-02-acme-chibi")

    def test_look_names_and_description(self) -> None:
        self.assertEqual(L.job_name(Path("a.lesson.md"), self.lesson), "en-2030-01-02-a")
        self.assertEqual(L.job_name(Path("a.lesson.md"), self.lesson, "illustrated"), "en-2030-01-02-a-chibi")
        self.assertTrue(L.is_illustrated(self.doc))
        self.assertIn("made with AI", L.description_intro(self.lesson, True))
        self.assertIn("original drawings", L.description_intro(self.lesson, False))
        with self.assertRaisesRegex(L.LessonError, "library"):
            L.build_scenes(self.lesson, look="illustrated")
        with self.assertRaisesRegex(L.LessonError, "unknown look"):
            L.build_scenes(self.lesson, look="sketch")


if __name__ == "__main__":
    unittest.main()
