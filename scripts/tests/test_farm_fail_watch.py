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

from datetime import datetime, timedelta, timezone
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class TheSerialiseAdviceNamesHowManyProcesses(unittest.TestCase):
    """A reader given only the largest process looks for the rest of the limit elsewhere.

    On 2026-11-05 two grade pods were OOM-killed at 48 GiB with the killed task holding 22.0
    and 20.6 GiB. The box-health lead read the ~26 GiB difference as files on a memory-backed
    filesystem and told the lane so. It was not: each pod ran eight workers under one parent,
    and the measurement inside the live pods gave memory.stat `file` 0 and `shmem` 0, with the
    summed process RSS matching `memory.current` within 0.1%. The advice now names the count,
    so the ordinary explanation is in the message rather than left to the reader.
    """

    GIB = 1024 ** 3

    def advice(self, **found: object) -> str:
        return WATCH.memory_advice(found)

    def test_it_names_the_count_and_what_holds_the_rest(self) -> None:
        text = self.advice(largest_process_bytes=22 * self.GIB,
                           memory_limit_bytes=48 * self.GIB,
                           memory_peak_bytes=47 * self.GIB, process_count=8)
        self.assertIn("SERIALISE", text)
        self.assertIn("8 processes", text)
        self.assertIn("the other 7", text)

    def test_without_a_count_it_says_no_more_than_it_knows(self) -> None:
        text = self.advice(largest_process_bytes=22 * self.GIB,
                           memory_limit_bytes=48 * self.GIB, memory_peak_bytes=47 * self.GIB)
        self.assertIn("SERIALISE", text)
        self.assertNotIn("processes, so the rest", text)

    def test_one_process_adds_no_claim_about_others(self) -> None:
        """A single sampled process cannot explain a gap, so the sentence stays out."""
        text = self.advice(largest_process_bytes=22 * self.GIB,
                           memory_limit_bytes=48 * self.GIB,
                           memory_peak_bytes=47 * self.GIB, process_count=1)
        self.assertIn("SERIALISE", text)
        self.assertNotIn("the other 0", text)

    def test_one_oversized_process_is_still_a_bigger_pod(self) -> None:
        text = self.advice(largest_process_bytes=44 * self.GIB,
                           memory_limit_bytes=48 * self.GIB,
                           memory_peak_bytes=47 * self.GIB, process_count=1)
        self.assertIn("RAISE MEMORY", text)
        self.assertIn("62Gi", text)


