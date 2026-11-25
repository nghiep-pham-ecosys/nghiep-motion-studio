import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motion_studio import cli  # noqa: E402
from motion_studio.render import (  # noqa: E402
    SAMPLE_RATE,
    RenderOptions,
    ffmpeg_available,
    mux_cmd,
    plan,
    render,
    scene_audio_cmd,
    scene_video_job,
)
from motion_studio.schema import from_dict, load  # noqa: E402

SAMPLE = ROOT / "samples" / "three-scenes" / "scenes.json"
REQUIRE_FFMPEG = os.environ.get("MOTION_STUDIO_REQUIRE_FFMPEG") == "1"
FAST = RenderOptions(preset="ultrafast", crf=30)


def doc_with(scenes: list, fps: int = 30, languages=("en",)) -> dict:
    langs = list(languages)
    return {
        "version": 1,
        "title": {lang: "Acme demo" for lang in langs},
        "languages": langs,
        "output": {"width": 1920, "height": 1080, "fps": fps},
        "scenes": scenes,
    }


def title_scene(sid: str, seconds: float, langs=("en",)) -> dict:
    return {
        "id": sid,
        "narration": {lang: "Some words." for lang in langs},
        "duration_s": seconds,
        "visual": {"kind": "title", "text": {lang: f"Scene {sid}" for lang in langs}},
    }


