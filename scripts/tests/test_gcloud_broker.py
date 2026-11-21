#!/usr/bin/env python3
"""scripts/gcloud-broker: the command runs with gcloud's file logging off (dispatch://INFRA-4824).

gcloud copies a command's output into a log file under CLOUDSDK_CONFIG/logs, so a printed access
token lands on disk. gcloud-broker runs its command in a fresh CLOUDSDK_CONFIG with the broker's
token in CLOUDSDK_AUTH_ACCESS_TOKEN_FILE; the fake secret-run of test_gcloud_reader hands the
token over as secret-run does.
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

    def run(self, *args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmpdir),
            "TMPDIR": str(self.tmpdir),
            "LOG": str(self.log),
            "FAKE_TOKEN": TOKEN,
            **(env_extra or {}),
        }
        return subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)

    def tool_log(self) -> dict[str, str]:
        path = self.log / "tool.log"
        if not path.exists():
            return {}
        return {key: value for key, _, value in (line.partition(" ") for line in path.read_text().splitlines())}


class GcloudBrokerFileLoggingTest(unittest.TestCase):
    def test_the_command_runs_with_file_logging_off(self) -> None:
        for caller in ({}, {"CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "false"}):
            with self.subTest(caller=caller), Fixture() as f:
                result = f.run("gcloud", "auth", "print-access-token", env_extra=caller)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(f.tool_log()["token"], TOKEN)
                self.assertEqual(f.tool_log()["file_logging"], "true")

    @unittest.skipUnless((SDK_BIN / "gcloud").is_file(), "no Google Cloud SDK at ~/.gcloud")
    def test_the_real_gcloud_writes_no_log_file(self) -> None:
        with Fixture() as f:
            result = f.run("sh", "-c", PRINT_TOKEN_AND_LIST_LOGS,
                           env_extra={"PATH": f"{SDK_BIN}:{f.bin}:/usr/bin:/bin"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((f.log / "printed").read_text().strip(), TOKEN, "gcloud printed no token")
            self.assertEqual((f.log / "token-files").read_text(), "", "a log file holds the token")
            self.assertEqual((f.log / "log-files").read_text(), "", "gcloud wrote a log file")


if __name__ == "__main__":
    unittest.main()
