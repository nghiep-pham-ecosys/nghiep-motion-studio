"""stage-upload and finish-upload: gate check, metadata, the draft release (through a fake gh), the upload record.

No network: every gh call goes to FakeGh, and the work dir is a temp folder.
"""

import datetime as dt
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motion_studio import cli, publish, workdir  # noqa: E402
from motion_studio.gate import Limits  # noqa: E402
from motion_studio.publish import GATE_CHECKS, Gh, PublishError  # noqa: E402

NOW = dt.datetime(2026, 11, 26, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=7)))
WHEN = "2026-12-01T19:00:00+07:00"
EN_PLAYLIST = "Process Simulation Lab — Python from scratch"
VI_PLAYLIST = "Mô phỏng quá trình hoá học bằng Python"
LESSON_PLAYLIST = "English for engineers — learn with me"
CHAPTERS = [("intro", "Intro", 0.0, 40.0), ("why", "Why it matters", 40.0, 300.0), ("how", "How it works", 340.0, 400.0)]


class FakeGh:
    """Answers like gh does for releases and runs; keeps draft releases in memory."""

    def __init__(self, releases=None, runs=None, result=None):
        self.releases = releases if releases is not None else {}
        self.runs, self.result, self.cmds = runs or [], result, []

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        args = cmd[1:]
        ok = lambda out="": subprocess.CompletedProcess(cmd, 0, out, "")  # noqa: E731
        if args[:2] == ["release", "view"]:
            rel = self.releases.get(args[2])
            if rel is None:
                return subprocess.CompletedProcess(cmd, 1, "", "release not found\n")
            return ok(json.dumps({"isDraft": rel["isDraft"], "assets": [{"name": n} for n in rel["assets"]]}))
        if args[:2] == ["release", "create"]:
            assert "--draft" in args, "a release is only ever created as a draft"
            self.releases[args[2]] = {"isDraft": True, "assets": []}
            return ok()
        if args[:2] == ["release", "upload"]:
            files = args[3:args.index("--repo")]
            for f in files:
                assert Path(f).is_file(), f
                self.releases[args[2]]["assets"].append(Path(f).name)
                if Path(f).name == "manifest.json":
                    self.releases[args[2]]["manifest"] = json.loads(Path(f).read_text())
            return ok()
        if args[:2] == ["run", "list"]:
            return ok(json.dumps(self.runs))
        if args[:2] == ["run", "download"]:
            out = Path(args[args.index("--dir") + 1])
            (out / "result.json").write_text(json.dumps(self.result))
            return ok()
        raise AssertionError(f"unexpected gh call {cmd}")


def scenes_doc():
    return {"version": 1, "title": {"en": "RAG explained"}, "languages": ["en"],
            "scenes": [{"id": sid, "chapter": {"en": name}, "narration": {"en": f"About {name}."},
                        "duration_s": dur, "visual": {"kind": "title", "text": {"en": name}}}
                       for sid, name, _, dur in CHAPTERS]}


def timeline_doc():
    return {"output": "demo.en.mp4", "lang": "en", "width": 1920, "height": 1080, "fps": 30, "duration_s": 740.0,
            "scenes": [{"id": sid, "index": i, "start_frame": int(start * 30), "frames": int(dur * 30),
                        "start_s": start, "duration_s": dur, "samples": int(dur * 48000)}
                       for i, (sid, _, start, dur) in enumerate(CHAPTERS)]}


