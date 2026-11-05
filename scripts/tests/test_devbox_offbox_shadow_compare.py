"""`devbox-offbox-shadow-compare` is the evidence the cluster watcher is armed on.

The watch of sami-devbox is armed in the dispatch cluster only after a day in which it agreed,
pass by pass, with the one on sami-rocqperf (dispatch://INFRA-993). So a comparison that quietly
calls two different days "agreed" -- by pairing a pass with its neighbour, by dropping a pass with
no partner, or by reading a pod's log as empty -- is the failure that matters here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

COMPARE = Path(__file__).resolve().parent.parent / "devbox-offbox-shadow-compare"
SESSION = "01a1a7fc-a94f-72f8-9651-bd5ecd335709"
OTHER = "01a15f9c-6bee-7127-b4f1-907ec8d3176d"


def record(at: float, **fields) -> str:
    line = {"at": at, "iso": "2026-11-05T10:00:00Z", "watcher": "sami-rocqperf",
            "host": "sami-devbox", "mode": "report-only", "reached": "judged",
            "watched": [SESSION], "live": [SESSION], "dead": [], "named": [], "restarting": {},
            "stalled": False, "scan_failed": "", "would_restart": [],
            "refused": {"stall": [], "limit": [], "box_restart": []}}
    line.update(fields)
    return json.dumps(line, sort_keys=True)


def run(tmp_path: Path, a: list[str], b: list[str], *args: str) -> subprocess.CompletedProcess:
    (tmp_path / "a.jsonl").write_text("".join(line + "\n" for line in a))
    (tmp_path / "b.jsonl").write_text("".join(line + "\n" for line in b))
    return subprocess.run([sys.executable, str(COMPARE), str(tmp_path / "a.jsonl"),
                           str(tmp_path / "b.jsonl"), *args],
                          capture_output=True, text=True, timeout=60)


def test_a_day_of_agreement_passes(tmp_path: Path) -> None:
    passes = [record(1_000_000 + 300 * i) for i in range(288)]
    done = run(tmp_path, passes, [record(1_000_000 + 300 * i + 7) for i in range(288)],
               "--quiet", "--min-passes", "288")
    assert done.returncode == 0, done.stdout
    assert "288 pass(es) compared, 288 agreed, 0 disagreed" in done.stdout


def test_a_different_verdict_is_a_disagreement(tmp_path: Path) -> None:
    done = run(tmp_path, [record(1_000_000, dead=[SESSION], would_restart=[SESSION], live=[])],
               [record(1_000_005, dead=[], live=[SESSION])])
    assert done.returncode == 1
    assert "DISAGREE" in done.stdout
    assert f"dead: only in A: {SESSION}" in done.stdout


def test_a_different_refusal_is_a_disagreement(tmp_path: Path) -> None:
    """Both would restart nothing, but for different reasons: that is not agreement."""
    stall = record(1_000_000, dead=[SESSION], live=[],
                   refused={"stall": [f"UTIL ({SESSION})"], "limit": [], "box_restart": []})
    box = record(1_000_003, dead=[SESSION], live=[],
                 refused={"stall": [], "limit": [], "box_restart": [f"UTIL ({SESSION})"]})
    done = run(tmp_path, [stall], [box])
    assert done.returncode == 1
    assert "refused/stall" in done.stdout and "refused/box_restart" in done.stdout


def test_a_pass_with_no_partner_is_reported_not_dropped(tmp_path: Path) -> None:
    """A watch that did not run is not a watch that agreed."""
    done = run(tmp_path, [record(1_000_000), record(1_000_300)], [record(1_000_000)])
    assert "UNPAIRED A" in done.stdout
    assert "1 pass(es) compared" in done.stdout
    assert done.returncode == 0  # unpaired is not a disagreement; --min-passes is the gate
    strict = run(tmp_path, [record(1_000_000), record(1_000_300)], [record(1_000_000)],
                 "--min-passes", "2")
    assert strict.returncode == 1
    assert "fewer than the 2 asked for" in strict.stdout


def test_a_pass_is_never_paired_with_its_neighbour(tmp_path: Path) -> None:
    """Each watch runs every 300 s and the window is 150 s, so a missed run leaves a hole rather
    than shifting every later pass onto the wrong partner."""
    done = run(tmp_path, [record(1_000_000), record(1_000_300)],
               [record(1_000_300), record(1_000_600)])
    assert done.returncode == 0
    assert done.stdout.count("UNPAIRED") == 2, done.stdout


def test_a_pod_log_is_read_through_its_prefix(tmp_path: Path) -> None:
    """A finished pod's log is all there is of it: kubectl's prefix and the watch's own lines sit
    around the shadow line."""
    pod = [
        "[pod/devbox-offbox-watch-29345-abcde/watch] devbox-offbox-watch on devbox-offbox-watch-29345",
        "[pod/devbox-offbox-watch-29345-abcde/watch] 1 registry entries, 1 with auto_restart=yes",
        "[pod/devbox-offbox-watch-29345-abcde/watch] SHADOW-PASS " + record(1_000_000, watcher="pod"),
    ]
    done = run(tmp_path, pod, [record(1_000_004)], "--quiet")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 pass(es) compared, 1 agreed" in done.stdout


def test_an_empty_log_compares_nothing_and_says_so(tmp_path: Path) -> None:
    done = run(tmp_path, [], [record(1_000_000)], "--min-passes", "1")
    assert done.returncode == 1
    assert "0 pass(es) compared" in done.stdout
    assert "UNPAIRED B" in done.stdout
