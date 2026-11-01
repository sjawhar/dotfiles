#!/usr/bin/env python3
"""scripts/agentbox gives every box a cpu, memory, pids and disk limit (dispatch://INFRA-702).

A box that goes past its memory limit is stopped by its own cgroup, and no other box is touched,
so each `docker run` carries --memory, --memory-swap, --cpus and --pids-limit. The defaults come
from 44 live sessions measured on sami-rocqperf (the comment above BOX_MEMORY in the script);
`agentbox new --memory SIZE --cpus N`, or AGENTBOX_MEMORY / AGENTBOX_CPUS, changes one box, and a
value above the per-box cap is refused with exit 2 before anything starts.

Disk is not a docker flag here: --storage-opt size= needs overlay2 on xfs with project quotas and
this host's docker data root is ext4, so the box gets a filesystem of its own instead - a sparse
ext4 image loop-mounted at its box directory, which holds its clones, its $HOME and its /tmp.
`--disk SIZE` and AGENTBOX_DISK change it and `--disk 0` turns it off.

Technique, as test_agentbox_envoy.py: source scripts/agentbox (its source guard runs nothing) with
`docker` and the other host tools stubbed first on PATH, and run cmd_new with the steps that need
a real host replaced by no-ops. The docker stub records `docker run`'s arguments, and the sudo and
mkfs stubs record what the disk setup asked of them, so no test mounts anything.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"
GATEWAY = "172.31.0.1"


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

# `sudo` and `mkfs.ext4` record what open_box_disk asked for and do nothing: no test mounts a
# filesystem. `truncate` is the real one, so the image a test inspects has the size it was given.
SUDO_STUB = r"""
printf '%s\n' "$*" >>"$STUB_SUDO_ARGS"
exit 0
"""
MKFS_STUB = r"""
printf '%s\n' "$*" >>"$STUB_MKFS_ARGS"
exit 0
"""

NO_HOST = (
    "image_tag() { echo agentbox:test; }; build_image() { :; }; ensure_forwarders() { :; }\n"
    f"ensure_network() {{ NETWORK_GATEWAY={GATEWAY} NETWORK_SUBNET=172.31.0.0/16; }}\n"
    "mount_args() { :; }; clone_checkout() { :; }; close_box() { :; }\n"
)


class AgentboxLimits(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        (self.home / ".config").mkdir(parents=True)
        (self.root / "src" / "repo1").mkdir(parents=True)
        stubs = self.root / "bin"
        write_stub(stubs, "docker", DOCKER_STUB)
        for name in ("git", "sleep"):
            write_stub(stubs, name, "exit 0")
        write_stub(stubs, "curl", "exit 7")
        # No launcher credential: the box gets no broker identity, and nothing else changes.
        write_stub(stubs, "agent-secrets", "echo none; exit 1")
        write_stub(stubs, "sudo", SUDO_STUB)
        write_stub(stubs, "mkfs.ext4", MKFS_STUB)
        # Nothing was really mounted, so nothing is a mountpoint.
        write_stub(stubs, "mountpoint", "exit 1")
        write_stub(self.root / "dotfiles" / "scripts", "envoy-nats-seed", "exit 1")
        (self.root / "dotfiles" / "agent-secrets").symlink_to(DOTFILES / "agent-secrets")
        self.run_args = self.root / "run-args"
        self.sudo_args = self.root / "sudo-args"
        self.mkfs_args = self.root / "mkfs-args"
        self.boxes = self.root / "boxes"
        self.disks = self.root / "disks"
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{stubs}:/usr/bin:/bin",
            "DOTFILES_DIR": str(self.root / "dotfiles"),
            "AGENTBOX_SRC": str(self.root / "src"),
            "AGENT_SECRETS_URL": "https://broker.example",
            "STUB_RUN_ARGS": str(self.run_args),
            "AGENTBOX_BOXES": str(self.boxes),
            "AGENTBOX_DISKS": str(self.disks),
            "STUB_SUDO_ARGS": str(self.sudo_args),
            "STUB_MKFS_ARGS": str(self.mkfs_args),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def launch(self, *options: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
        """cmd_new with these options before the repo, against the stubbed host."""
        script = f'source "{AGENTBOX}"\n{NO_HOST}cmd_new {" ".join(options)} repo1 -- omp\n'
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env={**self.env, **extra_env},
            check=False,
            timeout=60,
        )

    def run_args_of(self, *options: str, **extra_env: str) -> list[str]:
        result = self.launch(*options, **extra_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.run_args.read_text(encoding="utf-8").splitlines()

    def assert_limits(self, args: list[str], memory: str, swap: str, cpus: str) -> None:
        for flag, value in (
            ("--memory", memory),
            ("--memory-swap", swap),
            ("--cpus", cpus),
            ("--pids-limit", "8192"),
            # Every box shares the user slice's ceiling with the host's sessions.
            ("--cgroup-parent", f"user-{os.getuid()}-agentbox.slice"),
        ):
            self.assertIn(flag, args, args)
            self.assertEqual(args[args.index(flag) + 1], value, args)

    def test_every_box_gets_the_measured_defaults(self) -> None:
        self.assert_limits(self.run_args_of(), "24g", "32g", "8")

    def test_flags_override_one_box(self) -> None:
        args = self.run_args_of("--memory", "48g", "--cpus", "16")
        self.assert_limits(args, "48g", "56g", "16")

    def test_the_environment_overrides_the_defaults(self) -> None:
        args = self.run_args_of(AGENTBOX_MEMORY="12g", AGENTBOX_CPUS="2")
        self.assert_limits(args, "12g", "20g", "2")
        # A flag still wins over the environment.
        args = self.run_args_of("--memory", "4g", AGENTBOX_MEMORY="12g")
        self.assert_limits(args, "4g", "12g", "8")

    def test_a_value_above_the_cap_is_refused_before_any_box_starts(self) -> None:
        for options, naming in (
            (("--memory", "200g"), ("--memory", "200g", "96g")),
            (("--cpus", "64"), ("--cpus", "64", "32")),
        ):
            with self.subTest(options[0]):
                result = self.launch(*options)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                for word in naming:
                    self.assertIn(word, result.stderr)
                self.assertFalse(self.run_args.exists(), "a box started despite the refusal")

    def test_an_over_cap_environment_value_names_its_variable(self) -> None:
        result = self.launch(AGENTBOX_CPUS="64")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("AGENTBOX_CPUS", result.stderr)

    def test_a_value_docker_would_not_take_is_refused(self) -> None:
        for options in (("--memory", "lots"), ("--cpus", "many")):
            with self.subTest(options[1]):
                result = self.launch(*options)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(options[0], result.stderr)

    def test_docker_is_still_given_no_storage_opt(self) -> None:
        """--storage-opt size= needs overlay2 on xfs with project quotas; this host's docker data
        root is ext4 under the overlayfs snapshotter, where docker refuses it. The limit is the
        box filesystem below instead."""
        self.assertNotIn("--storage-opt", self.run_args_of())

    def box_disk(self) -> tuple[Path, list[str]]:
        """The image the last launch made, and what it asked sudo to do with it."""
        images = sorted(self.disks.glob("agentbox-*.img")) if self.disks.is_dir() else []
        self.assertEqual(len(images), 1, f"expected one box disk image, got {images}")
        sudo = self.sudo_args.read_text(encoding="utf-8").splitlines() if self.sudo_args.exists() else []
        return images[0], sudo

    def assert_disk(self, args: list[str], size: str, size_bytes: int) -> None:
        image, sudo = self.box_disk()
        box = image.name[: -len(".img")]
        self.assertEqual(image.stat().st_size, size_bytes)
        # Sparse: the limit reserves nothing, exactly as the memory limit does not.
        self.assertLess(image.stat().st_blocks * 512, size_bytes)
        # The two steps that need root, and no others: the loop mount, and the ext4 image's
        # root-owned lost+found given to the session that works in that directory.
        self.assertEqual(
            sudo,
            [
                f"-n mount -o loop,noatime,discard {image} {self.boxes / box}",
                f"-n chown {os.getuid()}:{os.getgid()} {self.boxes / box}/lost+found",
            ],
        )
        mkfs = self.mkfs_args.read_text(encoding="utf-8").splitlines()
        self.assertEqual(mkfs, [f"-q -F -m 0 -E root_owner={os.getuid()}:{os.getgid()} {image}"])
        # The box is told its own ceiling, so a session can name what its ENOSPC came from.
        self.assertIn(f"AGENTBOX_DISK={size}", args)

    def test_every_box_gets_a_filesystem_of_its_own_at_the_default_size(self) -> None:
        """32 GiB: about eight times the largest of the 117 session worktrees on this host's
        /mnt/nvme on 2026-11-01 (4.1 GiB)."""
        self.assert_disk(self.run_args_of(), "32g", 32 * 1024**3)

    def test_the_disk_flag_and_variable_change_one_box(self) -> None:
        # One box per case: each makes an image of its own, and assert_disk expects exactly one.
        for options, variables, size, size_bytes in (
            (("--disk", "8g"), {}, "8g", 8 * 1024**3),
            ((), {"AGENTBOX_DISK": "4g"}, "4g", 4 * 1024**3),
            # A flag still wins over the variable.
            (("--disk", "2g"), {"AGENTBOX_DISK": "4g"}, "2g", 2 * 1024**3),
        ):
            with self.subTest(size):
                self.tearDown()
                self.setUp()
                self.assert_disk(self.run_args_of(*options, **variables), size, size_bytes)

    def test_disk_zero_starts_a_box_with_no_filesystem_of_its_own(self) -> None:
        """A machine without the sudo rule for `mount -o loop` can still run boxes."""
        args = self.run_args_of("--disk", "0")
        self.assertIn("AGENTBOX_DISK=0", args)
        self.assertFalse(self.disks.exists(), "an image was made for a box with no disk limit")
        self.assertFalse(self.sudo_args.exists(), "sudo was called for a box with no disk limit")

    def test_a_disk_value_outside_the_range_is_refused_before_any_box_starts(self) -> None:
        for options, naming in (
            (("--disk", "600g"), ("--disk", "600g", "512g")),
            (("--disk", "64m"), ("--disk", "64m", "1g")),
            (("--disk", "plenty"), ("--disk", "plenty", "not a size")),
        ):
            with self.subTest(options[1]):
                result = self.launch(*options)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                for word in naming:
                    self.assertIn(word, result.stderr)
                self.assertFalse(self.run_args.exists(), "a box started despite the refusal")
                self.assertFalse(self.disks.exists(), "an image was made despite the refusal")

    def test_an_out_of_range_disk_variable_names_its_variable(self) -> None:
        result = self.launch(AGENTBOX_DISK="600g")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("AGENTBOX_DISK", result.stderr)


if __name__ == "__main__":
    unittest.main()
