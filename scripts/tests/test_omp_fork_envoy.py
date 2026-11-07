#!/usr/bin/env python3
"""A fork cannot reach Envoy, and no prompt is allowed to be what stops it (INFRA-1644).

scripts/omp-fork replays a live session's transcript, so the copy believes it is that session.
On 2026-11-07 a change (dotfiles c71c9635, about 21:05Z) removed the fork's Envoy isolation on
the reasoning that a fork with its own session id can no longer collide with its parent. It
collided in a worse way: the copies registered with Envoy carrying their PARENT's title and
subscriptions, so for 34 minutes `envoy ps` showed twins of the SRE, LPS and PIPE sessions. One
of them pushed a merge commit to its parent's branch with its own Omp-Session trailer.

The fork's brief says, in its own words, "change nothing and message no one" and "Never edit,
commit, launch or write". **That instruction did not hold**, which is the whole reason this file
exists: the control has to be mechanical. Reverted as 1530f24c.

So these check the mechanism, from inside the fork's own environment:
  * the fork is handed an Envoy config of its own, not the live one;
  * that config has `envoy.enabled` false, while its Dispatch section survives, since a fork is
    allowed to READ Dispatch;
  * `OMP_PROFILE` is `default`, because pi-envoy reads a named profile's own envoy.json and
    ignores ENVOY_CONFIG_FILE entirely — a fork started from a profiled session would otherwise
    find the live configuration by another road;
  * an ENVOY_CONFIG_FILE exported by the caller does not reach the fork.

Technique, as in test_omp_fork_secretsd.py: a stub `omp` first on PATH copies its environment and
the config it was handed out of the scratch tree before omp-fork removes it.
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


class OmpForkEnvoy(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="omp-fork-envoy-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

        self.config_home = self.root / "config"
        (self.config_home / "legion").mkdir(parents=True)
        self.live_config = self.config_home / "legion" / "envoy.json"
        self.live_config.write_text(json.dumps({
            "envoy": {"enabled": True, "url": "http://127.0.0.1:9020", "token": "live-token"},
            "dispatch": {"url": "https://dispatch.example", "token": "dispatch-token"},
        }), encoding="utf-8")

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
            "import json, os\n"
            "seen = dict(os.environ)\n"
            "handed = os.environ.get('ENVOY_CONFIG_FILE')\n"
            "seen['_handed_config'] = open(handed).read() if handed and os.path.exists(handed) else ''\n"
            f"open({str(self.envfile)!r}, 'w').write(json.dumps(seen))\n"
            f"print({ANSWER!r})\n",
            encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "XDG_CONFIG_HOME": str(self.config_home),
            "XDG_RUNTIME_DIR": str(self.root / "run"),
        }
        (self.root / "run").mkdir()
        self.env.pop("ENVOY_CONFIG_FILE", None)

    def run_fork(self) -> dict[str, str]:
        proc = subprocess.run([str(FORK), str(self.transcript), "say ok"],
                              cwd=str(self.root), env=self.env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(self.envfile.read_text(encoding="utf-8"))

    def test_the_fork_gets_a_config_of_its_own(self) -> None:
        env = self.run_fork()
        handed = Path(env["ENVOY_CONFIG_FILE"])
        self.assertNotEqual(handed, self.live_config)
        self.assertTrue(handed.parent.name.startswith("omp-fork-"), handed)

    def test_envoy_is_off_in_it(self) -> None:
        config = json.loads(self.run_fork()["_handed_config"])
        self.assertIs(config["envoy"]["enabled"], False)

    def test_dispatch_survives_because_a_fork_may_read(self) -> None:
        config = json.loads(self.run_fork()["_handed_config"])
        self.assertEqual(config["dispatch"]["url"], "https://dispatch.example")

    def test_the_profile_is_default_so_a_named_one_cannot_let_envoy_back_in(self) -> None:
        # pi-envoy reads $PI_CODING_AGENT_DIR/envoy.json for a named profile and ignores
        # ENVOY_CONFIG_FILE, and that file links to the live config, so a fork of a profiled
        # session would reach Envoy by the other road.
        self.env["OMP_PROFILE"] = "pbc"
        self.assertEqual(self.run_fork()["OMP_PROFILE"], "default")

    def test_an_exported_config_file_does_not_reach_the_fork(self) -> None:
        self.env["ENVOY_CONFIG_FILE"] = str(self.live_config)
        env = self.run_fork()
        self.assertNotEqual(env["ENVOY_CONFIG_FILE"], str(self.live_config))
        self.assertIs(json.loads(env["_handed_config"])["envoy"]["enabled"], False)

    def test_the_live_config_is_not_modified(self) -> None:
        before = self.live_config.read_text(encoding="utf-8")
        self.run_fork()
        self.assertEqual(self.live_config.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