class AJobWhoseMemoryIsUnboundedIsNotToldToBuyMore(unittest.TestCase):
    """"Ask for 1.25x" is right for a job that needs more and wrong for one with no ceiling.

    CURL, 2026-11-05: three memory kills in one lane in one morning, three different correct
    remedies, and the watch advised 1.25-1.3 x for all three. One was a runaway its own base
    commit already documents -- "two kills at about 101 GB at 8 and at 2 cores... the growth
    survives at about 4 GB a minute" -- so a 35 GiB limit buys about two minutes and another
    dead job. The lane had already removed an automatic doubled retry from its tooling for
    the same reason (#1780): growing a runaway's limit buys a bigger kill.

    History and this run's series are independent checks. A sustained climb must withhold
    the multiplier even for shell-offload jobs with no size-index history. Series heights
    include sandbox memory under gVisor, so only their shape and rate may inform advice.
    """

    GIB = 1024 ** 3

    def advice(self, **found: object) -> str:
        return WATCH.memory_advice(found)

    def test_a_recent_peak_above_this_limit_warns_against_sizing_from_this_peak(self) -> None:
        text = self.advice(largest_process_bytes=26 * self.GIB,
                           memory_limit_bytes=28 * self.GIB,
                           memory_peak_bytes=27 * self.GIB,
                           process_count=1,
                           recent_memory_peaks=[101 * self.GIB])
        self.assertIn("RAISE MEMORY", text)
        self.assertIn("101", text)
        self.assertIn("is what it was given", text)
        self.assertIn("growth is bounded", text)

    def test_a_bounded_history_leaves_the_advice_alone(self) -> None:
        """Every earlier run fitting inside this limit is the ordinary case: size and move on."""
        text = self.advice(largest_process_bytes=26 * self.GIB,
                           memory_limit_bytes=28 * self.GIB,
                           memory_peak_bytes=27 * self.GIB,
                           process_count=1,
                           recent_memory_peaks=[12 * self.GIB, 19 * self.GIB])
        self.assertIn("RAISE MEMORY", text)
        self.assertNotIn("is what it was given", text)

    def test_no_history_leaves_the_advice_alone(self) -> None:
        text = self.advice(largest_process_bytes=26 * self.GIB,
                           memory_limit_bytes=28 * self.GIB,
                           memory_peak_bytes=27 * self.GIB, process_count=1)
        self.assertIn("RAISE MEMORY", text)
        self.assertNotIn("is what it was given", text)

    def test_the_serialise_answer_is_not_overridden_by_it(self) -> None:
        """Too many processes at once is still too many, whatever an earlier run peaked at."""
        text = self.advice(largest_process_bytes=4 * self.GIB,
                           memory_limit_bytes=32 * self.GIB,
                           memory_peak_bytes=31 * self.GIB, process_count=8,
                           recent_memory_peaks=[101 * self.GIB])
        self.assertIn("SERIALISE", text)
        self.assertNotIn("is what it was given", text)

    def test_it_reaches_the_weaker_cores_path_too(self) -> None:
        """The one-process-alone branch is exactly where a runaway lands, so it must warn."""
        text = self.advice(memory_limit_bytes=28 * self.GIB,
                           memory_peak_bytes=27 * self.GIB,
                           cpu_last_cores=1.1, cpu_limit_cores=11,
                           recent_memory_peaks=[101 * self.GIB])
        self.assertIn("RAISE MEMORY", text)
        self.assertIn("is what it was given", text)

    def series_text(self, values, *, seconds=None, container="job") -> str:
        start = datetime(2026, 11, 5, 19, 31, tzinfo=timezone.utc)
        seconds = seconds if seconds is not None else [60 * i for i in range(len(values))]
        lines = ["test-pod  window", f"  container {container}: {len(values)} samples",
                 "    peak 999.00 GiB, first reached at an irrelevant summary time"]
        for second, value in zip(seconds, values):
            stamp = (start + timedelta(seconds=second)).isoformat().replace("+00:00", "Z")
            lines.append(f"    {stamp}  {value:.2f} GiB  ####")
        return "\n".join(lines)

    def with_series(self, text, **overrides):
        found = dict(memory_limit_bytes=64 * GIB, memory_limit_text="64Gi",
                     memory_peak_bytes=63 * GIB, largest_process_bytes=60 * GIB,
                     pod="test-pod", recent_memory_peaks=[])
        found.update(overrides)
        with patch.object(WATCH, "run_text", return_value=text):
            found["memory_shape"] = WATCH.read_memory_shape(found)
        return found, WATCH.memory_advice(found)

    def test_a_climb_without_history_replaces_the_multiplier_with_time_bought(self) -> None:
        found, text = self.with_series(self.series_text([2 + 1.8 * i for i in range(13)]))
        self.assertEqual(found["memory_shape"]["status"], "unbounded")
        self.assertIn("1.8 GiB a minute", text)
        self.assertIn("80Gi buys about 9 more minutes", text)
        self.assertIn("128Gi buys about 36 more minutes", text)
        self.assertIn("any limit you pick is a date, not a fix", text)
        self.assertNotIn("--memory", text)
        self.assertNotIn("1.3 x", text)
        self.assertNotIn("RAISE MEMORY", text)

    def test_bounded_history_cannot_overrule_this_runs_climb(self) -> None:
        _, text = self.with_series(self.series_text([2 + i for i in range(13)]),
                                   recent_memory_peaks=[12 * GIB, 19 * GIB])
        self.assertIn("no plateau", text)
        self.assertNotIn("--memory", text)

    def test_a_small_dip_and_one_missed_sample_do_not_hide_the_climb(self) -> None:
        seconds = [0, 60, 120, 180, 240, 360, 420, 480, 540, 600, 660, 720]
        values = [2, 3, 4, 3.95, 6, 8, 9, 10, 11, 12, 13, 14]
        _, text = self.with_series(self.series_text(values, seconds=seconds))
        self.assertIn("no plateau", text)
        self.assertNotIn("--memory", text)

    def test_a_plateau_is_checked_and_keeps_sizing_from_the_farm_peak(self) -> None:
        _, text = self.with_series(self.series_text([2, 4, 6, 8, 10] + [10] * 9))
        self.assertIn("--memory 82Gi", text)  # 1.3 x the farm's 63 GiB, not the series
        self.assertIn("Series checked", text)
        self.assertNotIn("shape unknown", text)
        self.assertIn("short spike", text)

    def test_a_real_fall_is_not_a_monotonic_climb(self) -> None:
        _, text = self.with_series(self.series_text([2, 3, 4, 3.8] + list(range(6, 16))))
        self.assertIn("--memory 82Gi", text)
        self.assertIn("Series checked", text)

    def test_a_series_above_the_limit_never_becomes_a_size(self) -> None:
        for values, expected_size in (
                ([2.38 + 1.8 * i for i in range(63)], None),
                ([70, 90, 113.52] + [113.52] * 11, "--memory 82Gi")):
            with self.subTest(values=values[:3]):
                found, text = self.with_series(self.series_text(values))
                self.assertEqual(found["memory_peak_bytes"], 63 * GIB)
                self.assertNotIn("--memory 148Gi", text)  # 1.3 x 113.52 is NOT a size
                self.assertNotIn("--memory 1299Gi", text)  # nor the helper's peak summary
                if expected_size:
                    self.assertIn(expected_size, text)
                else:
                    self.assertNotIn("--memory", text)

    def test_without_a_farm_peak_the_series_cannot_supply_one(self) -> None:
        _, text = self.with_series(self.series_text([113.52] * 13), memory_peak_bytes=None)
        self.assertIn("no memory peak was recorded", text)
        self.assertNotIn("--memory", text)

    def test_no_pod_no_points_and_an_api_refusal_all_say_shape_unknown(self) -> None:
        for series, overrides, reason in (
                (None, {"pod": None}, "no pod name"),
                ("test-pod  window\n  container job: 0 samples\n", {}, "no points"),
                (None, {}, "could not be read")):
            with self.subTest(reason=reason):
                _, text = self.with_series(series, **overrides)
                self.assertIn("shape unknown", text)
                self.assertIn(reason, text)
                self.assertNotIn("Series checked", text)

    def test_short_gapped_or_stale_series_are_unknown_not_bounded(self) -> None:
        cases = [
            (self.series_text([2, 3, 4]), {}),
            (self.series_text(list(range(13)), seconds=[180 * i for i in range(13)]), {}),
            (self.series_text(list(range(13))), {"finished_at": "2026-11-05T20:00:00Z"}),
        ]
        for series, overrides in cases:
            with self.subTest(overrides=overrides, series=series[:60]):
                _, text = self.with_series(series, **overrides)
                self.assertIn("shape unknown", text)
                self.assertNotIn("Series checked", text)

    def test_a_sidecars_climb_is_not_the_jobs_shape(self) -> None:
        flat = self.series_text([10] * 13)
        climbing_sidecar = self.series_text(list(range(13)), container="upload")
        _, text = self.with_series(flat + "\n" + climbing_sidecar)
        self.assertIn("--memory 82Gi", text)
        self.assertIn("Series checked", text)

    def test_a_single_shell_container_needs_no_farm_job_container_name(self) -> None:
        _, text = self.with_series(self.series_text(list(range(13)), container="shell"))
        self.assertIn("no plateau", text)
        self.assertNotIn("--memory", text)

    def test_collect_reuses_the_series_helper_for_the_failed_pod(self) -> None:
        job = job_record(memory="64Gi", disk="60Gi")
        result = result_record(63 * GIB, GIB, oom_kill=1)
        result["failed_pods"][0]["job"]["terminated"]["finishedAt"] = "2026-11-05T19:44:00Z"
        calls = []

        def commands(args, timeout=90):
            calls.append(args)
            if args[0] == "gcloud":  # the job.log read, which collect makes for a memory kill
                self.assertEqual(args[1:3], ["storage", "cat"])
                self.assertTrue(args[3].endswith("/out/ossv/test-run/job.log"))
                return "all good\n"
            self.assertEqual(Path(args[0]).name, "pod-memory-series")
            self.assertEqual(args[1], result["failed_pods"][0]["name"])
            self.assertEqual(args[-1], "2026-11-05T19:44:00Z")
            return self.series_text([2 + i for i in range(13)])

        with patch.object(WATCH, "read_object", side_effect=[job, result]), \
                patch.object(WATCH, "read_pod", return_value=None), \
                patch.object(WATCH, "run_text", side_effect=commands):
            found, _ = WATCH.collect("farm/test-run", "test-context")
        self.assertEqual([Path(args[0]).name for args in calls], ["pod-memory-series", "gcloud"])
        self.assertIsNone(found["script_error"])
        self.assertNotIn("--memory", WATCH.message(found))

    def test_a_live_pods_name_and_start_are_used_without_a_failed_pod_record(self) -> None:
        live = pod(memory="64Gi", disk="60Gi", name="pi-shjob-test")
        live["status"]["containerStatuses"][-1]["state"]["terminated"].update(
            startedAt="2026-11-05T19:31:00Z", finishedAt="2026-11-05T19:44:00Z")
        found = WATCH.evidence(job_record(memory="64Gi"),
                               result_record(63 * GIB, GIB, failed_pods=[]), live)
        self.assertEqual(found["pod"], "pi-shjob-test")
        with patch.object(WATCH, "run_text", return_value=self.series_text(list(range(13)))) as run:
            found["memory_shape"] = WATCH.read_memory_shape(found)
        self.assertEqual(run.call_args.args[0][1:],
                         ["pi-shjob-test", "2026-11-05T19:31:00Z", "2026-11-05T19:44:00Z"])
        self.assertNotIn("--memory", WATCH.memory_advice(found))

    def test_the_real_series_helper_handles_a_climb_and_a_refused_api(self) -> None:
        start = datetime(2026, 11, 5, 19, 31, tzinfo=timezone.utc)
        body = {"timeSeries": [{
            "resource": {"labels": {"container_name": "shell"}},
            "points": [{"interval": {"endTime": (start + timedelta(minutes=i)).isoformat()},
                        "value": {"int64Value": str(int((2 + 1.8 * i) * GIB))}}
                       for i in range(13)],
        }]}
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory)
            (tools / "gcloud").write_text("#!/bin/sh\nprintf 'test-token\\n'\n")
            (tools / "gcloud").chmod(0o755)
            for curl_body, climbing in (
                    ("printf '%s' '" + json.dumps(body) + "'", True),
                    ("printf '%s' '{\"timeSeries\": []}'", False),
                    ("exit 22", False)):
                with self.subTest(climbing=climbing, curl=curl_body[:40]):
                    (tools / "curl").write_text("#!/bin/sh\n" + curl_body + "\n")
                    (tools / "curl").chmod(0o755)
                    found = dict(pod="pi-shjob-test", memory_limit_bytes=64 * GIB,
                                 memory_peak_bytes=63 * GIB,
                                 finished_at="2026-11-05T19:43:00Z")
                    with patch.dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"]):
                        found["memory_shape"] = WATCH.read_memory_shape(found)
                    text = WATCH.memory_advice(found)
                    if climbing:
                        self.assertIn("1.8 GiB a minute", text)
                        self.assertNotIn("--memory", text)
                    else:
                        self.assertIn("shape unknown", text)
                        self.assertNotIn("Series checked", text)


