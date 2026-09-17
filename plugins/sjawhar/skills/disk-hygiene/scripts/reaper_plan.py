#!/usr/bin/env python3
"""Hourly workspace-reaper driver (plan-workspace-reaper.md, D7; amended 2026-09-18).

Regenerates the protected set (D4), inventories the configured store, produces a
deletion plan, and ledgers the diff against the previous run's plan. Then, ONLY
when free space is below the config's `apply_below_free_gb`, it consumes that plan
through `disk_hygiene.py apply` — the same path a hand-run uses, with its own
re-verification of every item. Above the floor, or with the key unset/0, nothing
is removed and the run is evidence-gathering exactly as before.

Why it changed: on 2026-09-18 the box reached 91% used while this driver had
planned 49 items an hour for weeks and consumed none, and the pile that filled it
(1.3 TB of plain /tmp scratch) was not even in the plan — see the SCRATCH class in
disk_hygiene.cmd_inventory.

Subcommands:
  run             one pass: plan always, apply below the floor (what
                  disk-hygiene-reaper.service executes)
  record-failure  append a timer-failure line with the unit's journal tail to the
                  ledger (what disk-hygiene-reaper-failure.service executes via
                  OnFailure=)

Config (per-machine, seeded by installers/disk-hygiene-reaper.sh — absolute paths
stay out of the committed tree): ~/.config/disk-hygiene/reaper.json
  {"repo": "...", "roots": [...], "fresh_hours": 24,
   "protected": [fixed entries...], "anchors": [...]}
The config file doubles as the D4 generator's --fixed file (extra keys ignored).

State and the human-read surface: ~/.local/state/disk-hygiene/
  protected.json, inventory.json, plan.json, plan.prev.json, and
  reaper-ledger.jsonl — one `plan-only-run` line per timer fire (the plan diff),
  one `timer-failure` line per OnFailure. A releases.json in the same dir is
  passed to `plan` when present (read-only there; only apply retires entries).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import disk_hygiene as dh  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "disk_hygiene.py"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "disk-hygiene" / "reaper.json"
STATE = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "disk-hygiene"
UNIT = "disk-hygiene-reaper.service"


def step(argv: list[str], dest: Path) -> dict[str, Any]:
    """Run one pipeline stage; a non-zero exit kills the run loudly (OnFailure= then
    surfaces it). stdout is preserved verbatim in `dest` for the human reader."""
    r = subprocess.run(argv, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        sys.exit(f"reaper-plan: {' '.join(argv)} failed rc={r.returncode}\n"
                 f"stdout: {r.stdout[-2000:]}\nstderr: {r.stderr[-2000:]}")
    dest.write_text(r.stdout)
    return json.loads(r.stdout)


def plan_key(item: dict[str, Any]) -> str:
    return f"{item.get('kind')}|{item.get('path')}|{item.get('name')}"


def cmd_run() -> None:
    if not CONFIG.is_file():
        sys.exit(f"reaper-plan: config missing: {CONFIG} — run installers/disk-hygiene-reaper.sh")
    cfg: dict[str, Any] = json.loads(CONFIG.read_text())
    STATE.mkdir(parents=True, exist_ok=True)
    ledger = STATE / "reaper-ledger.jsonl"
    py = sys.executable
    prot = step([py, str(SCRIPT), "protected", "--fixed", str(CONFIG)], STATE / "protected.json")
    inv_argv = [py, str(SCRIPT), "inventory", "--repo", str(cfg["repo"])]
    for root in cfg["roots"]:
        inv_argv += ["--root", str(root)]
    step(inv_argv, STATE / "inventory.json")
    plan_argv = [py, str(SCRIPT), "plan", "--inventory", str(STATE / "inventory.json"),
                 "--protected", str(STATE / "protected.json"),
                 "--fresh-hours", str(cfg.get("fresh_hours", 24))]
    releases = STATE / "releases.json"
    if releases.is_file():
        plan_argv += ["--releases", str(releases)]
    plan_path = STATE / "plan.json"
    plan_doc = step(plan_argv, plan_path)
    new_keys = {plan_key(i) for i in plan_doc["plan"]}
    prev_path = STATE / "plan.prev.json"
    prev_keys: set[str] | None = None
    if prev_path.is_file():
        prev_keys = {plan_key(i) for i in json.loads(prev_path.read_text())["plan"]}
    free_gb = dh.df_free_gb()
    floor_gb = float(cfg.get("apply_below_free_gb", 0) or 0)
    applying = bool(floor_gb) and free_gb < floor_gb
    dh.ledger_write(ledger, {
        "op": "apply-run" if applying else "plan-only-run",
        "items": len(plan_doc["plan"]), "refused": len(plan_doc["refused"]),
        "added": sorted(new_keys - prev_keys) if prev_keys is not None else sorted(new_keys),
        "left_plan": sorted(prev_keys - new_keys) if prev_keys is not None else [],
        "first_run": prev_keys is None,
        "sources": prot["sources"], "plan_file": str(plan_path),
        "free_gb": free_gb, "apply_below_free_gb": floor_gb or None,
    })
    shutil.copyfile(plan_path, prev_path)  # rotate only after the diff is ledgered
    if applying:
        # The plan is what it always was; what changed on 2026-09-18 is that a box below the
        # floor now consumes it. Every removal still passes apply's own re-verification
        # (protected re-read, live processes, divergence for a workspace, age for scratch),
        # and each one is ledgered with its recovery id. Above the floor nothing is deleted:
        # the steady state stays plan-only, which is the ruling this preserves.
        apply_argv = [py, str(SCRIPT), "apply", "--plan", str(plan_path),
                      "--protected", str(STATE / "protected.json"), "--ledger", str(ledger)]
        if releases.is_file():
            apply_argv += ["--releases", str(releases)]
        r = subprocess.run(apply_argv, capture_output=True, text=True)
        if r.returncode != 0:
            # Loud: a non-zero exit fails the unit, which fires OnFailure= into the ledger.
            sys.exit(f"reaper-plan: apply failed rc={r.returncode}: {r.stderr[-400:]}")
        dh.ledger_write(ledger, {"op": "apply-finished", "free_gb_after": dh.df_free_gb(),
                                 "free_gb_before": free_gb, "floor_gb": floor_gb})


def cmd_record_failure() -> None:
    """OnFailure= handler: put the failure where a human reads (the reaper ledger),
    with the unit's journal tail. Recording never raises past a journal read
    problem — the entry itself carries the journalctl rc/stderr instead."""
    r = subprocess.run(["journalctl", "--user", "-u", UNIT, "-n", "25", "--no-pager", "-o", "cat"],
                       capture_output=True, text=True, timeout=120)
    STATE.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {"op": "timer-failure", "unit": UNIT,
                             "journal_tail": r.stdout.strip().splitlines()[-25:]}
    if r.returncode != 0:
        entry["journalctl_error"] = f"rc={r.returncode}: {r.stderr[-400:]}"
    dh.ledger_write(STATE / "reaper-ledger.jsonl", entry)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("run", "record-failure"):
        sys.exit(f"usage: {sys.argv[0]} run|record-failure")
    if sys.argv[1] == "run":
        cmd_run()
    else:
        cmd_record_failure()


if __name__ == "__main__":
    main()