class PlanTest(unittest.TestCase):
    def test_frames_round_up_and_start_times_add_up(self):
        scenes = from_dict(doc_with([title_scene("a", 1.01), title_scene("b", 2.0)]), Path("."), check_files=False)
        timeline = plan(scenes, "en", RenderOptions())
        self.assertEqual([t.frames for t in timeline], [31, 60])
        self.assertEqual([t.start_frame for t in timeline], [0, 31])
        self.assertAlmostEqual(timeline[1].start_s, 31 / 30)
        self.assertEqual([t.samples for t in timeline], [31 * 1600, 60 * 1600])

    def test_exact_durations_do_not_gain_a_frame(self):
        scenes = from_dict(doc_with([title_scene("a", 0.1), title_scene("b", 3.0)]), Path("."), check_files=False)
        self.assertEqual([t.frames for t in plan(scenes, "en", RenderOptions())], [3, 90])

    def test_samples_never_drift_with_odd_fps(self):
        scenes_list = [title_scene(f"s{i}", 1.0 + i / 7) for i in range(50)]
        scenes = from_dict(doc_with(scenes_list, fps=7), Path("."), check_files=False)
        timeline = plan(scenes, "en", RenderOptions())
        total_frames = sum(t.frames for t in timeline)
        self.assertEqual(sum(t.samples for t in timeline), round(total_frames * SAMPLE_RATE / 7))

    def test_audio_extends_scene_and_adds_tail(self):
        scene = title_scene("a", 1.0)
        scene["audio"] = {"en": "a.wav"}
        scenes = from_dict(doc_with([scene]), Path("."), check_files=False)
        timeline = plan(scenes, "en", RenderOptions(tail_s=0.5), audio_length=lambda p: 2.0)
        self.assertEqual(timeline[0].frames, 75)  # 2.0 s audio + 0.5 s tail at 30 fps

    def test_duration_wins_when_longer_than_audio(self):
        scene = title_scene("a", 5.0)
        scene["audio"] = {"en": "a.wav"}
        scenes = from_dict(doc_with([scene]), Path("."), check_files=False)
        self.assertEqual(plan(scenes, "en", RenderOptions(), audio_length=lambda p: 1.0)[0].frames, 150)

    def test_scene_without_audio_in_this_language_uses_duration(self):
        scene = title_scene("a", 2.0, langs=("en", "vi"))
        scene["audio"] = {"en": "a.wav"}
        scenes = from_dict(doc_with([scene], languages=("en", "vi")), Path("."), check_files=False)
        self.assertEqual(plan(scenes, "vi", RenderOptions(), audio_length=lambda p: 9.0)[0].frames, 60)

    def test_unknown_language(self):
        scenes = from_dict(doc_with([title_scene("a", 1.0)]), Path("."), check_files=False)
        with self.assertRaises(ValueError):
            plan(scenes, "vi", RenderOptions())

    def test_start_past_clip_end_is_rejected(self):
        for loop in (False, True):
            for start in (1.0, 5.0):
                scene = {"id": "c", "narration": {"en": "x"}, "duration_s": 2,
                         "visual": {"kind": "video", "src": "clip.mp4", "start_s": start, "loop": loop}}
                scenes = from_dict(doc_with([scene]), Path("."), check_files=False)
                with self.assertRaisesRegex(ValueError, "at or past the end of clip.mp4"):
                    plan(scenes, "en", RenderOptions(), clip_length=lambda p: 1.0)

    def test_options_are_checked(self):
        for bad in ({"jobs": -1}, {"crf": 60}, {"tail_s": -1}, {"tail_s": float("nan")}):
            with self.assertRaises(ValueError, msg=bad):
                RenderOptions(**bad)


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.scenes = load(SAMPLE)
        self.timeline = plan(self.scenes, "vi", RenderOptions())

    def test_title_card_writes_text_to_a_file(self):
        job = scene_video_job(self.scenes, self.scenes.scenes[0], "vi", self.timeline[0], "v.mp4", RenderOptions())
        cmd = job.cmd
        self.assertEqual(job.files, {"title-0000.txt": "Dây chuyền này\nhoạt động thế nào"})
        vf = cmd[cmd.index("-vf") + 1]
        self.assertIn("textfile=title-0000.txt", vf)
        self.assertIn("expansion=none", vf)
        self.assertEqual(cmd[cmd.index("-frames:v") + 1], "90")

    def test_svg_gets_a_raster_size_and_motion_uses_zoompan(self):
        cmd = scene_video_job(self.scenes, self.scenes.scenes[1], "vi", self.timeline[1], "v.mp4", RenderOptions()).cmd
        self.assertEqual(cmd[cmd.index("-height") + 1], "2160")
        vf = cmd[cmd.index("-vf") + 1]
        self.assertIn("zoompan=", vf)
        self.assertIn("pad=3840:2160", vf, "fit at 2x so zoompan keeps the SVG detail")
        self.assertNotIn("-loop", cmd)

    def test_still_image_loops(self):
        cmd = scene_video_job(self.scenes, self.scenes.scenes[2], "vi", self.timeline[2], "v.mp4", RenderOptions()).cmd
        self.assertIn("-loop", cmd)
        vf = cmd[cmd.index("-vf") + 1]
        self.assertNotIn("zoompan", vf)
        self.assertIn("trunc(iw*sar/2)*2", vf, "non-square pixels are made square first")
        self.assertIn("overlay=", vf, "image is composited over the background")
        self.assertIn("out_color_matrix=bt709", vf)
        self.assertIn("bt709", cmd)

    def test_loop_with_start_cuts_the_tail_once(self):
        scene = {"id": "c", "narration": {"en": "x"}, "duration_s": 2,
                 "visual": {"kind": "video", "src": "clip.mp4", "start_s": 0.5, "loop": True}}
        scenes = from_dict(doc_with([scene]), Path("."), check_files=False)
        timing = plan(scenes, "en", RenderOptions(), clip_length=lambda p: 3.0)[0]
        job = scene_video_job(scenes, scene, "en", timing, "v.mp4", RenderOptions())
        self.assertEqual(len(job.pre), 1)
        self.assertIn("-ss", job.pre[0])
        self.assertNotIn("-ss", job.cmd)
        self.assertEqual(job.cmd[job.cmd.index("-stream_loop") + 3], "loop-0000.mkv")

    def test_silence_when_no_audio(self):
        cmd = scene_audio_cmd(self.scenes.scenes[0], "vi", self.timeline[0], "a.wav", RenderOptions())
        self.assertIn("anullsrc=r=48000:cl=stereo", cmd)
        self.assertIn(f"atrim=end_sample={90 * 1600}", cmd[cmd.index("-af") + 1])

    def test_mux_copies_video_and_tags_language(self):
        cmd = mux_cmd(self.scenes, "vi", Path("/tmp/x.mp4"), RenderOptions())
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "copy")
        self.assertIn("language=vie", cmd)
        self.assertIn("title=Dây chuyền này hoạt động thế nào", cmd)

    def test_font_name_cannot_break_the_filter(self):
        with self.assertRaises(ValueError):
            RenderOptions(font="Evil:fontfile=/etc/passwd")


