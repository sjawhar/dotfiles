#!/usr/bin/env python3
"""A fork runs under omp's own `--fork`, so it never carries the live session's id
(dispatch://INFRA-1644).

scripts/omp-fork used to `--resume` its copy, which kept the source session's id. Everything
keyed by session id then saw a second copy of the live session: Envoy's registry of sessions,
roles and messages took the copy as the live session (2026-11-05), and secretsd's registry of
tokens and grants did the same (2026-11-07) -- the copy's REGISTER revoked the live session's
grants and its exit UNREGISTERed the id, after which every `secrets` call and every
`forward browser grant` in 59 of 153 live sessions answered UNKNOWN_TOKEN until it restarted.

`omp --fork <path>` resolves before any session is created and gives the run a session id of
its own, with `parentSession` set to the source. The two workarounds that existed only because
the copy kept the parent's id -- an `envoy.enabled: false` config plus `OMP_PROFILE=default`,
and a private `XDG_RUNTIME_DIR` with a dead `SECRETSD_SOCK` -- are therefore gone, and the fork
runs with the caller's own environment.

Technique: a stub `omp` first on PATH that writes its own argv and environment out before
omp-fork removes the scratch directory.
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


class OmpForkFlag(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="omp-fork-flag-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.caller_runtime = self.root / "caller-run"
        (self.caller_runtime / "secretsd").mkdir(parents=True)
        self.live_token = self.caller_runtime / "secretsd" / f"{SID}.token"
        self.live_token.write_text("a" * 64, encoding="utf-8")

        self.transcript = self.root / f"2026-11-07T20-00-00-000Z_{SID}.jsonl"
        self.transcript.write_text("".join(line + "\n" for line in [
            json.dumps({"type": "session", "version": 3, "id": SID,
                        "timestamp": "2026-11-07T20:00:00.000Z", "cwd": str(self.root)}),
            json.dumps({"type": "message", "message": {"role": "user",
                        "content": [{"type": "text", "text": "first"}]}}),
            json.dumps({"type": "message", "message": {"role": "user",
                        "content": [{"type": "text", "text": "second"}]}}),
        ]), encoding="utf-8")

        self.callfile = self.root / "fork-call.json"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        stub = self.bin / "omp"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "call = {'argv': sys.argv[1:], 'env': dict(os.environ), 'cwd': os.getcwd()}\n"
            "forked = sys.argv[sys.argv.index('--fork') + 1] if '--fork' in sys.argv else None\n"
            "call['forked_text'] = open(forked).read() if forked else None\n"
            f"open({str(self.callfile)!r}, 'w').write(json.dumps(call))\n"
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
        self.env.pop("OMP_PROFILE", None)
        self.env.pop("PI_PROFILE", None)
        self.env.pop("PI_CODING_AGENT_DIR", None)

    def run_fork(self, *extra: str) -> dict:
        proc = subprocess.run([str(FORK), *extra, str(self.transcript), "say ok"],
                              cwd=str(self.root), env=self.env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        call = json.loads(self.callfile.read_text(encoding="utf-8"))
        call["stdout"] = json.loads(proc.stdout.strip().splitlines()[-1])
        call["stderr"] = proc.stderr
        return call

    def test_omp_is_asked_to_fork_the_copy_and_never_to_resume_it(self) -> None:
        call = self.run_fork()
        argv = call["argv"]
        self.assertIn("--fork", argv)
        self.assertNotIn("--resume", argv)
        self.assertNotIn("-r", argv)
        self.assertFalse([a for a in argv if a.startswith("--resume=")], argv)
        forked = Path(argv[argv.index("--fork") + 1])
        self.assertEqual(forked.name, self.transcript.name)
        self.assertTrue(forked.parent.name.startswith("omp-fork-"), forked)
        self.assertEqual(argv[argv.index("--session-dir") + 1], str(forked.parent))

    def test_the_original_is_never_handed_to_omp(self) -> None:
        before = self.transcript.stat()
        call = self.run_fork()
        self.assertNotIn(str(self.transcript), call["argv"])
        after = self.transcript.stat()
        self.assertEqual((before.st_mtime_ns, before.st_ino), (after.st_mtime_ns, after.st_ino))

    def test_at_still_cuts_the_copy_omp_forks(self) -> None:
        # omp's --fork takes the whole file it is given, so the cut is this script's own work.
        call = self.run_fork("--at", "2")
        self.assertEqual(call["stdout"]["cut"], 2)
        self.assertEqual(len(call["forked_text"].splitlines()), 2)
        self.assertNotIn("second", call["forked_text"])

    def test_the_fork_keeps_the_callers_environment(self) -> None:
        # The Envoy and secretsd isolation existed only because the copy kept the parent's id.
        self.env["OMP_PROFILE"] = "pbc"
        env = self.run_fork()["env"]
        self.assertEqual(env["XDG_RUNTIME_DIR"], str(self.caller_runtime))
        self.assertNotIn("SECRETSD_SOCK", env)
        self.assertNotIn("ENVOY_CONFIG_FILE", env)
        self.assertEqual(env["OMP_PROFILE"], "pbc")

    def test_a_named_profile_is_no_longer_warned_about(self) -> None:
        self.env["OMP_PROFILE"] = "pbc"
        self.assertEqual(self.run_fork()["stderr"], "")

    def test_the_live_token_file_is_untouched(self) -> None:
        before = self.live_token.stat()
        self.run_fork()
        after = self.live_token.stat()
        self.assertEqual((before.st_mtime_ns, before.st_ino), (after.st_mtime_ns, after.st_ino))


if __name__ == "__main__":
    unittest.main()
