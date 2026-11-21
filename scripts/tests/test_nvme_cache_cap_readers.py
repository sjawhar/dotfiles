#!/usr/bin/env python3
"""devbox/nvme-cache-cap never removes a cache a running process may read (dispatch://INFRA-3905).

Over a cap it used to run each tool's cleaner (`go clean -modcache`, `uv cache clean`, `pip cache
purge`, `bun pm cache rm`, `mise cache clear`) or `rm -rf` with no look at who read the cache, and
the Go module cache holds the toolchain every build on sami-devbox runs from. Now a cache over its
cap goes only when no process holds anything under it (devbox/proc-holds.sh: open file, cwd, exe,
root, mapped file, command-line argument) and none of its tool's reading commands runs, and it goes
by a rename to a sibling, so a reader sees the whole cache or none of it.

Technique: a real reader process holds one file of the cache open, the cleaner runs with that
cache's cap at 0, and then the reader opens a second file of the cache by its path, as a build
reading its next source does; it exits 0 only if that file is still there. The same cache with no
reader is removed, so a kept cache is the reader's doing and not a cleaner that removes nothing.
A scratch /proc (NVME_CACHE_PROC) plays the commands that read without holding, a scan that does
not finish, and a /proc too big for one argument list. uv is a stub that records its calls.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DEVBOX = Path(__file__).resolve().parents[2] / "devbox"

READER = r"""
import os, sys
held, later = sys.argv[1:3]
f = open(held, "rb")
print("ready", flush=True)
sys.stdin.readline()
try:
    with open(later, "rb") as g:
        g.read()
except OSError as e:
    print(f"lost: {e}", flush=True)
    sys.exit(1)
