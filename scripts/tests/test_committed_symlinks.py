#!/usr/bin/env python3
"""Every committed symlink points at a path this repository tracks (root AGENTS.md, "Everything committed is portable").

A link into a gitignored path such as vendor/, or out of the repository, dangles in a fresh clone:
scripts/pr-snapshot once pointed into vendor/compound-engineering. The links are read from the index
(`git ls-files -s`, mode 120000), so the check sees what a clone gets, not what this checkout holds.
"""

from __future__ import annotations

import posixpath
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


class CommittedSymlinks(unittest.TestCase):
    def test_every_committed_link_points_at_a_tracked_path(self) -> None:
        links: dict[str, str] = {}
        for line in git("ls-files", "-s", "-z").split("\0"):
            if line.startswith("120000 "):
                meta, path = line.split("\t", 1)
                links[path] = meta.split()[1]
        # An enumeration that found no link has checked nothing: the hook links in shims/ are always there.
        self.assertGreater(len(links), 0, "git ls-files -s listed no symlink; the check could not see any")

        tracked = set(git("ls-files", "-z").split("\0")) - {""}
        tracked_dirs = {parent for path in tracked for parent in Path(path).parents} - {Path(".")}
        targets = {sha: git("cat-file", "blob", sha) for sha in set(links.values())}

        bad: list[str] = []
        for path, sha in sorted(links.items()):
            target = targets[sha]
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
            if posixpath.isabs(target) or resolved == ".." or resolved.startswith("../"):
                bad.append(f"{path} -> {target}: outside the repository")
            elif resolved not in tracked and Path(resolved) not in tracked_dirs:
                bad.append(f"{path} -> {target}: {resolved} is not tracked, so a fresh clone has nothing there")
        self.assertEqual(bad, [], "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
