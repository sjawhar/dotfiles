#!/usr/bin/env python3
"""installers/gcloud-sdk.sh turns gcloud's file logging off in the SDK it installs and in each
configuration of the configuration directory (dispatch://INFRA-4824).

gcloud copies a command's output into a log file under its configuration directory's logs/, so a
token it prints lands on disk. docker-credential-gcloud runs the SDK's own gcloud by path, past
shims/gcloud, so the installer sets the installation property, which every caller of the SDK reads
whatever its environment. A Google VM's snap gcloud reads no property of this SDK, so the installer
also sets the property in each named configuration, which every gcloud reading that directory reads.

It also writes the kube context of each cluster in KUBE_CLUSTERS: a new machine gets the grade
cluster theorem-us-central1 in its own two files (dispatch://INFRA-5073), a machine an earlier
version set up keeps the files it has, and each context devbox/sami-devbox-check and
scripts/cluster-packing read is one the installer writes.

Technique: the real installer against a stand-in SDK under a scratch HOME. The stand-in gcloud logs
its argv, answers `version` with the installer's pinned version (so nothing is downloaded), writes
the property line a `--configuration <name> config set` asks for, and for `container clusters
get-credentials <cluster> --location <location> --project <project>` writes at $KUBECONFIG the
kubeconfig real gcloud writes: that cluster's one context, named as gcloud names it,
gke_<project>_<location>_<cluster>; for the cluster STUB_MISSING_CLUSTER names, it fails as gcloud
fails for a cluster that does not exist. A stand-in kubectl lists no context, so the installer
asks for each file, and its merge into ~/.kube/config writes nothing. The metadata host is a
closed port, so the Google VM branch (sudo) never runs. sami-devbox-check is read by sourcing it
without its dispatch, as test_sami_devbox_check_identity.py does, and cluster-packing is loaded
as test_cluster_packing.py loads it.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import cast

DOTFILES = Path(__file__).resolve().parents[2]
INSTALLER = DOTFILES / "installers" / "gcloud-sdk.sh"
CHECK = DOTFILES / "devbox" / "sami-devbox-check"
PACKING = DOTFILES / "scripts" / "cluster-packing"
_pin = re.search(r"^GCLOUD_SDK_VERSION=(\S+)$", INSTALLER.read_text(), re.M)
assert _pin, f"{INSTALLER} pins no GCLOUD_SDK_VERSION"
VERSION = _pin.group(1)
# The grade cluster (dispatch://INFRA-4464): regional, so gcloud names its context for the region.
CENTRAL = "theorem-us-central1"
CENTRAL_CONTEXT = "gke_theorem-agent-infra_us-central1_theorem-us-central1"
CENTRAL_CALL = "container clusters get-credentials theorem-us-central1 --location us-central1 --project theorem-agent-infra"

GCLOUD = r"""#!/bin/bash
printf '%s\n' "$*" >> "$LOG/gcloud.argv"
case "$1" in
  version) echo "$STUB_VERSION" ;;
  container)
    [[ "$2 $3 $5 $7 $#" == "clusters get-credentials --location --project 8" ]] || {
      echo "stub gcloud: not get-credentials <cluster> --location <location> --project <project>: $*" >&2; exit 2; }
    [[ "$4" != "${STUB_MISSING_CLUSTER:-}" ]] || {
      echo "ERROR: (gcloud.container.clusters.get-credentials) ResponseError: code=404, message=Not found: projects/$8/locations/$6/clusters/$4." >&2; exit 1; }
    name="gke_${8}_${6}_${4}"
    cat > "$KUBECONFIG" <<EOF
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: c3R1Yg==
    server: https://203.0.113.1
  name: $name
contexts:
- context:
    cluster: $name
    user: $name
  name: $name
current-context: $name
kind: Config
preferences: {}
users:
- name: $name
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      command: gke-gcloud-auth-plugin
      installHint: Install gke-gcloud-auth-plugin for use with kubectl by following
        https://cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl#install_plugin
      provideClusterInfo: true
