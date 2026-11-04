#!/usr/bin/env python3
"""scripts/farm-fail-watch: which resource was at its bound, and what the lane is told.

The case these exist for (dispatch://OSSV-175, 2026-11-04 21:09Z): Kubernetes reported an
ephemeral-storage eviction of farm/20261104t210028z-2d717d as `OOMKilled`, exit 137, and the
watch named memory, because it read the exit code and the memory limit and never the disk
peak. The pod's real numbers are the DISK fixture below, taken from the cluster and from the
farm's own record that night:

  pod    ossv-farm-20261104t210028z-2d717d-kx45t-qw6vn, job container OOMKilled exit 137,
         memory limit 8503Mi, ephemeral-storage request 4Gi
  farm   memory_peak_bytes 2025984000 (1.89 GiB, 23% of the limit),
         disk_peak_bytes 21106288640 (19.66 GiB, 491% of the request), oom_kill 0

The four cases here are the four a kill can be: memory, disk, both near their bound, and
evidence that does not separate them.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "farm-fail-watch"
LOADER = importlib.machinery.SourceFileLoader("farm_fail_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

GIB = 1 << 30
MIB = 1 << 20


def pod(memory: str = "8503Mi", disk: str = "4Gi", reason: str = "OOMKilled",
        code: int = 137, name: str = "ossv-farm-20261104t210028z-2d717d-kx45t-qw6vn") -> dict:
    """A failed farm pod as the cluster returns it: the three containers, the job's end and
    the job container's own bounds."""
    return {
        "metadata": {"name": name},
        "spec": {"containers": [
            {"name": "fetch", "resources": {"requests": {"memory": "4Gi", "ephemeral-storage": "4Gi"}}},
            {"name": "job", "resources": {
                "limits": {"cpu": "3", "memory": memory, "ephemeral-storage": disk},
                "requests": {"cpu": "3", "memory": memory, "ephemeral-storage": disk}}},
            {"name": "upload", "resources": {"requests": {"memory": "2Gi", "ephemeral-storage": "4Gi"}}},
        ]},
        "status": {"phase": "Failed", "containerStatuses": [
            {"name": "fetch", "state": {"terminated": {"reason": "Completed", "exitCode": 0}}},
            {"name": "job", "state": {"terminated": {
                "reason": reason, "exitCode": code,
                "startedAt": "2026-11-04T21:05:30Z", "finishedAt": "2026-11-04T21:05:53Z"}}},
        ]},
    }


def job_record(mode: str = "measured", disk: str = "4Gi", memory: str = "8503Mi",
               recent_disk: tuple[int, ...] = (1671049216, 1721692160)) -> dict:
    return {
        "id": "farm/20261104t210028z-2d717d",
        "namespace": "builds-ossv",
        "memory": memory,
        "disk": disk,
        "store": None,
        "labels": {"ossv-unit": "spirv-tools__spvtools_binary_parser_fuzzer",
                   "ossv-kind": "oft-check",
                   "ossv-owner": "01a14b36-227b-7141-acdb-b5397251a0b1"},
        "omp_session": {"id": "01a14b36-227b-7141-acdb-b5397251a0b1"},
        "size": {"mode": mode,
                 "index": "gs://theorem-ossv-runs/out/ossv-index/build-nosandbox/oft-check/"
                          "spirv-tools__spvtools_binary_parser_fuzzer.json",
                 "recent_peaks": [{"disk_peak_bytes": peak, "memory_peak_bytes": 6775709696}
                                  for peak in recent_disk]},
    }


def result_record(memory_peak: float, disk_peak: float, oom_kill: int = 0, **extra) -> dict:
    record = {
        "finished": "2026-11-04T21:08:01Z",
        "exit_code": 255,
        "note": "PodFailurePolicy: OOMKilled, exit 137",
        "memory_peak_bytes": memory_peak,
        "memory_peak_kind": "kernel",
        "disk_peak_bytes": disk_peak,
        "disk_peak_kind": "walk",
        "oom_kill": oom_kill,
        "failed_pods": [{
            "name": "ossv-farm-20261104t210028z-2d717d-kx45t-qw6vn",
            "job": {"terminated": {"reason": "OOMKilled", "exitCode": 137,
                                   "finishedAt": "2026-11-04T21:05:53Z"}},
        }],
        "pods_missing": 0,
    }
    record.update(extra)
    return record


