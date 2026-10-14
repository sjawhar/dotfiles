#!/usr/bin/env python3
"""scripts/envoy reads its listener and bearer from the envoy.json a command reads.

The file is the one Legion's envoy-client picks for a script: ENVOY_CONFIG_FILE, else a named omp
profile's PI_CODING_AGENT_DIR, else, under Claude Code (CLAUDE_CODE_CHILD_SESSION=1),
CLAUDE_CONFIG_DIR, else OPENCODE_CONFIG_DIR, else ${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json,
and never a checkout's .envoy/envoy.json.
ENVOY_URL overrides envoy.url, a file that sets envoy.enabled false wins over it, and without an
enabled section ENVOY_URL or ENVOY_NATS_URL must be set. The bearer is ENVOY_TOKEN_FILE, else
ENVOY_TOKEN, else the enabled section's envoy.token, which goes only to the listener written
beside it; a `!` token is a command whose output is never printed.

Technique: stub `curl` first on PATH. It records its argv NUL-separated, appends every header
file it is handed with `-H @<file>` to a log (how the script passes the bearer), and answers as
the listener would, so each test reads back the URL called, the bearer sent, and that the bearer
never appeared in an argv. Every environment is built from scratch, so nothing of the session
running the tests leaks in.
"""

from __future__ import annotations

import json
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
ENVOY = DOTFILES / "scripts" / "envoy"
HOST = "example-host-test"

