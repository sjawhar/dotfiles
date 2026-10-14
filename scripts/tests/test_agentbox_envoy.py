#!/usr/bin/env python3
"""scripts/agentbox passes a box no variable that overrides an envoy.json field.

A box session reads Dispatch and Envoy from the envoy.json its profile reads, as a host session
does: ENVOY_URL, ENVOY_NATS_URL or NATS_NKEY_SEED_FILE in the box's environment would override the
file's field, and turn Envoy on for a named profile that has no envoy.json. The box reaches the
host's listener at the file's own loopback URL through its entrypoint's relay, so the launcher
names that relay's target instead (AGENTBOX_ENVOY_FORWARDER, the host forwarder at the network
gateway), and mounts the host's ~/.config/legion read-only, where the neutral envoy.json lives.

Technique: source scripts/agentbox (its source guard runs nothing) with `docker`, `git`,
`agent-secrets`, `curl` and `sleep` stubbed first on PATH, and run cmd_new with the steps that
need a real host (image, network, clones, mounts) replaced by no-ops; session_env is the real one.
DOTFILES_DIR is a scratch tree whose scripts/envoy-nats-seed prints a seed path, and the caller
exports ENVOY_NATS_URL, so a launcher that still forwarded either would pass it.
"""

from __future__ import annotations

import socket
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"
GATEWAY = "172.31.0.1"
ENVOY_VARS = ("ENVOY_URL", "ENVOY_NATS_URL", "NATS_NKEY_SEED_FILE")


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


# `docker run` records its arguments, one per line; every other docker call answers nothing.
DOCKER_STUB = r"""
[[ "$1" == run ]] && { shift; printf '%s\n' "$@" >"$STUB_RUN_ARGS"; }
exit 0
"""

NO_HOST = (
    'image_tag() { echo agentbox:test; }; build_image() { :; }; ensure_forwarders() { :; }\n'
    f'ensure_network() {{ NETWORK_GATEWAY={GATEWAY} NETWORK_SUBNET=172.31.0.0/16; }}\n'
    "mount_args() { :; }; clone_checkout() { :; }; close_box() { :; }\n"
)


class AgentboxEnvoy(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        (self.home / ".config").mkdir(parents=True)
        (self.root / "src" / "repo1").mkdir(parents=True)
        stubs = self.root / "bin"
        write_stub(stubs, "docker", DOCKER_STUB)
        write_stub(stubs, "git", "exit 0")
        write_stub(stubs, "curl", "exit 7")
        write_stub(stubs, "sleep", "exit 0")
        # No launcher credential: the box gets no broker identity, and nothing else changes.
        write_stub(stubs, "agent-secrets", 'echo none; exit 1')
        self.seed = self.root / "run" / "envoy" / "nats-nkey-seed-omp-session"
        write_stub(self.root / "dotfiles" / "scripts", "envoy-nats-seed", f'echo "{self.seed}"')
        self.run_args = self.root / "run-args"
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{stubs}:/usr/bin:/bin",
            "DOTFILES_DIR": str(self.root / "dotfiles"),
            "AGENTBOX_SRC": str(self.root / "src"),
            "AGENT_SECRETS_URL": "https://broker.example",
            "STUB_RUN_ARGS": str(self.run_args),
            "ENVOY_NATS_URL": "nats://caller.internal.example:4222",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def box_env(self, *command: str) -> list[str]:
        """The -e values `docker run` got for a box running command."""
        script = f'source "{AGENTBOX}"\n{NO_HOST}cmd_new repo1 -- {" ".join(command)}\n'
        result = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, env=self.env, check=False, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.run_args.read_text(encoding="utf-8").splitlines()
        return [args[i + 1] for i, arg in enumerate(args) if arg == "-e"]

    def test_no_box_gets_an_envoy_variable(self) -> None:
        for name, command in (("default profile", ("omp",)), ("named profile", ("omp", "--profile", "theorem"))):
            with self.subTest(name):
                values = self.box_env(*command)
                names = {value.split("=", 1)[0] for value in values}
                self.assertEqual(names & set(ENVOY_VARS), set(), values)
                self.assertIn(f"AGENTBOX_ENVOY_FORWARDER={GATEWAY}:9020", values)

    def test_a_box_mounts_the_neutral_file_directory_read_only(self) -> None:
        """agentbox/mounts lists ~/.config/legion read-only, so a box session without a profile of
        its own reads the neutral envoy.json, and a named profile's link to it resolves, while no
        box can rewrite it. The real mount_args against the real mounts file, with the host paths
        it writes under (the box's runtime dir, secretsd's socket) moved into scratch; it still
        creates /run/user/<uid>/forward and /run/user/<uid>/envoy when they are missing, as every
        launch does."""
        legion = self.home / ".config" / "legion"
        legion.mkdir(parents=True)
        secretsd = self.root / "secretsd.sock"
        listener = socket.socket(socket.AF_UNIX)
        self.addCleanup(listener.close)
        listener.bind(str(secretsd))
        script = (
            f'source "{AGENTBOX}"\n'
            f'MOUNTS_FILE="{DOTFILES / "agentbox" / "mounts"}" RUN_BASE="{self.root / "run-base"}" '
            f'BUS_SOCK="{self.root / "run-base" / "bus" / "bus"}" SECRETSD_SOCK="{secretsd}"\n'
            "mount_args box1\n"
        )
        result = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, env=self.env, check=False, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        args = result.stdout.splitlines()
        binds = [args[i + 1] for i, arg in enumerate(args) if arg == "-v"]
        self.assertIn(f"{legion.resolve()}:{legion}:ro", binds)
        self.assertEqual([bind for bind in binds if bind.endswith(f":{legion}")], [], binds)


if __name__ == "__main__":
    unittest.main()
