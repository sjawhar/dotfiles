#!/usr/bin/env python3
"""Tests for pulumi-drift-watch: what it sends, to whom, and when it stays quiet.

bootstrap/prod had pending steps in every one of its 7 samples from 2026-10-30 to 2026-11-02
(6, 6, 6, 6, 1, 9, 6), so a pending plan is a standing fact: only a changed set is news. No test
runs git, gcloud or pulumi; each replaces the checkout, the token and the preview.
"""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "pulumi-drift-watch"
LOADER = importlib.machinery.SourceFileLoader("pulumi_drift_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)
SHIPPED_STACKS = list(WATCH.STACKS)
REAL_PREVIEW = WATCH.preview  # setUp replaces WATCH.preview; one test exercises the real one

ROLE = "notifications.role.legion-infra-controller"
INFRA_LEAD = "01a13c57-d826-7242-80fe-5f0670188b11"
URN = "urn:pulumi:prod::agent-infra-bootstrap::"
# The six steps pending at 2026-11-02T09:13Z (sent.bootstrap.prod on sami-rocqperf).
SIX = [
    URN + "gcp:iam/workloadIdentityPoolProvider:WorkloadIdentityPoolProvider::github-ossv",
    URN + "gcp:secretmanager/secretIamMember:SecretIamMember::agent-hosts-github-app-key-builds-linux-jobs",
    URN + "gcp:serviceaccount/account:Account::ossv-outside-index",
    URN + "gcp:serviceaccount/iAMMember:IAMMember::ossv-outside-index-github-publish-outside",
    URN + "gcp:storage/bucketIAMMember:BucketIAMMember::ossv-outside-index-ossv-release-autoformalization",
    URN + "gcp:storage/bucketIAMMember:BucketIAMMember::ossv-publish-index",
]


NO_STATE_TAIL = (
    "error: error listing stacks: could not list bucket: blob (code=NotFound): googleapi: Error 403:"
    " infra-applier@theorem-agent-infra.iam.gserviceaccount.com does not have storage.objects.list"
    " access to the Google Cloud Storage bucket. Permission 'storage.objects.list' denied on resource"
    " '//storage.googleapis.com/projects/_/buckets/theorem-agent-root-bootstrap-pulumi-state' (or it"
    " may not exist)., forbidden\n"
)


def plan(steps, rc=0, tail="Resources:\n    + 6 to create\n", no_state_access=False):
    counts = {"create": len(steps)} if steps else {}
    return {"dir": "bootstrap", "stack": "prod", "rc": rc, "counts": counts, "pending": len(steps),
            "unchanged": 560, "steps": list(steps), "tail": tail,
            "no_state_access": no_state_access}


class DriftWatchTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sent: list[tuple[str, str]] = []
        self.refuse: set[str] = set()
        self.plans: list[dict] = []
        self.start_error: Exception | None = None

        def send(target, text):
            self.sent.append((target, text))
            return target not in self.refuse

        def ensure_checkout():
            if self.start_error:
                raise self.start_error
            return "d35e4a96779d3a6fc6215a70b62b7e7cc07d9c1e"

        patches = {
            "STATE_ROOT": self.root, "CHECKOUT": self.root / "agent-infra", "LOG": self.root / "samples.jsonl",
            "send": send, "ensure_checkout": ensure_checkout, "preview_env": lambda: {},
            "preview": lambda directory, stack, env: self.plans.pop(0),
            # The shipped STACKS is empty (bootstrap/prod is applied by its own CI on merge).
            # These tests are about what one stack's run reports, so they give it one.
            "STACKS": [("bootstrap", "prod")],
        }
        for name, value in patches.items():
            original = getattr(WATCH, name)
            setattr(WATCH, name, value)
            self.addCleanup(setattr, WATCH, name, original)

    def run_watch(self, steps=None, **kw) -> int:
        if steps is not None:
            self.plans.append(plan(steps, **kw))
        with unittest.mock.patch.object(sys, "argv", ["pulumi-drift-watch"]):
            return WATCH.main()

    def report(self, text: str) -> str:
        self.assertLessEqual(len(text), 600, text)
        self.assertIn("Full report: ", text)
        path = Path(text.rsplit("Full report: ", 1)[1].strip())
        self.assertTrue(path.is_file(), path)
        self.assertEqual(path.parent, self.root / "reports")
        return path.read_text()

    def test_the_same_pending_set_is_sent_once(self) -> None:
        self.assertEqual(self.run_watch(SIX), 0)
        self.assertEqual([t for t, _ in self.sent], [ROLE])
        for _ in range(3):
            self.assertEqual(self.run_watch(list(reversed(SIX))), 0)
        self.assertEqual(len(self.sent), 1, "the same six steps, in any order, are not news")

    def test_a_changed_pending_set_is_sent_once_and_names_every_resource(self) -> None:
        self.run_watch(SIX)
        changed = SIX[1:] + [URN + "gcp:projects/iAMMember:IAMMember::legion-worker-project-read"]
        self.run_watch(changed)
        self.run_watch(changed)
        self.assertEqual(len(self.sent), 2)
        target, text = self.sent[1]
        self.assertEqual(target, ROLE)
        self.assertIn("1 added, 1 gone", text)
        for urn in changed:
            self.assertIn(urn.rsplit("::", 1)[1], text, "the controller cannot read the box's report")
        report = self.report(text)
        for urn in changed:
            self.assertIn(urn, report)

    def test_a_clean_run_makes_the_next_pending_set_news_again(self) -> None:
        self.run_watch(SIX)
        self.run_watch([])
        self.run_watch(SIX)
        self.assertEqual(len(self.sent), 2)

    def test_the_infra_lead_hears_it_only_when_nobody_holds_the_controller_role(self) -> None:
        self.refuse = {ROLE}
        self.assertEqual(self.run_watch(SIX), 0)
        self.assertEqual([t for t, _ in self.sent], [ROLE, INFRA_LEAD])
        self.run_watch(SIX)
        self.assertEqual(len(self.sent), 2, "delivered to the fallback is delivered")

    def test_nobody_took_it_exits_1_and_tries_again_next_run(self) -> None:
        self.refuse = {ROLE, INFRA_LEAD}
        self.assertEqual(self.run_watch(SIX), 1)
        self.refuse = set()
        self.assertEqual(self.run_watch(SIX), 0)
        self.assertEqual([t for t, _ in self.sent], [ROLE, INFRA_LEAD, ROLE])

    def test_a_failed_preview_is_said_once_until_it_recovers(self) -> None:
        tail = "error: " + "x" * 5000 + "\nerror: preview failed: 403 on projects/theorem-agent-infra\n"
        self.assertEqual(self.run_watch([], rc=255, tail=tail), 1)
        self.assertEqual(self.run_watch([], rc=255, tail=tail), 1)
        self.assertEqual(len(self.sent), 1)
        text = self.sent[0][1]
        self.assertIn("PULUMI PREVIEW FAILED", text)
        self.assertIn("403 on projects/theorem-agent-infra", text)
        self.assertIn("x" * 4000, self.report(text))

    def test_a_run_that_cannot_start_is_said_once_per_cause(self) -> None:
        self.start_error = RuntimeError("clone failed: " + "fatal: could not read Username " * 40)
        self.assertEqual(self.run_watch(), 1)
        self.assertEqual(self.run_watch(), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("COULD NOT RUN", self.sent[0][1])
        self.report(self.sent[0][1])
        self.start_error = RuntimeError("no Google token as infra-applier@: denied")
        self.assertEqual(self.run_watch(), 1)
        self.assertEqual(len(self.sent), 2, "another cause is a new fact")
        self.start_error = None
        self.run_watch(SIX)
        self.start_error = RuntimeError("no Google token as infra-applier@: denied")
        self.run_watch()
        self.assertEqual(len(self.sent), 4, "a failure after a run that worked is news again")

    def test_every_message_is_at_most_600_chars_and_names_its_report(self) -> None:
        many = [URN + f"gcp:storage/bucketIAMMember:BucketIAMMember::a-very-long-resource-name-{i:03d}-xxxxxxxx"
                for i in range(60)]
        self.run_watch(many)
        self.run_watch([], rc=1, tail="y" * 9000)
        self.start_error = RuntimeError("z" * 3000)
        self.run_watch()
        self.assertEqual(len(self.sent), 3)
        for _target, text in self.sent:
            self.report(text)
        self.assertIn("more", self.sent[0][1])
        self.assertEqual(self.report(self.sent[0][1]).count("a-very-long-resource-name-"), 60)

    def test_reports_older_than_seven_days_are_pruned(self) -> None:
        reports = self.root / "reports"
        reports.mkdir()
        old, recent = reports / "20261020T000000Z-pending.txt", reports / "20261030T000000Z-pending.txt"
        for path, days in ((old, 8), (recent, 6)):
            path.write_text("x")
            then = time.time() - days * 86400
            os.utime(path, (then, then))
        self.run_watch(SIX)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_a_403_on_the_state_bucket_is_recognised_by_the_preview(self) -> None:
        """The real 2026-11-07 text, after agent-infra #1064 moved bootstrap/'s state."""
        completed = unittest.mock.Mock(returncode=1, stdout=NO_STATE_TAIL)
        with unittest.mock.patch.object(WATCH, "run", return_value=completed):
            got = REAL_PREVIEW("bootstrap", "prod", {})
        self.assertTrue(got["no_state_access"])
        self.assertFalse(plan(SIX)["no_state_access"])
        self.assertFalse(plan([], rc=255, tail="error: preview failed")["no_state_access"])

    def test_no_state_reader_is_its_own_status_said_once_and_is_not_a_failed_run(self) -> None:
        for _ in range(2):
            self.assertEqual(self.run_watch([], rc=1, tail=NO_STATE_TAIL, no_state_access=True), 0,
                             "a stack with no reader is not a failed run; the fix is a grant or STACKS")
        self.assertEqual(len(self.sent), 1, "said once, until the state changes")
        target, text = self.sent[0]
        self.assertEqual(target, ROLE)
        self.assertIn("NO READER FOR THIS STACK'S STATE", text)
        self.assertNotIn("PULUMI PREVIEW FAILED", text)
        self.assertNotIn("PENDING", text)
        report = self.report(text)
        self.assertIn("NO READER FOR THIS STACK'S STATE", report)
        self.assertIn("theorem-agent-root-bootstrap-pulumi-state", report)
        self.assertTrue(text.rsplit("Full report: ", 1)[1].strip().endswith("-no-state-reader.txt"))

    def test_a_plain_failure_and_a_missing_state_reader_are_different_news(self) -> None:
        self.assertEqual(self.run_watch([], rc=255, tail="error: preview failed: boom"), 1)
        self.assertEqual(self.run_watch([], rc=1, tail=NO_STATE_TAIL, no_state_access=True), 0)
        self.assertEqual(len(self.sent), 2, "a different fact with a different fix is said again")
        self.assertIn("PULUMI PREVIEW FAILED", self.sent[0][1])
        self.assertIn("NO READER", self.sent[1][1])

    def test_an_empty_stacks_watches_nothing_and_exits_0(self) -> None:
        WATCH.STACKS = []

        def unreachable(*_a, **_k):
            raise AssertionError("an empty STACKS must clone nothing and mint no token")

        with unittest.mock.patch.object(WATCH, "ensure_checkout", unreachable), \
                unittest.mock.patch.object(WATCH, "preview_env", unreachable), \
                unittest.mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out, \
                unittest.mock.patch.object(sys, "argv", ["pulumi-drift-watch"]):
            self.assertEqual(WATCH.main(), 0)
        self.assertIn("watches nothing", out.getvalue())
        self.assertEqual(self.sent, [])

    def test_the_shipped_stacks_is_empty(self) -> None:
        """bootstrap/prod was the last entry; agent-infra's CI applies it on every merge."""
        self.assertEqual(SHIPPED_STACKS, [])


if __name__ == "__main__":
    unittest.main()
