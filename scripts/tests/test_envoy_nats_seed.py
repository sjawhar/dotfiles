"""envoy-nats-seed: its two output modes, the contract an envoy.json `natsSeed` entry relies on, and
its two seed sources.

`--value` prints the seed itself (the client reads the command's stdout as the seed) and exits 1
when there is none, so a missing credential is an error the client names; path mode prints the
cache file's path and exits 0 either way. With no source flag the seed comes from AWS through
`aws`; `--gcp-secret`, `--gcp-project` and `--gcloud-configuration` name a seed in GCP Secret
Manager, fetched through `gcloud`, cached under a file of its own.

Technique: the real script over a scratch XDG_RUNTIME_DIR, with fake `aws` and `gcloud` first on
PATH, so no run reaches AWS or GCP. Each fake records its argv and environment, and `gcloud` the
mode of the `--out-file` it was handed; it then fails with the stderr a test gave it, or answers
with the seed a test gave it (`aws` on stdout, `gcloud` into the `--out-file`). A fresh cache file
starts no background refresh and a fresh miss marker suppresses the fetch; a cache aged with
`os.utime` past the script's hour starts one detached refresh, which a test waits for. One test
puts this repository's `shims/gcloud` in front of the fake `gcloud`, as a dotfiles machine's PATH
does, and sets the variables a shell selects a gcloud configuration or account with.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "envoy-nats-seed"
GCLOUD_SHIM = Path(__file__).resolve().parents[2] / "shims" / "gcloud"
SEED = "SU" + "A" * 56
AWS_SEED = "SU" + "W" * 56
GCP_SEED = "SU" + "G" * 56
OLD_SEED = "SU" + "D" * 56
REFRESH_S = 3600

DEFAULT_CACHE = "nats-nkey-seed-omp-session"
GCP_CACHE = "nats-nkey-seed-gcp-test-project.test-secret"
GCP_ARGS = (
    "--gcp-secret", "test-secret", "--gcp-project", "test-project",
    "--gcloud-configuration", "test-config",
)
GCLOUD_ARGV = [
    "--configuration", "test-config", "secrets", "versions", "access", "latest",
    "--secret", "test-secret", "--project", "test-project", "--out-file",
]
AWS_ARGV = [
    "secretsmanager", "get-secret-value", "--region", "us-west-2",
    "--secret-id", "production/envoy/nats-nkey-seed-omp-session",
    "--query", "SecretString", "--output", "text",
]
GCLOUD_ERROR = (
    "ERROR: (gcloud.secrets.versions.access) NOT_FOUND: Secret "
    "[projects/test-project/secrets/test-secret] not found or has no versions."
)

FAKE = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

FAKE = Path({fake!r})
TOOL = {tool!r}
record = {{"tool": TOOL, "argv": sys.argv[1:], "env": dict(os.environ)}}
out = None
if "--out-file" in sys.argv:
    out = Path(sys.argv[sys.argv.index("--out-file") + 1])
    record["out_file_mode"] = out.stat().st_mode & 0o777 if out.exists() else None
with (FAKE / "calls.jsonl").open("a") as log:
    log.write(json.dumps(record) + "\\n")
fail = FAKE / (TOOL + ".fail")
if fail.exists():
    sys.stderr.write(fail.read_text())
    sys.exit(1)
seed = (FAKE / (TOOL + ".seed")).read_text()
if out is None:
    sys.stdout.write(seed + "\\n")
else:
    out.write_text(seed)
'''


class EnvoyNatsSeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = Path(tempfile.mkdtemp(prefix="envoy-nats-seed-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.runtime)], check=False)
        self.dir = self.runtime / "envoy"
        self.dir.mkdir(mode=0o700)
        self.seed = self.dir / DEFAULT_CACHE
        self.gcp_seed = self.dir / GCP_CACHE
        self.fake = self.runtime / "fake"
        self.bin = self.fake / "bin"
        self.bin.mkdir(parents=True)
        for tool in ("aws", "gcloud"):
            path = self.bin / tool
            path.write_text(FAKE.format(fake=str(self.fake), tool=tool))
            path.chmod(0o755)

    def run_script(
        self, *args: str, path_head: Path | None = None, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        path = f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        if path_head is not None:
            path = f"{path_head}{os.pathsep}{path}"
        env = {
            "PATH": path,
            "HOME": str(self.runtime),
            "XDG_RUNTIME_DIR": str(self.runtime),
            **(extra_env or {}),
        }
        return subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=30)

    def answer(self, tool: str, seed: str) -> None:
        (self.fake / f"{tool}.seed").write_text(seed)

    def fail_with(self, tool: str, stderr: str) -> None:
        (self.fake / f"{tool}.fail").write_text(stderr)

    def calls(self, tool: str) -> list[dict]:
        log = self.fake / "calls.jsonl"
        if not log.exists():
            return []
        records = [json.loads(line) for line in log.read_text().splitlines()]
        return [r for r in records if r["tool"] == tool]

    def cache(self, path: Path, seed: str, age_s: int = 0) -> None:
        path.write_text(seed)
        path.chmod(0o600)
        if age_s:
            then = time.time() - age_s
            os.utime(path, (then, then))

    def wait_for_content(self, path: Path, seed: str) -> None:
        """Waits for the detached refresh to replace `path` and remove its temporary files."""
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            temps = [p for p in self.dir.iterdir() if p.name.startswith(path.name + ".")
                     and p.suffix not in (".lock", ".miss")]
            if path.exists() and path.read_text() == seed and not temps:
                return
            time.sleep(0.05)
        self.fail_with_state(f"{path.name} never came to hold the refreshed seed")

    def fail_with_state(self, message: str) -> None:
        listing = sorted(p.name for p in self.dir.iterdir())
        self.fail(f"{message}; directory holds {listing}; calls {self.calls('aws') + self.calls('gcloud')}")

    def assert_gcloud_fetched_into_a_private_temp_file(self, call: dict) -> None:
        self.assertEqual(call["argv"][:-1], GCLOUD_ARGV)
        out = Path(call["argv"][-1])
        self.assertEqual(out.parent, self.dir)
        self.assertTrue(out.name.startswith(GCP_CACHE + "."), out.name)
        self.assertEqual(call["out_file_mode"], 0o600)

    def assert_no_seed_in_any_argv_or_environment(self) -> None:
        for call in self.calls("aws") + self.calls("gcloud"):
            for seed in (AWS_SEED, GCP_SEED, OLD_SEED):
                self.assertFalse(any(seed in arg for arg in call["argv"]), call["argv"])
                self.assertFalse(any(seed in value for value in call["env"].values()))

    def test_value_prints_the_cached_seed_and_path_mode_its_path(self) -> None:
        self.seed.write_text(SEED)
        self.seed.chmod(0o600)
        value = self.run_script("--value")
        self.assertEqual((value.returncode, value.stdout), (0, SEED))
        path_mode = self.run_script()
        self.assertEqual((path_mode.returncode, path_mode.stdout.strip()), (0, str(self.seed)))

    def test_value_exits_1_naming_the_miss_while_path_mode_still_exits_0(self) -> None:
        (self.dir / "nats-nkey-seed-omp-session.miss").write_text("the aws CLI is not installed\n")
        value = self.run_script("--value")
        self.assertEqual((value.returncode, value.stdout), (1, ""))
        self.assertIn("the aws CLI is not installed", value.stderr)
        path_mode = self.run_script()
        self.assertEqual((path_mode.returncode, path_mode.stdout), (0, ""))

    def test_gcp_seed_is_fetched_through_an_out_file_cached_and_served_by_value(self) -> None:
        self.answer("gcloud", GCP_SEED)
        first = self.run_script(*GCP_ARGS, "--value")
        self.assertEqual((first.returncode, first.stdout, first.stderr), (0, GCP_SEED, ""))
        self.assertEqual(self.gcp_seed.read_text(), GCP_SEED)
        self.assertEqual(self.gcp_seed.stat().st_mode & 0o777, 0o600)
        (call,) = self.calls("gcloud")
        self.assert_gcloud_fetched_into_a_private_temp_file(call)

        second = self.run_script(*GCP_ARGS, "--value")
        self.assertEqual((second.returncode, second.stdout), (0, GCP_SEED))
        path_mode = self.run_script(*GCP_ARGS)
        self.assertEqual((path_mode.returncode, path_mode.stdout.strip()), (0, str(self.gcp_seed)))
        self.assertEqual(len(self.calls("gcloud")), 1)
        self.assertEqual(self.calls("aws"), [])
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), [GCP_CACHE, GCP_CACHE + ".lock"])
        self.assert_no_seed_in_any_argv_or_environment()

    def test_failing_gcloud_names_its_error_and_value_exits_1(self) -> None:
        self.fail_with("gcloud", GCLOUD_ERROR + "\n\nUpdates are available for some Google Cloud CLI "
                       "components.  To install them,\nplease run:\n  $ gcloud components update\n")
        value = self.run_script(*GCP_ARGS, "--value")
        self.assertEqual((value.returncode, value.stdout), (1, ""))
        self.assertIn(GCLOUD_ERROR, value.stderr)
        lines = value.stderr.splitlines()
        self.assertTrue(lines)
        for line in lines:
            self.assertTrue(line.startswith("envoy-nats-seed: "), line)
        self.assertNotIn("components update", value.stderr)
        self.assertEqual((self.dir / (GCP_CACHE + ".miss")).read_text(), GCLOUD_ERROR + "\n")
        self.assertFalse((self.dir / (DEFAULT_CACHE + ".miss")).exists())
        self.assertEqual(self.calls("aws"), [])

    def test_aged_gcp_cache_refreshes_through_gcloud_and_never_aws(self) -> None:
        self.cache(self.gcp_seed, OLD_SEED, age_s=2 * REFRESH_S)
        self.answer("gcloud", GCP_SEED)
        self.answer("aws", AWS_SEED)
        value = self.run_script(*GCP_ARGS, "--value")
        self.assertEqual((value.returncode, value.stdout), (0, OLD_SEED))
        self.wait_for_content(self.gcp_seed, GCP_SEED)
        (call,) = self.calls("gcloud")
        self.assert_gcloud_fetched_into_a_private_temp_file(call)
        self.assertEqual(self.calls("aws"), [])
        self.assertFalse(self.seed.exists())
        self.assert_no_seed_in_any_argv_or_environment()

    def test_aged_default_cache_refreshes_through_aws_and_never_gcloud(self) -> None:
        self.cache(self.gcp_seed, GCP_SEED)
        self.cache(self.seed, OLD_SEED, age_s=2 * REFRESH_S)
        self.answer("gcloud", "SU" + "X" * 56)
        self.answer("aws", AWS_SEED)
        gcp = self.run_script(*GCP_ARGS, "--value")
        self.assertEqual((gcp.returncode, gcp.stdout), (0, GCP_SEED))
        value = self.run_script("--value")
        self.assertEqual((value.returncode, value.stdout), (0, OLD_SEED))
        self.wait_for_content(self.seed, AWS_SEED)
        (call,) = self.calls("aws")
        self.assertEqual(call["argv"], AWS_ARGV)
        self.assertEqual(self.calls("gcloud"), [])
        self.assertEqual(self.gcp_seed.read_text(), GCP_SEED)
        self.assert_no_seed_in_any_argv_or_environment()

    def test_the_two_sources_never_share_a_cache_miss_or_lock_file(self) -> None:
        self.answer("aws", AWS_SEED)
        self.fail_with("gcloud", GCLOUD_ERROR + "\n")
        default = self.run_script("--value")
        self.assertEqual((default.returncode, default.stdout), (0, AWS_SEED))
        gcp = self.run_script(*GCP_ARGS, "--value")
        self.assertEqual((gcp.returncode, gcp.stdout), (1, ""))
        self.assertIn(GCLOUD_ERROR, gcp.stderr)
        again = self.run_script("--value")
        self.assertEqual((again.returncode, again.stdout), (0, AWS_SEED))
        self.assertEqual(
            sorted(p.name for p in self.dir.iterdir()),
            [GCP_CACHE + ".lock", GCP_CACHE + ".miss", DEFAULT_CACHE, DEFAULT_CACHE + ".lock"],
        )

        (self.fake / "gcloud.fail").unlink()
        self.answer("gcloud", GCP_SEED)
        refresh = self.run_script(*GCP_ARGS, "--refresh")
        self.assertEqual((refresh.returncode, refresh.stdout.strip()), (0, str(self.gcp_seed)))
        self.assertEqual(
            sorted(p.name for p in self.dir.iterdir()),
            [GCP_CACHE, GCP_CACHE + ".lock", DEFAULT_CACHE, DEFAULT_CACHE + ".lock"],
        )
        self.assertEqual((self.gcp_seed.read_text(), self.seed.read_text()), (GCP_SEED, AWS_SEED))
        self.assertEqual(len(self.calls("aws")), 1)
        self.assertEqual(len(self.calls("gcloud")), 2)
        self.assert_no_seed_in_any_argv_or_environment()

    def test_an_incomplete_or_malformed_gcp_source_is_refused_and_runs_nothing(self) -> None:
        for args in (
            ("--gcp-secret", "test-secret", "--value"),
            ("--gcp-secret", "test-secret", "--gcp-project", "test-project", "--value"),
            ("--gcp-secret", "--value", "--gcp-project", "test-project", "--gcloud-configuration", "c"),
            ("--gcp-secret", "a/b", "--gcp-project", "test-project", "--gcloud-configuration", "c"),
            ("--gcp-secret", "s", "--gcp-project", "Bad.Project", "--gcloud-configuration", "c"),
            ("--gcp-secret", "s", "--gcp-project", "p", "--gcloud-configuration"),
        ):
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn("envoy-nats-seed: a GCP source needs", result.stderr)
        self.assertEqual(self.calls("aws") + self.calls("gcloud"), [])
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_gcp_fetch_ignores_the_callers_gcloud_selection(self) -> None:
        """shims/gcloud refuses a GCLOUD_ACCOUNT naming no configuration before gcloud runs, and
        CLOUDSDK_CORE_ACCOUNT would outrank the named configuration's account: the fetch must reach
        gcloud with none of the three selection variables a calling shell may hold."""
        shim_bin = self.fake / "shim-bin"
        shim_bin.mkdir()
        (shim_bin / "gcloud").symlink_to(GCLOUD_SHIM)
        self.answer("gcloud", GCP_SEED)
        selection = {
            "GCLOUD_ACCOUNT": "no-such-configuration",
            "CLOUDSDK_ACTIVE_CONFIG_NAME": "other-configuration",
            "CLOUDSDK_CORE_ACCOUNT": "someone-else@example.com",
        }
        value = self.run_script(*GCP_ARGS, "--value", path_head=shim_bin, extra_env=selection)
        self.assertEqual((value.returncode, value.stdout, value.stderr), (0, GCP_SEED, ""))
        (call,) = self.calls("gcloud")
        self.assert_gcloud_fetched_into_a_private_temp_file(call)
        for name in selection:
            self.assertNotIn(name, call["env"])


if __name__ == "__main__":
    unittest.main()
