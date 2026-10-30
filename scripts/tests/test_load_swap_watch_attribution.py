#!/usr/bin/env python3
"""Regression tests for who load-swap-watch says a process belongs to.

On 2026-10-30 at 20:17:23Z its CPU alert for ossv-agent-repo named 39 python3 processes as
session 01a14b36, which is VERIFY and runs on sami-rocqperf. That id reaches those processes by
inheritance: it is in the environment of whoever started the box's supervisor over ssh, and in
the `--resume` path of the lane's omp, whose session directory keeps the id the session had
when it was first created. The live owner was the repo lane 01a14b84, which only Envoy's list
of live sessions and the lane's own files can say.

Nothing here reads a real /proc: the process tree, the environments and the Envoy answer are
all fabricated.
"""

import argparse
import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "load-swap-watch"
LOADER = importlib.machinery.SourceFileLoader("load_swap_watch_attribution", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

VERIFY = "01a14b36-227b-7141-acdb-b5397251a0b1"
REPO_LANE = "01a14b84-6cd3-71af-918c-db534cd8d762"
RESUME = (
    "omp --config /home/sami/.ossv-agent/omp-vm.yml --resume"
    f" /home/sami/.omp/agent/sessions/-src-oss-verifier/2026-10-17T18-53-21-403Z_{VERIFY}/ossv-repo.jsonl"
)


class FakeTree(unittest.TestCase):
    """A fabricated /proc: parents, comms, command lines, environments and working directories."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        # ossv-agent-repo as it stood at 20:17Z: python3 under bash under the lane's omp, which
        # was resumed from a session directory named with the id the session had in October.
        self.parent = {2942803: 2942801, 2942801: 2342513, 2342513: 2342511, 2342511: 2342510, 2342510: 1}
        self.comm = {2942803: "python3", 2942801: "bash", 2342513: "omp", 2342511: "bash", 2342510: "tmux: server"}
        self.cmdline = {
            2942803: "python3 -B /home/sami/work/tmp/neartwin/memwatch.py",
            2942801: "bash -c ulimit -v 8000000",
            2342513: RESUME,
            2342511: "-bash",
            2342510: "tmux new-session",
        }
        # The poisoned variable: every descendant of the ssh-started supervisor carries it.
        self.environ = {pid: {"OMP_SESSION_ID": VERIFY} for pid in self.parent}
        self.cwd = {
            2942803: "/home/sami/work/tmp/neartwin/fixed-tools/tools",
            2942801: "/home/sami/work/tmp/neartwin/fixed-tools/tools",
            2342513: "/home/sami/src/oss-verifier",
            2342511: "/home/sami/src/oss-verifier",
            2342510: "/home/sami_theorem_dev",
        }
        for name, replacement in (
            ("ppid_of", lambda pid: self.parent.get(pid, 1)),
            ("comm_of", lambda pid: self.comm.get(pid, "")),
            ("cmdline_of", lambda pid: self.cmdline.get(pid, "")),
            ("environ_of", lambda pid: self.environ.get(pid, {})),
            ("_cwd_of", lambda pid: self.cwd.get(pid, "")),
            ("session_from_fds", lambda pid, limit=128: ""),
            ("age_of", lambda pid: 0.0),
        ):
            original = getattr(WATCH, name)
            setattr(WATCH, name, replacement)
            self.addCleanup(lambda n=name, o=original: setattr(WATCH, n, o))
        self.addCleanup(self.reset_lookups)
        self.reset_lookups()

    def reset_lookups(self) -> None:
        WATCH.LIVE_SESSIONS.clear()
        WATCH.LIVE_READ = False
        WATCH.PANES.clear()
        WATCH.PANES_READ = False

    def live(self, *session_ids: str) -> None:
        WATCH.LIVE_SESSIONS.clear()
        WATCH.LIVE_SESSIONS.update({session_id: {"session_id": session_id} for session_id in session_ids})
        WATCH.LIVE_READ = True


class SessionClaimTest(FakeTree):
    def test_the_environment_is_the_last_source_not_the_first(self) -> None:
        """The resume path is about this process; the environment is about its history."""
        claim, source = WATCH.session_claim(2942803)
        self.assertEqual(claim, VERIFY)
        self.assertEqual(source, "cmdline")

    def test_the_environment_is_used_when_nothing_else_names_a_session(self) -> None:
        self.cmdline[2342513] = "omp --config /home/sami/.ossv-agent/omp-vm.yml"
        claim, source = WATCH.session_claim(2942803)
        self.assertEqual(claim, VERIFY)
        self.assertEqual(source, "environ")

    def test_the_lane_workers_own_session_file_wins_over_both(self) -> None:
        directory = self.root / "home/sami/work/.ossv-workers/c-asan"
        directory.mkdir(parents=True)
        (directory / "session").write_text(REPO_LANE + "\n")
        self.cmdline[2342513] = f"omp --config x.yml @{directory}/message-resume-0.md"
        claim, source = WATCH.session_claim(2942803)
        self.assertEqual(claim, REPO_LANE)
        self.assertEqual(source, "worker")

    def test_an_unconfirmed_worker_session_is_not_claimed(self) -> None:
        directory = self.root / "home/sami/work/.ossv-workers/c-asan"
        directory.mkdir(parents=True)
        (directory / "session").write_text(VERIFY + "\n")
        (directory / "session_unconfirmed").write_text("")
        self.cmdline[2942801] = f"bash @{directory}/message-resume-0.md"
        claim, source = WATCH.session_claim(2942803)
        self.assertEqual(claim, VERIFY)
        self.assertEqual(source, "cmdline")


class SessionPhraseTest(FakeTree):
    def test_a_claim_that_is_not_live_here_says_so_and_names_the_live_owner(self) -> None:
        """The 20:17:23Z failure, as the alert would read now."""
        self.live(REPO_LANE)
        phrase = WATCH.session_phrase(2942803)
        self.assertIn(f"session {VERIFY}", phrase)
        self.assertIn("not live on this box", phrase)
        self.assertIn("the session file it was resumed from", phrase)
        self.assertIn(f"the live owner on this box is {REPO_LANE}", phrase)

    def test_an_inherited_environment_id_is_named_as_such(self) -> None:
        self.cmdline[2342513] = "omp --config /home/sami/.ossv-agent/omp-vm.yml"
        self.live(REPO_LANE)
        phrase = WATCH.session_phrase(2942803)
        self.assertIn("from the process environment (started it)", phrase)
        self.assertIn("not live on this box", phrase)

    def test_a_live_claim_is_simply_named(self) -> None:
        self.live(VERIFY, REPO_LANE)
        self.assertEqual(WATCH.session_phrase(2942803), f"session {VERIFY}")

    def test_an_envoy_that_did_not_answer_never_calls_a_session_dead(self) -> None:
        """LIVE_READ False means unverified, not 'nothing is live'."""
        self.assertEqual(WATCH.session_phrase(2942803), f"session {VERIFY}")

    def test_several_live_sessions_here_means_no_owner_is_guessed(self) -> None:
        self.live(REPO_LANE, "01a18c6a-ffcd-719e-92a0-31ace0ee6b81")
        phrase = WATCH.session_phrase(2942803)
        self.assertIn("not live on this box", phrase)
        self.assertIn("no live session on this box could be matched", phrase)

    def test_the_live_owner_comes_from_the_worker_file_when_there_is_one(self) -> None:
        directory = self.root / "home/sami/work/.ossv-workers/c-asan"
        directory.mkdir(parents=True)
        (directory / "session").write_text(REPO_LANE + "\n")
        self.cmdline[2942801] = f"bash @{directory}/message-resume-0.md"
        self.live(REPO_LANE, "01a18c6a-ffcd-719e-92a0-31ace0ee6b81")
        self.assertEqual(WATCH.session_phrase(2942803), f"session {REPO_LANE}")


class DescribeTest(FakeTree):
    def test_every_named_process_carries_its_cwd_and_its_pane(self) -> None:
        WATCH.PANES.update({2342511: "agent:bash.0"})
        WATCH.PANES_READ = True
        self.live(REPO_LANE)
        text = WATCH.describe(2942803)
        self.assertIn("cwd /home/sami/work/tmp/neartwin/fixed-tools/tools", text)
        self.assertIn("pane agent:bash.0", text)
        self.assertIn(f"the live owner on this box is {REPO_LANE}", text)

    def test_a_process_under_no_pane_says_so_once_panes_were_read(self) -> None:
        WATCH.PANES_READ = True
        self.assertIn("no tmux pane", WATCH.describe(2942803))

    def test_nothing_about_panes_is_claimed_before_they_are_read(self) -> None:
        self.assertNotIn("tmux pane", WATCH.describe(2942803))


class LiveSessionsTest(FakeTree):
    """The Envoy lookup itself: which rows count as this box, and what a failure does."""

    class Answer:
        def __init__(self, status: int, body) -> None:
            self.status, self.body = status, body

        def request(self, method: str, path: str, body=None):
            assert (method, path) == ("GET", "/v1/sessions")
            return self.status, self.body

    def setUp(self) -> None:
        super().setUp()
        original = WATCH.socket.gethostname
        WATCH.socket.gethostname = lambda: "ossv-agent-repo.us-east1-b.c.autoformalization.internal"
        self.addCleanup(lambda: setattr(WATCH.socket, "gethostname", original))

    def test_only_this_boxs_rows_count(self) -> None:
        WATCH.refresh_live_sessions(
            self.Answer(
                200,
                [
                    {"session_id": REPO_LANE, "host": "ossv-agent-repo.us-east1-b.c.autoformalization.internal"},
                    {"session_id": VERIFY, "host": "sami-rocqperf"},
                    {"session_id": "01a18c6a-ffcd-719e-92a0-31ace0ee6b81", "host": "ossv-agent-open"},
                ],
            )
        )
        self.assertEqual(sorted(WATCH.LIVE_SESSIONS), [REPO_LANE])
        self.assertTrue(WATCH.LIVE_READ)

    def test_a_short_hostname_matches_an_fqdn_row(self) -> None:
        WATCH.socket.gethostname = lambda: "ossv-agent-repo"
        WATCH.refresh_live_sessions(
            self.Answer(200, [{"session_id": REPO_LANE, "host": "ossv-agent-repo.us-east1-b.c.example"}])
        )
        self.assertEqual(sorted(WATCH.LIVE_SESSIONS), [REPO_LANE])

    def test_a_failed_lookup_leaves_attribution_unverified(self) -> None:
        WATCH.refresh_live_sessions(self.Answer(500, {"error": "boom"}))
        self.assertFalse(WATCH.LIVE_READ)
        self.assertEqual(WATCH.session_phrase(2942803), f"session {VERIFY}")


if __name__ == "__main__":
    unittest.main()
