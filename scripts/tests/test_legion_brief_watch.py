#!/usr/bin/env python3
"""scripts/legion-brief-watch against a fixture Dispatch and a stub scripts/envoy.

The watch reads production Dispatch alone: a READY packet counts when it was posted after
--release-time. When the newest LEGION_BRIEF_WATCH_MIN (5) counted packets each carry an
`Outcome:` line right after the READY header, no `Warning: brief-missing` and not the no-brief
placeholder, it sends one notice with `scripts/envoy send --source envoy`, records it in the state
file, and exits 1. The installer refuses a unit still carrying its release-time marker.

Technique: tests/fixtures/dispatch_stub.py serves each test's canned issues and event logs on a
free port (401 for any bearer but the fixture's); a stub scripts/envoy under a temp DOTFILES_DIR
logs every send. PATH is /usr/bin:/bin plus that stub's directory, so no gh and no mise.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import dispatch_stub  # noqa: E402

WATCH = Path(__file__).resolve().parents[1] / "legion-brief-watch"
INSTALLER = Path(__file__).resolve().parents[2] / "installers" / "legion-brief-watch.sh"
RELEASE = "2026-09-10T00:00:00Z"
TOKEN = "fixture-token-5f2c9a"
SHA = "0123456789abcdef0123456789abcdef01234567"

# scripts/envoy as the watch calls it. `send --source` alone is the watch's capability probe, which
# the real script answers with a refusal and sends nothing; every other call is a send.
ENVOY_STUB = """#!/bin/sh
if [ "$*" = "send --source" ]; then echo "--source accepts only envoy" >&2; exit 2; fi
printf '%s\\n' "$*" >> "$STUB_LOG"
echo '{"id":"notice-1","holder":"01a0ef85-0000-7000-8000-000000000000"}'
"""

# scripts/envoy before `--source` existed: `send --source envoy <topic> <msg>` publishes to the
# topic "--source", and `send --source` alone is a usage error.
ENVOY_WITHOUT_SOURCE = """#!/bin/sh
if [ $# -lt 3 ]; then echo "Usage: envoy send <target> <message>" >&2; exit 1; fi
printf '%s\\n' "$*" >> "$STUB_LOG"
echo '{"id":"notice-1"}'
"""

ENVOY_REFUSING = """#!/bin/sh
if [ "$*" = "send --source" ]; then echo "--source accepts only envoy" >&2; exit 2; fi
echo "error: Envoy at http://127.0.0.1:9020 failed POST /v1/messages/publish: {\\"error\\":\\"no holder\\"}" >&2
exit 1
"""

# Records curl's argv, and the mode of every @file it is handed, then runs the real curl.
CURL_RECORDER = """#!/bin/sh
for a in "$@"; do
  printf '%s\\n' "$a" >> "$CURL_LOG"
  case "$a" in @*) stat -c 'mode %a' "${a#@}" >> "$CURL_LOG" ;; esac
