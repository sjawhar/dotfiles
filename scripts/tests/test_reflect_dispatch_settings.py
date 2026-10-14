#!/usr/bin/env python3
"""The reflect skill's extract-dispatch-human.py resolves Dispatch as a command does.

Its own small reader (Python cannot import Legion's envoy-client) follows envoy-client's rule for
a script: the envoy.json is ENVOY_CONFIG_FILE's, else a named omp profile's, else, under Claude Code
(CLAUDE_CODE_CHILD_SESSION=1), CLAUDE_CONFIG_DIR's, else OPENCODE_CONFIG_DIR's, else
${XDG_CONFIG_HOME:-$HOME/.config}/legion's; a file envoy-client's schema refuses, or one that sets
dispatch.enabled false, is refused; the server is DISPATCH_URL, else the enabled section's
serverUrl; the token is DISPATCH_TOKEN_FILE, else DISPATCH_TOKEN, else dispatch.token, which goes
only to the server written beside it; a `!` token is a command whose output is never shown.

Technique: load the script as a module by path and call dispatch_settings with an explicit
environment against files in a scratch directory; nothing reads the caller's own environment.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "plugins" / "sjawhar" / "skills" / "reflect" / "extract-dispatch-human.py"

spec = importlib.util.spec_from_file_location("extract_dispatch_human", SCRIPT)
assert spec is not None and spec.loader is not None
reflect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reflect)

SERVER = "https://dispatch.example"


class DispatchSettings(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.neutral = self.home / ".config" / "legion" / "envoy.json"
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write(self, path: Path, content: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return path

    def dispatch(self, path: Path | None = None, **section: object) -> Path:
        return self.write(path or self.neutral, {"dispatch": section})

    def settings(self, **extra: str) -> tuple[str, str]:
        return reflect.dispatch_settings({**self.env, **extra})

    def refused(self, *needles: str, **extra: str) -> str:
        with self.assertRaises(ValueError) as caught:
            self.settings(**extra)
        message = str(caught.exception)
        for needle in needles:
            self.assertIn(needle, message)
        return message

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
            self.dispatch(path, enabled=True, serverUrl=f"https://{name}.example", token=name)
        env = {
            "ENVOY_CONFIG_FILE": str(places["handed"]),
            "OMP_PROFILE": "work",
            "PI_CODING_AGENT_DIR": str(places["profile"].parent),
            "CLAUDE_CODE_CHILD_SESSION": "1",
            "CLAUDE_CONFIG_DIR": str(places["claude"].parent),
            "OPENCODE_CONFIG_DIR": str(places["opencode"].parent),
            "XDG_CONFIG_HOME": str(self.root / "xdg"),
        }
        for name, change in (
            ("handed", {}),
            ("profile", {"ENVOY_CONFIG_FILE": ""}),
            ("claude", {"OMP_PROFILE": "default"}),
            ("opencode", {"CLAUDE_CONFIG_DIR": ""}),
            ("xdg", {"OPENCODE_CONFIG_DIR": ""}),
            ("home", {"XDG_CONFIG_HOME": ""}),
        ):
            env |= change
            with self.subTest(name):
                self.assertEqual(self.settings(**env), (f"https://{name}.example/api/v1", name))

    def test_claude_codes_directory_counts_only_under_claude_code(self) -> None:
        """A shell exports CLAUDE_CONFIG_DIR (Claude Code's docs tell users to), so outside Claude
        Code the script reads the next file in the order; Claude Code sets
        CLAUDE_CODE_CHILD_SESSION=1 in every command it runs, and only that value counts."""
        claude = self.root / "claude" / "envoy.json"
        opencode = self.root / "opencode" / "envoy.json"
        for name, path in (("claude", claude), ("opencode", opencode), ("neutral", self.neutral)):
            self.dispatch(path, enabled=True, serverUrl=f"https://{name}.example", token=name)
        base = {"CLAUDE_CONFIG_DIR": str(claude.parent)}
        for expected, extra in (
            ("neutral", {}),
            ("claude", {"CLAUDE_CODE_CHILD_SESSION": "1"}),
            ("neutral", {"CLAUDE_CODE_CHILD_SESSION": "0"}),
            ("neutral", {"CLAUDE_CODE_CHILD_SESSION": ""}),
            ("neutral", {"CLAUDE_CODE_CHILD_SESSION": "true"}),
            ("opencode", {"OPENCODE_CONFIG_DIR": str(opencode.parent)}),
        ):
            with self.subTest(extra=extra):
                self.assertEqual(self.settings(**base, **extra), (f"https://{expected}.example/api/v1", expected))

    def test_a_relative_path_the_rule_uses_is_refused_naming_it(self) -> None:
        self.dispatch(enabled=True, serverUrl=SERVER, token="t")
        self.refused("XDG_CONFIG_HOME is rel, which is not an absolute path", XDG_CONFIG_HOME="rel")
        self.refused("ENVOY_CONFIG_FILE is envoy.json, which is not an absolute path", ENVOY_CONFIG_FILE="envoy.json")
        self.refused(
            "CLAUDE_CONFIG_DIR is claude, which is not an absolute path",
            CLAUDE_CODE_CHILD_SESSION="1",
            CLAUDE_CONFIG_DIR="claude",
        )
        # Outside Claude Code the rule does not reach CLAUDE_CONFIG_DIR, so nothing refuses it.
        self.assertEqual(self.settings(CLAUDE_CONFIG_DIR="claude"), (f"{SERVER}/api/v1", "t"))

    def test_the_server_is_DISPATCH_URL_else_the_enabled_section(self) -> None:
        self.dispatch(enabled=True, serverUrl=f"{SERVER}/", token="t")
        self.assertEqual(self.settings(), (f"{SERVER}/api/v1", "t"))
        self.dispatch(enabled=True, token="t")
        self.assertEqual(self.settings(), ("http://localhost:8766/api/v1", "t"))
        self.assertEqual(self.settings(DISPATCH_URL="http://localhost:8766/base"), ("http://localhost:8766/base/api/v1", "t"))

    def test_dispatch_off_is_refused(self) -> None:
        self.refused(f"no envoy.json at {self.neutral}, and DISPATCH_URL is not set: Dispatch is off")
        self.dispatch(enabled=False, serverUrl=SERVER, token="t")
        self.refused(f"{self.neutral} sets dispatch.enabled to false: Dispatch is off")
        self.refused("Dispatch is off", DISPATCH_URL=SERVER, DISPATCH_TOKEN="t")
        # A section without enabled: true names no server.
        self.dispatch(serverUrl=SERVER, token="t")
        self.refused(f"{self.neutral} has no dispatch section with enabled true, and DISPATCH_URL is not set")

    def test_the_file_token_goes_only_to_its_own_server(self) -> None:
        self.dispatch(enabled=True, serverUrl=SERVER, token="file-token")
        self.refused(
            f"DISPATCH_URL names https://other.example, but {self.neutral}'s dispatch.token is for {SERVER}; "
            "set DISPATCH_TOKEN or DISPATCH_TOKEN_FILE for it",
            DISPATCH_URL="https://other.example",
        )
        self.refused("is for https://dispatch.example", DISPATCH_URL="http://dispatch.example")
        # The same origin spelled another way: case, user info, the default port, a path.
        self.assertEqual(
            self.settings(DISPATCH_URL="HTTPS://user@Dispatch.EXAMPLE:443/x"),
            ("HTTPS://user@Dispatch.EXAMPLE:443/x/api/v1", "file-token"),
        )
        # A section without enabled: true still pairs its token with the server written beside it.
        self.dispatch(token="file-token")
        self.assertEqual(self.settings(DISPATCH_URL="http://localhost:8766"), ("http://localhost:8766/api/v1", "file-token"))

    def test_the_variables_name_the_token_ahead_of_the_file(self) -> None:
        self.dispatch(enabled=True, serverUrl=SERVER, token="file-token")
        pointer = self.write(self.root / "token", "  pointed-token \n")
        self.assertEqual(self.settings(DISPATCH_TOKEN="plain")[1], "plain")
        self.assertEqual(self.settings(DISPATCH_TOKEN_FILE=str(pointer), DISPATCH_TOKEN="plain")[1], "pointed-token")
        self.assertEqual(self.settings(DISPATCH_TOKEN_FILE="", DISPATCH_TOKEN="")[1], "file-token")
        # A token for another server comes from a variable.
        self.assertEqual(self.settings(DISPATCH_URL="https://other.example", DISPATCH_TOKEN="plain")[1], "plain")

    def test_a_pointer_that_cannot_give_a_token_is_refused_never_bypassed(self) -> None:
        self.dispatch(enabled=True, serverUrl=SERVER, token="file-token")
        missing, empty = self.root / "missing", self.write(self.root / "empty", "\n")
        spaced = self.write(self.root / "spaced", "one two")
        self.refused(f"DISPATCH_TOKEN_FILE names {missing}, which could not be read", DISPATCH_TOKEN_FILE=str(missing))
        self.refused(f"DISPATCH_TOKEN_FILE names {empty}, which is empty", DISPATCH_TOKEN_FILE=str(empty))
        message = self.refused("which holds whitespace or a control character", DISPATCH_TOKEN_FILE=str(spaced))
        self.assertNotIn("one two", message)

    def test_a_command_token_runs_and_only_when_used(self) -> None:
        marker = self.root / "ran"
        self.dispatch(enabled=True, serverUrl=SERVER, token=f"!touch {marker}; printf '  command-token \\n'")
        self.assertEqual(self.settings(DISPATCH_TOKEN="plain")[1], "plain")
        self.assertFalse(marker.exists())
        self.refused("is for", DISPATCH_URL="https://other.example")
        self.assertFalse(marker.exists())
        self.assertEqual(self.settings()[1], "command-token")
        self.assertTrue(marker.exists())

    def test_a_command_that_gives_no_token_is_refused_never_showing_its_output(self) -> None:
        for command, reason in (
            ("!echo SECRET-OUT; echo SECRET-ERR >&2; exit 3", "exited with status 3"),
            ("!printf 'SECRET-ONE\\nSECRET-TWO\\n'", "printed more than one line, or whitespace inside its value"),
            ("!true", "printed nothing"),
            ("!kill -TERM $$", "exited with SIGTERM"),
        ):
            with self.subTest(reason):
                self.dispatch(enabled=True, serverUrl=SERVER, token=command)
                message = self.refused(f"{self.neutral}: dispatch.token's command {reason}")
                self.assertNotIn("SECRET", message)

    def test_a_background_process_holding_stdout_does_not_hold_the_script(self) -> None:
        self.dispatch(enabled=True, serverUrl=SERVER, token="!sleep 8 & echo command-token")
        started = time.monotonic()
        self.assertEqual(self.settings()[1], "command-token")
        self.assertLess(time.monotonic() - started, 5)

    def test_a_command_past_the_limit_is_killed_with_its_children(self) -> None:
        survivor = self.root / "survivor"
        self.dispatch(enabled=True, serverUrl=SERVER, token=f"!(sleep 3; touch {survivor}) & sleep 30")
        started = time.monotonic()
        with mock.patch.object(reflect, "COMMAND_TIMEOUT_S", 1):
            self.refused(f"{self.neutral}: dispatch.token's command did not finish within 1 s")
        self.assertLess(time.monotonic() - started, 5)
        time.sleep(3)
        self.assertFalse(survivor.exists(), "the command's background child outlived the kill")

    def test_a_file_envoy_client_refuses_is_refused(self) -> None:
        secret = "SECRET-IN-A-BROKEN-FILE"
        for content, reason in (
            ('{"dispatch": {"token": ' + secret + "}}", "invalid JSON (line 1 column"),
            ([], "expected a JSON object"),
            ({"dispatch": "x"}, "dispatch: expected an object"),
            ({"dispatch": {"enabled": True, "token": 7}}, "dispatch.token: expected a string"),
            ({"dispatch": {"enabled": True, "tokn": "x"}}, "unrecognized dispatch key(s): dispatch.tokn"),
            ({"dispatch": {"enabled": True, "serverUrl": "localhost:8766"}}, "dispatch.serverUrl: must be an http or https URL"),
            ({"dispatch": {"enabled": True}, "envoy": {"url": 1}}, "envoy.url: must be an http or https URL"),
            ({"natsUrls": ["nats://x:4222"]}, "the top-level natsUrls is not read; move it to envoy.natsUrls"),
        ):
            with self.subTest(reason):
                self.write(self.neutral, content)
                message = self.refused(f"{self.neutral}: {reason}", DISPATCH_URL=SERVER, DISPATCH_TOKEN="t")
                self.assertNotIn(secret, message)

    def test_values_a_header_cannot_carry_are_refused(self) -> None:
        self.dispatch(enabled=True, serverUrl=SERVER, token="file token")
        message = self.refused(f"{self.neutral}: dispatch.token holds whitespace or a control character")
        self.assertNotIn("file token", message)
        self.refused("DISPATCH_TOKEN holds whitespace or a control character", DISPATCH_TOKEN="a\nb")
        self.refused("DISPATCH_URL must be an http or https URL", DISPATCH_URL="localhost:8766", DISPATCH_TOKEN="t")


if __name__ == "__main__":
    unittest.main()