def ffprobe_json(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


@unittest.skipUnless(ffmpeg_available() or REQUIRE_FFMPEG, "ffmpeg not installed")
class RenderIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def assertStreams(self, path: Path, frames: int, fps: int = 30):
        info = ffprobe_json(path)
        video = [s for s in info["streams"] if s["codec_type"] == "video"]
        audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
        self.assertEqual(len(video), 1)
        self.assertEqual(len(audio), 1)
        v, a = video[0], audio[0]
        self.assertEqual((v["codec_name"], v["width"], v["height"]), ("h264", 1920, 1080))
        self.assertEqual(v["pix_fmt"], "yuv420p")
        self.assertEqual(v["r_frame_rate"], f"{fps}/1")
        self.assertEqual(int(v["nb_frames"]), frames)
        self.assertEqual((a["codec_name"], int(a["sample_rate"]), a["channels"]), ("aac", 48000, 2))
        # audio and video end together (well under the 200 ms drift budget)
        self.assertLess(abs(float(v["duration"]) - float(a["duration"])), 0.05)
        return info

    def test_sample_renders_to_1080p_mp4(self):
        scenes = load(SAMPLE)
        for lang in ("en", "vi"):
            out = self.dir / f"sample.{lang}.mp4"
            result = render(scenes, lang, out, FAST)
            self.assertEqual(result.output, out.resolve())
            self.assertAlmostEqual(result.duration_s, 10.0)
            info = self.assertStreams(out, frames=300)
            self.assertAlmostEqual(float(info["format"]["duration"]), 10.0, delta=0.05)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["sample.en.mp4", "sample.vi.mp4"])

    def test_audio_video_clip_and_motions(self):
        ff = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        # 1.3 s mono narration at 22.05 kHz: resampled, upmixed and padded by the renderer
        subprocess.run(ff + ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=22050:duration=1.3", "line.wav"], cwd=self.dir, check=True)
        # 0.5 s 640x360 clip at 25 fps with its own audio: scaled, padded, frame held, audio dropped
        subprocess.run(
            ff + ["-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=0.5",
                  "-f", "lavfi", "-i", "sine=duration=0.5", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "clip.mp4"],
            cwd=self.dir, check=True,
        )
        subprocess.run(ff + ["-f", "lavfi", "-i", "testsrc2=size=800x600", "-frames:v", "1", "card.png"], cwd=self.dir, check=True)
        scenes_list = [
            {"id": "voice", "narration": {"en": "Hello."}, "audio": {"en": "line.wav"},
             "visual": {"kind": "title", "text": {"en": "Voice line"}}},
            {"id": "clip", "narration": {"en": "A clip."}, "duration_s": 1.0,
             "visual": {"kind": "video", "src": "clip.mp4"}},
            {"id": "loop", "narration": {"en": "A loop."}, "duration_s": 1.2,
             "visual": {"kind": "video", "src": "clip.mp4", "loop": True, "start_s": 0.2}},
        ]
        for motion in ("zoom-in", "zoom-out", "pan-left", "pan-right", "none"):
            scenes_list.append({"id": f"img-{motion}", "narration": {"en": "An image."}, "duration_s": 0.5,
                                "visual": {"kind": "image", "src": "card.png", "motion": motion}})
        scenes = from_dict(doc_with(scenes_list), self.dir)
        out = self.dir / "mixed.mp4"
        result = render(scenes, "en", out, FAST)
        frames = [t.frames for t in result.timeline]
        self.assertEqual(frames[:3], [54, 30, 36])  # 1.3 + 0.5 tail = 1.8 s -> 54 frames
        self.assertStreams(out, frames=sum(frames))

    def test_output_is_tagged_bt709(self):
        scenes = from_dict(doc_with([title_scene("a", 0.5), title_scene("b", 0.5)]), self.dir)
        out = self.dir / "tags.mp4"
        render(scenes, "en", out, FAST)
        v = [s for s in ffprobe_json(out)["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual(
            (v.get("color_space"), v.get("color_primaries"), v.get("color_transfer"), v.get("color_range")),
            ("bt709", "bt709", "bt709", "tv"),
        )

    def pixel(self, path: Path, x: int, y: int, t: float = 0.1) -> tuple:
        raw = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(path),
             "-vf", f"scale=in_color_matrix=bt709:in_range=tv,format=rgb24,crop=2:2:{x}:{y}",
             "-frames:v", "1", "-f", "rawvideo", "-"],
            capture_output=True, check=True,
        ).stdout
        return tuple(raw[:3])

    def test_transparent_images_show_the_background(self):
        (self.dir / "dot.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400">'
            '<circle cx="200" cy="200" r="150" fill="#ff0000"/></svg>', encoding="utf-8")
        scenes_list = [
            {"id": f"m-{m}", "narration": {"en": "x"}, "duration_s": 0.5,
             "visual": {"kind": "image", "src": "dot.svg", "motion": m, "background": "#00ff00"}}
            for m in ("none", "zoom-in")
        ]
        scenes = from_dict(doc_with(scenes_list), self.dir)
        out = self.dir / "alpha.mp4"
        render(scenes, "en", out, FAST)
        for t in (0.1, 0.6):
            corner, centre = self.pixel(out, 100, 100, t), self.pixel(out, 960, 540, t)
            self.assertTrue(corner[1] > 200 and corner[0] < 40, f"corner at {t}s is {corner}, expected green")
            self.assertTrue(centre[0] > 200 and centre[1] < 40, f"centre at {t}s is {centre}, expected red")

    def test_loop_restarts_at_start_s(self):
        # 2 s clip at 25 fps whose brightness encodes the frame number; keyframes every 10 frames.
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=25:d=2",
             "-vf", "geq=lum='N*4':cb=128:cr=128", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0",
             "-g", "10", "-bf", "0", "-pix_fmt", "yuv420p", "num.mp4"],
            cwd=self.dir, check=True,
        )
        scene = {"id": "loop", "narration": {"en": "x"}, "duration_s": 3,
                 "visual": {"kind": "video", "src": "num.mp4", "start_s": 1.5, "loop": True}}
        out = self.dir / "loop.mp4"
        render(from_dict(doc_with([scene], fps=25), self.dir), "en", out, FAST)
        stats = subprocess.run(
            ["ffprobe", "-v", "error", "-f", "lavfi", "-i", f"movie={out.name},signalstats",
             "-show_entries", "frame_tags=lavfi.signalstats.YAVG", "-of", "csv=p=0"],
            cwd=self.dir, capture_output=True, text=True, check=True,
        ).stdout.split()
        stats = [y.strip(",") for y in stats if y.strip(",")]
        # Brightness rises with the source frame number (the exact mapping depends on range conversion).
        levels = [float(y) for y in stats]
        self.assertEqual(len(levels), 75)
        period = 12  # source frames 38..49: start_s 1.5 s at 25 fps up to the end of the 2 s clip
        for k in range(1, period):
            self.assertGreater(levels[k], levels[k - 1], f"first pass not increasing: {levels[:period]}")
        self.assertLess(levels[period], levels[period - 1], "the clip wraps after 12 frames")
        for k in range(len(levels) - period):
            # every pass is the same as the first: restarts at start_s, not at an earlier keyframe
            self.assertAlmostEqual(levels[k + period], levels[k], delta=1.5, msg=f"frame {k + period} differs")

    def test_start_past_clip_end_fails(self):
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=1",
             "-c:v", "libx264", "-preset", "ultrafast", "short.mp4"], cwd=self.dir, check=True)
        scene = {"id": "late", "narration": {"en": "x"}, "duration_s": 2,
                 "visual": {"kind": "video", "src": "short.mp4", "start_s": 5}}
        with self.assertRaisesRegex(ValueError, "past the end"):
            render(from_dict(doc_with([scene]), self.dir), "en", self.dir / "late.mp4", FAST)
        self.assertFalse((self.dir / "late.mp4").exists())

    def test_failed_render_leaves_no_output(self):
        scenes = from_dict(doc_with([title_scene("a", 0.5)]), self.dir)
        out = self.dir / "fail.mp4"
        from motion_studio.render import RenderError

        with self.assertRaises(RenderError):
            render(scenes, "en", out, RenderOptions(preset="no-such-preset"))
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_cli_render_writes_mp4_and_timeline(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["render", str(SAMPLE), "--lang", "vi", "-o", str(self.dir), "--preset", "ultrafast"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertTrue((self.dir / "three-scenes.vi.mp4").is_file())
        timeline = json.loads((self.dir / "three-scenes.vi.timeline.json").read_text(encoding="utf-8"))
        self.assertEqual([s["id"] for s in timeline["scenes"]], ["intro", "pipeline", "robot"])
        self.assertEqual(timeline["duration_s"], 10.0)

    def test_cli_render_from_inside_the_sample_folder_names_outputs_after_it(self):
        old = os.getcwd()
        os.chdir(SAMPLE.parent)
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = cli.main(["render", "scenes.json", "--lang", "en", "-o", str(self.dir), "--preset", "ultrafast"])
        finally:
            os.chdir(old)
        self.assertEqual(code, 0)
        self.assertTrue((self.dir / "three-scenes.en.mp4").is_file())


class CliOptionsTest(unittest.TestCase):
    def run_cli(self, *argv):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:  # argparse usage errors
                code = exc.code
        return code, err.getvalue()

    def test_bad_numbers_are_usage_errors(self):
        for argv in (["--jobs", "-1"], ["--crf", "99"]):
            code, err = self.run_cli("render", str(SAMPLE), *argv)
            self.assertEqual(code, 2, argv)
            self.assertNotIn("Traceback", err)

    def test_bad_font_prints_fail(self):
        code, err = self.run_cli("render", str(SAMPLE), "--font", "Bad:Font")
        self.assertEqual(code, 2)
        self.assertIn("FAIL font name", err)

    def test_unknown_language(self):
        code, err = self.run_cli("render", str(SAMPLE), "--lang", "fr")
        self.assertEqual(code, 2)
        self.assertIn("fr not in", err)

    def test_output_stem(self):
        self.assertEqual(cli.output_stem(SAMPLE), "three-scenes")
        self.assertEqual(cli.output_stem(Path("a/b/demo.json")), "demo")


if __name__ == "__main__":
    unittest.main()
