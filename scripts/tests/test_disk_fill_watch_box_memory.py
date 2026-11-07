#!/usr/bin/env python3
"""A box near its memory cap is named; a box full of page cache is not.

2026-11-07: the LPS revision lane was killed at its cgroup limit twice in a day while the host
had over 99% of its memory free, and the first version of this measurement ranked on
memory.current and called the quietest box on the fleet its worst. So the two cases that matter
are a cache-heavy box staying silent and an anon-heavy one alerting once and clearing once.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "disk-fill-watch"
loader = importlib.machinery.SourceFileLoader("disk_fill_watch_box_memory", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
WATCH = importlib.util.module_from_spec(spec)
loader.exec_module(WATCH)
SID = "00000000-0000-0000-0000-000000000002"
CAP = 160 * WATCH.GIB


class FakeEnvoy:
    def __init__(self):
        self.sent = []

    def send(self, target, text):
        self.sent.append((target, text))
        return True


class BoxMemory(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.slice = self.root / "slice"
        self.slice.mkdir()
        self.state = WATCH.State(self.root / "state.json")
        self.addCleanup(self.state.close)
        self.envoy = FakeEnvoy()
        self.options = WATCH.build_parser().parse_args(
            ["--state", str(self.root / "state.json"), "--print-alert"])

    def box(self, short: str, anon_gib: float, file_gib: float = 0.0, cap: int = CAP) -> None:
        """A cgroup directory shaped like the real one: current includes cache, stat splits it."""
        full = (short * 64)[:64]
        directory = self.slice / f"docker-{full}.scope"
        directory.mkdir(exist_ok=True)
        anon = int(anon_gib * WATCH.GIB)
        cached = int(file_gib * WATCH.GIB)
        (directory / "memory.max").write_text(f"{cap}\n")
        (directory / "memory.current").write_text(f"{anon + cached}\n")
        (directory / "memory.stat").write_text(f"anon {anon}\nfile {cached}\nshmem 0\n")

    def check(self, names=None):
        with patch.object(WATCH, "BOX_SLICE", self.slice), \
             patch.object(WATCH, "box_container_names", return_value=names or {}), \
             patch.object(WATCH, "box_disk_session", return_value=SID), \
             contextlib.redirect_stdout(io.StringIO()) as out, \
             patch.object(WATCH, "LOG_STREAM", out):
            result = WATCH.check_box_memory(self.options, self.state, self.envoy, time.time() + 90)
        return result, out.getvalue()

    # ── the case the first version of this got wrong ────────────────────────────────────────
    def test_a_box_full_of_page_cache_says_nothing(self):
        """150 GiB of a 160 GiB cap, and 20 of it anon. The kernel reclaims cache before it kills."""
        self.box("a", anon_gib=20, file_gib=130)
        (alerts, cleared), _ = self.check()
        self.assertEqual((alerts, cleared), (0, 0))
        self.assertEqual(self.envoy.sent, [])

    def test_an_anon_heavy_box_alerts_once_then_clears_once(self):
        self.box("b", anon_gib=130)                      # 81% of the cap, above the 75 bar
        (alerts, _), _ = self.check()
        self.assertEqual(alerts, 2)
        self.assertEqual({target for target, _ in self.envoy.sent}, {SID, WATCH.BOX_HEALTH_SRE})
        for _, text in self.envoy.sent:
            self.assertIn("BOX MEMORY HIGH", text)
            self.assertIn("81%", text)
            self.assertLessEqual(len(text), WATCH.MESSAGE_LIMIT)

        self.box("b", anon_gib=135)                      # still high: no second alert
        (alerts, _), _ = self.check()
        self.assertEqual(alerts, 0)
        self.assertEqual(len(self.envoy.sent), 2)

        self.box("b", anon_gib=100)                      # 62%, under the 65 clear
        (_, cleared), _ = self.check()
        self.assertEqual(cleared, 1)
        self.assertEqual(len(self.envoy.sent), 4)
        for _, text in self.envoy.sent[2:]:
            self.assertIn("BOX MEMORY CLEARED", text)

        self.box("b", anon_gib=130)                      # and it can alert again afterwards
        (alerts, _), _ = self.check()
        self.assertEqual(alerts, 2)

    def test_between_the_two_bars_it_neither_alerts_nor_clears(self):
        """70% is above the clear and below the alert: a box sitting on the line stays quiet."""
        self.box("c", anon_gib=112)
        (alerts, cleared), _ = self.check()
        self.assertEqual((alerts, cleared), (0, 0))

    def test_a_cgroup_with_no_limit_is_skipped(self):
        self.box("d", anon_gib=400)
        (self.slice / f"docker-{('d' * 64)[:64]}.scope" / "memory.max").write_text("max\n")
        (alerts, cleared), _ = self.check()
        self.assertEqual((alerts, cleared), (0, 0))

    def test_the_message_names_the_box_and_the_session(self):
        self.box("e", anon_gib=130)
        names = {("e" * 12): "agentbox-eeeeeeeeeeee"}
        self.check(names=names)
        for _, text in self.envoy.sent:
            self.assertIn("agentbox-eeeeeeeeeeee", text)
            self.assertIn(SID, text)
            # memory.current is shown beside the figure so nobody repeats the first version.
            self.assertIn("memory.current", text)


if __name__ == "__main__":
    unittest.main()
