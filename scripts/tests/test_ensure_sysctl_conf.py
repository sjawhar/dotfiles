#!/usr/bin/env python3
"""Tests for installers/lib.sh ensure_sysctl_conf as devbox/gce-host.sh calls it: the per-user pipe
buffer soft limit, devbox/sysctl-pipes.conf as /etc/sysctl.d/90-pipe-user-pages.conf.
installers/docker.sh's conntrack step calls the same helper; test_docker_installer.py covers that.

The call is gce-host.sh's own command as bash parses it: it must sit at the script's top level,
outside every if, loop and function, and it runs here after `source installers/lib.sh`, so a call
that is skipped or names another file, path or key fails here. sudo is test_docker_installer.py's
fake, which runs its command as the test user. The sysctl fake applies each `key = value` line, a
last line without a newline included, to an existing entry under INSTALL_ROOT/proc/sys. Every path
the helper writes or reads is under INSTALL_ROOT, /proc/sys included.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import final

from scripts.tests.test_docker_installer import FAKES, real_tool, write_command

DOTFILES = Path(__file__).resolve().parents[2]
GCE_HOST = DOTFILES / "devbox" / "gce-host.sh"
LIBRARY = DOTFILES / "installers" / "lib.sh"
PIPES_CONF = DOTFILES / "devbox" / "sysctl-pipes.conf"
SETTING = "fs.pipe-user-pages-soft = 262144\n"
# sami-devbox's /etc/sysctl.d/90-pipe-user-pages.conf, byte for byte (591 bytes, sha256 85261f2c...),
# as it was written by hand on 2026-11-22, before devbox/gce-host.sh wrote that file.
HAND_PIPES_CONF = (
    "# Per-user pipe buffer soft limit on sami-devbox (pages; 16 pages = one 64 KiB pipe).\n"
    "# Over the soft limit, every new pipe a user opens gets 8 KiB instead of 64 KiB, and a program that\n"
    "# writes more than 8 KiB into a pipe before its reader starts blocks for good: on 2026-11-22\n"
    "# actionlint hung on envoy.yaml (shellcheck scripts over 8 KiB) with sami holding ~1,584 pipes\n"
    "# across ~2,150 processes, over the default 16384. Raised 2026-11-22 01:57Z; the hard limit stays 0.\n"
    "# Rollback: delete this file and run sudo sysctl -w fs.pipe-user-pages-soft=16384.\n"
    "fs.pipe-user-pages-soft = 262144\n"
)
# The real tools lib.sh and the helper call. Only these and the two fakes are on PATH.
REAL_TOOLS = ("bash", "cat", "cmp", "install", "mktemp", "mv", "rm")
# `sysctl -q -p FILE` writes each setting of FILE, in order, to its entry under INSTALL_ROOT/proc/sys
# and, as procps sysctl does, fails with status 1 when that entry does not exist. SYSCTL_IGNORED: it
# exits 0 and writes nothing; SYSCTL_FAILS: it exits 1 and writes nothing; SYSCTL_SKIPS=<key>: it
# writes every setting but that key's and exits 0.
SYSCTL_FAKE = r'''echo "sysctl $*" >> "$CALL_LOG"
[[ $# == 3 && "$1" == -q && "$2" == -p ]] || { echo "fake sysctl: unexpected call: $*" >&2; exit 2; }
[[ "${SYSCTL_IGNORED:-}" != 1 ]] || exit 0
[[ "${SYSCTL_FAILS:-}" != 1 ]] || { echo "sysctl: fake failure" >&2; exit 1; }
while read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* || "$line" == \;* ]] && continue
    [[ "$line" =~ ^([^[:space:]=]+)[[:space:]]*=[[:space:]]*(.*)$ ]] || { echo "fake sysctl: bad line: $line" >&2; exit 2; }
    key="${BASH_REMATCH[1]}"
    [[ "$key" != "${SYSCTL_SKIPS:-}" ]] || continue
    entry="$INSTALL_ROOT/proc/sys/${key//.//}"
    [[ -f "$entry" ]] || { echo "sysctl: cannot stat ${entry#"$INSTALL_ROOT"}: No such file or directory" >&2; exit 1; }
    printf '%s\n' "${BASH_REMATCH[2]}" > "$entry"
done < "$3"'''


def gce_host_call() -> str:
    """gce-host.sh's one command that calls ensure_sysctl_conf, as bash parses it. bash prints the
    script's body as a function with four spaces before each top-level command and more before a
    command inside an if, a loop or a function; it parses the script and runs none of it."""
    parsed = subprocess.run(
        [real_tool("bash"), "-c", 'eval "gce_host() {\n$(<"$1")\n}" && declare -f gce_host', "_", str(GCE_HOST)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    calls = [line for line in parsed if line.lstrip().startswith("ensure_sysctl_conf ")]
    if len(calls) != 1 or not calls[0].startswith("    ensure_sysctl_conf "):
        raise AssertionError(f"{GCE_HOST} must call ensure_sysctl_conf once, at its top level: {calls}")
    return calls[0].strip().removesuffix(";")


@final
class Host:
    """A temporary directory with lib.sh, devbox/sysctl-pipes.conf, the fakes, the real tools and
    INSTALL_ROOT, the kernel's soft limit at its default."""

    def __init__(self) -> None:
        # First: a gce-host.sh that does not call the helper fails here, before any directory exists.
        self.call = gce_host_call()
        tools_found = {name: real_tool(name) for name in REAL_TOOLS}
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dotfiles = self.root / "dotfiles"
        (self.dotfiles / "installers").mkdir(parents=True)
        _ = shutil.copy2(LIBRARY, self.dotfiles / "installers" / "lib.sh")
        (self.dotfiles / "devbox").mkdir()
        self.source = self.dotfiles / "devbox" / PIPES_CONF.name
        _ = shutil.copy2(PIPES_CONF, self.source)

        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        write_command(self.bin_dir, "sudo", FAKES["sudo"])
        write_command(self.bin_dir, "sysctl", SYSCTL_FAKE)
        tools = self.root / "tools"
        tools.mkdir()
        for name, found in tools_found.items():
            (tools / name).symlink_to(found)

        self.log = self.root / "calls.log"
        self.install_root = self.root / "install-root"
        self.sysctl_dir = self.install_root / "etc/sysctl.d"
        self.conf = self.sysctl_dir / "90-pipe-user-pages.conf"
        self.proc_sys = self.install_root / "proc/sys"
        self.soft = self.proc_sys / "fs/pipe-user-pages-soft"
        self.soft.parent.mkdir(parents=True)
        _ = self.soft.write_text("16384\n", encoding="utf-8")
        self.env = {
            "HOME": str(self.root),
            "PATH": f"{self.bin_dir}:{tools}",
            "DOTFILES_DIR": str(self.dotfiles),
            "INSTALL_ROOT": str(self.install_root),
            "TMPDIR": str(self.root),
            "CALL_LOG": str(self.log),
        }

    def close(self) -> None:
        self.temp_dir.cleanup()

    def run(self, *before: str, call: str | None = None, **env: str) -> subprocess.CompletedProcess[str]:
        """Run gce-host.sh's call (or *call*) once, after gce-host.sh's own preamble for it (lib.sh,
        and DEVBOX_DIR as it sets it) and the lines *before*; the call log holds only this run's
        calls."""
        script = "\n".join(
            (
                "set -euo pipefail",
                'source "$DOTFILES_DIR/installers/lib.sh"',
                'DEVBOX_DIR="${DOTFILES_DIR}/devbox"',
                *before,
                call or self.call,
                "",
            )
        )
        self.log.unlink(missing_ok=True)
        return subprocess.run(
            ["bash", "-c", script],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def applies(self) -> list[str]:
        """Every sysctl call, through sudo or not."""
        return [call for call in self.calls() if call.startswith(("sudo sysctl", "sysctl"))]

    def sysctl_dir_names(self) -> set[str]:
        return {path.name for path in self.sysctl_dir.iterdir()}

    def kernel_entry(self, path: str, value: str) -> Path:
        """Another /proc/sys entry, holding *value*."""
        entry = self.proc_sys / path
        entry.parent.mkdir(parents=True, exist_ok=True)
        _ = entry.write_text(value, encoding="utf-8")
        return entry


class PipeUserPages(unittest.TestCase):
    def host(self) -> Host:
        host = Host()
        self.addCleanup(host.close)
        return host

    def assert_ok(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_failed(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_applied_once(self, h: Host) -> None:
        self.assertEqual(h.applies(), [f"sudo sysctl -q -p {h.conf}", f"sysctl -q -p {h.conf}"])

    def assert_installed(self, h: Host) -> None:
        """The repository's file, alone in sysctl.d with mode 0644, applied once."""
        self.assertEqual(h.conf.read_bytes(), PIPES_CONF.read_bytes())
        self.assertEqual(stat.S_IMODE(h.conf.stat().st_mode), 0o644)
        self.assertEqual(h.sysctl_dir_names(), {h.conf.name})
        self.assert_applied_once(h)
        self.assertEqual(h.soft.read_text(encoding="utf-8"), "262144\n")

    def test_the_repository_file_sets_the_limit(self) -> None:
        self.assertTrue(PIPES_CONF.read_text(encoding="utf-8").endswith(SETTING))

    def test_writes_the_file_and_applies_it(self) -> None:
        """A new machine: no file, the kernel at its default 16384."""
        h = self.host()
        self.assertFalse(h.conf.exists())

        result = h.run()

        self.assert_ok(result)
        self.assertIn(f"wrote {h.conf}", result.stdout)
        self.assert_installed(h)

    def test_identical_bytes_are_left_alone(self) -> None:
        """The repository's file in place and the kernel at its value: nothing is written or run."""
        h = self.host()
        self.assert_ok(h.run())
        status = h.conf.stat()

        result = h.run()

        self.assert_ok(result)
        self.assertIn("in place already", result.stdout)
        self.assertEqual(h.conf.read_bytes(), PIPES_CONF.read_bytes())
        self.assertEqual(h.conf.stat().st_ino, status.st_ino)
        self.assertEqual(h.conf.stat().st_mtime_ns, status.st_mtime_ns)
        self.assertEqual(h.sysctl_dir_names(), {h.conf.name})
        self.assertEqual(h.calls(), [])

    def test_identical_bytes_and_a_kernel_at_another_value_are_applied(self) -> None:
        """The file in place and the kernel back at 16384 (a hand sysctl -w): no write, one apply."""
        h = self.host()
        self.assert_ok(h.run())
        status = h.conf.stat()
        _ = h.soft.write_text("16384\n", encoding="utf-8")

        result = h.run()

        self.assert_ok(result)
        self.assertEqual(h.conf.stat().st_ino, status.st_ino)
        self.assertEqual(h.conf.stat().st_mtime_ns, status.st_mtime_ns)
        self.assert_installed(h)

    def test_differing_bytes_are_replaced_and_applied(self) -> None:
        cases = {
            # sami-devbox now: the hand-written file, which sets the same value, and the kernel at it.
            "the hand-written file": (HAND_PIPES_CONF, "262144\n"),
            # A file at the kernel's default, and the kernel at it.
            "a file at 16384": (
                PIPES_CONF.read_text(encoding="utf-8").replace(SETTING, "fs.pipe-user-pages-soft = 16384\n"),
                "16384\n",
            ),
        }
        self.assertIn(SETTING, HAND_PIPES_CONF)
        for name, (text, kernel) in cases.items():
            with self.subTest(name):
                self.assertNotEqual(text.encode(), PIPES_CONF.read_bytes())
                h = self.host()
                h.sysctl_dir.mkdir(parents=True)
                _ = h.conf.write_text(text, encoding="utf-8")
                old = h.conf.stat().st_ino
                _ = h.soft.write_text(kernel, encoding="utf-8")

                result = h.run()

                self.assert_ok(result)
                self.assertIn(f"wrote {h.conf}", result.stdout)
                self.assertNotEqual(h.conf.stat().st_ino, old)
                self.assert_installed(h)

    def test_a_kernel_that_keeps_its_value_fails(self) -> None:
        h = self.host()

        result = h.run(SYSCTL_IGNORED="1")

        self.assert_failed(result)
        self.assertIn("the kernel's fs.pipe-user-pages-soft is 16384, not 262144", result.stderr)

    def test_a_failed_apply_fails_although_the_kernel_holds_the_value(self) -> None:
        """The hand-written file and the kernel at 262144: the replaced file's sysctl -p fails."""
        h = self.host()
        h.sysctl_dir.mkdir(parents=True)
        _ = h.conf.write_text(HAND_PIPES_CONF, encoding="utf-8")
        _ = h.soft.write_text("262144\n", encoding="utf-8")

        result = h.run(SYSCTL_FAILS="1")

        self.assert_failed(result)
        self.assertIn("sysctl: fake failure", result.stderr)
        self.assertEqual(h.conf.read_bytes(), PIPES_CONF.read_bytes())

    def test_a_failed_apply_is_retried_without_rewriting(self) -> None:
        h = self.host()
        self.assert_failed(h.run(SYSCTL_IGNORED="1"))
        status = h.conf.stat()

        self.assert_ok(h.run())

        self.assertEqual(h.conf.stat().st_ino, status.st_ino)
        self.assertEqual(h.conf.stat().st_mtime_ns, status.st_mtime_ns)
        self.assert_installed(h)

    def test_a_kernel_without_the_key_fails(self) -> None:
        """No fs.pipe-user-pages-soft in /proc/sys: sysctl -p fails, and so does the step."""
        h = self.host()
        h.soft.unlink()

        result = h.run()

        self.assert_failed(result)
        self.assertIn("cannot stat /proc/sys/fs/pipe-user-pages-soft", result.stderr)
        self.assertFalse(h.soft.exists())
        self.assertEqual(h.conf.read_bytes(), PIPES_CONF.read_bytes())
        self.assert_applied_once(h)

    def test_every_key_is_checked_at_its_last_value(self) -> None:
        """Two keys, the first set twice: the kernel must hold each key's last value."""
        h = self.host()
        rmem = h.kernel_entry("net/ipv4/tcp_rmem", "4096\t87380\t6291456\n")
        text = (
            " \t# comment\n\n  ; another comment\n"
            "fs.pipe-user-pages-soft = 16384\n"
            "\tfs.pipe-user-pages-soft\t= 262144\t\n"
            "net.ipv4.tcp_rmem = 4096   87380\t6291456"
        )
        _ = h.source.write_text(text, encoding="utf-8")

        self.assert_ok(h.run())
        self.assertEqual(h.conf.read_text(encoding="utf-8"), text)
        self.assertEqual(h.soft.read_text(encoding="utf-8"), "262144\n")
        self.assert_applied_once(h)

        self.assert_ok(h.run())
        self.assertEqual(h.calls(), [])

        _ = rmem.write_text("4096\t87380\t4194304\n", encoding="utf-8")
        self.assert_ok(h.run())
        self.assertEqual(rmem.read_text(encoding="utf-8").split(), ["4096", "87380", "6291456"])
        self.assert_applied_once(h)

        _ = rmem.write_text("4096\t87380\t4194304\n", encoding="utf-8")
        result = h.run(SYSCTL_SKIPS="net.ipv4.tcp_rmem")
        self.assert_failed(result)
        self.assertIn("the kernel's net.ipv4.tcp_rmem is 4096 87380 4194304, not 4096 87380 6291456", result.stderr)

    def test_a_source_that_does_not_set_the_key_fails_before_writing(self) -> None:
        h = self.host()
        rmem = h.kernel_entry("net/ipv4/tcp_rmem", "4096\t87380\t6291456\n")
        _ = h.source.write_text("net.ipv4.tcp_rmem = 1 2 3\n", encoding="utf-8")

        result = h.run()

        self.assert_failed(result)
        self.assertIn("does not set fs.pipe-user-pages-soft", result.stderr)
        self.assertFalse(h.conf.exists())
        self.assertEqual(rmem.read_text(encoding="utf-8"), "4096\t87380\t6291456\n")
        self.assertEqual(h.calls(), [])

    def test_malformed_settings_fail_before_writing(self) -> None:
        for setting in ("", "fs.pipe-user-pages-soft =\n", " = 262144\n", "fs.pipe user = 262144\n", "not an assignment\n"):
            with self.subTest(setting=setting):
                h = self.host()
                _ = h.source.write_text(SETTING + setting if setting else "# only a comment\n", encoding="utf-8")

                result = h.run()

                self.assert_failed(result)
                self.assertIn("ensure_sysctl_conf: ", result.stderr)
                self.assertFalse(h.conf.exists())
                self.assertEqual(h.soft.read_text(encoding="utf-8"), "16384\n")
                self.assertEqual(h.calls(), [])

    def test_a_directory_at_the_path_does_not_take_the_file(self) -> None:
        h = self.host()
        h.conf.mkdir(parents=True)

        result = h.run()

        self.assert_failed(result)
        self.assertTrue(h.conf.is_dir())
        self.assertEqual(list(h.conf.iterdir()), [])
        self.assertEqual(h.sysctl_dir_names(), {h.conf.name})
        self.assertEqual(h.soft.read_text(encoding="utf-8"), "16384\n")
        self.assertEqual(h.applies(), [])

    def test_a_failed_copy_or_rename_keeps_the_old_file(self) -> None:
        for tool in ("install", "mv"):
            with self.subTest(tool=tool):
                h = self.host()
                h.sysctl_dir.mkdir(parents=True)
                _ = h.conf.write_text("old file\n", encoding="utf-8")
                write_command(h.bin_dir, tool, "exit 1")

                result = h.run()

                self.assert_failed(result)
                self.assertEqual(h.conf.read_text(encoding="utf-8"), "old file\n")
                self.assertEqual(h.sysctl_dir_names(), {h.conf.name})
                self.assertEqual(h.soft.read_text(encoding="utf-8"), "16384\n")
                self.assertEqual(h.applies(), [])

    def test_a_call_without_its_three_arguments_is_refused(self) -> None:
        calls = {
            "one": 'ensure_sysctl_conf "$DEVBOX_DIR/sysctl-pipes.conf"',
            "two": 'ensure_sysctl_conf "$DEVBOX_DIR/sysctl-pipes.conf" /etc/sysctl.d/90-pipe-user-pages.conf',
            "an empty key": 'ensure_sysctl_conf "$DEVBOX_DIR/sysctl-pipes.conf" /etc/sysctl.d/90-pipe-user-pages.conf ""',
        }
        for name, call in calls.items():
            with self.subTest(name):
                h = self.host()

                result = h.run(call=call)

                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("usage: ensure_sysctl_conf", result.stderr)
                self.assertFalse(h.sysctl_dir.exists())
                self.assertEqual(h.calls(), [])

    def test_an_unreadable_source_is_named(self) -> None:
        h = self.host()
        h.source.unlink()

        result = h.run()

        self.assert_failed(result)
        self.assertEqual(result.stderr, f"ensure_sysctl_conf: cannot read {h.source}\n")
        self.assertFalse(h.sysctl_dir.exists())
        self.assertEqual(h.calls(), [])

    def test_the_caller_s_ifs_does_not_change_the_parse(self) -> None:
        """A caller with IFS=$'\\n': the repository's `key = value` line still reads as one."""
        h = self.host()

        result = h.run("IFS=$'\\n'")

        self.assert_ok(result)
        self.assert_installed(h)


if __name__ == "__main__":
    _ = unittest.main()
