"""Pipeline tests without camera hardware. python3 -m unittest -v test_recorder.py"""
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import continuous_video_gui as recorder
from check_recording import inspect


def synthetic(s, realtime=False):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if realtime:
        cmd.append("-re")
    return cmd + ["-f", "lavfi", "-i", f"testsrc2=size=320x240:rate={s.fps}",
                  "-t", str(s.duration_minutes * 60), "-c:v", "libx264", "-preset", "ultrafast",
                  "-tune", "zerolatency", "-g", str(s.fps), "-sc_threshold", "0", "-f", "mpegts", "pipe:1"]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = recorder.Settings(output=self.tmp.name, date="20261012", duration_minutes=10/60, segment_minutes=2/60)

    def run_record(self, stop=None, command=None):
        with patch.object(recorder.shutil, "which", return_value="available"), patch.object(recorder, "camera_command", command or synthetic):
            return recorder.record(self.s, stop or threading.Event(), lambda text: None)

    def test_frame_retention_across_five_mp4_segments(self):
        folder = self.run_record()
        self.assertEqual(len(list(folder.glob("*.mp4"))), 5)
        self.assertEqual(inspect(folder), 250)
        self.assertTrue((folder / "KO_20261012_trial1_001.mp4").exists())
        self.assertEqual(json.loads((folder / "recording.json").read_text())["status"], "completed")

    def test_collision_preserves_previous_data(self):
        folder = Path(self.tmp.name) / self.s.prefix()
        folder.mkdir()
        sentinel = folder / "sentinel.txt"
        sentinel.write_text("keep")
        with self.assertRaises(FileExistsError):
            self.run_record()
        self.assertEqual(sentinel.read_text(), "keep")

    def test_stop_finalizes_playable_file(self):
        stop = threading.Event()
        timer = threading.Timer(2.5, stop.set)
        timer.start()
        try:
            folder = self.run_record(stop, lambda s: synthetic(s, realtime=True))
        finally:
            timer.cancel()
        self.assertGreater(inspect(folder), 0)
        self.assertEqual(json.loads((folder / "recording.json").read_text())["status"], "stopped")

    def test_camera_failure_is_reported(self):
        with self.assertRaises(RuntimeError):
            self.run_record(command=lambda s: ["python3", "-c", "raise SystemExit(2)"])
        metadata = json.loads((Path(self.tmp.name) / self.s.prefix() / "recording.json").read_text())
        self.assertEqual(metadata["status"], "failed")
        self.assertIsNotNone(metadata["camera_exit_code"])
        self.assertIsNotNone(metadata["ffmpeg_exit_code"])

    def test_writer_failure_stops_camera(self):
        with patch.object(recorder, "segment_command", lambda s, folder: ["python3", "-c", "raise SystemExit(2)"]):
            with self.assertRaises(RuntimeError):
                self.run_record(command=lambda s: synthetic(s, realtime=True))
        metadata = json.loads((Path(self.tmp.name) / self.s.prefix() / "recording.json").read_text())
        self.assertEqual(metadata["status"], "failed")
        self.assertIsNotNone(metadata["camera_exit_code"])

    def test_filename_and_validation(self):
        self.assertEqual(self.s.prefix(), "KO_20261012_trial1")
        self.s.duration_minutes = float("nan")
        with self.assertRaises(ValueError):
            self.s.validate()


if __name__ == "__main__":
    unittest.main()
