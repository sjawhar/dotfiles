#!/usr/bin/env python3
"""What shims/secrets hands the real secrets: an agent's human-tier request is refused, all else unchanged.

Technique: a stub `secrets` after shims/ on PATH logs every argv it receives and answers
`get --no-request KEY` the way the real client does, a stub `agent-secrets` answers `identity`, and a
scratch secretsd config.toml names a source root whose secrets.human.d holds the human-tier keys.
"""

from __future__ import annotations

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SHIMS = DOTFILES / "shims"
TIMEOUT = 20
AGENT_VARS = ("OMP_SESSION_ID", "PI_SESSION_FILE", "CLAUDECODE", "ENVOY_SESSION_ID")

STUB_SECRETS = r"""#!/bin/bash
{ printf 'argv'; for arg in "$@"; do printf '\t%s' "$arg"; done; printf '\n'; } >> "$STUB_LOG"
if [ "$1 $2" = "get --no-request" ]; then
    case " ${GRANTED_KEYS:-} " in
        *" $3 "*) printf '{"key":"%s","tier":"human","grant":true,"ttl":3600}\n' "$3" ;;
        *) printf '{"key":"%s","tier":"human","grant":false,"ttl":null}\n' "$3" ;;
    esac
    exit 0
fi
printf 'stdout from real secrets: %s\n' "$*"
printf 'stderr from real secrets\n' >&2
cat
exit "${STUB_EXIT:-0}"
"""