CURL_STUB = r"""
printf '%s\0' "$@" >>"$CURL_LOG"
prev=""
for a in "$@"; do
    if [ "$prev" = -H ] && [ "${a#@}" != "$a" ]; then cat "${a#@}" >>"$CURL_HEADERS"; fi
    prev="$a"
done
case "$*" in
    */v1/sessions*) cat "$CURL_SESSIONS" ;;
    *) echo '{"ok":true}' ;;
esac
"""


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class EnvoyCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        stub_dir = self.root / "bin"
        stub_dir.mkdir()
        write_stub(stub_dir, "curl", CURL_STUB)
        self.home = self.root / "home"
        self.home.mkdir()
        self.neutral = self.home / ".config" / "legion" / "envoy.json"
        self.curl_log = self.root / "curl.argv"
        self.headers = self.root / "curl.headers"
        self.sessions = self.root / "sessions.json"
        self.sessions.write_text("[]", encoding="utf-8")
        self.env = {
            "PATH": f"{stub_dir}:/usr/bin:/bin",
            "HOME": str(self.home),
            "HOSTNAME": HOST,
            "CURL_LOG": str(self.curl_log),
            "CURL_HEADERS": str(self.headers),
            "CURL_SESSIONS": str(self.sessions),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write(self, path: Path, content: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return path

    def envoy(self, *args: str, cwd: Path | None = None, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ENVOY), *args],
            capture_output=True,
            text=True,
            env={**self.env, **extra},
            cwd=cwd or self.root,
            check=False,
            timeout=30,
        )

    def ps(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return self.envoy("ps", "--json", **extra)

    def called_urls(self) -> list[str]:
        argv = self.curl_log.read_text(encoding="utf-8").split("\0")[:-1] if self.curl_log.exists() else []
        return [a for a in argv if "/v1/" in a]

    def bearers(self) -> list[str]:
        if not self.headers.exists():
            return []
        lines = self.headers.read_text(encoding="utf-8").splitlines()
        return [line.removeprefix("Authorization: Bearer ") for line in lines if line.startswith("Authorization:")]

    def assert_refused(self, result: subprocess.CompletedProcess[str], *needles: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for needle in needles:
            self.assertIn(needle, result.stderr)
        self.assertFalse(self.curl_log.exists(), "nothing may be sent")


class WhichFile(EnvoyCase):
    def test_each_location_in_order(self) -> None:
        places = {
            "handed": self.root / "handed.json",
            "profile": self.root / "omp-profile" / "envoy.json",
            "claude": self.root / "claude" / "envoy.json",
            "opencode": self.root / "opencode" / "envoy.json",
            "xdg": self.root / "xdg" / "legion" / "envoy.json",
            "home": self.neutral,
        }
        for name, path in places.items():
            self.write(path, {"envoy": {"enabled": True, "url": f"http://{name}.test:9020"}})
        env = {
            "ENVOY_CONFIG_FILE": str(places["handed"]),
            "OMP_PROFILE": "work",
            "PI_CODING_AGENT_DIR": str(places["profile"].parent),
            "CLAUDE_CODE_CHILD_SESSION": "1",
            "CLAUDE_CONFIG_DIR": str(places["claude"].parent),
            "OPENCODE_CONFIG_DIR": str(places["opencode"].parent),
            "XDG_CONFIG_HOME": str(self.root / "xdg"),
        }
        steps = [
            ("handed", {}),
            ("profile", {"ENVOY_CONFIG_FILE": ""}),
            ("claude", {"OMP_PROFILE": "default"}),
            ("claude", {"OMP_PROFILE": "work", "PI_CODING_AGENT_DIR": ""}),
            ("opencode", {"CLAUDE_CONFIG_DIR": ""}),
            ("xdg", {"OPENCODE_CONFIG_DIR": ""}),
            ("home", {"XDG_CONFIG_HOME": ""}),
        ]
        for name, change in steps:
            env |= change
            with self.subTest(name, change=change):
                path = self.envoy("config-path", **env)
                self.assertEqual(path.returncode, 0, path.stderr)
                self.assertEqual(path.stdout.strip(), str(places[name]))
                self.curl_log.unlink(missing_ok=True)
                result = self.ps(**env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.called_urls(), [f"http://{name}.test:9020/v1/sessions"])

    def test_claude_codes_directory_counts_only_under_claude_code(self) -> None:
        """A shell exports CLAUDE_CONFIG_DIR (Claude Code's docs tell users to), so outside Claude
        Code a command reads the next file in the order; Claude Code sets CLAUDE_CODE_CHILD_SESSION=1
        in every command it runs, and only that value counts."""
        claude = self.root / "claude" / "envoy.json"
        opencode = self.root / "opencode" / "envoy.json"
        for name, path in (("claude", claude), ("opencode", opencode), ("neutral", self.neutral)):
            self.write(path, {"envoy": {"enabled": True, "url": f"http://{name}.test:9020"}})
        base = {"CLAUDE_CONFIG_DIR": str(claude.parent)}
        for expected, extra in (
            (self.neutral, {}),
            (claude, {"CLAUDE_CODE_CHILD_SESSION": "1"}),
            (self.neutral, {"CLAUDE_CODE_CHILD_SESSION": "0"}),
            (self.neutral, {"CLAUDE_CODE_CHILD_SESSION": ""}),
            (self.neutral, {"CLAUDE_CODE_CHILD_SESSION": "true"}),
            (opencode, {"OPENCODE_CONFIG_DIR": str(opencode.parent)}),
        ):
            with self.subTest(extra=extra):
                path = self.envoy("config-path", **base, **extra)
                self.assertEqual(path.returncode, 0, path.stderr)
                self.assertEqual(path.stdout.strip(), str(expected))

    def test_a_relative_path_the_rule_uses_is_refused_naming_it(self) -> None:
        self.write(self.neutral, {"envoy": {"enabled": True}})
        for name, extra in (
            ("XDG_CONFIG_HOME", {"XDG_CONFIG_HOME": "rel"}),
            ("ENVOY_CONFIG_FILE", {"ENVOY_CONFIG_FILE": "envoy.json"}),
            ("CLAUDE_CONFIG_DIR", {"CLAUDE_CODE_CHILD_SESSION": "1", "CLAUDE_CONFIG_DIR": "claude"}),
            ("PI_CODING_AGENT_DIR", {"OMP_PROFILE": "work", "PI_CODING_AGENT_DIR": "agent"}),
            ("OPENCODE_CONFIG_DIR", {"OPENCODE_CONFIG_DIR": "opencode"}),
        ):
            with self.subTest(name):
                value = extra[name]
                for args in (("ps",), ("config-path",)):
                    result = self.envoy(*args, ENVOY_URL="http://x.test", **extra)
                    self.assert_refused(result, f"{name} is {value}, which is not an absolute path")
                    self.assertEqual(result.stdout, "")

    def test_a_relative_variable_the_rule_does_not_reach_is_not_refused(self) -> None:
        handed = self.write(self.root / "handed.json", {"envoy": {"enabled": True, "url": "http://handed.test"}})
        result = self.ps(ENVOY_CONFIG_FILE=str(handed), CLAUDE_CONFIG_DIR="claude", XDG_CONFIG_HOME="rel")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.called_urls(), ["http://handed.test/v1/sessions"])
        self.curl_log.unlink()
        self.write(self.neutral, {"envoy": {"enabled": True, "url": "http://neutral.test"}})
        result = self.ps(CLAUDE_CONFIG_DIR="claude")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.called_urls(), ["http://neutral.test/v1/sessions"])

    def test_a_checkout_envoy_json_is_never_read(self) -> None:
        self.write(self.neutral, {"envoy": {"enabled": True, "url": "http://neutral.test"}})
        checkout = self.root / "repo"
        self.write(checkout / ".envoy" / "envoy.json", {"envoy": {"enabled": True, "url": "http://repo.test", "token": "repo"}})
        result = self.envoy("ps", "--json", cwd=checkout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.called_urls(), ["http://neutral.test/v1/sessions"])
        self.assertEqual(self.bearers(), [])