def read(job: dict | None, result: dict | None, pod_object: dict | None) -> tuple[dict, dict, str]:
    found = WATCH.evidence(job, result, pod_object)
    call = WATCH.verdict(found)
    return found, call, WATCH.message(found, call)


class TheMeasuredDiskKill(unittest.TestCase):
    """farm/20261104t210028z-2d717d exactly as it happened."""

    def setUp(self) -> None:
        self.found, self.call, self.text = read(
            job_record(), result_record(2025984000, 21106288640), pod())

    def test_the_resource_named_is_disk_not_the_exit_code(self) -> None:
        self.assertEqual(self.call["resource"], "disk")
        self.assertEqual(self.found["reason"], "OOMKilled")
        self.assertEqual(self.found["exit_code"], 137)

    def test_each_peak_is_compared_with_its_own_bound(self) -> None:
        self.assertAlmostEqual(self.call["memory_share"], 2025984000 / (8503 * MIB), places=4)
        self.assertAlmostEqual(self.call["disk_share"], 21106288640 / (4 * GIB), places=4)

    def test_the_message_says_disk_and_both_real_numbers(self) -> None:
        self.assertIn("DISK, not memory", self.text)
        self.assertIn("19.66 GiB", self.text)
        self.assertIn("4Gi (4.00 GiB) ephemeral-storage request (491%)", self.text)
        self.assertIn("8503Mi (8.30 GiB) limit (23%)", self.text)

    def test_it_advises_disk_and_never_a_memory_raise(self) -> None:
        # 1.3 x 19.66 GiB = 25.6 GiB -> 26Gi
        self.assertIn("--disk 26Gi", self.text)
        self.assertIn("Do not raise memory", self.text)
        self.assertNotIn("--memory", self.text)
        self.assertNotIn("RAISE MEMORY", self.text)

    def test_a_measured_size_carries_the_store_warning(self) -> None:
        self.assertIn("size.mode=measured", self.text)
        self.assertIn("build store", self.text)
        self.assertIn("1.60 GiB", self.text)   # the store-backed history it was sized from
        self.assertIn("Pass --disk explicitly", self.text)

    def test_a_size_the_caller_gave_carries_no_store_warning(self) -> None:
        _, _, text = read(job_record(mode="explicit"),
                          result_record(2025984000, 21106288640), pod())
        self.assertIn("DISK, not memory", text)
        self.assertNotIn("size.mode=measured", text)

    def test_the_verdict_holds_when_the_killed_pod_is_gone(self) -> None:
        """A killed pod is often already removed: the bounds then come from job.json."""
        found, call, text = read(job_record(), result_record(2025984000, 21106288640), None)
        self.assertEqual(call["resource"], "disk")
        self.assertEqual(found["memory_limit_bytes"], 8503 * MIB)
        self.assertEqual(found["disk_request_bytes"], 4 * GIB)
        self.assertIn("--disk 26Gi", text)


class AMemoryKill(unittest.TestCase):
    """A genuine memory kill: the peak reached the limit and the disk peak did not."""

    def test_memory_at_its_limit_with_disk_idle_is_a_memory_kill(self) -> None:
        found, call, text = read(
            job_record(), result_record(8.2 * GIB, 1.2 * GIB), pod(memory="8Gi", disk="10Gi"))
        self.assertEqual(call["resource"], "memory")
        self.assertIn("MEMORY was at its bound", text)
        self.assertIn("--memory 11Gi", text)        # 1.3 x 8.2 GiB = 10.66 -> 11Gi
        self.assertNotIn("--disk", text)
        self.assertNotIn("size.mode=measured", text)  # the store warning is the disk case's

    def test_the_kernels_oom_kill_counter_names_memory_on_its_own(self) -> None:
        """oom_kill rises only when the memcg OOM killer runs, so it settles the question
        even when the sampler's last memory peak is well under the limit."""
        found, call, text = read(
            job_record(), result_record(3 * GIB, 1.2 * GIB, oom_kill=1), pod(disk="10Gi"))
        self.assertEqual(call["resource"], "memory")
        self.assertIn("oom_kill counter is 1", call["why"])
        self.assertIn("MEMORY was at its bound", text)

    def test_a_largest_process_under_half_the_limit_says_serialise(self) -> None:
        found, call, text = read(
            job_record(),
            result_record(8.2 * GIB, 1.2 * GIB, largest_process_bytes=2 * GIB),
            pod(memory="8Gi", disk="10Gi"))
        self.assertEqual(call["resource"], "memory")
        self.assertIn("SERIALISE", text)
        self.assertNotIn("RAISE MEMORY", text)

    def test_a_largest_process_over_half_the_limit_sizes_at_1_3_x_the_peak(self) -> None:
        _, _, text = read(
            job_record(),
            result_record(8.2 * GIB, 1.2 * GIB, largest_process_bytes=6 * GIB),
            pod(memory="8Gi", disk="10Gi"))
        self.assertIn("RAISE MEMORY to --memory 11Gi", text)
        self.assertNotIn("SERIALISE", text)

    def test_an_unrecorded_largest_process_with_no_cores_either_is_said_not_assumed(self) -> None:
        _, _, text = read(job_record(), result_record(8.2 * GIB, 1.2 * GIB),
                          pod(memory="8Gi", disk="10Gi"))
        self.assertIn("RAISE MEMORY to --memory 11Gi", text)
        self.assertIn("serialise test could not be applied", text)


