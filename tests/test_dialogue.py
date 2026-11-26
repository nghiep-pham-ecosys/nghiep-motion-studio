"""Tests for the dialogue visual: the mouth follows its own audio, poses per role, the character sheet, the clip.

The mouth is tested on a synthetic tone burst. The clip tests need ffmpeg and skip without it
(MOTION_STUDIO_REQUIRE_FFMPEG=1, set by `just ci`, turns the skip into a failure).
"""

import array
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motion_studio.pipeline import planned_seconds  # noqa: E402
from motion_studio.render import RenderOptions, count_video_frames, ffmpeg_available, render  # noqa: E402
from motion_studio.schema import ScenesError, from_dict, validate  # noqa: E402
from motion_studio.theme import LIGHT  # noqa: E402
from motion_studio.tts.base import TTSError  # noqa: E402
from motion_studio.tts.kokoro_tts import split_speed  # noqa: E402
from motion_studio.visuals import ADAPTERS, BuildRequest, VisualError  # noqa: E402
from motion_studio.visuals import dialogue as D  # noqa: E402

CHARS = ROOT / "samples" / "characters"
FPS = 30
HAVE_FFMPEG = ffmpeg_available()
REQUIRE_FFMPEG = os.environ.get("MOTION_STUDIO_REQUIRE_FFMPEG") == "1"


