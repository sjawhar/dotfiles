#!/usr/bin/env python3
"""devbox/tmp-empty-prune: a directory that vanishes during the walk is not a failure.

The run of 2026-11-02 00:00Z exited 1 on two lines like
`find: '/mnt/nvme/sami/tmp/tmp.TwbzNZQA0b': No such file or directory`. Lanes make and remove
their own tmp* directories in that root all the time, and -ignore_readdir_race does not cover a
directory that goes after find has read its name: GNU find reports it when fts's stat of it
fails (find/ftsfind.c consider_visiting, FTS_NS) and when -empty's open of it fails
(find/pred.c pred_empty), and exits 1 either way.

Technique: a `find` first on PATH runs /usr/bin/find with a removal spliced into the expression.
STUB_VANISH=stat removes, at each entry find visits, every other tmpgone* entry, so the later
ones are gone when find comes to stat them. STUB_VANISH=open removes a tmpgone* entry after
find has stat'ed it and just before -empty opens it. STUB_ENOENT_FOR adds that message for a
path that is still there, and STUB_EXIT replaces find's exit status.
"""

import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "devbox" / "tmp-empty-prune"
OLD = time.time() - 3 * 86400

FIND_STUB = r"""#!/bin/bash
args=()
for arg in "$@"; do
    if [[ "${STUB_VANISH:-}" == stat && "$arg" == -name ]]; then
        args+=('(' -exec sh -c 'for d in "${1%/*}"/tmpgone*; do
            [ "$d" = "$1" ] || rmdir -- "$d" 2>/dev/null; done' sh {} ';' -o -true ')')
    elif [[ "${STUB_VANISH:-}" == open && "$arg" == -empty ]]; then
        args+=('(' -name 'tmpgone*' -exec rmdir {} ';' -o -true ')')
    fi
    args+=("$arg")
done
status=0
/usr/bin/find "${args[@]}" || status=$?
if [[ -n "${STUB_ENOENT_FOR:-}" ]]; then
    echo "find: '$STUB_ENOENT_FOR': No such file or directory" >&2
    status=1
fi
exit "${STUB_EXIT:-$status}"
"""


def summary(stdout):
    """The removed and vanished counts of the script's last line."""
    match = re.search(r": (\d+) removed, (\d+) vanished during the walk, 0 failed$", stdout)
    assert match, stdout
    return int(match[1]), int(match[2])


class TmpEmptyPruneTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        base = Path(self.dir.name)
        binned = base / "bin"
        binned.mkdir()
        stub = binned / "find"
        stub.write_text(FIND_STUB)
        stub.chmod(0o755)
        self.root = base / "tmp"
        self.root.mkdir()
        # TMPDIR is the root itself, as it is on the box: the script's own temporary file
        # lands inside the tree it prunes.
        self.env = {
            "PATH": f"{binned}:/usr/bin:/bin",
            "HOME": str(base),
            "TMPDIR": str(self.root),
            "TMP_EMPTY_PRUNE_ROOT": str(self.root),
        }

    def entry(self, name, old=True, full=False):
        path = self.root / name
        path.mkdir()
        if full:
            (path / "out").write_text("x")
        if old:
            os.utime(path, (OLD, OLD))
        return path

    def run_prune(self, **extra):
        return subprocess.run(
            [str(SCRIPT)], capture_output=True, text=True, env={**self.env, **extra}
        )

    def left(self):
        return sorted(p.name for p in self.root.iterdir())

    def test_prunes_only_the_old_empty_tmp_directories(self):
        for name in ("tmpa1b2c3d4", "tmp.Xy12Ab34Cd"):
            self.entry(name)
        self.entry("tmpyoung", old=False)
        self.entry("tmpfull", full=True)
        self.entry("build")
        result = self.run_prune()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.left(), ["build", "tmpfull", "tmpyoung"])
        self.assertEqual(summary(result.stdout), (2, 0))

    def test_a_directory_removed_before_find_stats_it_is_not_a_failure(self):
        """The 2026-11-02 failure, the common way: the run completes and the rest is pruned."""
        for name in ("tmpgone1", "tmpgone2", "tmpold1"):
            self.entry(name)
        self.entry("tmpfull", full=True)
        result = self.run_prune(STUB_VANISH="stat")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.left(), ["tmpfull"])
        # A tmpgone* that find happens to visit first removes the other and is itself pruned.
        removed, vanished = summary(result.stdout)
        self.assertGreaterEqual(vanished, 1)
        self.assertEqual(removed + vanished, 3)

    def test_a_directory_removed_before_empty_opens_it_is_not_a_failure(self):
        self.entry("tmpgone1")
        self.entry("tmpold1")
        self.entry("tmpold2")
        self.entry("tmpfull", full=True)
        result = self.run_prune(STUB_VANISH="open")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.left(), ["tmpfull"])
        self.assertEqual(summary(result.stdout), (2, 1))

    def test_a_refusal_still_fails_the_run(self):
        """A directory find may not open is a real error, and the other prunes still happen."""
        locked = self.entry("tmplocked")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        self.entry("tmpold1")
        result = self.run_prune()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"'{locked}': Permission denied", result.stderr)
        self.assertEqual(self.left(), ["tmplocked"])

    def test_no_such_file_for_a_path_still_there_fails_the_run(self):
        full = self.entry("tmpfull", full=True)
        result = self.run_prune(STUB_ENOENT_FOR=str(full))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"'{full}': No such file or directory", result.stderr)

    def test_an_exit_no_line_accounts_for_fails_the_run(self):
        """Exit 1 with no line at all, and a kill with only a vanished directory to show."""
        result = self.run_prune(STUB_EXIT="1")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("find exited 1", result.stderr)
        self.entry("tmpgone1")
        result = self.run_prune(STUB_EXIT="137")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("find exited 137", result.stderr)


if __name__ == "__main__":
    unittest.main()