def write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class SecretsShim(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.log = root / "argv.log"
        stubs = root / "stubs"
        stubs.mkdir()
        write_executable(stubs / "secrets", STUB_SECRETS)
        write_executable(
            stubs / "agent-secrets",
            '#!/bin/sh\n[ "$1" = identity ] && exit "${IDENTITY_EXIT:-0}"\nexit 2\n',
        )
        source = root / "source"
        (source / "secrets.human.d").mkdir(parents=True)
        for name in (
            "HUMAN_KEY.env",
            "LOCAL_HUMAN.local.env",
            "GITHUB_ADMIN_PAT_THEOREM.env",
        ):
            (source / "secrets.human.d" / name).write_text(
                "sops ciphertext\n", encoding="utf-8"
            )
        config = root / "config.toml"
        config.write_text(f'[source.dotfiles]\npath = "{source}"\n', encoding="utf-8")
        self.base_env = {
            "PATH": f"{SHIMS}:{stubs}:/usr/bin:/bin",
            "HOME": str(root),
            "SECRETSD_CONFIG": str(config),
            "STUB_LOG": str(self.log),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_shim(
        self, *args: str, agent: bool = True, stdin: bytes = b"", **env: str
    ) -> subprocess.CompletedProcess[bytes]:
        full_env = dict(self.base_env)
        if agent:
            full_env["OMP_SESSION_ID"] = "01a1-agent-session"
        full_env.update(env)
        return subprocess.run(
            ["secrets", *args],
            env=full_env,
            input=stdin,
            capture_output=True,
            timeout=TIMEOUT,
            check=False,
        )

    def run_stub(
        self, *args: str, stdin: bytes = b"", **env: str
    ) -> subprocess.CompletedProcess[bytes]:
        """The same call straight to the stub, as it ran before the shim existed."""
        full_env = dict(self.base_env)
        full_env["PATH"] = full_env["PATH"].split(":", 1)[1]
        full_env["OMP_SESSION_ID"] = "01a1-agent-session"
        full_env.update(env)
        return subprocess.run(
            ["secrets", *args],
            env=full_env,
            input=stdin,
            capture_output=True,
            timeout=TIMEOUT,
            check=False,
        )

    def argv_log(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [
            line.split("\t")[1:]
            for line in self.log.read_text(encoding="utf-8").splitlines()
        ]

    def assert_passes_through(
        self, *args: str, agent: bool = True, **env: str
    ) -> list[list[str]]:
        """The shim's output, exit and final argv equal a direct call's; returns every argv the stub saw."""
        stdin = b"stdin bytes\x00\xff\n"
        shim = self.run_shim(*args, agent=agent, stdin=stdin, STUB_EXIT="3", **env)
        shim_calls = self.argv_log()
        self.log.unlink(missing_ok=True)
        direct = self.run_stub(*args, stdin=stdin, STUB_EXIT="3", **env)
        self.log.unlink(missing_ok=True)
        self.assertEqual(
            (shim.returncode, shim.stdout, shim.stderr),
            (direct.returncode, direct.stdout, direct.stderr),
        )
        self.assertEqual(shim_calls[-1], list(args))
        return shim_calls

    def assert_refused(self, *args: str, **env: str) -> str:
        result = self.run_shim(*args, **env)
        self.assertEqual(result.returncode, 77, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(self.argv_log(), [], "the real secrets ran")
        return result.stderr.decode()

    def test_agent_tier_keys_pass_through_byte_for_byte(self) -> None:
        for args in (
            ("AGENT_KEY", "OTHER_KEY", "--", "printf", "%s|", "a b", "$HOME", ""),
            ("get", "AGENT_KEY"),
            ("get", "AGENT_KEY", "--value"),
        ):
            self.assertEqual(self.assert_passes_through(*args), [list(args)])

    def test_non_request_commands_pass_through(self) -> None:
        for args in (
            ("list",),
            ("grants",),
            ("sources",),
            ("lock",),
            ("get", "--no-request", "HUMAN_KEY"),
            (),
        ):
            self.assert_passes_through(*args)

    def test_sami_shell_reaches_the_yubikey_unchanged(self) -> None:
        for args in (("HUMAN_KEY", "--", "env"), ("get", "GITHUB_ADMIN_PAT_THEOREM")):
            self.assertEqual(
                self.assert_passes_through(*args, agent=False), [list(args)]
            )

    def test_every_agent_variable_marks_an_agent(self) -> None:
        for var in AGENT_VARS:
            env = {
                var: "/x/2026-11-02T00-00-00-000Z_01a1-from-file.jsonl"
                if var == "PI_SESSION_FILE"
                else "1"
            }
            result = self.run_shim("get", "HUMAN_KEY", agent=False, **env)
            self.assertEqual(result.returncode, 77, var)

    def test_agent_human_key_is_refused_with_the_broker_command(self) -> None:
        for args in (
            ("get", "HUMAN_KEY"),
            ("get", "--value", "HUMAN_KEY"),
            ("get", "--ttl", "2h", "HUMAN_KEY"),
            ("HUMAN_KEY", "AGENT_KEY", "--", "env"),
            ("LOCAL_HUMAN", "--", "env"),
        ):
            self.log.unlink(missing_ok=True)
            stderr = self.assert_refused(*args)
            self.assertIn('secret-run --reason "<what for>" ', stderr)
            self.assertIn(
                "dispatch://INFRA-223/ask/d2bfc2df-9399-42a9-90b8-35b9f5a46d66", stderr
            )
            self.assertIn(
                "dispatch://INFRA-26/ask/1dc621e8-1e52-4b4c-ae1a-ac296d7f81f7", stderr
            )
            self.assertNotIn("omp --resume", stderr)

    def test_unregistered_session_gets_the_relaunch_line(self) -> None:
        stderr = self.assert_refused("HUMAN_KEY", "--", "env", IDENTITY_EXIT="1")
        self.assertIn("    omp --resume 01a1-agent-session\n", stderr)
        stderr = self.run_shim(
            "get",
            "HUMAN_KEY",
            agent=False,
            IDENTITY_EXIT="1",
            PI_SESSION_FILE="/s/2026-11-02T00-00-00-000Z_01a1-from-file.jsonl",
        ).stderr.decode()
        self.assertIn("    omp --resume 01a1-from-file\n", stderr)

    def test_admin_pat_is_sent_to_the_broker_name(self) -> None:
        stderr = self.assert_refused(
            "GITHUB_ADMIN_PAT_THEOREM", "--", "gh", "api", "user"
        )
        self.assertIn(
            'secret-run --reason "<what for>" GITHUB_ADMIN_PAT -- <command>', stderr
        )
        self.assertIn(
            "The broker holds this same token under the name GITHUB_ADMIN_PAT",
            stderr,
        )
        self.assertNotIn("not confirmed", stderr)

    def test_a_grant_another_session_holds_does_not_pass(self) -> None:
        # The real `get --no-request` reports "grant":true when ANY session holds the key.
        self.assert_refused("HUMAN_KEY", "--", "env", GRANTED_KEYS="HUMAN_KEY")
        self.assert_refused("get", "HUMAN_KEY", GRANTED_KEYS="HUMAN_KEY")

    def test_no_config_means_no_human_keys(self) -> None:
        self.assert_passes_through(
            "get", "HUMAN_KEY", SECRETSD_CONFIG="/nonexistent/config.toml"
        )

    def test_real_binary_is_the_first_past_the_shim(self) -> None:
        before = Path(self.temp_dir.name) / "before"
        before.mkdir()
        write_executable(before / "secrets", "#!/bin/sh\necho before\n")
        result = subprocess.run(
            [str(SHIMS / "secrets"), "list"],
            env={**self.base_env, "PATH": f"{before}:{self.base_env['PATH']}"},
            input=b"",
            capture_output=True,
            timeout=TIMEOUT,
            check=False,
        )
        self.assertEqual(result.stdout, b"stdout from real secrets: list\n")


if __name__ == "__main__":
    unittest.main()
