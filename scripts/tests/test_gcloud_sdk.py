#!/usr/bin/env python3
"""installers/gcloud-sdk.sh turns gcloud's file logging off in the SDK it installs (dispatch://INFRA-4824).

gcloud copies a command's output into a log file under its configuration directory's logs/, so a
token it prints lands on disk. docker-credential-gcloud runs the SDK's own gcloud by path, past
shims/gcloud, so the installer sets the installation property, which every caller of the SDK reads
whatever its environment.

Technique: the real installer against a stand-in SDK under a scratch HOME. The stand-in gcloud logs
its argv, answers `version` with the installer's pinned version (so nothing is downloaded) and
writes the kubeconfig `get-credentials` asks for; a stand-in kubectl answers the context reads. The
metadata host is a closed port, so the Google VM branch (sudo) never runs.
"""

from __future__ import annotations

import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
INSTALLER = DOTFILES / "installers" / "gcloud-sdk.sh"
_pin = re.search(r"^GCLOUD_SDK_VERSION=(\S+)$", INSTALLER.read_text(), re.M)
assert _pin, f"{INSTALLER} pins no GCLOUD_SDK_VERSION"
VERSION = _pin.group(1)

GCLOUD = r"""#!/bin/bash
printf '%s\n' "$*" >> "$LOG/gcloud.argv"
case "$1" in
  version) echo "$STUB_VERSION" ;;
  container) : > "$KUBECONFIG" ;;
esac
"""
KUBECTL = r"""#!/bin/bash
[[ "$*" != "config current-context" ]] || echo stub-context
"""


def executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class GcloudSdkInstallerTest(unittest.TestCase):
    home: Path
    log: Path
    env: dict[str, str]

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.home = root / "home"
        self.log = root / "log"
        self.log.mkdir()
        (root / "tmp").mkdir()
        sdk_bin = self.home / ".gcloud" / "google-cloud-sdk" / "bin"
        executable(sdk_bin / "gcloud", GCLOUD)
        executable(sdk_bin / "gke-gcloud-auth-plugin", "#!/bin/sh\n")
        executable(sdk_bin / "docker-credential-gcloud", "#!/bin/sh\n")
        executable(root / "bin" / "kubectl", KUBECTL)
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{root / 'bin'}:/usr/bin:/bin",
            "TMPDIR": str(root / "tmp"),
            "LOG": str(self.log),
            "STUB_VERSION": VERSION,
            "GCE_METADATA_HOST": "127.0.0.1:9",
        }

    def run_installer(self) -> list[str]:
        result = subprocess.run(["bash", str(INSTALLER)], env=self.env, capture_output=True, text=True,
                                timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return (self.log / "gcloud.argv").read_text().splitlines()

    def test_the_installation_turns_file_logging_off_before_any_other_gcloud_command(self) -> None:
        for run in ("first", "again"):
            with self.subTest(run=run):
                (self.log / "gcloud.argv").unlink(missing_ok=True)
                calls = self.run_installer()
                # The pinned-version read comes first; every command after it runs with logging off.
                first_other = next(i for i, call in enumerate(calls) if not call.startswith("version "))
                self.assertEqual(calls[first_other], "config set --installation core/disable_file_logging true")


if __name__ == "__main__":
    unittest.main()