class TheCoresFallbackWhenTheLogHasNoProcesses(unittest.TestCase):
    """CURL, 2026-11-04 21:25Z: farm/20261104t204327z-bda302, curl__fuzz_bufq__build,
    killed at its 56Gi limit at 21:22:12Z. Its pod log was ten lines with no per-process
    memory in it at all, so the per-process test could not run; the farm's usage record
    carried cpu_last_cores 29.336 of cpu_limit_cores 31.0 at the sample 18 s before the
    kill, with memory_peak_bytes 60,991,037,440 (56.8 GiB) -- twenty-nine processes of about
    2 GiB each, the parallel case, measured."""

    CURL = dict(memory_peak=60991037440, disk_peak=1713950720,
                cpu_last_cores=29.336, cpu_peak=29.819, cpu_limit_cores=31.0)

    def curl(self, **over):
        job = job_record(disk="60Gi", memory="56Gi")
        job["cpu"], job["cpu_limit"] = 16, 30
        figures = dict(self.CURL, **over)
        result = result_record(figures.pop("memory_peak"), figures.pop("disk_peak"), **figures)
        return read(job, result, pod(memory="56Gi", disk="60Gi", code=128))

    def test_the_kill_is_memory(self) -> None:
        _, call, text = self.curl()
        self.assertEqual(call["resource"], "memory")
        self.assertIn("MEMORY was at its bound", text)

    def test_cores_near_the_core_count_read_as_the_parallel_case(self) -> None:
        _, _, text = self.curl()
        self.assertIn("SERIALISE, probably", text)
        self.assertIn("no per-process samples in this job's log", text)
        self.assertIn("cpu_last_cores 29.34 of 31 cores", text)
        self.assertIn("fewer at once at the same limit", text)
        self.assertNotIn("--memory", text)

    def test_the_wording_stays_honest_about_weaker_evidence(self) -> None:
        """"this looks like", not an assertion, and it says to confirm from per-process
        samples before buying memory."""
        _, _, text = self.curl()
        self.assertIn("this looks like the parallel case", text)
        self.assertIn("Confirm from the next run's per-process samples", text)

    def test_one_process_alone_is_the_case_a_bigger_pod_fixes(self) -> None:
        _, _, text = self.curl(cpu_last_cores=1.02)
        self.assertIn("RAISE MEMORY to --memory 74Gi", text)   # 1.3 x 56.8 GiB
        self.assertIn("this looks like one process alone", text)
        self.assertNotIn("SERIALISE", text)

    def test_cores_in_between_settle_nothing_and_say_so(self) -> None:
        _, _, text = self.curl(cpu_last_cores=12.0)
        self.assertIn("neither the parallel case nor one process alone", text)
        self.assertIn("serialise test is unsettled", text)

    def test_per_process_samples_still_win_when_the_log_has_them(self) -> None:
        """The cores reading is the fallback, not the rule: a recorded largest process
        decides even when cpu_last_cores says parallel."""
        _, _, text = self.curl(largest_process_bytes=40 * GIB)
        self.assertIn("RAISE MEMORY to --memory 74Gi", text)
        self.assertNotIn("cpu_last_cores", text)


