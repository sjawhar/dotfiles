#!/usr/bin/env python3
"""scripts/gcloud-reader: one Google read as an agent reader account, through the broker.

gcloud-reader asks secret-run for METADATA_READ (a token of metadata-reader@theorem-agent-infra)
or, under --as legion, LEGION_READ (a token of legion-reader@theorem-agent-infra), always with
--register and --ttl 1h: the broker refuses a request with no lifetime on a secret whose
max-grant is an hour. The broker releases a google-service-account secret as a bare access token
(cybermonklabs/legion packages/envoy/internal/broker/secrets/googleserviceaccount.go, Minted{Value:
token.AccessToken}), which the fake secret-run below hands over the same way. The token goes to a
0600 file of a directory removed on exit, never into an argument list, and the command runs with
CLOUDSDK_AUTH_ACCESS_TOKEN_FILE set to it, a fresh CLOUDSDK_CONFIG and gcloud's file logging off.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "scripts" / "gcloud-reader"
TOKEN = "fake-reader-access-token"
SDK_BIN = Path(os.environ.get("HOME", "/nonexistent")) / ".gcloud" / "google-cloud-sdk" / "bin"
# Run by the command gcloud-reader gives it: prints the token as gcloud would, then lists the log
# files gcloud wrote under the configuration directory gcloud-reader made, and those holding the
# token, before the reader removes that directory on exit. A failed print leaves no token in
# "printed", which the test reads.
PRINT_TOKEN_AND_LIST_LOGS = (
    'gcloud auth print-access-token > "$LOG/printed"; '
    'find "$CLOUDSDK_CONFIG" -path "*/logs/*" -type f > "$LOG/log-files"; '
    'find "$CLOUDSDK_CONFIG" -path "*/logs/*" -type f -exec grep -lF "$FAKE_TOKEN" {} + > "$LOG/token-files"; '
    'exit 0'
)

# The fake secret-run logs each argument on its own line, then, unless SECRET_RUN_EXIT is set,
# exports the requested key (the word before `--`) as the bare token and runs the command after
# `--`, as secret-run does on the broker path.
SECRET_RUN = r"""#!/bin/bash
printf '%s\n' "$@" >> "$LOG/secret-run.argv"
[[ -z "${SECRET_RUN_EXIT:-}" ]] || exit "$SECRET_RUN_EXIT"
key=""
while (( $# )) && [[ "$1" != -- ]]; do key="$1"; shift; done
shift
export "$key=$FAKE_TOKEN"
exec "$@"
"""

# A fake tool logs its argv, the environment gcloud-reader gave it, and the token file's content.
TOOL = r"""#!/bin/bash
{
  printf 'argv %s\n' "$0 $*"
  printf 'token_file %s\n' "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-}"
  printf 'token_file_mode %s\n' "$(stat -c %a "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-/nonexistent}" 2>/dev/null)"
  printf 'token %s\n' "$(cat "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-/nonexistent}" 2>/dev/null)"
  printf 'config %s\n' "${CLOUDSDK_CONFIG:-}"
  printf 'prompts %s\n' "${CLOUDSDK_CORE_DISABLE_PROMPTS:-}"
  printf 'impersonate %s\n' "${CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT:-}"
  printf 'env_metadata %s\n' "${METADATA_READ:-}"
  printf 'env_legion %s\n' "${LEGION_READ:-}"
  printf 'file_logging %s\n' "${CLOUDSDK_CORE_DISABLE_FILE_LOGGING:-}"
} >> "$LOG/tool.log"
"""


def ttl_problems(script: Path, env_extra: dict[str, str] | None = None) -> list[str]:
    """Runs script as gcloud-reader for each --as, and returns what secret-run's argv lacks."""
    problems = []
    for as_args, key in (((), "METADATA_READ"), (("--as", "metadata"), "METADATA_READ"), (("--as", "legion"), "LEGION_READ")):
        with Fixture(script) as f:
            result = f.run(*as_args, "storage", "buckets", "list", env_extra=env_extra)
            argv = f.secret_run_argv()
            if result.returncode != 0:
                problems.append(f"{as_args}: exit {result.returncode}: {result.stderr}")
            if "--ttl\n1h\n" not in "".join(a + "\n" for a in argv):
                problems.append(f"{as_args}: secret-run got no --ttl 1h: {argv}")
            if "--register" not in argv:
                problems.append(f"{as_args}: secret-run got no --register: {argv}")
            if key not in argv or argv[argv.index("--") - 1] != key:
                problems.append(f"{as_args}: secret-run did not ask for {key} alone: {argv}")
    return problems