class TheOwnerLineNamesALaneAndThenTheAgent(unittest.TestCase):
    """The owner label is a session id, so it routes to a lane of several agents.

    On 2026-11-06 the curl lane got a notice labelled with its session and had to broadcast to
    five live agents to find whose job it was; the kind on the job already ruled four of them
    out. The line has to say both things, or a correct notice still costs a round.
    """

    def test_it_says_the_owner_is_a_session_and_gives_the_agent_its_kind(self) -> None:
        line = WATCH.owner_line({"owner": "01a19324", "kind": "rocq-kit", "unit": "curl__fuzz_bufq"})
        self.assertIn("01a19324", line)
        self.assertIn("SESSION, NOT AN AGENT", line)
        self.assertIn("ossv-kind=rocq-kit", line)
        self.assertIn("curl__fuzz_bufq", line)

    def test_without_a_kind_or_unit_it_claims_no_way_to_tell_the_agents_apart(self) -> None:
        line = WATCH.owner_line({"owner": "01a19324"})
        self.assertIn("SESSION, NOT AN AGENT", line)
        self.assertIn("nothing here tells one agent from another", line)
        self.assertNotIn("rules itself out", line)

    def test_an_unlabelled_job_is_not_called_a_session(self) -> None:
        line = WATCH.owner_line({"kind": "rocq-kit", "unit": "curl__fuzz_bufq"})
        self.assertIn("OWNER NOT LABELLED", line)
        self.assertNotIn("IS A SESSION", line)

    def test_the_whole_notice_carries_it_under_the_headline(self) -> None:
        found = {"run": "farm/1af893", "unit": "curl__fuzz_bufq", "kind": "rocq-kit",
                 "owner": "01a19324", "reason": "OOMKilled", "exit_code": 137,
                 "finished_at": "2026-11-06T02:04:25Z", "memory_peak_bytes": 33 * GIB,
                 "memory_limit_bytes": 32 * GIB, "disk_peak_bytes": GIB,
                 "disk_request_bytes": 50 * GIB}
        self.assertEqual(WATCH.message(found).splitlines()[1], WATCH.owner_line(found))


