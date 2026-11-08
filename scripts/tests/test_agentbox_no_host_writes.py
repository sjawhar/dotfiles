#!/usr/bin/env python3
"""`agentbox new --no-host-writes`: the box gets no writable host path it was not given.

A box binds three sets of host paths writable, and a harness that runs replayed or untrusted
sessions with their tools on can write through all three. On 2026-11-08 one did
(dispatch://BET-831): sandboxed copies wrote another session's hand-off log, three of one
lane's files and four folders of another's, under /home/sami/src, plus files on the durable
disk. The third surface is the one nobody names: ~/.dotfiles/agentbox/mounts binds about fifty
home paths read-write into every box, ~/.omp (every session's transcripts), ~/.ssh and
~/.local/bin among them.

The flag closes all three:
  - no /mnt/durable/<user> bind;
  - $SRC redirected to an empty per-box directory, so /home/<user>/src is unbound and
    box-private (the bind is unconditional, and an empty $SRC would hand docker `-v ":"`);
  - every home allow-list entry read-only, so the box still READS the logins it needs.

~/.omp read-only would leave omp unable to write its own transcript, so the session store
becomes a private directory under the box, as ~/.omp/run already is. A --dir is the explicit
way back to one writable host path, and still works.

Technique, as test_agentbox_limits.py: source scripts/agentbox with the host tools stubbed and
run cmd_new with the host steps replaced -- but keeping mount_args REAL, since it is what this
flag changes.
"""

from __future__ import annotations

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"
GATEWAY = "172.31.0.1"

DOCKER_STUB = r"""
[[ "$1" == run ]] && { shift; printf '%s\n' "$@" >"$STUB_RUN_ARGS"; }
exit 0
"""

