#!/usr/bin/env python3
"""Tests for no-offbox, the check every tool that copies data off this machine runs first.

LANE-C's embargoed BET-357 evidence may not leave sami-rocqperf (session
01a170c2-69ef-7141-8dc8-1e824a25ad38); on 2026-11-02 the sami-devbox bulk copy shipped part of
/mnt/durable/sami off the box because no mover could read that rule. A tree may not leave when
it, a directory above it or a directory below it holds `.no-offbox`, or when a path matches a
line of ~/.config/no-offbox/patterns. LANE-C's frozen sets are read-only with a complete
manifest, so no marker can go inside them: they live under a marked parent.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "no-offbox"
TELEPORT = SCRIPT.parent / "omp-teleport"
BACKUP = SCRIPT.parent / "backup-shared-state"


def stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8")
    p.chmod(0o755)


class NoOffbox(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        (self.home / ".config/no-offbox").mkdir(parents=True)
        self.data = self.root / "data"
        self.data.mkdir()

    def tearDown(self) -> None:
        for d, dirs, files in os.walk(self.root):
            for n in dirs + files:
                p = os.path.join(d, n)
                if not os.path.islink(p):
                    os.chmod(p, 0o700)
        self.temp_dir.cleanup()

    def patterns(self, *lines: str) -> None:
        (self.home / ".config/no-offbox/patterns").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def run_check(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if k != "SUDO_USER"}
        env["HOME"] = str(self.home)
        return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env=env, check=False, timeout=60)

    def tree(self, rel: str) -> Path:
        p = self.data / rel
        p.mkdir(parents=True, exist_ok=True)
        (p / "result.json").write_text("{}\n", encoding="utf-8")
        return p

    def mark(self, d: Path) -> Path:
        m = d / ".no-offbox"
        m.write_text("LANE-C, BET-357 evidence\n", encoding="utf-8")
        return m

    def test_an_unmarked_tree_may_leave(self) -> None:
        t = self.tree("lane/runs")
        self.patterns("bet357")
        r = self.run_check(str(t), str(self.data))
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_a_marked_tree_is_refused_and_the_refusal_names_the_marker(self) -> None:
        t = self.tree("lanec")
        m = self.mark(t)
        r = self.run_check(str(t))
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"REFUSED {t}: marker {m}", r.stdout)

    def test_a_tree_holding_a_marked_subtree_is_refused_as_a_whole(self) -> None:
        self.tree("durable/other")
        marked = self.tree("durable/nvme-cold/lanec")
        m = self.mark(marked)
        r = self.run_check(str(self.data / "durable"))
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"REFUSED {self.data / 'durable'}: holds marker {m}", r.stdout)
        # --excludes names the subtree to leave out, and the rest may go.
        ex = self.run_check("--excludes", str(self.data / "durable"))
        self.assertEqual((ex.returncode, ex.stdout), (1, f"{marked}\n"))
        self.assertIn("holds marker", ex.stderr)
        self.assertEqual(self.run_check(str(self.data / "durable/other")).returncode, 0)

    def test_a_marker_above_the_path_refuses_it_however_far_up(self) -> None:
        parent = self.tree("tmp/lanec")
        m = self.mark(parent)
        deep = self.tree("tmp/lanec/wordremoval/living/base/kept")
        r = self.run_check(str(deep))
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"REFUSED {deep}: marker {m}", r.stdout)
        # A single file under it too.
        r = self.run_check(str(deep / "result.json"))
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"marker {m}", r.stdout)

    def test_a_frozen_read_only_set_under_a_marked_parent_is_refused(self) -> None:
        """LANE-C's frozen sets: files 0444 and directories 0555 with a complete manifest, so no
        marker can go inside; the marker is on the parent."""
        parent = self.tree("tmp/flipsweep-v6")
        m = self.mark(parent)
        frozen = self.tree("tmp/flipsweep-v6/frozen-set")
        (frozen / "MANIFEST.sha256").write_text("abc  result.json\n", encoding="utf-8")
        for f in frozen.iterdir():
            f.chmod(0o444)
        frozen.chmod(0o555)
        self.assertFalse((frozen / ".no-offbox").exists())
        r = self.run_check(str(frozen))
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"REFUSED {frozen}: marker {m}", r.stdout)

    def test_a_symlink_to_a_marked_tree_is_refused(self) -> None:
        t = self.tree("cold/lanec/runs")
        m = self.mark(self.data / "cold/lanec")
        link = self.data / "link-to-runs"
        link.symlink_to(t)
        r = self.run_check(str(link))
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"marker {m}", r.stdout)

    def test_a_pattern_match_is_refused_at_the_path_above_it_and_below_it(self) -> None:
        self.patterns("# a comment", "bet357", f"{self.data}/tmp/lanec", "*/vmcast-*/out")
        t = self.tree("lanes/BET357-reruns/x")
        r = self.run_check(str(t))
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"pattern 'bet357' (from {self.home}/.config/no-offbox/patterns) matches {self.data}/lanes/BET357-reruns", r.stdout)
        # A pattern for an absolute path refuses what is under it.
        t = self.tree("tmp/lanec/t51/runs")
        r = self.run_check(str(t))
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"pattern '{self.data}/tmp/lanec'", r.stdout)
        # A glob, and a match below the tree handed over.
        self.tree("big/vmcast-3/out")
        r = self.run_check("--excludes", str(self.data / "big"))
        self.assertEqual((r.returncode, r.stdout), (1, f"{self.data}/big/vmcast-3/out\n"))
        self.assertIn("pattern '*/vmcast-*/out'", r.stderr)
        self.assertEqual(self.run_check(str(self.tree("ok/bet35/x"))).returncode, 0)

    def test_a_directory_that_cannot_be_read_is_refused(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root reads every directory")
        locked = self.tree("store/locked")
        locked.chmod(0)
        r = self.run_check(str(self.data / "store"))
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"cannot read {locked}", r.stdout)

    def test_a_patterns_file_that_cannot_be_read_refuses_everything(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root reads every file")
        self.patterns("bet357")
        (self.home / ".config/no-offbox/patterns").chmod(0)
        r = self.run_check(str(self.tree("fine")))
        self.assertEqual(r.returncode, 2)
        self.assertIn("cannot read the patterns file", r.stderr)

    def test_a_tar_list_is_checked_entry_by_entry_without_a_walk(self) -> None:
        """The bulk copy's form: tar --no-recursion lists of paths relative to the root."""
        self.patterns("embargo")
        self.tree("sami/ok")
        marked = self.tree("sami/nvme-cold/tmp/lanec/t51/runs")
        m = self.mark(self.data / "sami/nvme-cold/tmp/lanec")
        self.tree("sami/embargoed")
        entries = ["sami/ok/result.json", "sami/ok", "sami/nvme-cold/tmp/lanec/t51/runs/result.json",
                   "sami/nvme-cold/tmp/lanec", "sami/embargoed/result.json"]
        lst = self.root / "s0001.list"
        lst.write_bytes(b"\0".join(e.encode() for e in entries) + b"\0")
        r = self.run_check("--root", str(self.data), "--from", str(lst), "-0")
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"REFUSED {marked}/result.json: marker {m}", r.stdout)
        self.assertIn(f"REFUSED {self.data}/sami/nvme-cold/tmp/lanec: marker {m}", r.stdout)
        self.assertIn(f"REFUSED {self.data}/sami/embargoed/result.json: pattern 'embargo'", r.stdout)
        self.assertNotIn("sami/ok", r.stdout)
        ok = self.root / "ok.list"
        ok.write_text("sami/ok/result.json\nsami/ok\n", encoding="utf-8")
        self.assertEqual(self.run_check("--root", str(self.data), "--from", str(ok)).returncode, 0)


    # ── the movers that call it ──────────────────────────────────────────────────────────────
    def stubbed_env(self, *names: str) -> dict[str, str]:
        """PATH with each named tool replaced by one that logs its arguments to CALLS."""
        bin_ = self.root / "bin"
        for n in names:
            stub(bin_, n, f'printf "{n} %s\\n" "$*" >>"{self.root}/CALLS"; exit 0')
        env = {k: v for k, v in os.environ.items() if k != "SUDO_USER"}
        env.update(HOME=str(self.home), PATH=f"{bin_}:{env['PATH']}")
        return env

    def calls(self) -> str:
        f = self.root / "CALLS"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def test_omp_teleport_refuses_a_marked_session_before_it_reaches_the_host(self) -> None:
        sessions = self.tree("omp/agent/sessions/-lane")
        m = self.mark(sessions)
        t = sessions / "2026-11-02T10-00-00-000Z_01a170c2-69ef-7141-8dc8-1e824a25ad38.jsonl"
        t.write_text("{}\n", encoding="utf-8")
        r = subprocess.run([str(TELEPORT), "--host", "sami-devbox", str(t)], capture_output=True, text=True,
                           env=self.stubbed_env("ssh", "scp", "omp"), check=False, timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("no-offbox refuses this session leaving the machine; nothing changed", r.stderr)
        self.assertIn(f"marker {m}", r.stderr)
        self.assertEqual(self.calls(), "")

    def test_backup_shared_state_skips_a_marked_source_and_fails(self) -> None:
        env = self.stubbed_env("gcloud")
        env["TMPDIR"] = str(self.root)
        # Its sources are fixed paths; run a copy whose paths are this fixture's.
        text = BACKUP.read_text(encoding="utf-8")
        scrum = self.tree("scrum")
        m = self.mark(scrum)
        text = text.replace("/home/sami/src/scrum", str(scrum))
        text = text.replace("/mnt/nvme/sami/grade-farm", str(self.data / "none"))
        text = text.replace("/mnt/nvme/sami/compute-inventory.csv", str(self.tree("inv") / "result.json"))
        scripts = self.root / "scripts"
        scripts.mkdir()
        copy = scripts / "backup-shared-state"
        copy.write_text(text, encoding="utf-8")
        copy.chmod(0o755)
        (scripts / "no-offbox").symlink_to(SCRIPT)
        r = subprocess.run([str(copy)], capture_output=True, text=True, env=env, check=False, timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"REFUSED scrum-records: no-offbox: REFUSED {scrum}: marker {m}", r.stderr)
        self.assertIn("copied compute-inventory", r.stdout)
        self.assertNotIn("scrum", self.calls())
        self.assertIn("gcloud storage cp", self.calls())


if __name__ == "__main__":
    unittest.main()