class AKillWhoseLogEndsInAnErrorIsNotToldToBuyMemory(unittest.TestCase):
    """A Rocq run that fails in a large context costs about twice the memory of one that

    compiles, so a limit sized for the working proof OOM-kills the broken one and it arrives
    here as a memory kill. The curl lane measured it on 2026-11-06: 4.84 GiB clean against
    10.96 and 10.72 GiB broken, same unit, same tree, same limit. Advising more memory there
    buys memory for a bug and spends another farm slot.
    """

    def read(self, text: str) -> str | None:
        with patch.object(WATCH, "run_text", return_value=text):
            return WATCH.read_script_error("gs://b", "run")

    def test_an_error_in_the_last_lines_is_found_and_quoted(self) -> None:
        log = "\n".join(["compiling"] * 500 + ["Error:", "Syntax error: expected after [tactic]."])
        self.assertEqual(self.read(log), "Error:\nSyntax error: expected after [tactic].")

    def test_a_clean_log_yields_nothing(self) -> None:
        self.assertIsNone(self.read("\n".join(["compiling"] * 500 + ["vo 242"])))

    def test_an_error_further_back_than_forty_lines_is_still_found(self) -> None:
        """One of the two measured kills had its error 50 lines from the end, which is why
        the window is 200 and not the 40 first proposed."""
        log = "\n".join(["start", "Error: In environment"] + ["context line"] * 49)
        self.assertIsNotNone(self.read(log))

    def test_an_error_older_than_the_window_is_not_claimed(self) -> None:
        log = "\n".join(["Error: long ago"] + ["compiling"] * WATCH.LOG_TAIL_LINES)
        self.assertIsNone(self.read(log))

    def test_an_unreadable_log_yields_nothing_rather_than_an_exception(self) -> None:
        self.assertIsNone(self.read(""))

    def test_the_advice_withholds_the_raise_and_shows_the_error(self) -> None:
        found = {"memory_peak_bytes": 11 * GIB, "memory_limit_bytes": 10 * GIB,
                 "script_error": "Error:\nSyntax error."}
        advice = WATCH.memory_advice(found)
        self.assertIn("DO NOT RAISE MEMORY YET", advice)
        self.assertNotIn("RAISE MEMORY to", advice)
        self.assertIn("Syntax error.", advice)

    def test_without_an_error_the_sizing_advice_is_unchanged(self) -> None:
        found = {"memory_peak_bytes": 11 * GIB, "memory_limit_bytes": 10 * GIB,
                 "script_error": None, "cpu_last_cores": 1.0, "cpu_limit_cores": 8}
        self.assertNotIn("DO NOT RAISE MEMORY YET", WATCH.memory_advice(found))


