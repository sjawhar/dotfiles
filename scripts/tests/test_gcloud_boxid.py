#!/usr/bin/env python3
"""installers/gcloud-boxid.sh writes infra-kubeconfig with two contexts, and dispatch's is the current one.

Scripts written on sami-rocqperf name KUBECONFIG=/mnt/nvme/<user>/boxid/infra-kubeconfig. A script
that names no context reads dispatch, as before. A box creates and reads its build Jobs on the Kueue
MultiKueue manager theorem-multikueue (dispatch://INFRA-5858, R3), so the file holds the manager's
context too. Both come from ~/.kube/config, where installers/gcloud-sdk.sh writes them, and neither
holds a credential: gke-gcloud-auth-plugin takes its token from gcloud.

Technique: a byte copy of the real installer, in a scratch tree beside two stand-ins, for
installers/gcloud-box.sh (its gcloud configuration is test_gcloud_box.py's) and for
scripts/on-google-vm (exit 0: a Google VM). The real kubectl reads and writes the kubeconfigs, since
what is tested is what kubectl makes of them. Skipped where kubectl is not on PATH.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
INSTALLER = DOTFILES / "installers" / "gcloud-boxid.sh"
KUBECTL = shutil.which("kubectl")
DISPATCH = "gke_theorem-agent-infra_us-west1_dispatch"
MANAGER = "gke_theorem-agent-infra_us-central1_theorem-multikueue"
# ~/.kube/config as installers/gcloud-sdk.sh leaves it: one context for each cluster of KUBE_CLUSTERS, each
# with a user that runs gke-gcloud-auth-plugin, and a current context that is not dispatch's.
CONTEXTS = (DISPATCH, "gke_theorem-agent-infra_us-west1-b_grade-us-west1",
            "gke_theorem-agent-infra_us-central1_theorem-us-central1", MANAGER)
PLUGIN = {"apiVersion": "client.authentication.k8s.io/v1beta1", "command": "gke-gcloud-auth-plugin",
          "provideClusterInfo": True, "interactiveMode": "IfAvailable"}


def kube_config(contexts: tuple[str, ...]) -> dict:
    return {"apiVersion": "v1", "kind": "Config", "preferences": {}, "current-context": contexts[1],
            "clusters": [{"name": c, "cluster": {"server": f"https://10.0.0.{i + 1}", "certificate-authority-data": "Q0E="}}
                         for i, c in enumerate(contexts)],
            "users": [{"name": c, "user": {"exec": PLUGIN}} for c in contexts],
            "contexts": [{"name": c, "context": {"cluster": c, "user": c}} for c in contexts]}


def executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@unittest.skipUnless(KUBECTL, "no kubectl on PATH")
class GcloudBoxidKubeconfig(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.installer = root / "dotfiles" / "installers" / "gcloud-boxid.sh"
        self.installer.parent.mkdir(parents=True)
        shutil.copyfile(INSTALLER, self.installer)
        executable(root / "dotfiles" / "installers" / "gcloud-box.sh", "#!/bin/bash\necho 'gcloud-box: stand-in'\n")
        executable(root / "dotfiles" / "scripts" / "on-google-vm", "#!/bin/bash\nexit 0\n")
        self.home = root / "home"
        (self.home / ".kube").mkdir(parents=True)
        self.write_kube_config(CONTEXTS)
        (root / "nvme").mkdir()
        (root / "tmp").mkdir()
        self.file = root / "nvme" / "boxid" / "infra-kubeconfig"
        self.env = {"HOME": str(self.home), "PATH": f"{Path(KUBECTL or '').parent}:/usr/bin:/bin",
                    "TMPDIR": str(root / "tmp"), "GCLOUD_BOXID_DIR": str(self.file.parent)}

    def write_kube_config(self, contexts: tuple[str, ...]) -> None:
        (self.home / ".kube" / "config").write_text(json.dumps(kube_config(contexts)), encoding="utf-8")

    def run_installer(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(self.installer)], env=self.env, capture_output=True, text=True,
                              timeout=120, check=False)

    def kubectl(self, *args: str) -> str:
        done = subprocess.run([KUBECTL or "kubectl", "--kubeconfig", str(self.file), *args], env=self.env,
                              capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_the_file_holds_dispatch_as_its_current_context_and_the_manager(self) -> None:
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"wrote {self.file} (contexts {DISPATCH}, the current one, and {MANAGER})", result.stdout)
        self.assertEqual(sorted(self.kubectl("config", "get-contexts", "-o", "name").split()), sorted((DISPATCH, MANAGER)))
        self.assertEqual(self.kubectl("config", "current-context").strip(), DISPATCH)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        view = json.loads(self.kubectl("config", "view", "--raw", "-o", "json"))
        self.assertEqual(sorted(c["name"] for c in view["clusters"]), sorted((DISPATCH, MANAGER)))
        # No credential: each user only runs the plugin.
        for user in view["users"]:
            self.assertEqual(list(user["user"]), ["exec"], user)
            self.assertEqual(user["user"]["exec"]["command"], "gke-gcloud-auth-plugin")

    def test_a_second_run_changes_nothing(self) -> None:
        self.assertEqual(self.run_installer().returncode, 0)
        before = self.file.read_bytes()
        again = self.run_installer()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertNotIn("wrote", again.stdout)
        self.assertEqual(self.file.read_bytes(), before)

    def test_a_kube_config_without_the_managers_context_fails_and_leaves_the_file(self) -> None:
        """Before installers/gcloud-sdk.sh writes the manager's context, the run fails and keeps the file it had."""
        self.file.parent.mkdir(mode=0o700)
        self.file.write_text("the file of an earlier run\n", encoding="utf-8")
        self.write_kube_config(CONTEXTS[:3])
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.file.read_text(encoding="utf-8"), "the file of an earlier run\n")


if __name__ == "__main__":
    unittest.main()