class Fixture:
    def __init__(self, script: Path) -> None:
        self.script = script

    def __enter__(self) -> "Fixture":
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bin = root / "bin"
        self.log = root / "log"
        self.tmpdir = root / "tmp"
        for d in (self.bin, self.log, self.tmpdir):
            d.mkdir()
        for name, body in (("secret-run", SECRET_RUN), ("gcloud", TOOL), ("bq", TOOL)):
            path = self.bin / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return self

    def __exit__(self, *exc: object) -> None:
        self.tmp.cleanup()

    def run(self, *args: str, env_extra: dict[str, str] | None = None, reason: str | None = "INFRA-4748: the IAM policy of gs://theorem-build-io") -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmpdir),
            "TMPDIR": str(self.tmpdir),
            "LOG": str(self.log),
            "FAKE_TOKEN": TOKEN,
        }
        if reason is not None:
            env["GCLOUD_READER_REASON"] = reason
        env.update(env_extra or {})
        return subprocess.run([str(self.script), *args], env=env, capture_output=True, text=True, timeout=30)

    def secret_run_argv(self) -> list[str]:
        path = self.log / "secret-run.argv"
        return path.read_text().splitlines() if path.exists() else []

    def tool_log(self) -> dict[str, str]:
        path = self.log / "tool.log"
        if not path.exists():
            return {}
        return dict(line.split(" ", 1) if " " in line else (line, "") for line in path.read_text().splitlines())

    def leftovers(self) -> list[str]:
        return [p.name for p in self.tmpdir.iterdir() if p.name.startswith("gcloud-reader.")]