def tone_burst(path: Path, before_s: float = 0.5, tone_s: float = 1.0, after_s: float = 0.5, rate: int = 24000,
               amp: float = 0.5) -> None:
    """Silence, a 220 Hz tone, silence: a mono 16-bit WAV."""
    n0, n1, n2 = (round(x * rate) for x in (before_s, tone_s, after_s))
    data = array.array("h", [0] * n0)
    data.extend(int(amp * 32767 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(n1))
    data.extend([0] * n2)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


def visual(talk: str = "her", **extra) -> dict:
    return {"kind": "dialogue", "scenery": "characters/classroom.svg",
            "cast": {"her": "characters/her.svg", "him": "characters/him.svg"}, "talk": talk, **extra}


class MouthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.wav = Path(self.tmp.name) / "burst.wav"
        tone_burst(self.wav)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mouth_opens_only_during_the_burst(self) -> None:
        levels = D.mouth_levels(D.envelope(self.wav, FPS))
        self.assertEqual(len(levels), 60)  # 2 s at 30 fps
        self.assertTrue(all(lv == 0 for lv in levels[:14]), levels[:15])  # 0.0-0.47 s: silence
        self.assertTrue(all(lv == 2 for lv in levels[16:44]), levels[15:45])  # the tone: open
        self.assertTrue(all(lv == 0 for lv in levels[46:]), levels[45:])  # silence again

    def test_quieter_frames_half_open(self) -> None:
        env = [0.0, 0.3, 0.3, 0.05, 0.05, 0.3, 0.3, 0.0, 0.0]
        self.assertEqual(D.mouth_levels(env), [0, 2, 2, 1, 1, 2, 2, 0, 0])

    def test_silence_keeps_the_mouth_shut(self) -> None:
        self.assertEqual(D.mouth_levels([0.0, 0.001, 0.0]), [0, 0, 0])

    def test_single_frame_flicker_is_smoothed(self) -> None:
        self.assertEqual(D.mouth_levels([0.3, 0.3, 0.0, 0.3, 0.3]), [2, 2, 2, 2, 2])

    def test_the_speaker_moves_its_mouth_the_listener_nods(self) -> None:
        levels = D.mouth_levels(D.envelope(self.wav, FPS))
        frames = 90  # the clip is longer than the audio (tail and pause)
        for talk, other in (("her", "him"), ("him", "her")):
            got = D.poses(levels, frames, FPS, talk)
            self.assertEqual([p.mouth for p in got[talk][:60]], levels)
            self.assertTrue(all(p.mouth == 0 for p in got[talk][60:]))  # after the audio: closed
            self.assertTrue(all(p.mouth == 0 for p in got[other]))  # the listener never speaks
            self.assertTrue(all(p.glow for p in got[talk]) and not any(p.glow for p in got[other]))
            self.assertFalse(any(p.nod for p in got[talk]))
            nods = [i for i, p in enumerate(got[other]) if p.nod]
            self.assertTrue(nods and all(15 <= i <= 60 + 15 for i in nods), nods)  # only while the other speaks
            self.assertTrue(any(p.eyes_closed for p in got["her"]) or frames < 4 * FPS)

    def test_runs(self) -> None:
        self.assertEqual(D.runs("aabccc"), [("a", 2), ("b", 1), ("c", 3)])


class TimingTests(unittest.TestCase):
    def test_hold_scales_with_the_line(self) -> None:
        pause = {"scale": 1.2, "add_s": 1.0, "cue": "Your turn"}
        self.assertAlmostEqual(D.hold_seconds(pause, 2.0), 3.4)
        self.assertEqual(D.hold_seconds(None, 2.0), 0.0)

    def test_clip_seconds(self) -> None:
        self.assertAlmostEqual(D.clip_seconds(visual(), 2.0), 2.0 + D.TAIL_S)
        self.assertAlmostEqual(D.clip_seconds(visual(pause={"add_s": 3.0}), 2.0), 2.0 + D.CUE_GAP_S + 3.0)
        self.assertAlmostEqual(D.clip_seconds(visual(), 2.0, target_s=9.0), 9.0)


class CharacterTests(unittest.TestCase):
    def test_committed_characters_have_every_group(self) -> None:
        for name in ("her", "him"):
            D.check_character((CHARS / f"{name}.svg").read_text(encoding="utf-8"))

    def test_missing_group_is_named(self) -> None:
        text = (CHARS / "her.svg").read_text(encoding="utf-8").replace('id="mouth-2"', 'id="mouth-x"')
        with self.assertRaisesRegex(VisualError, "mouth-2"):
            D.check_character(text)

    def test_pose_switches_groups(self) -> None:
        text = (CHARS / "him.svg").read_text(encoding="utf-8")
        out = D.pose_svg(text, D.Pose(mouth=1, eyes_closed=True, nod=True, glow=True))
        self.assertIn('id="mouth-1" display="inline"', out)
        self.assertIn('id="mouth-0" display="none"', out)
        self.assertIn('id="mouth-2" display="none"', out)
        self.assertIn('id="eyes-closed" display="inline"', out)
        self.assertIn('id="eyes-open" display="none"', out)
        self.assertIn('id="glow" display="inline"', out)
        self.assertRegex(out, r'id="head" transform="translate\(0 \d+\)')
        rest = D.pose_svg(out, D.Pose(0, False, False, False))  # poses apply to an already posed SVG too
        self.assertNotIn("transform=", rest.split('id="head"')[1].split(">")[0])
        self.assertEqual(rest.count('display="inline"'), 2)  # eyes-open and mouth-0

    def test_sheet_is_in_step_with_the_characters(self) -> None:
        self.assertEqual((CHARS / "sheet.svg").read_text(encoding="utf-8"), D.character_sheet(CHARS))


class TextTests(unittest.TestCase):
    def test_bold_inside_a_word_and_italics_dropped(self) -> None:
        self.assertEqual(D.rich("post**s**, *judge* **LLM judge**"),
                         [(("post", False), ("s", True), (",", False)), (("judge", False),), (("LLM", True),),
                          (("judge", True),)])

    def test_wrap_keeps_lines_inside_the_width(self) -> None:
        words = D.rich("one two three four five six seven eight nine ten " * 3)
        for line in D.wrap_rich(words, 40, 400):
            width = sum(D._width(w, 40) for w in line) + 40 * 0.32 * (len(line) - 1)
            self.assertLessEqual(width, 400)

    def test_scene_svg_has_labels_caption_and_board(self) -> None:
        scenery = (CHARS / "classroom.svg").read_text(encoding="utf-8")
        svg = D.scene_svg(visual(part="2 · Words", label="AI voice", caption={"text": "A & **B**", "sub": "nghĩa"},
                                 board={"title": "Word 1", "lines": [{"style": "big", "text": "rubric"}]}),
                          scenery, LIGHT, 640, 360)
        for needle in ("2 · Words", "AI voice", "A &amp; ", ">B<", "nghĩa", "Word 1", "rubric"):
            self.assertIn(needle, svg)
        self.assertNotIn("<!--", svg)

    def test_long_board_text_shrinks_to_fit(self) -> None:
        blocks = D._layout([("body", "word " * 400)], 900, 500, LIGHT)
        self.assertLess(blocks[0].size, D.BOARD_STYLES["body"][0])


class SchemaTests(unittest.TestCase):
    def doc(self, v: dict) -> dict:
        return {"version": 1, "title": {"en": "Acme"}, "languages": ["en"],
                "scenes": [{"id": "a", "narration": {"en": "Hello."}, "duration_s": 1, "visual": v}]}

    def test_dialogue_validates(self) -> None:
        full = visual(part="1 · Intro", label="AI voice", caption={"text": "Hello.", "sub": "Xin chào."},
                      board={"title": "T", "lines": [{"style": "bullet", "text": "x"}]},
                      pause={"scale": 1.0, "add_s": 1.0, "cue": "Your turn"})
        self.assertEqual(validate(self.doc(full)), [])

    def test_bad_dialogue_fields(self) -> None:
        self.assertTrue(validate(self.doc(visual(talk="robot"))))
        self.assertTrue(validate(self.doc(visual(pause={"add_s": 500}))))
        bad = visual()
        del bad["cast"]["him"]
        self.assertTrue(validate(self.doc(bad)))

    def test_missing_files_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ScenesError, "cast/her: file not found"):
                from_dict(self.doc(visual()), Path(tmp))

    def test_registered_as_a_generated_kind(self) -> None:
        self.assertIn("dialogue", ADAPTERS)
        self.assertFalse(ADAPTERS["dialogue"].paced)


