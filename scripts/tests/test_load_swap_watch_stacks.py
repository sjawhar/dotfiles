#!/usr/bin/env python3
"""Regression tests for load-swap-watch's stack evidence: which half of the kernel stall we have.

sami-devbox's stall has two halves and only one of them a reboot fixes (kernel-stall-options.md,
dispatch://INFRA-993). The SCANNING half is `cleanup_offline_cgwb()` re-walking a dying cgroup's
inode lists, fixed by torvalds f6988c90671e, which Ubuntu's linux-gcp-7.0 7.0.0-1017 carries. The
WAITING half is `cgroup_writeback_umount()`'s global `synchronize_rcu()` + `flush_workqueue()`,
fixed only by 31c1d19ead2c in mainline 7.2, which no Ubuntu 24.04 kernel will carry. The stacks
of the blocked tasks are what tells them apart, so these tests hold the reading rule against a
fabricated stack of each kind:

- a kworker in `cleanup_offline_cgwb` reads as the scanning half, and says 1017 fixes it;
- an umount waiting in `cgroup_writeback_umount` with no scan frame anywhere reads as the
  waiting half, and says 1017 does not fix it and the box images must be split;
- both at once reads as the scanning half, because `cgroup_writeback_umount` is in every
  umount's stack by construction and only the scan frame says where the time goes;
- stacks naming neither, and stacks that could not be read, each say so rather than guessing.

It also holds the capture's shape: the D-state walk, the dmesg blocks the sysrq read keeps, one
capture per episode, and the one line the alert message carries.

Nothing here reads the real /proc, runs sudo, writes to /proc/sysrq-trigger, or sends anything.
"""

import importlib.machinery
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "load-swap-watch"
LOADER = importlib.machinery.SourceFileLoader("load_swap_watch_stacks", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)


# A kworker draining a dying cgroup's inode list: the scanning half, as /proc/<pid>/stack prints
# it. Frames taken from the shape f6988c90671e describes.
SCAN_STACK = """[<0>] __schedule+0x2d0/0x660
[<0>] schedule+0x27/0xb0
[<0>] cleanup_offline_cgwb+0x1b4/0x3a0
[<0>] cgwb_release_workfn+0x6e/0x240
[<0>] process_one_work+0x17c/0x350
[<0>] worker_thread+0xaa/0x350"""

# An umount parked in the global wait: the waiting half, the one 7.0.0-1017 does not fix.
WAIT_STACK = """[<0>] __schedule+0x2d0/0x660
[<0>] schedule+0x27/0xb0
[<0>] schedule_timeout+0x9e/0x170
[<0>] wait_for_completion+0x8d/0x160
[<0>] __flush_workqueue+0x15e/0x460
[<0>] cgroup_writeback_umount+0x4b/0x80
[<0>] generic_shutdown_super+0x15c/0x170
[<0>] kill_block_super+0x1b/0x40"""

# A box doing ordinary IO: neither function, and not this bug.
OTHER_STACK = """[<0>] folio_wait_bit_common+0x13d/0x360
[<0>] filemap_read+0x115/0x510
[<0>] vfs_read+0x261/0x390
[<0>] do_syscall_64+0xd9/0x5b0"""


def capture(tasks=(), workers=(), sysrq_text="", errors=(), at=1762290000.0):
    """A capture as capture_stacks builds one, with the stacks a test fabricates."""
    task_list = [dict(task) for task in tasks]
    worker_list = [dict(worker) for worker in workers]
    texts = [task["stack"] for task in task_list] + [worker["stack"] for worker in worker_list] + [sysrq_text]
    return {
        "at": at,
        "seconds": 3.2,
        "tasks": task_list,
        "workers": worker_list,
        "sysrq": {
            "ok": bool(sysrq_text),
            "error": "" if sysrq_text else "`sudo -n sh -c 'echo w > /proc/sysrq-trigger'` failed (exit 1): no sudo",
            "lines": 3021 if sysrq_text else 0,
            "blocks": 80 if sysrq_text else 0,
            "stall_blocks": 2 if sysrq_text else 0,
            "kept": [sysrq_text.splitlines()] if sysrq_text else [],
            "text": sysrq_text,
        },
        "errors": list(errors),
        "half": WATCH.half_verdict(texts),
    }


