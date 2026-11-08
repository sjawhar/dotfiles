#!/usr/bin/env python3
"""A holder somebody stopped on purpose is not an outage.

On 2026-11-08 three sessions were stopped at their own leads' request between 00:36:20Z and
00:36:38Z, each through `agentbox finish`, which writes `auto_restart=no` and an `owner_note`
into the session's restart-registry entry before the process dies. The watch went on paging the
chief of staff about their roles -- bet64-eta-meta, infra-utilization and ossv-schedfix -- at
00:49:59Z, thirteen minutes later, while the roles themselves were already unheld on the hub.
"""

import unittest
from unittest.mock import patch

from test_envoy_role_holder_watch_tuning import Box, HOLDER, HOST, OTHER, RunEnvoy, WATCH


class FinishedHolderTest(Box):
    def setUp(self):
        super().setUp()
        # Outside self.dir on purpose: writing into the lane's own directory makes the watch's
        # directory rule see fresh activity and quiet the alert, so the control cases would
        # pass for the wrong reason.
        self.registry = self.root / "restart-registry"
        self.registry.mkdir(parents=True, exist_ok=True)
        self.enterContext(patch.object(WATCH, "RESTART_REGISTRY", str(self.registry)))
        # The fixture's holder that nobody can see, which is the condition this watch alerts
        # on. Each test below changes only whether a deliberate stop is recorded for it.
        self.write_state(
            {"lead-lane": self.role(topics=["notifications.team.lane.>"])},
            {f"lead-lane|{HOLDER}": {"opened_at": self.now - 700, "dir": str(self.dir)}},
        )
        RunEnvoy.sessions_list = [
            {"session_id": OTHER, "dir": str(self.dir), "roles": ["stream-lead-lane"],
             "host": HOST, "topics": ["notifications.team.lane.>"]},
        ]

    def entry(self, body: str) -> None:
        (self.registry / f"{HOLDER}.entry").write_text(body)

    def test_a_holder_finished_on_purpose_is_skipped_and_forgotten(self):
        self.entry(
            f"session={HOLDER}\nrole=lead-lane\nauto_restart=no\n"
            "owner_note=Finished deliberately at 2026-11-08T00:36:20Z: the lane's own lead asked\n"
        )
        lines = self.period()
        self.assertEqual(RunEnvoy.sent, [], "a deliberate stop must alert nobody")
        self.assertNotIn("lead-lane", self.data()["roles"], "the role must be dropped from state")
        self.assertTrue(
            any("stopped on purpose" in line and "the lane's own lead asked" in line for line in lines),
            f"the reason must be logged, got {lines}",
        )

    def test_a_holder_with_no_registry_entry_is_still_judged(self):
        lines = self.period()
        self.assertTrue(RunEnvoy.sent, f"a holder nobody stopped must still be alerted on, got {lines}")

    def test_an_entry_without_auto_restart_no_is_still_judged(self):
        self.entry(f"session={HOLDER}\nrole=lead-lane\nauto_restart=yes\nowner_note=restarted\n")
        lines = self.period()
        self.assertTrue(
            RunEnvoy.sent,
            f"an entry that records no deliberate stop must not silence the watch, got {lines}",
        )

    def test_the_helper_reads_nothing_for_a_bad_id(self):
        self.assertEqual(WATCH.deliberately_finished("../../etc/passwd"), "")
        self.assertEqual(WATCH.deliberately_finished(""), "")


if __name__ == "__main__":
    unittest.main()
