"""A machine that runs no agent box, for the tests that call disk-fill-watch's run_once.

run_once measures the machine's agent boxes as well as its mounts: the home of each running box
(`docker ps` and `docker inspect`, then free_bytes of that home), the memory cgroup of each box,
and the box images and caches. Those tests patch free_bytes to put their own number on their own
mount, so every box running on the host got that number too, and each one sent a BOX DISK LOW
message that the test did not expect: on sami-devbox, where boxes run, they failed; in a Legion
pod, which runs no box, they passed. The box passes have their own tests
(test_disk_fill_watch_boxes.py, test_disk_fill_watch_box_memory.py). Here the test decides this
input as it decides every other: no box runs.
"""

from __future__ import annotations

import os
import unittest
import unittest.mock
from types import ModuleType


def no_agent_boxes(test: unittest.TestCase, watch: ModuleType) -> None:
    """For the rest of `test`: `watch` sees no running box, no box cgroup, no box image and no box cache."""
    for name in ("running_box_homes", "box_memory_readings"):
        original = getattr(watch, name)
        setattr(watch, name, lambda: [])
        test.addCleanup(setattr, watch, name, original)
    environment = unittest.mock.patch.dict(os.environ, {
        "AGENTBOX_DISKS": "/nonexistent/agentbox-disks",
        "AGENTBOX_CACHE_ROOT": "/nonexistent/box-cache",
    })
    environment.start()
    test.addCleanup(environment.stop)
