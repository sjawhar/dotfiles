#!/usr/bin/env python3
"""A fork never touches the live session's secretsd registration (dispatch://INFRA-1644).

scripts/omp-fork resumes a copy that keeps the live session's id. secretsd's registry is
keyed by that id alone, so the copy's secretsd plugin used to mint a token of its own,
REGISTER it under the live id -- displacing the live token and revoking its grants -- and
UNREGISTER that id on exit, after which every `secrets` call and every `forward browser
grant` in the live session answered UNKNOWN_TOKEN until it restarted. Measured on
2026-11-07: 58 of 154 live box sessions on sami-devbox were in that state, and an
end-to-end run against the real daemon turned a registered live token into UNKNOWN_TOKEN
in 8 seconds.

The copy therefore gets a runtime directory of its own, 0700 under its scratch root, and a
SECRETSD_SOCK inside it that nothing listens on: the plugin derives the token path from the
first and the socket from the second, so neither its token file nor its REGISTER can reach
the live session's.

Technique: a stub `omp` first on PATH that copies its own environment out before omp-fork
removes the scratch directory, and a caller XDG_RUNTIME_DIR that must come back untouched.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
FORK = DOTFILES / "scripts" / "omp-fork"
SID = "01a13c57-d826-7242-80fe-5f0670188b11"

ANSWER = json.dumps({
    "type": "message_end",
    "message": {
        "role": "assistant",
        "provider": "stub",
        "model": "stub",
        "usage": {"input": 1, "output": 1, "cost": {"total": 0.0}},
        "content": [{"type": "text", "text": "ok"}],
    },
})


class OmpForkSecretsd(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="omp-fork-secretsd-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.caller_runtime = self.root / "caller-run"
        (self.caller_runtime / "secretsd").mkdir(parents=True)
        self.live_token = self.caller_runtime / "secretsd" / f"{SID}.token"
        self.live_token.write_text("a" * 64, encoding="utf-8")

        self.transcript = self.root / f"2026-11-07T20-00-00-000Z_{SID}.jsonl"
        self.transcript.write_text(json.dumps({
            "type": "session", "version": 3, "id": SID,
            "timestamp": "2026-11-07T20:00:00.000Z", "cwd": str(self.root),
        }) + "\n", encoding="utf-8")

        self.envfile = self.root / "fork-env.json"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        stub = self.bin / "omp"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "seen = dict(os.environ)\n"
            "seen['_runtime_mode'] = oct(os.stat(os.environ['XDG_RUNTIME_DIR']).st_mode & 0o777)\n"
            f"open({str(self.envfile)!r}, 'w').write(json.dumps(seen))\n"
            f"print({ANSWER!r})\n",
            encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "XDG_RUNTIME_DIR": str(self.caller_runtime),
            "XDG_CONFIG_HOME": str(self.root / "config"),
        }
        self.env.pop("ENVOY_CONFIG_FILE", None)
        self.env.pop("SECRETSD_SOCK", None)

    def run_fork(self) -> dict[str, str]:
        proc = subprocess.run([str(FORK), str(self.transcript), "say ok"],
                              cwd=str(self.root), env=self.env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(self.envfile.read_text(encoding="utf-8"))

    def test_runtime_dir_is_the_forks_own(self) -> None:
        env = self.run_fork()
        runtime = Path(env["XDG_RUNTIME_DIR"])
        self.assertNotEqual(runtime, self.caller_runtime)
        self.assertEqual(runtime.name, "run")
        self.assertTrue(runtime.parent.name.startswith("omp-fork-"))

    def test_secretsd_socket_is_inside_it_and_dead(self) -> None:
        env = self.run_fork()
        sock = Path(env["SECRETSD_SOCK"])
        self.assertEqual(sock.parent, Path(env["XDG_RUNTIME_DIR"]))
        self.assertFalse(sock.exists())

    def test_an_exported_secretsd_sock_does_not_reach_the_fork(self) -> None:
        self.env["SECRETSD_SOCK"] = "/run/user/1000/secretsd.sock"
        env = self.run_fork()
        self.assertNotEqual(env["SECRETSD_SOCK"], "/run/user/1000/secretsd.sock")

    def test_the_live_token_file_is_untouched(self) -> None:
        before = self.live_token.stat()
        self.run_fork()
        after = self.live_token.stat()
        self.assertEqual(self.live_token.read_text(encoding="utf-8"), "a" * 64)
        self.assertEqual((before.st_mtime_ns, before.st_ino), (after.st_mtime_ns, after.st_ino))

    def test_the_runtime_dir_is_private(self) -> None:
        # The plugin refuses a runtime root other users may write ("writable by other
        # users"), and the 002 umask these sessions run under makes a plain mkdir 0775, so
        # the mode is set at creation. The stub reads it from inside the fork, before
        # omp-fork removes the scratch tree.
        env = self.run_fork()
        self.assertEqual(env["_runtime_mode"], "0o700")


if __name__ == "__main__":
    unittest.main()