done
exec /usr/bin/curl "$@"
"""


def ts(day: int, hour: int = 12, month: int = 9) -> str:
    """An instant as Dispatch writes it (Go's RFC 3339 with nanoseconds, trailing zeros trimmed)."""
    return f"2026-{month:02d}-{day:02d}T{hour:02d}:00:00.123456Z"


def write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class IssueLog:
    """One issue's Dispatch event log, built in seq order, with the fields the watch reads."""

    def __init__(self, key: str, created: str) -> None:
        self.key = key
        self.events: list[dict] = []
        self._append("issue.created", created, {"key": key, "status": "todo"})

    def _append(self, event_type: str, at: str, payload: dict) -> None:
        self.events.append({"seq": len(self.events) + 1, "type": event_type, "created_at": at, "payload": payload})

    def message(self, at: str, body: str) -> IssueLog:
        self._append("message.created", at, {"body": body})
        return self

    def ready(self, at: str, brief: str = "clean") -> IssueLog:
        """A READY packet. brief: "clean" (an Outcome), "missing" (the merger's no-brief placeholder
        and warning), "placeholder" (the placeholder with the warning line dropped), "warning" (an
        Outcome line reworded from the placeholder, with the warning), "none" (a merger that never
        saw the brief: the diff summary and the gate facts, no warning), or "quoted" (that old
        packet quoting a Verification block with an `Outcome:` line of its own)."""
        n = sum(e["type"] == "message.created" and e["payload"]["body"].startswith("READY #") for e in self.events) + 1
        lines = [f"READY #{n} at {SHA} (approved at {SHA}) for {self.key} (https://github.com/example/widgets/pull/{n})"]
        if brief in ("none", "quoted"):
            lines += ["M src/export.py"]
            if brief == "quoted":
                lines += ["## Verification", "Outcome: the export test passes on the fixture workbook"]
            lines += ["CI: run 18234 green; 0 open threads; thermo approve"]
            return self.message(at, "\n".join(lines))
        lines += {
            "clean": ["Outcome: A widget export now keeps the column order the user chose."],
            "missing": ["Outcome: (the pull request body carries no brief)", "Warning: brief-missing"],
            "placeholder": ["Outcome: (the pull request body carries no brief)"],
            "warning": ["Outcome: none written; the body has no brief", "Warning: brief-missing"],
        }[brief]
        lines += [
            "Look at first: src/export.py:40", "Not proven / risk: none", "Needs from you: the merge",
            "Gates: CI run 18234 green; 0 open threads; thermo approve", "E2E: tester passed", "Threads: none",
        ]
        return self.message(at, "\n".join(lines))

    def summary(self) -> dict:
        return {"key": self.key, "last_seq": self.events[-1]["seq"]}


def filed(key: str) -> IssueLog:
    """An issue filed long before the release: when its packets were posted is all that counts."""
    return IssueLog(key, ts(1, month=8))


def four_clean() -> list[IssueLog]:
    """Four clean packets posted after the release on two issues: one short of the go-notice."""
    a = filed("AGENTC-101").ready(ts(14)).ready(ts(16))
    b = filed("AGENTC-102").ready(ts(15)).ready(ts(17))
    return [a, b]


def five_clean() -> list[IssueLog]:
    a, b = four_clean()
    b.ready(ts(18))
    return [a, b]


class LegionBriefWatch(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.fixture = root / "fixture"
        dispatch_stub.write_case(self.fixture, TOKEN, [], {})
        self.httpd = dispatch_stub.server(self.fixture)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

        self.dotfiles = root / "dotfiles"
        self.envoy(ENVOY_STUB)
        self.stub_log = root / "envoy.log"
        self.state = root / "home" / ".omp" / "agent" / "legion-brief-watch.json"
        self.config = root / "envoy.json"
        self.write_config({"enabled": True, "serverUrl": self.url, "token": TOKEN})
        self.env = {
            "PATH": f"{self.dotfiles / 'scripts'}:/usr/bin:/bin",
            "HOME": str(root / "home"),
            "ENVOY_CONFIG": str(self.config),
            "LEGION_BRIEF_WATCH_STATE": str(self.state),
            "DOTFILES_DIR": str(self.dotfiles),
            "STUB_LOG": str(self.stub_log),
        }

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.temp_dir.cleanup()

    def envoy(self, body: str) -> None:
        write_executable(self.dotfiles / "scripts" / "envoy", body)

    def write_config(self, dispatch: dict) -> None:
        self.config.write_text(json.dumps({"natsUrls": ["nats://127.0.0.1:4222"], "dispatch": dispatch}), encoding="utf-8")

    def serve(self, *issues: IssueLog) -> None:
        dispatch_stub.write_case(self.fixture, TOKEN, [i.summary() for i in issues], {i.key: i.events for i in issues})

    def watch(self, *args: str, release: str | None = RELEASE, **env: str) -> subprocess.CompletedProcess[str]:
        argv = [str(WATCH), "--project", "AGENTC", *(["--release-time", release] if release else []), *args]
        # systemd's default UMask is 0022; the watch must make its own files private under it.
        return subprocess.run(argv, env={**self.env, **env}, capture_output=True, text=True, check=False, timeout=60,
                              preexec_fn=lambda: os.umask(0o022))

    def sends(self) -> list[str]:
        return self.stub_log.read_text(encoding="utf-8").splitlines() if self.stub_log.exists() else []

    def assert_notice_sent(self, result: subprocess.CompletedProcess[str]) -> str:
        self.assertEqual(result.returncode, 1, result.stderr)
        sends = self.sends()
        self.assertEqual(len(sends), 1, sends)
        prefix = "send --source envoy notifications.role.agentc-1305 "
        self.assertTrue(sends[0].startswith(prefix), sends[0])
        self.assertIn("newest 5 READY", sends[0])
        return sends[0][len(prefix):]

    def assert_held(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sends(), [])
        self.assertFalse(self.state.exists(), "no notice went out, so nothing may be recorded")

    def serve_and_notice(self, *issues: IssueLog) -> str:
        self.serve(*issues)
        return self.assert_notice_sent(self.watch())

    # --- the criterion -------------------------------------------------------------------------

    def test_five_clean_packets_posted_after_the_release_send_the_notice_once(self) -> None:
        self.serve(*five_clean())
        message = self.assert_notice_sent(self.watch())
        self.assertIn("0 warned", message)
        self.assertIn("goNoticeSentAt", json.loads(self.state.read_text(encoding="utf-8")))

    def test_four_packets_are_not_enough(self) -> None:
        self.serve(*four_clean())
        self.assert_held(self.watch())

    def test_a_window_with_no_issues_holds_with_a_zero_count(self) -> None:
        # Every run from the install until the first packet after the release.
        self.serve()
        result = self.watch()
        self.assert_held(result)
        self.assertIn("0 READY packets", result.stdout)

    def test_a_warning_on_the_newest_packet_holds_the_notice_and_the_line_names_the_count(self) -> None:
        a, b = four_clean()
        b.ready(ts(19), "missing")  # five in all, the newest warned
        self.serve(a, b)
        result = self.watch()
        self.assert_held(result)
        self.assertIn("5 READY packets", result.stdout)

    def test_an_early_warning_followed_by_five_clean_packets_sends_the_notice(self) -> None:
        # The warned packet is the oldest but sits on the issue Dispatch lists last, so only an order
        # by the packets' own time puts it outside the newest five.
        early = filed("AGENTC-103").ready(ts(13), "missing")
        message = self.serve_and_notice(*five_clean(), early)
        self.assertIn("6 such packets in all, 1 warned", message)

    def test_packets_from_a_merger_that_never_saw_the_brief_hold_the_notice(self) -> None:
        # Between the merge and the deploy, mergers publish today's READY: no Outcome line and no
        # warning. They must not pass as clean.
        a = filed("AGENTC-101").ready(ts(14), "none").ready(ts(16), "none")
        b = filed("AGENTC-102").ready(ts(15), "none").ready(ts(17), "none").ready(ts(18), "none")
        self.serve(a, b)
        self.assert_held(self.watch())

    def test_the_no_brief_placeholder_is_warned_without_its_warning_line(self) -> None:
        self.serve(*four_clean(), filed("AGENTC-103").ready(ts(18), "placeholder"))
        self.assert_held(self.watch())

    def test_the_warning_line_is_warned_whatever_the_outcome_line_says(self) -> None:
        self.serve(*four_clean(), filed("AGENTC-103").ready(ts(18), "warning"))
        self.assert_held(self.watch())

    def test_an_outcome_line_counts_only_right_after_the_ready_header(self) -> None:
        # An old-format packet quoting a Verification block's `Outcome:` never saw the brief.
        a = filed("AGENTC-101").ready(ts(14), "quoted").ready(ts(16), "quoted")
        b = filed("AGENTC-102").ready(ts(15), "quoted").ready(ts(17), "quoted").ready(ts(18), "quoted")
        self.serve(a, b)
        self.assert_held(self.watch())

    def test_a_packet_posted_before_the_release_is_not_counted(self) -> None:
        self.serve(*four_clean(), filed("AGENTC-90").ready(ts(9)))
        self.assert_held(self.watch())

    def test_a_packet_time_is_compared_as_an_instant_not_as_text(self) -> None:
        # 01:00 at +02:00 on release day is 23:00Z the day before: before the release.
        self.serve(*four_clean(), filed("AGENTC-90").ready("2026-09-10T01:00:00+02:00"))
        self.assert_held(self.watch())

    def test_packets_past_the_first_page_of_events_count(self) -> None:
        long = filed("AGENTC-101")
        for hour in range(230):
            long.message(ts(13 + hour // 24, hour % 24), "a progress note")
        for day in range(23, 28):
            long.ready(ts(day))
        self.serve_and_notice(long)

    def test_a_notice_already_sent_is_not_sent_again(self) -> None:
        self.serve(*five_clean())
        self.state.parent.mkdir(parents=True)
        self.state.write_text(json.dumps({"goNoticeSentAt": "2026-09-20T09:00:00Z"}), encoding="utf-8")
        result = self.watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sends(), [])

    def test_dry_run_prints_the_notice_and_neither_sends_nor_records_it(self) -> None:
        self.serve(*five_clean())
        result = self.watch("--dry-run")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("newest 5 READY", result.stdout)
        self.assertEqual(self.sends(), [])
        self.assertFalse(self.state.exists(), "a dry run must not suppress the real notice")

    # --- sending -------------------------------------------------------------------------------

    def test_a_hand_run_sends_through_the_envoy_of_its_own_checkout(self) -> None:
        # No DOTFILES_DIR: the script's own checkout, not ~/.dotfiles (HOME here holds none).
        copy = self.dotfiles / "scripts" / "legion-brief-watch"
        shutil.copy2(WATCH, copy)
        self.serve(*five_clean())
        env = {k: v for k, v in self.env.items() if k != "DOTFILES_DIR"}
        result = subprocess.run([str(copy), "--project", "AGENTC", "--release-time", RELEASE],
                                env=env, capture_output=True, text=True, check=False, timeout=60)
        self.assert_notice_sent(result)

    def test_an_envoy_without_source_is_refused_before_anything_is_sent(self) -> None:
        self.envoy(ENVOY_WITHOUT_SOURCE)
        self.serve(*five_clean())
        result = self.watch()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--source", result.stderr)
        self.assertEqual(self.sends(), [], "an envoy without --source would publish to the topic --source")
        self.assertFalse(self.state.exists())

    def test_a_notice_envoy_refuses_is_not_recorded_so_the_next_run_sends_it(self) -> None:
        self.envoy(ENVOY_REFUSING)
        self.serve(*five_clean())
        result = self.watch()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("no holder", result.stderr)
        self.assertFalse(self.state.exists())

    def test_a_notice_that_cannot_be_recorded_is_not_sent(self) -> None:
        # Sent but unrecorded, it would go out again on every run.
        locked = Path(self.temp_dir.name) / "locked"
        locked.mkdir(mode=0o500)
        self.serve(*five_clean())
        result = self.watch(LEGION_BRIEF_WATCH_STATE=str(locked / "legion-brief-watch.json"))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.sends(), [])

    # --- Dispatch and its credential -----------------------------------------------------------

    def test_a_listing_that_is_not_keys_and_sequence_numbers_exits_2(self) -> None:
        # A string last_seq would make the page loop count forever; one below 1 or fractional would
        # fetch no page or the wrong ones, when every issue has at least its issue.created event.
        for last_seq in ("5", 0, 2.5):
            with self.subTest(last_seq=last_seq):
                log = filed("AGENTC-101")
                dispatch_stub.write_case(self.fixture, TOKEN, [{"key": log.key, "last_seq": last_seq}], {log.key: log.events})
                result = self.watch()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("issue list", result.stderr)

    def test_a_failing_dispatch_exits_2_naming_dispatch(self) -> None:
        self.serve(*five_clean())
        (self.fixture / "fail").touch()
        result = self.watch()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("Dispatch", result.stderr)
        self.assertEqual(self.sends(), [])

    def test_the_token_travels_in_a_private_header_file_and_is_never_printed(self) -> None:
        recorder = Path(self.temp_dir.name) / "recorder"
        write_executable(recorder / "curl", CURL_RECORDER)
        curl_log = Path(self.temp_dir.name) / "curl.log"
        self.serve(*five_clean())
        result = self.watch(PATH=f"{recorder}:{self.env['PATH']}", CURL_LOG=str(curl_log))
        self.assertEqual(result.returncode, 1, result.stderr)  # the fixture answered: the bearer arrived
        argv = curl_log.read_text(encoding="utf-8")
        modes = {line for line in argv.splitlines() if line.startswith("mode ")}
        self.assertEqual(modes, {"mode 600"}, "the bearer reaches curl only as a 0600 header file")
        for text in (argv, (self.fixture / "requests.log").read_text(encoding="utf-8"), result.stdout, result.stderr):
            self.assertNotIn(TOKEN, text)

    def test_release_time_is_required_and_must_be_rfc3339(self) -> None:
        self.serve(*five_clean())
        for release in (None, "2026-09-10", "yesterday", "2026-09-10 00:00:00Z"):
            with self.subTest(release=release):
                result = self.watch(release=release)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("--release-time", result.stderr)
        self.assertEqual(self.sends(), [])

    def test_a_set_token_file_that_is_missing_or_blank_never_falls_back(self) -> None:
        self.serve(*five_clean())
        blank = Path(self.temp_dir.name) / "blank-token"
        blank.write_text("  \n", encoding="utf-8")
        for path in (Path(self.temp_dir.name) / "no-such-token", blank):
            with self.subTest(path=path):
                result = self.watch(DISPATCH_TOKEN_FILE=str(path), DISPATCH_TOKEN=TOKEN)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(str(path), result.stderr)
        self.assertEqual(self.sends(), [])

    def test_a_token_file_is_trimmed_and_outranks_dispatch_token_and_the_config(self) -> None:
        self.serve(*five_clean())
        token_file = Path(self.temp_dir.name) / "token"
        token_file.write_text(f"  {TOKEN}\n", encoding="utf-8")
        self.write_config({"enabled": True, "serverUrl": self.url, "token": "config-token"})
        self.assert_notice_sent(self.watch(DISPATCH_TOKEN_FILE=str(token_file), DISPATCH_TOKEN="env-token"))

    def test_dispatch_token_outranks_the_config_token(self) -> None:
        self.serve(*five_clean())
        self.write_config({"enabled": True, "serverUrl": self.url, "token": "config-token"})
        self.assert_notice_sent(self.watch(DISPATCH_TOKEN=TOKEN))

    def test_a_set_but_empty_dispatch_token_is_an_error_not_a_fall_through(self) -> None:
        self.serve(*five_clean())
        result = self.watch(DISPATCH_TOKEN="")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("DISPATCH_TOKEN", result.stderr)

    def test_an_enabled_config_without_a_token_names_dispatch_token(self) -> None:
        self.serve(*five_clean())
        self.write_config({"enabled": True, "serverUrl": self.url})
        result = self.watch()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("dispatch.token", result.stderr)

    def test_enabled_gates_the_url_and_not_the_token(self) -> None:
        self.serve(*five_clean())
        self.write_config({"enabled": False, "serverUrl": self.url, "token": TOKEN})
        result = self.watch()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("DISPATCH_URL", result.stderr)
        self.assertIn("dispatch.serverUrl", result.stderr)
        self.assert_notice_sent(self.watch(DISPATCH_URL=self.url))

    def test_a_trailing_slash_on_the_url_is_dropped(self) -> None:
        self.serve(*five_clean())
        self.write_config({"enabled": True, "serverUrl": self.url + "/", "token": TOKEN})
        self.assert_notice_sent(self.watch())

    def test_an_enabled_config_without_a_server_url_is_an_error_not_localhost(self) -> None:
        self.serve(*five_clean())
        self.write_config({"enabled": True, "token": TOKEN})
        result = self.watch()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("dispatch.serverUrl", result.stderr)


class Installer(unittest.TestCase):
    """installers/legion-brief-watch.sh against units in a temp HOME's ~/.dotfiles, the checkout
    arm_user_timer links from."""

    def install(self, release_arg: str, comment: str = "") -> tuple[subprocess.CompletedProcess[str], Path]:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        home = root / "home"
        for checkout, arg in ((home / ".dotfiles", release_arg), (root / "decoy", "--release-time 2026-10-01T12:00:00Z")):
            unit = checkout / "legion" / "legion-brief-watch.service"
            unit.parent.mkdir(parents=True)
            unit.write_text(
                f"[Service]\n{comment}Type=oneshot\n"
                f"ExecStart=%h/.dotfiles/scripts/legion-brief-watch --project AGENTC {arg}\n",
                encoding="utf-8",
            )
            (unit.parent / "legion-brief-watch.timer").write_text("[Timer]\nOnCalendar=daily\n", encoding="utf-8")
        # DOTFILES_DIR names a decoy checkout whose unit would pass, so only a check that reads the
        # unit the helper links refuses. No session bus under this XDG_RUNTIME_DIR, so a systemctl
        # the installer reaches cannot act.
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "XDG_RUNTIME_DIR": str(root), "DOTFILES_DIR": str(root / "decoy")}
        result = subprocess.run([str(INSTALLER)], env=env, capture_output=True, text=True, check=False, timeout=60)
        return result, home / ".config" / "systemd" / "user" / "legion-brief-watch.service"

    def test_a_unit_whose_release_time_the_watch_would_refuse_is_not_armed(self) -> None:
        for release_arg in (
            "--release-time UNSET-until-the-brief-merges",
            "--release-time 2026-10-01",
            "--release-time=2026-10-01T12:00:00Z",
            "--release-time 2026-10-01 12:00:00Z",
        ):
            with self.subTest(release_arg=release_arg):
                result, link = self.install(release_arg)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("--release-time", result.stderr)
                self.assertFalse(link.exists(), "nothing may be linked into the user manager")

    def test_an_example_instant_in_a_comment_does_not_arm_the_marker(self) -> None:
        result, link = self.install(
            "--release-time UNSET-until-the-brief-merges",
            comment="# Written after the merge, e.g. --release-time 2026-10-01T12:00:00Z\n",
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("--release-time", result.stderr)
        self.assertFalse(link.exists(), "nothing may be linked into the user manager")

    @unittest.skipIf(Path("/.dockerenv").exists(), "an agentbox stops the installer before it links the units")
    def test_a_unit_with_its_release_time_written_is_linked(self) -> None:
        result, link = self.install("--release-time 2026-10-01T12:00:00.5+02:00")
        self.assertTrue(link.is_symlink(), result.stderr)


if __name__ == "__main__":
    unittest.main()