class KokoroSpeedTests(unittest.TestCase):
    def test_split_speed(self) -> None:
        self.assertEqual(split_speed("af_heart@0.8"), ("af_heart", 0.8))
        self.assertEqual(split_speed("am_michael"), ("am_michael", 1.0))
        for bad in ("af_heart@fast", "af_heart@3"):
            with self.assertRaises(TTSError):
                split_speed(bad)


@unittest.skipUnless(HAVE_FFMPEG or REQUIRE_FFMPEG, "ffmpeg is not installed")
class ClipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        shutil.copytree(CHARS, self.base / "characters")
        self.wav = self.base / "line.wav"
        tone_burst(self.wav, 0.2, 0.6, 0.2)  # 1.0 s

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def request(self, v: dict, audio: Path | None) -> BuildRequest:
        return BuildRequest(scene_id="s", lang="en", visual=v, base_dir=self.base, width=320, height=180, fps=10,
                            background=LIGHT.background, target_s=1.5, theme=LIGHT, audio=audio)

    def test_clip_lasts_narration_plus_pause(self) -> None:
        v = visual(talk="him", caption={"text": "Hello."}, pause={"scale": 1.0, "add_s": 0.5, "cue": "Your turn"})
        cache = self.base / "cache"
        cache.mkdir()
        work = cache / "w"
        work.mkdir()
        out = self.base / "clip.mp4"
        D.DialogueAdapter().build(self.request(v, self.wav), out, work)
        want = math.ceil(D.clip_seconds(v, 1.0, 1.5) * 10 - 1e-6)
        self.assertEqual(count_video_frames(out), want)
        self.assertTrue(list((cache / "dialogue-sprites").glob("him-*.png")))  # poses are cached for later scenes

    def test_cache_key_follows_the_audio(self) -> None:
        adapter = D.DialogueAdapter()
        other = self.base / "other.wav"
        tone_burst(other, 0.1, 0.6, 0.3)
        self.assertNotEqual(adapter.cache_key(self.request(visual(), self.wav)),
                            adapter.cache_key(self.request(visual(), other)))

    def test_render_stretches_the_scene_and_plan_knows_it(self) -> None:
        audio = self.base / "audio"
        audio.mkdir()
        shutil.copy(self.wav, audio / "a.en.wav")
        pause = {"scale": 1.0, "add_s": 1.0, "cue": "Your turn"}
        doc = {"version": 1, "title": {"en": "Acme"}, "languages": ["en"],
               "output": {"width": 320, "height": 180, "fps": 10},
               "scenes": [{"id": "a", "narration": {"en": "Hello."}, "duration_s": 1, "audio": {"en": "audio/a.en.wav"},
                           "visual": visual(pause=pause, caption={"text": "Hello."})}]}
        (self.base / "scenes.json").write_text(json.dumps(doc), encoding="utf-8")
        scenes = from_dict(doc, self.base)
        expected = 1.0 + D.CUE_GAP_S + 2.0
        self.assertAlmostEqual(planned_seconds(scenes, "en"), expected, places=2)
        opts = RenderOptions(preset="ultrafast", crf=30, visuals_dir=self.base / "visuals", temp_dir=self.base,
                             normalize_audio=False)
        result = render(scenes, "en", self.base / "out.mp4", opts)
        self.assertAlmostEqual(result.duration_s, expected, delta=0.2)


if __name__ == "__main__":
    unittest.main()