def gate_doc(video: Path, ok=True, limits=None, checks=GATE_CHECKS):
    return {"video": str(video), "lang": "en", "ok": ok, "limits": limits or asdict(Limits()),
            "checks": [{"name": n, "ok": ok, "summary": "", "details": {}} for n in checks]}


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "work"
        self.job = self.root / "demo"
        self.job.mkdir(parents=True)
        env = mock.patch.dict(os.environ, {workdir.WORK_DIR_ENV: str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        for name in (workdir.CLOUD_COPY_ENV, publish.REPO_ENV, publish.GH_ENV):
            os.environ.pop(name, None)
        (self.job / workdir.JOB_MARKER).touch()
        self.scenes = self.job / "scenes.json"
        self.scenes.write_text(json.dumps(scenes_doc()))
        self.video = self.job / "demo.en.mp4"
        self.video.write_bytes(b"\x00" * 4096)
        (self.job / "demo.en.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nHi\n")
        (self.job / "demo.en.timeline.json").write_text(json.dumps(timeline_doc()))
        self.write_gate(gate_doc(self.video))

    def write_gate(self, doc, older=False):
        gate = self.job / "demo.en.gate.json"
        gate.write_text(json.dumps(doc))
        t = self.video.stat().st_mtime + (-100 if older else 1)
        os.utime(gate, (t, t))

    def make_shorts(self, loudness_ok=True):
        folder = self.job / "shorts"
        folder.mkdir()
        clips = []
        for n, chapter in enumerate(["Why it matters", None], 1):
            (folder / f"demo-{n}.en.mp4").write_bytes(b"\x00" * 1024)
            (folder / f"demo-{n}.en.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nHi\n")
            t = self.video.stat().st_mtime + 5
            os.utime(folder / f"demo-{n}.en.mp4", (t, t))
            clips.append({"path": f"shorts/demo-{n}.en.mp4", "chapter": chapter, "loudness_ok": loudness_ok})
        (folder / "demo.en.shorts.json").write_text(json.dumps({"wanted": 2, "found": 2, "clips": clips}))

    def stage(self, fake=None, **kw):
        fake = fake or FakeGh()
        staged = publish.stage(self.video, self.scenes, kw.pop("publish_at", WHEN), gh=Gh(binary="gh", run=fake),
                               now=NOW, **kw)
        return staged, fake

    # --- stage --------------------------------------------------------------------------------------------------

    def test_stage_puts_the_batch_on_a_draft_release_with_chapters_and_the_ai_label(self):
        staged, fake = self.stage(tags=["rag", "llm"])
        verbs = [c[1:3] for c in fake.cmds]
        self.assertEqual(verbs, [["release", "view"], ["release", "create"], ["release", "view"],
                                 ["release", "upload"], ["release", "upload"]])
        self.assertTrue(all("--repo" in c and publish.DEFAULT_REPO in c for c in fake.cmds))
        rel = fake.releases["yt-demo-en"]
        self.assertTrue(rel["isDraft"])
        self.assertEqual(rel["assets"], ["demo.en.mp4", "demo.en.srt", "manifest.json"])
        item = rel["manifest"]["items"][0]
        self.assertEqual(rel["manifest"]["job"], "demo")
        self.assertEqual((item["title"], item["language"], item["publish_at"], item["kind"]),
                         ("RAG explained", "en", WHEN, "video"))
        self.assertIn("Chapters\n0:00 Intro\n0:40 Why it matters\n5:40 How it works", item["description"])
        self.assertIn("Made with AI", item["description"])
        self.assertEqual(item["tags"], ["rag", "llm"])
        record = json.loads(staged.record.read_text())
        self.assertEqual((record["tag"], record["status"], staged.record.name), ("yt-demo-en", "staged", "demo.en.staged.json"))
        self.assertFalse(list(self.job.glob(".stage-*")), "the temporary manifest folder is removed")

    def test_stage_refuses_without_a_passing_fresh_gate_and_calls_nothing(self):
        cases = {
            "failed": (gate_doc(self.video, ok=False), False, "the gate failed"),
            "stale": (gate_doc(self.video), True, "older than the video"),
            "limits": (gate_doc(self.video, limits={**asdict(Limits()), "min_duration_s": 1.0}), False, "changed limits"),
            "missing check": (gate_doc(self.video, checks=GATE_CHECKS[:-1]), False, "fact-check"),
            "other video": (gate_doc(self.job / "other.en.mp4"), False, "not demo.en.mp4"),
        }
        for name, (doc, older, message) in cases.items():
            with self.subTest(name):
                self.write_gate(doc, older)
                fake = FakeGh()
                with self.assertRaisesRegex(PublishError, message):
                    self.stage(fake)
                self.assertEqual(fake.cmds, [])
        (self.job / "demo.en.gate.json").unlink()
        with self.assertRaisesRegex(PublishError, "run `motion-studio gate` first"):
            self.stage()

    def test_stage_accepts_changed_gate_limits_only_when_asked(self):
        self.write_gate(gate_doc(self.video, limits={**asdict(Limits()), "min_duration_s": 1.0}))
        staged, _ = self.stage(allow_gate_limits=True)
        self.assertEqual(staged.tag, "yt-demo-en")

    def test_gate_accepts_the_topic_files_duration_window_only(self):
        lesson = {**asdict(Limits()), "min_duration_s": 1200.0, "max_duration_s": 1800.0}
        self.write_gate(gate_doc(self.video, limits=lesson))
        with self.assertRaisesRegex(PublishError, "changed limits"):
            publish.check_gate(self.video)
        topic = self.job / "topic.json"
        topic.write_text(json.dumps({"gate": {"min_s": 1200, "max_s": 1800}}), encoding="utf-8")
        self.assertEqual(publish.topic_window(topic), (1200.0, 1800.0))
        publish.check_gate(self.video, window=publish.topic_window(topic))
        self.write_gate(gate_doc(self.video, limits={**lesson, "target_lufs": -10.0}))
        with self.assertRaisesRegex(PublishError, "changed limits"):
            publish.check_gate(self.video, window=(1200.0, 1800.0))
        topic.write_text(json.dumps({"job": "x"}), encoding="utf-8")
        self.assertIsNone(publish.topic_window(topic))
        self.assertIsNone(publish.topic_window(None))

    def test_stage_refuses_a_past_time_a_published_release_and_a_started_upload(self):
        with self.assertRaisesRegex(PublishError, "in the past"):
            self.stage(publish_at="2026-11-25T19:00:00+07:00")
        with self.assertRaisesRegex(PublishError, "needs a UTC offset"):
            self.stage(publish_at="2026-12-01T19:00:00")
        public = FakeGh(releases={"yt-demo-en": {"isDraft": False, "assets": []}})
        with self.assertRaisesRegex(PublishError, "not a draft"):
            self.stage(public)
        self.assertFalse(any(c[1:3] == ["release", "upload"] for c in public.cmds))
        started = FakeGh(releases={"yt-demo-en": {"isDraft": True, "assets": ["result.json"]}})
        with self.assertRaisesRegex(PublishError, "an upload of it has started"):
            self.stage(started)
        self.assertFalse(any(c[1:3] == ["release", "upload"] for c in started.cmds))

    def test_stage_reuses_an_existing_draft(self):
        fake = FakeGh(releases={"yt-demo-en": {"isDraft": True, "assets": ["demo.en.mp4"]}})
        self.stage(fake)
        self.assertNotIn(["release", "create"], [c[1:3] for c in fake.cmds])
        self.assertTrue(all("--clobber" in c for c in fake.cmds if c[1:3] == ["release", "upload"]))

    def test_stage_with_shorts_spaces_them_a_day_apart(self):
        self.make_shorts()
        staged, fake = self.stage(shorts=True)
        items = fake.releases["yt-demo-en"]["manifest"]["items"]
        self.assertEqual([i["key"] for i in items], ["demo.en", "demo-1.en", "demo-2.en"])
        self.assertEqual([i["title"] for i in items[1:]], ["Why it matters #Shorts", "RAG explained #Shorts"])
        self.assertEqual([i["publish_at"] for i in items[1:]], ["2026-12-02T19:00:00+07:00", "2026-12-03T19:00:00+07:00"])
        self.assertEqual(items[1]["path"], "shorts/demo-1.en.mp4")
        self.assertTrue(all("Made with AI" in i["description"] for i in items))
        self.assertIn("demo-2.en.srt", fake.releases["yt-demo-en"]["assets"])
        self.assertEqual(len(staged.files), 6)

    def test_stage_refuses_shorts_that_failed_their_loudness_check(self):
        self.make_shorts(loudness_ok=False)
        with self.assertRaisesRegex(PublishError, "did not pass"):
            self.stage(shorts=True)

    def test_burned_or_loose_videos_are_refused(self):
        burned = self.job / "demo.en.burned.mp4"
        burned.write_bytes(b"\x00")
        with self.assertRaisesRegex(PublishError, "clean"):
            publish.video_parts(burned)
        loose = Path(self.tmp.name) / "demo.en.mp4"
        loose.write_bytes(b"\x00")
        with self.assertRaisesRegex(PublishError, "job folder"):
            publish.video_parts(loose)

    # --- chapters and text ------------------------------------------------------------------------------------------

    def test_chapters_follow_youtube_rules(self):
        doc, tl = scenes_doc(), timeline_doc()
        self.assertEqual(publish.chapters(doc, tl, "en", "T"), [(0, "Intro"), (40, "Why it matters"), (340, "How it works")])
        del doc["scenes"][0]["chapter"]
        self.assertEqual(publish.chapters(doc, tl, "en", "T")[0], (0, "T"))  # the first chapter starts at 0:00
        doc = scenes_doc()
        doc["scenes"][2]["chapter"] = doc["scenes"][1]["chapter"]
        with self.assertRaisesRegex(PublishError, "only from 3"):
            publish.chapters(doc, tl, "en", "T")
        tl["scenes"][1]["start_s"] = 35.0
        tl["scenes"][2]["start_s"] = 41.0
        with self.assertRaisesRegex(PublishError, "shorter than 10 s"):
            publish.chapters(scenes_doc(), tl, "en", "T")
        self.assertEqual([publish.timestamp(s) for s in (0, 75.9, 3725)], ["0:00", "1:15", "1:02:05"])

    def test_text_limits(self):
        with self.assertRaisesRegex(PublishError, "title"):
            publish.check_text("x" * 101, "d", [], "v")
        with self.assertRaisesRegex(PublishError, "description"):
            publish.check_text("t", "<b>", [], "v")
        with self.assertRaisesRegex(PublishError, "tags"):
            publish.check_text("t", "d", ["x" * 501], "v")

    # --- finish -----------------------------------------------------------------------------------------------------

    def result_for(self, staged, **change):
        items = {i["key"]: {"path": i["path"], "video_id": f"VID{n:08d}", "url": "https://www.youtube.com/watch?v=x",
                            "publish_at": "2026-12-01T12:00:00.000Z", "captions": True}
                 for n, i in enumerate(staged.manifest["items"], 1)}
        return {"version": 1, "tag": staged.tag, "job": "demo", "complete": True, "items": items, **change}

    def test_finish_writes_the_upload_record_and_cleans_the_job(self):
        staged, _ = self.stage()
        result = Path(self.tmp.name) / "result.json"
        result.write_text(json.dumps(self.result_for(staged)))
        done = publish.finish(staged.record, gh=Gh(run=FakeGh()), result_file=result)
        record = json.loads((self.job / "upload.json").read_text())
        self.assertEqual((record["version"], record["status"], record["job"]), (1, "uploaded", "demo"))
        self.assertEqual(record["files"][0]["path"], "demo.en.mp4")
        self.assertEqual(record["files"][0]["remote_id"], "VID00000001")
        self.assertIsNotNone(done.cleaned)
        self.assertFalse(self.video.exists())
        self.assertTrue((self.job / "demo.en.srt").exists() and (self.job / "scenes.json").exists())
        self.assertIn("cleaned", json.loads((self.job / "upload.json").read_text()))

    def test_finish_keeps_videos_that_are_not_uploaded_yet(self):
        staged, _ = self.stage()
        (self.job / "demo.vi.mp4").write_bytes(b"\x00" * 10)
        result = Path(self.tmp.name) / "result.json"
        result.write_text(json.dumps(self.result_for(staged)))
        done = publish.finish(staged.record, gh=Gh(run=FakeGh()), result_file=result)
        self.assertEqual(done.waiting, ["demo.vi.mp4"])
        self.assertIsNone(done.cleaned)
        self.assertTrue(self.video.exists())
        workdir.read_record(self.job / "upload.json", self.job)  # the record is valid for clean --after-upload

    def test_finish_merges_an_earlier_record(self):
        (self.job / "upload.json").write_text(json.dumps({"version": 1, "status": "uploaded", "job": "demo",
                                                          "files": [{"path": "demo.vi.mp4", "remote_id": "OLDVID01"}]}))
        staged, _ = self.stage()
        result = Path(self.tmp.name) / "result.json"
        result.write_text(json.dumps(self.result_for(staged)))
        done = publish.finish(staged.record, gh=Gh(run=FakeGh()), result_file=result, clean=False)
        paths = [f["path"] for f in json.loads(done.record.read_text())["files"]]
        self.assertEqual(paths, ["demo.en.mp4", "demo.vi.mp4"])
        self.assertTrue(self.video.exists())

    def test_finish_reads_the_artifact_of_the_newest_successful_live_run(self):
        staged, _ = self.stage()
        runs = [{"databaseId": 9, "displayTitle": "youtube upload dry run yt-demo-en", "status": "completed", "conclusion": "success"},
                {"databaseId": 8, "displayTitle": "youtube upload live yt-demo-en", "status": "completed", "conclusion": "success"},
                {"databaseId": 7, "displayTitle": "youtube upload live yt-demo-en", "status": "completed", "conclusion": "failure"}]
        fake = FakeGh(runs=runs, result=self.result_for(staged))
        publish.finish(staged.record, gh=Gh(run=fake), clean=False)
        download = [c for c in fake.cmds if c[1:3] == ["run", "download"]][0]
        self.assertEqual(download[3], "8")
        self.assertIn("youtube-upload-yt-demo-en", download)
        none = FakeGh(runs=runs[2:], result=self.result_for(staged))
        with self.assertRaisesRegex(PublishError, "ended failure"):
            publish.finish(staged.record, gh=Gh(run=none), clean=False)

    def test_finish_refuses_a_result_that_does_not_match(self):
        staged, _ = self.stage()
        result = Path(self.tmp.name) / "result.json"
        bad = {
            "incomplete": (self.result_for(staged, complete=False), "not complete"),
            "other tag": (self.result_for(staged, tag="yt-other-en"), "not 'yt-demo-en'"),
            "no id": (self.result_for(staged, items={"demo.en": {"path": "demo.en.mp4"}}), "no video id"),
        }
        for name, (doc, message) in bad.items():
            with self.subTest(name):
                result.write_text(json.dumps(doc))
                with self.assertRaisesRegex(PublishError, message):
                    publish.finish(staged.record, gh=Gh(run=FakeGh()), result_file=result)
                self.assertFalse((self.job / "upload.json").exists())
                self.assertTrue(self.video.exists())

    # --- CLI --------------------------------------------------------------------------------------------------------

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cli_dry_run_prints_the_manifest_and_calls_nothing(self):
        with mock.patch.object(publish.Gh, "__call__", side_effect=AssertionError("no gh call in a dry run")):
            code, out, _ = self.run_cli("stage-upload", str(self.video), "--scenes", str(self.scenes),
                                        "--publish-at", "2099-12-01T19:00:00+07:00", "--tags", "rag, llm", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn('"tag": "yt-demo-en"', out)
        self.assertIn("nothing staged", out)
        self.assertFalse((self.job / "demo.en.staged.json").exists())

    def test_cli_refusal_and_finish(self):
        self.write_gate(gate_doc(self.video, ok=False))
        code, _, err = self.run_cli("stage-upload", str(self.video), "--scenes", str(self.scenes),
                                    "--publish-at", "2099-12-01T19:00:00+07:00", "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("FAIL", err)
        self.write_gate(gate_doc(self.video))
        staged, _ = self.stage()
        result = Path(self.tmp.name) / "result.json"
        result.write_text(json.dumps(self.result_for(staged)))
        code, out, _ = self.run_cli("finish-upload", str(staged.record), "--result", str(result))
        self.assertEqual(code, 0, out)
        self.assertIn("demo.en.mp4 -> VID00000001", out)
        self.assertIn("removed", out)

    # --- playlist: --playlist, the topic file's preset, the language's preset ------------------------------------

    def write_topic(self, name="demo.topic.json", **extra):
        doc = {"version": 1, "job": "demo", "scenes": "scenes.json", "factcheck": "factcheck.md",
               "cast": {"en": {"preset": "male-male"}}, **extra}
        path = Path(self.tmp.name) / "topics" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def write_voices(self, folder: Path, playlists):
        doc = json.loads((ROOT / "voices.json").read_text(encoding="utf-8"))
        if playlists is None:
            doc.pop("playlists")
        else:
            doc["playlists"] = playlists
        folder.mkdir(exist_ok=True)
        (folder / "voices.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return folder / "voices.json"

    def test_the_language_preset_of_the_checkout_is_the_default_for_the_video_and_its_shorts(self):
        self.make_shorts()
        staged, fake = self.stage(shorts=True)
        items = fake.releases["yt-demo-en"]["manifest"]["items"]
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertEqual(set(item["playlist"]), {"title", "description", "privacy"})
            self.assertEqual((item["playlist"]["title"], item["playlist"]["privacy"]), (EN_PLAYLIST, "public"))
        self.assertEqual(staged.manifest["items"][0]["playlist"]["title"], EN_PLAYLIST)

    def test_vietnamese_default_and_the_named_lesson_preset(self):
        block, where = publish.choose_playlist("vi", "demo", self.scenes)
        self.assertEqual(block["title"], VI_PLAYLIST)
        self.assertIn("default for vi", where)
        voices = json.loads((ROOT / "voices.json").read_text(encoding="utf-8"))
        self.assertEqual(voices["playlists"]["presets"]["english-for-engineers"]["title"], LESSON_PLAYLIST)

    def test_the_flag_wins_and_keeps_a_matching_preset_description(self):
        topic = self.write_topic(playlist="english-for-engineers")
        tag, manifest, _ = publish.build(self.video, self.scenes, WHEN, now=NOW, playlist="My own list", topic=topic)
        self.assertEqual(manifest["items"][0]["playlist"], {"title": "My own list", "description": "", "privacy": "public"})
        _, manifest, _ = publish.build(self.video, self.scenes, WHEN, now=NOW, playlist=f" {VI_PLAYLIST} ")
        block = manifest["items"][0]["playlist"]
        self.assertEqual(block["title"], VI_PLAYLIST)
        self.assertTrue(block["description"])
        with self.assertRaisesRegex(PublishError, "title must be"):
            publish.build(self.video, self.scenes, WHEN, now=NOW, playlist="a <b>")

    def test_the_topic_file_picks_a_preset(self):
        topic = self.write_topic(playlist="english-for-engineers")
        self.make_shorts()
        staged, fake = self.stage(shorts=True, topic=topic)
        titles = {i["playlist"]["title"] for i in fake.releases["yt-demo-en"]["manifest"]["items"]}
        self.assertEqual(titles, {LESSON_PLAYLIST})

    def test_a_topic_without_playlist_uses_the_language_preset_of_its_own_voices_config(self):
        voices = self.write_voices(Path(self.tmp.name) / "topics", {
            "by_language": {"en": "mine"}, "presets": {"mine": {"title": "Topic voices list", "privacy": "unlisted"}}})
        topic = self.write_topic()
        self.assertEqual(voices.parent, topic.parent)
        block, where = publish.choose_playlist("en", "demo", self.scenes, topic=topic)
        self.assertEqual(block, {"title": "Topic voices list", "description": "", "privacy": "unlisted"})
        self.assertIn("default for en (mine)", where)

    def test_topic_file_refusals(self):
        with self.assertRaisesRegex(PublishError, "not 'demo'"):
            publish.choose_playlist("en", "demo", self.scenes, topic=self.write_topic(job="other"))
        with self.assertRaisesRegex(PublishError, "preset 'nope'"):
            publish.choose_playlist("en", "demo", self.scenes, topic=self.write_topic(playlist="nope"))
        with self.assertRaisesRegex(PublishError, "is invalid"):
            publish.choose_playlist("en", "demo", self.scenes, topic=self.write_topic(playlist="Not A Preset Name"))
        fake = FakeGh()
        with self.assertRaisesRegex(PublishError, "preset 'nope'"):
            self.stage(fake, topic=self.write_topic(playlist="nope"))
        self.assertEqual(fake.cmds, [], "a bad playlist choice stops before any gh call")

    def test_no_playlist_when_no_voices_config_sets_one(self):
        with mock.patch.object(publish, "REPO_VOICES", Path(self.tmp.name) / "missing.json"):
            _, manifest, _ = publish.build(self.video, self.scenes, WHEN, now=NOW)
            self.assertNotIn("playlist", manifest["items"][0])
            self.write_voices(self.job, None)  # the nearest voices.json above the scenes file, without playlists
            block, where = publish.choose_playlist("en", "demo", self.scenes)
        self.assertIsNone(block)
        self.assertIn("no playlist for en", where)

    def test_bad_playlist_presets_are_refused_by_the_schema(self):
        for preset in ({"title": " padded"}, {"title": "x" * 151}, {"title": "ok", "privacy": "secret"},
                       {"title": "ok", "id": "x"}):
            with self.subTest(preset):
                self.write_voices(self.job, {"presets": {"bad": preset}, "by_language": {"en": "bad"}})
                with self.assertRaisesRegex(PublishError, "is invalid"):
                    publish.choose_playlist("en", "demo", self.scenes)

    def test_cli_playlist_flag_and_default_line(self):
        with mock.patch.object(publish.Gh, "__call__", side_effect=AssertionError("no gh call in a dry run")):
            code, out, _ = self.run_cli("stage-upload", str(self.video), "--scenes", str(self.scenes),
                                        "--publish-at", "2099-12-01T19:00:00+07:00", "--playlist", "My own list",
                                        "--dry-run")
            self.assertEqual(code, 0, out)
            self.assertIn('"playlist": {', out)
            self.assertIn("OK   playlist 'My own list' (public) for every item", out)
            topic = self.write_topic(playlist="english-for-engineers")
            code, out, _ = self.run_cli("stage-upload", str(self.video), "--scenes", str(self.scenes),
                                        "--publish-at", "2099-12-01T19:00:00+07:00", "--topic", str(topic), "--dry-run")
            self.assertEqual(code, 0, out)
            self.assertIn(f"OK   playlist {LESSON_PLAYLIST!r}", out)


if __name__ == "__main__":
    unittest.main()
