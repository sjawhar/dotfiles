#!/usr/bin/env python3
"""A box's home is kept when the box is removed, and the session's next box starts with it.

$HOME in a box is $BOXES/<box>/home on the host, so a file a session writes in its home and not
under a --dir path lives there and dies with the box. `agentbox move --self` on sami-devbox removed
box agentbox-74a3ddaacbdc with its home at 17:22Z on 2026-11-03, and PEER's WAKE-peer-01a13837.md
and .leanperf.md, written minutes before, went with it: a host-wide find found nothing.

Technique, as test_agentbox_limits.py: source scripts/agentbox (its source guard runs nothing) and
call box_home_carry and seed_box_home against box directories under a temporary root.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"


class BoxHomeCarry(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.boxes = self.root / "boxes"
        self.homes = self.root / "box-homes"
        # The script reads the broker url from $HOME/.dotfiles at source time.
        (self.home / ".dotfiles").symlink_to(DOTFILES)
        self.env = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin",
            "AGENTBOX_BOXES": str(self.boxes),
            "AGENTBOX_BOX_HOMES": str(self.homes),
        }
        self.sid = "01a19700-0000-7000-8000-0000000e0001"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_bash(self, script: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", f'source "{AGENTBOX}"\n{script}\n'],
            capture_output=True, text=True, env={**self.env, **extra_env}, check=False, timeout=120,
        )

    def box_with_home(self, box: str, files: dict[str, str], *, stamp: bool = True,
                      before: tuple[str, ...] = ()) -> Path:
        """A box directory as a launch leaves it: its home, its start stamp, then what the session
        wrote. `before` names files the LAUNCH put there, older than the stamp."""
        home = self.boxes / box / "home"
        home.mkdir(parents=True)
        for name in before:
            path = home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("from the launch\n", encoding="utf-8")
        if stamp:
            (self.boxes / box / ".started").write_text("", encoding="utf-8")
        time.sleep(0.01)
        for name, text in files.items():
            path = home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return home

    def test_what_the_session_wrote_is_kept_and_the_next_box_starts_with_it(self) -> None:
        old = "agentbox-74a3ddaacbdc"
        self.box_with_home(old, {"WAKE-peer-01a13837.md": "wake\n", ".leanperf.md": "notes\n",
                                 "sub/dir/file.txt": "deep\n"},
                           before=(".bashrc",))
        carried = self.run_bash(f'box_home_carry {old} {self.sid}')
        self.assertEqual(carried.returncode, 0, carried.stderr)
        dest = Path(carried.stdout.strip())
        self.assertTrue(dest.is_dir(), carried.stdout + carried.stderr)
        self.assertEqual(dest.parent, self.homes / self.sid)
        self.assertEqual((dest / "WAKE-peer-01a13837.md").read_text(encoding="utf-8"), "wake\n")
        self.assertEqual((dest / ".leanperf.md").read_text(encoding="utf-8"), "notes\n")
        self.assertEqual((dest / "sub/dir/file.txt").read_text(encoding="utf-8"), "deep\n")
        # What the launch put there is not the session's work and is not kept.
        self.assertFalse((dest / ".bashrc").exists(), "a file older than the start stamp is not carried")
        self.assertIn("file(s) the session wrote in its box home are kept in", carried.stderr)
        # The session's next box starts with those files, and keeps what the new launch wrote.
        new = "agentbox-000000000001"
        (self.boxes / new / "home").mkdir(parents=True)
        (self.boxes / new / "home" / ".bashrc").write_text("new launch\n", encoding="utf-8")
        seeded = self.run_bash(f'BOX_SEED_SID={self.sid} seed_box_home {new}')
        self.assertEqual(seeded.returncode, 0, seeded.stderr)
        box_home = self.boxes / new / "home"
        self.assertEqual((box_home / "WAKE-peer-01a13837.md").read_text(encoding="utf-8"), "wake\n")
        self.assertEqual((box_home / "sub/dir/file.txt").read_text(encoding="utf-8"), "deep\n")
        self.assertEqual((box_home / ".bashrc").read_text(encoding="utf-8"), "new launch\n",
                         "a file the new box has already is not replaced")
        # The copy stays where it is, so nothing depends on the new box surviving.
        self.assertTrue((dest / "WAKE-peer-01a13837.md").exists())

    def test_a_home_with_nothing_new_is_carried_as_nothing_and_a_big_one_keeps_the_box(self) -> None:
        empty = "agentbox-000000000002"
        self.box_with_home(empty, {}, before=("only-the-launch",))
        nothing = self.run_bash(f'box_home_carry {empty} {self.sid}')
        self.assertEqual(nothing.returncode, 0, nothing.stderr)
        self.assertEqual(nothing.stdout.strip(), "")
        self.assertFalse((self.homes / self.sid).exists())
        # Over the cap: the box directory is kept instead, so nothing is lost and nothing is copied.
        big = "agentbox-000000000003"
        home = self.box_with_home(big, {"huge.bin": ""})
        (home / "huge.bin").write_bytes(b"x" * 200_000)
        kept = self.run_bash(f'box_home_carry {big} {self.sid}',
                             AGENTBOX_BOX_HOME_CARRY_BYTES="1000")
        self.assertEqual(kept.returncode, 1, kept.stdout + kept.stderr)
        self.assertIn("is left in place for its owner", kept.stderr)
        self.assertFalse(any((self.homes / self.sid).glob(f"{big}-*")) if (self.homes / self.sid).exists() else False)

    def test_a_box_with_no_start_stamp_carries_its_whole_home(self) -> None:
        """A box started before the stamp existed: everything in its home counts as the session's,
        since there is nothing to tell the launch's files from its own."""
        old = "agentbox-000000000004"
        self.box_with_home(old, {"notes.md": "work\n"}, stamp=False, before=("also-kept",))
        carried = self.run_bash(f'box_home_carry {old} ""')
        self.assertEqual(carried.returncode, 0, carried.stderr)
        dest = Path(carried.stdout.strip())
        self.assertEqual((dest / "notes.md").read_text(encoding="utf-8"), "work\n")
        self.assertTrue((dest / "also-kept").exists())
        # With no session id the copy is keyed by the box, so it is still findable.
        self.assertEqual(dest.parent, self.homes / old)


if __name__ == "__main__":
    unittest.main()