# Everything that needs a real host, EXCEPT mount_args, which is what is under test.
# box_cache_mounts shells out to the build caches; its binds are not this flag's business.
NO_HOST = (
    "image_tag() { echo agentbox:test; }; build_image() { :; }; ensure_forwarders() { :; }\n"
    f"ensure_network() {{ NETWORK_GATEWAY={GATEWAY} NETWORK_SUBNET=172.31.0.0/16; }}\n"
    "clone_checkout() { :; }; close_box() { :; }\n"
    "box_cache_mounts() { :; }\n"
)


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class AgentboxNoHostWrites(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        (self.home / ".config").mkdir(parents=True)
        (self.home / ".omp" / "agent" / "sessions").mkdir(parents=True)
        (self.root / "src" / "repo1").mkdir(parents=True)
        self.durable = self.root / "durable"
        self.durable.mkdir()
        self.work = self.root / "work"
        self.work.mkdir()
        stubs = self.root / "bin"
        write_stub(stubs, "docker", DOCKER_STUB)
        for name in ("git", "sleep"):
            write_stub(stubs, name, "exit 0")
        write_stub(stubs, "curl", "exit 7")
        write_stub(stubs, "agent-secrets", "echo none; exit 1")
        write_stub(stubs, "sudo", "exit 0")
        write_stub(stubs, "mkfs.ext4", "exit 0")
        write_stub(stubs, "df", "printf 'Avail\\n%s\\n' \"${STUB_FREE_BYTES:-1099511627776}\"")
        write_stub(stubs, "mountpoint", "exit 1")
        dot = self.root / "dotfiles"
        write_stub(dot / "scripts", "envoy-nats-seed", "exit 1")
        write_stub(dot / "scripts", "agentbox-docker-snapshot", "exit 0")
        (dot / "agent-secrets").symlink_to(DOTFILES / "agent-secrets")
        # The real allow-list, so the test binds what production binds.
        (dot / "agentbox").symlink_to(DOTFILES / "agentbox")
        self.run_args = self.root / "run-args"
        self.env = {
            "AGENTBOX_IDENTITY_FILE": str(self.root / "not-a-box"),
            "AGENTBOX_BOX": "",
            "AGENTBOX_HOST": "sami-rocqperf",
            "HOME": str(self.home),
            "PATH": f"{stubs}:/usr/bin:/bin",
            "DOTFILES_DIR": str(dot),
            "AGENTBOX_SRC": str(self.root / "src"),
            "AGENTBOX_DURABLE": str(self.durable),
            "AGENT_SECRETS_URL": "https://broker.example",
            "STUB_RUN_ARGS": str(self.run_args),
            "AGENTBOX_BOXES": str(self.root / "boxes"),
            "AGENTBOX_DISKS": str(self.root / "disks"),
            "AGENTBOX_DISK": "0",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def binds(self, *options: str) -> list[str]:
        """The -v arguments of the docker run this launch would make."""
        script = f'source "{AGENTBOX}"\n{NO_HOST}cmd_new {" ".join(options)} repo1 -- omp\n'
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                env=self.env, check=False, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        args = self.run_args.read_text(encoding="utf-8").splitlines()
        return [args[i + 1] for i, a in enumerate(args) if a == "-v" and i + 1 < len(args)]

    @staticmethod
    def dest(bind: str) -> str:
        parts = bind.split(":")
        return parts[1] if len(parts) > 1 else ""

    def dest_binds(self, binds: list[str], destination: str) -> list[str]:
        return [b for b in binds if self.dest(b) == destination]

    # --- the default, so each assertion below is a change and not a coincidence ---

    def test_by_default_src_durable_and_omp_are_writable_host_paths(self) -> None:
        binds = self.binds()
        src = self.dest_binds(binds, f"{self.root}/src")
        self.assertTrue(src, f"~/src is bound by default: {binds}")
        self.assertFalse(any(b.endswith(":ro") for b in src), src)
        self.assertTrue(self.dest_binds(binds, str(self.durable)),
                        f"the durable disk is bound by default: {binds}")
        omp = self.dest_binds(binds, f"{self.home}/.omp")
        self.assertTrue(omp, f"~/.omp is bound by default: {binds}")
        self.assertFalse(any(b.endswith(":ro") for b in omp),
                         f"and writable, which is the hazard: {omp}")

    # --- the flag ---

    def test_durable_is_not_bound(self) -> None:
        self.assertEqual(self.dest_binds(self.binds("--no-host-writes"), str(self.durable)), [])

    def test_home_src_is_not_bound_so_a_write_there_dies_with_the_box(self) -> None:
        binds = self.binds("--no-host-writes")
        self.assertEqual(self.dest_binds(binds, f"{self.root}/src"), [], binds)
        self.assertFalse(any(self.dest(b) == f"{self.home}/src" for b in binds), binds)

    def test_no_empty_bind_is_handed_to_docker(self) -> None:
        """An emptied $SRC would be `-v ":"`; the redirect is why it is not."""
        for b in self.binds("--no-host-writes"):
            self.assertNotIn(":" + ":", f":{b}:", b)
            self.assertTrue(b.split(":")[0], f"a bind with no source: {b!r}")

    def test_every_allow_list_entry_is_read_only(self) -> None:
        """The surface nobody names: ~/.omp, ~/.ssh, ~/.local/bin and the rest."""
        binds = self.binds("--no-host-writes")
        for path in (".omp", ".ssh", ".local/bin", ".config/gh", ".aws"):
            got = self.dest_binds(binds, f"{self.home}/{path}")
            self.assertTrue(got, f"~/{path} should still be bound, to read: {binds}")
            self.assertTrue(all(b.endswith(":ro") for b in got), f"~/{path} must be ro: {got}")

    def test_the_session_can_still_write_its_own_transcript(self) -> None:
        """A read-only ~/.omp would stop omp recording; the store is private instead."""
        binds = self.binds("--no-host-writes")
        store = self.dest_binds(binds, f"{self.home}/.omp/agent/sessions")
        self.assertTrue(store, f"no private session store: {binds}")
        self.assertFalse(any(b.endswith(":ro") for b in store), store)
        self.assertNotIn(str(self.home) + "/.omp/agent/sessions:", store[0].split(":")[0] + ":",
                         f"the store must not be the host's own: {store}")

    def test_a_named_dir_is_still_writable(self) -> None:
        """--dir is the explicit way back to one writable host path."""
        binds = self.binds("--no-host-writes", "--dir", str(self.work))
        got = self.dest_binds(binds, str(self.work))
        self.assertTrue(got, f"--dir must still be bound: {binds}")
        self.assertFalse(any(b.endswith(":ro") for b in got), got)

    def test_the_environment_variable_does_the_same(self) -> None:
        self.env["AGENTBOX_NO_HOST_WRITES"] = "1"
        self.assertEqual(self.dest_binds(self.binds(), str(self.durable)), [])


if __name__ == "__main__":
    unittest.main()