class BothNearTheirBound(unittest.TestCase):
    """Both peaks close to their own bound: say both, and which was closer."""

    def setUp(self) -> None:
        # memory 7.8 of 8 GiB (97.5%), disk 9.9 of 10 GiB (99.0%): disk is closer.
        self.found, self.call, self.text = read(
            job_record(), result_record(7.8 * GIB, 9.9 * GIB), pod(memory="8Gi", disk="10Gi"))

    def test_both_are_named(self) -> None:
        self.assertEqual(self.call["resource"], "both")
        self.assertIn("BOTH were at their bound", self.text)
        self.assertIn("memory peak 7.80 GiB", self.text)
        self.assertIn("disk peak 9.90 GiB", self.text)

    def test_the_closer_one_is_said(self) -> None:
        self.assertEqual(self.call["closer"], "disk")
        self.assertIn("DISK was closer to its own bound", self.text)

    def test_both_sizes_are_advised_and_neither_is_forbidden(self) -> None:
        self.assertIn("--memory 11Gi", self.text)   # 1.3 x 7.8 = 10.14 -> 11Gi
        self.assertIn("--disk 13Gi", self.text)     # 1.3 x 9.9 = 12.87 -> 13Gi
        self.assertNotIn("Do not raise memory", self.text)

    def test_memory_closer_reads_the_other_way_round(self) -> None:
        _, call, text = read(job_record(), result_record(7.98 * GIB, 8.2 * GIB),
                             pod(memory="8Gi", disk="10Gi"))
        self.assertEqual(call["closer"], "memory")
        self.assertIn("MEMORY was closer to its own bound", text)

    def test_a_disk_share_in_the_both_case_still_warns_about_a_measured_size(self) -> None:
        self.assertIn("size.mode=measured", self.text)


class EvidenceThatDoesNotSeparateThem(unittest.TestCase):
    """Say so rather than guess: a wrong confident message is what this watch is fixing."""

    def test_neither_peak_near_its_bound_is_said_plainly(self) -> None:
        found, call, text = read(
            job_record(), result_record(2 * GIB, 2 * GIB), pod(memory="8Gi", disk="10Gi"))
        self.assertEqual(call["resource"], "unknown")
        self.assertIn("NOT DISTINGUISHABLE", text)
        self.assertIn("the kill reason is not distinguishable from these", text)
        self.assertIn("memory peak 2.00 GiB", text)
        self.assertIn("disk peak 2.00 GiB", text)

    def test_it_advises_no_raise_at_all(self) -> None:
        _, _, text = read(job_record(), result_record(2 * GIB, 2 * GIB),
                          pod(memory="8Gi", disk="10Gi"))
        self.assertIn("DO NOT raise either size blindly", text)
        self.assertNotIn("RAISE MEMORY", text)
        self.assertNotIn("RAISE DISK", text)
        self.assertNotIn("--memory ", text)
        self.assertNotIn("--disk ", text)

    def test_a_missing_peak_is_unknown_not_a_guess(self) -> None:
        """No sampler record at all: nothing can be compared with anything."""
        found, call, text = read(job_record(), {"failed_pods": []}, pod())
        self.assertEqual(call["resource"], "unknown")
        self.assertIn("a peak or a bound is missing", call["why"])
        self.assertIn("memory peak unknown", text)

    def test_one_known_peak_at_its_bound_still_names_that_resource(self) -> None:
        """The disk peak is missing, memory is at its limit: memory is named, since the one
        comparison that can be made is decisive."""
        record = result_record(7.9 * GIB, 0)
        record["disk_peak_bytes"] = None
        _, call, text = read(job_record(), record, pod(memory="8Gi", disk="10Gi"))
        self.assertEqual(call["resource"], "memory")
        self.assertIn("disk peak unknown", text)


class TheUnits(unittest.TestCase):
    def test_kubernetes_quantities_become_bytes(self) -> None:
        for text, value in (("8503Mi", 8503 * MIB), ("4Gi", 4 * GIB), ("2000000", 2000000),
                            ("1G", 10 ** 9), ("512Ki", 512 * 1024), (None, None), ("x", None)):
            self.assertEqual(WATCH.size_bytes(text), value, text)

    def test_a_size_to_pass_the_farm_is_whole_gib_rounded_up(self) -> None:
        self.assertEqual(WATCH.whole_gib(1.3 * 21106288640), "26Gi")
        self.assertEqual(WATCH.whole_gib(1), "1Gi")

    def test_the_unit_label_reads_as_a_lane_writes_it(self) -> None:
        self.assertEqual(WATCH.unit_name("spirv-tools__spvtools_binary_parser_fuzzer"),
                         "spirv-tools/spvtools_binary_parser_fuzzer")


if __name__ == "__main__":
    unittest.main()
