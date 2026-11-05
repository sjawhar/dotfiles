"""A bind whose parent is bound is not a lost bind.

`restart-registry-check` compares each entry's resume line with the 2026-11-04 restart that
worked, and reports binds that command had and the entry lacks. It compared the two path lists
literally, so an owner who replaced twelve sibling binds with their one parent -- a tidier entry
that gives the box exactly the same files -- was told twelve binds were missing.

On 2026-11-05 that cost three lanes a round each, and each had to prove the same thing by hand:
the INFRA deploy lane's twelve paths under `/theorem-ext4/sami`, PROCS's worktree under
`/mnt/nvme/sami/wt`, TASKCOACH's under `/mnt/durable/sami`. A box mounts a `--dir` at its own
absolute path, so binding a parent shows every child under it, and `findmnt -T` on each child
answered with the parent's device every time. Eleven drift reports became six, and the six that
remain are real.

The check is advice to an owner, so a false report is not harmless: the owners who answered it
carefully are exactly the ones who will stop reading it when it cries wolf.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "restart-registry-check"


def load():
    spec = importlib.util.spec_from_loader(
        "restart_registry_check",
        importlib.machinery.SourceFileLoader("restart_registry_check", str(SCRIPT)),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_child_of_a_bound_parent_is_covered():
    mod = load()
    assert mod.covered_by("/theorem-ext4/sami/roll/repin46/pc", {"/theorem-ext4/sami"})
    assert mod.covered_by("/mnt/nvme/sami/wt/procs-forkwait", {"/mnt/nvme/sami/wt"})


def test_an_unrelated_path_is_not_covered():
    mod = load()
    assert not mod.covered_by("/mnt/nvme/sami/cutover", {"/mnt/nvme/sami/wt"})
    # A shared prefix in the NAME is not a parent directory.
    assert not mod.covered_by("/mnt/nvme/sami/wt-other", {"/mnt/nvme/sami/wt"})


def test_a_path_does_not_cover_itself():
    """Self-coverage would hide a bind that is genuinely gone from a rewritten entry."""
    mod = load()
    assert not mod.covered_by("/mnt/nvme/sami/wt", {"/mnt/nvme/sami/wt"})


def test_drift_reports_the_bind_that_is_really_lost_and_not_the_covered_one():
    mod = load()
    good = {
        "s1": (
            "x",
            "agentbox new --dir /theorem-ext4/sami/roll --dir /mnt/nvme/sami/gone -- omp",
            "/home/sami",
        )
    }
    entry = {
        "session": "s1",
        "resume": "agentbox new --dir /theorem-ext4/sami -- omp",
        "cwd": "/home/sami",
    }
    out = mod.drift(entry, good)
    assert len(out) == 1, out
    assert "/mnt/nvme/sami/gone" in out[0]
    assert "/theorem-ext4/sami/roll" not in out[0]


def test_a_path_every_box_has_is_never_reported_lost():
    """`~/src`, the durable directory and the dotfiles are in every box without a --dir."""
    mod = load()
    good = {"s1": ("x", f"agentbox new --dir {mod.HOME}/src/repo -- omp", None)}
    entry = {"session": "s1", "resume": "agentbox new --dir /mnt/nvme/sami/x -- omp"}
    assert mod.drift(entry, good) == []
