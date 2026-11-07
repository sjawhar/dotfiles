#!/usr/bin/env python3
"""A missed INFRA-6 mark tells someone, and the role's holder is read from two records.

On 2026-11-07 the 01:29Z and 04:29Z polls of scripts/infra6-scoreboard-poll were recorded
`missed` in marks.txt and nothing else happened: `envoy ps` listed the lead-infra holder with
`roles: []` after its restart, while Envoy's own role record still named it. So this covers
the two changes together:

  * resolve_infra falls back to `envoy role <role>` (GET /v1/roles/<role>, the subcommand added
    to scripts/envoy) and accepts its answer only when that session is live in `envoy ps`, by
    session id, since `roles` is the field that went empty;
  * every mark recorded missed sends one Envoy message — to the holder when there is one, else
    to the SRE's role notifications.role.lead-box-health — naming the mark, why, and the
    command to run it by hand.

Technique: a stub `envoy` on PATH records every call and answers `ps`, `role`, `send` and
`notify` from files the test writes, and a stub `date` pins the clock to one minute past a
13:29Z tick so `cron` has exactly one mark due (that tick's wake is pre-settled in marks.txt).
`envoy role` itself is driven against a one-request HTTP server, with ENVOY_URL naming it.
"""

from __future__ import annotations

import http.server
import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
POLL = DOTFILES / "scripts" / "infra6-scoreboard-poll"
ENVOY = DOTFILES / "scripts" / "envoy"

HOLDER = "01a13c57-d826-7242-80fe-5f0670188b11"
LEAD = "02b24d68-e937-8353-91ff-6f6781299c22"
FAKE_NOW = "2026-11-07T13:30:00Z"
POLL_MARK = "poll 2026-11-07T13:29:00Z"
WAKE_MARK = "wake 2026-11-07T10:59:00Z"
BOX_HEALTH = "notifications.role.lead-box-health"

# Every call is appended to $STUB_CALLS; `ps` answers $STUB_PS and `role` answers $STUB_ROLE,
# which exits 1 when empty, as the real subcommand does for a role with no live holder.
ENVOY_STUB = r"""
cmd=${1:-}; shift || true
case "$cmd" in
    ps) printf 'ps %s\n' "$*" >>"$STUB_CALLS"; cat "$STUB_PS"; exit 0 ;;
    role)
        printf 'role %s\n' "$*" >>"$STUB_CALLS"
        [ -s "$STUB_ROLE" ] || exit 1
        cat "$STUB_ROLE"; exit 0 ;;
    send|notify)
        target=${@: -1}
        body=$(cat)
        { printf '=== %s %s\n' "$cmd" "$target"; printf '%s\n' "$body"; } >>"$STUB_CALLS"
        [ "$cmd" = send ] &&
            printf '{"event_id":"e1","topic":"notifications.agent.%s"}\n' "$target"
        exit 0 ;;
esac
exit 2
"""

# The clock the script reads. A call that already names a time (-d) is passed through; one that
# asks for now is answered from $STUB_NOW, so `cron` sees exactly one tick.
DATE_STUB = r"""
for arg in "$@"; do
    [ "$arg" = -d ] && exec /usr/bin/date "$@"
done
exec /usr/bin/date -u -d "$STUB_NOW" "$@"
"""


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def session(session_id: str, roles: list[str], title: str) -> dict[str, object]:
    return {
        "session_id": session_id,
        "title": title,
        "roles": roles,
        "host": "sami-devbox",
        "dir": "/home/sami",
        "topics": [],
        "port": 0,
        "updated_at": 1762519800000,
    }