print("read", flush=True)
"""

UV_STUB = r"""#!/bin/bash
echo "$*" >>"$STUB_UV_CALLS"
"""

# name, the variable naming its directory, the directory under the scratch tree, its cap variable,
# and the subdirectory holding its files (cargo's caches are subdirectories of CARGO_HOME).
CACHES = [
    ("uv", "UV_CACHE_DIR", "scratch/cache/uv", "UV_CACHE_CAP_BYTES", ""),
    ("cargo", "CARGO_HOME", "nvme/cargo", "CARGO_CACHE_CAP_BYTES", "registry/src"),
    ("gomodcache", "GOMODCACHE", "nvme/gomodcache", "GOMODCACHE_CAP_BYTES", ""),
    ("huggingface", "HF_HOME", "nvme/cache/huggingface", "HF_CACHE_CAP_BYTES", ""),
    ("ms-playwright", "PLAYWRIGHT_BROWSERS_PATH", "nvme/cache/ms-playwright", "PLAYWRIGHT_CACHE_CAP_BYTES", ""),
    ("mise", "MISE_CACHE_DIR", "nvme/cache/mise", "MISE_CACHE_CAP_BYTES", ""),
    ("puppeteer", "PUPPETEER_CACHE_DIR", "nvme/cache/puppeteer", "PUPPETEER_CACHE_CAP_BYTES", ""),
    ("pip", "PIP_CACHE_DIR", "scratch/cache/pip", "PIP_CACHE_CAP_BYTES", ""),
    ("bun", "BUN_INSTALL_CACHE_DIR", "nvme/cache/bun", "BUN_CACHE_CAP_BYTES", ""),
]


class NvmeCacheCapReaders(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.unlock)
        stubs = self.root / "bin"
        stubs.mkdir()
        (stubs / "uv").write_text(UV_STUB, encoding="utf-8")
        (stubs / "uv").chmod(0o755)
        self.uv_calls = self.root / "uv-calls"
        (self.root / "nvme").mkdir()
        (self.root / "scratch").mkdir()
        self.env = {"HOME": str(self.root), "PATH": f"{stubs}:{os.environ['PATH']}", "TMPDIR": str(self.root),
                    "STUB_UV_CALLS": str(self.uv_calls), "NVME_CACHE_ROOT": str(self.root / "nvme"),
                    "SCRATCH_CACHE_ROOT": str(self.root / "scratch")}
        for _, var, where, cap, _ in CACHES:
            self.env[var] = str(self.root / where)
            self.env[cap] = "1000000000000"
        self.readers: list[subprocess.Popen] = []

    def unlock(self) -> None:
        for reader in self.readers:
            reader.kill()
            reader.wait()
        for here, dirs, files in os.walk(self.root):
            for name in dirs + files:
                path = os.path.join(here, name)
                if not os.path.islink(path):
                    os.chmod(path, 0o700)
        self.tmp.cleanup()

    def fill(self, where: str, sub: str, read_only: bool = False) -> Path:
        files = self.root / where / sub / "pkg"
        files.mkdir(parents=True)
        for name in ("held", "later"):
            (files / name).write_bytes(b"x" * 4096)
        if read_only:  # as the Go module cache keeps its files
            for path in (files / "held", files / "later", files):
                path.chmod(0o555 if path.is_dir() else 0o444)
        return files

    def read_while(self, files: Path) -> subprocess.Popen:
        reader = subprocess.Popen([sys.executable, "-c", READER, str(files / "held"), str(files / "later")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.readers.append(reader)
        self.assertEqual(reader.stdout.readline().strip(), "ready")
        return reader

    def finish(self, reader: subprocess.Popen) -> str:
        out, _ = reader.communicate("go on\n", timeout=30)
        return f"exit {reader.returncode}: {out.strip()}"

    def run_cap(self, **settings: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(DEVBOX / "nvme-cache-cap")], capture_output=True, text=True,
                              env=dict(self.env, **settings), check=False, timeout=600)

    def test_a_cache_a_live_reader_holds_is_kept_and_stays_readable(self) -> None:
        for name, _, where, cap, sub in CACHES:
            with self.subTest(cache=name):
                files = self.fill(where, sub, read_only=name == "gomodcache")
                reader = self.read_while(files)
                r = self.run_cap(**{cap: "0"})
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn(f"pid {reader.pid} holds {files}/held (an open file)", r.stdout)
                self.assertEqual(self.finish(reader), "exit 0: read", r.stdout)
                self.assertEqual(list((self.root / where / sub).parent.glob("*.removing.*")), [])
                self.assertFalse(self.uv_calls.exists(), "uv pruned a cache a reader held")

    def test_the_same_cache_with_no_reader_is_renamed_away_and_removed(self) -> None:
        for name, _, where, cap, sub in CACHES:
            with self.subTest(cache=name):
                files = self.fill(where, sub, read_only=name == "gomodcache")
                r = self.run_cap(**{cap: "0"}, NVME_CACHE_PROC=str(self.root / "no-proc"))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertFalse(files.exists(), r.stdout)
                self.assertIn(f"nothing held {self.root / where / sub}; renamed it to", r.stdout)
                self.assertEqual(list((self.root / where / sub).parent.glob("*.removing.*")), [], r.stdout)
                self.assertNotIn("reclaimed-bytes: 0\n", r.stdout)
                if (self.root / where).exists():
                    shutil.rmtree(self.root / where)

    def test_a_reading_command_that_holds_no_file_keeps_its_cache(self) -> None:
        proc = self.root / "proc"
        for name, where, cap, sub, command in (("gomodcache", "nvme/gomodcache", "GOMODCACHE_CAP_BYTES", "", "go"),
                                               ("cargo", "nvme/cargo", "CARGO_CACHE_CAP_BYTES", "registry/src", "rustc"),
                                               ("pip", "scratch/cache/pip", "PIP_CACHE_CAP_BYTES", "", "pip")):
            with self.subTest(cache=name):
                files = self.fill(where, sub)
                (proc / "4242").mkdir(parents=True, exist_ok=True)
                (proc / "4242" / "comm").write_text(command + "\n", encoding="utf-8")
                r = self.run_cap(**{cap: "0"}, NVME_CACHE_PROC=str(proc))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertTrue((files / "later").exists(), r.stdout)
                self.assertIn(f"pid 4242 ({command}) is running", r.stdout)
                shutil.rmtree(self.root / where)

    def test_a_scan_that_cannot_finish_keeps_the_cache(self) -> None:
        files = self.fill("nvme/cache/bun", "")
        r = self.run_cap(BUN_CACHE_CAP_BYTES="0", NVME_CACHE_SCAN_SECONDS="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((files / "later").exists(), r.stdout)
        self.assertIn("did not finish (deadline)", r.stdout)

    def test_a_proc_too_big_for_one_argument_list_still_finds_the_holder(self) -> None:
        # On sami-devbox on 2026-11-16, 22,765 processes made the one find over every
        # /proc/<pid>/{fd,cwd,exe,root} fail with "Argument list too long", and the scan read as
        # finished with nothing held. A long scratch path makes 8,000 entries enough here.
        files = self.fill("nvme/cache/bun", "")
        proc = self.root / ("p" * 120) / "proc"
        pids = range(10000, 18000)
        for pid in pids:
            (proc / str(pid) / "fd").mkdir(parents=True)
            for link in ("cwd", "exe", "root"):
                (proc / str(pid) / link).symlink_to("/")
        (proc / "17999" / "fd" / "3").symlink_to(files / "held")
        one_list = sum(len(f"{proc}/{pid}/{kind}") + 1 + 8 for pid in pids for kind in ("fd", "cwd", "exe", "root"))
        self.assertGreater(one_list, os.sysconf("SC_ARG_MAX"))
        r = self.run_cap(BUN_CACHE_CAP_BYTES="0", NVME_CACHE_PROC=str(proc))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((files / "later").exists(), r.stdout)
        self.assertIn(f"pid 17999 holds {files}/held (an open file)", r.stdout)

    def test_a_sibling_an_earlier_run_left_is_removed_once_nothing_holds_it(self) -> None:
        cache = self.root / "nvme" / "cache" / "mise"
        cache.mkdir(parents=True)
        left = self.root / "nvme" / "cache" / "mise.removing.1.1"
        (left / "pkg").mkdir(parents=True)
        (left / "pkg" / "file").write_bytes(b"y" * 1000)
        r = self.run_cap(NVME_CACHE_PROC=str(self.root / "no-proc"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(left.exists(), r.stdout)
        self.assertTrue(cache.exists(), r.stdout)
        self.assertIn(f"removed {left}", r.stdout)

    def test_a_cache_with_a_filesystem_mounted_inside_is_kept(self) -> None:
        """rm -rf would delete a filesystem mounted inside a cache over its cap. The cache is kept,
        not renamed, and the mounted file stays. Run in a user and mount namespace of its own,
        where this user may mount a tmpfs."""
        if shutil.which("unshare") is None or subprocess.run(
                ["unshare", "-rm", "true"], capture_output=True, check=False).returncode != 0:
            self.skipTest("needs `unshare -rm` to mount a tmpfs without root")
        files = self.fill("nvme/cache/bun", "")
        (files / "mnt").mkdir()
        mounted = files / "mnt" / "data"
        script = ('mount -t tmpfs none "$1" && echo precious > "$2" || exit 9\n'
                  '"$3"; rc=$?\nif [ -f "$2" ]; then echo "MOUNTED FILE SURVIVED"; fi\nexit $rc\n')
        r = subprocess.run(["unshare", "-rm", "sh", "-c", script, "sh", str(files / "mnt"), str(mounted),
                            str(DEVBOX / "nvme-cache-cap")], capture_output=True, text=True, check=False, timeout=600,
                           env=dict(self.env, BUN_CACHE_CAP_BYTES="0", NVME_CACHE_PROC=str(self.root / "no-proc")))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("MOUNTED FILE SURVIVED", r.stdout)
        self.assertIn(f"kept {self.root / 'nvme/cache/bun'}: a filesystem is mounted inside it, at {files}/mnt",
                      r.stdout)
        self.assertTrue((files / "later").exists(), r.stdout)


if __name__ == "__main__":
    unittest.main()
