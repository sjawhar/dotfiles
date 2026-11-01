#!/usr/bin/env python3
"""`agentbox new --dir PATH` gives a box a host directory instead of a clone (dispatch://INFRA-702).

50 of the 82 live sessions on sami-rocqperf on 2026-11-01 worked in a directory that is not a
repository — a lane's scratch tree, a directory of worktrees, a coordination root — so no box
could be started for them: `new` demanded a `<repo>` and worked in its clone. `--dir` names an
existing host directory, binds it read-write at its own path, and is the box's working directory
when no repo is named. It is the host's directory: shared, not cloned, and outside the box's disk
image, while the cpu, memory, pids and disk limits are set exactly as for any other box.

Technique, as test_agentbox_limits.py: source scripts/agentbox (its source guard runs nothing)
with `docker` and the other host tools stubbed first on PATH, and run cmd_new with the steps that
need a real host replaced by no-ops. The docker stub records `docker run`'s arguments.
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


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


DOCKER_STUB = r"""
[[ "$1" == run ]] && { shift; printf '%s\n' "$@" >"$STUB_RUN_ARGS"; }
exit 0
"""

# mount_args is the function under test here, so it is NOT stubbed; everything that needs a real
# host is.
NO_HOST = (
    "image_tag() { echo agentbox:test; }; build_image() { :; }; ensure_forwarders() { :; }\n"
    f"ensure_network() {{ NETWORK_GATEWAY={GATEWAY} NETWORK_SUBNET=172.31.0.0/16; }}\n"
    "clone_checkout() { mkdir -p \"$2/.git\"; }; close_box() { :; }\n"
)


class AgentboxDirs(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        (self.home / ".config").mkdir(parents=True)
        (self.home / ".docker").mkdir()
        self.src = self.root / "src"
        (self.src / "repo1").mkdir(parents=True)
        self.lane = self.root / "lane"
        (self.lane / "gates").mkdir(parents=True)
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        stubs = self.root / "bin"
        write_stub(stubs, "docker", DOCKER_STUB)
        for name in ("git", "sleep", "jq", "mountpoint"):
            write_stub(stubs, name, "exit 0")
        write_stub(stubs, "curl", "exit 7")
        write_stub(stubs, "agent-secrets", "echo none; exit 1")
        write_stub(self.root / "dotfiles" / "scripts", "envoy-nats-seed", "exit 1")
        (self.root / "dotfiles" / "agentbox").mkdir(parents=True, exist_ok=True)
        (self.root / "dotfiles" / "agentbox" / "mounts").symlink_to(DOTFILES / "agentbox" / "mounts")
        (self.root / "dotfiles" / "agent-secrets").symlink_to(DOTFILES / "agent-secrets")
        self.run_args = self.root / "run-args"
        self.boxes = self.root / "boxes"
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{stubs}:/usr/bin:/bin",
            "DOTFILES_DIR": str(self.root / "dotfiles"),
            "AGENTBOX_SRC": str(self.src),
            "AGENT_SECRETS_URL": "https://broker.example",
            "STUB_RUN_ARGS": str(self.run_args),
            "AGENTBOX_BOXES": str(self.boxes),
            # The limits are tested in scripts/tests/test_agentbox_limits.py; a box here needs no
            # filesystem of its own, which would want root.
            "AGENTBOX_DISK": "0",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def launch(self, *options: str) -> subprocess.CompletedProcess[str]:
        script = f'source "{AGENTBOX}"\n{NO_HOST}cmd_new {" ".join(options)} -- omp\n'
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
            timeout=120,
        )

    def run_args_of(self, *options: str) -> list[str]:
        result = self.launch(*options)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.run_args.read_text(encoding="utf-8").splitlines()

    def binds_of(self, args: list[str]) -> list[str]:
        return [value for flag, value in zip(args, args[1:]) if flag == "-v"]

    def workdir_of(self, args: list[str]) -> str:
        return args[args.index("-w") + 1]

    def test_a_named_directory_is_bound_read_write_and_is_the_working_directory(self) -> None:
        args = self.run_args_of("--dir", str(self.lane))
        self.assertIn(f"{self.lane}:{self.lane}", self.binds_of(args))
        # No `:ro`: the session works in it.
        self.assertNotIn(f"{self.lane}:{self.lane}:ro", self.binds_of(args))
        self.assertEqual(self.workdir_of(args), str(self.lane))

    def test_every_named_directory_is_bound_and_the_first_is_the_working_directory(self) -> None:
        args = self.run_args_of("--dir", str(self.lane), "--dir", str(self.scratch))
        binds = self.binds_of(args)
        self.assertIn(f"{self.lane}:{self.lane}", binds)
        self.assertIn(f"{self.scratch}:{self.scratch}", binds)
        self.assertEqual(self.workdir_of(args), str(self.lane))

    def test_a_repo_still_wins_the_working_directory(self) -> None:
        """A box may have both: the clone it works in, and a lane tree beside it."""
        args = self.run_args_of("--dir", str(self.lane), "repo1")
        self.assertIn(f"{self.lane}:{self.lane}", self.binds_of(args))
        self.assertTrue(self.workdir_of(args).endswith("/repo1"), self.workdir_of(args))

    def test_a_directory_is_resolved_so_a_symlink_reaches_the_box_as_its_target(self) -> None:
        link = self.root / "lane-link"
        link.symlink_to(self.lane)
        args = self.run_args_of("--dir", str(link))
        self.assertIn(f"{self.lane}:{self.lane}", self.binds_of(args))
        self.assertEqual(self.workdir_of(args), str(self.lane))

    def test_the_limits_are_unchanged_for_a_box_with_no_repo(self) -> None:
        args = self.run_args_of("--dir", str(self.lane))
        for flag in ("--memory", "--memory-swap", "--cpus", "--pids-limit", "--cgroup-parent"):
            self.assertIn(flag, args, args)

    def test_a_box_still_needs_a_repo_or_a_directory(self) -> None:
        result = self.launch()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("a box needs a repo or a --dir", result.stderr)
        self.assertFalse(self.run_args.exists(), "a box started with neither")

    def test_a_directory_that_cannot_be_given_is_refused_before_any_box_starts(self) -> None:
        a_file = self.root / "a-file"
        a_file.write_text("x", encoding="utf-8")
        for path, naming in (
            (str(self.root / "absent"), "is not an existing directory"),
            (str(a_file), "is not an existing directory"),
            ("/", "the whole host filesystem"),
            (str(self.home), "would shadow the box"),
            (str(self.boxes / "agentbox-1"), "is not an existing directory"),
        ):
            with self.subTest(path):
                result = self.launch("--dir", path)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(naming, result.stderr)
                self.assertFalse(self.run_args.exists(), "a box started despite the refusal")

    def test_a_directory_inside_the_box_tree_is_refused(self) -> None:
        inside = self.boxes / "agentbox-1" / "knives"
        inside.mkdir(parents=True)
        result = self.launch("--dir", str(inside))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("inside the box tree", result.stderr)

    def tree_with_store(self, name: str) -> tuple[Path, Path]:
        """A checkout whose `.git/objects` is a SYMLINK to a store outside it, as
        ~/src/machcsl-riscv and four other checkouts on sami-rocqperf are."""
        tree = self.root / name
        (tree / ".git").mkdir(parents=True)
        store = self.root / "git-objects" / name
        store.mkdir(parents=True)
        (tree / ".git" / "objects").symlink_to(store)
        return tree, store

    def test_a_named_tree_carries_the_object_store_that_lives_outside_it(self) -> None:
        """A tree is not its objects. A box given ~/src/machcsl-riscv and not
        /mnt/nvme/<user>/git-objects/machcsl-riscv answers `not a git repository` for every
        command in it (measured 2026-11-01 in box agentbox-a0875d5a6f7c). `agentbox move` has
        always followed the store; `agentbox new --dir` did not."""
        tree, store = self.tree_with_store("machcsl")
        result = self.launch("--dir", str(tree))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        binds = self.binds_of(self.run_args.read_text(encoding="utf-8").splitlines())
        self.assertIn(f"{tree}:{tree}", binds)
        self.assertIn(f"{store}:{store}", binds)
        self.assertIn(str(store), result.stderr)

    def test_a_named_tree_carries_every_store_its_alternates_name(self) -> None:
        tree, store = self.tree_with_store("borrower")
        lender = self.root / "git-objects" / "lender"
        lender.mkdir(parents=True)
        (store / "info").mkdir()
        (store / "info" / "alternates").write_text(f"{lender}\n", encoding="utf-8")
        args = self.run_args_of("--dir", str(tree))
        self.assertIn(f"{lender}:{lender}", self.binds_of(args))

    def test_a_named_worktree_carries_the_checkout_it_borrows_from(self) -> None:
        """A linked worktree's `.git` is a FILE naming a git dir in another tree; without that
        tree the box has the working files and no repository."""
        owner, _ = self.tree_with_store("owner")
        gitdir = owner / ".git" / "worktrees" / "wt"
        gitdir.mkdir(parents=True)
        (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
        worktree = self.root / "wt"
        worktree.mkdir()
        (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
        binds = self.binds_of(self.run_args_of("--dir", str(worktree)))
        self.assertIn(f"{worktree}:{worktree}", binds)
        self.assertIn(f"{gitdir}:{gitdir}", binds)
        self.assertIn(f"{self.root / 'git-objects' / 'owner'}:{self.root / 'git-objects' / 'owner'}", binds)

    def test_a_directory_the_box_keeps_its_own_copy_of_is_refused_by_name(self) -> None:
        """docker refuses two mounts at one destination, so each of these ended the launch with
        `Duplicate mount point: <path>` once the box directory and its disk image already
        existed, naming neither the flag nor a fix (measured 2026-11-01). The box cannot be given
        the host's copy of any of them, so the refusal belongs at the start."""
        (self.home / ".omp" / "run").mkdir(parents=True)
        (self.home / ".config" / "jj" / "repos").mkdir(parents=True)
        for path, naming in (
            ("/tmp", "/tmp of its own"),
            (str(self.home / ".omp" / "run"), "omp broker"),
            (str(self.home / ".config" / "jj" / "repos"), "jj metadata"),
        ):
            with self.subTest(path):
                result = self.launch("--dir", path)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"--dir '{path}' cannot be given to a box", result.stderr)
                self.assertIn(naming, result.stderr)
                self.assertFalse(self.run_args.exists(), "a box started despite the refusal")

    def test_a_directory_every_box_has_is_bound_once_and_said_so(self) -> None:
        """`--dir ~/src` and `--dir <an allow-list entry>` are what a session working in one of
        them asks for, and binding the path twice is the same docker refusal. The box has the
        directory either way, so the launch keeps it as the working directory, does not bind it
        again, and says which mount it already comes from."""
        (self.home / ".aws").mkdir()
        for path, naming in ((str(self.src), f"every box has {self.src}"), (str(self.home / ".aws"), "read-write")):
            with self.subTest(path):
                result = self.launch("--dir", path)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                args = self.run_args.read_text(encoding="utf-8").splitlines()
                at_path = [b for b in self.binds_of(args) if b.split(":")[1:2] == [path]]
                self.assertEqual(at_path, [f"{path}:{path}"], at_path)
                self.assertEqual(self.workdir_of(args), path)
                self.assertIn("is not mounted again", result.stderr)
                self.assertIn(naming, result.stderr)

    def test_a_shared_working_directory_never_lends_its_session_id_to_a_box(self) -> None:
        """omp names a session directory after the cwd, so every session working in a host
        directory - the host's and every box's - writes into the same one. box_session_id must
        answer nothing rather than the newest file there: measured 2026-11-01, guessing returned
        a live host session's id, which `agentbox restart` would notify and close would use to
        release that session's knives claims."""
        sessions = self.home / ".omp" / "agent" / "sessions" / "-lane"
        sessions.mkdir(parents=True)
        (sessions / "2026-11-01T00-00-00-000Z_01a19999-0000-7000-8000-000000000000.jsonl").write_text(
            "{}\n", encoding="utf-8"
        )
        # The docker stub answers nothing, as a box with no omp holding a file open does.
        script = f'source "{AGENTBOX}"\nbox_session_id agentbox-1\n'
        result = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, env=self.env, check=False, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
