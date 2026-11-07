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

import fcntl
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Mapping
from pathlib import Path

SHIMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SHIMS.parent / "scripts" / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gce_metadata_stub import MetadataStub  # noqa: E402 - found through the path above
from stdin_cases import SSH_CONNECTION, SSH_TTY, run_with_tty, run_without_tty  # noqa: E402

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


def wait_for_child(pid: int, comm: str, limit: float = 600) -> bool:
    """True once process `pid` has a child named `comm`; False when `pid` is gone first or `limit`
    seconds pass (a bound for a hang, far above what a loaded machine needs)."""
    deadline = time.monotonic() + limit
    children = Path(f"/proc/{pid}/task/{pid}/children")
    while time.monotonic() < deadline:
        try:
            if Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0] == "Z":
                return False
            kids = children.read_text(encoding="utf-8").split()
        except OSError:
            return False
        for kid in kids:
            try:
                if Path(f"/proc/{kid}/comm").read_text(encoding="utf-8").strip() == comm:
                    return True
            except OSError:
                continue
        time.sleep(0.05)
    return False


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
            # Not a Google VM, wherever the suite runs (scripts/on-google-vm): a closed port.
            "GCE_METADATA_HOST": "127.0.0.1:9",
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
                self.assertIn("--configuration <name>", result.stderr)
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
            self.assertIn("--configuration <name>", result.stderr)
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
        are weighed exactly and refused, with no stack overflow, and the work grows in
        proportion to the flags. The work is counted in the commands bash traces, not in
        seconds, which a loaded machine stretches (load 300-380 on 2026-11-03)."""
        self.stub_gcloud_that_must_not_run()
        args = ["--future-flag", "x"] * 3000 + ["auth", "login"]
        result = self.run_shim(args=args)
        self.assertEqual(result.returncode, 1, result.stderr[-500:])
        self.assertIn("ambiguous", result.stderr)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)

        def traced_commands(flags: int) -> int:
            trace = subprocess.run(["bash", "-x", str(SHIMS / "gcloud"), *(["--future-flag", "x"] * flags),
                                    "auth", "login"], capture_output=True, text=True,
                                   env={**self.env, "PS4": "+"}, check=False)
            self.assertEqual(trace.returncode, 1)
            return sum(1 for line in trace.stderr.splitlines() if line.startswith("+"))

        small, large = traced_commands(500), traced_commands(1000)
        self.assertGreater(small, 500)
        self.assertLess(large, 2.2 * small)

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
                self.assertIn("--configuration <name>", result.stderr)
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
                self.assertIn("--configuration <name>", result.stderr)
                self.assertIn("GCLOUD_ACCOUNT=<name>", result.stderr)
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

    def test_google_vm_with_box_active_refuses_application_default_login_and_revoke(self):
        """On a Google VM the box's ADC is the metadata server behind an absent well-known file,
        so the file a login would write is exactly what must not appear. A stub metadata server
        stands in for the VM's (scripts/on-google-vm reads GCE_METADATA_HOST)."""
        server = MetadataStub.start()
        self.addCleanup(server.stop)
        google = {"GCE_METADATA_HOST": server.host}
        self.stub_gcloud_that_must_not_run()
        for operation in ("login", "revoke"):
            with self.subTest(operation=operation):
                result = self.run_shim(google, args=["auth", "application-default", operation])
                self.assertEqual(result.returncode, 1)
                self.assertIn("gcloud auth login", result.stderr)
                self.assertEqual(result.stdout, "")
        # Another configuration active: not the box's identity, so it passes, as before.
        (self.cloudsdk_config / "active_config").write_text("default\n", encoding="utf-8")
        result = self.run_shim(google, args=["auth", "application-default", "login"])
        self.assertEqual(result.stdout.strip(), "SHOULD-NOT-RUN")

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

    # In an agent session no route writes or removes the well-known ADC file. On 2026-11-01T02:41Z
    # `--configuration theorem auth login --no-launch-browser --update-adc` was not routed, kept
    # --update-adc, and at 02:53Z wrote the user's own credential over the box's.
    AGENT_MARKERS = (
        {"OMP_SESSION_ID": "01a00000-0000-7000-8000-000000000000"},
        {"PI_SESSION_FILE": "/tmp/session.jsonl"},
        {"AGENTBOX_SESSION": "1"},
        # What shims/tmux puts in the pane of an agent that had no marker of its own, so every
        # refusal below holds for the pane an SSH-only agent opens (round 5 of INFRA-1538).
        {"AGENT_SHIM_AGENT": "1"},
        {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "include.path",
         "GIT_CONFIG_VALUE_0": "/home/u/.dotfiles/gh-app-routes.gitconfig"},
    )
    ADC_WRITES = (
        ({}, ["auth", "login", "--update-adc"]),
        ({}, ["--configuration", "theorem", "auth", "login", "--no-launch-browser", "--update-adc"]),
        ({}, ["--configuration=theorem", "auth", "login", "--update-adc"]),
        ({"GCLOUD_ACCOUNT": "theorem"}, ["auth", "login", "--update-adc"]),
        ({"CLOUDSDK_ACTIVE_CONFIG_NAME": "theorem"}, ["auth", "login", "--update-adc"]),
        ({}, ["auth", "login", "--cred-file=/tmp/key.json", "--update-adc"]),
        ({}, ["--configuration", "box", "auth", "login", "--cred-file", "/tmp/key.json", "--update-adc"]),
        # `auth login --flags-file …` is refused one step earlier now, by the flags-file guard
        # (test_agent_session_refuses_a_flags_file_whatever_it_holds).
        ({}, ["auth", "application-default", "login"]),
        ({}, ["beta", "auth", "application-default", "login", "--no-launch-browser"]),
        ({}, ["auth", "application-default", "revoke"]),
    )
    BREAK_GLASS_URL = "https://github.com/theorem-labs/agent-infra/blob/main/docs/break-glass.md"
    APPLIER = "infra-applier@theorem-agent-infra.iam.gserviceaccount.com"

    def assert_agent_refusal(self, result: subprocess.CompletedProcess[str]) -> None:
        """Every agent-session refusal: nothing ran, and the text names the box identity, the CI
        route and the applier, and the break-glass page, and tells the agent to run no sign-in."""
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
        self.assertIn("gcloud shim: REFUSED in an agent session", result.stderr)
        self.assertIn("box identity", result.stderr)
        self.assertIn("CI apply", result.stderr)
        self.assertIn(self.APPLIER, result.stderr)
        self.assertIn("dispatch ask", result.stderr)
        self.assertIn(self.BREAK_GLASS_URL, result.stderr)
        self.assertNotIn("gcloud auth login", result.stderr)

    def write_user_adc(self) -> Path:
        """The well-known ADC holding a user login, as on sami-rocqperf since 2026-11-01."""
        adc = self.cloudsdk_config / "application_default_credentials.json"
        adc.write_text('{"type":"authorized_user"}\n', encoding="utf-8")
        return adc

    def test_agent_session_refuses_every_route_that_writes_the_adc(self):
        adc = self.write_user_adc()
        self.stub_gcloud_that_must_not_run()
        for marker in self.AGENT_MARKERS:
            for env, args in self.ADC_WRITES:
                with self.subTest(marker=marker, env=env, args=args):
                    result = self.run_shim({**marker, **env}, args=args)
                    self.assert_agent_refusal(result)
                    self.assertIn(str(adc), result.stderr)
        self.assertEqual(adc.read_text(encoding="utf-8"), '{"type":"authorized_user"}\n')

    def test_agent_session_refusal_names_the_account_the_login_would_write(self):
        (self.configs_dir / "config_gws-personal").write_text("[core]\naccount = someone@example.com\n",
                                                             encoding="utf-8")
        self.stub_gcloud_that_must_not_run()
        result = self.run_shim(self.AGENT_MARKERS[0],
                               args=["--configuration", "gws-personal", "auth", "login",
                                     "--cred-file=/tmp/key.json", "--update-adc"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("configuration gws-personal, account someone@example.com", result.stderr)

    def test_agent_session_passes_calls_that_leave_the_adc_alone(self):
        self.write_user_adc()
        for args in (["config", "list"], ["auth", "list"], ["auth", "print-access-token"],
                     ["auth", "application-default", "print-access-token"], ["compute", "instances", "list"]):
            with self.subTest(args=args):
                result = self.run_shim(self.AGENT_MARKERS[0], args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(stub_argv(result), args)

    # Every VM an agent creates gets a run-time limit at creation, so a forgotten one stops by itself
    # (dispatch://INFRA-672/ask/a76ad180-3939-48ed-87bf-a6b716d4def0).
    UNLIMITED_CREATES = (
        ["compute", "instances", "create", "vm-1", "--zone", "us-east4-a"],
        ["--project", "p", "compute", "instances", "create", "vm-1", "--spot"],
        ["beta", "compute", "instances", "create", "vm-1"],
        ["compute", "instances", "bulk", "create", "--count", "2", "--name-pattern", "vm-#"],
        ["compute", "instances", "create", "vm-1", "--instance-termination-action=STOP"],
        # A --flags-file create is refused by the flags-file guard before this one sees it.
    )

    def test_agent_session_refuses_a_vm_create_without_a_run_limit(self):
        self.stub_gcloud_that_must_not_run()
        for marker in self.AGENT_MARKERS:
            for args in self.UNLIMITED_CREATES:
                with self.subTest(marker=marker, args=args):
                    result = self.run_shim(marker, args=args)
                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
                    self.assertIn("gcloud shim: REFUSED in an agent session", result.stderr)
                    for text in ("--max-run-duration", "--termination-time",
                                 "--instance-termination-action=STOP",
                                 "--discard-local-ssds-at-termination-timestamp=true",
                                 "https://cloud.google.com/compute/docs/instances/limit-vm-runtime",
                                 "GCLOUD_NO_RUN_LIMIT=<reason>", "set-scheduling"):
                        self.assertIn(text, result.stderr)

    def test_agent_session_passes_a_vm_create_with_a_run_limit_unchanged(self):
        cases = (
            ({}, ["compute", "instances", "create", "vm-1", "--max-run-duration=8h",
                  "--instance-termination-action=STOP"]),
            ({}, ["compute", "instances", "create", "vm-1", "--max-run-duration", "28800s",
                  "--instance-termination-action", "STOP"]),
            ({}, ["compute", "instances", "create", "vm-1", "--termination-time=2026-11-04T00:00:00Z",
                  "--instance-termination-action=STOP"]),
            ({}, ["compute", "instances", "bulk", "create", "--count", "2", "--max-run-duration=1h"]),
            ({"GCLOUD_NO_RUN_LIMIT": "model gateway, declared in code"},
             ["compute", "instances", "create", "gateway-1", "--zone", "us-east4-a"]),
        )
        for env, args in cases:
            with self.subTest(env=env, args=args):
                result = self.run_shim({**self.AGENT_MARKERS[0], **env}, args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(stub_argv(result), args)

    def test_run_limit_rule_leaves_other_compute_commands_and_the_users_shell_alone(self):
        for args in (["compute", "instances", "list"], ["compute", "instances", "start", "vm-1"],
                     ["compute", "instances", "set-scheduling", "vm-1", "--max-run-duration=8h"],
                     ["compute", "instance-templates", "create", "t-1"], ["compute", "disks", "create", "d-1"],
                     ["compute", "ssh", "vm-1", "--", "compute", "instances", "create"]):
            with self.subTest(args=args):
                result = self.run_shim(self.AGENT_MARKERS[0], args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(stub_argv(result), args)
        with self.subTest("outside an agent session"):
            args = ["compute", "instances", "create", "vm-1"]
            result = self.run_shim(args=args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(stub_argv(result), args)
        with self.subTest("an empty escape is no escape"):
            self.stub_gcloud_that_must_not_run()
            result = self.run_shim({**self.AGENT_MARKERS[0], "GCLOUD_NO_RUN_LIMIT": ""},
                                   args=["compute", "instances", "create", "vm-1"])
            self.assertEqual(result.returncode, 1)

    def test_users_own_session_keeps_its_adc_login(self):
        """Outside an agent session the user's explicit login with --update-adc reaches gcloud as he
        typed it, and a bare one under box is still routed with --update-adc dropped."""
        self.write_user_adc()
        args = ["--configuration", "theorem", "auth", "login", "--update-adc"]
        result = self.run_shim(args=args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stub_argv(result), args)
        result = self.run_shim(args=["auth", "login", "--update-adc"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=theorem", result.stdout)
        self.assertNotIn("--update-adc", stub_argv(result))

    def test_users_own_session_routes_his_plain_login_and_refuses_the_adc_login(self):
        """With no agent variable set and box active, his plain login goes to his own configuration
        without --update-adc, and an ADC login is refused while the box's ADC is external_account."""
        self.write_external_account_adc()
        result = self.run_shim(args=["auth", "login", "--update-adc"])
        self.assertEqual(result.returncode, 0, result.stderr)
        config = next(line for line in result.stdout.splitlines() if line.startswith("CONFIG="))
        self.assertNotIn(config, ("CONFIG=box", "CONFIG=<unset>"))
        self.assertEqual(stub_argv(result), ["auth", "login"])
        self.stub_gcloud_that_must_not_run()
        result = self.run_shim(args=["auth", "application-default", "login"])
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)

    # In an agent session a call whose account is the user's own login is refused unless it names,
    # with --configuration, the configuration that holds that login: the route an apply no declared
    # identity can make yet takes under Sami's standing permission, each use recorded
    # (dispatch://INFRA-6/ask/6c7b1e80-176f-40e3-967e-e5d642d501ec). On 2026-11-02T00:07Z an agent
    # shell exported CLOUDSDK_CORE_ACCOUNT=sami@theorem.dev, and every later kubectl in it created
    # GKE Jobs as him through gke-gcloud-auth-plugin's `gcloud config config-helper`.
    USER = "sami@theorem.dev"
    BOX = "dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com"

    def write_identity_configs(self) -> None:
        (self.configs_dir / "config_theorem").write_text(f"[core]\naccount = {self.USER}\n", encoding="utf-8")
        (self.configs_dir / "config_box").write_text(f"[core]\naccount = {self.BOX}\n", encoding="utf-8")

    def test_agent_session_refuses_the_users_account(self):
        self.write_identity_configs()
        self.stub_gcloud_that_must_not_run()
        cases = (
            ({"CLOUDSDK_CORE_ACCOUNT": self.USER}, ["config", "config-helper", "--format=json"],
             "CLOUDSDK_CORE_ACCOUNT in the environment"),
            ({}, ["auth", "print-access-token", f"--account={self.USER}"], "--account"),
            ({}, ["storage", "cp", "ctx.tar.gz", "gs://bucket/ctx.tar.gz", "--account", self.USER], "--account"),
            ({"CLOUDSDK_CORE_ACCOUNT": self.BOX}, ["--account", self.USER, "auth", "list"], "--account"),
            # A configuration that does not hold that login is no route to it.
            ({}, ["--configuration", "box", "--account", self.USER, "auth", "print-access-token"], "--account"),
        )
        for marker in self.AGENT_MARKERS:
            for env, args, source in cases:
                with self.subTest(marker=marker, env=env, args=args):
                    result = self.run_shim({**marker, **env}, args=args)
                    self.assert_agent_refusal(result)
                    self.assertIn(f"Identity: {self.USER}, a person's own Google login, named by {source}.",
                                  result.stderr)
                    self.assertIn("dispatch://INFRA-6/ask/6c7b1e80-176f-40e3-967e-e5d642d501ec", result.stderr)

    # A --flags-file is argv gcloud has not expanded yet: it loads YAML — a mapping, a list of
    # mappings, a JSON one-liner — expands it IN PLACE before it resolves --account,
    # --configuration, --cred-file and impersonation, and follows a flags file a flags file
    # names (calliope/cli.py:92-108,125-130,985). Three rounds of INFRA-1538 found a reader here
    # missing one valid shape after another, each of which ran as the person while the shim
    # named the machine identity. In an agent session the flag is refused outright, and nothing
    # is parsed; a person's own session is untouched.
    def test_agent_session_refuses_a_flags_file_whatever_it_holds(self):
        self.write_identity_configs()
        self.stub_gcloud_that_must_not_run()
        flags_file = self.stub_dir / "flags.yaml"
        contents = (f"--account: {self.USER}\n", f'"--account": {self.USER}\n',
                    f'{{"--account": "{self.USER}"}}\n', f"- --account: {self.USER}\n",
                    f"--account: {self.BOX}\n", "--project: p\n", "")
        commands = (["auth", "print-access-token", f"--flags-file={flags_file}"],
                    ["config", "list", "--flags-file", str(flags_file)],
                    ["auth", "login", f"--flags-file={flags_file}"],
                    ["compute", "instances", "create", "vm-1", f"--flags-file={flags_file}"])
        for marker in self.AGENT_MARKERS:
            for text in contents:
                for args in commands:
                    with self.subTest(marker=sorted(marker), text=text, args=args[0]):
                        flags_file.write_text(text, encoding="utf-8")
                        result = self.run_shim(marker, args=args)
                        self.assert_agent_refusal(result)
                        self.assertIn("Identity: unknown.", result.stderr)
                        self.assertIn("--flags-file", result.stderr)

    def test_a_flags_file_this_shim_cannot_read_is_refused_too(self):
        self.stub_gcloud_that_must_not_run()
        result = self.run_shim(self.AGENT_MARKERS[0],
                               args=["config", "list", "--flags-file", str(self.stub_dir / "gone.yaml")])
        self.assert_agent_refusal(result)

    def test_a_persons_own_flags_file_runs(self):
        self.write_identity_configs()
        flags_file = self.stub_dir / "flags.yaml"
        flags_file.write_text(f"--account: {self.USER}\n", encoding="utf-8")
        args = ["config", "list", f"--flags-file={flags_file}"]
        result = self.run_shim(args=args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stub_argv(result), args)

    def test_agent_session_refusal_without_a_holding_configuration_routes_to_a_person(self):
        self.stub_gcloud_that_must_not_run()
        result = self.run_shim({**self.AGENT_MARKERS[0], "CLOUDSDK_CORE_ACCOUNT": "someone@example.com"},
                               args=["auth", "print-access-token"])
        self.assert_agent_refusal(result)
        self.assertNotIn("--configuration", result.stderr.split("Instead:")[1])

    def test_agent_session_passes_the_recorded_route_and_machine_accounts(self):
        self.write_identity_configs()
        cases = (
            ({"CLOUDSDK_CORE_ACCOUNT": self.USER}, ["--configuration", "theorem", "auth", "print-access-token"]),
            ({}, ["--configuration=theorem", "--account", self.USER, "projects", "list"]),
            ({}, ["--configuration", "theorem", "auth", "print-access-token"]),
            ({"GCLOUD_ACCOUNT": "theorem"}, ["auth", "print-access-token"]),
            ({"CLOUDSDK_CORE_ACCOUNT": self.BOX}, ["auth", "print-access-token"]),
            ({}, ["auth", "print-access-token", f"--account={self.BOX}"]),
            ({}, ["auth", "print-access-token", f"--impersonate-service-account={self.APPLIER}"]),
            ({}, ["config", "config-helper", "--format=json"]),
            ({"CLOUDSDK_CORE_ACCOUNT": ""}, ["auth", "list"]),
            ({}, ["auth", "login", "--cred-file=/tmp/key.json"]),
        )
        for env, args in cases:
            with self.subTest(env=env, args=args):
                result = self.run_shim({**self.AGENT_MARKERS[0], **env}, args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(stub_argv(result), args)

    def test_users_own_session_keeps_his_account(self):
        self.write_identity_configs()
        for env, args in (({"CLOUDSDK_CORE_ACCOUNT": self.USER}, ["auth", "print-access-token"]),
                          ({}, ["auth", "print-access-token", "--account", self.USER]),
                          ({}, ["--configuration", "theorem", "auth", "print-access-token"]),
                          ({"GCLOUD_ACCOUNT": "theorem"}, ["auth", "print-access-token"])):
            with self.subTest(env=env, args=args):
                result = self.run_shim(env, args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(stub_argv(result), args)

    # In an agent session with KUBECONFIG unset, get-credentials writes a kubeconfig of the cluster's
    # own, never ~/.kube/config: on 2026-11-02 an agent's get-credentials for grade-us-west1 switched
    # that shared file's current-context and every other session's builds-* `kubectl create` failed.
    def stub_gcloud_printing_kubeconfig(self) -> None:
        write_stub(self.stub_dir, "gcloud", 'echo "KUBECONFIG=${KUBECONFIG-<unset>}"\nprintf "ARG=%s\\n" "$@"')

    def test_agent_get_credentials_writes_a_kubeconfig_of_the_clusters_own(self):
        self.stub_gcloud_printing_kubeconfig()
        own = self.fake_home / ".kube" / "config.d"
        cases = (
            (["container", "clusters", "get-credentials", "grade-us-west1", "--region", "us-west1",
              "--project", "p"], "grade-us-west1"),
            (["container", "clusters", "get-credentials", "--zone", "us-west1-a", "builds"], "builds"),
            (["--project", "p", "beta", "container", "clusters", "get-credentials", "-z", "z", "c1"], "c1"),
            (["container", "clusters", "get-credentials",
              "projects/p/locations/us-west1/clusters/grade-us-west1"], "grade-us-west1"),
        )
        for marker in self.AGENT_MARKERS:
            for args, cluster in cases:
                with self.subTest(marker=marker, args=args):
                    result = self.run_shim(marker, args=args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f"KUBECONFIG={own / cluster}", result.stdout)
                    self.assertIn(f"export KUBECONFIG={own / cluster}", result.stderr)
                    self.assertEqual(stub_argv(result), args)
        self.assertEqual(own.stat().st_mode & 0o777, 0o700)

    def test_get_credentials_keeps_a_chosen_kubeconfig_and_a_persons_shell(self):
        self.stub_gcloud_printing_kubeconfig()
        args = ["container", "clusters", "get-credentials", "grade-us-west1", "--region", "us-west1"]
        cases = (
            ({**self.AGENT_MARKERS[0], "KUBECONFIG": "/tmp/mine"}, "KUBECONFIG=/tmp/mine"),
            ({}, "KUBECONFIG=<unset>"),
        )
        for env, expected in cases:
            with self.subTest(env=env):
                result = self.run_shim(env, args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)
        result = self.run_shim(self.AGENT_MARKERS[0], args=["container", "clusters", "list"])
        self.assertIn("KUBECONFIG=<unset>", result.stdout)

    def test_agent_get_credentials_without_a_readable_cluster_is_refused(self):
        self.stub_gcloud_that_must_not_run()
        for args in (["container", "clusters", "get-credentials", "--region", "us-west1"],
                     ["container", "clusters", "get-credentials", "a", "b"],
                     ["container", "clusters", "get-credentials", "Bad_Name"]):
            with self.subTest(args=args):
                result = self.run_shim(self.AGENT_MARKERS[0], args=args)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
                self.assertIn("Set KUBECONFIG", result.stderr)


class GcloudAzureCredentialOnGoogleVm(unittest.TestCase):
    """A config dir copied from an Azure box to a Google VM keeps the Azure box credential gcloud
    stored, and gcloud reads it before the metadata server: LINUX-1248's own CLOUDSDK_CONFIG, moved
    from sami-rocqperf to sami-devbox on 2026-11-03, failed every call with "Unable to retrieve
    Identity Pool subject token". On a Google VM the shim removes that entry from whichever dir the
    call uses, and nothing else; elsewhere it changes nothing."""

    ACCOUNT = "dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com"
    PERSON = "someone@example.com"
    AZURE = {"type": "external_account", "audience": "//iam.googleapis.com/projects/1/locations/global/"
             "workloadIdentityPools/azure/providers/entra", "token_url": "https://sts.googleapis.com/v1/token",
             "credential_source": {"url": "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01",
                                   "headers": {"Metadata": "True"}}}
    # Another federation's entry is not the Azure box's, so it stays.
    AWS = {"type": "external_account", "credential_source": {"url": "http://169.254.169.254/latest/meta-data/iam"}}
    USER_LOGIN = {"type": "authorized_user", "refresh_token": "fake"}

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        write_stub(root, "gcloud", 'echo "REAL-GCLOUD-RAN"')
        self.config = root / "lane-gcloud"
        self.config.mkdir()
        with sqlite3.connect(self.config / "credentials.db") as db:
            db.execute('CREATE TABLE "credentials" (account_id TEXT PRIMARY KEY, value BLOB)')
            for account, value in ((self.ACCOUNT, self.AZURE), ("aws@x.iam.gserviceaccount.com", self.AWS),
                                   (self.PERSON, self.USER_LOGIN)):
                db.execute("INSERT INTO credentials VALUES (?, ?)", (account, json.dumps(value)))
        with sqlite3.connect(self.config / "access_tokens.db") as db:
            db.execute('CREATE TABLE "access_tokens" (account_id TEXT PRIMARY KEY, access_token TEXT)')
            db.executemany("INSERT INTO access_tokens VALUES (?, 'cached')", [(self.ACCOUNT,), (self.PERSON,)])
        self.env = {"PATH": f"{root}:{SHIMS}:/usr/bin:/bin", "HOME": str(root / "home"),
                    "CLOUDSDK_CONFIG": str(self.config)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_shim(self, extra_env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in {**self.env, **extra_env}.items() if v is not None}
        return subprocess.run([str(SHIMS / "gcloud"), "auth", "print-access-token"], capture_output=True,
                              text=True, env=env, check=False)

    def rows(self, db: str, table: str) -> set[str]:
        with sqlite3.connect(self.config / db) as conn:
            return {account for (account,) in conn.execute(f"SELECT account_id FROM {table}")}

    def test_on_a_google_vm_the_azure_entry_goes_and_nothing_else(self) -> None:
        server = MetadataStub.start()
        self.addCleanup(server.stop)
        result = self.run_shim({"GCE_METADATA_HOST": server.host})
        self.assertEqual(result.returncode, 0, result.stderr)
        # stdout is gcloud's alone: a token printed there is read by callers.
        self.assertEqual(result.stdout, "REAL-GCLOUD-RAN\n")
        self.assertIn(f"gcloud shim: removed the stored external_account credential of {self.ACCOUNT} (an Azure one)",
                      result.stderr)
        self.assertEqual(self.rows("credentials.db", "credentials"), {"aws@x.iam.gserviceaccount.com", self.PERSON})
        self.assertEqual(self.rows("access_tokens.db", "access_tokens"), {self.PERSON})
        # No copy of the entry stays in the file, so the next call stops at the shim's grep.
        self.assertNotIn(b"/metadata/identity/oauth2/token", (self.config / "credentials.db").read_bytes())
        again = self.run_shim({"GCE_METADATA_HOST": server.host})
        self.assertEqual((again.returncode, again.stdout, again.stderr), (0, "REAL-GCLOUD-RAN\n", ""))

    def test_the_default_config_dir_is_repaired_too(self) -> None:
        server = MetadataStub.start()
        self.addCleanup(server.stop)
        home_config = Path(self.env["HOME"]) / ".config" / "gcloud"
        home_config.parent.mkdir(parents=True)
        self.config.rename(home_config)
        self.config = home_config
        result = self.run_shim({"GCE_METADATA_HOST": server.host, "CLOUDSDK_CONFIG": None})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(self.ACCOUNT, self.rows("credentials.db", "credentials"))

    def test_off_a_google_vm_nothing_changes(self) -> None:
        before = (self.config / "credentials.db").read_bytes()
        # A closed port: on-google-vm says no. Unset: an Azure box, where the DMI name is not Google's.
        for host in ("127.0.0.1:9", None):
            with self.subTest(host=host):
                result = self.run_shim({"GCE_METADATA_HOST": host})
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "REAL-GCLOUD-RAN\n", ""))
                self.assertEqual((self.config / "credentials.db").read_bytes(), before)

    def test_a_store_it_cannot_change_is_reported_and_the_call_runs(self) -> None:
        server = MetadataStub.start()
        self.addCleanup(server.stop)
        (self.config / "credentials.db").chmod(0o400)
        self.config.chmod(0o500)
        try:
            result = self.run_shim({"GCE_METADATA_HOST": server.host})
        finally:
            self.config.chmod(0o700)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "REAL-GCLOUD-RAN\n")
        self.assertIn("gcloud shim: gcloud-drop-azure-credential: cannot change", result.stderr)

    def record_greps(self) -> Path:
        """A grep that notes its arguments and is then the real grep, ahead of the shim on PATH."""
        log = Path(self.tmp.name) / "grep.log"
        stub_dir = Path(self.tmp.name) / "grep-stub"
        stub_dir.mkdir()
        write_stub(stub_dir, "grep", f'printf "%s\\n" "$*" >>"{log}"\nexec /usr/bin/grep "$@"')
        self.env["PATH"] = f"{stub_dir}:{self.env['PATH']}"
        return log

    def store_reads(self, log: Path) -> list[str]:
        if not log.exists():
            return []
        return [line for line in log.read_text().splitlines() if "credentials.db" in line]

    def set_azure_entry(self, present: bool) -> None:
        db = sqlite3.connect(self.config / "credentials.db", isolation_level=None)
        try:
            if present:
                db.execute("INSERT INTO credentials VALUES (?, ?)", (self.ACCOUNT, json.dumps(self.AZURE)))
            else:
                db.execute("DELETE FROM credentials WHERE account_id = ?", (self.ACCOUNT,))
            db.execute("VACUUM")
        finally:
            db.close()

    def test_an_unchanged_store_is_never_read_again(self) -> None:
        """The grep opened the credential store before every gcloud call, which the CWS rule
        theorem_gcloud_credential_read reported 109 times in a week (2026-10-30..11-06). A store
        whose mtime, size and inode are the ones the last check saw is not opened at all."""
        log = self.record_greps()
        first = self.run_shim({"GCE_METADATA_HOST": "127.0.0.1:9"})
        self.assertEqual((first.returncode, first.stdout), (0, "REAL-GCLOUD-RAN\n"), first.stderr)
        self.assertEqual(len(self.store_reads(log)), 1, self.store_reads(log))
        for run in range(3):
            with self.subTest(run=run):
                again = self.run_shim({"GCE_METADATA_HOST": "127.0.0.1:9"})
                self.assertEqual((again.returncode, again.stdout), (0, "REAL-GCLOUD-RAN\n"), again.stderr)
        self.assertEqual(len(self.store_reads(log)), 1, self.store_reads(log))

    def test_a_changed_store_is_read_again_and_its_azure_entry_still_goes(self) -> None:
        """What the marker remembers is one store's bytes. A store that changed after a check that
        found no Azure entry is checked again, and the entry it gained is removed as before."""
        server = MetadataStub.start()
        self.addCleanup(server.stop)
        self.set_azure_entry(False)
        log = self.record_greps()
        clean = self.run_shim({"GCE_METADATA_HOST": server.host})
        self.assertEqual((clean.returncode, clean.stdout, clean.stderr), (0, "REAL-GCLOUD-RAN\n", ""))
        self.assertEqual(len(self.store_reads(log)), 1, self.store_reads(log))

        self.set_azure_entry(True)
        changed = self.run_shim({"GCE_METADATA_HOST": server.host})
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertEqual(changed.stdout, "REAL-GCLOUD-RAN\n")
        self.assertEqual(len(self.store_reads(log)), 2, self.store_reads(log))
        self.assertIn(f"gcloud shim: removed the stored external_account credential of {self.ACCOUNT} (an Azure one)",
                      changed.stderr)
        self.assertEqual(self.rows("credentials.db", "credentials"), {"aws@x.iam.gserviceaccount.com", self.PERSON})


class GcloudStorageParallelism(unittest.TestCase):
    """`gcloud storage` sizes its worker pool from the core count: 16 processes of 4 threads on a
    64-core box. The shim gives a storage command a small process count unless the caller chose
    one, in the environment, in the configuration the call runs under, or in the SDK's installation
    properties. The thread count is the SDK's own choice and the caller's, never the shim's."""

    PROCESSES = shim_array("GCLOUD_STORAGE_PROCESS_COUNT")[0]
    UNSET = "<unset>"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # The SDK layout: <sdk>/bin/gcloud, and its installation properties at <sdk>/properties.
        self.sdk = self.root / "sdk"
        (self.sdk / "bin").mkdir(parents=True)
        write_stub(
            self.sdk / "bin",
            "gcloud",
            'echo "PROCESSES=${CLOUDSDK_STORAGE_PROCESS_COUNT-<unset>}"\n'
            'echo "THREADS=${CLOUDSDK_STORAGE_THREAD_COUNT-<unset>}"',
        )
        self.home = self.root / "home"
        self.configs = self.home / ".config" / "gcloud" / "configurations"
        self.configs.mkdir(parents=True)
        for name in ("box", "theorem"):
            (self.configs / f"config_{name}").write_text("[core]\naccount = a@b.iam.gserviceaccount.com\n",
                                                         encoding="utf-8")
        (self.home / ".config" / "gcloud" / "active_config").write_text("box\n", encoding="utf-8")
        self.env = {"PATH": f"{self.sdk / 'bin'}:{SHIMS}:/usr/bin:/bin", "HOME": str(self.home),
                    "GCE_METADATA_HOST": "127.0.0.1:9"}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_shim(self, args: list[str], extra_env: Mapping[str, str] | None = None) -> tuple[str, str]:
        env = {**self.env, **(extra_env or {})}
        result = subprocess.run([str(SHIMS / "gcloud"), *args], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines())
        return values["PROCESSES"], values["THREADS"]

    def test_the_default_is_small(self):
        self.assertIn(int(self.PROCESSES), range(1, 9))

    def test_storage_commands_get_the_default(self):
        for args in (["storage", "cp", "-r", "d", "gs://b/d"], ["storage", "rsync", "gs://b/d", "d"],
                     ["alpha", "storage", "ls", "gs://b"], ["--project", "p", "storage", "cat", "gs://b/o"],
                     ["--configuration", "theorem", "storage", "rm", "gs://b/o"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_shim(args), (self.PROCESSES, self.UNSET))

    def test_other_commands_are_untouched(self):
        for args in (["config", "list"], ["compute", "instances", "create", "storage"],
                     ["--project", "storage", "config", "list"], ["auth", "print-access-token"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_shim(args), (self.UNSET, self.UNSET))

    def test_the_callers_environment_is_kept(self):
        cases = (
            ({"CLOUDSDK_STORAGE_PROCESS_COUNT": "32"}, ("32", self.UNSET)),
            ({"CLOUDSDK_STORAGE_THREAD_COUNT": "1"}, (self.PROCESSES, "1")),
            ({"CLOUDSDK_STORAGE_PROCESS_COUNT": "", "CLOUDSDK_STORAGE_THREAD_COUNT": "8"}, ("", "8")),
        )
        for env, expected in cases:
            with self.subTest(env=env):
                self.assertEqual(self.run_shim(["storage", "cp", "a", "gs://b/a"], env), expected)

    def test_a_configured_value_is_kept(self):
        """The environment outranks a configuration, so the shim sets nothing there either."""
        (self.configs / "config_box").write_text("[core]\nprocess_count = 1\n[storage]\n  process_count=24\n",
                                                 encoding="utf-8")
        (self.configs / "config_theorem").write_text("[storage]\nthread_count = 2\n[core]\nprocess_count = 3\n",
                                                     encoding="utf-8")
        cases = (
            ([], {}, self.UNSET),
            (["--configuration", "theorem"], {}, self.PROCESSES),
            # The last --configuration is empty, so it names none and active_config's box decides.
            (["--configuration=theorem", "--configuration="], {}, self.UNSET),
            ([], {"CLOUDSDK_ACTIVE_CONFIG_NAME": "theorem"}, self.PROCESSES),
            ([], {"GCLOUD_ACCOUNT": "theorem"}, self.PROCESSES),
            ([], {"CLOUDSDK_CONFIG": str(self.root / "empty")}, self.PROCESSES),
        )
        for flags, env, expected in cases:
            with self.subTest(flags=flags, env=env):
                self.assertEqual(self.run_shim([*flags, "storage", "cp", "a", "gs://b/a"], env),
                                 (expected, self.UNSET))

    def test_an_installation_property_is_kept(self):
        (self.sdk / "properties").write_text("[storage]\nprocess_count = 2\n", encoding="utf-8")
        self.assertEqual(self.run_shim(["storage", "ls"]), (self.UNSET, self.UNSET))

    def test_without_a_home_the_storage_call_still_runs(self):
        """No HOME and no passwd entry: no configuration to read, and nothing to refuse."""
        write_stub(self.sdk / "bin", "id", "exit 1")
        env = {k: v for k, v in self.env.items() if k != "HOME"}
        result = subprocess.run([str(SHIMS / "gcloud"), "storage", "ls"], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"PROCESSES={self.PROCESSES}", result.stdout)


LOCK_CRASH = "ERROR: gcloud crashed (OperationalError): database is locked"
# What a `gcloud storage` task-graph worker logs when it finds credentials.db locked, after the
# progress dots gcloud writes to a stderr that is no terminal (reproduced on 2026-11-02).
LOCK_TASK_ERROR = "ERROR: database is locked"


class GcloudLockedStoreRetry(unittest.TestCase):
    """The SDK crashes when another gcloud holds its credentials.db past the 5 s busy timeout.
    The shim runs a read-only command again on exactly that crash, a bounded number of times,
    and shows only the attempt that counts."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.stub_dir = Path(self.tmp.name)
        self.calls = self.stub_dir / "calls"
        # The backoff sleeps; a stub keeps the tests fast and records each delay.
        write_stub(self.stub_dir, "sleep", f'echo "$1" >> "{self.stub_dir}/sleeps"')
        home = self.stub_dir / "home"
        (home / ".config" / "gcloud" / "configurations").mkdir(parents=True)
        self.env = {"PATH": f"{self.stub_dir}:{SHIMS}:/usr/bin:/bin", "HOME": str(home)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def stub_gcloud_crashing(self, times: int, crash: str = LOCK_CRASH, partial: bool = True) -> None:
        """A gcloud whose first `times` calls print partial output (stdout only when `partial`)
        and crash, then succeed."""
        partial_out = 'echo "PARTIAL-$n"; ' if partial else ""
        write_stub(
            self.stub_dir,
            "gcloud",
            f'n=$(( $(cat "{self.calls}" 2>/dev/null || echo 0) + 1 )); echo "$n" > "{self.calls}"\n'
            f'if (( n <= {times} )); then {partial_out}'
            f'echo "WARNING: Could not store access token in cache: database is locked" >&2; '
            f'echo "{crash}" >&2; exit 1; fi\n'
            'echo "OK-$n"; echo "NOTE-$n" >&2',
        )

    def run_shim(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(SHIMS / "gcloud"), *args], capture_output=True, text=True, env=self.env)

    def call_count(self) -> int:
        return int(self.calls.read_text(encoding="utf-8"))

    def test_locked_store_crash_is_run_again_and_only_the_last_attempt_shows(self):
        for args in (["config", "config-helper", "--format=json"], ["auth", "print-access-token"],
                     ["--project", "p", "beta", "auth", "print-identity-token"],
                     ["config", "configurations", "describe", "box"], ["storage", "sign-url", "gs://b/o"]):
            with self.subTest(args=args):
                self.calls.unlink(missing_ok=True)
                self.stub_gcloud_crashing(times=1)
                result = self.run_shim(args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "OK-2\n")
                self.assertNotIn(LOCK_CRASH, result.stderr)
                self.assertIn("NOTE-2", result.stderr)
                self.assertIn("attempt 1;", result.stderr)
                self.assertEqual(self.call_count(), 2)

    def test_the_storage_task_error_is_a_locked_store_too(self):
        self.stub_gcloud_crashing(times=1, crash=f"....{LOCK_TASK_ERROR}")
        result = self.run_shim(["auth", "print-access-token"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "OK-2\n")
        self.assertEqual(self.call_count(), 2)

    def test_storage_transfers_and_reads_are_run_again_on_either_text(self):
        """cp and rsync give each object the same bytes on every attempt, and ls, cat, du and the
        objects/buckets reads change nothing. stdout goes through as it comes; stderr shows each
        attempt, the crashed one included."""
        source = self.stub_dir / "src"
        source.mkdir()
        (source / "f.txt").write_text("x\n", encoding="utf-8")
        commands = (
            ["storage", "cp", str(source / "f.txt"), "gs://b/f.txt"],
            ["storage", "cp", "-r", str(source), "gs://b/d", "--no-clobber"],
            ["storage", "cp", "gs://b/f.txt", str(self.stub_dir / "dl.txt")],
            ["storage", "rsync", str(source), "gs://b/d", "--recursive"],
            ["--project", "p", "storage", "ls", "-l", "gs://b/"],
            ["storage", "cat", "gs://b/f.txt"],
            ["storage", "du", "-s", "gs://b"],
            ["storage", "objects", "describe", "gs://b/f.txt"],
            ["storage", "buckets", "list"],
        )
        for args in commands:
            for crash in (LOCK_CRASH, LOCK_TASK_ERROR, f"..........{LOCK_TASK_ERROR}"):
                with self.subTest(args=args, crash=crash):
                    self.calls.unlink(missing_ok=True)
                    self.stub_gcloud_crashing(times=2, crash=crash, partial=False)
                    result = self.run_shim(args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "OK-3\n")
                    self.assertEqual(result.stderr.count(crash), 2)
                    self.assertIn("attempt 2;", result.stderr)
                    self.assertIn("NOTE-3", result.stderr)
                    self.assertEqual(self.call_count(), 3)

    DEADLINE = 60

    def bound_retries(self) -> None:
        """A clock that only the stub `sleep` moves, by the delay the shim asked for
        (GCLOUD_LOCK_RETRY_CLOCK), so the attempts that fit before the deadline follow from the
        backoff alone and not from the machine's load: with a real clock, load 300-380 let only
        4-6 attempts into 10 s (2026-11-03). Each delay of attempt a is under 0.6*a s and at most
        6 s, so 7 repeats take at most 16.8 s of the clock plus 1 s of rounding each, well inside
        60 s; each delay is at least 0.2*a s (2 s once capped), so the attempts are few."""
        self.env["GCLOUD_LOCK_RETRY_SECONDS"] = str(self.DEADLINE)
        self.clock_ms = self.stub_dir / "clock-ms"
        self.clock_ms.write_text("0\n", encoding="utf-8")
        write_stub(self.stub_dir, "fake-clock", f'echo $(( $(cat "{self.clock_ms}") / 1000 ))')
        self.env["GCLOUD_LOCK_RETRY_CLOCK"] = str(self.stub_dir / "fake-clock")
        # The shim asks for <s>.<ms> seconds; the stub adds that many milliseconds to the clock.
        write_stub(self.stub_dir, "sleep",
                   f'echo "$1" >> "{self.stub_dir}/sleeps"; ms=${{1/./}}; '
                   f'echo $(( $(cat "{self.clock_ms}") + 10#$ms )) > "{self.clock_ms}"')

    def assert_bounded(self, calls: int) -> None:
        """More attempts than the old limit of 6, none begun past the deadline, and a count the
        smallest delays allow: 0.2*a s each until the 6 s cap, then at least 2 s."""
        self.assertGreater(calls, 6)
        self.assertLessEqual(calls, 30)
        self.assertLessEqual(int(self.clock_ms.read_text(encoding="utf-8")), self.DEADLINE * 1000)

    def test_storage_retry_is_bounded(self):
        self.bound_retries()
        self.stub_gcloud_crashing(times=1000, crash=LOCK_TASK_ERROR, partial=False)
        result = self.run_shim(["storage", "rsync", "gs://b/d", str(self.stub_dir)])
        self.assertEqual(result.returncode, 1)
        self.assert_bounded(self.call_count())
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.count(LOCK_TASK_ERROR), self.call_count())

    def test_a_store_held_past_the_old_six_attempts_is_waited_for(self):
        """At 00:38Z on 2026-11-03 the store stayed locked for about two minutes and 67 calls gave
        up after 6 attempts in about 40 s. A call keeps trying until GCLOUD_LOCK_RETRY_SECONDS."""
        for args in (["config", "config-helper"], ["storage", "cat", "gs://b/o"]):
            with self.subTest(args=args):
                self.calls.unlink(missing_ok=True)
                self.stub_gcloud_crashing(times=9, crash=LOCK_TASK_ERROR, partial=False)
                result = self.run_shim(args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "OK-10\n")
                self.assertEqual(self.call_count(), 10)

    def test_a_repeat_attempt_waits_for_a_slot_and_gcloud_does_not_hold_it(self):
        """With every slot held, a repeat attempt waits; it runs once a slot is free, and the
        gcloud it runs has no descriptor on the slot."""
        self.env["GCLOUD_LOCK_RETRY_SLOTS"] = "1"
        slots = Path(self.env["HOME"]) / ".config" / "gcloud" / ".gcloud-shim-retry-slots"
        slots.mkdir()
        fds = self.stub_dir / "fds"
        write_stub(
            self.stub_dir,
            "gcloud",
            f'n=$(( $(cat "{self.calls}" 2>/dev/null || echo 0) + 1 )); echo "$n" > "{self.calls}"\n'
            f'ls /proc/$$/fd > "{fds}"\n'
            f'if (( n == 1 )); then echo "{LOCK_CRASH}" >&2; exit 1; fi\necho OK',
        )
        with open(slots / "0", "a") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            proc = subprocess.Popen([str(SHIMS / "gcloud"), "auth", "print-access-token"], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, env=self.env)
            # Waiting means the shim's flock(1) child is blocked on the held slot: wait for that
            # child, however long a loaded machine takes to reach it, rather than for a fixed time.
            self.assertTrue(wait_for_child(proc.pid, "flock"), "the repeat attempt never waited for the slot")
            self.assertIsNone(proc.poll())
            self.assertEqual(self.call_count(), 1)
        out, err = proc.communicate(timeout=600)
        self.assertEqual((proc.returncode, out), (0, "OK\n"), err)
        self.assertEqual(self.call_count(), 2)
        self.assertNotIn("9", fds.read_text(encoding="utf-8").split())

    def test_a_slot_held_past_the_deadline_gives_up_with_the_last_attempt(self):
        self.env["GCLOUD_LOCK_RETRY_SLOTS"] = "1"
        self.env["GCLOUD_LOCK_RETRY_SECONDS"] = "2"
        slots = Path(self.env["HOME"]) / ".config" / "gcloud" / ".gcloud-shim-retry-slots"
        slots.mkdir()
        self.stub_gcloud_crashing(times=1)
        with open(slots / "0", "a") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            result = self.run_shim(["auth", "print-access-token"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.call_count(), 1)
        self.assertEqual(result.stdout, "PARTIAL-1\n")
        self.assertIn(LOCK_CRASH, result.stderr)

    def test_a_storage_attempt_that_wrote_stdout_is_not_run_again(self):
        """Its bytes are already downstream; a second attempt would write them twice."""
        self.stub_gcloud_crashing(times=1, crash=LOCK_TASK_ERROR, partial=True)
        result = self.run_shim(["storage", "cat", "gs://b/f.txt"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.call_count(), 1)
        self.assertEqual(result.stdout, "PARTIAL-1\n")
        self.assertIn(LOCK_TASK_ERROR, result.stderr)

    def test_storage_stdout_is_streamed_byte_for_byte(self):
        """`storage cat` of a large object into a pipe must not wait for the whole object: the
        stub's first bytes reach the reader while the stub still runs, and every byte, a NUL
        first included, arrives unchanged."""
        go = self.stub_dir / "go"
        write_stub(
            self.stub_dir,
            "gcloud",
            f"printf '\\0\\001first\\n'; while [[ ! -e {go} ]]; do /bin/sleep 0.05; done; printf 'rest\\377'",
        )
        proc = subprocess.Popen([str(SHIMS / "gcloud"), "storage", "cat", "gs://b/o"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=self.env)
        try:
            assert proc.stdout is not None
            self.assertEqual(proc.stdout.read(8), b"\x00\x01first\n")
            go.touch()
            out, err = proc.communicate(timeout=600)
        finally:
            go.touch()
            proc.kill()
            proc.wait()
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(out, b"rest\xff")

    def test_storage_status_passes_through(self):
        write_stub(self.stub_dir, "gcloud", 'echo "ERROR: (gcloud.storage.cp) not found" >&2; exit 2')
        result = self.run_shim(["storage", "cp", "gs://b/o", str(self.stub_dir / "o")])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "ERROR: (gcloud.storage.cp) not found\n")

    def test_storage_calls_that_remove_or_read_stdin_run_once(self):
        fifo = self.stub_dir / "fifo"
        os.mkfifo(fifo)
        source = self.stub_dir / "f.txt"
        source.write_text("x\n", encoding="utf-8")
        cases = (
            ["storage", "rsync", str(self.stub_dir), "gs://b/d", "--delete-unmatched-destination-objects"],
            ["storage", "mv", "gs://b/o", "gs://b/p"],
            ["storage", "rm", "gs://b/o"],
            ["storage", "cp", "-I", "gs://b/d/"],
            ["storage", "cp", "-rI", "gs://b/d/"],
            ["storage", "cp", "--read-paths-from-stdin", "gs://b/d/"],
            ["storage", "cp", "-", "gs://b/o"],
            ["storage", "cp", "gs://b/o", "-"],
            ["storage", "cp", str(fifo), "gs://b/o"],
            ["storage", "cp", "/dev/stdin", "gs://b/o"],
            ["storage", "objects", "update", "gs://b/o", "--storage-class=NEARLINE"],
            ["storage", "buckets", "create", "gs://b"],
            ["storage", "hash", str(source)],
        )
        for args in cases:
            with self.subTest(args=args):
                self.calls.unlink(missing_ok=True)
                self.stub_gcloud_crashing(times=1, crash=LOCK_TASK_ERROR, partial=False)
                result = self.run_shim(args)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.call_count(), 1)
                self.assertIn(LOCK_TASK_ERROR, result.stderr)
                self.assertNotIn("gcloud shim:", result.stderr)

    def test_storage_other_errors_run_once(self):
        for crash in ("ERROR: (gcloud.storage.cp) database is locked", "WARNING: database is locked",
                      "ERROR: database is locked: gs://b/o", "ERROR: gcloud crashed (OperationalError): disk I/O error"):
            with self.subTest(crash=crash):
                self.calls.unlink(missing_ok=True)
                self.stub_gcloud_crashing(times=1, crash=crash, partial=False)
                result = self.run_shim(["storage", "cp", "a", "gs://b/o"])
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.call_count(), 1)
                self.assertIn(crash, result.stderr)

    def test_retry_is_bounded_and_the_last_crash_passes_through(self):
        self.bound_retries()
        self.stub_gcloud_crashing(times=1000)
        result = self.run_shim(["config", "config-helper", "--format=json"])
        self.assertEqual(result.returncode, 1)
        calls = self.call_count()
        self.assert_bounded(calls)
        self.assertEqual(result.stdout, f"PARTIAL-{calls}\n")
        self.assertEqual(result.stderr.count(LOCK_CRASH), 1)
        delays = [float(d) for d in (self.stub_dir / "sleeps").read_text(encoding="utf-8").split()]
        self.assertGreaterEqual(len(delays), 5)
        for attempt, delay in enumerate(delays[:5], start=1):
            self.assertGreaterEqual(delay, 0.2 * attempt)
            self.assertLess(delay, 0.6 * attempt)
        self.assertLessEqual(max(delays), 6)

    def test_any_groups_describe_list_and_get_iam_policy_are_run_again(self):
        """On 2026-11-03 at 01:52Z transcript-mirror.service failed when `compute instances
        describe` crashed on a locked credentials.db."""
        for args in (["compute", "instances", "describe", "machcsl-gcp-4", "--zone", "us-east1-b"],
                     ["--project", "p", "container", "clusters", "list"],
                     ["beta", "iam", "service-accounts", "keys", "list", "--iam-account=a@b"],
                     ["projects", "get-iam-policy", "p", "--format=json"]):
            with self.subTest(args=args):
                self.calls.unlink(missing_ok=True)
                self.stub_gcloud_crashing(times=1)
                result = self.run_shim(args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "OK-2\n")
                self.assertNotIn(LOCK_CRASH, result.stderr)
                self.assertEqual(self.call_count(), 2)

    def test_other_failures_and_other_commands_run_once(self):
        """A command that changes anything runs once: the crash text is the same whether or not
        its request went out."""
        cases = (
            (["compute", "instances", "create", "vm"], LOCK_CRASH),
            (["compute", "instances", "delete", "vm", "--zone", "z"], LOCK_CRASH),
            (["compute", "ssh", "vm", "--zone", "z", "--command", "list"], LOCK_CRASH),
            (["compute", "ssh", "vm", "--command", "list"], LOCK_CRASH),
            (["compute", "ssh", "list"], LOCK_CRASH),
            (["run", "jobs", "execute", "j", "--args", "describe"], LOCK_CRASH),
            (["container", "clusters", "get-credentials", "c"], LOCK_CRASH),
            (["storage", "rm", "gs://b/list"], LOCK_CRASH),
            (["auth", "login"], LOCK_CRASH),
            (["auth", "print-access-token", "-"], LOCK_CRASH),
            (["config", "config-helper", "--flags-file=-"], LOCK_CRASH),
            (["config", "config-helper"], "ERROR: gcloud crashed (OperationalError): disk I/O error"),
            (["auth", "print-access-token"], "ERROR: (gcloud.auth.print-access-token) database is locked"),
        )
        for args, crash in cases:
            with self.subTest(args=args, crash=crash):
                self.calls.unlink(missing_ok=True)
                self.stub_gcloud_crashing(times=1, crash=crash)
                result = self.run_shim(args)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.call_count(), 1)
                self.assertEqual(result.stdout, "PARTIAL-1\n")
                self.assertIn(crash, result.stderr)

    def test_the_retry_directory_is_removed(self):
        self.env["TMPDIR"] = str(self.stub_dir / "tmp")
        (self.stub_dir / "tmp").mkdir()
        self.stub_gcloud_crashing(times=1)
        result = self.run_shim(["auth", "print-access-token"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list((self.stub_dir / "tmp").iterdir()), [])


class GcloudLockLog(unittest.TestCase):
    """Each lock text a retried call prints adds one tab-separated line to
    ~/.local/state/gcloud-shim/locks.tsv: time, kind, outcome, subcommand, shim pid, omp session,
    and the program that ran the shim."""

    setUp = GcloudLockedStoreRetry.setUp
    tearDown = GcloudLockedStoreRetry.tearDown
    stub_gcloud_crashing = GcloudLockedStoreRetry.stub_gcloud_crashing
    run_shim = GcloudLockedStoreRetry.run_shim
    DEADLINE = GcloudLockedStoreRetry.DEADLINE
    bound_retries = GcloudLockedStoreRetry.bound_retries

    def log_rows(self) -> list[list[str]]:
        log = Path(self.env["HOME"]) / ".local" / "state" / "gcloud-shim" / "locks.tsv"
        return [line.split("\t") for line in log.read_text(encoding="utf-8").splitlines()]

    def test_a_retried_store_lock_and_the_cache_warning_each_add_a_line(self):
        self.stub_gcloud_crashing(times=1)
        self.env["OMP_SESSION_ID"] = "01a1-test-session"
        caller = self.stub_dir / "poller.sh"
        caller.write_text('#!/bin/bash\n"$@"\nexit $?\n', encoding="utf-8")
        result = subprocess.run(["bash", str(caller), str(SHIMS / "gcloud"), "config", "config-helper",
                                 "--format=json"], capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = self.log_rows()
        self.assertEqual(sorted(r[1:4] for r in rows), [["credcache", "retried", "config config-helper"],
                                                        ["token-cache", "warning", "config config-helper"]])
        for row in rows:
            self.assertEqual(len(row), 7)
            self.assertRegex(row[0], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertRegex(row[4], r"^\d+$")
            self.assertEqual(row[5:], ["01a1-test-session", f"bash {caller}"])

    def test_the_caller_column_never_holds_inline_code(self):
        self.stub_gcloud_crashing(times=1)
        env = {k: v for k, v in self.env.items() if k != "OMP_SESSION_ID"}
        result = subprocess.run(["bash", "-c", f'"{SHIMS / "gcloud"}" auth list; exit $?  # SECRET'],
                                capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({tuple(r[5:]) for r in self.log_rows()}, {("-", "bash")})

    def test_a_store_lock_past_the_deadline_is_logged_as_gave_up(self):
        self.bound_retries()
        self.stub_gcloud_crashing(times=1000, crash=LOCK_TASK_ERROR, partial=False)
        result = self.run_shim(["storage", "rsync", "gs://b/d", str(self.stub_dir)])
        self.assertEqual(result.returncode, 1)
        outcomes = [r[2] for r in self.log_rows() if r[1] == "store"]
        self.assertEqual(outcomes, ["retried"] * (len(outcomes) - 1) + ["gave-up"])
        self.assertEqual(len(outcomes), int(self.calls.read_text(encoding="utf-8")))
        self.assertEqual({r[3] for r in self.log_rows()}, {"storage rsync gs://b/d"})

    def test_the_schema_warning_alone_is_logged_and_the_call_succeeds(self):
        write_stub(self.stub_dir, "gcloud",
                   'echo "WARNING: Could not initialize access token cache schema: database is locked" >&2; echo TOKEN')
        result = self.run_shim(["auth", "print-access-token"])
        self.assertEqual((result.returncode, result.stdout), (0, "TOKEN\n"))
        self.assertEqual([r[1:4] for r in self.log_rows()], [["token-cache", "warning", "auth print-access-token"]])

    def test_no_lock_text_writes_no_log(self):
        write_stub(self.stub_dir, "gcloud", "echo OK")
        self.assertEqual(self.run_shim(["auth", "list"]).returncode, 0)
        self.assertFalse((Path(self.env["HOME"]) / ".local" / "state" / "gcloud-shim").exists())

    def test_an_unwritable_log_does_not_change_the_call(self):
        state = Path(self.env["HOME"]) / ".local" / "state"
        state.mkdir(parents=True)
        (state / "gcloud-shim").write_text("not a directory", encoding="utf-8")
        self.stub_gcloud_crashing(times=1)
        result = self.run_shim(["auth", "print-access-token"])
        self.assertEqual((result.returncode, result.stdout), (0, "OK-2\n"), result.stderr)


class GcloudShimCopiesOnPath(unittest.TestCase):
    """Two copies of the shim on PATH must not take each other for the real gcloud: at 06:23Z on
    2026-11-03 a scratch copy ahead of shims/ and the installed shim called each other until an
    agent box's 8192 processes were used up (dispatch://INFRA-993). A copy is skipped when it has
    the same bytes or holds the shim's marker line (another version), and the real one runs."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.real = self.root / "sdk"
        self.real.mkdir()
        write_stub(self.real, "gcloud", 'echo "REAL-GCLOUD-RAN"')

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def copy_of_shim(self, name: str, extra: str = "") -> Path:
        directory = self.root / name
        directory.mkdir()
        copy = directory / "gcloud"
        copy.write_text((SHIMS / "gcloud").read_text(encoding="utf-8") + extra, encoding="utf-8")
        copy.chmod(0o755)
        return directory

    def test_a_second_copy_ahead_on_path_reaches_the_real_gcloud(self) -> None:
        for name, extra in (("same-bytes", ""), ("other-version", "# a later edit\n")):
            with self.subTest(copy=name):
                copy = self.copy_of_shim(name, extra)
                env = {"PATH": f"{copy}:{SHIMS}:{self.real}:/usr/bin:/bin", "HOME": str(self.root / "home"),
                       "GCE_METADATA_HOST": "127.0.0.1:9"}
                # `version` runs by exec, not by the lock retry, so a loop is one process the
                # timeout ends rather than a growing tree.
                for first in (copy / "gcloud", SHIMS / "gcloud"):
                    result = subprocess.run([str(first), "version"], capture_output=True, text=True, env=env,
                                            timeout=300, check=False)
                    self.assertEqual((result.returncode, result.stdout), (0, "REAL-GCLOUD-RAN\n"), result.stderr)


class GcloudSessionConfig(unittest.TestCase):
    """An agent session's gcloud runs on its own copy of ~/.config/gcloud, at $AGENT_SESSION_SCRATCH/gcloud, made at
    the session's first call: the shared sqlite stores on the root disk took 2,148 fsyncs in 300 s and gave 2,000-4,200
    `database is locked` lines an hour (2026-11-03). A caller's CLOUDSDK_CONFIG, an agentbox, a process outside an agent
    session, and a session with no scratch directory keep the old directory; so does a command whose change must last,
    which also sets the session's copy aside so the next call copies again."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        write_stub(root, "gcloud", 'echo "CLOUDSDK_CONFIG=${CLOUDSDK_CONFIG:-<unset>}"\nprintf "ARG=%s\\n" "$@"')
        self.origin = root / "home" / ".config" / "gcloud"
        (self.origin / "configurations").mkdir(parents=True)
        (self.origin / "configurations" / "config_default").write_text("[core]\naccount = a@example.com\n")
        (self.origin / "active_config").write_text("default")
        for name in ("logs", ".gcloud-shim-retry-slots", "cache"):
            (self.origin / name).mkdir()
            (self.origin / name / "x").write_text("x")
        self.store("credentials.db", "refresh-1")
        self.store("access_tokens.db", "token-1")
        (self.origin / "credentials.db-journal").write_text("")  # an empty (cold) journal: a sidecar, not copied
        self.scratch = root / "scratch"
        self.scratch.mkdir(mode=0o700)
        self.copy = self.scratch / "gcloud"
        self.env = {"PATH": f"{root}:{SHIMS}:/usr/bin:/bin", "HOME": str(root / "home"),
                    "GCE_METADATA_HOST": "127.0.0.1:9", "OMP_SESSION_ID": "01a00000-0000-7000-8000-000000000000",
                    "AGENT_SESSION_SCRATCH": str(self.scratch)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def store(self, name: str, value: str) -> None:
        with sqlite3.connect(self.origin / name) as db:
            db.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
            db.execute("DELETE FROM t")
            db.execute("INSERT INTO t VALUES (?)", (value,))

    def rows(self, path: Path) -> list[str]:
        with sqlite3.connect(path) as db:
            return [row[0] for row in db.execute("SELECT v FROM t")]

    def run_shim(self, args: list[str], **extra: str | None) -> str:
        env = {k: v for k, v in {**self.env, **extra}.items() if v is not None}
        result = subprocess.run([str(SHIMS / "gcloud"), *args], capture_output=True, text=True, env=env, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        line = next(x for x in result.stdout.splitlines() if x.startswith("CLOUDSDK_CONFIG="))
        return line.removeprefix("CLOUDSDK_CONFIG=")

    def test_an_agent_session_call_runs_on_its_own_copy(self) -> None:
        self.assertEqual(self.run_shim(["auth", "print-access-token"]), str(self.copy))
        self.assertEqual(self.rows(self.copy / "credentials.db"), ["refresh-1"])
        self.assertEqual((self.copy / "active_config").read_text(), "default")
        self.assertTrue((self.copy / "configurations" / "config_default").is_file())
        self.assertTrue((self.copy / ".gcloud-session-copied").is_file())
        for left_out in ("logs", ".gcloud-shim-retry-slots", "cache", "access_tokens.db", "credentials.db-journal"):
            self.assertFalse((self.copy / left_out).exists(), left_out)
        self.assertEqual(stat.S_IMODE(self.copy.stat().st_mode), 0o700)

    def test_the_copy_is_made_once(self) -> None:
        self.run_shim(["config", "config-helper"])
        self.store("credentials.db", "refresh-2")
        self.assertEqual(self.run_shim(["storage", "ls"]), str(self.copy))
        self.assertEqual(self.rows(self.copy / "credentials.db"), ["refresh-1"])

    def test_the_old_directory_where_this_does_not_apply(self) -> None:
        own = str(Path(self.tmp.name) / "own")
        self.assertEqual(self.run_shim(["auth", "list"], CLOUDSDK_CONFIG=own), own)
        self.assertEqual(self.run_shim(["auth", "list"], OMP_SESSION_ID=None), "<unset>")
        self.assertEqual(self.run_shim(["auth", "list"], AGENT_SESSION_SCRATCH=None), "<unset>")
        self.assertFalse(self.copy.exists())

    def test_a_change_that_must_last_runs_on_the_shared_directory_and_resets_the_copy(self) -> None:
        self.run_shim(["auth", "list"])
        for args in (["config", "set", "project", "p"], ["config", "unset", "project"],
                     ["config", "configurations", "activate", "default"], ["init"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_shim(args), "<unset>")
                self.assertFalse(self.copy.exists())
                self.store("credentials.db", "-".join(args))
                self.assertEqual(self.run_shim(["auth", "list"]), str(self.copy))
                self.assertEqual(self.rows(self.copy / "credentials.db"), ["-".join(args)])
        self.assertTrue(list(self.scratch.glob("gcloud.replaced.*")))

    def test_an_agentbox_session_runs_on_a_copy_in_the_box_tmp(self) -> None:
        box_tmp = Path(self.tmp.name) / "box-tmp"
        box_tmp.mkdir()
        box_copy = box_tmp / "agentbox-gcloud-session" / "gcloud"
        got = self.run_shim(["storage", "ls"], AGENT_SESSION_SCRATCH=None, AGENTBOX_BOX="agentbox-0123", TMPDIR=str(box_tmp))
        self.assertEqual(got, str(box_copy))
        self.assertEqual(self.rows(box_copy / "credentials.db"), ["refresh-1"])
        self.assertEqual(stat.S_IMODE(box_copy.parent.stat().st_mode), 0o700)
        self.assertFalse(self.copy.exists())
        self.assertEqual(self.rows(self.origin / "credentials.db"), ["refresh-1"], "the box's seed was written")

    def test_a_copy_that_cannot_be_made_leaves_the_call_on_the_shared_directory(self) -> None:
        (self.origin / "credentials.db").write_text("not a sqlite database")
        self.assertEqual(self.run_shim(["auth", "list"]), "<unset>")
        self.assertFalse(self.copy.exists())
        self.assertEqual([p for p in self.scratch.iterdir()], [], "a partial copy was left behind")


class GcloudNonInteractiveSsh(unittest.TestCase):
    """INFRA-1538: `ssh sami-devbox gcloud …` is an agent's command, not Sami's, so the
    agent-session rules apply to it, the shim selects the machine's own configuration for it, and
    the notice names the identity the call ENDED on — the first draft announced "the machine
    identity" before resolving one, and with active_config naming a person's configuration the
    call ran as that person under that claim (review of INFRA-1538). A person's own SSH session
    (SSH_TTY set, whatever one command reads on stdin), and a person who sets
    AGENT_SHIM_PERSON=1, are unchanged."""

    USER = "Sami@Theorem.dev"
    BOX = "box-identity@theorem-agent-infra.iam.gserviceaccount.com"
    setUp = GcloudRouting.setUp
    tearDown = GcloudRouting.tearDown
    write_user_adc = GcloudRouting.write_user_adc
    stub_gcloud_that_must_not_run = GcloudRouting.stub_gcloud_that_must_not_run
    NOTICE = "gcloud shim: non-interactive SSH, so an agent's call"

    def write_identity_configs(self, box: bool = True) -> None:
        """A person's configuration active, as on a machine where Sami signed in."""
        (self.configs_dir / "config_theorem").write_text(f"[core]\naccount = {self.USER}\n", encoding="utf-8")
        if box:
            (self.configs_dir / "config_box").write_text(f"[core]\naccount = {self.BOX}\n", encoding="utf-8")
        (self.cloudsdk_config / "active_config").write_text("theorem\n", encoding="utf-8")

    def run_shim(self, extra_env: Mapping[str, str] | None = None, args: list[str] | None = None,
                 tty: bool = False) -> subprocess.CompletedProcess[str]:
        run = run_with_tty if tty else run_without_tty
        env = {**self.env, "SSH_CONNECTION": SSH_CONNECTION, **(extra_env or {})}
        return run([str(SHIMS / "gcloud"), *(args or ["config", "list"])], env=env)

    def test_a_command_over_ssh_with_no_terminal_is_an_agent_session(self) -> None:
        adc = self.write_user_adc()
        self.stub_gcloud_that_must_not_run()
        result = self.run_shim(args=["auth", "application-default", "login"])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
        self.assertIn("gcloud shim: REFUSED in an agent session", result.stderr)
        self.assertEqual(adc.read_text(encoding="utf-8"), '{"type":"authorized_user"}\n')

    def test_an_active_human_configuration_is_replaced_by_box(self) -> None:
        # The review's case: active_config=theorem holding Sami@Theorem.dev. The call must not run
        # as him, and the line must name the account it does run as.
        self.write_identity_configs()
        result = self.run_shim()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=box", result.stdout)
        self.assertNotIn(self.USER, result.stdout)
        self.assertIn(f"configuration box, account {self.BOX}", result.stderr)
        self.assertIn("AGENT_SHIM_PERSON=1", result.stderr)

    def test_an_active_human_configuration_with_no_box_here_is_refused(self) -> None:
        # Nothing to select instead, so the call does not run as the person either.
        self.write_identity_configs(box=False)
        self.stub_gcloud_that_must_not_run()
        result = self.run_shim()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
        self.assertIn("gcloud shim: REFUSED, a command over non-interactive SSH", result.stderr)
        self.assertIn(self.USER, result.stderr)
        self.assertIn("AGENT_SHIM_PERSON=1", result.stderr)

    def test_an_explicitly_selected_human_configuration_is_named_as_itself(self) -> None:
        # The standing exception (dispatch://INFRA-6/ask/6c7b1e80) still passes, and the line says
        # whose account it is rather than calling it the machine identity.
        self.write_identity_configs()
        result = self.run_shim(args=["--configuration", "theorem", "config", "list"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"configuration theorem, account {self.USER}", result.stderr)
        self.assertNotIn("machine identity", result.stderr)
        self.assertEqual(stub_argv(result), ["--configuration", "theorem", "config", "list"])

    def test_gcloud_account_still_selects_its_configuration(self) -> None:
        self.write_identity_configs()
        result = self.run_shim({"GCLOUD_ACCOUNT": "theorem"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=theorem", result.stdout)
        self.assertIn(f"configuration theorem, account {self.USER}", result.stderr)

    # The notice must name the configuration and account the command ENDS UP on, in main's own
    # order, not the first one some helper resolves: with CLOUDSDK_ACTIVE_CONFIG_NAME=box and
    # GCLOUD_ACCOUNT=theorem it said box while gcloud ran theorem (re-review of INFRA-1538).
    def test_gcloud_account_beats_an_active_config_name_in_the_notice_too(self) -> None:
        self.write_identity_configs()
        result = self.run_shim({"GCLOUD_ACCOUNT": "theorem", "CLOUDSDK_ACTIVE_CONFIG_NAME": "box"},
                               args=["auth", "print-access-token"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=theorem", result.stdout)
        self.assertIn(f"configuration theorem, account {self.USER}", result.stderr)
        self.assertNotIn("configuration box", result.stderr)

    def test_a_configuration_flag_beats_both_environment_selections(self) -> None:
        # gcloud reads --configuration from raw argv before any environment variable.
        self.write_identity_configs()
        result = self.run_shim({"GCLOUD_ACCOUNT": "box", "CLOUDSDK_ACTIVE_CONFIG_NAME": "box"},
                               args=["--configuration", "theorem", "config", "list"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"configuration theorem, account {self.USER}", result.stderr)

    # An account the CALL names is a choice of its own. identity_is_selected knows only
    # configurations and --cred-file, so a service account passed this way was refused as if it
    # were the configuration file's person (re-review of INFRA-1538).
    SERVICE_ACCOUNT = "alternate@p.iam.gserviceaccount.com"

    def test_an_explicit_service_account_runs_and_is_the_named_account(self) -> None:
        for selection in (("flag", ["--account", self.SERVICE_ACCOUNT, "config", "list"], {}),
                          ("environment", ["config", "list"], {"CLOUDSDK_CORE_ACCOUNT": self.SERVICE_ACCOUNT})):
            label, args, env = selection
            for box in (True, False):
                with self.subTest(selection=label, box=box):
                    self.write_identity_configs(box=box)
                    result = self.run_shim(env, args=args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f"account {self.SERVICE_ACCOUNT}", result.stderr)
                    self.assertNotIn(self.USER, result.stderr)
                    self.assertEqual(stub_argv(result), args)

    def test_an_explicit_human_account_is_still_refused(self) -> None:
        # The existing agent-session guard, not weakened by the account-aware route above.
        self.write_identity_configs()
        self.stub_gcloud_that_must_not_run()
        result = self.run_shim(args=["--account", self.USER, "config", "list"])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
        self.assertIn("REFUSED in an agent session", result.stderr)

    def test_impersonation_is_named_beside_the_account(self) -> None:
        applier = "applier@p.iam.gserviceaccount.com"
        self.write_identity_configs()
        result = self.run_shim({"CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT": applier})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"configuration box, account {self.BOX}, impersonating {applier}", result.stderr)

    def test_impersonation_the_configuration_sets_is_named_too(self) -> None:
        # The SDK resolves a property from the flag, then the environment, then the
        # configuration FILE (core/properties.py:5487-5527); the notice read only the first two
        # and so named an identity short of the one the call acts with (round 3 of INFRA-1538).
        applier = "infra-applier@p.iam.gserviceaccount.com"
        self.write_identity_configs()
        (self.configs_dir / "config_box").write_text(
            f"[core]\naccount = {self.BOX}\n[auth]\nimpersonate_service_account = {applier}\n",
            encoding="utf-8")
        result = self.run_shim()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"configuration box, account {self.BOX}, impersonating {applier}", result.stderr)

    # The SSH rule makes this an agent session too, so the flags-file refusal applies here, and
    # it comes before this route names any identity: with `--account: <the person>` in a file
    # the notice used to claim the machine's while the SDK ran as the person (INFRA-1538).
    def test_a_flags_file_is_refused_before_any_identity_claim(self) -> None:
        self.write_identity_configs()
        flags_file = self.stub_dir / "flags.yaml"
        for text in (f"--account: {self.USER}\n", "--project: p\n"):
            with self.subTest(text=text):
                flags_file.write_text(text, encoding="utf-8")
                self.stub_gcloud_that_must_not_run()
                result = self.run_shim(args=["config", "list", f"--flags-file={flags_file}"])
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
                self.assertIn("gcloud shim: REFUSED in an agent session", result.stderr)
                self.assertNotIn("configuration box, account", result.stderr)

    def test_a_change_that_outlives_the_call_keeps_its_configuration(self) -> None:
        # The deliberate exception: selecting box for `auth revoke` or `config set` would write
        # or empty box's own state. The notice says which configuration it is.
        self.write_identity_configs()
        for args in (["auth", "revoke"], ["config", "set", "project", "p"]):
            with self.subTest(args=args):
                result = self.run_shim(args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONFIG=<unset>", result.stdout)
                self.assertIn(f"configuration theorem, account {self.USER}", result.stderr)
                self.assertIn("outlives this call", result.stderr)

    def test_a_persons_ssh_session_is_the_person(self) -> None:
        for stdin_is_a_pipe in (True, False):
            with self.subTest(piped=stdin_is_a_pipe):
                self.write_user_adc()
                result = self.run_shim({"SSH_TTY": SSH_TTY}, args=["auth", "application-default", "login"],
                                       tty=not stdin_is_a_pipe)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("REFUSED in an agent session", result.stderr)
                self.assertNotIn(self.NOTICE, result.stderr)

    def test_agent_shim_person_leaves_the_person_alone(self) -> None:
        self.write_identity_configs()
        self.write_user_adc()
        result = self.run_shim({"AGENT_SHIM_PERSON": "1"}, args=["auth", "application-default", "login"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("REFUSED in an agent session", result.stderr)
        self.assertNotIn(self.NOTICE, result.stderr)

    def test_a_call_that_is_allowed_still_runs_and_is_named_once(self) -> None:
        result = self.run_shim(args=["config", "list"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stub_argv(result), ["config", "list"])
        self.assertEqual(result.stderr.count(self.NOTICE), 1, result.stderr)

    def test_a_session_marker_needs_no_notice(self) -> None:
        result = self.run_shim({"OMP_SESSION_ID": "01a00000-0000-7000-8000-000000000000"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(self.NOTICE, result.stderr)



if __name__ == "__main__":
    unittest.main()
