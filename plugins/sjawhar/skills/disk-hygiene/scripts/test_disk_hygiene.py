#!/usr/bin/env python3
"""Scenario drivers for the workspace reaper (plan-workspace-reaper.md, revision 4).

Stdlib unittest only — run with `python3 -m unittest -v test_disk_hygiene` from this
directory. Every test builds real scratch jj/git repos under /tmp and drives the
script through its CLI (the real surface); scenario 5 drives cmd_apply in-process
to inject a concurrent op deterministically.

Scenario map (verification table in the plan):
  1   D1 race            -> test_s01_d1_creation_window_absent_from_plan
  2   D1 idle=None       -> test_s02_d1_idle_none_merged_absent_from_plan
  3   D2 divergence      -> test_s03_d2_divergence_survives_apply
  4   D2 positive        -> test_s04_d2_clean_merged_removed_with_restoration_id
  5   D3 revert          -> test_s05_d3_forget_op_capture_survives_concurrent_op
  6   D4 container       -> test_s06_d4_container_bind_mount_protected
  7   D5 lock            -> test_s07_d5_second_apply_exits_on_held_lock
  8   D6 staleness       -> test_s08_d6_stale_plan_item_survives_apply
  9   released positive  -> test_s09_released_matching_identity_removed_and_retired
  10  released adoption  -> test_s10_released_adoption_refused_and_retired
  11  released unpushed  -> test_s11_released_unpushed_head_refused_entry_kept
  11b released dirty     -> test_s11b_released_dirty_worktree_refused_file_survives
  11c released ignored   -> test_s11c_released_ignored_file_only_refused_file_survives
  11d released ignored/  -> test_s11d_released_ignored_dir_only_refused_dir_survives
  12  D8 stale reg       -> test_s12_d8_stale_registration_forgotten
  12t D8 twin            -> test_s12b_d8_young_registering_op_refused
  13  released fallback  -> test_s13_released_git_fallback_spares_foreign_admin_entry
  14  retire crash-safety-> test_s14_removed_ledgered_before_retire_failure
  15  releases flock     -> test_s15_concurrent_releases_file_mutators_lose_no_update
  16  capture append     -> test_s16_release_capture_appends_to_releases_file
  17  unnamed stale reg  -> test_s17_unnamed_stale_registration_reported_not_dropped
  18  admin-id safety    -> test_s18_crafted_admin_id_never_escapes_worktrees_dir
  19  D4 cwd anchor      -> test_s19_d4_proc_cwd_anchor_protects_only_containing_slot
  20  D4 loud failure    -> test_s20_d4_generator_fails_loud_without_knives
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
SCRIPT = SCRIPTS_DIR / "disk_hygiene.py"
sys.path.insert(0, str(SCRIPTS_DIR))
import disk_hygiene  # noqa: E402 -- needs SCRIPTS_DIR on sys.path first
GIT_ID = ["-c", "user.email=reaper-test@local", "-c", "user.name=reaper-test"]


def sh(*cmd: str | Path, cwd: str | Path | None = None, check: bool = True,
       env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    r = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, timeout=300, env=env)
    if check and r.returncode != 0:
        raise AssertionError(f"command failed rc={r.returncode}: {cmd}\nstdout: {r.stdout}\nstderr: {r.stderr}")
    return r


def script(*args: str | Path, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return sh(sys.executable, SCRIPT, *args, check=check, env=env)


class ReaperScenarioTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="reaper-t-", dir="/tmp"))

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)  # scratch cleanup only

    # ------------------------------------------------------------- helpers

    def make_jj_repo(self) -> Path:
        repo = self.base / "repo"
        sh("jj", "git", "init", "--colocate", repo)
        (repo / "f.txt").write_text("hello\n")
        (repo / ".gitignore").write_text(".env\n")
        sh("jj", "describe", "-m", "base", cwd=repo)
        sh("jj", "bookmark", "create", "main", "-r", "@", cwd=repo)
        sh("jj", "new", cwd=repo)  # @ empty on main
        return repo

    def add_ws(self, repo: Path, name: str) -> Path:
        dest = self.base / name
        sh("jj", "workspace", "add", dest, cwd=repo)
        return dest

    def inventory(self, repo: Path, box_dir: str | Path = "none") -> Path:
        # Host semantics by default, so these scenarios mean the same thing on the host and in
        # an agentbox (where `auto` would narrow what counts as observably absent).
        r = script("inventory", "--repo", repo, "--root", self.base, "--trunk", "main", "--box-dir", box_dir)
        p = self.base / "inventory.json"
        p.write_text(r.stdout)
        return p

    def plan(self, *extra: str | Path, inventory: Path | None = None) -> tuple[dict[str, Any], Path]:
        args: list[str | Path] = ["plan"]
        if inventory is not None:
            args += ["--inventory", inventory]
        r = script(*args, *extra)
        p = self.base / "plan.json"
        p.write_text(r.stdout)
        return json.loads(r.stdout), p

    def protected_file(self) -> Path:
        p = self.base / "protected.json"
        p.write_text(json.dumps({"protected": []}))
        return p

    def apply(self, plan_path: Path, *extra: str | Path, check: bool = True) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
        ledger = self.base / "ledger.jsonl"
        r = script("apply", "--plan", plan_path, "--protected", self.protected_file(),
                   "--ledger", ledger, "--io-limit", "100", *extra, check=check)
        entries = [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
        return r, entries

    def plan_paths(self, doc: dict[str, Any]) -> list[str | None]:
        return [i.get("path") for i in doc["plan"]]

    def registered(self, repo: Path) -> set[str]:
        out = sh("jj", "--ignore-working-copy", "workspace", "list", cwd=repo).stdout
        return {line.split(":")[0] for line in out.splitlines() if ":" in line}

    def make_git_repo(self) -> Path:
        repo = self.base / "gitrepo"
        sh("git", "init", "-q", "-b", "main", repo)
        (repo / "a.txt").write_text("alpha\n")
        (repo / ".gitignore").write_text(".env\n.venv/\n")
        sh("git", *GIT_ID, "-C", repo, "add", "a.txt", ".gitignore")
        sh("git", *GIT_ID, "-C", repo, "commit", "-qm", "one")
        bare = self.base / "remote.git"
        sh("git", "init", "-q", "--bare", bare)
        sh("git", "-C", repo, "remote", "add", "origin", bare)
        sh("git", "-C", repo, "push", "-q", "origin", "main")
        return repo

    def add_git_wt(self, repo: Path, name: str) -> Path:
        dest = self.base / name
        sh("git", "-C", repo, "worktree", "add", "-q", "--detach", dest)
        return dest

    def capture_release(self, path: Path, repo: Path, kind: str, name: str | None = None) -> dict[str, Any]:
        args: list[str | Path] = ["release-capture", "--path", path, "--repo", repo, "--kind", kind,
                                  "--session", "test-session", "--message", "released by test"]
        if name:
            args += ["--name", name]
        r = script(*args)
        return json.loads(r.stdout)

    def releases_file(self, *entries: dict[str, Any]) -> Path:
        p = self.base / "releases.json"
        p.write_text(json.dumps({"releases": list(entries)}, indent=1))
        return p

    def release_entries(self, path: Path) -> list[dict[str, Any]]:
        return json.loads(path.read_text())["releases"]

    def git_worktree_paths(self, repo: Path) -> list[str]:
        out = sh("git", "-C", repo, "worktree", "list", "--porcelain").stdout
        return [line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("worktree ")]

    # ------------------------------------------------------------- D1

    def test_s01_d1_creation_window_absent_from_plan(self) -> None:
        """Scenario 1: mid-creation slot (.jj/repo pointer, no checkout) must not be planned."""
        repo = self.make_jj_repo()
        slot = self.base / "midcreation"
        (slot / ".jj").mkdir(parents=True)
        (slot / ".jj" / "repo").write_text(str(repo / ".jj" / "repo"))
        inv = self.inventory(repo)
        inv_doc = json.loads(inv.read_text())
        row = next(r for r in inv_doc["rows"] if r["path"] == str(slot))
        self.assertEqual(row["class"], "UNREGISTERED")
        self.assertIsNone(row["jj_idle_hours"])
        doc, _ = self.plan("--fresh-hours", "0", inventory=inv)
        self.assertNotIn(str(slot), self.plan_paths(doc),
                         "mid-creation UNREGISTERED slot bypassed the freshness gate into the plan")
        self.assertIn("refused", doc)
        refused = [x for x in doc["refused"] if x.get("path") == str(slot)]
        self.assertTrue(refused and "idle" in refused[0]["reason"], f"refusal not reported: {doc.get('refused')}")

    def test_s02_d1_idle_none_merged_absent_from_plan(self) -> None:
        """Scenario 2: MERGED slot with tree_state removed (idle=None) must be refused."""
        repo = self.make_jj_repo()
        ws = self.add_ws(repo, "w2")
        (ws / ".jj" / "working_copy" / "tree_state").unlink()
        inv = self.inventory(repo)
        row = next(r for r in json.loads(inv.read_text())["rows"] if r["path"] == str(ws))
        self.assertEqual(row["class"], "MERGED")
        self.assertIsNone(row["jj_idle_hours"])
        doc, _ = self.plan(inventory=inv)
        self.assertNotIn(str(ws), self.plan_paths(doc),
                         "idle=None MERGED slot bypassed the freshness gate into the plan")
        self.assertIn("refused", doc)
        self.assertTrue(any(x.get("path") == str(ws) for x in doc["refused"]))

    # ------------------------------------------------------------- D2

    def test_s03_d2_divergence_survives_apply(self) -> None:
        """Scenario 3: gitignored .env + modified tracked file => refuse; both survive."""
        repo = self.make_jj_repo()
        ws = self.add_ws(repo, "w3")
        (ws / ".env").write_text("SECRET=only-on-disk\n")
        (ws / "f.txt").write_text("hello\nmodified-only-on-disk\n")
        inv = self.inventory(repo)
        doc, plan_path = self.plan("--fresh-hours", "0", inventory=inv)
        self.assertIn(str(ws), self.plan_paths(doc))
        _, ledger = self.apply(plan_path)
        self.assertTrue((ws / ".env").exists(), "gitignored .env was destroyed")
        self.assertEqual((ws / ".env").read_text(), "SECRET=only-on-disk\n")
        self.assertTrue((ws / "f.txt").exists(), "modified tracked file was destroyed")
        self.assertEqual((ws / "f.txt").read_text(), "hello\nmodified-only-on-disk\n")
        skips = [e for e in ledger if e.get("op") == "skip" and e.get("path") == str(ws)]
        self.assertTrue(skips and "diverg" in skips[0]["reason"], f"no skipped-with-content ledger line: {ledger}")
        self.assertIn("w3", self.registered(repo), "registration was forgotten despite divergence (check must run before forget)")

    def test_s04_d2_clean_merged_removed_with_restoration_id(self) -> None:
        """Scenario 4: clean MERGED slot is removed, forgotten, and ledgered with a recovery id."""
        repo = self.make_jj_repo()
        ws = self.add_ws(repo, "w4")
        inv = self.inventory(repo)
        doc, plan_path = self.plan("--fresh-hours", "0", inventory=inv)
        self.assertIn(str(ws), self.plan_paths(doc))
        _, ledger = self.apply(plan_path)
        self.assertFalse(ws.exists(), "clean MERGED slot not removed")
        self.assertNotIn("w4", self.registered(repo))
        removed = [e for e in ledger if e.get("op") == "removed" and e.get("path") == str(ws)]
        self.assertTrue(removed, f"no removed ledger line: {ledger}")
        self.assertIn("forget_op", removed[0], "ledger lacks the restoration op id (D3)")
        self.assertIn("registration only", removed[0]["recover"])
        self.assertIn("reused", removed[0]["recover"])

    # ------------------------------------------------------------- D3

    def test_s05_d3_forget_op_capture_survives_concurrent_op(self) -> None:
        """Scenario 5: a concurrent op between forget and capture; the ledger id still reverts the forget."""
        repo = self.make_jj_repo()
        ws = self.add_ws(repo, "w5")
        inv = self.inventory(repo)
        _, plan_path = self.plan("--fresh-hours", "0", inventory=inv)
        sys.path.insert(0, str(SCRIPTS_DIR))
        import disk_hygiene as dh  # noqa: PLC0415
        orig_run = dh.run

        def hooked(cmd: list[str], cwd: str | None = None, timeout: int = 600) -> tuple[int, str]:
            result = orig_run(cmd, cwd, timeout)
            if "forget" in cmd:  # land a concurrent op between the forget and the id capture
                sh("jj", "--ignore-working-copy", "describe", "-r", "main", "-m", "concurrent", cwd=repo)
            return result

        ledger_path = self.base / "ledger.jsonl"
        import argparse
        args = argparse.Namespace(plan=str(plan_path), protected=str(self.protected_file()),
                                  ledger=str(ledger_path), io_limit=100.0, releases=None)
        dh.run = hooked
        try:
            dh.cmd_apply(args)
        finally:
            dh.run = orig_run
        ledger = [json.loads(line) for line in ledger_path.read_text().splitlines()]
        removed = [e for e in ledger if e.get("op") == "removed" and e.get("path") == str(ws)]
        self.assertTrue(removed, f"slot not removed: {ledger}")
        self.assertIn("forget_op", removed[0], "ledger lacks the forget op id (D3)")
        op_id = removed[0]["forget_op"]
        sh("jj", "--ignore-working-copy", "op", "revert", op_id, cwd=repo)
        self.assertIn("w5", self.registered(repo), "op revert of the ledgered id did not re-register the workspace")
        desc = sh("jj", "--ignore-working-copy", "log", "-r", "main", "--no-graph",
                  "-T", "description.first_line()", cwd=repo).stdout.strip()
        self.assertEqual(desc, "concurrent", "the op landed after the forget did not survive the revert")

    # ------------------------------------------------------------- D4

    def fake_knives(self) -> tuple[dict[str, str], Path]:
        """A knives CLI double emitting the measured `repos --json` shape (2026-09-15,
        knives 2.0.11): rc 3 with the complete repo list on stdout when the forge is
        unreachable, one default-layout path, one bare checkout path, one null path.
        Returns (env with the double first on PATH, registry TOML path)."""
        bindir = self.base / "bin"
        bindir.mkdir()
        for name in ("knives", "ai", "bare-repo"):
            (self.base / "knives" / name).mkdir(parents=True, exist_ok=True)
        (self.base / "knives" / "ai" / "default").mkdir()
        (self.base / "knives-ws").mkdir()
        payload = json.dumps({"repos": [
            {"name": "ai", "path": str(self.base / "knives" / "ai" / "default")},
            {"name": "bare", "path": str(self.base / "knives" / "bare-repo")},
            {"name": "nopath", "path": None},
        ]})
        fake = bindir / "knives"
        fake.write_text("#!/bin/sh\n"
                        f"[ \"$1 $2\" = 'repos --json' ] || {{ echo \"unexpected args: $@\" >&2; exit 9; }}\n"
                        f"cat <<'EOF'\n{payload}\nEOF\n"
                        "echo 'forge unreachable (simulated)' >&2\n"
                        "exit 3\n")
        fake.chmod(0o755)
        registry = self.base / "repos.toml"
        registry.write_text("[repos.ai]\nupstream = \"https://example.invalid/ai\"\n\n"
                            "[repos.bare]\nupstream = \"https://example.invalid/bare\"\n"
                            f"workspaces = \"{self.base / 'knives-ws'}\"\n")
        env = dict(os.environ)
        env["PATH"] = f"{bindir}:{env['PATH']}"
        return env, registry

    def generate_protected(self, env: dict[str, str], registry: Path, fixed: dict[str, Any] | None = None) -> tuple[dict[str, Any], Path]:
        fixed_path = self.base / "fixed.json"
        fixed_path.write_text(json.dumps(fixed or {"protected": [], "anchors": []}))
        r = script("protected", "--fixed", fixed_path, "--knives-registry", registry, env=env)
        doc: dict[str, Any] = json.loads(r.stdout)
        out = self.base / "generated-protected.json"
        out.write_text(r.stdout)
        return doc, out

    def test_s06_d4_container_bind_mount_protected(self) -> None:
        """Scenario 6: a slot bind-mounted into a container (no host cwd inside it) is
        protected; an identical sibling slot with no liveness evidence is still planned."""
        repo = self.make_jj_repo()
        ws = self.add_ws(repo, "w6")
        control = self.add_ws(repo, "w6control")
        cid = sh("docker", "create", "-v", f"{ws}:/mnt", "busybox", "true").stdout.strip()
        self.addCleanup(lambda: sh("docker", "rm", "-f", cid, check=False))
        env, registry = self.fake_knives()
        keep = self.base / "fixedkeep"
        keep.mkdir()
        doc, protected_path = self.generate_protected(env, registry, {"protected": [str(keep)], "anchors": []})
        real_ws = os.path.realpath(ws)
        self.assertIn(real_ws, doc["protected"], "bind-mount source missing from the generated protected set")
        self.assertIn(str(keep), doc["protected"], "fixed entry missing")
        self.assertIn(str(self.base / "knives" / "ai"), doc["protected"],
                      "default-layout knives checkout must protect its PARENT dir (sibling workspace slots)")
        self.assertNotIn(str(self.base / "knives"), doc["protected"])
        self.assertIn(str(self.base / "knives" / "bare-repo"), doc["protected"],
                      "bare-layout knives checkout must protect the checkout itself, never its parent (= $HOME)")
        self.assertIn(str(self.base / "knives-ws"), doc["protected"],
                      "registry `workspaces` dir missing (repos --json does not expose it; measured)")
        self.assertGreaterEqual(doc["sources"]["docker_bind_sources"], 1)
        self.assertEqual(doc["sources"]["knives"], 3)
        inv = self.inventory(repo)
        plan_doc, _ = self.plan("--fresh-hours", "0", "--protected", protected_path, inventory=inv)
        self.assertNotIn(str(ws), self.plan_paths(plan_doc),
                         "bind-mounted slot with no host cwd was planned for removal")
        self.assertIn(str(control), self.plan_paths(plan_doc),
                      "control slot vanished from the plan: the generated set blanket-protects "
                      "(a broad anchor/bind such as /tmp or $HOME neuters the reaper)")

    def test_s19_d4_proc_cwd_anchor_protects_only_containing_slot(self) -> None:
        """A live process cwd INSIDE a slot protects it; the ubiquitous broad cwds
        (/, $HOME, /tmp — all measured live on the real box) protect nothing beneath
        them. Residual stated in the plan: a slot driven only via `jj -R` from
        elsewhere is protected by the idle threshold alone, not by D4."""
        repo = self.make_jj_repo()
        ws = self.add_ws(repo, "w19")
        env, registry = self.fake_knives()
        holder = subprocess.Popen(["sleep", "60"], cwd=str(ws))
        try:
            doc, protected_path = self.generate_protected(env, registry)
            self.assertIn(str(ws), doc["anchors"], "live cwd missing from generated anchors")
            inv = self.inventory(repo)
            plan_doc, _ = self.plan("--fresh-hours", "0", "--protected", protected_path, inventory=inv)
            self.assertNotIn(str(ws), self.plan_paths(plan_doc), "slot with a live cwd inside was planned")
        finally:
            holder.terminate()
            holder.wait()
        doc, protected_path = self.generate_protected(env, registry)
        self.assertNotIn(str(ws), doc["anchors"], "anchor survived its process")
        inv = self.inventory(repo)
        plan_doc, _ = self.plan("--fresh-hours", "0", "--protected", protected_path, inventory=inv)
        self.assertIn(str(ws), self.plan_paths(plan_doc),
                      "slot with no liveness evidence missing from the plan (broad-cwd blanket protection?)")

    def test_s20_d4_generator_fails_loud_without_knives(self) -> None:
        """A missing source is a loud failure, never a silently smaller protected set."""
        env, registry = self.fake_knives()
        env["PATH"] = "/usr/bin:/bin"  # docker present, knives absent
        fixed_path = self.base / "fixed.json"
        fixed_path.write_text(json.dumps({"protected": []}))
        r = script("protected", "--fixed", fixed_path, "--knives-registry", registry, env=env, check=False)
        self.assertNotEqual(r.returncode, 0, "generator succeeded with the knives source missing")
        self.assertIn("knives", r.stderr)

    # ------------------------------------------------------------- D5

    def test_s07_d5_second_apply_exits_on_held_lock(self) -> None:
        """Scenario 7: while one apply holds the per-store lock, a second writes one ledger line and exits."""
        repo = self.make_jj_repo()
        self.add_ws(repo, "w7")
        inv = self.inventory(repo)
        _, plan_path = self.plan("--fresh-hours", "0", inventory=inv)
        # same derivation as apply_lock_path in disk_hygiene.py
        key = str(Path(repo).resolve())
        h = hashlib.sha256(key.encode()).hexdigest()[:16]
        state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "disk-hygiene"
        state.mkdir(parents=True, exist_ok=True)
        lock_path = state / f"apply-{h}.lock"
        with open(lock_path, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            r, ledger = self.apply(plan_path)
        self.assertEqual(r.returncode, 0)
        locked = [e for e in ledger if e.get("op") == "locked"]
        self.assertTrue(locked, f"no lock-held ledger line: {ledger}")
        self.assertFalse(any(e.get("op") == "apply-start" for e in ledger),
                         "second apply proceeded despite the held lock")

    # ------------------------------------------------------------- D6

    def test_s08_d6_stale_plan_item_survives_apply(self) -> None:
        """Scenario 8: plan, then commit in the slot, then apply; the slot must survive."""
        repo = self.make_jj_repo()
        ws = self.add_ws(repo, "w8")
        inv = self.inventory(repo)
        doc, plan_path = self.plan("--fresh-hours", "0", inventory=inv)
        self.assertIn(str(ws), self.plan_paths(doc))
        (ws / "new-work.txt").write_text("committed after the plan\n")
        sh("jj", "commit", "-m", "post-plan work", cwd=ws)
        _, ledger = self.apply(plan_path)
        self.assertTrue(ws.exists(), "slot deleted although its class changed after the plan")
        self.assertIn("w8", self.registered(repo))
        skips = [e for e in ledger if e.get("op") == "skip" and e.get("path") == str(ws)]
        self.assertTrue(skips and "re-verif" in skips[0]["reason"], f"no re-verification skip: {ledger}")

    # ------------------------------------------------------------- released class

    def test_s09_released_matching_identity_removed_and_retired(self) -> None:
        """Scenario 9: matching identity, pushed HEAD, clean tree => removed + entry retired."""
        repo = self.make_git_repo()
        wt = self.add_git_wt(repo, "rel9")
        entry = self.capture_release(wt, repo, "git")
        for field in ("head", "admin_id", "mtime"):
            self.assertIn(field, entry)
        rel = self.releases_file(entry)
        doc, plan_path = self.plan("--releases", rel)
        self.assertIn(str(wt), self.plan_paths(doc))
        # plan is read-only: no entry consumed
        self.assertFalse(any(e.get("retired") for e in self.release_entries(rel)), "plan consumed a release entry")
        _, ledger = self.apply(plan_path, "--releases", rel)
        self.assertFalse(wt.exists(), "released slot with matching identity not removed")
        self.assertNotIn(str(wt), self.git_worktree_paths(repo))
        retired = [e for e in self.release_entries(rel) if e.get("retired")]
        self.assertEqual(len(retired), 1, "entry not retired after being acted on")
        self.assertTrue(any(e.get("op") == "removed" and e.get("path") == str(wt) for e in ledger), f"ledger: {ledger}")

    def test_s10_released_adoption_refused_and_retired(self) -> None:
        """Scenario 10: HEAD moved after release => refuse AND retire the entry."""
        repo = self.make_git_repo()
        wt = self.add_git_wt(repo, "rel10")
        entry = self.capture_release(wt, repo, "git")
        (wt / "b.txt").write_text("adopted\n")
        sh("git", *GIT_ID, "-C", wt, "add", "b.txt")
        sh("git", *GIT_ID, "-C", wt, "commit", "-qm", "adopter's commit")
        sh("git", "-C", wt, "push", "-q", "origin", "HEAD:refs/heads/adopted")  # keep unpushed-guard out of the way
        rel = self.releases_file(entry)
        _, plan_path = self.plan("--releases", rel)
        _, ledger = self.apply(plan_path, "--releases", rel)
        self.assertTrue(wt.exists(), "adopted slot was deleted")
        self.assertTrue((wt / "b.txt").exists())
        retired = [e for e in self.release_entries(rel) if e.get("retired")]
        self.assertEqual(len(retired), 1, "adoption evidence must retire the entry (one-shot)")
        refusals = [e for e in ledger if e.get("op") == "released-refused" and e.get("path") == str(wt)]
        self.assertTrue(refusals and "adoption" in refusals[0]["reason"], f"ledger: {ledger}")

    def test_s11_released_unpushed_head_refused_entry_kept(self) -> None:
        """Scenario 11: detached HEAD with a commit on no remote ref => refused by the reachability guard."""
        repo = self.make_git_repo()
        wt = self.add_git_wt(repo, "rel11")
        (wt / "x.txt").write_text("local only\n")
        sh("git", *GIT_ID, "-C", wt, "add", "x.txt")
        sh("git", *GIT_ID, "-C", wt, "commit", "-qm", "local-only")
        entry = self.capture_release(wt, repo, "git")  # identity matches current state
        rel = self.releases_file(entry)
        _, plan_path = self.plan("--releases", rel)
        _, ledger = self.apply(plan_path, "--releases", rel)
        self.assertTrue(wt.exists(), "slot with unpushed commits was deleted")
        self.assertTrue((wt / "x.txt").exists())
        self.assertFalse(any(e.get("retired") for e in self.release_entries(rel)),
                         "unpushed refusal must keep the entry")
        refusals = [e for e in ledger if e.get("op") == "released-refused" and e.get("path") == str(wt)]
        self.assertTrue(refusals and "unpushed" in refusals[0]["reason"], f"ledger: {ledger}")

    def test_s11b_released_dirty_worktree_refused_file_survives(self) -> None:
        """Scenario 11b: modified tracked file => the --porcelain gate blocks removal; the file survives."""
        repo = self.make_git_repo()
        wt = self.add_git_wt(repo, "rel11b")
        entry = self.capture_release(wt, repo, "git")
        (wt / "a.txt").write_text("alpha\nmodified-only-on-disk\n")  # content edit: dir mtime unchanged
        rel = self.releases_file(entry)
        _, plan_path = self.plan("--releases", rel)
        _, ledger = self.apply(plan_path, "--releases", rel)
        self.assertTrue(wt.exists(), "dirty released worktree was deleted")
        self.assertEqual((wt / "a.txt").read_text(), "alpha\nmodified-only-on-disk\n")
        self.assertIn(str(wt), self.git_worktree_paths(repo), "git worktree remove was attempted on a dirty tree")
        self.assertFalse(any(e.get("retired") for e in self.release_entries(rel)))
        refusals = [e for e in ledger if e.get("op") == "released-refused" and e.get("path") == str(wt)]
        self.assertTrue(refusals and "dirty" in refusals[0]["reason"], f"ledger: {ledger}")

    def test_s11c_released_ignored_file_only_refused_file_survives(self) -> None:
        """Scenario 11c: only a gitignored file exists on disk => refuse (Decision 1); the file survives.
        Created BEFORE release capture so identity matches and only the content gate stands."""
        repo = self.make_git_repo()
        wt = self.add_git_wt(repo, "rel11c")
        (wt / ".env").write_text("SECRET=only-on-disk\n")
        entry = self.capture_release(wt, repo, "git")
        rel = self.releases_file(entry)
        _, plan_path = self.plan("--releases", rel)
        _, ledger = self.apply(plan_path, "--releases", rel)
        self.assertTrue(wt.exists(), "released worktree holding only a gitignored file was deleted")
        self.assertEqual((wt / ".env").read_text(), "SECRET=only-on-disk\n")
        self.assertIn(str(wt), self.git_worktree_paths(repo), "git worktree remove was attempted despite ignored content")
        self.assertFalse(any(e.get("retired") for e in self.release_entries(rel)),
                         "ignored-content refusal must keep the entry (divergence semantics)")
        refusals = [e for e in ledger if e.get("op") == "released-refused" and e.get("path") == str(wt)]
        self.assertTrue(refusals and "ignored" in refusals[0]["reason"], f"ledger: {ledger}")

    def test_s11d_released_ignored_dir_only_refused_dir_survives(self) -> None:
        """Scenario 11d: only an ignored directory (.venv/x, the venv case) => refuse; contents survive."""
        repo = self.make_git_repo()
        wt = self.add_git_wt(repo, "rel11d")
        (wt / ".venv").mkdir()
        (wt / ".venv" / "x").write_text("payload-only-on-disk\n")
        entry = self.capture_release(wt, repo, "git")
        rel = self.releases_file(entry)
        _, plan_path = self.plan("--releases", rel)
        _, ledger = self.apply(plan_path, "--releases", rel)
        self.assertTrue(wt.exists(), "released worktree holding only an ignored directory was deleted")
        self.assertEqual((wt / ".venv" / "x").read_text(), "payload-only-on-disk\n")
        self.assertIn(str(wt), self.git_worktree_paths(repo))
        self.assertFalse(any(e.get("retired") for e in self.release_entries(rel)))
        refusals = [e for e in ledger if e.get("op") == "released-refused" and e.get("path") == str(wt)]
        self.assertTrue(refusals and "ignored" in refusals[0]["reason"], f"ledger: {ledger}")

    # ------------------------------------------------------------- D8

    def test_s12_d8_stale_registration_forgotten(self) -> None:
        """Scenario 12: registration with no directory, merged, op older than threshold => forgotten."""
        repo = self.make_jj_repo()
        ws = self.add_ws(repo, "w12")
        shutil.rmtree(ws)
        inv = self.inventory(repo)
        rows = json.loads(inv.read_text())["rows"]
        self.assertTrue(any(r["class"] == "STALE_REGISTRATION" and r.get("name") == "w12" for r in rows))
        doc, plan_path = self.plan("--fresh-hours", "0", inventory=inv)
        forgets = [i for i in doc["plan"] if i.get("kind") == "forget" and i.get("name") == "w12"]
        self.assertTrue(forgets, f"STALE_REGISTRATION not planned: {doc['plan']}")
        _, ledger = self.apply(plan_path)
        self.assertNotIn("w12", self.registered(repo), "stale registration not forgotten")
        forgot = [e for e in ledger if e.get("op") == "forgot" and e.get("name") == "w12"]
        self.assertTrue(forgot, f"ledger: {ledger}")
        self.assertIn("forget_op", forgot[0], "D8 forget must carry the D3 restoration id")

    def test_s12c_unobservable_registration_never_forgotten(self) -> None:
        """Scenario 12's exact state (no directory, merged, old op) seen from a process that cannot
        observe the workspace's location -- another box's checkout, from inside a box. Absence is
        not evidence there: the registration must classify UNOBSERVABLE, stay out of the plan,
        and survive apply. 2026-09-25 09:15Z: a prune from inside a box broke four live worktrees."""
        repo = self.make_jj_repo()
        ws = self.add_ws(repo, "w12c")
        shutil.rmtree(ws)
        elsewhere = self.base / "some-other-box"
        elsewhere.mkdir()
        inv = self.inventory(repo, box_dir=elsewhere)
        rows = [r for r in json.loads(inv.read_text())["rows"] if r.get("name") == "w12c"]
        self.assertTrue(rows and all(r["class"] == "UNOBSERVABLE" for r in rows), f"rows: {rows}")
        doc, plan_path = self.plan("--fresh-hours", "0", inventory=inv)
        self.assertFalse(any(i.get("name") == "w12c" for i in doc["plan"]),
                         f"an unobservable registration was planned: {doc['plan']}")
        self.apply(plan_path)
        self.assertIn("w12c", self.registered(repo), "an unobservable registration was forgotten")

    def test_s12b_d8_young_registering_op_refused(self) -> None:
        """Scenario 12 twin: registering op younger than the threshold => refused (creation window)."""
        repo = self.make_jj_repo()
        ws = self.add_ws(repo, "w12b")
        shutil.rmtree(ws)
        inv = self.inventory(repo)
        doc, plan_path = self.plan("--fresh-hours", "999999", inventory=inv)
        self.assertFalse(any(i.get("name") == "w12b" for i in doc["plan"]),
                         "young stale registration planned despite the op-age guard")
        self.assertIn("refused", doc)
        refused = [x for x in doc["refused"] if x.get("name") == "w12b"]
        self.assertTrue(refused and "younger" in refused[0]["reason"], f"refused: {doc.get('refused')}")
        self.apply(plan_path)
        self.assertIn("w12b", self.registered(repo), "young stale registration was forgotten")

    # ------------------------------------------------------------- released-git fallback (targeted admin cleanup)

    def test_s13_released_git_fallback_spares_foreign_admin_entry(self) -> None:
        """A released-slot reap that takes the remove-failed => rm fallback must clean up exactly
        the reaped slot's .git/worktrees/<admin_id> entry — never run a store-wide prune that
        eats other sessions' stale admin entries. Remove is forced to fail via `git worktree
        lock` (measured: `remove --force` exits 128 on a locked worktree)."""
        repo = self.make_git_repo()
        wt = self.add_git_wt(repo, "rel13")
        foreign = self.add_git_wt(repo, "rel13-foreign")
        foreign_admin = repo / ".git" / "worktrees" / "rel13-foreign"
        self.assertTrue(foreign_admin.is_dir())
        shutil.rmtree(foreign)  # another session's residue: stale (prunable) admin entry, no release
        entry = self.capture_release(wt, repo, "git")
        sh("git", "-C", repo, "worktree", "lock", wt)
        rel = self.releases_file(entry)
        _, plan_path = self.plan("--releases", rel)
        _, ledger = self.apply(plan_path, "--releases", rel)
        self.assertFalse(wt.exists(), "released slot not removed on the fallback path")
        reaped_admin = repo / ".git" / "worktrees" / entry["admin_id"]
        self.assertFalse(reaped_admin.exists(), "reaped slot's admin entry survived the fallback cleanup")
        self.assertTrue(foreign_admin.is_dir(),
                        "foreign stale admin entry was destroyed (store-wide prune must not run)")
        removed = [e for e in ledger if e.get("op") == "removed" and e.get("path") == str(wt)]
        self.assertTrue(removed, f"ledger: {ledger}")
        fallback = removed[0].get("fallback")
        self.assertTrue(fallback and "remove failed" in fallback[0], f"fallback diagnostic missing: {removed[0]}")
        self.assertTrue(any("admin entry removed" in str(x) for x in fallback),
                        f"admin-entry cleanup result not ledgered: {fallback}")
        self.assertEqual(len([e for e in self.release_entries(rel) if e.get("retired")]), 1)

    # ------------------------------------------------------------- retire ordering / crash safety

    def test_s14_removed_ledgered_before_retire_failure(self) -> None:
        """The `removed` ledger line (the recovery record) must land before retirement; a retire
        failure is ledgered as its own error line and apply continues instead of crashing."""
        repo = self.make_git_repo()
        wt = self.add_git_wt(repo, "rel14")
        entry = self.capture_release(wt, repo, "git")
        rel = self.releases_file(entry)
        _, plan_path = self.plan("--releases", rel)
        rel.write_text(json.dumps({"releases": []}))  # clobbered mid-flight: retirement will fail
        r, ledger = self.apply(plan_path, "--releases", rel, check=False)
        self.assertEqual(r.returncode, 0, f"apply crashed on the retire failure:\n{r.stderr}")
        self.assertFalse(wt.exists())
        removed = [e for e in ledger if e.get("op") == "removed" and e.get("path") == str(wt)]
        self.assertTrue(removed, f"removal completed with no removed ledger line (recovery record lost): {ledger}")
        retire_err = [e for e in ledger if e.get("op") == "error" and e.get("stage") == "retire"]
        self.assertTrue(retire_err, f"retire failure not ledgered: {ledger}")
        self.assertTrue(any(e.get("op") == "apply-complete" for e in ledger),
                        "apply did not continue past the retire failure")

    # ------------------------------------------------------------- releases-file lock

    def test_s15_concurrent_releases_file_mutators_lose_no_update(self) -> None:
        """Two retire_release calls interleaved read-before-write: without an exclusive lock on
        the releases file the second writer resurrects the first retirement (lost update).
        Direct in-process interleaving via a json.load seam — deterministic pre-fix (the second
        mutator runs to completion inside the first one's read/write window); post-fix the
        LOCK_EX on the file itself makes the second mutator wait its turn."""
        import threading
        sys.path.insert(0, str(SCRIPTS_DIR))
        import disk_hygiene as dh  # noqa: PLC0415
        repo = self.make_git_repo()
        wa = self.add_git_wt(repo, "rel15a")
        wb = self.add_git_wt(repo, "rel15b")
        ea = self.capture_release(wa, repo, "git")
        eb = self.capture_release(wb, repo, "git")
        rel = self.releases_file(ea, eb)
        a_read = threading.Event()
        b_done = threading.Event()
        first = threading.Event()
        real_json = dh.json

        class Shim:
            def __getattr__(self, name: str) -> Any:
                return getattr(real_json, name)

            def load(self, fp: Any) -> Any:
                doc = real_json.load(fp)
                if not first.is_set():  # pause only the first mutator, after its read
                    first.set()
                    a_read.set()
                    b_done.wait(timeout=5)
                return doc

        errors: list[BaseException] = []

        def retire(entry: dict[str, Any]) -> None:
            try:
                dh.retire_release(str(rel), entry, "removed")
            except BaseException as exc:  # noqa: BLE001 — surfaced via assertion below
                errors.append(exc)

        dh.json = Shim()  # type: ignore[assignment]
        try:
            ta = threading.Thread(target=retire, args=(ea,))
            ta.start()
            self.assertTrue(a_read.wait(timeout=5), "first mutator never reached its read")
            tb = threading.Thread(target=retire, args=(eb,))
            tb.start()
            tb.join(timeout=2)  # unlocked: completes inside A's window; locked: blocks on the flock
            b_done.set()
            ta.join(timeout=10)
            tb.join(timeout=10)
        finally:
            dh.json = real_json
        self.assertFalse(errors, f"retire_release raised: {errors}")
        self.assertFalse(ta.is_alive() or tb.is_alive(), "a mutator deadlocked")
        retired = [e for e in self.release_entries(rel) if e.get("retired")]
        self.assertEqual(len(retired), 2,
                         f"a concurrent retirement was lost (read-modify-write not serialized): {self.release_entries(rel)}")

    def test_s16_release_capture_appends_to_releases_file(self) -> None:
        """release-capture --releases appends the entry to the file (under the same lock the
        retirement path holds) as well as printing it."""
        repo = self.make_git_repo()
        wt = self.add_git_wt(repo, "rel16b")
        rel = self.releases_file()
        r = script("release-capture", "--path", wt, "--repo", repo, "--kind", "git",
                   "--session", "test-session", "--message", "released by test", "--releases", rel)
        printed = json.loads(r.stdout)
        entries = self.release_entries(rel)
        self.assertEqual(len(entries), 1, f"entry not appended: {entries}")
        self.assertEqual(entries[0], printed)

    # ------------------------------------------------------------- unnamed STALE_REGISTRATION

    def test_s17_unnamed_stale_registration_reported_not_dropped(self) -> None:
        """A git worktree admin entry whose directory is gone classifies STALE_REGISTRATION with
        no jj name and has no named twin row — it must land in `refused`, not vanish."""
        repo = self.make_jj_repo()
        wt = self.base / "plainwt17"
        sh("git", "-C", repo, "worktree", "add", "-q", "--detach", wt)
        shutil.rmtree(wt)
        inv = self.inventory(repo)
        rows = json.loads(inv.read_text())["rows"]
        row = next(r for r in rows if r["path"] == str(wt))
        self.assertEqual(row["class"], "STALE_REGISTRATION")
        self.assertIsNone(row["name"])
        doc, _ = self.plan("--fresh-hours", "0", inventory=inv)
        self.assertNotIn(str(wt), self.plan_paths(doc))
        refused = [x for x in doc["refused"] if x.get("path") == str(wt)]
        self.assertTrue(refused, f"unnamed STALE_REGISTRATION dropped silently: {doc['refused']}")
        self.assertIn("git worktree admin entry", refused[0]["reason"])

    # ------------------------------------------------------------- admin-id join safety

    def test_s18_crafted_admin_id_never_escapes_worktrees_dir(self) -> None:
        """A crafted .git pointer ending in '/..' makes slot_identity's basename capture yield
        admin_id '..', which a matching crafted release entry passes through the identity gate —
        so _cleanup_admin_entry must refuse non-component ids instead of joining them onto
        .git/worktrees/ and rmtree'ing .git itself (or the whole worktrees/ dir)."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        import disk_hygiene as dh  # noqa: PLC0415
        repo = self.make_git_repo()
        self.add_git_wt(repo, "rel18")  # a live worktree so .git/worktrees/ exists (the shared-store state)
        slot = self.base / "crafted18"
        slot.mkdir()
        (slot / ".git").write_text(f"gitdir: {repo}/.git/worktrees/..\n")
        ident = dh.slot_identity(str(slot))
        assert ident is not None, "crafted pointer slot must still yield an identity for this scenario"
        self.assertEqual(ident["admin_id"], "..",
                         "precondition: basename of the crafted pointer is '..' (matches a crafted entry)")
        rmtree_targets: list[str] = []
        real_rmtree = shutil.rmtree

        def recording_rmtree(p: Any, *a: Any, **k: Any) -> None:
            rmtree_targets.append(str(p))  # record only — never delete during this probe

        dh.shutil.rmtree = recording_rmtree  # type: ignore[assignment]
        try:
            results = {bad: dh._cleanup_admin_entry(str(repo), bad)  # noqa: SLF001
                       for bad in (ident["admin_id"], "", ".", "sub/dir", "../evil")}
        finally:
            dh.shutil.rmtree = real_rmtree
        worktrees = (repo / ".git" / "worktrees").resolve()
        for target in rmtree_targets:
            rp = Path(target).resolve()
            self.assertTrue(rp.parent == worktrees and rp.name not in ("", ".", ".."),
                            f"rmtree target escapes .git/worktrees/: {target} -> {rp}")
        self.assertEqual(rmtree_targets, [], "no rmtree may run for a non-component admin id")
        for bad, result in results.items():
            self.assertIn("refused", result, f"admin id {bad!r} not refused: {result}")

    # ------------------------------------------------------------- scratch (2026-09-18)

    def age(self, path: Path, hours: float) -> None:
        old = time.time() - hours * 3600
        os.utime(path, (old, old))

    def test_s21_stale_scratch_directory_is_planned_and_removed(self) -> None:
        """The pile that filled the box: a plain directory no workspace walk ever saw. 1.3 TB
        of /tmp was invisible to this inventory until it grew a scratch class."""
        repo = self.make_jj_repo()
        scratch = self.base / "forrest"
        scratch.mkdir()
        (scratch / "blob").write_text("x" * 4096)
        self.age(scratch, 48)

        doc, plan_path = self.plan("--protected", self.protected_file(), inventory=self.inventory(repo))
        planned = [i for i in doc["plan"] if i["kind"] == "scratch"]
        self.assertEqual([i["path"] for i in planned], [str(scratch)], f"scratch not planned: {doc['plan']}")

        _, entries = self.apply(plan_path)
        removed = [e for e in entries if e["op"] == "removed" and e.get("kind") == "scratch"]
        self.assertEqual([e["path"] for e in removed], [str(scratch)], f"scratch not removed: {entries}")
        self.assertFalse(scratch.exists(), "scratch directory survived its own removal entry")

    def test_s22_fresh_scratch_directory_survives_plan_and_apply(self) -> None:
        """Freshness is the whole guard for a plain directory: a dir written to inside the
        window is someone's live work with no process holding it open right now."""
        repo = self.make_jj_repo()
        fresh = self.base / "fresh-scratch"
        fresh.mkdir()
        (fresh / "blob").write_text("y")
        self.age(fresh, 1)

        doc, _ = self.plan("--protected", self.protected_file(), inventory=self.inventory(repo))
        self.assertEqual([i for i in doc["plan"] if i["kind"] == "scratch"], [], f"fresh scratch planned: {doc['plan']}")
        self.assertTrue(fresh.exists())

    def test_s23_scratch_written_to_after_planning_survives_apply(self) -> None:
        """The plan can be an hour old. A directory touched between plan and apply must
        survive on the apply-time re-derivation, not on the plan's stale evidence."""
        repo = self.make_jj_repo()
        scratch = self.base / "touched-after-plan"
        scratch.mkdir()
        (scratch / "blob").write_text("z")
        self.age(scratch, 48)

        _, plan_path = self.plan("--protected", self.protected_file(), inventory=self.inventory(repo))
        os.utime(scratch, None)  # an agent comes back to it

        _, entries = self.apply(plan_path)
        skips = [e for e in entries if e["op"] == "skip" and e["path"] == str(scratch)]
        self.assertTrue(skips and "written to" in skips[0]["reason"], f"touched scratch not skipped: {entries}")
        self.assertTrue(scratch.exists(), "a directory written to after planning was removed")

    def test_s24_scratch_that_became_a_working_copy_survives_apply(self) -> None:
        """A scratch path that grew a .jj between plan and apply is a workspace now, and the
        scratch path has none of the divergence checks a workspace removal requires."""
        repo = self.make_jj_repo()
        scratch = self.base / "became-workspace"
        scratch.mkdir()
        (scratch / "blob").write_text("w")
        self.age(scratch, 48)

        _, plan_path = self.plan("--protected", self.protected_file(), inventory=self.inventory(repo))
        (scratch / ".jj").mkdir()
        self.age(scratch, 48)  # mkdir bumped the mtime; re-age so ONLY the working-copy guard can save it

        _, entries = self.apply(plan_path)
        skips = [e for e in entries if e["op"] == "skip" and e["path"] == str(scratch)]
        self.assertTrue(skips and "working copy" in skips[0]["reason"], f"new working copy not skipped: {entries}")
        self.assertTrue(scratch.exists(), "a path that became a working copy was removed as scratch")

    def test_s25_live_scratch_directory_is_never_planned(self) -> None:
        """A process with its cwd inside the directory keeps it, exactly as for a workspace."""
        repo = self.make_jj_repo()
        scratch = self.base / "live-scratch"
        scratch.mkdir()
        self.age(scratch, 48)
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=scratch)
        try:
            doc, _ = self.plan("--protected", self.protected_file(), inventory=self.inventory(repo))
            self.assertEqual([i for i in doc["plan"] if i["kind"] == "scratch"], [],
                             f"live scratch planned: {doc['plan']}")
        finally:
            proc.terminate()
            proc.wait(timeout=30)

    def test_s26_protected_scratch_directory_is_never_planned(self) -> None:
        repo = self.make_jj_repo()
        scratch = self.base / "protected-scratch"
        scratch.mkdir()
        self.age(scratch, 48)
        prot = self.base / "protected.json"
        prot.write_text(json.dumps({"protected": [str(scratch)]}))

        doc, _ = self.plan("--protected", prot, inventory=self.inventory(repo))
        self.assertEqual([i for i in doc["plan"] if i["kind"] == "scratch"], [],
                         f"protected scratch planned: {doc['plan']}")


class ContainerCensusOtherFamilyTest(unittest.TestCase):
    """cmd_containers' box-local-daemon discriminator for family="other" rows (AGENTC-751):
    four lanes (e2e, env typing, Reaper, agent-c#20033) independently found the same leaked
    census row a leaked/stranded-only reaper would otherwise never remove."""

    def test_box_local_created_never_started_stale_no_holders_is_leaked(self) -> None:
        self.assertTrue(disk_hygiene._is_leaked_never_started(
            True, "created", 0, "0001-01-01T00:00:00Z", 2.0, []))

    def test_shared_daemon_same_container_stays_unknown(self) -> None:
        self.assertFalse(disk_hygiene._is_leaked_never_started(
            False, "created", 0, "0001-01-01T00:00:00Z", 2.0, []))

    def test_box_local_too_young_stays_unknown(self) -> None:
        self.assertFalse(disk_hygiene._is_leaked_never_started(
            True, "created", 0, "0001-01-01T00:00:00Z", 0.5, []))

    def test_box_local_with_holder_stays_unknown(self) -> None:
        self.assertFalse(disk_hygiene._is_leaked_never_started(
            True, "created", 0, "0001-01-01T00:00:00Z", 2.0, [(123, "some-holder-process")]))

    def test_box_local_ran_once_real_started_at_stays_unknown(self) -> None:
        self.assertFalse(disk_hygiene._is_leaked_never_started(
            True, "created", 0, "2026-09-24T10:00:00Z", 2.0, []))

    def test_docker_info_failure_treated_as_not_box_local(self) -> None:
        original = disk_hygiene.run
        disk_hygiene.run = lambda cmd, cwd=None, timeout=600: (1, "Cannot connect to the Docker daemon")
        try:
            self.assertFalse(disk_hygiene._docker_daemon_is_box_local())
        finally:
            disk_hygiene.run = original


if __name__ == "__main__":
    unittest.main()
