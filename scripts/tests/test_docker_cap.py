#!/usr/bin/env python3
"""devbox/docker-cap's build-cache bound (dispatch://INFRA-586).

Until 2026-11-01 the script bounded the cache with BuildKit's own `--max-used-space` cap. That
cap is a no-op on a box with the containerd image store: BuildKit compares it against the sum
of the PRIVATE bytes of its records only, skipping every record whose layer chain also exists
in the image store (moby/buildkit v0.26.2 cache/manager.go, prune()). On sami-rocqperf that was
16.3 GB of 65.1 GB, so a 50 GB cap removed nothing while the disk stood at 97% used.

The bound is now an age ladder: while the cache is over the cap, remove the records no build has
used for each age in turn, oldest age first, stopping at the first age that brings it under.
The last age is a floor that protects a build running now.

Technique: `docker` and `df` are stubs first on PATH. The docker stub holds the cache size in a
file, answers `builder du` from it, and lets each test say how many bytes each `until=` age
removes; it logs every prune so a test can assert which ones ran, and in which order.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "devbox" / "docker-cap"
GB = 1000000000

DOCKER_STUB = r"""#!/bin/bash
set -euo pipefail
human() { awk -v n="$1" 'BEGIN { printf "%.4gGB\n", n / 1000000000 }'; }
case "$1 ${2:-}" in
"info --format")
    echo /var/lib/docker
    ;;
"builder du")
    size="$(cat "$STUB_STATE")"
    printf 'Shared:\t\t0B\nPrivate:\t%s\nReclaimable:\t%s\nTotal:\t\t%s\n' \
        "$(human "$size")" "$(human "$size")" "$(human "$size")"
    ;;
"builder prune")
    printf '%s\n' "$*" >>"$STUB_LOG"
    age=""
    for arg in "$@"; do [[ $arg == until=* ]] && age="${arg#until=}"; done
    freed="$(awk -v a="${age%h}" -v s="$(cat "$STUB_STATE")" -v r="${STUB_REMOVES:-}" '
        BEGIN {
            n = split(r, parts, ",")
            for (i = 1; i <= n; i++) {
                split(parts[i], kv, ":")
                if (kv[1] + 0 == a + 0) { print (kv[2] < s ? kv[2] : s); exit }
            }
            print 0
        }')"
    awk -v s="$(cat "$STUB_STATE")" -v f="$freed" 'BEGIN { printf "%.0f\n", s - f }' >"$STUB_STATE"
    printf 'Total:\t%s\n' "$(human "$freed")"
    ;;
"image prune" | "volume prune")
    printf '%s\n' "$*" >>"$STUB_LOG"
    echo "Total reclaimed space: 0B"
    ;;
*)
    echo "stub docker: unexpected args: $*" >&2
    exit 1
    ;;
esac
"""

DF_STUB = r"""#!/bin/bash
set -euo pipefail
case "$*" in
*--output=pcent*) printf 'Use%%\n%s%%\n' "$STUB_PCT" ;;
*--output=avail*) printf 'Avail\n%s\n' "${STUB_AVAIL:-81820971008}" ;;
*)
    echo "stub df: unexpected args: $*" >&2
    exit 1
    ;;
esac
"""


class DockerCapTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.addCleanup(self.dir.cleanup)
        binned = root / "bin"
        binned.mkdir()
        for name, body in (("docker", DOCKER_STUB), ("df", DF_STUB)):
            path = binned / name
            path.write_text(body)
            path.chmod(0o755)
        self.state = root / "cache"
        self.log = root / "prunes"
        self.env = {
            "PATH": f"{binned}:/usr/bin:/bin",
            "STUB_STATE": str(self.state),
            "STUB_LOG": str(self.log),
            "HOME": str(root),
        }

    def run_cap(self, cache_bytes, pct, removes="", **extra):
        self.state.write_text(f"{cache_bytes}\n")
        self.log.write_text("")
        result = subprocess.run(
            [str(SCRIPT)],
            capture_output=True,
            text=True,
            env={**self.env, "STUB_PCT": str(pct), "STUB_REMOVES": removes, **extra},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def prunes(self):
        lines = [l for l in self.log.read_text().splitlines() if l.startswith("builder prune")]
        return [l.split("until=")[1] for l in lines]

    def cache(self):
        return int(self.state.read_text().strip())

    def test_under_the_threshold_prunes_nothing(self):
        """The disk has room, so the hourly run costs a build nothing."""
        out = self.run_cap(65.1 * GB, 71, removes=f"48:{10 * GB}")
        self.assertIn("at or under 80%; nothing to do", out)
        self.assertEqual(self.log.read_text(), "")

    def test_the_ladder_stops_at_the_first_age_that_fits(self):
        """The 2026-11-01 state: 65.1 GB of cache against the 50 GB cap."""
        out = self.run_cap(
            65.1 * GB, 97, removes=f"48:{10 * GB},24:{20 * GB},12:{5 * GB},6:{5 * GB}"
        )
        self.assertEqual(self.prunes(), ["48h", "24h"])
        self.assertEqual(self.cache(), int(35.1 * GB))
        self.assertIn("build cache is now 35 GB, cap 50GB", out)

    def test_every_prune_names_an_age_and_all(self):
        """--all, because a shared record is no candidate without it; an age, because the floor
        is what keeps a build that runs now."""
        self.run_cap(65.1 * GB, 97, removes=f"48:{GB},24:{GB},12:{GB},6:{GB}")
        for line in self.log.read_text().splitlines():
            if line.startswith("builder prune"):
                self.assertIn("--all", line)
                self.assertRegex(line, r"--filter until=\d+h")

    def test_the_floor_is_the_last_age(self):
        """Nothing under the cap is reached, so the ladder walks to its floor and stops there."""
        self.run_cap(65.1 * GB, 97, removes=f"48:{GB},24:{GB},12:{GB},6:{GB}")
        self.assertEqual(self.prunes(), ["48h", "24h", "12h", "6h"])
        self.assertEqual(self.cache(), int(61.1 * GB))

    def test_a_cache_under_the_cap_is_left_alone(self):
        """The cap is read in Docker's SI units: 49.9 GB is under a 50GB cap."""
        self.run_cap(49.9 * GB, 97, removes=f"48:{10 * GB}")
        self.assertEqual(self.prunes(), [])

    def test_the_cap_and_the_ladder_can_be_overridden(self):
        self.run_cap(
            65.1 * GB,
            97,
            removes=f"72:{60 * GB},6:{GB}",
            DOCKER_CAP_BUILD_CACHE="10GB",
            DOCKER_CAP_CACHE_AGES="72h 6h",
        )
        self.assertEqual(self.prunes(), ["72h"])
        self.assertEqual(self.cache(), int(5.1 * GB))

    def test_images_and_volumes_are_pruned_whatever_the_cache_does(self):
        self.run_cap(10 * GB, 97)
        log = self.log.read_text()
        self.assertIn("image prune --force --filter until=24h", log)
        self.assertIn("volume prune --force", log)


if __name__ == "__main__":
    unittest.main()
