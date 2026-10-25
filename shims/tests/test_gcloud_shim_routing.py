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

import json
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Mapping
from pathlib import Path

SHIMS = Path(__file__).resolve().parent.parent
# The SDK 589.0.0 root parser's own flag table, each flag with the number of
# values it takes: written by `python3 scripts/gcloud-global-flags --json` from
# the installed SDK, and kept apart from the shim so the shim's arrays are
# checked against something they did not produce.
SDK_FLAGS = json.loads((Path(__file__).resolve().parent / "gcloud_global_flags_589.json").read_text(encoding="utf-8"))


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
            'echo "FILE_LOGGING_OFF=${CLOUDSDK_CORE_DISABLE_FILE_LOGGING:-<unset>}"\n'
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

    def write_external_account_adc(self) -> Path:
        """The box's ADC: an external_account credential in the fake config root."""
        adc = self.cloudsdk_config / "application_default_credentials.json"
        adc.write_text('{"type":"external_account"}\n', encoding="utf-8")
        return adc

    def stub_gcloud_that_must_not_run(self) -> None:
        write_stub(self.stub_dir, "gcloud", "echo SHOULD-NOT-RUN\nexit 0")

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

    def test_every_call_writes_no_log_file(self):
        """gcloud writes one log file per call and never prunes them (tens of GB a day on a busy
        box), and copies a command's output into that file, so a printed token lands there too.
        The shim turns file logging off for every command on every path, and leaves a caller's
        own setting alone."""
        for args in (["auth", "print-access-token"], ["beta", "auth", "print-identity-token"],
                     ["--verbosity", "debug", "auth", "docker-helper", "get"],
                     ["config", "list"], ["auth", "list"], ["compute", "instances", "list"]):
            with self.subTest(args=args):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("FILE_LOGGING_OFF=1", result.stdout)
                self.assertEqual(stub_argv(result), args)
        with self.subTest("account path"):
            result = self.run_shim({"GCLOUD_ACCOUNT": "theorem"}, args=["compute", "instances", "list"])
            self.assertIn("FILE_LOGGING_OFF=1", result.stdout)
        with self.subTest("caller's own setting"):
            result = self.run_shim({"CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "false"}, args=["auth", "list"])
            self.assertIn("FILE_LOGGING_OFF=false", result.stdout)

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
        self.stub_gcloud_that_must_not_run()
        result = self.run_shim(args=["auth", "login"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("gcloud config configurations create theorem --no-activate", result.stderr)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_non_auth_commands_containing_auth_words_are_untouched(self):
        self.write_external_account_adc()
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
        self.write_external_account_adc()
        self.stub_gcloud_that_must_not_run()
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

    def test_empty_configuration_selects_none_and_is_routed(self):
        """gcloud reads the last --configuration and treats an empty name as none,
        falling through to active_config, so the login would land on box."""
        cases = (
            (["--configuration=", "auth", "login", "--update-adc"], ["--configuration=", "auth", "login"]),
            (["--configuration", "", "auth", "login", "--update-adc"], ["--configuration", "", "auth", "login"]),
            (["auth", "login", "--configuration=", "--update-adc"], ["auth", "login", "--configuration="]),
            (
                ["--configuration=theorem", "auth", "login", "--configuration=", "--update-adc"],
                ["--configuration=theorem", "auth", "login", "--configuration="],
            ),
        )
        for args, expected_args in cases:
            with self.subTest(args=args):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONFIG=theorem", result.stdout)
                self.assertIn("routing auth login", result.stderr)
                self.assertEqual(stub_argv(result), expected_args)

    def test_last_non_empty_configuration_is_still_an_explicit_choice(self):
        args = ["--configuration=", "auth", "login", "--configuration=box", "--update-adc"]
        result = self.run_shim(args=args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=<unset>", result.stdout)
        self.assertNotIn("routing auth login", result.stderr)
        self.assertEqual(stub_argv(result), args)

    def test_empty_identity_variables_select_none(self):
        for extra_env in ({"GCLOUD_ACCOUNT": ""}, {"CLOUDSDK_ACTIVE_CONFIG_NAME": ""}):
            with self.subTest(extra_env=extra_env):
                result = self.run_shim(extra_env, args=["auth", "login"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONFIG=theorem", result.stdout)
                self.assertIn("routing auth login", result.stderr)

    def run_shim_without_home(
        self,
        extra_env: Mapping[str, str] | None = None,
        args: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = {key: value for key, value in self.env.items() if key != "HOME"}
        env.update(extra_env or {})
        return subprocess.run(
            [str(SHIMS / "gcloud"), *(args or ["config", "list"])],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_home_unset_runs_an_unprotectable_call_without_resolving_the_config(self):
        """With HOME unset a call no protection covers runs as it did before any of
        these protections: it reads no gcloud file, so it never needs a home. The
        failing `id` would make any attempt to find the configuration refuse."""
        write_stub(self.stub_dir, "id", "exit 1")
        for args in (
            ["config", "list"],
            ["auth", "list"],
            ["auth", "application-default", "print-access-token"],
            ["--project", "p", "compute", "instances", "list"],
        ):
            with self.subTest(args=args):
                result = self.run_shim_without_home(args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONFIG=<unset>", result.stdout)
                self.assertEqual(stub_argv(result), args)
                self.assertEqual(result.stderr, "")

    def test_home_unset_box_login_uses_cloudsdk_config_and_is_routed(self):
        """CLOUDSDK_CONFIG names the configuration before any home is looked up."""
        write_stub(self.stub_dir, "id", "exit 1")
        config = {"CLOUDSDK_CONFIG": str(self.cloudsdk_config)}
        result = self.run_shim_without_home(config, args=["auth", "login", "--update-adc"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=theorem", result.stdout)
        self.assertIn("routing auth login", result.stderr)
        self.assertEqual(stub_argv(result), ["auth", "login"])
        result = self.run_shim_without_home({**config, "GCLOUD_ACCOUNT": "default"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=default", result.stdout)

    def test_home_unset_and_no_passwd_entry_refuses_protected_commands(self):
        """No CLOUDSDK_CONFIG, no HOME and no passwd home: the shim cannot see whether
        box is active, so it refuses rather than let the login replace box."""
        write_stub(self.stub_dir, "id", "exit 1")
        self.stub_gcloud_that_must_not_run()
        for extra_env, args in (
            ({}, ["auth", "login"]),
            ({}, ["alpha", "auth", "login", "--update-adc"]),
            ({}, ["auth", "application-default", "login"]),
            ({}, ["--future-flag", "value", "auth", "login"]),
            ({"GCLOUD_ACCOUNT": "default"}, ["config", "list"]),
        ):
            with self.subTest(extra_env=extra_env, args=args):
                result = self.run_shim_without_home(extra_env, args=args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("cannot find gcloud's configuration directory", result.stderr)
                self.assertIn("Set CLOUDSDK_CONFIG or HOME", result.stderr)
                self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
                self.assertNotIn("unbound variable", result.stderr)

    def test_config_root_matches_gcloud_for_each_home(self):
        """gcloud's config dir is CLOUDSDK_CONFIG when non-empty, else
        expanduser('~')/.config/gcloud (lib/googlecloudsdk/core/config.py:231-244,
        core/util/files.py:1317-1324). The shim's resolution is compared with
        Python's own expanduser under the same environment; neither reads a file."""
        base = {"PATH": "/usr/bin:/bin"}
        for extra_env in (
            {},
            {"HOME": ""},
            {"HOME": "/"},
            {"HOME": "/fixture-home/"},
            {"HOME": "/fixture-home//"},
            {"HOME": "/fixture-home", "CLOUDSDK_CONFIG": ""},
            {"CLOUDSDK_CONFIG": "/fixture-config"},
        ):
            with self.subTest(extra_env=extra_env):
                env = {**base, **extra_env}
                shim = subprocess.run(
                    ["/bin/bash", "-c", 'source "$1"; resolve_config_root; printf "%s" "$GCLOUD_CONFIG_ROOT"', "probe", str(SHIMS / "gcloud")],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=True,
                )
                gcloud = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import os, sys\n"
                        "root = os.environ.get('CLOUDSDK_CONFIG') or "
                        "os.path.join(os.path.expanduser('~'), '.config', 'gcloud')\n"
                        "sys.stdout.write(root)",
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=True,
                )
                self.assertEqual(shim.stdout, gcloud.stdout)

    def test_flags_file_can_supply_the_last_cred_file_so_it_is_refused(self):
        """A flags file expands in place, so its empty --cred-file comes after argv's
        and wins: argv's non-empty --cred-file is not the effective one."""
        flags_file = self.stub_dir / "login.yaml"
        flags_file.write_text("--cred-file: ''\n--update-adc: true\n", encoding="utf-8")
        self.stub_gcloud_that_must_not_run()
        for args in (
            ["auth", "login", "--cred-file=/tmp/credential.json", "--flags-file", str(flags_file)],
            ["auth", "login", "--cred-file", "/tmp/credential.json", f"--flags-file={flags_file}"],
        ):
            with self.subTest(args=args):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("--flags-file", result.stderr)
                self.assertIn("--configuration theorem", result.stderr)
                self.assertNotIn("SHOULD-NOT-RUN", result.stdout)

    def test_release_track_is_stripped_once(self):
        """The SDK takes one track word, so a second is a command word, not a track."""
        args = ["alpha", "beta", "auth", "login", "--update-adc"]
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
        self.write_external_account_adc()
        self.stub_gcloud_that_must_not_run()
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
        self.write_external_account_adc()
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
        self.write_external_account_adc()
        args = ["--future-global-flag", "value", "config", "list"]
        result = self.run_shim(args=args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=<unset>", result.stdout)
        self.assertEqual(stub_argv(result), args)

    def test_unknown_flag_passes_through_when_no_reading_is_protected(self):
        """Every reading of the unknown flag's value must be able to name a protected
        command before it counts; an `auth` that no reading leads to one is not enough."""
        self.write_external_account_adc()
        for args in (
            ["auth", "--future-flag=value", "list"],
            ["auth", "--future-flag", "value", "list"],
            ["auth", "-qq", "list"],
            ["auth", "-qh", "list"],
            ["compute", "instances", "create", "auth"],
            ["--future-flag", "value", "compute", "instances", "create", "auth"],
            ["compute", "ssh", "vm", "--future-flag", "value", "--", "auth", "login"],
        ):
            with self.subTest(args=args):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONFIG=<unset>", result.stdout)
                self.assertNotIn("ambiguous", result.stderr)
                self.assertEqual(stub_argv(result), args)

    def test_many_unknown_flags_pass_through_without_protected_words_in_order(self):
        """No reading of a flag can add a word, so without `auth` followed by a protected
        operation word no number of unknown flags makes the path protected."""
        self.write_external_account_adc()
        for args in (
            ["--future-flag", "alpha"] * 12 + ["config", "list"],
            ["--future-flag", "auth"] * 12 + ["list"],
            ["--future-flag", "login"] * 6 + ["auth", "list"],
            ["--future-flag", "x"] * 40 + ["compute", "instances", "list"],
        ):
            with self.subTest(args=args[-4:], length=len(args)):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("ambiguous", result.stderr)
                self.assertEqual(stub_argv(result), args)

    def test_many_unknown_flags_still_refuse_a_reachable_protected_command(self):
        """With the words in order, every reading is weighed, however many flags
        precede them."""
        self.stub_gcloud_that_must_not_run()
        self.write_external_account_adc()
        for args in (
            ["--future-flag", "alpha"] * 12 + ["auth", "login"],
            ["--future-flag", "x"] * 40 + ["auth", "login"],
            ["auth"] + ["--future-flag", "x"] * 40 + ["application-default", "revoke"],
        ):
            with self.subTest(args=args[-4:], length=len(args)):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("ambiguous", result.stderr)
                self.assertNotIn("SHOULD-NOT-RUN", result.stdout)

    def test_thousands_of_unknown_flags_are_refused_cleanly(self):
        """The walk is a loop, not recursion: 3000 choice points before `auth login`
        are weighed exactly and refused, with no stack overflow and in little time."""
        self.stub_gcloud_that_must_not_run()
        args = ["--future-flag", "x"] * 3000 + ["auth", "login"]
        started = time.monotonic()
        result = self.run_shim(args=args)
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 1, result.stderr[-500:])
        self.assertIn("ambiguous", result.stderr)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
        self.assertLess(elapsed, 10)

    def test_unknown_flags_only_after_a_decided_path_change_nothing(self):
        """Many unknown flags after `auth login` cannot move it: the login is routed."""
        args = ["auth", "login"] + ["--future-flag", "x"] * 40
        result = self.run_shim(args=args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=theorem", result.stdout)
        self.assertEqual(stub_argv(result), args)

    def test_unknown_flag_refuses_a_possible_protected_command(self):
        self.stub_gcloud_that_must_not_run()
        adc = self.cloudsdk_config / "application_default_credentials.json"
        for args, adc_contents in (
            (["--future-global-flag", "value", "auth", "login"], None),
            (["auth", "--future-global-flag", "value", "login"], None),
            (["--future-global-flag", "auth", "login"], None),
            (["auth", "--future-global-flag", "value", "application-default", "login"], '{"type":"external_account"}\n'),
        ):
            with self.subTest(args=args):
                if adc_contents is None:
                    adc.unlink(missing_ok=True)
                else:
                    adc.write_text(adc_contents, encoding="utf-8")
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("ambiguous", result.stderr)
                self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
                self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_ambiguous_application_default_runs_where_its_refusal_would_not_apply(self):
        """With no external_account ADC the unambiguous command would run, so the
        ambiguous one does too: no protected path is reachable."""
        args = ["auth", "--future-global-flag", "value", "application-default", "login"]
        result = self.run_shim(args=args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stub_argv(result), args)

    def test_unknown_flag_with_flags_file_refuses_an_implicit_box_login(self):
        flags_file = self.stub_dir / "login.yaml"
        flags_file.write_text("--update-adc:\n", encoding="utf-8")
        self.stub_gcloud_that_must_not_run()
        for args in (
            ["--future-flag", "value", "auth", "login", f"--flags-file={flags_file}"],
            ["auth", "login", "--future-flag", "value", f"--flags-file={flags_file}"],
        ):
            with self.subTest(args=args):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("--configuration theorem", result.stderr)
                self.assertNotIn("SHOULD-NOT-RUN", result.stdout)

    def test_shim_flag_table_matches_the_sdk_589_fixture(self):
        """The arrays are the SDK's table split by value count, with nothing left out."""
        flags = SDK_FLAGS["flags"]
        self.assertEqual(SDK_FLAGS["sdk_version"], "589.0.0")
        self.assertEqual(
            sorted(shim_array("GCLOUD_GLOBAL_VALUE_FLAGS")),
            sorted(flag for flag, count in flags.items() if count == 1),
        )
        self.assertEqual(
            sorted(shim_array("GCLOUD_GLOBAL_BOOLEAN_FLAGS")),
            sorted(flag for flag, count in flags.items() if count == 0),
        )
        self.assertEqual(set(flags.values()), {0, 1})
        self.assertIn("Google Cloud SDK 589.0.0", (SHIMS / "gcloud").read_text(encoding="utf-8"))

    def test_each_sdk_flag_consumes_the_values_the_sdk_gives_it(self):
        """Driven from the fixture, not from the shim's own arrays: every flag with its
        SDK value count before `auth application-default login` must leave that path
        intact, so the refusal still fires.

        The probe is the application-default refusal because it depends only on the
        command path, so it holds for the flags that select an identity
        (`--configuration`) or carry opaque flags (`--flags-file`) as much as for the
        rest."""
        self.write_external_account_adc()
        self.stub_gcloud_that_must_not_run()
        protected = ["auth", "application-default", "login"]

        for flag, count in SDK_FLAGS["flags"].items():
            forms = [[flag, *["VALUE"] * count]]
            if count:
                forms.append([f"{flag}=VALUE"])
            for form in forms:
                for args in ([*form, *protected], ["auth", *form, "application-default", "login"]):
                    with self.subTest(flag=flag, count=count, args=args):
                        result = self.run_shim(args=args)
                        self.assertEqual(result.returncode, 1, result.stdout)
                        self.assertIn("gcloud auth login", result.stderr)
                        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)

    def test_flags_file_refuses_implicit_box_login(self):
        flags_file = self.stub_dir / "login.yaml"
        self.stub_gcloud_that_must_not_run()
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
        self.write_external_account_adc()
        flags_file = self.stub_dir / "login.yaml"
        flags_file.write_text("--configuration: theorem\n", encoding="utf-8")
        self.stub_gcloud_that_must_not_run()
        result = self.run_shim(
            args=["auth", "application-default", "login", f"--flags-file={flags_file}"]
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("gcloud auth login", result.stderr)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)

    def test_external_adc_refusal_ignores_explicit_configuration_flag(self):
        self.write_external_account_adc()
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
        self.write_external_account_adc()
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
        self.write_external_account_adc()
        for operation in ("login", "revoke"):
            with self.subTest(operation=operation):
                result = self.run_shim(args=["auth", "application-default", operation])
                self.assertEqual(result.returncode, 1)
                self.assertIn("gcloud auth login", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_adc_is_opened_only_for_the_commands_it_can_refuse(self):
        """Ordinary calls leave the ADC unopened: a runtime monitor then sees gcloud read it,
        not this wrapper. An unreadable ADC makes any read by the shim fail loudly under set -e."""
        adc = self.write_external_account_adc()
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
        self.stub_gcloud_that_must_not_run()
        result = self.run_shim({"GCLOUD_ACCOUNT": "nonexistent"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nonexistent", result.stderr)
        self.assertIn("default", result.stderr)
        self.assertIn("gws-personal", result.stderr)
        self.assertIn("theorem", result.stderr)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stderr)

    def test_traversal_values_are_rejected(self):
        self.stub_gcloud_that_must_not_run()
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
        self.stub_gcloud_that_must_not_run()
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