class ASecondKillShowsBothPointsAndReadsNeither(unittest.TestCase):
    """A retry at a higher limit is a second measurement of the same work.

    Whether two peaks mean a fixed appetite underestimated twice or something that grows
    until stopped is exactly what the pair is for, and one pair is not enough to decide it
    in code: the curl lane read a single point as a heavy file on 2026-11-06 and retracted
    it when the second point arrived. So the notice shows both and concludes nothing.
    """

    def test_both_limits_and_both_peaks_appear(self) -> None:
        found = {"memory_limit_text": "40960Mi", "memory_limit_bytes": 40 * GIB,
                 "memory_peak_bytes": int(41.36 * GIB),
                 "retry_of": {"run": "farm/1af893", "limit_text": "32Gi",
                              "limit_bytes": 32 * GIB, "peak_bytes": int(33.28 * GIB)}}
        line = WATCH.retry_line(found)
        for part in ("farm/1af893", "32Gi", "33.28 GiB", "40.00 GiB", "41.36 GiB"):
            self.assertIn(part, line)

    def test_it_draws_no_conclusion(self) -> None:
        found = {"memory_limit_bytes": 40 * GIB, "memory_peak_bytes": 41 * GIB,
                 "retry_of": {"run": "farm/x", "limit_bytes": 32 * GIB, "peak_bytes": 33 * GIB}}
        line = WATCH.retry_line(found)
        self.assertIn("left here unread", line)
        for verdict in ("RAISE MEMORY", "SERIALISE", "unbounded"):
            self.assertNotIn(verdict, line)

    def test_a_first_kill_carries_no_such_line(self) -> None:
        self.assertIsNone(WATCH.retry_line({"memory_peak_bytes": GIB}))

    def test_a_parent_whose_records_are_gone_is_not_invented(self) -> None:
        with patch.object(WATCH, "read_object", return_value=None):
            self.assertIsNone(WATCH.read_retry_of("gs://b", "farm/gone"))

    def test_a_parent_with_a_job_but_no_result_shows_the_limit_and_an_unknown_peak(self) -> None:
        with patch.object(WATCH, "read_object", side_effect=[{"memory": "32Gi"}, None]):
            of = WATCH.read_retry_of("gs://b", "farm/half")
        self.assertEqual(of["limit_bytes"], 32 * GIB)
        self.assertIsNone(of["peak_bytes"])
        self.assertIn("unknown", WATCH.retry_line({"retry_of": of, "memory_peak_bytes": GIB}))


