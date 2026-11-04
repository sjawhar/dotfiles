#!/usr/bin/env python3
"""Box loop filesystems alert their session and SRE, not just the host filesystem."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "disk-fill-watch"
loader = importlib.machinery.SourceFileLoader("disk_fill_watch_boxes", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
WATCH = importlib.util.module_from_spec(spec)
loader.exec_module(WATCH)
SID = "00000000-0000-0000-0000-000000000001"


class FakeEnvoy:
    def __init__(self):
        self.sent = []

    def send(self, target, text):
        self.sent.append((target, text))
        return True


class BoxWatch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = WATCH.State(self.root / "state.json")
        self.addCleanup(self.state.close)
        self.envoy = FakeEnvoy()
        self.box = {"name": "agentbox-scratch", "home": str(self.root / "home"),
                    "image": str(self.root / "disks/agentbox-scratch.img")}
        self.options = WATCH.build_parser().parse_args(["--state", str(self.root / "state.json"), "--print-alert"])

    def check(self, free=10, dry_run=False):
        self.options.dry_run = dry_run
        with patch.object(WATCH, "running_box_homes", return_value=[self.box]), \
             patch.object(WATCH, "free_bytes", return_value=(free * WATCH.GIB, 100 * WATCH.GIB)), \
             patch.object(WATCH, "box_disk_dirs", return_value="27 GB ~/.cache; 2 GB /tmp"), \
             patch.object(WATCH, "box_disk_session", return_value=SID), \
             contextlib.redirect_stdout(io.StringIO()) as out, patch.object(WATCH, "LOG_STREAM", out):
            WATCH.check_agentboxes(self.options, self.state, self.envoy, time.time() + 90)
        return out.getvalue()

    def test_at_90_percent_both_owners_hear_once_and_clear_rearms(self):
        self.check()
        self.assertEqual({x[0] for x in self.envoy.sent}, {SID, WATCH.BOX_HEALTH_SRE})
        for _, text in self.envoy.sent:
            self.assertIn("agentbox-scratch", text)
            self.assertIn("90.0%", text)
            self.assertIn("~/.cache", text)
            self.assertLessEqual(len(text), WATCH.MESSAGE_LIMIT)
        self.check(free=5)
        self.assertEqual(len(self.envoy.sent), 2)
        self.check(free=20)
        self.check()
        self.assertEqual(len(self.envoy.sent), 4)

    def test_dry_run_prints_both_addresses_without_sending(self):
        text = self.check(free=9, dry_run=True)
        self.assertIn(SID, text)
        self.assertIn(WATCH.BOX_HEALTH_SRE, text)
        self.assertIn("~/.cache", text)
        self.assertEqual(self.envoy.sent, [])
        self.assertFalse(self.state.path.read_text())

    def test_inspection_requires_a_running_box_with_a_loop_backed_home(self):
        row = {"Name": "/agentbox-scratch", "State": {"Running": True},
               "Config": {"Env": ["HOME=/home/sami"]},
               "Mounts": [{"Destination": "/home/sami", "Source": str(self.root / "home")}]}
        with patch.object(WATCH, "box_loop_image", return_value=self.box["image"]):
            self.assertEqual(WATCH.box_home_from_inspect(row), self.box)
            row["State"]["Running"] = False
            self.assertIsNone(WATCH.box_home_from_inspect(row))
            row["State"]["Running"] = True
        with patch.object(WATCH, "box_loop_image", return_value=""):
            self.assertIsNone(WATCH.box_home_from_inspect(row))

    def test_one_box_closing_during_inspect_does_not_hide_other_boxes(self):
        row = {"Name": "/agentbox-scratch", "State": {"Running": True},
               "Config": {"Env": ["HOME=/home/sami"]},
               "Mounts": [{"Destination": "/home/sami", "Source": self.box["home"]}]}

        def run(args, **kwargs):
            if args[1] == "ps":
                return subprocess.CompletedProcess(args, 0, "agentbox-scratch\nagentbox-gone\n", "")
            result = subprocess.CompletedProcess(args, 1, json.dumps([row]), "Error: No such object: agentbox-gone")
            if kwargs.get("check"):
                result.check_returncode()
            return result

        with patch.object(WATCH.subprocess, "run", side_effect=run), \
             patch.object(WATCH, "box_loop_image", return_value=self.box["image"]):
            self.assertEqual(WATCH.running_box_homes(), [self.box])

    def test_directory_timeout_keeps_completed_top_level_sizes(self):
        partial = f"25000000000\t{self.box['home']}/.cache\n2000000000\t{self.box['home']}/go\n".encode()
        with patch.object(WATCH.subprocess, "run", side_effect=subprocess.TimeoutExpired("du", 15, output=partial)):
            text = WATCH.box_disk_dirs(self.box["home"])
        self.assertIn("25.0 GB ~/.cache", text)
        self.assertIn("2.0 GB ~/go", text)
        self.assertIn("incomplete", text)

    def test_storage_reading_counts_allocated_blocks_not_sparse_length(self):
        disks, caches = self.root / "disks", self.root / "box-cache"
        disks.mkdir()
        (caches / "agentbox-scratch").mkdir(parents=True)
        image = disks / "agentbox-scratch.img"
        with image.open("wb") as handle:
            handle.truncate(128 * WATCH.GIB)
            handle.write(b"x" * 4096)
        (caches / "agentbox-scratch/cache").write_bytes(b"x" * 8192)
        with patch.object(WATCH, "agentbox_storage_paths", return_value=(disks, caches)), \
             contextlib.redirect_stdout(io.StringIO()) as out, patch.object(WATCH, "LOG_STREAM", out):
            WATCH.box_storage_reading(self.state, time.time() + 90)
        reading = self.state.data["box_storage"]
        self.assertEqual(reading["image_bytes"], image.stat().st_blocks * 512)
        self.assertGreaterEqual(reading["cache_bytes"], 8192)
        self.assertEqual(reading["total_bytes"], reading["image_bytes"] + reading["cache_bytes"])
        self.assertIn("allocated", out.getvalue())
        self.assertIn("box-cache", out.getvalue())


if __name__ == "__main__":
    unittest.main()
