#!/usr/bin/python3
"""Routing tests for the gcloud shim: account selection and fail-loud validation.

Technique: build a scratch dir containing a stub `gcloud` that dumps
CLOUDSDK_ACTIVE_CONFIG_NAME and argv to stdout, put it first on PATH, and
invoke the shim by absolute path. This ensures the stub wins over the parallel
sibling shim while `find_real_gcloud` discovers the stub as real gcloud. A
fake `$HOME/.config/gcloud/configurations` (or an arbitrary CLOUDSDK_CONFIG
override) supplies the configurations GCLOUD_ACCOUNT is validated against, so
the tests never read or write Sami's real ~/.config/gcloud.
"""

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


def stub_argv(result: subprocess.CompletedProcess[str]) -> list[str]:
    """The argv the stub gcloud received, one `ARG=` line per entry."""
    return [line.removeprefix("ARG=") for line in result.stdout.splitlines() if line.startswith("ARG=")]


class GcloudRouting(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.stub_dir = Path(self.tmp.name)
        write_stub(
            self.stub_dir,
            "gcloud",
            'echo "CONFIG=${CLOUDSDK_ACTIVE_CONFIG_NAME:-<unset>}"\n'
            'echo "DISPLAY=${DISPLAY:-<unset>}"\n'
            'echo "BROWSER=${BROWSER:-<unset>}"\n'
            'printf "ARG=%s\\n" "$@"',
        )
        # Fake $HOME so the default config root ($HOME/.config/gcloud) never
        # touches Sami's real one.
        self.fake_home = self.stub_dir / "home"
        self.cloudsdk_config = self.fake_home / ".config" / "gcloud"
        self.configs_dir = self.cloudsdk_config / "configurations"
        self.configs_dir.mkdir(parents=True)
        for name in ("default", "gws-personal", "theorem"):
            (self.configs_dir / f"config_{name}").write_text("[core]\n", encoding="utf-8")
        (self.cloudsdk_config / "active_config").write_text("box\n", encoding="utf-8")
        self.env = {
            "PATH": f"{self.stub_dir}:{SHIMS}:/usr/bin:/bin",
            "HOME": str(self.fake_home),
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
            [str(SHIMS / "gcloud"), *(args or ["config", "list"])],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_unset_account_runs_real_gcloud_untouched(self):
        result = self.run_shim()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=<unset>", result.stdout)

    def test_empty_account_runs_real_gcloud_untouched(self):
        result = self.run_shim({"GCLOUD_ACCOUNT": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=<unset>", result.stdout)

    def test_known_account_sets_active_config_name(self):
        result = self.run_shim({"GCLOUD_ACCOUNT": "theorem"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=theorem", result.stdout)

    def test_box_auth_login_routes_to_theorem(self):
        result = self.run_shim(args=["auth", "login"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=theorem", result.stdout)
        self.assertIn("configuration theorem", result.stderr)

    def test_box_auth_login_uses_cloudsdk_config_override(self):
        (self.cloudsdk_config / "active_config").write_text("default\n", encoding="utf-8")
        override_root = self.stub_dir / "override-cloudsdk"
        override_configs = override_root / "configurations"
        override_configs.mkdir(parents=True)
        (override_root / "active_config").write_text("box\n", encoding="utf-8")
        (override_configs / "config_theorem").write_text("[core]\n", encoding="utf-8")

        result = self.run_shim(
            {"CLOUDSDK_CONFIG": str(override_root)},
            args=["auth", "login"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=theorem", result.stdout)
        self.assertIn("configuration theorem", result.stderr)

    def test_explicit_configuration_disables_box_auth_login_routing(self):
        for args in (
            ["--configuration", "box", "auth", "login"],
            ["--configuration=box", "auth", "login"],
        ):
            with self.subTest(args=args):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONFIG=<unset>", result.stdout)
                self.assertNotIn("routing auth login", result.stderr)
                self.assertEqual(stub_argv(result), args)

    def test_account_and_active_configuration_disable_box_auth_login_routing(self):
        for extra_env in (
            {"GCLOUD_ACCOUNT": "default"},
            {"CLOUDSDK_ACTIVE_CONFIG_NAME": "default"},
        ):
            with self.subTest(extra_env=extra_env):
                result = self.run_shim(extra_env, args=["auth", "login"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONFIG=default", result.stdout)
                self.assertNotIn("routing auth login", result.stderr)

    def test_credential_file_disables_box_auth_login_routing(self):
        for args in (
            ["auth", "login", "--cred-file=/tmp/credential.json"],
            ["auth", "login", "--cred-file", "/tmp/credential.json"],
        ):
            with self.subTest(args=args):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONFIG=<unset>", result.stdout)
                self.assertNotIn("routing auth login", result.stderr)
                self.assertEqual(stub_argv(result), args)

    def test_non_box_active_configuration_leaves_auth_login_untouched(self):
        (self.cloudsdk_config / "active_config").write_text("default\n", encoding="utf-8")
        result = self.run_shim(args=["auth", "login"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=<unset>", result.stdout)
        self.assertNotIn("routing auth login", result.stderr)

    def test_missing_theorem_configuration_fails_before_running_gcloud(self):
        (self.configs_dir / "config_theorem").unlink()
        write_stub(self.stub_dir, "gcloud", "echo SHOULD-NOT-RUN\nexit 0")
        result = self.run_shim(args=["auth", "login"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("gcloud config configurations create theorem --no-activate", result.stderr)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_non_auth_commands_containing_auth_words_are_untouched(self):
        adc = self.cloudsdk_config / "application_default_credentials.json"
        adc.write_text('{"type":"external_account"}\n', encoding="utf-8")
        for args in (
            ["compute", "instances", "create", "auth", "login"],
            ["compute", "instances", "create", "auth", "application-default", "login"],
        ):
            with self.subTest(args=args):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONFIG=<unset>", result.stdout)
                self.assertEqual(stub_argv(result), args)

    def test_routed_login_removes_update_adc_and_keeps_other_arguments_in_order(self):
        args = ["auth", "login", "--no-launch-browser", "--update-adc", "--project", "my-project"]
        result = self.run_shim(args=args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=theorem", result.stdout)
        self.assertNotIn("ARG=--update-adc", result.stdout)
        self.assertEqual(stub_argv(result), ["auth", "login", "--no-launch-browser", "--project", "my-project"])
        self.assertEqual(result.stderr.count("\n"), 2)
        self.assertIn("box's ADC stays its own identity", result.stderr)

    def test_value_taking_global_flags_preserve_box_login_protection(self):
        cases = (
            (
                ["--project", "autoformalization", "auth", "login", "--update-adc"],
                ["--project", "autoformalization", "auth", "login"],
            ),
            (["--account", "sami@theorem.dev", "auth", "login"], None),
            (["--format", "json", "auth", "login"], None),
        )

        for args, expected_args in cases:
            with self.subTest(args=args):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONFIG=theorem", result.stdout)
                self.assertIn("routing auth login", result.stderr)
                self.assertEqual(stub_argv(result), expected_args or args)

    def test_external_adc_refusal_ignores_explicit_configuration_flag(self):
        adc = self.cloudsdk_config / "application_default_credentials.json"
        adc.write_text('{"type":"external_account"}\n', encoding="utf-8")
        for operation in ("login", "revoke"):
            with self.subTest(operation=operation):
                result = self.run_shim(
                    args=["--configuration", "theorem", "auth", "application-default", operation]
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("gcloud auth login", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_unknown_leading_global_flag_fails_closed_for_external_adc(self):
        adc = self.cloudsdk_config / "application_default_credentials.json"
        adc.write_text('{"type":"external_account"}\n', encoding="utf-8")
        result = self.run_shim(
            args=["--future-global-flag", "value", "auth", "application-default", "login"]
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown leading global flag", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_update_adc_is_unchanged_when_box_auth_login_bypasses_routing(self):
        bypasses = (
            ({}, ["--configuration", "box", "auth", "login", "--update-adc"]),
            ({}, ["--configuration=box", "auth", "login", "--update-adc"]),
            ({"GCLOUD_ACCOUNT": "default"}, ["auth", "login", "--update-adc"]),
            ({"CLOUDSDK_ACTIVE_CONFIG_NAME": "default"}, ["auth", "login", "--update-adc"]),
            ({}, ["auth", "login", "--cred-file=/tmp/credential.json", "--update-adc"]),
            ({}, ["auth", "login", "--cred-file", "/tmp/credential.json", "--update-adc"]),
        )

        for extra_env, args in bypasses:
            with self.subTest(extra_env=extra_env, args=args):
                result = self.run_shim(extra_env, args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("routing auth login", result.stderr)
                self.assertEqual(stub_argv(result), args)

    def test_external_account_adc_refuses_application_default_login_and_revoke(self):
        adc = self.cloudsdk_config / "application_default_credentials.json"
        adc.write_text('{"type":"external_account"}\n', encoding="utf-8")
        for operation in ("login", "revoke"):
            with self.subTest(operation=operation):
                result = self.run_shim(args=["auth", "application-default", operation])
                self.assertEqual(result.returncode, 1)
                self.assertIn("gcloud auth login", result.stderr)
                self.assertIn("legacy_credentials", result.stderr)
                self.assertIn("GOOGLE_APPLICATION_CREDENTIALS", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_non_external_or_missing_adc_allows_application_default_operations(self):
        adc = self.cloudsdk_config / "application_default_credentials.json"
        for contents in ('{"type":"authorized_user"}\n', None):
            if contents is None:
                adc.unlink(missing_ok=True)
            else:
                adc.write_text(contents, encoding="utf-8")
            for operation in ("login", "revoke"):
                with self.subTest(contents=contents, operation=operation):
                    args = ["auth", "application-default", operation]
                    result = self.run_shim(args=args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("CONFIG=<unset>", result.stdout)
                    self.assertEqual(stub_argv(result), args)

    def test_unknown_account_is_loud_and_never_runs_real_gcloud(self):
        write_stub(self.stub_dir, "gcloud", "echo SHOULD-NOT-RUN\nexit 0")
        result = self.run_shim({"GCLOUD_ACCOUNT": "nonexistent"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nonexistent", result.stderr)
        self.assertIn("default", result.stderr)
        self.assertIn("gws-personal", result.stderr)
        self.assertIn("theorem", result.stderr)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_traversal_values_are_rejected(self):
        write_stub(self.stub_dir, "gcloud", "echo SHOULD-NOT-RUN\nexit 0")
        for bad in ("../evil", "foo/bar", ".", ".."):
            with self.subTest(bad=bad):
                result = self.run_shim({"GCLOUD_ACCOUNT": bad})
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
                self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_arguments_with_spaces_survive_as_single_argv_entries(self):
        result = self.run_shim(
            {"GCLOUD_ACCOUNT": "theorem"},
            args=["config", "set", "project", "my project name"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ARG=my project name\n", result.stdout)
        self.assertEqual(result.stdout.count("ARG="), 4)

    def test_respects_cloudsdk_config_override(self):
        other_configs_root = self.stub_dir / "other-cloudsdk"
        other_configs_dir = other_configs_root / "configurations"
        other_configs_dir.mkdir(parents=True)
        (other_configs_dir / "config_alt").write_text("[core]\n", encoding="utf-8")
        result = self.run_shim({"GCLOUD_ACCOUNT": "alt", "CLOUDSDK_CONFIG": str(other_configs_root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=alt", result.stdout)

    def test_cloudsdk_config_override_ignores_home_configurations(self):
        # "theorem" exists under the fake $HOME but not under this override,
        # so the override must be authoritative rather than merely additive.
        other_configs_root = self.stub_dir / "other-cloudsdk"
        (other_configs_root / "configurations").mkdir(parents=True)
        write_stub(self.stub_dir, "gcloud", "echo SHOULD-NOT-RUN\nexit 0")
        result = self.run_shim({"GCLOUD_ACCOUNT": "theorem", "CLOUDSDK_CONFIG": str(other_configs_root)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("theorem", result.stderr)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)

    def test_find_real_gcloud_skips_sourced_shim(self):
        env = {
            **self.env,
            "PATH": f"{SHIMS}:{self.stub_dir}:/usr/bin:/bin",
        }
        result = subprocess.run(
            ["/bin/bash", "-c", f'source "{SHIMS / "gcloud"}"; find_real_gcloud'],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.stub_dir / "gcloud"))

    def test_headless_auth_login_sets_display_and_browser(self):
        for extra_env in ({}, {"GCLOUD_ACCOUNT": "theorem"}):
            with self.subTest(extra_env=extra_env):
                write_stub(self.stub_dir, "forward", "exit 0")
                result = self.run_shim(extra_env, args=["auth", "login"])
                self.assertEqual(result.returncode, 0, result.stderr)
                # CONFIG=theorem with no GCLOUD_ACCOUNT proves the box-routed branch forwarded.
                self.assertIn("CONFIG=theorem", result.stdout)
                self.assertIn("DISPLAY=:0", result.stdout)
                self.assertIn("BROWSER=forward open %s", result.stdout)

    def test_headless_init_sets_display_and_browser(self):
        write_stub(self.stub_dir, "forward", "exit 0")
        result = self.run_shim(args=["init"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DISPLAY=:0", result.stdout)
        self.assertIn("BROWSER=forward open %s", result.stdout)

    def test_non_auth_command_is_untouched_even_with_forward(self):
        write_stub(self.stub_dir, "forward", "exit 0")
        result = self.run_shim(args=["config", "list"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DISPLAY=<unset>", result.stdout)
        self.assertIn("BROWSER=<unset>", result.stdout)

    def test_existing_display_is_preserved(self):
        write_stub(self.stub_dir, "forward", "exit 0")
        result = self.run_shim({"DISPLAY": ":99"}, args=["auth", "login"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DISPLAY=:99", result.stdout)
        self.assertIn("BROWSER=<unset>", result.stdout)

    def test_without_forward_on_path_auth_login_is_untouched(self):
        result = self.run_shim(args=["auth", "login"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DISPLAY=<unset>", result.stdout)
        self.assertIn("BROWSER=<unset>", result.stdout)


if __name__ == "__main__":
    unittest.main()
