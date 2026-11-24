#!/usr/bin/env python3
"""scripts/gcloud-broker: the command runs with gcloud's file logging off (dispatch://INFRA-4824).

gcloud copies a command's output into a log file under CLOUDSDK_CONFIG/logs, so a printed access
token lands on disk. gcloud-broker runs its command in a fresh CLOUDSDK_CONFIG with the broker's
token in CLOUDSDK_AUTH_ACCESS_TOKEN_FILE; the fake secret-run of test_gcloud_reader hands the
token over as secret-run does.

gcloud-broker and az-broker ask a person for a human-tier login, so each refuses, exit 2, before
secret-run runs, when the caller gives no --reason: until 2026-11-23 each made up one ("gcloud-broker
admin command"), which names no act (dispatch://INFRA-1481, R5). The inner hop, which secret-run
runs once the login is released, needs none.
"""

from __future__ import annotations

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_gcloud_reader import PRINT_TOKEN_AND_LIST_LOGS, SDK_BIN, SECRET_RUN, TOKEN, TOOL

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "scripts" / "gcloud-broker"
AZ_SCRIPT = DOTFILES / "scripts" / "az-broker"
REASON = "INFRA-1481: list projects"


class Fixture:
    def __init__(self) -> None:
        self.tmp: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bin: Path = root / "bin"
        self.log: Path = root / "log"
        self.tmpdir: Path = root / "tmp"

    def __enter__(self) -> "Fixture":
        for d in (self.bin, self.log, self.tmpdir):
            d.mkdir()
        for name, body in (("secret-run", SECRET_RUN), ("gcloud", TOOL)):
            path = self.bin / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return self

    def __exit__(self, *exc: object) -> None:
        self.tmp.cleanup()

    def run(self, *args: str, env_extra: dict[str, str] | None = None, script: Path = SCRIPT) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmpdir),
            "TMPDIR": str(self.tmpdir),
            "LOG": str(self.log),
            "FAKE_TOKEN": TOKEN,
            **(env_extra or {}),
        }
        return subprocess.run([str(script), *args], env=env, capture_output=True, text=True, timeout=60)

    def secret_run_argv(self) -> list[str]:
        path = self.log / "secret-run.argv"
        return path.read_text().splitlines() if path.exists() else []

    def tool_log(self) -> dict[str, str]:
        path = self.log / "tool.log"
        if not path.exists():
            return {}
        return {key: value for key, _, value in (line.partition(" ") for line in path.read_text().splitlines())}


class GcloudBrokerFileLoggingTest(unittest.TestCase):
    def test_the_command_runs_with_file_logging_off(self) -> None:
        for caller in ({}, {"CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "false"}):
            with self.subTest(caller=caller), Fixture() as f:
                result = f.run("--reason", REASON, "gcloud", "auth", "print-access-token", env_extra=caller)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(f.tool_log()["token"], TOKEN)
                self.assertEqual(f.tool_log()["file_logging"], "true")

    @unittest.skipUnless((SDK_BIN / "gcloud").is_file(), "no Google Cloud SDK at ~/.gcloud")
    def test_the_real_gcloud_writes_no_log_file(self) -> None:
        with Fixture() as f:
            result = f.run("--reason", REASON, "sh", "-c", PRINT_TOKEN_AND_LIST_LOGS,
                           env_extra={"PATH": f"{SDK_BIN}:{f.bin}:/usr/bin:/bin"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((f.log / "printed").read_text().strip(), TOKEN, "gcloud printed no token")
            self.assertEqual((f.log / "token-files").read_text(), "", "a log file holds the token")
            self.assertEqual((f.log / "log-files").read_text(), "", "gcloud wrote a log file")


class NoReasonTest(unittest.TestCase):
    # Each wrapper, and the example its refusal names.
    SCRIPTS: dict[Path, str] = {SCRIPT: "stop instance x", AZ_SCRIPT: "deallocate vm x"}

    def test_no_reason_is_refused_before_secret_run(self) -> None:
        for script, example in self.SCRIPTS.items():
            for args in (("gcloud", "projects", "list"), ("--reason", " \t", "gcloud", "projects", "list"),
                         ("--", "gcloud", "projects", "list")):
                with self.subTest(script=script.name, args=args), Fixture() as f:
                    result = f.run(*args, script=script)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stderr.splitlines(), [
                        f"{script.name}: refused, nothing requested: pass --reason with the issue key and what the "
                        f"command does ('INFRA-1234: {example}')"
                    ])
                    self.assertEqual(f.secret_run_argv(), [], "secret-run ran with no reason")
                    self.assertEqual(f.tool_log(), {}, "the command ran")

    def test_a_reason_reaches_secret_run(self) -> None:
        """The control: the same call with --reason R or --reason=R reaches secret-run, which the refusal above never
        does."""
        for script in self.SCRIPTS:
            for form in (("--reason", REASON), (f"--reason={REASON}",)):
                with self.subTest(script=script.name, form=form), Fixture() as f:
                    result = f.run(*form, "gcloud", "projects", "list", script=script)
                    argv = f.secret_run_argv()
                    self.assertEqual(argv[:2], ["--reason", REASON], result.stderr)

    def test_a_reason_flag_with_no_value_is_a_usage_error(self) -> None:
        for script in self.SCRIPTS:
            with self.subTest(script=script.name), Fixture() as f:
                result = f.run("--reason", script=script)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stderr.splitlines(), [f"usage: {script.name} --reason R [--] <command...>"])
                self.assertEqual(f.secret_run_argv(), [])

    def test_the_inner_hop_needs_no_reason(self) -> None:
        """secret-run runs the inner hop with the login released, and Legion's airlock check runs it the same
        way (GCLOUD_BROKER_INNER=1, no --reason)."""
        with Fixture() as f:
            result = f.run("gcloud", "projects", "list", env_extra={"GCLOUD_BROKER_INNER": "1", "GCLOUD_SAMI": TOKEN})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(f.tool_log()["token"], TOKEN)
            self.assertEqual(f.secret_run_argv(), [])

    def test_az_broker_inner_hop_needs_no_reason(self) -> None:
        """The same for az-broker, which Legion's airlock check runs with AZ_BROKER_INNER=1 and no --reason. A stub
        az takes the login, and the command after it runs."""
        with Fixture() as f:
            az = f.bin / "az"
            az.write_text('#!/bin/bash\nprintf "az %s\\n" "$1" >> "$LOG/az.log"\n')
            az.chmod(az.stat().st_mode | stat.S_IEXEC)
            login = '{"tenant_id":"t","client_id":"c","federated_token":"f","expires_at":"x"}'
            result = f.run("sh", "-c", 'echo azure-inner-ran', script=AZ_SCRIPT,
                           env_extra={"AZ_BROKER_INNER": "1", "AZURE_ADMIN": login})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "azure-inner-ran\n")
            self.assertEqual((f.log / "az.log").read_text(), "az login\n")
            self.assertEqual(f.secret_run_argv(), [])


if __name__ == "__main__":
    unittest.main()
