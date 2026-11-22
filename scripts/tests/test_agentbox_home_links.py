#!/usr/bin/env python3
"""A home allow-list entry that links into ~/.dotfiles is the same link in a box (scripts/agentbox
mount_args, adopt_link_home).

~/.gitconfig is a link to ~/.dotfiles/.gitconfig. A box used to get a bind of the file the link
resolved to at the box's start, and every move of the checkout (and every `git config --global`)
replaces that file with a new inode, so a box never read a later setting: on 2026-11-21 every box
on sami-devbox read a ~/.gitconfig of 2026-11-15 or older, without dotfiles#133's
core.excludesFile. The box's home is the host's $HOME path and ~/.dotfiles is a directory bind at
its own path, so the same link in the box's home resolves to the live file.

Technique, as test_agentbox_no_host_writes.py: source scripts/agentbox with the host tools stubbed
and run cmd_new with the host steps replaced, keeping mount_args real; read the -v arguments of
the docker run it would make and the box's home directory it fills. For adopt_link_home, a docker
stub runs the in-box script on this host, against the fixture home, with stubs for the mount tools.
"""

from __future__ import annotations

import os
import socket
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"

DOCKER_STUB = r"""
[[ "$1" == run ]] && { shift; printf '%s\n' "$@" >"$STUB_RUN_ARGS"; }
exit 0
"""

# `docker exec -u root <box> sh -c SCRIPT sh ARGS…` runs SCRIPT here, with the box's environment.
DOCKER_EXEC_STUB = r"""
[[ "$1" == exec ]] || exit 1
shift
while [[ "$1" == -* ]]; do shift 2; done
shift
printf '%s\n' "$*" >>"$STUB_EXEC_LOG"
exec env AGENTBOX_USER="$(id -un)" "$@"
"""