class ReadingRule(unittest.TestCase):
    """The one question the capture exists to answer, over a fabricated stack of each kind."""

    def test_scan_frame_reads_as_the_scanning_half(self):
        self.assertEqual(WATCH.half_verdict([SCAN_STACK]), "scanning")
        reading = WATCH.HALF_READING["scanning"]
        self.assertIn("the scanning half; kernel 7.0.0-1017 fixes this", reading)
        self.assertIn("cleanup_offline_cgwb", reading)

    def test_wait_frame_alone_reads_as_the_waiting_half(self):
        self.assertEqual(WATCH.half_verdict([WAIT_STACK]), "waiting")
        reading = WATCH.HALF_READING["waiting"]
        self.assertIn("the waiting half", reading)
        self.assertIn("1017 does not fix this", reading)
        self.assertIn("split off the shared filesystem", reading)

    def test_both_read_as_the_scanning_half(self):
        # Every umount's stack holds cgroup_writeback_umount -- the umount calls it. The scan
        # frame is what says the time is going into the rescan, so it decides.
        self.assertEqual(WATCH.half_verdict([WAIT_STACK, SCAN_STACK]), "scanning")

    def test_neither_function_says_so(self):
        self.assertEqual(WATCH.half_verdict([OTHER_STACK]), "neither")
        self.assertIn("does not say which half of the bug we have", WATCH.HALF_READING["neither"])

    def test_unreadable_stacks_say_so(self):
        self.assertEqual(WATCH.half_verdict([]), "unreadable")
        self.assertEqual(WATCH.half_verdict(["", "   "]), "unreadable")
        self.assertIn("No kernel stack could be read", WATCH.HALF_READING["unreadable"])

    def test_every_verdict_has_a_reading_and_a_phrase(self):
        for half in ("scanning", "waiting", "neither", "unreadable"):
            self.assertIn(half, WATCH.HALF_READING)
            self.assertIn(half, WATCH.HALF_SHORT)


class ReportSection(unittest.TestCase):
    """What the section looks like in the report the alert already writes."""

    def section(self, **kwargs):
        return WATCH.stack_section(capture(**kwargs))

    def test_the_scanning_half_as_the_report_reads_it(self):
        text = self.section(
            tasks=[{"pid": 4242, "comm": "umount", "wchan": "wait_for_completion", "blocked": 1980.0, "stack": WAIT_STACK}],
            workers=[
                {
                    "pid": 77,
                    "comm": "kworker/5:2+inode_switch_wbs",
                    "state": "R",
                    "named": True,
                    "busy": True,
                    "cpu_seconds": 5901.0,
                    "stack": SCAN_STACK,
                }
            ],
            sysrq_text="task:umount state:D\nCall Trace:\n cgroup_writeback_umount+0x4b/0x80",
        )
        self.assertTrue(text.startswith(WATCH.STACK_HEADING))
        self.assertIn("the scanning half; kernel 7.0.0-1017 fixes this", text)
        self.assertIn("pid 4242 umount, blocked 33 min, wchan wait_for_completion", text)
        self.assertIn("cleanup_offline_cgwb+0x1b4/0x3a0", text)
        self.assertIn("kworker/5:2+inode_switch_wbs (running a switch work item now), 5901.0 s of CPU", text)
        self.assertIn("it wrote 3021 kernel log lines, 80 blocked-task traces", text)

    def test_the_waiting_half_as_the_report_reads_it(self):
        text = self.section(
            tasks=[{"pid": 11, "comm": "fusermount3", "wchan": "cgroup_writeback_umount", "blocked": 7200.0, "stack": WAIT_STACK}],
            sysrq_text="task:fusermount3 state:D\nCall Trace:\n cgroup_writeback_umount+0x4b/0x80",
        )
        self.assertIn("the waiting half", text)
        self.assertIn("the box images must be split off the shared filesystem", text)
        self.assertIn("Writeback switch kworkers (0):", text)

    def test_a_capture_that_could_not_read_anything_says_why(self):
        text = self.section(errors=["`sudo -n cat /proc/<pid>/stack` failed (exit 1): sudo: a password is required"])
        self.assertIn("No kernel stack could be read", text)
        self.assertIn("a password is required", text)
        self.assertIn("echo w > /proc/sysrq-trigger", text)
        self.assertIn("no sudo", text)
        self.assertIn("Tasks in D state (0", text)