class Listener(EnvoyCase):
    def test_an_enabled_section_names_the_listener_or_takes_the_default(self) -> None:
        for section, url in (
            ({"enabled": True, "url": "http://listener.test:9021/"}, "http://listener.test:9021/v1/sessions"),
            ({"enabled": True}, "http://127.0.0.1:9020/v1/sessions"),
        ):
            with self.subTest(section=section):
                self.write(self.neutral, {"envoy": section})
                self.curl_log.unlink(missing_ok=True)
                result = self.ps()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.called_urls(), [url])

    def test_send_posts_to_the_file_listener(self) -> None:
        self.write(self.neutral, {"envoy": {"enabled": True, "url": "http://listener.test"}})
        result = self.envoy("send", "notifications.role.example", "hello")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.called_urls(), ["http://listener.test/v1/messages/publish"])

    def test_enabled_false_wins_over_ENVOY_URL(self) -> None:
        self.write(self.neutral, {"envoy": {"enabled": False, "url": "http://listener.test"}})
        for extra in ({}, {"ENVOY_URL": "http://other.test"}):
            with self.subTest(extra=extra):
                result = self.ps(**extra)
                self.assert_refused(result, f"{self.neutral} sets envoy.enabled to false: Envoy is off")

    def test_without_an_enabled_section_only_a_variable_names_an_envoy(self) -> None:
        # A section without enabled: true counts for nothing, its url and token included.
        self.write(self.neutral, {"envoy": {"url": "http://listener.test", "token": "file-token"}})
        result = self.ps()
        self.assert_refused(
            result,
            f"{self.neutral} has no enabled envoy section, and neither ENVOY_URL nor ENVOY_NATS_URL is set: Envoy is off",
        )
        result = self.ps(ENVOY_URL="http://listener.test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.called_urls(), ["http://listener.test/v1/sessions"])
        self.assertEqual(self.bearers(), [])
        self.curl_log.unlink()
        result = self.ps(ENVOY_NATS_URL="nats://nats.test:4222")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.called_urls(), ["http://127.0.0.1:9020/v1/sessions"])

    def test_a_missing_file_needs_a_variable(self) -> None:
        result = self.ps()
        self.assert_refused(
            result, f"no envoy.json at {self.neutral}, and neither ENVOY_URL nor ENVOY_NATS_URL is set: Envoy is off"
        )
        result = self.ps(ENVOY_URL="http://listener.test", ENVOY_NATS_URL=" , ")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.called_urls(), ["http://listener.test/v1/sessions"])

    def test_a_file_that_is_not_a_valid_envoy_json_is_refused(self) -> None:
        secret = "SECRET-IN-A-BROKEN-FILE"
        cases = {
            "invalid JSON": '{"envoy": {"token": ' + secret + "}}",
            "two values": "{} {}",
            "envoy: expected an object": {"envoy": "x"},
            "unrecognized envoy key(s): envoy.tokn": {"envoy": {"enabled": True, "tokn": "x"}},
            "unrecognized dispatch key(s): dispatch.extra": {"dispatch": {"extra": 1}, "envoy": {"enabled": True}},
            "the top-level natsUrls is not read; move it to envoy.natsUrls": {"natsUrls": ["nats://x:4222"]},
            "envoy.url: must be an http or https URL": {"envoy": {"enabled": True, "url": "listener.test:9020"}},
            "envoy.enabled: expected a boolean": {"envoy": {"enabled": "true"}},
            "expected a JSON object": [],
        }
        for reason, content in cases.items():
            with self.subTest(reason):
                self.write(self.neutral, content)
                result = self.ps(ENVOY_URL="http://listener.test")
                self.assert_refused(result, f"{self.neutral}: ")
                self.assertIn("invalid JSON" if reason == "two values" else reason, result.stderr)
                self.assertNotIn(secret, result.stdout + result.stderr)

    def test_ENVOY_URL_must_be_http(self) -> None:
        result = self.ps(ENVOY_URL="listener.test:9020")
        self.assert_refused(result, "ENVOY_URL must be an http or https URL")