class Infra6ScoreboardPoll(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.bin = self.root / "bin"
        write_stub(self.bin, "envoy", ENVOY_STUB)
        write_stub(self.bin, "date", DATE_STUB)
        self.calls = self.root / "calls.txt"
        self.ps = self.root / "ps.json"
        self.role = self.root / "role.txt"
        self.role.write_text("", encoding="utf-8")
        self.leads = self.root / "leads.tsv"
        self.leads.write_text(f"{LEAD}\tlinux\n", encoding="utf-8")
        self.state = self.root / "state" / "infra6-scoreboard-poll"
        self.state.mkdir(parents=True)
        # That tick's wake is older than the grace and is not what these cases are about.
        (self.state / "marks.txt").write_text(f"{WAKE_MARK}\n", encoding="utf-8")

    def run_cron(self) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.update(
            PATH=f"{self.bin}:{env['PATH']}",
            STUB_CALLS=str(self.calls),
            STUB_PS=str(self.ps),
            STUB_ROLE=str(self.role),
            STUB_NOW=FAKE_NOW,
            XDG_STATE_HOME=str(self.root / "state"),
            INFRA6_LEADS=str(self.leads),
        )
        return subprocess.run(
            [str(POLL), "cron"], env=env, capture_output=True, text=True, check=False
        )

    def recorded(self) -> str:
        return self.calls.read_text(encoding="utf-8") if self.calls.exists() else ""

    def marks(self) -> list[str]:
        return (self.state / "marks.txt").read_text(encoding="utf-8").splitlines()

    def test_holder_only_in_the_role_record_still_polls(self) -> None:
        """`envoy ps` lists the holder with no role; the role record names it; the poll goes out."""
        self.ps.write_text(
            json.dumps([session(HOLDER, [], "INFRA"), session(LEAD, [], "linux")]),
            encoding="utf-8",
        )
        self.role.write_text(f"{HOLDER}\n", encoding="utf-8")

        result = self.run_cron()

        self.assertIn("role lead-infra", self.recorded())
        self.assertIn(f"=== send {LEAD}", self.recorded())
        self.assertIn("INFRA-6 blocked-agent-hours poll", self.recorded())
        self.assertIn(POLL_MARK, self.marks())
        self.assertNotIn(f"{POLL_MARK} missed", self.marks())
        self.assertNotIn(f"=== notify {BOX_HEALTH}", self.recorded())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_holder_anywhere_is_missed_and_told_to_box_health(self) -> None:
        """Neither record names a holder: the mark is missed and the SRE's role hears why."""
        self.ps.write_text(json.dumps([session(LEAD, [], "linux")]), encoding="utf-8")

        self.run_cron()

        self.assertIn(f"{POLL_MARK} missed", self.marks())
        recorded = self.recorded()
        self.assertIn(f"=== notify {BOX_HEALTH}", recorded)
        self.assertIn(POLL_MARK, recorded)
        self.assertIn("has no live holder", recorded)
        self.assertIn("infra6-scoreboard-poll tick", recorded)
        self.assertNotIn(f"=== send {LEAD}", recorded)

    def test_a_role_holder_that_is_not_live_is_no_holder(self) -> None:
        """The role record names a session `envoy ps` does not list: it is not taken."""
        self.ps.write_text(json.dumps([session(LEAD, [], "linux")]), encoding="utf-8")
        self.role.write_text(f"{HOLDER}\n", encoding="utf-8")

        self.run_cron()

        self.assertIn(f"{POLL_MARK} missed", self.marks())
        self.assertIn(f"=== notify {BOX_HEALTH}", self.recorded())

    def test_the_listing_naming_the_role_is_unchanged(self) -> None:
        """The usual path: `envoy ps` carries the role, and no role lookup is made at all."""
        self.ps.write_text(
            json.dumps(
                [session(HOLDER, ["lead-infra"], "INFRA"), session(LEAD, [], "linux")]
            ),
            encoding="utf-8",
        )

        result = self.run_cron()

        self.assertNotIn("role lead-infra", self.recorded())
        self.assertIn(f"=== send {LEAD}", self.recorded())
        self.assertIn(POLL_MARK, self.marks())
        self.assertNotIn(f"=== notify {BOX_HEALTH}", self.recorded())
        self.assertEqual(result.returncode, 0, result.stderr)


class RoleHandler(http.server.BaseHTTPRequestHandler):
    """GET /v1/roles/<role>: the listener's answer, or its 404 for a role with no live holder."""

    holder: str | None = None
    seen: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
        RoleHandler.seen.append(self.path)
        if RoleHandler.holder is None:
            body = json.dumps({"error": "no holder for role lead-infra"}).encode()
            self.send_response(404)
        else:
            body = json.dumps(
                {"role": "lead-infra", "holder": RoleHandler.holder, "last_seen": 1}
            ).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


class EnvoyRoleSubcommand(unittest.TestCase):
    def setUp(self) -> None:
        RoleHandler.seen = []
        RoleHandler.holder = None
        self.server = http.server.HTTPServer(("127.0.0.1", 0), RoleHandler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def run_role(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["ENVOY_URL"] = self.url
        env.pop("ENVOY_TOKEN", None)
        env.pop("ENVOY_TOKEN_FILE", None)
        return subprocess.run(
            [str(ENVOY), "role", *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_a_held_role_prints_its_holder(self) -> None:
        RoleHandler.holder = HOLDER

        result = self.run_role("lead-infra")

        self.assertEqual(result.stdout.strip(), HOLDER)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(RoleHandler.seen, ["/v1/roles/lead-infra"])

    def test_an_unheld_role_prints_nothing_and_fails(self) -> None:
        result = self.run_role("lead-infra")

        self.assertEqual(result.stdout, "")
        self.assertEqual(result.returncode, 1)
        self.assertIn("/v1/roles/lead-infra", result.stderr)


if __name__ == "__main__":
    unittest.main()
