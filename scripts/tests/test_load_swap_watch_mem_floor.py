#!/usr/bin/env python3
"""Regression tests for load-swap-watch's MemAvailable floor on boxes of different sizes.

One absolute number cannot serve every box this watch runs on. `--mem-floor` 8 GiB is 12.5% of
a 64 GB lane VM, 3.2% of the 251 GB devbox and 0.5% of a 1.5 TB builder, so on the big boxes it
is reached only once the box is already dying. And a floor larger than the box is not a floor
at all: on 2026-10-31 at 03:49:22Z a hand run with `--mem-floor 200000000000` alerted on
ossv-agent-repo, a 64 GB box, with "MemAvailable is 58.6 GiB, under the 186.3 GiB floor".

So the floor is `max(--mem-floor, --mem-floor-fraction x MemTotal)`, never above
`--mem-floor-cap x MemTotal`.
"""

import argparse
import importlib.machinery
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "load-swap-watch"
LOADER = importlib.machinery.SourceFileLoader("load_swap_watch_mem_floor", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

GIB = WATCH.GIB
LANE_VM = 63 * GIB      # ossv-agent-repo and its siblings: 16 cores, ~64 GB
DEVBOX = 252 * GIB      # sami-rocqperf
BUILDER = 1500 * GIB    # the largest box this watch has run on


def options(**overrides) -> argparse.Namespace:
    values = {
        "mem_floor": 8 * GIB,
        "mem_floor_fraction": 0.10,
        "mem_floor_cap": 0.25,
        "clear_factor": 0.7,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def box(total: int, available: int) -> dict:
    return {"mem_total": total, "mem_available": available}


class MemFloorTest(unittest.TestCase):
    def test_a_small_box_keeps_the_absolute_floor(self) -> None:
        """8 GiB is 12.7% of a 64 GB lane VM, above the 10% fraction, so it stands."""
        floor, how = WATCH.mem_floor(box(LANE_VM, 40 * GIB), options())
        self.assertEqual(floor, 8 * GIB)
        self.assertIn("absolute", how)

    def test_a_large_box_scales_the_floor_up(self) -> None:
        """On a 1.5 TB builder 8 GiB is 0.5%: the box is dead long before it is reached."""
        floor, how = WATCH.mem_floor(box(BUILDER, 400 * GIB), options())
        self.assertAlmostEqual(floor / GIB, 150.0, places=6)
        self.assertIn("10% of this box's", how)

    def test_the_devbox_sits_between_them(self) -> None:
        floor, _how = WATCH.mem_floor(box(DEVBOX, 100 * GIB), options())
        self.assertAlmostEqual(floor / GIB, 25.2, places=6)

    def test_a_floor_larger_than_the_box_is_capped(self) -> None:
        """The 03:49:22Z false alarm: 186.3 GiB asked for on a 64 GB box."""
        floor, how = WATCH.mem_floor(box(LANE_VM, 58 * GIB), options(mem_floor=200_000_000_000))
        self.assertAlmostEqual(floor / GIB, 0.25 * 63, places=6)
        self.assertIn("capped at 25% of this box's", how)

    def test_an_unreadable_memtotal_keeps_the_absolute_floor(self) -> None:
        """The cap must not clamp the floor to zero and silence the rule."""
        floor, how = WATCH.mem_floor(box(0, 40 * GIB), options())
        self.assertEqual(floor, 8 * GIB)
        self.assertIn("MemTotal unreadable", how)


class VerdictTest(unittest.TestCase):
    """The rule itself, on a small and a large box."""

    def sample(self, total: int, available: int) -> dict:
        return {
            "at": time.time(), "cores": 16,
            "cpu": {"some_avg60": 0.0}, "io": {"some_avg60": 0.0},
            "memory_pressure": {"full_avg60": 0.0},
            "load1": 1.0, "load5": 1.0, "load15": 1.0,
            "procs_running": 1, "procs_blocked": 0,
            "swap_total": 0, "swap_free": 0, "swap_used_fraction": 0.0,
            "mem_available": available, "mem_total": total, "pswpin": 0,
        }

    def full(self, **overrides) -> argparse.Namespace:
        values = {
            "cpu_stall": WATCH.CPU_STALL, "queue_per_core": 3.0, "swap_used": 0.85,
            "mem_floor": 8 * GIB, "mem_floor_fraction": 0.10, "mem_floor_cap": 0.25,
            "horizon": 7200.0, "rate_window": 3600.0, "clear_factor": 0.7,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_the_lane_vm_that_false_alarmed_is_quiet_now(self) -> None:
        """ossv-agent-repo as it read at 03:49:22Z, with the mis-set floor it was given."""
        found = WATCH.verdict(
            self.sample(LANE_VM, 58_600_000_000), [], self.full(mem_floor=200_000_000_000)
        )
        self.assertNotIn("memory", found["findings"])

    def test_a_lane_vm_that_really_is_short_still_fires(self) -> None:
        found = WATCH.verdict(self.sample(LANE_VM, 4 * GIB), [], self.full())
        self.assertIn("memory", found["findings"])
        self.assertIn("8.0 GiB floor", found["findings"]["memory"]["reasons"][0])

    def test_a_large_box_fires_where_the_absolute_floor_never_would(self) -> None:
        found = WATCH.verdict(self.sample(BUILDER, 100 * GIB), [], self.full())
        self.assertIn("memory", found["findings"])
        self.assertIn("150.0 GiB floor", found["findings"]["memory"]["reasons"][0])
        self.assertIn("10% of this box's", found["findings"]["memory"]["reasons"][0])

    def test_a_large_box_with_room_is_quiet(self) -> None:
        found = WATCH.verdict(self.sample(BUILDER, 900 * GIB), [], self.full())
        self.assertNotIn("memory", found["findings"])

    def test_the_clear_uses_the_same_scaled_floor(self) -> None:
        options_ = self.full()
        # 150 GiB floor / 0.7 = 214.3 GiB to clear on the builder.
        self.assertFalse(WATCH.clears("memory", self.sample(BUILDER, 200 * GIB), options_))
        self.assertTrue(WATCH.clears("memory", self.sample(BUILDER, 300 * GIB), options_))
        # 8 GiB / 0.7 = 11.4 GiB on the lane VM.
        self.assertFalse(WATCH.clears("memory", self.sample(LANE_VM, 10 * GIB), options_))
        self.assertTrue(WATCH.clears("memory", self.sample(LANE_VM, 20 * GIB), options_))


if __name__ == "__main__":
    unittest.main()