class Bearer(EnvoyCase):
    def test_the_file_token_is_sent_as_a_header_and_never_in_an_argv(self) -> None:
        self.write(self.neutral, {"envoy": {"enabled": True, "url": "http://listener.test", "token": "file-token-1"}})
        result = self.envoy("send", "notifications.role.example", "hello")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.bearers(), ["file-token-1"])
        self.assertNotIn("file-token-1", self.curl_log.read_text(encoding="utf-8"))

    def test_the_file_token_goes_only_to_its_own_listener(self) -> None:
        self.write(self.neutral, {"envoy": {"enabled": True, "url": "http://listener.test", "token": "file-token-1"}})
        result = self.ps(ENVOY_URL="https://listener.test")
        self.assert_refused(
            result,
            f"ENVOY_URL names https://listener.test, but {self.neutral}'s envoy.token is for http://listener.test; "
            "set ENVOY_TOKEN or ENVOY_TOKEN_FILE for it",
        )
        # The same origin spelled another way: case, user info, the default port, a path.
        result = self.ps(ENVOY_URL="HTTP://user@Listener.TEST:80/base/")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.called_urls(), ["HTTP://user@Listener.TEST:80/base/v1/sessions"])
        self.assertEqual(self.bearers(), ["file-token-1"])

    def test_a_file_token_without_a_url_pairs_with_the_default_listener(self) -> None:
        self.write(self.neutral, {"envoy": {"enabled": True, "token": "file-token-1"}})
        result = self.ps(ENVOY_URL="http://127.0.0.1:9020")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.bearers(), ["file-token-1"])

    def test_the_variables_name_the_token_ahead_of_the_file(self) -> None:
        self.write(self.neutral, {"envoy": {"enabled": True, "url": "http://listener.test", "token": "file-token-1"}})
        pointer = self.write(self.root / "token", "  pointed-token \n")
        for extra, bearer in (
            ({"ENVOY_TOKEN": "plain-token"}, "plain-token"),
            ({"ENVOY_TOKEN_FILE": str(pointer), "ENVOY_TOKEN": "plain-token"}, "pointed-token"),
            ({"ENVOY_TOKEN_FILE": "", "ENVOY_TOKEN": "plain-token"}, "plain-token"),
            # Another listener needs a token of its own, which a variable gives.
            ({"ENVOY_URL": "http://other.test", "ENVOY_TOKEN": "plain-token"}, "plain-token"),
        ):
            with self.subTest(extra=extra):
                self.headers.unlink(missing_ok=True)
                result = self.ps(**extra)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.bearers(), [bearer])

    def test_a_pointer_that_cannot_give_a_token_is_refused_never_bypassed(self) -> None:
        self.write(self.neutral, {"envoy": {"enabled": True, "token": "file-token-1"}})
        empty = self.write(self.root / "empty", " \n")
        spaced = self.write(self.root / "spaced", "one two\n")
        missing = self.root / "missing"
        for path, reason in ((missing, "could not be read"), (empty, "is empty"), (spaced, "holds whitespace or a control character")):
            with self.subTest(reason):
                result = self.ps(ENVOY_TOKEN_FILE=str(path))
                self.assert_refused(result, f"ENVOY_TOKEN_FILE names {path}, which {reason}")
                self.assertNotIn("one two", result.stderr)

    def test_a_token_a_header_line_cannot_carry_is_refused(self) -> None:
        self.write(self.neutral, {"envoy": {"enabled": True, "token": "file token"}})
        result = self.ps()
        self.assert_refused(result, f"{self.neutral}: envoy.token holds whitespace or a control character")
        self.assertNotIn("file token", result.stderr)
        result = self.ps(ENVOY_TOKEN="plain\ntoken")
        self.assert_refused(result, "ENVOY_TOKEN holds whitespace or a control character")


