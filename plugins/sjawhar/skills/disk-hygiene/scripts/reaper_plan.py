#!/usr/bin/env python3
"""Hourly PLAN-ONLY workspace-reaper driver (plan-workspace-reaper.md, D7).

Regenerates the protected set (D4), inventories the configured store, produces a
deletion plan, and ledgers the diff against the previous run's plan. It NEVER
invokes `apply` — no code path from this driver removes anything. The apply
schedule arrives only after the plan's scenario 14 (attended first real pass)
settles; until then the timer gathers evidence.

Subcommands:
  run             one plan-only pass (what disk-hygiene-reaper.service executes)
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
    dh.ledger_write(ledger, {
        "op": "plan-only-run",  # D7: the dry run. This driver never invokes apply.
        "items": len(plan_doc["plan"]), "refused": len(plan_doc["refused"]),
        "added": sorted(new_keys - prev_keys) if prev_keys is not None else sorted(new_keys),
        "left_plan": sorted(prev_keys - new_keys) if prev_keys is not None else [],
        "first_run": prev_keys is None,
        "sources": prot["sources"], "plan_file": str(plan_path),
    })
    shutil.copyfile(plan_path, prev_path)  # rotate only after the diff is ledgered


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