class GcloudReaderTest(unittest.TestCase):
    def test_asks_for_metadata_read_by_default_with_an_hour_and_register(self) -> None:
        self.assertEqual(ttl_problems(SCRIPT), [])
        with Fixture(SCRIPT) as f:
            f.run("storage", "buckets", "list")
            argv = f.secret_run_argv()
            self.assertEqual(argv[: argv.index("--")], ["--register", "--ttl", "1h", "--reason", "INFRA-4748: the IAM policy of gs://theorem-build-io", "METADATA_READ"])

    def test_as_legion_asks_for_legion_read(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("--as", "legion", "--", "gcloud", "organizations", "get-iam-policy", "123456789012")
            self.assertEqual(result.returncode, 0, result.stderr)
            argv = f.secret_run_argv()
            self.assertEqual(argv[argv.index("--") - 1], "LEGION_READ")
            self.assertNotIn("METADATA_READ", argv)

    def test_a_copy_without_the_ttl_fails_the_ttl_check(self) -> None:
        """The negative control of the --ttl case: the broker refuses a request with no lifetime."""
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "gcloud-reader"
            text = SCRIPT.read_text()
            stripped = re.sub(r" --ttl 1h", "", text)
            self.assertNotEqual(stripped, text, "the script holds no ' --ttl 1h' to remove")
            copy.write_text(stripped)
            copy.chmod(0o755)
            self.assertTrue(any("no --ttl 1h" in p for p in ttl_problems(copy)))

    def test_another_as_exits_2_before_any_call(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("--as", "other", "storage", "buckets", "list")
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("--as", result.stderr)
            self.assertEqual(f.secret_run_argv(), [])
            self.assertEqual(f.tool_log(), {})

    def test_no_reason_exits_2_before_any_call(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("storage", "buckets", "list", reason=None)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("GCLOUD_READER_REASON", result.stderr)
            self.assertEqual(f.secret_run_argv(), [])

    def test_help_and_no_command_ask_nothing(self) -> None:
        with Fixture(SCRIPT) as f:
            self.assertEqual(f.run("--help").returncode, 0)
            self.assertEqual(f.run().returncode, 2)
            self.assertEqual(f.run("--as", "legion").returncode, 2)
            self.assertEqual(f.secret_run_argv(), [])

    def test_a_refused_release_stops_before_any_call(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("storage", "buckets", "list", env_extra={"SECRET_RUN_EXIT": "1"})
            self.assertEqual(result.returncode, 1)
            self.assertEqual(f.tool_log(), {})
            result = f.run("storage", "buckets", "list", env_extra={"SECRET_RUN_EXIT": "77"})
            self.assertEqual(result.returncode, 77)
            self.assertEqual(f.tool_log(), {})

    def test_the_token_reaches_the_command_in_a_file_and_in_no_argv(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("storage", "buckets", "get-iam-policy", "gs://theorem-build-io")
            self.assertEqual(result.returncode, 0, result.stderr)
            log = f.tool_log()
            self.assertEqual(log["token"], TOKEN, "the released token is written as it is, with no newline")
            self.assertEqual(log["token_file_mode"], "600")
            self.assertTrue(log["config"].startswith(str(f.tmpdir) + "/gcloud-reader."), log["config"])
            self.assertEqual(log["prompts"], "1")
            self.assertEqual(log["impersonate"], "")
            self.assertEqual(log["env_metadata"], "", "the command still holds METADATA_READ")
            self.assertNotIn(TOKEN, log["argv"])
            self.assertNotIn(TOKEN, "\n".join(f.secret_run_argv()))
            self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_a_first_word_that_is_no_command_runs_gcloud(self) -> None:
        with Fixture(SCRIPT) as f:
            f.run("storage", "buckets", "list")
            self.assertRegex(f.tool_log()["argv"], r"/gcloud storage buckets list$")
        with Fixture(SCRIPT) as f:
            f.run("--as", "legion", "--", "bq", "--project_id=theorem-agent-infra", "ls")
            self.assertRegex(f.tool_log()["argv"], r"/bq --project_id=theorem-agent-infra ls$")

    def test_the_directory_is_gone_after_exit(self) -> None:
        with Fixture(SCRIPT) as f:
            f.run("storage", "buckets", "list")
            token_file = Path(f.tool_log()["token_file"])
            self.assertFalse(token_file.parent.exists(), token_file.parent)
            self.assertEqual(f.leftovers(), [])
        with Fixture(SCRIPT) as f:
            failing = f.bin / "gcloud"
            failing.write_text(TOOL + "exit 3\n")
            result = f.run("storage", "buckets", "list")
            self.assertEqual(result.returncode, 3)
            self.assertEqual(f.leftovers(), [])

    def test_the_command_runs_with_file_logging_off(self) -> None:
        """gcloud copies a command's output into a log file under CLOUDSDK_CONFIG/logs, so a printed
        token lands on disk (dispatch://INFRA-4824). The command runs with file logging off, whatever
        the caller set."""
        for caller in ({}, {"CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "false"}):
            with self.subTest(caller=caller), Fixture(SCRIPT) as f:
                result = f.run("auth", "print-access-token", env_extra=caller)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(f.tool_log()["file_logging"], "true")

    @unittest.skipUnless((SDK_BIN / "gcloud").is_file(), "no Google Cloud SDK at ~/.gcloud")
    def test_the_real_gcloud_writes_no_log_file(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("sh", "-c", PRINT_TOKEN_AND_LIST_LOGS,
                           env_extra={"PATH": f"{SDK_BIN}:{f.bin}:/usr/bin:/bin"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((f.log / "printed").read_text().strip(), TOKEN, "gcloud printed no token")
            self.assertEqual((f.log / "token-files").read_text(), "", "a log file holds the token")
            self.assertEqual((f.log / "log-files").read_text(), "", "gcloud wrote a log file")


if __name__ == "__main__":
    unittest.main()
