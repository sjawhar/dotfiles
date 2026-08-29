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


class GcloudRouting(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.stub_dir = Path(self.tmp.name)
        write_stub(
            self.stub_dir,
            "gcloud",
            'echo "CONFIG=${CLOUDSDK_ACTIVE_CONFIG_NAME:-<unset>}"\n'
            'printf "ARG=%s\\n" "$@"',
        )
        # Fake $HOME so the default configurations path
        # ($HOME/.config/gcloud/configurations) never touches Sami's real one.
        self.fake_home = self.stub_dir / "home"
        self.configs_dir = self.fake_home / ".config" / "gcloud" / "configurations"
        self.configs_dir.mkdir(parents=True)
        for name in ("default", "gws-personal", "theorem"):
            (self.configs_dir / f"config_{name}").write_text("[core]\n", encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
