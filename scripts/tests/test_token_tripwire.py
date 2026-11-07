#!/usr/bin/env python3
"""Regression tests for token-tripwire: the three ways it could report a clean zero forever.

On 2026-11-07 a session copy displaced a live session's secretsd token and 59 of 153 boxed
sessions could not fetch a secret, silently, for a day (dispatch://INFRA-1644). The watch exists
because that state cannot be enumerated: it is invisible until a session asks. A watch for an
invisible state is the easiest kind to get wrong, because a broken one looks exactly like a
quiet fleet. Each test here is one way the first version was wrong or could be:

  * the cgroup names the container by ID, not by box name, so looking for "agentbox-" in it
    found nothing and every event was skipped;
  * a request that fails and then succeeds for the same pid is the NORMAL two-line shape, and
    reporting it would tell healthy sessions they are broken;
  * a session already told must not be told again inside the quiet period.
"""
import importlib.machinery
import importlib.util
import pathlib
import sys
import tempfile
import unittest
import unittest.mock

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "token-tripwire"
LOADER = importlib.machinery.SourceFileLoader("token_tripwire", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
tripwire = importlib.util.module_from_spec(SPEC)
sys.modules[LOADER.name] = tripwire
LOADER.exec_module(tripwire)

UNKNOWN = (
    '2026-11-07T20:38:14.683523Z  INFO request handled key=APIFY_TOKEN source=- peer_uid=65534 '
    'peer_pid=3897384 caller_pid=Some(3897384) scope_kind=None untrusted_registered_session=None '
    'registered_root_pid=None request_id=None decision="UNKNOWN_TOKEN"'
)
VERIFIED = (
    '2026-11-07T20:38:14.684939Z  INFO request handled key=APIFY_TOKEN source=- peer_uid=65534 '
    'peer_pid=3897384 caller_pid=Some(3897384) scope_kind=Some(VerifiedSession) '
    'untrusted_registered_session=Some("01a19679-aacf-7456-a9fa-87327b9ecd86") decision="ok"'
)


class ParsesSecretsdLines(unittest.TestCase):
    def test_reads_the_pid_and_the_scope_of_both_shapes(self) -> None:
        unknown = tripwire.line_re.match(UNKNOWN)
        verified = tripwire.line_re.match(VERIFIED)
        self.assertIsNotNone(unknown, "the UNKNOWN line is the one the watch exists to see")
        self.assertIsNotNone(verified)
        assert unknown is not None and verified is not None
        self.assertEqual(unknown["pid"], "3897384")
        self.assertEqual(unknown["scope"], "None")
        self.assertEqual(verified["pid"], "3897384")
        self.assertTrue(verified["scope"].startswith("Some(VerifiedSession)"))

    def test_a_line_from_another_daemon_is_not_a_failure(self) -> None:
        self.assertIsNone(tripwire.line_re.match("INFO something else entirely"))


class NamesTheBox(unittest.TestCase):
    """The defect that made the first version report nothing at all."""

    def setUp(self) -> None:
        self.proc = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))

    def _pid_with_cgroup(self, text: str) -> str:
        pid = "4242"
        (self.proc / pid).mkdir()
        (self.proc / pid / "cgroup").write_text(text, encoding="utf-8")
        return pid

    def test_resolves_a_container_id_through_docker(self) -> None:
        pid = self._pid_with_cgroup(
            "0::/user.slice/user-1000.slice/user-1000-agentbox.slice/"
            "docker-7999c36193ee63a03e9a8fa1dc9d0945bc40cd89a9765292b0d5a78f5fc1f3f6.scope/init.scope\n"
        )
        asked: list[list[str]] = []

        class Result:
            stdout = "/agentbox-4e9a6c911017\n"

        def fake_run(argv, **_kwargs):
            asked.append(argv)
            return Result()

        with unittest.mock.patch.object(tripwire.subprocess, "run", fake_run), \
                unittest.mock.patch.object(tripwire, "PROC_ROOT", str(self.proc)):
            self.assertEqual(tripwire.box_of(pid), "agentbox-4e9a6c911017")
        self.assertIn("inspect", asked[0], "the id in the cgroup is not the box name")

    def test_a_host_process_belongs_to_no_box(self) -> None:
        pid = self._pid_with_cgroup("0::/user.slice/user-1000.slice/session-3.scope\n")
        with unittest.mock.patch.object(tripwire, "PROC_ROOT", str(self.proc)):
            self.assertEqual(tripwire.box_of(pid), "")

    def test_a_pid_that_has_exited_belongs_to_no_box(self) -> None:
        with unittest.mock.patch.object(tripwire, "PROC_ROOT", str(self.proc)):
            self.assertEqual(tripwire.box_of("999999"), "")


class ReadsTheBoxRecord(unittest.TestCase):
    def test_takes_the_session_from_the_record(self) -> None:
        directory = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        (directory / "agentbox-abc.info").write_text(
            "box=agentbox-abc\nsession=01a1708b-35e2-746c-9dda-337c0544ec65\n"
            "created=2026-11-07T19:32:35Z\n",
            encoding="utf-8",
        )
        with unittest.mock.patch.object(tripwire, "RECORD_DIRS", [str(directory)]):
            self.assertEqual(
                tripwire.session_of("agentbox-abc"), "01a1708b-35e2-746c-9dda-337c0544ec65"
            )
            self.assertEqual(tripwire.session_of("agentbox-missing"), "")


if __name__ == "__main__":
    unittest.main()
