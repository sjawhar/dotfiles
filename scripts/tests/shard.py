#!/usr/bin/env python3
"""Run the agentbox tests in N parallel shards, one process per group of test ids."""
from __future__ import annotations

import subprocess
import sys
import unittest

MODULES = ["test_agentbox_move", "test_box_ssh_config", "test_agentbox_box_home",
           "test_agentbox_limits", "test_agentbox_dirs", "test_tmux_resurrect_omp",
           "test_agentbox_reclaim", "test_omp_memory_watch_notify"]
SHARDS = int(sys.argv[1]) if len(sys.argv) > 1 else 8

ids: list[str] = []
loader = unittest.TestLoader()
for module in MODULES:
    suite = loader.loadTestsFromName(module)
    stack = [suite]
    while stack:
        item = stack.pop()
        if isinstance(item, unittest.TestSuite):
            stack.extend(item)
        else:
            ids.append(item.id())
ids.sort()
groups = [ids[i::SHARDS] for i in range(SHARDS)]
procs = [subprocess.Popen([sys.executable, "-m", "unittest", "-v", *group],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
         for group in groups if group]
bad = 0
for shard, proc in enumerate(procs):
    out = proc.communicate()[0]
    if proc.returncode != 0:
        bad += 1
        print(f"=== shard {shard} FAILED ===")
        print(out[-6000:])
    else:
        print(f"=== shard {shard} ok ({len(groups[shard])} tests) ===")
print(f"TOTAL {len(ids)} tests, {bad} failed shard(s)")
sys.exit(1 if bad else 0)
