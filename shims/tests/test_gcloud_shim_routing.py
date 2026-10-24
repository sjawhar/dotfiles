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


def shim_array(name: str) -> list[str]:
    """One of the shim's own flag tables, read by sourcing it."""
    result = subprocess.run(
        ["/bin/bash", "-c", f'source "{SHIMS / "gcloud"}"; printf "%s\\n" "${{{name}[@]}}"'],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.split()


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

    def test_global_flags_among_the_auth_words_preserve_box_login_protection(self):
        """A global flag's separate value is not a command word, wherever it sits."""
        cases = (
            (
                ["auth", "--http-timeout", "30", "login", "--update-adc"],
                ["auth", "--http-timeout", "30", "login"],
            ),
            (
                ["auth", "--http-timeout=30", "login", "--update-adc"],
                ["auth", "--http-timeout=30", "login"],
            ),
            (["--http-timeout", "30", "auth", "login"], None),
            (["auth", "--verbosity", "debug", "login"], None),
        )

        for args, expected_args in cases:
            with self.subTest(args=args):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONFIG=theorem", result.stdout)
                self.assertIn("routing auth login", result.stderr)
                self.assertEqual(stub_argv(result), expected_args or args)

    def test_global_flags_among_the_auth_words_preserve_adc_refusal(self):
        adc = self.cloudsdk_config / "application_default_credentials.json"
        adc.write_text('{"type":"external_account"}\n', encoding="utf-8")
        write_stub(self.stub_dir, "gcloud", "echo SHOULD-NOT-RUN\nexit 0")
        for args in (
            ["auth", "--http-timeout", "30", "application-default", "login"],
            ["auth", "application-default", "--http-timeout", "30", "revoke"],
            ["auth", "application-default", "--http-timeout=30", "login"],
            ["--verbosity", "debug", "auth", "application-default", "revoke"],
        ):
            with self.subTest(args=args):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("gcloud auth login", result.stderr)
                self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
                self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_empty_cred_file_is_an_ordinary_login_and_is_routed(self):
        """An unset shell variable expands to an empty value, which selects no
        credential file for the SDK, so the box protections still apply."""
        cases = (
            (["auth", "login", "--cred-file=", "--update-adc"], ["auth", "login", "--cred-file="]),
            (["auth", "login", "--cred-file", "", "--update-adc"], ["auth", "login", "--cred-file", ""]),
            (
                ["auth", "login", "--cred-file=/tmp/credential.json", "--cred-file=", "--update-adc"],
                ["auth", "login", "--cred-file=/tmp/credential.json", "--cred-file="],
            ),
        )

        for args, expected_args in cases:
            with self.subTest(args=args):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONFIG=theorem", result.stdout)
                self.assertIn("routing auth login", result.stderr)
                self.assertEqual(stub_argv(result), expected_args)

    def test_last_cred_file_value_decides_the_bypass(self):
        """argparse keeps the last occurrence, so a later real file still bypasses."""
        args = ["auth", "login", "--cred-file=", "--cred-file=/tmp/credential.json", "--update-adc"]
        result = self.run_shim(args=args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=<unset>", result.stdout)
        self.assertNotIn("routing auth login", result.stderr)
        self.assertEqual(stub_argv(result), args)

    def test_release_track_auth_commands_are_routed(self):
        for track in ("alpha", "beta"):
            with self.subTest(track=track):
                result = self.run_shim(args=[track, "auth", "login", "--update-adc"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONFIG=theorem", result.stdout)
                self.assertIn("routing auth login", result.stderr)
                self.assertEqual(stub_argv(result), [track, "auth", "login"])

    def test_release_track_application_default_operations_are_refused(self):
        adc = self.cloudsdk_config / "application_default_credentials.json"
        adc.write_text('{"type":"external_account"}\n', encoding="utf-8")
        write_stub(self.stub_dir, "gcloud", "echo SHOULD-NOT-RUN\nexit 0")
        for track in ("alpha", "beta"):
            for operation in ("login", "revoke"):
                with self.subTest(track=track, operation=operation):
                    result = self.run_shim(args=[track, "auth", "application-default", operation])
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("gcloud auth login", result.stderr)
                    self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
                    self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_release_track_login_keeps_the_explicit_escapes(self):
        flags_file = self.stub_dir / "login.yaml"
        flags_file.write_text("--update-adc:\n", encoding="utf-8")
        with self.subTest(case="flags-file refused"):
            result = self.run_shim(args=["alpha", "auth", "login", f"--flags-file={flags_file}"])
            self.assertEqual(result.returncode, 1)
            self.assertIn("--configuration theorem", result.stderr)
        with self.subTest(case="explicit configuration passes through"):
            args = ["beta", "--configuration", "theorem", "auth", "login"]
            result = self.run_shim(args=args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("CONFIG=<unset>", result.stdout)
            self.assertEqual(stub_argv(result), args)

    def test_release_track_login_keeps_headless_forwarding(self):
        write_stub(self.stub_dir, "forward", "exit 0")
        result = self.run_shim(args=["alpha", "auth", "login"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=theorem", result.stdout)
        self.assertIn("DISPLAY=:0", result.stdout)
        self.assertIn("BROWSER=forward open %s", result.stdout)

    def test_read_only_commands_run_whatever_the_adc_holds(self):
        """A command that cannot change a credential is never refused for the ADC."""
        adc = self.cloudsdk_config / "application_default_credentials.json"
        adc.write_text('{"type":"external_account"}\n', encoding="utf-8")
        for args in (
            ["--no-log-http", "config", "list"],
            ["--log-http", "config", "list"],
            ["--assert=readonly", "config", "list"],
            ["--assert", "readonly", "config", "list"],
            ["--http-timeout", "30", "compute", "instances", "list"],
            ["--no-user-output-enabled", "config", "list"],
            ["--universe-domain", "googleapis.com", "config", "list"],
        ):
            with self.subTest(args=args):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONFIG=<unset>", result.stdout)
                self.assertEqual(stub_argv(result), args)

    def test_unknown_flag_passes_through_when_no_auth_command_is_possible(self):
        adc = self.cloudsdk_config / "application_default_credentials.json"
        adc.write_text('{"type":"external_account"}\n', encoding="utf-8")
        args = ["--future-global-flag", "value", "config", "list"]
        result = self.run_shim(args=args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=<unset>", result.stdout)
        self.assertEqual(stub_argv(result), args)

    def test_unknown_flag_refuses_a_possible_protected_command(self):
        write_stub(self.stub_dir, "gcloud", "echo SHOULD-NOT-RUN\nexit 0")
        for args in (
            ["--future-global-flag", "value", "auth", "login"],
            ["auth", "--future-global-flag", "value", "login"],
            ["auth", "--future-global-flag", "value", "application-default", "login"],
        ):
            with self.subTest(args=args):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("ambiguous", result.stderr)
                self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
                self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_global_flag_table_matches_each_flags_value_arity(self):
        """Every flag in the shim's table is consumed with exactly the number of
        values it is listed under, so a value can never pose as a command word.

        The probe is the application-default refusal: it depends only on the command
        path, so it holds for the flags that select an identity (`--configuration`)
        or carry opaque flags (`--flags-file`) as much as for the rest."""
        value_flags = shim_array("GCLOUD_GLOBAL_VALUE_FLAGS")
        boolean_flags = shim_array("GCLOUD_GLOBAL_BOOLEAN_FLAGS")
        self.assertTrue(value_flags)
        self.assertTrue(boolean_flags)
        adc = self.cloudsdk_config / "application_default_credentials.json"
        adc.write_text('{"type":"external_account"}\n', encoding="utf-8")
        write_stub(self.stub_dir, "gcloud", "echo SHOULD-NOT-RUN\nexit 0")
        protected = ["auth", "application-default", "login"]

        for flag in value_flags:
            for args in ([flag, "VALUE", *protected], [f"{flag}=VALUE", *protected]):
                with self.subTest(flag=flag, arity=1, args=args):
                    result = self.run_shim(args=args)
                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertIn("gcloud auth login", result.stderr)
                    self.assertNotIn("SHOULD-NOT-RUN", result.stdout)

        for flag in boolean_flags:
            with self.subTest(flag=flag, arity=0):
                result = self.run_shim(args=[flag, *protected])
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("gcloud auth login", result.stderr)
                self.assertNotIn("SHOULD-NOT-RUN", result.stdout)

    def test_flags_file_refuses_implicit_box_login(self):
        flags_file = self.stub_dir / "login.yaml"
        write_stub(self.stub_dir, "gcloud", "echo SHOULD-NOT-RUN\nexit 0")
        cases = (
            (["auth", "login", f"--flags-file={flags_file}"], "--update-adc:\n"),
            (["auth", "login", "--flags-file", str(flags_file)], "--configuration: box\n"),
        )

        for args, contents in cases:
            with self.subTest(args=args):
                flags_file.write_text(contents, encoding="utf-8")
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("--configuration theorem", result.stderr)
                self.assertIn("GCLOUD_ACCOUNT=theorem", result.stderr)
                self.assertEqual(result.stderr.count("\n"), 1)
                self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
                self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_explicit_configuration_login_passes_through_flags_file(self):
        flags_file = self.stub_dir / "login.yaml"
        flags_file.write_text("--update-adc:\n", encoding="utf-8")
        args = ["--configuration", "theorem", "auth", "login", f"--flags-file={flags_file}"]
        result = self.run_shim(args=args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=<unset>", result.stdout)
        self.assertNotIn("flags-file", result.stderr)
        self.assertEqual(stub_argv(result), args)

    def test_external_adc_refusal_ignores_flags_file(self):
        adc = self.cloudsdk_config / "application_default_credentials.json"
        adc.write_text('{"type":"external_account"}\n', encoding="utf-8")
        flags_file = self.stub_dir / "login.yaml"
        flags_file.write_text("--configuration: theorem\n", encoding="utf-8")
        write_stub(self.stub_dir, "gcloud", "echo SHOULD-NOT-RUN\nexit 0")
        result = self.run_shim(
            args=["auth", "application-default", "login", f"--flags-file={flags_file}"]
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("gcloud auth login", result.stderr)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)

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
        """Its value could hide the command path, so a possible application-default
        login is refused even where the box is not this machine's identity."""
        (self.cloudsdk_config / "active_config").write_text("default\n", encoding="utf-8")
        adc = self.cloudsdk_config / "application_default_credentials.json"
        adc.write_text('{"type":"external_account"}\n', encoding="utf-8")
        result = self.run_shim(
            args=["--future-global-flag", "value", "auth", "application-default", "login"]
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("ambiguous", result.stderr)
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

    def test_adc_is_opened_only_for_the_commands_it_can_refuse(self):
        """Ordinary calls leave the ADC unopened: a runtime monitor then sees gcloud read it,
        not this wrapper. An unreadable ADC makes any read by the shim fail loudly under set -e."""
        adc = self.cloudsdk_config / "application_default_credentials.json"
        adc.write_text('{"type":"external_account"}\n', encoding="utf-8")
        adc.chmod(0)
        try:
            for args in (["config", "list"], ["auth", "print-access-token"],
                         ["--configuration", "theorem", "auth", "print-access-token"]):
                with self.subTest(args=args):
                    result = self.run_shim(args=args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(stub_argv(result), args)
            result = self.run_shim(args=["auth", "application-default", "login"])
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("ARG=", result.stdout)
        finally:
            adc.chmod(0o600)

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