EOF
    ;;
  --configuration)
    [[ "${*:3}" == "config set core/disable_file_logging true" ]] &&
      printf 'disable_file_logging = true\n' >> "${CLOUDSDK_CONFIG:-$HOME/.config/gcloud}/configurations/config_$2" ;;
esac
"""
KUBECTL = r"""#!/bin/bash
[[ "$*" != "config current-context" ]] || echo stub-context
"""


def executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def kubeconfig_contexts(path: Path) -> list[str]:
    """The context names of a kubeconfig in the block style gcloud and kubectl write: each item of the top-level
    `contexts:` list starts `- ` at column 0 with its own keys two columns in, so a `name:` there is a context's name
    and the `cluster:` and `user:` of its `context:` map, deeper in, are not. No `contexts:` block holds none."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if "contexts:" not in lines:
        return []
    names: list[str] = []
    for line in lines[lines.index("contexts:") + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(("- ", "  ")):
            break
        if line[2:].startswith("name:"):
            names.append(line[2:].removeprefix("name:").strip().strip("\"'"))
    return names


def packing_default_contexts() -> tuple[str, ...]:
    """DEFAULT_CONTEXTS of scripts/cluster-packing, loaded as scripts/tests/test_cluster_packing.py loads the script."""
    loader = importlib.machinery.SourceFileLoader("cluster_packing", str(PACKING))
    spec = importlib.util.spec_from_loader("cluster_packing", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return cast("tuple[str, ...]", module.DEFAULT_CONTEXTS)


class GcloudSdkInstallerTest(unittest.TestCase):
    maxDiff: int | None = None
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

    def test_each_configuration_turns_file_logging_off_once(self) -> None:
        for where in ("default directory", "CLOUDSDK_CONFIG"):
            with self.subTest(where=where):
                (self.log / "gcloud.argv").unlink(missing_ok=True)
                if where == "CLOUDSDK_CONFIG":
                    config_dir = self.home.parent / "elsewhere"
                    self.env["CLOUDSDK_CONFIG"] = str(config_dir)
                else:
                    config_dir = self.home / ".config" / "gcloud"
                configurations = config_dir / "configurations"
                configurations.mkdir(parents=True)
                (configurations / "config_box").write_text("[core]\naccount = box@example.com\n")
                (configurations / "config_default").write_text("")
                (configurations / "config_set").write_text("[core]\ndisable_file_logging = true\n")
                (configurations / "notes").write_text("not a configuration\n")
                calls = self.run_installer()
                installation = calls.index("config set --installation core/disable_file_logging true")
                per_configuration = [i for i, call in enumerate(calls) if call.startswith("--configuration ")]
                self.assertEqual(sorted(calls[i] for i in per_configuration), [
                    "--configuration box config set core/disable_file_logging true",
                    "--configuration default config set core/disable_file_logging true",
                ])
                self.assertTrue(all(i > installation for i in per_configuration), calls)
                self.assertIn("account = box@example.com", (configurations / "config_box").read_text())
                # A second run finds the property in every configuration and sets nothing.
                (self.log / "gcloud.argv").unlink()
                again = self.run_installer()
                self.assertEqual([call for call in again if call.startswith("--configuration ")], [])

    def test_a_new_machine_gets_theorem_us_central1_in_its_own_files(self) -> None:
        kube = self.home / ".kube"
        calls = self.run_installer()
        asked = [call for call in calls if call.startswith("container clusters get-credentials ")]
        # Once for ~/.kube/config.d/<cluster> and once for the named file: neither holds the context yet.
        self.assertEqual([call for call in asked if call.split()[3:4] == [CENTRAL]], [CENTRAL_CALL] * 2, asked)
        for path in (kube / f"{CENTRAL}.config", kube / "config.d" / CENTRAL):
            with self.subTest(file=str(path.relative_to(self.home))):
                self.assertTrue(path.is_file(), sorted(str(entry.relative_to(kube)) for entry in kube.rglob("*")))
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(kubeconfig_contexts(path), [CENTRAL_CONTEXT], path.read_text(encoding="utf-8"))
        # No call, no path and no file under ~/.kube names the grade cluster before it.
        self.assertEqual([call for call in calls if "grade-us-west1" in call], [])
        named = [str(entry.relative_to(kube)) for entry in sorted(kube.rglob("*"))
                 if "grade-us-west1" in str(entry.relative_to(kube))
                 or (entry.is_file() and "grade-us-west1" in entry.read_text(encoding="utf-8"))]
        self.assertEqual(named, [])

    def test_a_failed_get_credentials_leaves_no_scratch_directory(self) -> None:
        # theorem-us-central1 does not exist yet: gcloud fails, and the installer exits with gcloud's status.
        self.env["STUB_MISSING_CLUSTER"] = CENTRAL
        result = subprocess.run(["bash", str(INSTALLER)], env=self.env, capture_output=True, text=True, timeout=120,
                                check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ERROR: (gcloud.container.clusters.get-credentials) ResponseError: code=404", result.stderr)
        tmp = Path(self.env["TMPDIR"])
        self.assertEqual(sorted(str(entry.relative_to(tmp)) for entry in tmp.rglob("*")), [])

    def test_a_machine_set_up_before_keeps_its_grade_us_west1_files(self) -> None:
        kube = self.home / ".kube"
        (kube / "config.d").mkdir(mode=0o700, parents=True)
        west = "gke_theorem-agent-infra_us-west1-b_grade-us-west1"
        # What an earlier version of the installer wrote, with a line the stand-in gcloud never writes.
        earlier = "\n".join(["# written by an earlier version", "apiVersion: v1", "contexts:", "- context:",
                             f"    cluster: {west}", f"    user: {west}", f"  name: {west}", f"current-context: {west}",
                             "kind: Config", ""])
        kept = (kube / "grade-us-west1.config", kube / "config.d" / "grade-us-west1")
        for path in kept:
            _ = path.write_text(earlier, encoding="utf-8")
            path.chmod(0o600)
        _ = self.run_installer()
        for path in kept:
            with self.subTest(file=str(path.relative_to(self.home))):
                self.assertEqual(path.read_bytes().decode(), earlier)
        for path in (kube / f"{CENTRAL}.config", kube / "config.d" / CENTRAL):
            with self.subTest(file=str(path.relative_to(self.home))):
                self.assertTrue(path.is_file(), sorted(str(entry.relative_to(kube)) for entry in kube.rglob("*")))

    def test_every_context_the_check_and_the_packing_script_read_is_one_the_installer_writes(self) -> None:
        _ = self.run_installer()
        written = {context for path in (self.home / ".kube" / "config.d").iterdir()
                   for context in kubeconfig_contexts(path)}
        body = f'source <(sed \'/^case "${{1:-}}" in/,$d\' {CHECK}); printf "%s\\n" "$KUBE_CONTEXT" "$GRADE_KUBE_CONTEXT"'
        result = subprocess.run(["bash", "-c", body], env=self.env, capture_output=True, text=True, timeout=60,
                                check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        check = result.stdout.splitlines()
        self.assertEqual(len(check), 2, result.stdout)
        kube_context, grade_context = check
        defaults = packing_default_contexts()
        reads = {"KUBE_CONTEXT": kube_context, "GRADE_KUBE_CONTEXT": grade_context,
                 **{f"DEFAULT_CONTEXTS[{i}]": context for i, context in enumerate(defaults)}}
        for name, context in reads.items():
            with self.subTest(read=name):
                self.assertIn(context, written)
        # The packing script reads the grade cluster the check asks.
        self.assertIn(grade_context, defaults)


if __name__ == "__main__":
    unittest.main()
