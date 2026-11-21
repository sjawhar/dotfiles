#!/usr/bin/env python3
"""devbox/gocache-cap caps every Go build cache under its roots, and their sum, not each one.

On 2026-11-21 at 03:2xZ sami-devbox held 475 GB in ~/.cache/go-build, Go's default for a go command
with no GOCACHE (omp's Python eval kernel drops GOCACHE from the environment it gives its tools),
and lane caches named with GOCACHE=<a directory of the lane's own> under /mnt/scratch/<user>, at any
depth. gocache-cap capped one directory, the box's GOCACHE, so none of them was capped.

Technique: the ps and go stubs and the scratch /proc of test_gocache_cap_guard; the roots are a
scratch tree's, set through GOCACHE_CAP_ROOTS, with caches made where the lanes and Go put them.
"""

import subprocess
import unittest
from pathlib import Path

from scripts.tests.test_gocache_cap_guard import HOUR, NO_AGES, SELF, Harness, go


class GocacheCapCaches(Harness):
    def setUp(self) -> None:
        super().setUp()
        self.scratch = self.root / "scratch"
        self.home_cache = self.root / ".cache"
        self.scratch.mkdir()
        self.home_cache.mkdir()
        self.env["GOCACHE_CAP_ROOTS"] = f"{self.scratch} {self.home_cache}"

    def test_caps_every_go_build_cache_under_the_roots(self) -> None:
        caches = [
            self.make_cache(self.scratch / "gocache"),  # the box's GOCACHE
            self.make_cache(self.scratch / "lane.Ab12Cd" / "gocache"),
            self.make_cache(self.scratch / "proto-stacks" / "w1" / "gocache"),
            self.make_cache(self.scratch / "box-cache" / "agentbox-6ace86fd7cf0" / "go-build"),
            self.make_cache(self.home_cache / "go-build"),  # Go's default
        ]
        old = [self.entry("old-d", 4 * HOUR, size=10, cache=cache) for cache in caches]
        r = self.run_cap(**NO_AGES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([p for p in old if p.exists()], [], r.stdout)
        self.assertIn("5 Go build caches under", r.stdout)
        self.assertIn("gocache-cap: reclaimed-bytes: 50\n", r.stdout)
        for cache in caches:
            self.assertTrue((cache / "trim.txt").exists(), cache)
        self.assert_go_never_ran()

    def test_a_deeper_cache_a_running_go_command_builds_in_is_capped(self) -> None:
        # Four directories down, which every pass's search does not reach, and one outside the roots.
        deep = self.make_cache(self.scratch / "session-tmp" / "123-AbCd" / "tmp.Xy" / "gocache")
        outside = self.make_cache(self.root / "elsewhere" / "gocache")
        old = [self.entry("old-d", 4 * HOUR, cache=cache) for cache in (deep, outside)]
        self.environ(4242, HOME="/home/x", GOCACHE=f"{deep}/")
        self.environ(4343, GOCACHE=str(outside))
        r = self.run_cap(SELF + go(60, pid=4242) + go(60, pid=4343), **NO_AGES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("and 2 more a running go command builds in", r.stdout)
        self.assertEqual([p for p in old if p.exists()], [], r.stdout)

    def test_a_deeper_cache_is_capped_by_the_search_without_a_limit(self) -> None:
        # Four directories down: every pass leaves it, a sweep by hand at every depth caps it.
        deep = self.make_cache(self.scratch / "session-tmp" / "123-AbCd" / "tmp.Xy" / "gocache")
        old = self.entry("old-d", 4 * HOUR, cache=deep)
        r = self.run_cap(**NO_AGES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("found no Go build cache under", r.stdout)
        self.assertTrue(old.exists(), r.stdout)
        r = self.run_cap(GOCACHE_CAP_DEPTH="0", **NO_AGES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("(at every depth)", r.stdout)
        self.assertFalse(old.exists(), r.stdout)

    def test_the_limits_hold_for_the_sum_of_the_caches(self) -> None:
        # Each cache is under the trim size; together they are over it.
        first = self.make_cache(self.scratch / "a" / "gocache")
        second = self.make_cache(self.home_cache / "go-build")
        cold = [self.entry("cold-d", 4 * HOUR, size=1000, cache=cache) for cache in (first, second)]
        sizes = [int(subprocess.run(["du", "-sb", str(c)], capture_output=True, text=True, check=True)
                     .stdout.split()[0]) for c in (first, second)]
        r = self.run_cap(GOCACHE_TRIM_BYTES=str(max(sizes) + 1), GOCACHE_CAP_BYTES="1000000000")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([p for p in cold if p.exists()], [], r.stdout)

    def test_a_cache_inside_another_is_counted_once(self) -> None:
        parent = self.make_cache(self.scratch / "gocache")
        lane = self.make_cache(parent / "INFRA")
        self.entry("a-d", 0, size=5000, cache=parent)
        self.entry("b-d", 0, size=7000, cache=lane)
        size = subprocess.run(["du", "-sb", str(parent)], capture_output=True, text=True, check=True).stdout.split()[0]
        r = self.run_cap(GOCACHE_TRIM_BYTES="1000000000")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"2 Go build caches under {self.scratch} {self.home_cache} (to depth 4) hold {size} bytes",
                      r.stdout)

    def test_a_directory_named_like_an_entry_directory_is_not_searched(self) -> None:
        # A cache keeps its entries in 00-ff; walking them costs a pass over every entry.
        self.make_cache(self.scratch / "gocache")
        hidden = self.make_cache(self.scratch / "ab" / "gocache")
        old = self.entry("old-d", 4 * HOUR, cache=hidden)
        r = self.run_cap(**NO_AGES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("1 Go build cache under", r.stdout)
        self.assertTrue(old.exists(), r.stdout)

    def test_leaves_a_cache_go_did_not_make_alone(self) -> None:
        # golangci-lint's cache has Go's layout and its own README; its readers are not go commands.
        lint = self.make_cache(self.home_cache / "golangci-lint")
        (lint / "README").write_text("This directory holds cached build artifacts from golangci-lint.\n",
                                     encoding="utf-8")
        old = self.entry("old-d", 4 * HOUR, cache=lint)
        r = self.run_cap(**NO_AGES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("found no Go build cache under", r.stdout)
        self.assertTrue(old.exists(), r.stdout)

    def test_says_when_it_found_no_cache(self) -> None:
        r = self.run_cap(**NO_AGES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("found no Go build cache under", r.stdout)
        self.assertIn("gocache-cap: reclaimed-bytes: 0\n", r.stdout)

    def test_says_how_many_directories_it_could_not_read(self) -> None:
        cache = self.make_cache(self.scratch / "gocache")
        old = self.entry("old-d", 4 * HOUR, cache=cache)
        locked = self.scratch / "locked"
        self.make_cache(locked / "gocache")
        locked.chmod(0)
        try:
            r = self.run_cap(**NO_AGES)
        finally:
            locked.chmod(0o755)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("1 directories there could not be read, and a cache inside one is not capped", r.stdout)
        self.assertFalse(old.exists(), r.stdout)


if __name__ == "__main__":
    unittest.main()
