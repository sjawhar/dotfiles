#!/usr/bin/env python3
"""Install only on a non-Google box, and keep a stopped service stopped."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from gce_metadata_stub import MetadataStub

ROOT = Path(__file__).resolve().parents[2]


class MetadataInstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.log = self.home / "calls"
        systemctl = self.bin / "systemctl"
        systemctl.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >>"$CALLS"\ncase "$*" in *is-enabled*) [[ "${UNIT_ENABLED:-disabled}" == enabled ]];; *is-active*) [[ "${UNIT_ACTIVE:-inactive}" == active ]];; esac\n')
        systemctl.chmod(0o755)
        config = self.home / ".config/gcloud"
        config.mkdir(parents=True)
        (config / "application_default_credentials.json").write_text(json.dumps({"type": "external_account"}))
        self.env = {**os.environ, "HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "CALLS": str(self.log), "DOTFILES_DIR": str(ROOT), "GCE_METADATA_HOST": "127.0.0.1:9"}
        self.env.pop("AGENTBOX_BOX", None)

    def install(self):
        result = subprocess.run(["bash", str(ROOT / "installers/gcloud-metadata.sh")], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.log.read_text() if self.log.exists() else ""

    def test_google_vm_arms_nothing(self):
        metadata = MetadataStub.start()
        self.addCleanup(metadata.stop)
        self.env["GCE_METADATA_HOST"] = metadata.host
        self.assertEqual(self.install(), "")
        self.assertFalse((self.home / ".config/systemd/user/gcloud-metadata.service").exists())

    def test_non_google_box_links_and_starts_new_service(self):
        calls = self.install()
        self.assertIn("enable --now gcloud-metadata.service", calls)
        unit = self.home / ".config/systemd/user/gcloud-metadata.service"
        self.assertEqual(unit.resolve(), ROOT / "devbox/gcloud-metadata.service")

    def test_disabled_inactive_existing_service_stays_stopped(self):
        unit = self.home / ".config/systemd/user/gcloud-metadata.service"
        unit.parent.mkdir(parents=True)
        unit.symlink_to(ROOT / "devbox/gcloud-metadata.service")
        calls = self.install()
        self.assertNotIn("enable --now", calls)

    def test_existing_active_or_enabled_service_is_started(self):
        unit = self.home / ".config/systemd/user/gcloud-metadata.service"
        unit.parent.mkdir(parents=True)
        unit.symlink_to(ROOT / "devbox/gcloud-metadata.service")
        for active, enabled in (("active", "disabled"), ("inactive", "enabled"), ("active", "enabled")):
            with self.subTest(active=active, enabled=enabled):
                self.log.unlink(missing_ok=True)
                self.env.update(UNIT_ACTIVE=active, UNIT_ENABLED=enabled)
                self.assertIn("enable --now gcloud-metadata.service", self.install())


if __name__ == "__main__":
    unittest.main()
