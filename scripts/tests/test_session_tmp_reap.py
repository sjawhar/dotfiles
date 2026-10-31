#!/usr/bin/env python3
"""The /mnt/nvme/<user>/tmp root of devbox/session-scratch-reap (dispatch://INFRA-675).

That root holds the working directories agents make by hand: 1,095 GB in 13,657 entries on
2026-10-31, owned by nothing. The rule the SRE decided is an `OWNER` file at the top of an
entry holding one full session id, and three conditions that must ALL hold before an entry with
one is removed - no process holds it, its owner is not live on this box, and nothing in it was
written for 7 days.

Every test here is one of the conditions the spec says this must ship under:

  report-only first            test_report_only_removes_nothing, test_live_only_with_tmp_reap_live
  never a directory a process  test_a_process_standing_in_it_keeps_it
  holds or stands in
  never a paused agent's       test_a_live_owner_keeps_it, test_envoy_unreachable_keeps_everything
  the full session id, never   test_a_prefix_owner_is_unowned
  a prefix
  a quiet owner is not consent test_no_owner_file_is_never_a_candidate

plus the walking rules: a symlink and a mount are never followed, and an entry becomes a
removal candidate only when all three conditions hold at once.
"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "devbox" / "session-scratch-reap"
LIVE = "01a18385-f8c8-74a9-b6ff-694fb5fe87a7"
DEAD = "01a13c57-d826-7242-80fe-5f0670188b11"
HOST = socket.gethostname().split(".")[0]
DAY = 86400


class FakeEnvoy:
    """A /v1/sessions endpoint. `rows` is what it answers; closing it makes it unreachable."""

    def __init__(self, rows):
        payload = json.dumps(rows).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's name
                if not self.path.endswith("/v1/sessions"):
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class TmpRootTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="tmp-reap-root-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.envoy = FakeEnvoy([
            {"session_id": LIVE, "host": HOST},
            {"session_id": DEAD, "host": "another-box"},
        ])
        self.addCleanup(self.envoy.close)

    def entry(self, name, owner=None, age_days=30, size=16):
        """One top-level entry: a directory, a file in it, and an OWNER when one is given."""
        path = self.root / name
        (path / "work").mkdir(parents=True)
        (path / "work" / "data").write_bytes(b"x" * size)
        if owner is not None:
            (path / "OWNER").write_text(owner + "\n")
        self.age(path, age_days)
        return path

    def age(self, path, days):
        when = os.stat(path).st_mtime - days * DAY
        for here, dirs, files in os.walk(path, topdown=False):
            for name in files + dirs:
                os.utime(os.path.join(here, name), (when, when), follow_symlinks=False)
        os.utime(path, (when, when))

    def run_reaper(self, *args, root=None, envoy=None, **env):
        done = subprocess.run(
            [str(SCRIPT), "--tmp", *args],
            capture_output=True,
            text=True,
            timeout=300,
            stdin=subprocess.DEVNULL,
            env={
                **os.environ,
                "TMP_REAP_ROOT": str(root or self.root),
                "ENVOY_URL": self.envoy.url if envoy is None else envoy,
                "TMP_REAP_IDLE_DAYS": "7",
                "TMP_REAP_REPORT_MIN_BYTES": "1",
                **{key: str(value) for key, value in env.items()},
            },
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout

    def summary(self, out):
        for line in out.splitlines():
            if f": {self.root}: " in line and " unowned (" in line:
                return line
        self.fail(f"no summary line in:\n{out}")

    # --- a quiet owner is not consent -------------------------------------------------------

    def test_no_owner_file_is_never_a_candidate(self):
        """Old, idle, held by nothing - and still never removed, because nobody claimed it."""
        quiet = self.entry("quiet-lane", owner=None)
        out = self.run_reaper()
        self.assertNotIn(f"would remove {quiet}", out)
        self.assertIn("1 unowned", self.summary(out))
        self.assertTrue(quiet.exists())

    def test_an_owner_that_is_not_a_session_id_is_unowned(self):
        for text in ("", "   \n", "# written by hand\n", f"{LIVE} {DEAD}\n", "01a18385"):
            with self.subTest(owner=text):
                shutil.rmtree(self.root, ignore_errors=True)
                self.root.mkdir()
                entry = self.entry("odd-owner")
                (entry / "OWNER").write_text(text)
                self.age(entry, 30)
                out = self.run_reaper()
                self.assertNotIn(f"would remove {entry}", out)
                self.assertIn("1 unowned", self.summary(out))

    # --- the full session id, never a prefix ------------------------------------------------

    def test_a_prefix_owner_is_unowned(self):
        """01a16551-5d7c and 01a16551-03d5 are different lanes; a prefix names neither."""
        entry = self.entry("prefix-lane", owner=None)
        (entry / "OWNER").write_text(DEAD[:13] + "\n")
        self.age(entry, 30)
        out = self.run_reaper()
        self.assertNotIn(f"would remove {entry}", out)
        self.assertIn("1 unowned", self.summary(out))

    # --- never a paused agent's directory ---------------------------------------------------

    def test_a_live_owner_keeps_it(self):
        entry = self.entry("live-lane", owner=LIVE)
        out = self.run_reaper()
        self.assertIn(f"keep {entry} (its owner {LIVE} is live on this box)", out)
        self.assertIn("1 with a live owner", self.summary(out))

    def test_an_owner_live_on_another_box_does_not_count(self):
        """Envoy lists every box; only a session on THIS host can be holding this disk."""
        entry = self.entry("elsewhere", owner=DEAD)
        out = self.run_reaper()
        self.assertIn(f"would remove {entry}", out)

    def test_envoy_unreachable_keeps_everything(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            closed = f"http://127.0.0.1:{sock.getsockname()[1]}"
        entry = self.entry("dead-lane", owner=DEAD)
        out = self.run_reaper(envoy=closed, TMP_REAP_ENVOY_SECONDS="2")
        self.assertIn("every OWNER counts as live this run", out)
        self.assertNotIn(f"would remove {entry}", out)
        self.assertIn("1 with a live owner", self.summary(out))

    # --- never a directory a process holds or stands in --------------------------------------

    def test_a_process_standing_in_it_keeps_it(self):
        entry = self.entry("held-lane", owner=DEAD)
        child = subprocess.Popen(["sleep", "120"], cwd=entry)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        out = self.run_reaper()
        self.assertIn(f"keep {entry} (live: pid {child.pid} via cwd)", out)
        self.assertIn("1 held by a process", self.summary(out))

    def test_an_open_file_in_it_keeps_it(self):
        entry = self.entry("open-lane", owner=DEAD)
        child = subprocess.Popen(
            ["bash", "-c", f'exec 9<"{entry}/work/data"; sleep 120'], cwd="/"
        )
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        out = self.run_reaper()
        self.assertIn(f"keep {entry} (live:", out)
        self.assertIn("1 held by a process", self.summary(out))

    # --- nothing written for 7 days -----------------------------------------------------------

    def test_a_recent_write_anywhere_inside_keeps_it(self):
        entry = self.entry("busy-lane", owner=DEAD)
        (entry / "work" / "fresh").write_text("just now\n")
        out = self.run_reaper()
        self.assertIn(f"keep {entry} (written 0d ago, inside the 7d window)", out)
        self.assertIn("1 written inside the 7d window", self.summary(out))

    def test_an_unreadable_subtree_keeps_its_own_entry_only(self):
        """A container that ran as root leaves files this user cannot read. That entry stays;
        the other 13,000 are still decided."""
        blind = self.entry("blind-lane", owner=DEAD)
        shut = blind / "work" / "shut"
        shut.mkdir()
        (shut / "data").write_bytes(b"x" * 16)
        self.age(blind, 30)
        shut.chmod(0o000)
        self.addCleanup(shut.chmod, 0o700)
        going = self.entry("gone-lane", owner=DEAD)

        out = self.run_reaper()
        self.assertIn(f"keep {blind} (owner {DEAD} is gone, but the walk of it could not be finished", out)
        self.assertIn(f"would remove {going}", out)
        self.assertIn("1 would be removed", self.summary(out))

    def test_would_remove_only_when_all_three_hold(self):
        going = self.entry("gone-lane", owner=DEAD)
        live = self.entry("live-lane", owner=LIVE)
        fresh = self.entry("fresh-lane", owner=DEAD)
        (fresh / "work" / "fresh").write_text("just now\n")
        quiet = self.entry("quiet-lane", owner=None)
        held = self.entry("held-lane", owner=DEAD)
        child = subprocess.Popen(["sleep", "120"], cwd=held)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)

        out = self.run_reaper()
        self.assertIn(f"would remove {going}", out)
        for kept in (live, fresh, quiet, held):
            self.assertNotIn(f"would remove {kept}", out)
        summary = self.summary(out)
        self.assertIn("1 would be removed", summary)
        self.assertIn("1 with a live owner", summary)
        self.assertIn("1 held by a process", summary)
        self.assertIn("1 written inside the 7d window", summary)
        self.assertIn("1 unowned", summary)

    # --- symlinks and mounts ------------------------------------------------------------------

    def test_a_symlink_is_never_followed(self):
        target = Path(tempfile.mkdtemp(prefix="tmp-reap-target-"))
        self.addCleanup(shutil.rmtree, target, ignore_errors=True)
        (target / "work").mkdir()
        (target / "work" / "data").write_bytes(b"x" * 16)
        (target / "OWNER").write_text(DEAD + "\n")
        self.age(target, 30)
        link = self.root / "link-lane"
        link.symlink_to(target)
        out = self.run_reaper()
        self.assertNotIn("would remove", out)
        self.assertIn("1 unowned", self.summary(out))
        self.assertTrue((target / "work" / "data").exists())

    def test_a_mount_point_is_not_crossed(self):
        """/mnt holds /mnt/nvme, a filesystem of its own. An entry on another device is never
        a candidate, whatever its OWNER says, and nothing inside it is walked."""
        if not os.access("/mnt/nvme", os.W_OK) or os.stat("/mnt").st_dev == os.stat("/mnt/nvme").st_dev:
            self.skipTest("no writable nested mount to test the device gate with")
        owner = Path("/mnt/nvme/OWNER")
        if owner.exists():
            self.skipTest("/mnt/nvme already holds an OWNER file")
        owner.write_text(DEAD + "\n")
        self.addCleanup(owner.unlink)
        out = self.run_reaper(root="/mnt")
        self.assertNotIn("would remove", out)
        self.assertNotIn("/mnt/nvme/", out)
        self.assertTrue(owner.exists())

    # --- report-only ---------------------------------------------------------------------------

    def test_report_only_removes_nothing(self):
        """No --dry-run, no TMP_REAP_LIVE: it decides, prints, and the directory is still there."""
        entry = self.entry("gone-lane", owner=DEAD)
        out = self.run_reaper()
        self.assertIn("report-only (TMP_REAP_LIVE unset)", self.summary(out))
        self.assertIn(f"would remove {entry}", out)
        for line in out.splitlines():
            self.assertNotIn("session-scratch-reap: removed ", line)
        self.assertTrue(entry.exists())

    def test_dry_run_with_tmp_reap_live_removes_nothing(self):
        entry = self.entry("gone-lane", owner=DEAD)
        out = self.run_reaper("--dry-run", TMP_REAP_LIVE="1")
        self.assertIn("report-only (--dry-run)", self.summary(out))
        self.assertTrue(entry.exists())

    def test_live_only_with_tmp_reap_live(self):
        """The removal path itself, so report-only is a gate and not a dead branch."""
        going = self.entry("gone-lane", owner=DEAD)
        kept = self.entry("live-lane", owner=LIVE)
        out = self.run_reaper(TMP_REAP_LIVE="1")
        self.assertIn(f"removed {going}", out)
        self.assertIn("1 removed", self.summary(out))
        self.assertFalse(going.exists())
        self.assertTrue(kept.exists())

    # --- the report ------------------------------------------------------------------------------

    def test_report_names_the_unowned_entries_largest_first(self):
        big = self.entry("big-quiet", owner=None, size=4096)
        small = self.entry("small-quiet", owner=None, size=16)
        owned = self.entry("live-lane", owner=LIVE)
        out = self.run_reaper("--report")
        listed = [line for line in out.splitlines() if "session-scratch-reap:   " in line]
        self.assertEqual(len(listed), 2, out)
        self.assertIn(str(big), listed[0])
        self.assertIn(str(small), listed[1])
        self.assertNotIn(str(owned), out.split("largest first")[1])

    def test_an_absent_root_is_not_an_error(self):
        out = self.run_reaper(root=str(self.root / "nowhere"))
        self.assertIn("is absent; nothing to reap", out)


if __name__ == "__main__":
    unittest.main()
