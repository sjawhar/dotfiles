#!/usr/bin/env python3
"""Finish only this box, after disabling its own restart entry; never stop a real box."""

from __future__ import annotations

import json
import shlex
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"
SID = "01a19700-0000-7000-8000-000000000d09"
OTHER = "01a19700-0000-7000-8000-000000000d10"
STAMP = "2026-11-05T09:00:00Z"


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class AgentboxFinish(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.stubs = self.root / "bin"
        self.stubs.mkdir()
        self.identity = self.root / "identity"
        self.identity.write_text("agentbox\n", encoding="utf-8")
        self.durable = self.root / "durable" / "host-user"
        self.registry = self.durable / "restart-registry" / "sessions"
        self.registry.mkdir(parents=True)
        self.entry = self.registry / f"{SID}.entry"
        self.original = (
            f"session={SID}\ntitle=FINISH\nresume=agentbox new --dir /work -- omp --resume {SID}\n"
            "auto_restart=yes\nowner_note=Standing work\nafter=Do not change me\n"
        )
        self.entry.write_text(self.original, encoding="utf-8")
        self.entry.chmod(0o640)
        self.other = self.registry / f"{OTHER}.entry"
        self.other.write_text(f"session={OTHER}\nauto_restart=yes\nowner_note=Other owner\n", encoding="utf-8")
        self.tmux_call = self.root / "tmux.json"
        self.ssh_call = self.root / "ssh.json"
        self.at_stop = self.root / "entry-at-stop"
        self.kill_call = self.root / "kill-called"
        # Record the registry at the irreversible boundary, not only after cmd_finish returns.
        write_stub(self.stubs, "tmux", '''python3 - "$@" <<'PY'
import json, os, pathlib, sys
pathlib.Path(os.environ["STUB_TMUX"]).write_text(json.dumps(sys.argv[1:]))
entry = pathlib.Path(os.environ["STUB_ENTRY"])
pathlib.Path(os.environ["STUB_AT_STOP"]).write_bytes(entry.read_bytes() if entry.exists() else b"MISSING")
PY
exit "${STUB_TMUX_EXIT:-0}"''')
        write_stub(self.stubs, "ssh", '''python3 - "$@" <<'PY'
import json, os, pathlib, sys
pathlib.Path(os.environ["STUB_SSH"]).write_text(json.dumps(sys.argv[1:]))
pathlib.Path(os.environ["STUB_AT_STOP"]).write_bytes(pathlib.Path(os.environ["STUB_ENTRY"]).read_bytes())
PY
exit "${STUB_SSH_EXIT:-0}"''')
        write_stub(self.stubs, "docker", 'echo "unexpected Docker call" >&2; exit 90')
        write_stub(self.stubs, "date", f"printf '%s\\n' '{STAMP}'")
        write_stub(self.stubs, "mountpoint", "exit 1")
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "DOTFILES_DIR": str(DOTFILES),
            "AGENTBOX_IDENTITY_FILE": str(self.identity),
            "AGENTBOX_BOX": "agentbox-aaaaaaaaaaaa",
            "AGENTBOX_HOST": "test-host",
            "AGENTBOX_DURABLE": str(self.durable),
            "AGENTBOX_BOXES": str(self.root / "boxes"),
            "OMP_SESSION_ID": SID,
            "TMUX_PANE": "%7",
            "STUB_TMUX": str(self.tmux_call),
            "STUB_SSH": str(self.ssh_call),
            "STUB_ENTRY": str(self.entry),
            "STUB_AT_STOP": str(self.at_stop),
            "STUB_KILL": str(self.kill_call),
        }

    def run_finish(self, *args: str, extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        # kill is a shell builtin, so a PATH stub alone would NOT make an accidental kill safe.
        script = f'''source {shlex.quote(str(AGENTBOX))}
kill() {{ printf '%s\\n' "$*" >>"$STUB_KILL"; return 90; }}
cmd_finish "$@"
'''
        return subprocess.run(["bash", "-c", script, "finish", *args],
                              env={**self.env, **(extra or {})}, check=False,
                              capture_output=True, text=True, timeout=30)

    def assert_untouched(self) -> None:
        self.assertEqual(self.entry.read_text(encoding="utf-8"), self.original)
        self.assertFalse(self.tmux_call.exists())
        self.assertFalse(self.ssh_call.exists())
        self.assertFalse(self.kill_call.exists())

    def test_refuses_outside_a_box_even_with_a_box_variable(self) -> None:
        self.identity.unlink()
        result = self.run_finish()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("box-only", result.stderr)
        self.assertIn("host", result.stderr)
        self.assertIn("docker stop", result.stderr)
        self.assert_untouched()

    def test_records_reason_and_utc_time_before_respawning_only_its_pane(self) -> None:
        other = self.other.read_bytes()
        result = self.run_finish("--reason", "All tasks complete; handoff sent")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        recorded = self.entry.read_text(encoding="utf-8")
        self.assertIn("auto_restart=no\n", recorded)
        self.assertIn(f"owner_note=Finished deliberately at {STAMP}: All tasks complete; handoff sent\n", recorded)
        self.assertNotIn("auto_restart=yes", recorded)
        self.assertNotIn("Standing work", recorded)
        self.assertIn("after=Do not change me\n", recorded)
        self.assertEqual(self.at_stop.read_text(encoding="utf-8"), recorded)
        self.assertEqual(stat.S_IMODE(self.entry.stat().st_mode), 0o640)
        self.assertEqual(self.other.read_bytes(), other)
        call = json.loads(self.tmux_call.read_text(encoding="utf-8"))
        self.assertEqual(call[:6], ["respawn-pane", "-k", "-t", "%7", "-c", str(self.home)])
        self.assertEqual(call[6:8], ["bash", "-c"])
        # Execute the replacement shell's command safely: the stub bash reports its arguments.
        write_stub(self.stubs, "bash", 'printf "SHELL:%s\\n" "$*"')
        shell = subprocess.run(["/bin/bash", "-c", call[8]], env=self.env,
                               check=False, capture_output=True, text=True, timeout=10)
        self.assertEqual(shell.returncode, 0, shell.stderr)
        self.assertIn(f"agentbox: session {SID} finished deliberately; this pane is a shell again.\n", shell.stdout)
        self.assertIn("SHELL:-i", shell.stdout)
        self.assertFalse(self.kill_call.exists())

    def test_headless_records_finish_before_stopping_its_box_through_host_ssh(self) -> None:
        result = self.run_finish(extra={"TMUX_PANE": ""})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        recorded = self.entry.read_text(encoding="utf-8")
        self.assertIn("auto_restart=no\n", recorded)
        self.assertEqual(self.at_stop.read_text(encoding="utf-8"), recorded)
        self.assertEqual(json.loads(self.ssh_call.read_text(encoding="utf-8")),
                         ["-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "test-host",
                          "docker", "stop", "agentbox-aaaaaaaaaaaa"])
        self.assertFalse(self.tmux_call.exists())
        self.assertFalse(self.kill_call.exists())

    def test_dry_run_shows_record_and_pane_command_without_writes(self) -> None:
        before = self.entry.stat().st_mtime_ns
        result = self.run_finish("--dry-run", "--reason", "Work is done")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"would update {self.entry}", result.stdout)
        self.assertIn("auto_restart=no", result.stdout)
        self.assertIn(f"owner_note=Finished deliberately at {STAMP}: Work is done", result.stdout)
        line = next(line for line in result.stdout.splitlines() if line.startswith("  tmux "))
        call = shlex.split(line)
        self.assertEqual(call[:7], ["tmux", "respawn-pane", "-k", "-t", "%7", "-c", str(self.home)])
        self.assertIn("exec bash -i", call[-1])
        self.assertEqual(self.entry.stat().st_mtime_ns, before)
        self.assert_untouched()
        self.assertEqual(sorted(p.name for p in self.registry.iterdir()), [self.entry.name, self.other.name])

    def test_headless_dry_run_shows_host_ssh_stop_without_writes(self) -> None:
        result = self.run_finish("--dry-run", extra={"TMUX_PANE": ""})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("auto_restart=no", result.stdout)
        line = next(line for line in result.stdout.splitlines() if line.startswith("  ssh "))
        self.assertEqual(shlex.split(line),
                         ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "test-host",
                          "docker", "stop", "agentbox-aaaaaaaaaaaa"])
        self.assert_untouched()

    def test_dry_run_cli_does_not_create_a_scratch_root(self) -> None:
        scratch = self.root / "absent-scratch"
        result = subprocess.run(["bash", str(AGENTBOX), "finish", "--dry-run"],
                                env={**self.env, "AGENTBOX_SCRATCH": str(scratch)},
                                check=False, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("would end box", result.stdout)
        self.assertFalse(scratch.exists())
        self.assert_untouched()

    def test_unregistered_session_still_respawns_and_creates_no_entry(self) -> None:
        self.entry.unlink()
        result = self.run_finish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no restart-registry entry", result.stdout)
        self.assertIn("nothing to restart", result.stdout)
        self.assertFalse(self.entry.exists())
        self.assertTrue(self.tmux_call.exists())
        self.assertEqual(self.at_stop.read_text(encoding="utf-8"), "MISSING")

    def test_refuses_a_registry_entry_for_another_session(self) -> None:
        self.entry.write_text(self.original.replace(f"session={SID}", f"session={OTHER}"), encoding="utf-8")
        before = self.entry.read_bytes()
        result = self.run_finish()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("nothing was ended", result.stderr)
        self.assertEqual(self.entry.read_bytes(), before)
        self.assertFalse(self.tmux_call.exists())

    def test_refuses_symlink_to_another_sessions_entry(self) -> None:
        self.entry.unlink()
        self.entry.symlink_to(self.other)
        before = self.other.read_bytes()
        result = self.run_finish()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("nothing was ended", result.stderr)
        self.assertTrue(self.entry.is_symlink())
        self.assertEqual(self.other.read_bytes(), before)
        self.assertFalse(self.tmux_call.exists())

    def test_failed_registry_write_does_not_end_the_box(self) -> None:
        write_stub(self.stubs, "python3", 'echo "registry write failed" >&2; exit 1')
        result = self.run_finish()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("nothing was ended", result.stderr)
        self.assert_untouched()

    def test_inaccessible_registry_is_not_mistaken_for_an_unregistered_session(self) -> None:
        self.registry.chmod(0)
        try:
            result = self.run_finish()
        finally:
            self.registry.chmod(0o700)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("nothing was ended", result.stderr)
        self.assert_untouched()

    def test_relay_failure_leaves_restart_disabled_and_explains_host_recovery(self) -> None:
        result = self.run_finish(extra={"STUB_TMUX_EXIT": "1"})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("auto_restart=no\n", self.entry.read_text(encoding="utf-8"))
        self.assertIn("could not respawn pane %7", result.stderr)
        self.assertIn("docker stop agentbox-aaaaaaaaaaaa", result.stderr)

    def test_failed_host_ssh_leaves_restart_disabled_and_gives_host_stop_command(self) -> None:
        result = self.run_finish(extra={"TMUX_PANE": "", "STUB_SSH_EXIT": "255"})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("auto_restart=no\n", self.entry.read_text(encoding="utf-8"))
        self.assertIn("could not stop", result.stderr)
        self.assertIn("host session on test-host", result.stderr)
        self.assertIn("docker stop agentbox-aaaaaaaaaaaa", result.stderr)
        self.assertFalse(self.kill_call.exists())

    def test_rejects_bad_identity_or_multiline_reason_before_recording(self) -> None:
        for args, extra in [
            ((), {"OMP_SESSION_ID": ""}),
            ((), {"OMP_SESSION_ID": "../other"}),
            ((), {"AGENTBOX_BOX": "other-box"}),
            ((), {"TMUX_PANE": "", "AGENTBOX_HOST": "-oProxyCommand=bad"}),
            ((), {"PI_SESSION_FILE": str(self.root / "Subagent.jsonl")}),
            (("--reason", "Done\nauto_restart=yes"), {}),
            (("--reason",), {}),
            (("--unknown",), {}),
        ]:
            with self.subTest(args=args, extra=extra):
                result = self.run_finish(*args, extra=extra)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assert_untouched()


if __name__ == "__main__":
    unittest.main()
