#!/usr/bin/env python3
"""Tests for installers/docker.sh: dockerd's live-restore, docker-ce pinned at one version, and
conntrack's TCP TIME_WAIT timeout.

The installer runs with a PATH of fakes and real tools only. Each fake (sudo, curl, apt-get,
dpkg, dpkg-query, dockerd, systemctl, sysctl, getent, id, usermod) writes one line per call to a
log; the fake sudo runs its command as the test user. jq, mktemp, install, tee, mv, cp and cmp are
real. Every path the installer writes or reads is under INSTALL_ROOT, a temporary directory,
/proc/sys included.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from typing import cast, final


DOTFILES = Path(__file__).resolve().parents[2]
INSTALLER = DOTFILES / "installers" / "docker.sh"
LIBRARY = DOTFILES / "installers" / "lib.sh"
CONNTRACK_CONF = DOTFILES / "devbox" / "sysctl-conntrack.conf"
# sami-devbox's /etc/sysctl.d/90-conntrack-time-wait.conf, byte for byte, as it was written by hand
# on 2026-11-21, before installers/docker.sh wrote that file.
HAND_CONNTRACK_CONF = (
    "# Docker start/exit hold one host-wide bridge lock while dockerd dumps the conntrack table; 92% of\n"
    "# its entries were TCP TIME_WAIT flows kept 120 s. 15 s halves that cost (see sjawhar/dotfiles devbox).\n"
    "net.netfilter.nf_conntrack_tcp_timeout_time_wait = 15\n"
)
VERSION = "5:29.9.3-1~ubuntu.24.04~noble"
OLD_VERSION = "5:29.9.2-1~ubuntu.24.04~noble"
NEWER_VERSION = "5:29.10.0-1~ubuntu.24.04~noble"
PIN_TEXT = (
    "# Pinned by installers/docker.sh (DOCKER_CE_VERSION, set by devbox/gce-host.sh); "
    "to upgrade, change docker_ce_version in devbox/gce-host.sh and run it again.\n"
    "Package: docker-ce docker-ce-cli\n"
    f"Pin: version {VERSION}\n"
    "Pin-Priority: 1001\n"
)
SOURCES_TEXT = (
    "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] "
    "https://download.docker.com/linux/ubuntu noble stable\n"
)
KEY_TEXT = (
    "-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
    "fake docker key\n"
    "-----END PGP PUBLIC KEY BLOCK-----\n"
)
INSTALL_PINNED = f"apt-get install -y docker-ce={VERSION} docker-ce-cli={VERSION}"
DOCKER_INFO = "docker info -f {{.LiveRestoreEnabled}}"
# sami-devbox's /etc/docker/daemon.json, byte for byte, as sysbox-ce's install script wrote it.
SYSBOX_DAEMON_JSON = """{
    "runtimes": {
        "sysbox-runc": {
            "path": "/usr/bin/sysbox-runc"
        }
    },
    "bip": "172.20.0.1/16",
    "default-address-pools": [
        {
            "base": "172.25.0.0/16",
            "size": 24
        }
    ]
}
"""
# The real tools the installer, lib.sh and the fakes call. Only these and the fakes are on PATH,
# so the machine's own docker, dockerd and systemctl are never reached.
REAL_TOOLS = (
    "bash", "sh", "env", "jq", "mktemp", "install", "tee", "chmod", "mv", "rm", "cp", "ln", "cat",
    "cmp", "date", "dirname", "grep", "readlink", "sleep", "timeout",
)

FAKES = {
    "sudo": r'''echo "sudo $*" >> "$CALL_LOG"
exec "$@"''',
    "curl": r'''echo "curl $*" >> "$CALL_LOG"
[[ "${CURL_FAILS:-}" != 1 ]] || exit 22
case "${!#}" in
    https://get.docker.com)
        # The official installer's stand-in: it "installs" a docker command.
        cat <<'EOF'
echo "get.docker.com script ran" >> "$CALL_LOG"
printf '#!/bin/bash\necho "docker $*" >> "$CALL_LOG"\n' > "$FAKE_BIN/docker"
chmod +x "$FAKE_BIN/docker"
EOF
        ;;
    https://download.docker.com/linux/ubuntu/gpg) printf '%s' "$KEY_TEXT" ;;
    *) exit 22 ;;
esac''',
    "dpkg": r'''echo "dpkg $*" >> "$CALL_LOG"
[[ "$*" == --print-architecture ]] || exit 2
echo amd64''',
    # Answers only the two queries the installer makes, from one file per installed package.
    "dpkg-query": r'''echo "dpkg-query $*" >> "$CALL_LOG"
format=''
packages=()
for argument in "$@"; do
    case "$argument" in
        -W) ;;
        -f=*) format="${argument#-f=}" ;;
        *) packages+=("$argument") ;;
    esac
done
for package in "${packages[@]}"; do
    if [[ ! -f "$PKG_STATE/$package" ]]; then
        echo "dpkg-query: no packages found matching $package" >&2
        exit 1
    fi
    version="$(<"$PKG_STATE/$package")"
    case "$format" in
        '${db:Status-Status} ${Version}') printf 'installed %s' "$version" ;;
        '${db:Status-Status}') printf 'installed' ;;
        *) echo "fake dpkg-query: unexpected format $format" >&2; exit 2 ;;
    esac
done''',
    # Records what it saw when it installs: the daemon.json of that moment, and its frontend.
    "apt-get": r'''echo "apt-get $*" >> "$CALL_LOG"
[[ "${1:-}" == update && $# == 2 && "$2" == -qq ]] && exit 0
[[ "${1:-}" == install ]] || { echo "fake apt-get: unexpected call: $*" >&2; exit 100; }
daemon_json="$INSTALL_ROOT/etc/docker/daemon.json"
if [[ -f "$daemon_json" ]]; then
    echo "apt-get-saw live-restore $(jq -c '.["live-restore"]' "$daemon_json")" >> "$CALL_LOG"
else
    echo "apt-get-saw live-restore absent" >> "$CALL_LOG"
fi
echo "apt-get-saw running live-restore $(cat "$RUNNING_LIVE_RESTORE" 2>/dev/null || echo false)" >> "$CALL_LOG"
echo "apt-get-saw DEBIAN_FRONTEND=${DEBIAN_FRONTEND:-}" >> "$CALL_LOG"
allow_downgrades=0
for argument in "$@"; do
    [[ "$argument" == --allow-downgrades ]] && allow_downgrades=1
done
if [[ "${APT_REFUSES_DOWNGRADE:-}" == 1 ]] && (( ! allow_downgrades )); then
    echo "E: Packages were downgraded and -y was used without --allow-downgrades." >&2
    exit 100
fi
for argument in "${@:2}"; do
    case "$argument" in
        -*) ;;
        *=*) printf '%s\n' "${APT_INSTALLS_VERSION:-${argument#*=}}" > "$PKG_STATE/${argument%%=*}" ;;
        *) echo latest > "$PKG_STATE/$argument" ;;
    esac
done''',
    # Keeps a copy of the file it was shown; refuses on DOCKERD_VALIDATE_FAILS, or on bad JSON.
    "dockerd": r'''echo "dockerd $*" >> "$CALL_LOG"
[[ $# == 3 && "$1" == --validate && "$2" == --config-file ]] || exit 2
cp "$3" "$VALIDATED"
if [[ "${DOCKERD_VALIDATE_FAILS:-}" == 1 ]] || ! jq -e 'type == "object"' "$3" >/dev/null; then
    echo "unable to configure the Docker daemon with file $3: fake refusal" >&2
    exit 1
fi
echo "configuration OK"''',
    # A reload applies daemon.json's live-restore to the running dockerd, unless RELOAD_IGNORED
    # (dockerd took the SIGHUP and did not apply the file) or RELOAD_FAILS.
    "systemctl": r'''echo "systemctl $*" >> "$CALL_LOG"
case "$*" in
    "is-active --quiet docker.service") [[ "${DOCKER_ACTIVE:-}" == 1 ]] ;;
    "reload docker.service")
        [[ "${RELOAD_FAILS:-}" != 1 ]] || exit 1
        if [[ "${RELOAD_IGNORED:-}" != 1 ]]; then
            jq -r '.["live-restore"] // false' "$INSTALL_ROOT/etc/docker/daemon.json" > "$RUNNING_LIVE_RESTORE"
        fi
        ;;
    *) echo "fake systemctl: unexpected call: $*" >&2; exit 2 ;;
esac''',
    # `sysctl -q -p FILE` writes each `key = value` line of FILE to INSTALL_ROOT/proc/sys, and, as
    # procps sysctl does, fails with status 1 when that entry does not exist (procfs makes no new
    # one); SYSCTL_IGNORED: it exits 0 and writes nothing.
    "sysctl": r'''echo "sysctl $*" >> "$CALL_LOG"
[[ $# == 3 && "$1" == -q && "$2" == -p ]] || { echo "fake sysctl: unexpected call: $*" >&2; exit 2; }
[[ "${SYSCTL_IGNORED:-}" != 1 ]] || exit 0
while read -r key equals value; do
    if [[ -z "$key" || "$key" == \#* ]]; then
        continue
    fi
    [[ "$equals" == "=" ]] || { echo "fake sysctl: unexpected line in $3: $key $equals $value" >&2; exit 2; }
    entry="$INSTALL_ROOT/proc/sys/${key//.//}"
    [[ -f "$entry" ]] || { echo "sysctl: cannot stat ${entry#"$INSTALL_ROOT"}: No such file or directory" >&2; exit 1; }
    printf '%s\n' "$value" > "$entry"
done < "$3"''',
    # The running dockerd's live-restore, as the last reload left it.
    "docker": r'''echo "docker $*" >> "$CALL_LOG"
[[ "$*" == "info -f {{.LiveRestoreEnabled}}" ]] || exit 2
# exec, so that timeout's signal reaches the process that holds the output pipe.
[[ "${DOCKER_INFO_HANGS:-}" != 1 ]] || exec sleep 3600
if [[ -f "$RUNNING_LIVE_RESTORE" ]]; then cat "$RUNNING_LIVE_RESTORE"; else echo false; fi''',
    "getent": r'''echo "getent $*" >> "$CALL_LOG"
[[ "$*" == "group docker" ]] || exit 2
echo "docker:x:999:"''',
    "id": r'''echo "id $*" >> "$CALL_LOG"
echo "${USER_GROUPS:-tester docker}"''',
    "usermod": r'''echo "usermod $*" >> "$CALL_LOG"''',
}


def write_command(directory: Path, name: str, body: str) -> None:
    """Write an executable bash fake into *directory*."""
    path = directory / name
    _ = path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def real_tool(name: str) -> str:
    """The system's own binary: Ubuntu's jq, not a mise shim that may be another program."""
    found = shutil.which(name, path="/usr/bin:/bin") or shutil.which(name)
    if found is None:
        raise unittest.SkipTest(f"requires {name}")
    return found


def json_object(path: Path) -> dict[str, object]:
    value = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(value, dict):
        raise AssertionError(f"{path} does not hold a JSON object")
    return cast(dict[str, object], value)


@final
class Machine:
    """A temporary directory with the installer, the fakes, the real tools and INSTALL_ROOT."""

    def __init__(self) -> None:
        tools_found = {name: real_tool(name) for name in REAL_TOOLS}
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        installers = self.root / "dotfiles" / "installers"
        installers.mkdir(parents=True)
        self.installer = installers / "docker.sh"
        _ = shutil.copy2(INSTALLER, self.installer)
        _ = shutil.copy2(LIBRARY, installers / "lib.sh")
        devbox = self.root / "dotfiles" / "devbox"
        devbox.mkdir()
        self.conntrack_source = devbox / CONNTRACK_CONF.name
        _ = shutil.copy2(CONNTRACK_CONF, self.conntrack_source)

        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        for name, body in FAKES.items():
            write_command(self.bin_dir, name, body)
        tools = self.root / "tools"
        tools.mkdir()
        for name, found in tools_found.items():
            (tools / name).symlink_to(found)

        self.log = self.root / "calls.log"
        self.pkg_state = self.root / "packages"
        self.pkg_state.mkdir()
        self.validated = self.root / "validated.json"
        self.running_live_restore = self.root / "running-live-restore"
        self.install_root = self.root / "install-root"
        self.os_release = self.install_root / "etc/os-release"
        self.os_release.parent.mkdir(parents=True)
        _ = self.os_release.write_text(
            "ID=ubuntu\nVERSION_CODENAME=noble\nUBUNTU_CODENAME=noble\n", encoding="utf-8"
        )
        self.docker_dir = self.install_root / "etc/docker"
        self.daemon_json = self.docker_dir / "daemon.json"
        self.apt_dir = self.install_root / "etc/apt"
        self.preferences = self.apt_dir / "preferences.d/docker-ce.pref"
        self.sources = self.apt_dir / "sources.list.d/docker.list"
        self.keyring = self.apt_dir / "keyrings/docker.asc"
        self.sysctl_dir = self.install_root / "etc/sysctl.d"
        self.conntrack_conf = self.sysctl_dir / "90-conntrack-time-wait.conf"
        # The kernel's value, at its default until a test or the fake sysctl changes it.
        self.time_wait = self.install_root / "proc/sys/net/netfilter/nf_conntrack_tcp_timeout_time_wait"
        self.time_wait.parent.mkdir(parents=True)
        _ = self.time_wait.write_text("120\n", encoding="utf-8")
        self.env = {
            "HOME": str(self.root),
            "USER": "tester",
            "PATH": f"{self.bin_dir}:{tools}",
            "INSTALL_ROOT": str(self.install_root),
            "TMPDIR": str(self.root),
            "CALL_LOG": str(self.log),
            "FAKE_BIN": str(self.bin_dir),
            "PKG_STATE": str(self.pkg_state),
            "VALIDATED": str(self.validated),
            "RUNNING_LIVE_RESTORE": str(self.running_live_restore),
            "KEY_TEXT": KEY_TEXT,
        }

    def close(self) -> None:
        self.temp_dir.cleanup()

    def install(self, **env: str) -> subprocess.CompletedProcess[str]:
        """Run the installer once; the call log holds only this run's calls."""
        self.log.unlink(missing_ok=True)
        return subprocess.run(
            ["bash", str(self.installer)],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def calls_of(self, prefix: str) -> list[str]:
        return [call for call in self.calls() if call.startswith(prefix)]

    def write_daemon_json(self, text: str) -> None:
        self.docker_dir.mkdir(parents=True, exist_ok=True)
        _ = self.daemon_json.write_text(text, encoding="utf-8")

    def set_installed(self, version: str, *packages: str) -> None:
        for package in packages or ("docker-ce", "docker-ce-cli"):
            _ = (self.pkg_state / package).write_text(version + "\n", encoding="utf-8")

    def installed(self, package: str) -> str | None:
        path = self.pkg_state / package
        return path.read_text(encoding="utf-8").strip() if path.exists() else None

    def backups(self) -> list[Path]:
        return sorted(self.docker_dir.glob("daemon.json.bak-*"))

    def docker_dir_names(self) -> set[str]:
        return {path.name for path in self.docker_dir.iterdir()}

    def sysctl_dir_names(self) -> set[str]:
        return {path.name for path in self.sysctl_dir.iterdir()}


class InstallerCase(unittest.TestCase):
    def machine(self) -> Machine:
        machine = Machine()
        self.addCleanup(machine.close)
        return machine

    def assert_ok(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_failed(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_mode(self, path: Path, mode: int) -> None:
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode, path)


class LiveRestore(InstallerCase):
    def test_sysbox_daemon_json_keeps_every_key_and_gets_live_restore(self) -> None:
        """(a) sami-devbox's file: every key stays as it was, validated, backed up, reloaded once."""
        m = self.machine()
        m.write_daemon_json(SYSBOX_DAEMON_JSON)

        result = m.install(DOCKER_LIVE_RESTORE="1", DOCKER_ACTIVE="1")

        self.assert_ok(result)
        before = cast(dict[str, object], json.loads(SYSBOX_DAEMON_JSON))
        after = json_object(m.daemon_json)
        self.assertEqual(list(after), [*before, "live-restore"])
        for key, value in before.items():
            self.assertEqual(json.dumps(after[key]), json.dumps(value), key)
        self.assertIs(after["live-restore"], True)
        self.assert_mode(m.daemon_json, 0o644)

        backups = m.backups()
        self.assertEqual(len(backups), 1, m.docker_dir_names())
        self.assertEqual(backups[0].read_text(encoding="utf-8"), SYSBOX_DAEMON_JSON)
        self.assertEqual(m.docker_dir_names(), {"daemon.json", backups[0].name})

        validations = m.calls_of("dockerd ")
        self.assertEqual(len(validations), 1, m.calls())
        staged = Path(validations[0].split()[-1])
        self.assertEqual(staged.parent, m.docker_dir)
        self.assertNotEqual(staged, m.daemon_json)
        self.assertEqual(m.validated.read_bytes(), m.daemon_json.read_bytes())
        self.assertEqual(m.calls_of("systemctl reload"), ["systemctl reload docker.service"])
        # The running dockerd is asked after the reload, and it reports live-restore on.
        calls = m.calls()
        self.assertIn(DOCKER_INFO, calls[calls.index("systemctl reload docker.service") :])
        self.assertEqual(m.running_live_restore.read_text(encoding="utf-8").strip(), "true")

    def test_second_run_writes_nothing_and_reloads_nothing(self) -> None:
        """(b) The result of (a) run again: no write, no backup, no validation, no reload."""
        m = self.machine()
        m.write_daemon_json(SYSBOX_DAEMON_JSON)
        self.assert_ok(m.install(DOCKER_LIVE_RESTORE="1", DOCKER_ACTIVE="1"))
        contents = m.daemon_json.read_bytes()
        status = m.daemon_json.stat()
        names = m.docker_dir_names()

        result = m.install(DOCKER_LIVE_RESTORE="1", DOCKER_ACTIVE="1")

        self.assert_ok(result)
        self.assertIn("live-restore is on", result.stdout)
        self.assertEqual(m.daemon_json.read_bytes(), contents)
        self.assertEqual(m.daemon_json.stat().st_ino, status.st_ino)
        self.assertEqual(m.daemon_json.stat().st_mtime_ns, status.st_mtime_ns)
        self.assertEqual(m.docker_dir_names(), names)
        self.assertEqual(len(m.backups()), 1)
        self.assertEqual(m.calls_of("sudo "), [f"sudo timeout 10 {DOCKER_INFO}"])
        self.assertEqual(m.calls_of("dockerd "), [])
        self.assertEqual(m.calls_of("systemctl reload"), [])

    def test_refused_validation_changes_nothing(self) -> None:
        """(c) dockerd --validate refuses: the file stays, the install fails, nothing reloads."""
        m = self.machine()
        m.write_daemon_json(SYSBOX_DAEMON_JSON)
        m.set_installed(OLD_VERSION)

        result = m.install(
            DOCKER_LIVE_RESTORE="1",
            DOCKER_ACTIVE="1",
            DOCKERD_VALIDATE_FAILS="1",
            DOCKER_CE_VERSION=VERSION,
        )

        self.assert_failed(result)
        self.assertIn("changed nothing", result.stderr)
        self.assertEqual(m.daemon_json.read_text(encoding="utf-8"), SYSBOX_DAEMON_JSON)
        self.assertEqual(m.docker_dir_names(), {"daemon.json"})
        self.assertEqual(len(m.calls_of("dockerd ")), 1, m.calls())
        self.assertEqual(m.calls_of("systemctl "), [])
        self.assertEqual(m.calls_of("apt-get "), [])
        self.assertEqual(m.installed("docker-ce"), OLD_VERSION)

    def test_no_daemon_json_writes_only_live_restore(self) -> None:
        m = self.machine()

        result = m.install(DOCKER_LIVE_RESTORE="1")

        self.assert_ok(result)
        self.assertEqual(json_object(m.daemon_json), {"live-restore": True})
        self.assert_mode(m.daemon_json, 0o644)
        self.assert_mode(m.docker_dir, 0o755)
        self.assertEqual(m.docker_dir_names(), {"daemon.json"})
        self.assertEqual(len(m.calls_of("dockerd ")), 1, m.calls())
        # dockerd is not running: its next start reads the file, and nothing reloads.
        self.assertEqual(m.calls_of("systemctl "), ["systemctl is-active --quiet docker.service"])

    def test_live_restore_false_becomes_true_with_a_backup(self) -> None:
        m = self.machine()
        original = '{\n    "bip": "172.20.0.1/16",\n    "live-restore": false\n}\n'
        m.write_daemon_json(original)

        result = m.install(DOCKER_LIVE_RESTORE="1", DOCKER_ACTIVE="1")

        self.assert_ok(result)
        self.assertEqual(json_object(m.daemon_json), {"bip": "172.20.0.1/16", "live-restore": True})
        self.assertEqual([path.read_text(encoding="utf-8") for path in m.backups()], [original])
        self.assertEqual(m.calls_of("systemctl reload"), ["systemctl reload docker.service"])

    def test_unreadable_json_changes_nothing(self) -> None:
        m = self.machine()
        original = '{"bip": "172.20.0.1/16",\n'
        m.write_daemon_json(original)

        result = m.install(DOCKER_LIVE_RESTORE="1", DOCKER_ACTIVE="1")

        self.assert_failed(result)
        self.assertEqual(m.daemon_json.read_text(encoding="utf-8"), original)
        self.assertEqual(m.docker_dir_names(), {"daemon.json"})
        self.assertEqual(m.calls_of("sudo "), [])
        self.assertEqual(m.calls_of("systemctl "), [])

    def test_without_dockerd_the_file_is_written_unvalidated(self) -> None:
        m = self.machine()
        (m.bin_dir / "dockerd").unlink()
        m.write_daemon_json(SYSBOX_DAEMON_JSON)

        result = m.install(DOCKER_LIVE_RESTORE="1")

        self.assert_ok(result)
        self.assertIs(json_object(m.daemon_json)["live-restore"], True)
        self.assertEqual(len(m.backups()), 1)

    def test_a_failed_reload_is_done_by_the_next_run_before_the_upgrade(self) -> None:
        """The file has live-restore on and dockerd does not: the next run reloads first."""
        m = self.machine()
        m.write_daemon_json(SYSBOX_DAEMON_JSON)
        m.set_installed(OLD_VERSION)
        env = {"DOCKER_LIVE_RESTORE": "1", "DOCKER_ACTIVE": "1", "DOCKER_CE_VERSION": VERSION}

        first = m.install(**env, RELOAD_FAILS="1")

        self.assert_failed(first)
        self.assertIn("systemctl reload docker.service failed", first.stderr)
        self.assertEqual(m.calls_of("apt-get "), [])
        self.assertIs(json_object(m.daemon_json)["live-restore"], True)

        result = m.install(**env)

        self.assert_ok(result)
        calls = m.calls()
        self.assertEqual(m.calls_of("systemctl reload"), ["systemctl reload docker.service"])
        self.assertLess(calls.index("systemctl reload docker.service"), calls.index(INSTALL_PINNED))
        self.assertIn("apt-get-saw running live-restore true", calls)
        self.assertEqual(m.calls_of("dockerd "), [])
        self.assertEqual(len(m.backups()), 1)

    def test_a_reload_dockerd_does_not_apply_stops_the_upgrade(self) -> None:
        m = self.machine()
        m.write_daemon_json(SYSBOX_DAEMON_JSON)
        m.set_installed(OLD_VERSION)

        result = m.install(
            DOCKER_LIVE_RESTORE="1", DOCKER_ACTIVE="1", DOCKER_CE_VERSION=VERSION, RELOAD_IGNORED="1"
        )

        self.assert_failed(result)
        self.assertIn("does not report live-restore on", result.stderr)
        self.assertEqual(m.calls_of("apt-get "), [])
        self.assertEqual(m.installed("docker-ce"), OLD_VERSION)

    def test_a_linked_daemon_json_stays_a_link(self) -> None:
        m = self.machine()
        target = m.root / "managed" / "daemon.json"
        target.parent.mkdir()
        _ = target.write_text(SYSBOX_DAEMON_JSON, encoding="utf-8")
        m.docker_dir.mkdir(parents=True)
        m.daemon_json.symlink_to(target)

        result = m.install(DOCKER_LIVE_RESTORE="1")

        self.assert_ok(result)
        self.assertTrue(m.daemon_json.is_symlink())
        self.assertEqual(m.daemon_json.resolve(), target)
        self.assertIs(json_object(target)["live-restore"], True)
        backups = [path.read_text(encoding="utf-8") for path in target.parent.glob("daemon.json.bak-*")]
        self.assertEqual(backups, [SYSBOX_DAEMON_JSON])

    def test_a_dockerd_that_does_not_answer_stops_the_upgrade(self) -> None:
        """docker info hangs: each call ends after 10 s, and the step fails in bounded time."""
        m = self.machine()
        m.write_daemon_json(SYSBOX_DAEMON_JSON)
        m.set_installed(OLD_VERSION)

        started = time.monotonic()
        result = m.install(
            DOCKER_LIVE_RESTORE="1", DOCKER_ACTIVE="1", DOCKER_CE_VERSION=VERSION, DOCKER_INFO_HANGS="1"
        )

        self.assertLess(time.monotonic() - started, 40)
        self.assert_failed(result)
        self.assertIn("does not report live-restore on", result.stderr)
        self.assertEqual(m.calls_of("apt-get "), [])

    def test_an_empty_daemon_json_changes_nothing(self) -> None:
        """jq gives no output for an empty file; without dockerd nothing else would catch it."""
        m = self.machine()
        (m.bin_dir / "dockerd").unlink()
        m.write_daemon_json("")

        result = m.install(DOCKER_LIVE_RESTORE="1")

        self.assert_failed(result)
        self.assertEqual(m.daemon_json.read_text(encoding="utf-8"), "")
        self.assertEqual(m.docker_dir_names(), {"daemon.json"})


class PinnedVersion(InstallerCase):
    def test_older_version_installs_the_pin_without_downgrades(self) -> None:
        """(d) 29.9.2 installed: apt-get installs exactly the pinned pair, and the pin is written."""
        m = self.machine()
        m.set_installed(OLD_VERSION)
        m.set_installed("present", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin")

        result = m.install(DOCKER_CE_VERSION=VERSION)

        self.assert_ok(result)
        calls = m.calls()
        self.assertEqual(m.calls_of("apt-get install"), [INSTALL_PINNED], calls)
        self.assertFalse(any("--allow-downgrades" in call for call in calls), calls)
        self.assertLess(calls.index("apt-get update -qq"), calls.index(INSTALL_PINNED))
        self.assertIn("apt-get-saw DEBIAN_FRONTEND=noninteractive", calls)
        self.assertEqual(m.preferences.read_text(encoding="utf-8"), PIN_TEXT)
        self.assertEqual(m.sources.read_text(encoding="utf-8"), SOURCES_TEXT)
        self.assertEqual(m.keyring.read_text(encoding="utf-8"), KEY_TEXT)
        for path in (m.preferences, m.sources, m.keyring):
            self.assert_mode(path, 0o644)
        self.assertEqual(m.installed("docker-ce"), VERSION)
        self.assertEqual(m.installed("docker-ce-cli"), VERSION)
        # The daemon.json step is off unless DOCKER_LIVE_RESTORE=1.
        self.assertFalse(m.docker_dir.exists())

    def test_at_the_pin_installs_nothing(self) -> None:
        """(e) Both packages at the pin: no apt-get call; only the missing apt files are written."""
        m = self.machine()
        m.set_installed(VERSION)

        result = m.install(DOCKER_CE_VERSION=VERSION)

        self.assert_ok(result)
        self.assertEqual(m.calls_of("apt-get "), [])
        self.assertEqual(m.preferences.read_text(encoding="utf-8"), PIN_TEXT)
        self.assertEqual(m.sources.read_text(encoding="utf-8"), SOURCES_TEXT)

    def test_at_the_pin_with_every_file_in_place_changes_nothing(self) -> None:
        """sami-devbox after the upgrade and a first run: no write, no download, no apt-get."""
        m = self.machine()
        m.set_installed(VERSION)
        self.assert_ok(m.install(DOCKER_CE_VERSION=VERSION))
        files = (m.preferences, m.sources, m.keyring)
        before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in files]

        result = m.install(DOCKER_CE_VERSION=VERSION)

        self.assert_ok(result)
        self.assertEqual([(path.read_bytes(), path.stat().st_mtime_ns) for path in files], before)
        self.assertEqual(m.calls_of("sudo "), [])
        self.assertEqual(m.calls_of("curl "), [])
        self.assertEqual(m.calls_of("apt-get "), [])

    def test_an_existing_repository_is_kept(self) -> None:
        """get.docker.com made the keyring and the list: neither is downloaded or written again."""
        m = self.machine()
        m.set_installed(OLD_VERSION)
        existing = {m.sources: "deb https://example.invalid noble stable\n", m.keyring: "key\n"}
        for path, text in existing.items():
            path.parent.mkdir(parents=True)
            _ = path.write_text(text, encoding="utf-8")

        result = m.install(DOCKER_CE_VERSION=VERSION)

        self.assert_ok(result)
        self.assertEqual(m.calls_of("curl "), [])
        for path, text in existing.items():
            self.assertEqual(path.read_text(encoding="utf-8"), text, path)

    def test_no_docker_also_installs_its_other_packages(self) -> None:
        m = self.machine()

        result = m.install(DOCKER_CE_VERSION=VERSION)

        self.assert_ok(result)
        self.assertEqual(
            m.calls_of("apt-get install"),
            [f"{INSTALL_PINNED} containerd.io docker-buildx-plugin docker-compose-plugin"],
        )
        self.assertEqual(m.installed("docker-ce"), VERSION)
        self.assertEqual(m.installed("containerd.io"), "latest")

    def test_a_newer_installed_version_fails_loudly(self) -> None:
        """apt refuses the downgrade; the installer does not try again with --allow-downgrades."""
        m = self.machine()
        m.set_installed(NEWER_VERSION)

        result = m.install(DOCKER_CE_VERSION=VERSION, APT_REFUSES_DOWNGRADE="1")

        self.assert_failed(result)
        self.assertIn("--allow-downgrades", result.stderr)
        self.assertEqual(m.calls_of("apt-get install"), [INSTALL_PINNED])
        self.assertEqual(m.installed("docker-ce"), NEWER_VERSION)

    def test_another_version_after_apt_get_fails(self) -> None:
        m = self.machine()
        m.set_installed(OLD_VERSION)

        result = m.install(DOCKER_CE_VERSION=VERSION, APT_INSTALLS_VERSION=NEWER_VERSION)

        self.assert_failed(result)
        self.assertIn(f"is not at {VERSION}", result.stderr)

    def test_not_ubuntu_writes_nothing(self) -> None:
        m = self.machine()
        _ = m.os_release.write_text("ID=debian\nVERSION_CODENAME=trixie\n", encoding="utf-8")

        result = m.install(DOCKER_CE_VERSION=VERSION)

        self.assert_failed(result)
        self.assertIn("not Ubuntu", result.stderr)
        self.assertFalse(m.apt_dir.exists())
        self.assertEqual(m.calls_of("apt-get "), [])


class Defaults(InstallerCase):
    def test_neither_variable_runs_get_docker_com_as_before(self) -> None:
        """(f) No daemon.json, no apt pin, no apt-get: get.docker.com, then the docker group."""
        m = self.machine()
        (m.bin_dir / "docker").unlink()

        result = m.install(USER_GROUPS="tester")

        self.assert_ok(result)
        calls = m.calls()
        self.assertEqual(m.calls_of("curl "), ["curl -fsSL https://get.docker.com"], calls)
        self.assertIn("sudo sh", calls)
        self.assertIn("get.docker.com script ran", calls)
        self.assertEqual(m.calls_of("sudo usermod"), ["sudo usermod -aG docker tester"])
        self.assertFalse(m.docker_dir.exists())
        self.assertFalse(m.apt_dir.exists())
        self.assertFalse(m.sysctl_dir.exists())
        for command in ("apt-get ", "dpkg", "dockerd ", "systemctl ", "docker ", "sysctl "):
            self.assertEqual(m.calls_of(command), [], command)

    def test_neither_variable_and_docker_present_downloads_nothing(self) -> None:
        m = self.machine()

        result = m.install()

        self.assert_ok(result)
        self.assertEqual(m.calls_of("curl "), [])
        self.assertEqual(m.calls_of("sudo "), [])


class ConntrackTimeWait(InstallerCase):
    def test_writes_the_file_and_applies_it(self) -> None:
        """No file yet: devbox/sysctl-conntrack.conf is written in place, then sysctl applies it."""
        m = self.machine()

        result = m.install(DOCKER_CONNTRACK_TIME_WAIT="1")

        self.assert_ok(result)
        self.assertEqual(m.conntrack_conf.read_bytes(), CONNTRACK_CONF.read_bytes())
        self.assert_mode(m.conntrack_conf, 0o644)
        self.assertEqual(m.sysctl_dir_names(), {m.conntrack_conf.name})
        self.assertEqual(m.calls_of("sudo sysctl"), [f"sudo sysctl -q -p {m.conntrack_conf}"])
        self.assertEqual(m.time_wait.read_text(encoding="utf-8"), "15\n")

    def test_second_run_writes_nothing_and_applies_nothing(self) -> None:
        m = self.machine()
        self.assert_ok(m.install(DOCKER_CONNTRACK_TIME_WAIT="1"))
        status = m.conntrack_conf.stat()

        result = m.install(DOCKER_CONNTRACK_TIME_WAIT="1")

        self.assert_ok(result)
        self.assertIn("in place already", result.stdout)
        self.assertEqual(m.conntrack_conf.read_bytes(), CONNTRACK_CONF.read_bytes())
        self.assertEqual(m.conntrack_conf.stat().st_ino, status.st_ino)
        self.assertEqual(m.conntrack_conf.stat().st_mtime_ns, status.st_mtime_ns)
        self.assertEqual(m.sysctl_dir_names(), {m.conntrack_conf.name})
        self.assertEqual(m.calls_of("sudo "), [])
        self.assertEqual(m.calls_of("sysctl "), [])

    def test_the_hand_written_file_is_replaced(self) -> None:
        """sami-devbox: the file written by hand, and the kernel at 15 already."""
        m = self.machine()
        m.sysctl_dir.mkdir(parents=True)
        _ = m.conntrack_conf.write_text(HAND_CONNTRACK_CONF, encoding="utf-8")
        _ = m.time_wait.write_text("15\n", encoding="utf-8")

        result = m.install(DOCKER_CONNTRACK_TIME_WAIT="1")

        self.assert_ok(result)
        self.assertEqual(m.conntrack_conf.read_bytes(), CONNTRACK_CONF.read_bytes())
        self.assert_mode(m.conntrack_conf, 0o644)
        self.assertEqual(m.sysctl_dir_names(), {m.conntrack_conf.name})
        self.assertEqual(m.calls_of("sudo sysctl"), [f"sudo sysctl -q -p {m.conntrack_conf}"])
        self.assertEqual(m.time_wait.read_text(encoding="utf-8"), "15\n")

    def test_a_kernel_at_another_value_gets_the_file_in_place(self) -> None:
        """The file is in place and the kernel holds 120 (a hand sysctl -w): no write, one apply."""
        m = self.machine()
        self.assert_ok(m.install(DOCKER_CONNTRACK_TIME_WAIT="1"))
        mtime = m.conntrack_conf.stat().st_mtime_ns
        _ = m.time_wait.write_text("120\n", encoding="utf-8")

        result = m.install(DOCKER_CONNTRACK_TIME_WAIT="1")

        self.assert_ok(result)
        self.assertEqual(m.conntrack_conf.stat().st_mtime_ns, mtime)
        self.assertEqual(m.calls_of("sudo sysctl"), [f"sudo sysctl -q -p {m.conntrack_conf}"])
        self.assertEqual(m.time_wait.read_text(encoding="utf-8"), "15\n")

    def test_a_kernel_that_keeps_its_value_fails_the_install(self) -> None:
        m = self.machine()

        result = m.install(DOCKER_CONNTRACK_TIME_WAIT="1", SYSCTL_IGNORED="1")

        self.assert_failed(result)
        self.assertIn("is 120, not 15", result.stderr)

    def test_a_kernel_without_the_setting_fails_the_install(self) -> None:
        """No nf_conntrack in the kernel: sysctl -p fails, and so does the install."""
        m = self.machine()
        m.time_wait.unlink()

        result = m.install(DOCKER_CONNTRACK_TIME_WAIT="1")

        self.assert_failed(result)
        self.assertIn("cannot stat /proc/sys/net/netfilter/nf_conntrack_tcp_timeout_time_wait", result.stderr)
        self.assertFalse(m.time_wait.exists())
        self.assertEqual(m.calls_of("sudo sysctl"), [f"sudo sysctl -q -p {m.conntrack_conf}"])

    def test_the_rollback_value_is_applied_from_the_file(self) -> None:
        """The header's rollback: 120 in the file; the next run writes it and the kernel takes it."""
        m = self.machine()
        self.assert_ok(m.install(DOCKER_CONNTRACK_TIME_WAIT="1"))
        rollback = CONNTRACK_CONF.read_text(encoding="utf-8").replace(" = 15\n", " = 120\n")
        self.assertNotEqual(rollback, CONNTRACK_CONF.read_text(encoding="utf-8"))
        _ = m.conntrack_source.write_text(rollback, encoding="utf-8")

        result = m.install(DOCKER_CONNTRACK_TIME_WAIT="1")

        self.assert_ok(result)
        self.assertEqual(m.conntrack_conf.read_text(encoding="utf-8"), rollback)
        self.assertEqual(m.time_wait.read_text(encoding="utf-8"), "120\n")

    def test_off_unless_asked(self) -> None:
        """The other two steps leave sysctl alone."""
        m = self.machine()
        m.set_installed(VERSION)

        result = m.install(DOCKER_LIVE_RESTORE="1", DOCKER_CE_VERSION=VERSION)

        self.assert_ok(result)
        self.assertFalse(m.sysctl_dir.exists())
        self.assertEqual(m.calls_of("sysctl "), [])
        self.assertEqual(m.calls_of("sudo sysctl"), [])


class Order(InstallerCase):
    def test_live_restore_is_on_before_the_version_changes(self) -> None:
        """(g) daemon.json has live-restore, and the running dockerd reports it, before apt-get install."""
        m = self.machine()
        m.write_daemon_json(SYSBOX_DAEMON_JSON)
        m.set_installed(OLD_VERSION)

        result = m.install(DOCKER_LIVE_RESTORE="1", DOCKER_CE_VERSION=VERSION, DOCKER_ACTIVE="1")

        self.assert_ok(result)
        calls = m.calls()
        install = calls.index(INSTALL_PINNED)
        replace = next(
            index
            for index, call in enumerate(calls)
            if call.startswith("sudo mv -f -- ") and call.endswith(f" {m.daemon_json}")
        )
        self.assertLess(replace, install, calls)
        self.assertLess(calls.index("systemctl reload docker.service"), install, calls)
        self.assertIn("apt-get-saw live-restore true", calls)
        self.assertIn("apt-get-saw running live-restore true", calls)
        self.assertEqual(m.installed("docker-ce"), VERSION)


if __name__ == "__main__":
    _ = unittest.main()
