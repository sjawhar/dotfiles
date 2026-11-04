#!/usr/bin/env python3
"""Host caches, disk defaults, admission and online growth for agent boxes."""
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "agentbox"


class BoxStorage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {**os.environ, "AGENTBOX_IDENTITY_FILE": str(self.root / "not-a-box"),
                    "AGENTBOX_BOX": "", "AGENTBOX_HOST": "sami-devbox",
                    "AGENTBOX_BOXES": str(self.root / "boxes"),
                    "AGENTBOX_DISKS": str(self.root / "disks"),
                    "AGENTBOX_CACHE_ROOT": str(self.root / "box-cache")}
        self.env.pop("AGENTBOX_DISK", None)

    def run_shell(self, body, **env):
        return subprocess.run(["bash", "-c", f"source {shlex.quote(str(SCRIPT))}\n{body}"],
                              env={**self.env, **env}, text=True, capture_output=True, timeout=30)

    def test_disk_default_depends_on_physical_host(self):
        for host, size in (("sami-devbox", "128g"), ("sami-rocqperf", "32g"), ("laptop", "32g")):
            with self.subTest(host=host):
                result = self.run_shell('echo "$BOX_DISK"', AGENTBOX_HOST=host)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), size)

    def test_environment_has_per_box_cache_paths_not_a_cargo_target(self):
        result = self.run_shell('NETWORK_GATEWAY=172.31.0.1; forward_channels() { :; }; loopback_services() { :; }; session_env agentbox-cachetest')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = result.stdout.splitlines()
        cache = self.root / "box-cache/agentbox-cachetest"
        for key, suffix in (("XDG_CACHE_HOME", ""), ("GOCACHE", "go-build"),
                            ("GOMODCACHE", "go-mod"), ("npm_config_cache", "npm"),
                            ("BUN_INSTALL_CACHE_DIR", "bun"), ("UV_CACHE_DIR", "uv"),
                            ("PIP_CACHE_DIR", "pip")):
            self.assertIn(f"{key}={cache / suffix}", args)
        self.assertFalse(any(x.startswith("CARGO_TARGET_DIR=") for x in args))

    def test_cache_mounts_are_created_outside_the_image(self):
        # Other mount construction is covered by test_agentbox_dirs. Exercise the cache helper.
        result = self.run_shell('box_cache_mounts agentbox-cachetest')
        self.assertEqual(result.returncode, 0, result.stderr)
        cache = self.root / "box-cache/agentbox-cachetest"
        for child in ("go-build", "go-mod", "npm", "bun", "uv", "pip", "cargo-registry", "cargo-git"):
            self.assertTrue((cache / child).is_dir(), child)
        self.assertIn(f"{cache}:{cache}", result.stdout.splitlines())
        self.assertIn(f"{cache}/cargo-registry:{Path.home()}/.cargo/registry", result.stdout.splitlines())
        self.assertIn(f"{cache}/cargo-git:{Path.home()}/.cargo/git", result.stdout.splitlines())

    def test_closed_box_removes_only_its_own_host_cache(self):
        first = self.root / "box-cache/agentbox-one"
        second = self.root / "box-cache/agentbox-two"
        first.mkdir(parents=True)
        second.mkdir()
        (first / "cache").write_text("build output")
        result = self.run_shell('docker() { return 0; }; close_box_cache agentbox-one')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(first.exists())
        self.assertTrue(second.is_dir())

    def test_cache_is_kept_until_container_removal_is_confirmed(self):
        cache = self.root / "box-cache/agentbox-one"
        cache.mkdir(parents=True)
        for docker in ('docker() { echo agentbox-one; };', 'docker() { return 1; };'):
            result = self.run_shell(docker + ' close_box_cache agentbox-one')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(cache.is_dir())
            self.assertIn("cache", result.stderr)

    def test_failed_cache_creation_never_emits_a_bind_for_docker_to_create(self):
        (self.root / "box-cache").write_text("not a directory")
        result = self.run_shell('out="$(box_cache_mounts agentbox-cachetest)" || exit 7; printf "%s" "$out"')
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_admission_refuses_under_500_gib_and_accepts_the_boundary(self):
        for free, allowed in ((500 * 1024**3 - 1, False), (500 * 1024**3, True)):
            result = self.run_shell(f'df() {{ printf "Avail\\n{free}\\n"; }}; check_box_disk_space')
            self.assertEqual(result.returncode == 0, allowed, result.stderr)
            if not allowed:
                self.assertIn("500 GiB", result.stderr)
                self.assertIn(str(self.root), result.stderr)

    def test_every_image_creation_path_checks_host_space_before_creating_state(self):
        result = self.run_shell('df() { printf "Avail\\n535797170176\\n"; }; truncate() { echo unexpected-truncate; exit 99; }; set_disk_bytes; open_box_disk agentbox-denied')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("500 GiB", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse((self.root / "boxes").exists())

    def test_growth_refreshes_loop_then_resizes_and_refuses_shrink(self):
        disks = self.root / "disks"
        disks.mkdir()
        image = disks / "agentbox-test.img"
        with image.open("wb") as handle:
            handle.truncate(1024**3)
        body = '''
resolve_box() { echo agentbox-test; }
mountpoint() { return 0; }
findmnt() { echo /dev/loop123; }
sudo() {
    shift
    if [[ "$1" == losetup && "$2" == -j ]]; then echo /dev/loop123
    else printf '%s\\n' "$*"; fi
}
cmd_grow agentbox-test 2g
'''
        result = self.run_shell(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(image.stat().st_size, 2 * 1024**3)
        self.assertLess(result.stdout.index("losetup -c /dev/loop123"), result.stdout.index("resize2fs /dev/loop123"))
        result = self.run_shell(body.replace("2g", "1g"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(image.stat().st_size, 2 * 1024**3)
        self.assertIn("shrink", result.stderr)


if __name__ == "__main__":
    unittest.main()
