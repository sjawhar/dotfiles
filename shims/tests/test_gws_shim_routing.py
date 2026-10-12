#!/usr/bin/python3
"""Routing tests for the gws shim: which credential path fires, and fail-loud.

Technique: build a scratch dir containing a stub `gws` that dumps its auth env to
stdout, put it first on PATH, and invoke the shim by absolute path. This ensures
the stub wins over the sibling shims while `find_real_gws` discovers the stub as
real gws.
"""

import os
import stat
import subprocess
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

SHIMS = Path(__file__).resolve().parent.parent

PROFILES = ("personal", "theorem", "trajectory")


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class GwsRouting(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.stub_dir = Path(self.tmp.name)
        write_stub(
            self.stub_dir,
            "gws",
            'echo "TOKEN=${GOOGLE_WORKSPACE_CLI_TOKEN:-}"\n'
            'echo "FILE=${GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE:-}"\n'
            'echo "ADC=${GOOGLE_APPLICATION_CREDENTIALS:-}"\n'
            'echo "DIR=${GOOGLE_WORKSPACE_CLI_CONFIG_DIR:-}"\n'
            'echo "CLIENT_ID=${GOOGLE_WORKSPACE_CLI_CLIENT_ID:-}"\n'
            'echo "CLIENT_SECRET=${GOOGLE_WORKSPACE_CLI_CLIENT_SECRET:-}"\n'
            '[[ -v GWS_ACCOUNT ]] && echo "ACCOUNT=$GWS_ACCOUNT" || echo "ACCOUNT=<unset>"\n'
            'printf "ARG=%s\\n" "$@"\n'
            'while IFS= read -r v; do\n'
            '  [[ -n "${!v:-}" ]] && echo "LEAKED=$v"\n'
            'done < <(compgen -v | grep -E "^GWS_.*_OAUTH$")\n'
            '[[ -n "${GWS_SHIM_SECRETS_REEXEC:-}" ]] && echo "LEAKED=GWS_SHIM_SECRETS_REEXEC"\n'
            'if [[ -n "${GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE:-}" ]]; then\n'
            '  echo "MODE=$(stat -c %a "$GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE")"\n'
            '  cat "$GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"\n'
            'fi\n'
            'exit "${STUB_EXIT:-0}"',
        )
        write_stub(
            self.stub_dir,
            "secrets",
            'key="$1"; shift\n'
            '[[ "$1" == "--" ]] && shift\n'
            'echo "SECRETS_KEY=$key" >&2\n'
            'if [[ -z "${STUB_SECRET_VALUE:-}" ]]; then\n'
            '  echo "stub secrets: no value configured for $key" >&2; exit 1\n'
            'fi\n'
            'export "$key=$STUB_SECRET_VALUE"\n'
            'exec "$@"',
        )
        self.env = {
            "PATH": f"{self.stub_dir}:{SHIMS}:/usr/bin:/bin",
            "HOME": os.environ["HOME"],
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_shim(
        self,
        extra_env: Mapping[str, str] | None = None,
        args: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **(extra_env or {})}
        return subprocess.run(
            [str(SHIMS / "gws"), *(args or ["drive", "files", "list"])],
            capture_output=True,
            text=True,
            env=env,
        )

    def default_credential(self, tag: str) -> str:
        return (
            f'{{"type":"authorized_user","id":"{tag}",'
            f'"client_id":"{tag}-client-id","client_secret":"{tag}-client-secret"}}'
        )

    def test_account_unset_is_loud_and_never_calls_secrets(self):
        write_stub(self.stub_dir, "secrets", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim()
        self.assertNotEqual(result.returncode, 0)
        for profile in PROFILES:
            self.assertIn(profile, result.stderr)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_work_is_not_a_profile(self):
        write_stub(self.stub_dir, "secrets", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim({"GWS_ACCOUNT": "work"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("work", result.stderr)
        for profile in PROFILES:
            self.assertIn(profile, result.stderr)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_each_profile_fetches_via_secrets_and_cleans_up(self):
        for profile in PROFILES:
            for exit_code in (0, 42):
                with self.subTest(profile=profile, exit_code=exit_code):
                    key = f"GWS_{profile.upper()}_READ_OAUTH"
                    result = self.run_shim(
                        {
                            "GWS_ACCOUNT": profile,
                            "STUB_SECRET_VALUE": self.default_credential(profile),
                            "STUB_EXIT": str(exit_code),
                        }
                    )
                    self.assertEqual(result.returncode, exit_code, result.stderr)
                    self.assertIn(f"SECRETS_KEY={key}", result.stderr)
                    self.assertIn(f'"id":"{profile}"', result.stdout)
                    self.assertIn("MODE=600", result.stdout)
                    self.assertIn(
                        f"DIR={os.environ['HOME']}/.config/gws/{profile}", result.stdout
                    )
                    self.assertNotIn("LEAKED=", result.stdout)
                    creds_file = Path(
                        next(
                            line[5:]
                            for line in result.stdout.splitlines()
                            if line.startswith("FILE=")
                        )
                    )
                    self.assertFalse(creds_file.exists())

    def test_preset_read_credential_skips_secrets(self):
        write_stub(self.stub_dir, "secrets", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim(
            {
                "GWS_ACCOUNT": "theorem",
                "GWS_THEOREM_READ_OAUTH": self.default_credential("theorem"),
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"id":"theorem"', result.stdout)
        self.assertIn(f"DIR={os.environ['HOME']}/.config/gws/theorem", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_unrelated_oauth_variables_never_leak_into_real_gws(self):
        result = self.run_shim(
            {
                "GWS_ACCOUNT": "personal",
                "GWS_PERSONAL_READ_OAUTH": self.default_credential("personal"),
                "GWS_WORK_ADMIN_OAUTH": '{"type":"authorized_user","id":"admin"}',
                "GWS_FOO_SEND_OAUTH": '{"type":"authorized_user","id":"foo"}',
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("LEAKED=", result.stdout)

    def test_schema_needs_no_profile_or_credential(self):
        write_stub(self.stub_dir, "secrets", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim(
            {"GWS_PERSONAL_READ_OAUTH": '{"type":"authorized_user"}'},
            args=["schema", "drive.files.list"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FILE=\n", result.stdout)
        self.assertIn("TOKEN=\n", result.stdout)
        self.assertNotIn("LEAKED=", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_auth_with_caller_config_dir_passes_through_with_no_profile(self):
        write_stub(self.stub_dir, "secrets", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim(
            {"GOOGLE_WORKSPACE_CLI_CONFIG_DIR": "/tmp/gws-caller-dir"},
            args=["auth", "status"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DIR=/tmp/gws-caller-dir", result.stdout)
        self.assertIn("FILE=\n", result.stdout)
        self.assertIn("TOKEN=\n", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_auth_without_config_dir_or_account_fails(self):
        result = self.run_shim(args=["auth", "status"])
        self.assertNotEqual(result.returncode, 0)
        for profile in PROFILES:
            self.assertIn(profile, result.stderr)

    def test_auth_selects_personal_store(self):
        result = self.run_shim({"GWS_ACCOUNT": "personal"}, args=["auth", "status"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"DIR={os.environ['HOME']}/.config/gws/personal", result.stdout)
        self.assertNotIn("/.config/gws/theorem", result.stdout)
        self.assertNotIn("/.config/gws/trajectory", result.stdout)

    def test_explicit_token_env_passes_through(self):
        result = self.run_shim({"GOOGLE_WORKSPACE_CLI_TOKEN": "preset-token"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TOKEN=preset-token", result.stdout)

    def test_explicit_token_env_pass_through_scrubs_oauth_credentials(self):
        result = self.run_shim(
            {
                "GOOGLE_WORKSPACE_CLI_TOKEN": "preset-token",
                "GWS_PERSONAL_READ_OAUTH": '{"type":"authorized_user"}',
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TOKEN=preset-token", result.stdout)
        self.assertNotIn("LEAKED=", result.stdout)

    def test_explicit_credentials_file_passes_through(self):
        credentials_file = self.stub_dir / "caller-creds.json"
        credentials_file.write_text('{"type":"authorized_user"}', encoding="utf-8")
        result = self.run_shim({"GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE": str(credentials_file)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"FILE={credentials_file}", result.stdout)

    def test_materialization_fails_loudly_when_client_id_is_missing(self):
        credential = (
            '{"type":"authorized_user","refresh_token":"refresh-token",'
            '"client_secret":"credential-client-secret"}'
        )
        result = self.run_shim(
            {"GWS_ACCOUNT": "trajectory", "GWS_TRAJECTORY_READ_OAUTH": credential}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("client_id", result.stderr)

    def test_find_real_gws_skips_sourced_shim(self):
        env = {
            **self.env,
            "PATH": f"{SHIMS}:{self.stub_dir}:/usr/bin:/bin",
            "GOOGLE_WORKSPACE_CLI_TOKEN": "unused",
        }
        result = subprocess.run(
            ["/bin/bash", "-c", f'source "{SHIMS / "gws"}"; find_real_gws'],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.stub_dir / "gws"))

    def test_reexec_loop_guard_terminates(self):
        # A `secrets` that succeeds without exporting anything would otherwise
        # re-exec forever.
        write_stub(self.stub_dir, "secrets", 'shift; [[ "$1" == "--" ]] && shift; exec "$@"')
        result = self.run_shim({"GWS_ACCOUNT": "trajectory"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("re-exec", result.stderr)

    def test_reexec_preserves_argument_containing_a_space(self):
        # `secrets KEY -- <self> "$@"` must not reflow argv: --params JSON is full of
        # spaces, and a split would turn one argument into several.
        result = self.run_shim(
            {
                "GWS_ACCOUNT": "trajectory",
                "STUB_SECRET_VALUE": self.default_credential("trajectory"),
            },
            args=["drive", "files", "list", "--params", '{"q": "name contains hello there"}'],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ARG={"q": "name contains hello there"}\n', result.stdout)
        self.assertEqual(result.stdout.count("ARG="), 5)


if __name__ == "__main__":
    unittest.main()