class ASampledPeakIsLabelledNotRefused(unittest.TestCase):
    """A gVisor sampled peak is named, and the sizing is left in place.

    Two claims against the sampled figure were made on 2026-11-06 and both were retracted by
    the lanes that made them: a 1.5-to-3.5x gap against the kernel's peak, and "a sampled peak
    exceeded its cgroup while the kernel never acted" -- the kernel had acted, in a third
    record neither lane had read. The two pairs measured both ways agree within 1-2%, so
    refusing to size would withhold advice from most gVisor kills on a claim that does not
    hold. This watch refused for 45 minutes; these tests pin the reversal.
    """

    def found(self, kind: str) -> dict:
        return {"memory_peak_bytes": 33 * GIB, "memory_limit_bytes": 32 * GIB,
                "memory_peak_kind": kind, "cpu_last_cores": 1.0, "cpu_limit_cores": 8}

    def test_a_sampled_peak_is_labelled_and_still_sized(self) -> None:
        advice = WATCH.memory_advice(self.found("sampled"))
        self.assertIn("SAMPLED figure", advice)
        self.assertIn("RAISE MEMORY to", advice)

    def test_the_label_names_the_retractions_rather_than_a_factor(self) -> None:
        """A factor in a notice gets used as one, and none is established."""
        advice = WATCH.memory_advice(self.found("sampled"))
        self.assertIn("RETRACTED", advice)
        self.assertIn("none is established", advice)

    def test_a_kernel_peak_carries_no_label(self) -> None:
        self.assertNotIn("SAMPLED figure", WATCH.memory_advice(self.found("kernel")))

    def test_an_unrecorded_kind_carries_no_label(self) -> None:
        self.assertNotIn("SAMPLED figure", WATCH.memory_advice(self.found(None)))

    def test_a_kernel_peak_is_sized_from_as_before(self) -> None:
        self.assertNotIn("DO NOT SIZE FROM THIS PEAK", WATCH.memory_advice(self.found("kernel")))

    def test_an_unrecorded_kind_is_sized_from_rather_than_refused(self) -> None:
        """Only `sampled` is refused: an older run with no kind recorded is not gVisor evidence."""
        self.assertNotIn("DO NOT SIZE FROM THIS PEAK", WATCH.memory_advice(self.found(None)))

    def test_it_claims_no_mechanism_the_evidence_withdrew(self) -> None:
        """"The sampled value tracks the limit" was offered and withdrawn the same hour, because
        one run read 29.13 GiB against a non-binding 112Gi limit. The advice must not assert it."""
        advice = WATCH.memory_advice(self.found("sampled"))
        for claim in ("tracks the limit", "climbs toward the limit", "grows until"):
            self.assertNotIn(claim, advice)

    def test_a_script_error_still_takes_precedence(self) -> None:
        found = self.found("sampled")
        found["script_error"] = "Error:\nSyntax error."
        self.assertIn("DO NOT RAISE MEMORY YET", WATCH.memory_advice(found))


if __name__ == "__main__":
    unittest.main()
