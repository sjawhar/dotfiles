#!/usr/bin/env python3
"""`agentbox ls` ends promptly however large a left box tree is (scripts/agentbox cmd_ls).

On sami-devbox on 2026-11-21 `agentbox ls` printed its table, then ran `du` over each box tree with
no container: two were loop-mounted filesystems, one of 119 GiB and 3 million inodes, and each walk
took 41-58 s, so the command ran into a 300 s limit. A tree on its own filesystem is now measured by
df, and any other du has a bounded wait.

Technique: source scripts/agentbox with a stub `docker` (the running boxes), a stub `mountpoint`
that names one fixture tree as a filesystem of its own, and a `du` on PATH that records its
argument and then sleeps for ten minutes, as the walk of a large tree does.
"""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "agentbox"
# The test's own limit: a cmd_ls that walks the tree with du runs into it and fails.
HANG_GUARD = 60


class AgentboxLs(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.boxes = self.root / "boxes"
        self.disks = self.root / "disks"
        self.disks.mkdir()
        self.running = self.boxes / "agentbox-000000000001"
        self.mounted = self.boxes / "agentbox-000000000002"
        self.plain = self.boxes / "agentbox-000000000003"
        self.image_only = self.boxes / "agentbox-000000000004"
        for tree in (self.running, self.mounted, self.plain, self.image_only):
            tree.mkdir(parents=True)
        (self.disks / "agentbox-000000000004.img").write_bytes(b"x" * 4096)
        self.du_record = self.root / "du-record"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        du = bin_dir / "du"
        du.write_text('#!/bin/bash\nprintf "%s\\n" "${@: -1}" >> "$DU_RECORD"\nexec sleep 600\n')
        du.chmod(du.stat().st_mode | stat.S_IEXEC)
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DU_RECORD": str(self.du_record),
            "AGENTBOX_IDENTITY_FILE": str(self.root / "not-a-box"),
            "AGENTBOX_BOX": "",
            "AGENTBOX_BOXES": str(self.boxes),
            "AGENTBOX_DISKS": str(self.disks),
            "AGENTBOX_LS_DU_WAIT": "2",
            # The script sources $DOTFILES_DIR/agent-secrets/broker-url.sh: this tree's.
            "DOTFILES_DIR": str(SCRIPT.parents[1]),
        }

    def ls(self) -> tuple[subprocess.CompletedProcess[str], float]:
        body = f"""
docker() {{
    case "$*" in
        *table*) printf 'NAMES\\tIMAGE\\tSTATUS\\nagentbox-000000000001\\tagentbox:t\\tUp 3 days\\n' ;;
        *) printf 'agentbox-000000000001\\n' ;;
    esac
}}
mountpoint() {{ [[ "${{@: -1}}" == {shlex.quote(str(self.mounted))} ]]; }}
cmd_ls
"""
        start = time.monotonic()
        result = subprocess.run(
            ["bash", "-c", f"source {shlex.quote(str(SCRIPT))}\n{body}"],
            env=self.env, capture_output=True, text=True, timeout=HANG_GUARD,
        )
        return result, time.monotonic() - start

    def du_calls(self) -> list[str]:
        return self.du_record.read_text().splitlines() if self.du_record.exists() else []

    def test_a_tree_on_its_own_filesystem_is_measured_by_df_not_walked(self) -> None:
        result, took = self.ls()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(str(self.mounted), self.du_calls(), "the mounted tree is never walked")
        used = subprocess.run(["df", "-h", "--output=used", str(self.mounted)], capture_output=True, text=True, check=True)
        line = next(x for x in result.stdout.splitlines() if "agentbox-000000000002" in x)
        self.assertIn(f"{used.stdout.splitlines()[-1].strip()} on its own filesystem", line)
        self.assertIn("agentbox-000000000001", result.stdout.splitlines()[1], "the table comes first")
        # Two du calls (the plain tree and the image), each cut at AGENTBOX_LS_DU_WAIT=2, and no wait
        # on the mounted tree.
        self.assertLess(took, 20, result.stdout)

    def test_a_du_past_its_wait_says_the_size_was_not_measured(self) -> None:
        result, _ = self.ls()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(str(self.plain), self.du_calls())
        line = next(x for x in result.stdout.splitlines() if "agentbox-000000000003" in x)
        self.assertIn("size not measured in 2 s; release: rm -rf", line)
        self.assertNotIn("agentbox-000000000001  ", result.stdout, "a running box is no left tree")

    def test_a_du_that_finishes_prints_its_size(self) -> None:
        # The control of the case above: the real du, which ends at once on a small tree.
        (self.root / "bin" / "du").unlink()
        result, _ = self.ls()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        line = next(x for x in result.stdout.splitlines() if "agentbox-000000000003" in x)
        self.assertRegex(line, r"agentbox-000000000003  \d+(\.\d+)?[KMGT]?; release: rm -rf")
        line = next(x for x in result.stdout.splitlines() if "agentbox-000000000004" in x)
        self.assertRegex(line, r"an unmounted filesystem image \(\d+(\.\d+)?[KMGT]? used\)")


if __name__ == "__main__":
    unittest.main()