class CaptureShape(unittest.TestCase):
    """The walk, the dmesg cut, the episode gate and the message line."""

    def proc_tree(self, directory: Path, tasks: list[tuple[int, str, str]]) -> Path:
        proc = directory / "proc"
        proc.mkdir()
        (proc / "uptime").write_text("100000.00 50000.00\n")
        for pid, comm, state in tasks:
            base = proc / str(pid)
            base.mkdir()
            fields = ["0"] * 40
            fields[11], fields[12] = "300", "200"  # utime, stime: 5 s of CPU at 100 Hz
            (base / "stat").write_text(f"{pid} ({comm}) {state} " + " ".join(fields) + "\n")
            (base / "wchan").write_text("wait_for_completion")
            # se.exec_start is in ms since boot: blocked for 1,000 s of the 100,000 s uptime.
            (base / "sched").write_text("se.exec_start : 99000000.0\n")
        return proc

    def test_d_state_walk_puts_the_umounts_first(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = self.proc_tree(
                Path(directory),
                [(10, "python", "D"), (11, "umount", "D"), (12, "bash", "S"), (13, "kworker/1:0+inode_switch_wbs", "R")],
            )
            tasks = WATCH.d_state_tasks(proc)
        self.assertEqual([task["pid"] for task in tasks], [11, 10])
        self.assertEqual(tasks[0]["comm"], "umount")
        self.assertEqual(tasks[0]["wchan"], "wait_for_completion")
        self.assertAlmostEqual(tasks[0]["blocked"], 1000.0, places=3)

    def test_the_walk_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = self.proc_tree(Path(directory), [(100 + n, "python", "D") for n in range(12)])
            self.assertEqual(len(WATCH.d_state_tasks(proc, limit=5)), 5)

    def test_dmesg_is_cut_into_one_block_per_blocked_task(self):
        lines = [
            "2026-11-04T21:00:00,0+00:00 sysrq: Show Blocked State",
            "2026-11-04T21:00:00,1+00:00 task:umount state:D pid:4242",
            "2026-11-04T21:00:00,2+00:00  cgroup_writeback_umount+0x4b/0x80",
            "2026-11-04T21:00:00,3+00:00 task:tar state:D pid:99",
            "2026-11-04T21:00:00,4+00:00  folio_wait_bit_common+0x13d/0x360",
        ]
        blocks = WATCH.dmesg_blocks(lines)
        self.assertEqual([len(block) for block in blocks], [1, 2, 2])
        stall = [block for block in blocks if any(name in "\n".join(block) for name in WATCH.STALL_FUNCTIONS)]
        self.assertEqual(len(stall), 1)
        self.assertIn("umount", stall[0][0])

    def test_one_episode_is_one_capture(self):
        # A stall that opens as a hung umount and grows a writeback crossing half an hour later
        # is one episode, so the second run does not sysrq the box again.
        opened = 1762290000.0
        umount = {"kind": "umount", "event": "alert", "incident": {"opened_at": opened}}
        writeback = {"kind": "writeback", "event": "alert", "incident": {"opened_at": opened + 1800}}
        self.assertEqual(WATCH.stall_episode([umount]), f"umount:{opened:.0f}")
        self.assertEqual(WATCH.stall_episode([writeback, umount]), f"umount:{opened:.0f}")
        # A later, separate stall is a separate episode, and a run about no stall captures nothing.
        self.assertNotEqual(WATCH.stall_episode([{"kind": "umount", "event": "alert", "incident": {"opened_at": opened + 7200}}]),
                            f"umount:{opened:.0f}")
        self.assertEqual(WATCH.stall_episode([{"kind": "cpu", "event": "alert", "incident": {"opened_at": opened}}]), "")
        self.assertEqual(WATCH.stall_episode([{"kind": "umount", "event": "clear", "incident": {"opened_at": opened}}]), "")

    def test_the_alert_message_says_the_evidence_is_in_the_report(self):
        now = {
            "at": time.time(),
            "cores": 288,
            "load1": 40.0,
            "procs_blocked": 30,
            "cpu": {"some_avg60": 4.0},
            "io": {"some_avg60": 90.0},
            "memory_pressure": {"full_avg60": 0.1},
            "swap_total": 0,
            "swap_free": 0,
            "swap_used_fraction": 0.0,
            "mem_available": 100 * WATCH.GIB,
            "mem_total": 1000 * WATCH.GIB,
            "umounts": [{"pid": 4242, "comm": "umount", "mount": "/mnt/nvme/sami/agentbox/boxes/b", "blocked": 1980.0}],
            "switch_workers": [],
        }
        options = WATCH.parse_args([])
        finding = WATCH.umount_finding(now, options)
        items = [{"kind": "umount", "event": "alert", "finding": finding, "incident": {"opened_at": now["at"] - 1980}}]
        taken = capture(workers=[{"pid": 77, "comm": "kworker/5:2+inode_switch_wbs", "state": "R", "named": True,
                                  "busy": True, "cpu_seconds": 5901.0, "stack": SCAN_STACK}])
        message = WATCH.compose_message(items, now, options, None, "/tmp/report.txt", now["at"], "sami-devbox", taken)
        self.assertIn(f"Stack evidence in the report: {WATCH.HALF_SHORT['scanning']}.", message)
        self.assertLessEqual(len(message), WATCH.MESSAGE_LIMIT)
        # The action a stall message must always carry survives beside it.
        self.assertIn("reset by the reset plan", message)
        # Without a capture (none taken this episode) the message says nothing about stacks.
        plain = WATCH.compose_message(items, now, options, None, "/tmp/report.txt", now["at"], "sami-devbox")
        self.assertNotIn("Stack evidence", plain)

    def test_the_section_lands_in_the_report(self):
        now = {
            "at": time.time(),
            "cores": 288,
            "load1": 40.0,
            "load5": 40.0,
            "load15": 40.0,
            "procs_running": 5,
            "procs_blocked": 30,
            "cpu": {"some_avg60": 4.0},
            "io": {"some_avg60": 90.0},
            "memory_pressure": {"full_avg60": 0.1},
            "swap_total": 0,
            "swap_free": 0,
            "swap_used_fraction": 0.0,
            "mem_available": 100 * WATCH.GIB,
            "mem_total": 1000 * WATCH.GIB,
            "umounts": [{"pid": 4242, "comm": "umount", "mount": "/mnt/nvme/x", "blocked": 1980.0}],
            "switch_workers": [],
        }
        options = WATCH.parse_args([])
        finding = WATCH.umount_finding(now, options)
        items = [{"kind": "umount", "event": "alert", "finding": finding,
                  "incident": {"opened_at": now["at"] - 1980, "alerted_at": now["at"]}}]
        taken = capture(tasks=[{"pid": 4242, "comm": "umount", "wchan": "wait_for_completion",
                                "blocked": 1980.0, "stack": WAIT_STACK}])
        text = WATCH.report_text(items, now, options, "", now["at"], "sami-devbox", taken)
        self.assertIn(WATCH.STACK_HEADING, text)
        self.assertIn("the waiting half", text)
        # And a report written without a capture is the report it always was.
        self.assertNotIn(WATCH.STACK_HEADING, WATCH.report_text(items, now, options, "", now["at"], "sami-devbox"))


if __name__ == "__main__":
    unittest.main()