NO_HOST = (
    "image_tag() { echo agentbox:test; }; build_image() { :; }; ensure_forwarders() { :; }\n"
    "ensure_network() { NETWORK_GATEWAY=172.31.0.1 NETWORK_SUBNET=172.31.0.0/16; }\n"
    "clone_checkout() { :; }; close_box() { :; }\n"
    "box_cache_mounts() { :; }\n"
    "collect_start() { :; }\n"
)


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class HomeLinks(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        (self.home / ".config").mkdir(parents=True)
        (self.home / ".omp" / "agent" / "sessions").mkdir(parents=True)
        (self.root / "src" / "repo1").mkdir(parents=True)
        # ~/.dotfiles as an installed machine has it: a checkout, ~/.gitconfig a link into it.
        self.checkout = self.home / ".dotfiles"
        self.checkout.mkdir()
        (self.checkout / ".gitconfig").write_text("[core]\n\texcludesFile = ~/.dotfiles/global.gitignore\n")
        (self.home / ".gitconfig").symlink_to(self.checkout / ".gitconfig")
        self.stubs = self.root / "bin"
        write_stub(self.stubs, "docker", DOCKER_STUB)
        for name in ("git", "sleep"):
            write_stub(self.stubs, name, "exit 0")
        write_stub(self.stubs, "curl", "exit 7")
        write_stub(self.stubs, "agent-secrets", "echo none; exit 1")
        write_stub(self.stubs, "sudo", "exit 0")
        write_stub(self.stubs, "df", "printf 'Avail\\n1099511627776\\n'")
        write_stub(self.stubs, "mountpoint", "exit 1")
        dot = self.root / "dotfiles"
        write_stub(dot / "scripts", "envoy-nats-seed", "exit 1")
        write_stub(dot / "scripts", "agentbox-docker-snapshot", "exit 0")
        (dot / "agent-secrets").symlink_to(DOTFILES / "agent-secrets")
        # The real allow-list, so the test binds what production binds.
        (dot / "agentbox").symlink_to(DOTFILES / "agentbox")
        self.run_args = self.root / "run-args"
        self.exec_log = self.root / "exec-log"
        self.boxes = self.root / "boxes"
        # A stand-in secretsd socket, named through SECRETSD_SOCK at each launch: the script binds
        # secretsd's socket into a box and stops when it is missing, so without this a test passed
        # only on a host that runs secretsd.
        self.secretsd = self.root / "secretsd.sock"
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(self.secretsd))
        self.env = {
            "AGENTBOX_IDENTITY_FILE": str(self.root / "not-a-box"),
            "AGENTBOX_BOX": "",
            "AGENTBOX_HOST": "sami-rocqperf",
            "HOME": str(self.home),
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "DOTFILES_DIR": str(dot),
            "AGENTBOX_SRC": str(self.root / "src"),
            "AGENTBOX_DURABLE": str(self.root / "durable"),
            "AGENT_SECRETS_URL": "https://broker.example",
            "STUB_RUN_ARGS": str(self.run_args),
            "STUB_EXEC_LOG": str(self.exec_log),
            "AGENTBOX_BOXES": str(self.boxes),
            "AGENTBOX_DISKS": str(self.root / "disks"),
            "AGENTBOX_DISK": "0",
            "AGENTBOX_RUN_BASE": str(self.root / "run"),
        }

    def launch(self, *options: str) -> tuple[list[str], Path]:
        """The -v arguments of the docker run this launch would make, and the box's home."""
        script = f'source "{AGENTBOX}"\nSECRETSD_SOCK="{self.secretsd}"\n{NO_HOST}cmd_new {" ".join(options)} repo1 -- omp\n'
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                env=self.env, check=False, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        args = self.run_args.read_text(encoding="utf-8").splitlines()
        binds = [args[i + 1] for i, a in enumerate(args) if a == "-v" and i + 1 < len(args)]
        homes = list(self.boxes.glob("agentbox-*/home"))
        self.assertEqual(len(homes), 1, homes)
        return binds, homes[0]

    def binds_at(self, binds: list[str], destination: Path) -> list[str]:
        return [b for b in binds if b.split(":")[1:2] == [str(destination)]]

    def test_a_link_into_the_dotfiles_is_the_same_link_in_the_box_not_a_file_bind(self) -> None:
        binds, home = self.launch()
        self.assertEqual(self.binds_at(binds, self.home / ".gitconfig"), [], binds)
        link = home / ".gitconfig"
        self.assertTrue(link.is_symlink(), list(home.iterdir()))
        self.assertEqual(os.readlink(link), str(self.checkout / ".gitconfig"))
        # The link resolves through the ~/.dotfiles directory bind, which every box has.
        self.assertTrue(self.binds_at(binds, self.checkout), binds)

    def test_a_plain_file_and_a_link_outside_the_dotfiles_are_still_bound(self) -> None:
        # The control of the case above: only a link into ~/.dotfiles changes.
        (self.home / ".gitconfig").unlink()
        (self.home / ".gitconfig").write_text("[user]\n\tname = T\n")
        elsewhere = self.root / "npmrc"
        elsewhere.write_text("registry=x\n")
        (self.home / ".npmrc").symlink_to(elsewhere)
        binds, home = self.launch()
        self.assertEqual(self.binds_at(binds, self.home / ".gitconfig"), [f"{self.home}/.gitconfig:{self.home}/.gitconfig"])
        self.assertEqual(self.binds_at(binds, self.home / ".npmrc"), [f"{elsewhere}:{self.home}/.npmrc"])
        self.assertFalse((home / ".gitconfig").exists() or (home / ".gitconfig").is_symlink())

    def test_under_no_host_writes_the_entry_stays_a_read_only_bind(self) -> None:
        binds, home = self.launch("--no-host-writes")
        self.assertEqual(self.binds_at(binds, self.home / ".gitconfig"),
                         [f"{self.checkout}/.gitconfig:{self.home}/.gitconfig:ro"])
        self.assertFalse((home / ".gitconfig").is_symlink())

    def link_home(self, box_home: Path) -> subprocess.CompletedProcess[str]:
        """adopt_link_home against BOX_HOME, the box's view of $HOME."""
        write_stub(self.stubs, "docker", DOCKER_EXEC_STUB)
        env = {**self.env}
        script = (f'source "{AGENTBOX}"\nHOME={box_home}\n'
                  f'home_dotfiles_link() {{ [[ "$1" == .gitconfig ]] && echo {self.checkout}/.gitconfig; }}\n'
                  "adopt_link_home agentbox-000000000001\n")
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env,
                              check=False, timeout=60)

    def test_adopt_replaces_a_file_bind_with_the_link(self) -> None:
        box_home = self.root / "box-home"
        box_home.mkdir()
        (box_home / ".gitconfig").write_text("the copy of the box's start\n")
        umounts = self.root / "umounts"
        write_stub(self.stubs, "mountpoint", f'[[ "${{@: -1}}" == {box_home}/.gitconfig && ! -e {umounts} ]]')
        write_stub(self.stubs, "findmnt", 'echo rw,relatime')
        write_stub(self.stubs, "umount", f'printf "%s\\n" "$*" >>{umounts}')
        result = self.link_home(box_home)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(umounts.read_text().split(), ["-l", f"{box_home}/.gitconfig"])
        self.assertEqual(os.readlink(box_home / ".gitconfig"), str(self.checkout / ".gitconfig"))
        self.assertIn(f"{box_home}/.gitconfig is now a link to {self.checkout}/.gitconfig", result.stdout)
        # Run again: the link is in place, so nothing is unmounted or said.
        again = self.link_home(box_home)
        self.assertEqual(again.stdout, "", again.stderr)
        self.assertEqual(len(umounts.read_text().splitlines()), 1)

    def test_adopt_leaves_a_read_only_bind_and_a_file_of_the_box(self) -> None:
        box_home = self.root / "box-home"
        box_home.mkdir()
        (box_home / ".gitconfig").write_text("read-only bind\n")
        write_stub(self.stubs, "mountpoint", "exit 0")
        write_stub(self.stubs, "findmnt", "echo ro,relatime")
        write_stub(self.stubs, "umount", "exit 9")
        result = self.link_home(box_home)
        self.assertIn("is a read-only bind (--no-host-writes); left as it is", result.stdout)
        self.assertFalse((box_home / ".gitconfig").is_symlink())
        write_stub(self.stubs, "mountpoint", "exit 1")
        result = self.link_home(box_home)
        self.assertIn("is a file of the box's own, not a bind of the host's; left as it is", result.stdout)
        self.assertEqual((box_home / ".gitconfig").read_text(), "read-only bind\n")


if __name__ == "__main__":
    unittest.main()
