#!/usr/bin/python3
"""Routing tests for the gws shim: which credential path fires, and fail-loud.

Technique: build a scratch dir containing (a) a stub `gws` that dumps its auth env to
stdout, (b) a stub `google-user-token` that prints a fixed token (or exits 1), put it
first on PATH, and invoke the shim by absolute path. This ensures both stubs win over
the parallel sibling shims while `find_real_gws` discovers the stub as real gws.
"""

import os
import stat
import subprocess
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

SHIMS = Path(__file__).resolve().parent.parent


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
            'for v in GWS_WORK_READ_OAUTH GWS_WORK_SEND_OAUTH GWS_WORK_ADMIN_OAUTH GWS_PERSONAL_READ_OAUTH GWS_PERSONAL_SEND_OAUTH GWS_SHIM_SECRETS_REEXEC; do\n'
            '  [[ -n "${!v:-}" ]] && echo "LEAKED=$v"\n'
            'done\n'
            'if [[ -n "${GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE:-}" ]]; then\n'
            '  echo "MODE=$(stat -c %a "$GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE")"\n'
            '  cat "$GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"\n'
            'fi\n'
            'exit "${STUB_EXIT:-0}"',
        )
        write_stub(self.stub_dir, "google-user-token", "echo unexpected broker >&2; exit 99")
        write_stub(self.stub_dir, "uv", '[[ "$1" == "run" && "$2" == "--quiet" && "$3" == "--script" ]] || exit 99\n"$4"')
        write_stub(
            self.stub_dir,
            "secrets",
            'key="$1"; shift\n'
            '[[ "$1" == "--" ]] && shift\n'
            'if [[ -z "${STUB_SECRET_VALUE:-}" ]]; then\n'
            '  echo "stub secrets: no value configured for $key" >&2; exit 1\n'
            'fi\n'
            'export "$key=$STUB_SECRET_VALUE"\n'
            'exec "$@"',
        )
        # Tests simulate the EC2 devbox by default; per-test overrides simulate laptops.
        self.dmi_file = self.stub_dir / "sys_vendor"
        self.dmi_file.write_text("Amazon EC2\n", encoding="utf-8")
        self.env = {
            "PATH": f"{self.stub_dir}:{SHIMS}:/usr/bin:/bin",
            "HOME": os.environ["HOME"],
            "GWS_SHIM_DMI_SYS_VENDOR": str(self.dmi_file),
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

    def test_broker_token_injected_by_default(self):
        write_stub(self.stub_dir, "google-user-token", "echo broker-token-xyz")
        result = self.run_shim()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TOKEN=broker-token-xyz", result.stdout)

    def test_unknown_account_is_loud(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim({"GWS_ACCOUNT": "wrok"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("wrok", result.stderr)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_account_unset_still_uses_broker_on_ec2(self):
        write_stub(self.stub_dir, "google-user-token", "echo broker-token-xyz")
        result = self.run_shim()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TOKEN=broker-token-xyz", result.stdout)
        self.assertIn("ACCOUNT=<unset>", result.stdout)

    def test_account_work_still_uses_broker_on_ec2(self):
        write_stub(self.stub_dir, "google-user-token", "echo broker-token-xyz")
        result = self.run_shim({"GWS_ACCOUNT": "work"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TOKEN=broker-token-xyz", result.stdout)

    def test_auth_subcommand_gets_no_credential(self):
        # `gws auth login` is how the read credentials get created (Tasks 7, 8). If
        # the shim injected one here, provisioning would be impossible.
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        write_stub(self.stub_dir, "secrets", "echo SHOULD-NOT-RUN >&2; exit 1")
        self.dmi_file.write_text("LENOVO\n", encoding="utf-8")
        result = self.run_shim(args=["auth", "status"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FILE=\n", result.stdout)  # no credential injected
        self.assertIn("TOKEN=\n", result.stdout)
        self.assertIn(f"DIR={os.environ['HOME']}/.config/gws/work", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_auth_subcommand_does_not_set_application_default_credentials(self):
        result = self.run_shim(args=["auth", "status"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ADC=\n", result.stdout)

    def test_auth_subcommand_personal_account_selects_personal_store(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim({"GWS_ACCOUNT": "personal"}, args=["auth", "status"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"DIR={os.environ['HOME']}/.config/gws/personal", result.stdout)
        self.assertNotIn("/.config/gws/work", result.stdout)

    def test_auth_subcommand_does_not_leak_oauth_credentials(self):
        result = self.run_shim(
            {"GWS_WORK_SEND_OAUTH": '{"type":"authorized_user"}'},
            args=["auth", "status"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("LEAKED=", result.stdout)

    def test_auth_subcommand_precedes_send_credentials(self):
        result = self.run_shim(
            {"GWS_WORK_SEND_OAUTH": '{"type":"authorized_user"}'},
            args=["auth", "status"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FILE=\n", result.stdout)

    def test_auth_subcommand_keeps_caller_config_dir(self):
        # Task 7 Step 2 and Task 8 Step 2 both invoke
        # `GOOGLE_WORKSPACE_CLI_CONFIG_DIR=... gws auth login`.
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim(
            {"GOOGLE_WORKSPACE_CLI_CONFIG_DIR": "/tmp/gws-caller-dir"},
            args=["auth", "login"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DIR=/tmp/gws-caller-dir", result.stdout)
        self.assertNotIn("/.config/gws/work", result.stdout)
        self.assertIn("FILE=\n", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_work_send_env_materializes_credentials_file(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim(
            {
                "GWS_WORK_SEND_OAUTH": (
                    '{"type":"authorized_user","client_id":"work-client-id",'
                    '"client_secret":"work-client-secret"}'
                )
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"client_id":"work-client-id"', result.stdout)
        self.assertIn("TOKEN=\n", result.stdout)
        self.assertIn("MODE=600", result.stdout)
        self.assertIn(f"DIR={os.environ['HOME']}/.config/gws/work", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)
        creds_file = Path(next(line[5:] for line in result.stdout.splitlines() if line.startswith("FILE=")))
        self.assertFalse(creds_file.exists())

    def test_personal_send_env_selects_personal_store(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim(
            {
                "GWS_PERSONAL_SEND_OAUTH": (
                    '{"type":"authorized_user","client_id":"personal-client-id",'
                    '"client_secret":"personal-client-secret"}'
                )
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"DIR={os.environ['HOME']}/.config/gws/personal", result.stdout)
        self.assertIn("MODE=600", result.stdout)
        self.assertIn('"client_id":"personal-client-id"', result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)
        creds_file = Path(next(line[5:] for line in result.stdout.splitlines() if line.startswith("FILE=")))
        self.assertFalse(creds_file.exists())

    def test_work_send_env_cleans_credentials_file_after_real_gws_fails(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim(
            {
                "GWS_WORK_SEND_OAUTH": (
                    '{"type":"authorized_user","client_id":"work-client-id",'
                    '"client_secret":"work-client-secret"}'
                ),
                "STUB_EXIT": "42",
            }
        )
        self.assertEqual(result.returncode, 42, result.stderr)
        creds_file = Path(next(line[5:] for line in result.stdout.splitlines() if line.startswith("FILE=")))
        self.assertFalse(creds_file.exists())

    def test_explicit_token_env_passes_through(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim({"GOOGLE_WORKSPACE_CLI_TOKEN": "preset-token"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TOKEN=preset-token", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_explicit_token_env_pass_through_scrubs_oauth_credentials(self):
        result = self.run_shim(
            {
                "GOOGLE_WORKSPACE_CLI_TOKEN": "preset-token",
                "GWS_WORK_SEND_OAUTH": '{"type":"authorized_user"}',
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TOKEN=preset-token", result.stdout)
        self.assertNotIn("LEAKED=", result.stdout)

    def test_explicit_credentials_file_passes_through(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        credentials_file = self.stub_dir / "caller-creds.json"
        credentials_file.write_text('{"type":"authorized_user"}', encoding="utf-8")
        result = self.run_shim({"GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE": str(credentials_file)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"FILE={credentials_file}", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_explicit_token_wins_over_send_credentials(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim(
            {
                "GOOGLE_WORKSPACE_CLI_TOKEN": "preset-token",
                "GWS_WORK_SEND_OAUTH": '{"type":"authorized_user"}',
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TOKEN=preset-token", result.stdout)
        self.assertIn("FILE=\n", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_broker_failure_is_loud(self):
        write_stub(self.stub_dir, "google-user-token", "echo broker down >&2; exit 1")
        result = self.run_shim()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("broker down", result.stderr)

    def test_empty_broker_token_is_loud(self):
        write_stub(self.stub_dir, "google-user-token", "exit 0")
        result = self.run_shim()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("google-user-token returned an empty token", result.stderr)
        self.assertNotIn("TOKEN=", result.stdout)

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

    def test_mise_uv_runs_broker_when_uv_is_not_on_path(self):
        (self.stub_dir / "uv").unlink()
        mise_uv = self.stub_dir / "home" / ".mise" / "shims"
        mise_uv.mkdir(parents=True)
        write_stub(mise_uv, "uv", 'echo fallback-token')
        result = self.run_shim({"HOME": str(self.stub_dir / "home")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TOKEN=fallback-token", result.stdout)

    def test_work_off_ec2_fetches_read_credential_via_secrets(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        self.dmi_file.write_text("LENOVO\n", encoding="utf-8")
        result = self.run_shim(
            {
                "STUB_SECRET_VALUE": (
                    '{"type":"authorized_user","id":"work-read",'
                    '"client_id":"work-client-id","client_secret":"work-client-secret"}'
                )
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"id":"work-read"', result.stdout)
        self.assertIn(f"DIR={os.environ['HOME']}/.config/gws/work", result.stdout)
        self.assertNotIn("LEAKED=", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_read_credential_reexec_does_not_poison_nested_invocations(self):
        self.dmi_file.write_text("LENOVO\n", encoding="utf-8")
        result = self.run_shim(
            {
                "STUB_SECRET_VALUE": (
                    '{"type":"authorized_user","id":"work-read",'
                    '"client_id":"work-client-id","client_secret":"work-client-secret"}'
                )
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("LEAKED=GWS_SHIM_SECRETS_REEXEC", result.stdout)

    def test_missing_dmi_file_fetches_read_credential_via_secrets(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim(
            {
                "GWS_SHIM_DMI_SYS_VENDOR": str(self.stub_dir / "does-not-exist"),
                "STUB_SECRET_VALUE": (
                    '{"type":"authorized_user","id":"work-read",'
                    '"client_id":"work-client-id","client_secret":"work-client-secret"}'
                ),
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"id":"work-read"', result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_personal_account_on_ec2_uses_personal_store(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim(
            {
                "GWS_ACCOUNT": "personal",
                "STUB_SECRET_VALUE": (
                    '{"type":"authorized_user","id":"personal-read",'
                    '"client_id":"personal-client-id","client_secret":"personal-client-secret"}'
                ),
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"id":"personal-read"', result.stdout)
        self.assertIn(f"DIR={os.environ['HOME']}/.config/gws/personal", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_missing_read_credential_is_loud_and_does_not_fall_back(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        self.dmi_file.write_text("LENOVO\n", encoding="utf-8")
        result = self.run_shim()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stub secrets: no value configured for GWS_WORK_READ_OAUTH", result.stderr)
        self.assertNotIn("TOKEN=", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_reexec_loop_guard_terminates(self):
        # A `secrets` that succeeds without exporting anything would otherwise
        # re-exec forever.
        write_stub(self.stub_dir, "secrets", 'shift; [[ "$1" == "--" ]] && shift; exec "$@"')
        self.dmi_file.write_text("LENOVO\n", encoding="utf-8")
        result = self.run_shim()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("re-exec", result.stderr)

    def test_broker_wins_over_work_read_credential_on_ec2(self):
        # On the devbox the broker outranks a stored work credential, even one the
        # caller happens to have exported. Precedence, not preference.
        write_stub(self.stub_dir, "google-user-token", "echo broker-token-xyz")
        result = self.run_shim(
            {"GWS_WORK_READ_OAUTH": '{"type":"authorized_user","id":"work-read"}'}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TOKEN=broker-token-xyz", result.stdout)
        self.assertIn("FILE=\n", result.stdout)
        self.assertNotIn('"id":"work-read"', result.stdout)

    def test_broker_scrubs_read_and_admin_oauth_credentials(self):
        write_stub(self.stub_dir, "google-user-token", "echo broker-token-xyz")
        result = self.run_shim(
            {
                "GWS_WORK_READ_OAUTH": '{"type":"authorized_user","id":"work-read"}',
                "GWS_WORK_ADMIN_OAUTH": '{"type":"authorized_user","id":"work-admin"}',
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TOKEN=broker-token-xyz", result.stdout)
        self.assertNotIn("LEAKED=", result.stdout)

    def test_reexec_preserves_argument_containing_a_space(self):
        # `secrets KEY -- <self> "$@"` must not reflow argv: --params JSON is full of
        # spaces, and a split would turn one argument into several.
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        self.dmi_file.write_text("LENOVO\n", encoding="utf-8")
        result = self.run_shim(
            {
                "STUB_SECRET_VALUE": (
                    '{"type":"authorized_user","id":"work-read",'
                    '"client_id":"work-client-id","client_secret":"work-client-secret"}'
                )
            },
            args=["drive", "files", "list", "--params", '{"q": "name contains hello there"}'],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ARG={"q": "name contains hello there"}\n', result.stdout)
        self.assertEqual(result.stdout.count("ARG="), 5)

    def test_work_send_credentials_work_on_non_ec2(self):
        self.dmi_file.write_text("LENOVO\n", encoding="utf-8")
        result = self.run_shim(
            {
                "GWS_WORK_SEND_OAUTH": (
                    '{"type":"authorized_user","client_id":"work-client-id",'
                    '"client_secret":"work-client-secret"}'
                )
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"client_id":"work-client-id"', result.stdout)

    def test_send_credential_does_not_leak_into_real_gws_env(self):
        # The credential must reach real gws as a 0600 file and by no other route.
        # This matters most after Task 4's re-exec, where `secrets` *exports* the
        # value: without the scrub, real gws would inherit the plaintext JSON.
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim(
            {
                "GWS_WORK_SEND_OAUTH": (
                    '{"type":"authorized_user","client_id":"work-client-id",'
                    '"client_secret":"work-client-secret"}'
                ),
                "GWS_WORK_ADMIN_OAUTH": '{"type":"authorized_user","id":"work-admin"}',
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"client_id":"work-client-id"', result.stdout)  # reached the file
        self.assertNotIn("LEAKED=", result.stdout)  # and nothing else

    def test_materialized_credential_exports_its_client_config(self):
        credential = (
            '{"type":"authorized_user","refresh_token":"refresh-token",'
            '"client_id":"credential-client-id","client_secret":"credential-client-secret"}'
        )

        result = self.run_shim({"GWS_WORK_SEND_OAUTH": credential})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CLIENT_ID=credential-client-id", result.stdout)
        self.assertIn("CLIENT_SECRET=credential-client-secret", result.stdout)

    def test_materialized_credential_is_the_application_default_credentials_file(self):
        credential = (
            '{"type":"authorized_user","refresh_token":"refresh-token",'
            '"client_id":"credential-client-id","client_secret":"credential-client-secret"}'
        )

        result = self.run_shim({"GWS_WORK_SEND_OAUTH": credential})

        self.assertEqual(result.returncode, 0, result.stderr)
        credentials_file = next(
            line[5:] for line in result.stdout.splitlines() if line.startswith("FILE=")
        )
        application_default_credentials_file = next(
            line[4:] for line in result.stdout.splitlines() if line.startswith("ADC=")
        )
        self.assertEqual(application_default_credentials_file, credentials_file)

    def test_materialization_fails_loudly_when_client_id_is_missing(self):
        credential = (
            '{"type":"authorized_user","refresh_token":"refresh-token",'
            '"client_secret":"credential-client-secret"}'
        )

        result = self.run_shim({"GWS_WORK_SEND_OAUTH": credential})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("client_id", result.stderr)

    def test_both_send_credentials_set_is_loud(self):
        # Resolution order checks work before personal, so without this guard
        # setting both would silently send as work.
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN >&2; exit 1")
        result = self.run_shim(
            {
                "GWS_WORK_SEND_OAUTH": '{"type":"authorized_user","id":"work-send"}',
                "GWS_PERSONAL_SEND_OAUTH": '{"type":"authorized_user","id":"personal-send"}',
            }
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("GWS_WORK_SEND_OAUTH", result.stderr)
        self.assertIn("GWS_PERSONAL_SEND_OAUTH", result.stderr)
        self.assertNotIn("FILE=", result.stdout)  # real gws never ran at all
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)


if __name__ == "__main__":
    unittest.main()
