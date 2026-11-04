"""omp-memory-watch: every report records the scope's memory.pressure (cgroup v2 PSI).

The librarian's eval harness asked for it on 2026-11-04: an eval arm that ran near its ceiling
and then ended at its time limit could not be judged stalled or not, since no report held its
stall totals.

Run: python3 -m unittest scripts/tests/test_omp_memory_watch_pressure.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "omp-memory-watch"
LOADER = importlib.machinery.SourceFileLoader("omp_memory_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

GIB = WATCH.GIB
PSI = (
    "some avg10=1.50 avg60=12.34 avg300=4.00 total=81500000\n"
    "full avg10=0.50 avg60=3.21 avg300=1.00 total=2250000\n"
)


class PressureLineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        original = WATCH.CGROUP_ROOT
        WATCH.CGROUP_ROOT = self.root
        self.addCleanup(lambda: setattr(WATCH, "CGROUP_ROOT", original))
        self.scope = self.root / "user.slice" / "omp-1.scope"
        self.scope.mkdir(parents=True)

    def test_the_line_gives_avg60_and_total_in_seconds(self) -> None:
        (self.scope / "memory.pressure").write_text(PSI)
        line = WATCH.pressure_line("/user.slice/omp-1.scope")
        self.assertRegex(line, r"^Memory pressure \(memory\.pressure at \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\): ")
        self.assertIn("some avg60 12.34%, total 81.5 s; full avg60 3.21%, total 2.2 s", line)

    def test_a_missing_file_is_named_and_does_not_raise(self) -> None:
        line = WATCH.pressure_line("/user.slice/omp-1.scope")
        self.assertTrue(line.startswith("memory.pressure not readable: "), line)
        self.assertIn("No such file", line)
        self.assertTrue(WATCH.pressure_line("").startswith("memory.pressure not readable: "))

    def test_every_report_kind_carries_the_line(self) -> None:
        (self.scope / "memory.pressure").write_text(PSI)
        cgroup = "/user.slice/omp-1.scope"
        incident = dict(
            cgroup=cgroup, limit=24 * GIB, warn_at=17 * GIB, peak=18 * GIB, current=19 * GIB,
            limit_from="memory.max", opened_at=1000.0, pid=7, omp_pid=7, scope="omp-1.scope",
        )
        texts = {
            "near": WATCH.alert_text("omp-1.scope", incident, 1100.0, "first alert"),
            "still-rising": WATCH.alert_text("omp-1.scope", incident, 1200.0, "STILL RISING 1m"),
            "cleared": WATCH.clear_text("omp-1.scope", incident, "it fell", 1300.0),
            "wall": WATCH.wall_text(dict(
                cgroup=cgroup, scope="omp-1.scope", limit=24 * GIB, peak=24 * GIB,
                current=24 * GIB, usage=20 * GIB, limit_from="memory.max", peak_touch=True,
            )),
        }
        for kind, text in texts.items():
            with self.subTest(kind=kind):
                self.assertIn("some avg60 12.34%, total 81.5 s", text)


if __name__ == "__main__":
    unittest.main()