class TokenCommand(EnvoyCase):
    def section(self, token: str, **more: object) -> None:
        self.write(self.neutral, {"envoy": {"enabled": True, "url": "http://listener.test", "token": token, **more}})

    def test_the_command_output_trimmed_is_the_token(self) -> None:
        self.section("!printf '  command-token-1\\n'")
        result = self.ps()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.bearers(), ["command-token-1"])
        self.assertNotIn("command-token-1", result.stdout + result.stderr)

    def test_a_broadcast_runs_the_command_once_for_every_call(self) -> None:
        runs = self.root / "runs"
        self.section(f"!echo run >>{runs}; echo command-token-1")
        sessions = [
            {"session_id": f"0000000{n}-0000-0000-0000-000000000000", "machine_id": HOST, "dir": "/x", "title": f"s{n}"}
            for n in (1, 2)
        ]
        self.sessions.write_text(json.dumps(sessions), encoding="utf-8")
        result = self.envoy("broadcast", "hello")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 sent, 0 failed", result.stdout)
        self.assertEqual(runs.read_text(encoding="utf-8").splitlines(), ["run"])
        self.assertEqual(self.bearers(), ["command-token-1"] * 3)

    def test_a_failing_command_is_refused_naming_the_field_and_file_never_its_output(self) -> None:
        self.section("!echo SECRET-OUT; echo SECRET-ERR >&2; exit 3")
        result = self.ps()
        self.assert_refused(result, f"{self.neutral}: envoy.token's command exited with status 3")
        self.assertNotIn("SECRET", result.stdout + result.stderr)

    def test_output_no_header_can_carry_is_refused(self) -> None:
        for command, reason in (
            ("!printf 'SECRET-ONE\\nSECRET-TWO\\n'", "printed more than one line, or whitespace inside its value"),
            ("!true", "printed nothing"),
        ):
            with self.subTest(reason):
                self.section(command)
                result = self.ps()
                self.assert_refused(result, f"{self.neutral}: envoy.token's command {reason}")
                self.assertNotIn("SECRET", result.stdout + result.stderr)

    def test_a_background_process_holding_stdout_does_not_hold_envoy(self) -> None:
        self.section("!sleep 8 & echo command-token-1")
        started = time.monotonic()
        result = self.ps()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.bearers(), ["command-token-1"])

    def test_a_command_past_the_limit_is_refused_and_killed_with_its_children(self) -> None:
        survivor = self.root / "survivor"
        self.section(f"!(sleep 12; touch {survivor}) & sleep 30")
        started = time.monotonic()
        result = self.ps()
        self.assert_refused(result, f"{self.neutral}: envoy.token's command did not finish within 10 s")
        self.assertLess(time.monotonic() - started, 20)
        time.sleep(max(0.0, 13 - (time.monotonic() - started)))
        self.assertFalse(survivor.exists(), "the command's background child outlived the limit")

    def test_the_command_runs_only_when_its_token_is_used(self) -> None:
        marker = self.root / "ran"
        for name, extra, section in (
            ("a variable names the token", {"ENVOY_TOKEN": "plain-token"}, {"enabled": True}),
            ("the section is not enabled", {"ENVOY_URL": "http://listener.test"}, {}),
            ("another listener", {"ENVOY_URL": "http://other.test"}, {"enabled": True}),
        ):
            with self.subTest(name):
                self.write(self.neutral, {"envoy": {**section, "token": f"!touch {marker}; echo t"}})
                self.ps(**extra)
                self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
