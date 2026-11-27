#!/usr/bin/env python3
"""oryx/install.sh arms dotfiles-follow without changing forward or using Tailscale.

Its op step, alone or first in a full run, links oryx's mise pin of op into
~/.config/mise/conf.d and runs `mise install op` under the install lock, with no sudo,
systemctl or loginctl.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


DOTFILES = Path(__file__).resolve().parents[2]
INSTALL_COMMAND = 'rc=0; mise install op || rc=$?; [ "$rc" != 75 ] || rc=1; exit "$rc"'


SYSTEMCTL = r'''echo "$*" >>"$STUB_LOG"
case "$*" in
    "--user list-timers --all dotfiles-follow.timer")
        printf 'NEXT LEFT LAST PASSED UNIT ACTIVATES\nThu 1min - dotfiles-follow.timer dotfiles-follow.service\n'
        ;;
    "--user show -p NextElapseUSecRealtime --value dotfiles-follow.timer")
        echo 'Thu 2026-11-13 00:00:00 UTC'
        ;;
    "is-enabled --quiet bluetooth" | "is-active --quiet bluetooth")
        [[ "$STUB_BLUETOOTH" == on ]]
        ;;
esac
'''

# sudo logs its argv and runs the command, as root would: the stubs log under their own names.
SUDO = 'echo "sudo $*" >>"$STUB_LOG"\nexec "$@"'

LOGINCTL = r'''echo "loginctl $*" >>"$STUB_LOG"
if [[ "$*" == "show-user sami -p Linger --value" ]]; then
    echo "$STUB_LINGER"
fi
'''

HOSTNAME = 'echo "hostname $*" >>"$STUB_LOG"\nprintf "%s\\n" "$STUB_HOSTNAME"'

MISE = r'''global=unset
[[ -z ${MISE_GLOBAL_CONFIG_FILE+x} ]] || global=set
printf 'mise %s PWD=%s MISE_DATA_DIR=%s MISE_CONFIG_DIR=%s MISE_GLOBAL_CONFIG_FILE=%s DOTFILES_INSTALL_LOCK_HELD=%s\n' \
    "$*" "$PWD" "${MISE_DATA_DIR-}" "${MISE_CONFIG_DIR-}" "$global" "${DOTFILES_INSTALL_LOCK_HELD-unset}" >>"$STUB_LOG"
exit "${STUB_MISE_RC:-0}"
'''

FLOCK = r'''echo "flock $*" >>"$STUB_LOG"
[[ "${STUB_FLOCK:-}" != timeout ]] || exit 75
while [[ $# -gt 0 && $1 == -* ]]; do
    case "$1" in
        -w | -E) shift 2 ;;
        *) shift ;;
    esac
done
shift
exec "$@"
'''


def write_command(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def write_mise_stub(tree: Path) -> None:
    mise = tree / "bin" / "mise"
    mise.parent.mkdir(parents=True, exist_ok=True)
    if mise.exists() or mise.is_symlink():
        mise.unlink()
    write_command(mise.parent, "mise", MISE)


class OryxInstall(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.home = root / "home"
        self.dotfiles = self.home / ".dotfiles"
        self.home.mkdir()
        shutil.copytree(
            DOTFILES,
            self.dotfiles,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", "__pycache__"),
        )
        write_mise_stub(self.dotfiles)
        self.installer = self.dotfiles / "oryx/install.sh"
        self.log = root / "systemctl.log"
        self.forward_log = root / "forward.log"
        fakebin = root / "bin"
        fakebin.mkdir()
        write_command(fakebin, "hostname", HOSTNAME)
        write_command(fakebin, "systemd-detect-virt", "exit 1")
        write_command(fakebin, "systemctl", SYSTEMCTL)
        write_command(fakebin, "sudo", SUDO)
        write_command(fakebin, "loginctl", LOGINCTL)
        write_command(fakebin, "forward", 'echo "$*" >>"$FORWARD_LOG"')
        write_command(fakebin, "flock", FLOCK)
        for tool in ("bash", "dirname", "mkdir", "ln", "grep", "readlink", "cmp", "diff"):
            tool_path = shutil.which(tool)
            if tool_path is None:
                raise RuntimeError(f"test requires {tool}")
            (fakebin / tool).symlink_to(tool_path)
        self.env = {
            "HOME": str(self.home),
            "PATH": str(fakebin),
            "STUB_HOSTNAME": "oryx",
            "STUB_LOG": str(self.log),
            "FORWARD_LOG": str(self.forward_log),
            "MISE_DATA_DIR": str(self.data_dir()),
            "MISE_GLOBAL_CONFIG_FILE": str(root / "wrong-global-config.toml"),
            "MISE_CONFIG_DIR": str(root / "wrong-mise-config"),
            # The agent phone line's settings as a new oryx has them: bluetooth off, no linger.
            "STUB_BLUETOOTH": "off",
            "STUB_LINGER": "no",
        }
        self.forward_config = self.home / ".config/forward/config.toml"
        self.forward_config.parent.mkdir(parents=True)
        self.forward_bytes = b'listen = "127.0.0.1"\npeer = "127.0.0.1"\n'
        self.forward_config.write_bytes(self.forward_bytes)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def data_dir(self) -> Path:
        return self.home.parent / "mise-data"

    def op_link(self) -> Path:
        return self.home / ".config/mise/conf.d/op.toml"

    def flock_call(self, data_dir: Path) -> str:
        return f"flock -w 600 -E 75 {data_dir / '.dotfiles-install.lock'} bash -c {INSTALL_COMMAND}"

    def mise_call(self, data_dir: Path) -> str:
        return (
            f"mise install op PWD={self.home} MISE_DATA_DIR={data_dir} "
            f"MISE_CONFIG_DIR={self.home / '.config/mise'} MISE_GLOBAL_CONFIG_FILE=unset "
            "DOTFILES_INSTALL_LOCK_HELD=1"
        )

    def home_paths(self) -> set[Path]:
        return {
            path.relative_to(self.home)
            for path in self.home.rglob("*")
            if path.relative_to(self.home).parts[0] != ".dotfiles"
        }

    def install(
        self, *arguments: str, cwd: Path | None = None, unset: tuple[str, ...] = (), **extra: str
    ) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **extra}
        for name in unset:
            _ = env.pop(name, None)
        return subprocess.run(
            ["/bin/bash", str(self.installer), *arguments],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_arms_follow_without_tailscale_or_forward(self) -> None:
        self.assertIsNone(shutil.which("tailscale", path=self.env["PATH"]))

        result = self.install()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dotfiles-follow: armed", result.stdout)
        dropin = self.home / ".config/systemd/user/dotfiles-follow.service.d/oryx.conf"
        self.assertTrue(dropin.is_symlink())
        self.assertEqual(dropin.resolve(), self.dotfiles / "oryx/dotfiles-follow.service.d/oryx.conf")
        for suffix in ("service", "timer"):
            unit = self.home / f".config/systemd/user/dotfiles-follow.{suffix}"
            self.assertTrue(unit.is_symlink())
            self.assertEqual(unit.resolve(), self.dotfiles / f"devbox/dotfiles-follow.{suffix}")
        self.assertEqual(self.op_link().readlink(), self.dotfiles / "oryx/mise-op.toml")
        self.assertEqual(self.calls(), [
            "hostname -s",
            self.flock_call(self.data_dir()),
            self.mise_call(self.data_dir()),
            "--user is-active --quiet dotfiles-follow.timer",
            "--user daemon-reload",
            "--user enable --now dotfiles-follow.timer",
            "--user list-timers --all dotfiles-follow.timer",
            "--user show -p NextElapseUSecRealtime --value dotfiles-follow.timer",
            "is-enabled --quiet bluetooth",
            "sudo systemctl enable --now bluetooth",
            "enable --now bluetooth",
            "loginctl show-user sami -p Linger --value",
            "sudo loginctl enable-linger sami",
            "loginctl enable-linger sami",
        ])
        self.assertEqual(self.forward_config.read_bytes(), self.forward_bytes)
        self.assertFalse(self.forward_log.exists())

    def test_refuses_on_another_host_before_linking_or_arming(self) -> None:
        result = self.install(STUB_HOSTNAME="sami-devbox")

        self.assertEqual(result.returncode, 1)
        self.assertIn("runs only on oryx", result.stderr)
        self.assertFalse(self.op_link().exists())
        self.assertFalse((self.home / ".config/systemd/user/dotfiles-follow.service.d/oryx.conf").exists())
        self.assertFalse((self.home / ".config/systemd/user/dotfiles-follow.service").exists())
        self.assertFalse((self.home / ".config/systemd/user/dotfiles-follow.timer").exists())
        self.assertEqual(self.calls(), ["hostname -s"])
        self.assertEqual(self.forward_config.read_bytes(), self.forward_bytes)

    def test_leaves_bluetooth_and_linger_alone_when_already_on(self) -> None:
        result = self.install(STUB_BLUETOOTH="on", STUB_LINGER="yes")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-3:], [
            "is-enabled --quiet bluetooth",
            "is-active --quiet bluetooth",
            "loginctl show-user sami -p Linger --value",
        ])
        self.assertFalse([call for call in self.calls() if call.startswith("sudo ")])

    def test_run_from_a_workspace_links_the_dropin_from_home_dotfiles(self) -> None:
        # arm_user_timer links the units from ~/.dotfiles so that a unit armed from a workspace
        # checkout does not point into a directory that will be removed (installers/lib.sh).
        # systemd skips a dangling drop-in, and the unit then runs the plain ExecStart: the hook
        # sweep on, and alerts to lead-box-health and then the company SRE session.
        workspace = self.home / "src/dotfiles-wt"
        shutil.copytree(DOTFILES, workspace, symlinks=True, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        write_mise_stub(workspace)

        result = subprocess.run(
            ["/bin/bash", str(workspace / "oryx/install.sh")], env=self.env, capture_output=True, text=True
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        dropin = self.home / ".config/systemd/user/dotfiles-follow.service.d/oryx.conf"
        self.assertEqual(dropin.readlink(), self.dotfiles / "oryx/dotfiles-follow.service.d/oryx.conf")
        self.assertEqual(self.op_link().readlink(), self.dotfiles / "oryx/mise-op.toml")
        shutil.rmtree(workspace)
        self.assertTrue(dropin.exists())
        self.assertTrue(self.op_link().exists())

    def test_op_alone_links_only_the_pinned_config_and_installs_under_lock(self) -> None:
        before = self.home_paths()

        result = self.install("op")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            "hostname -s",
            self.flock_call(self.data_dir()),
            self.mise_call(self.data_dir()),
        ])
        self.assertEqual(
            self.home_paths() - before,
            {
                Path(".config/mise"),
                Path(".config/mise/conf.d"),
                Path(".config/mise/conf.d/op.toml"),
            },
        )
        self.assertEqual(self.op_link().readlink(), self.dotfiles / "oryx/mise-op.toml")

    def test_op_alone_twice_leaves_its_link_unchanged(self) -> None:
        first = self.install("op")
        self.assertEqual(first.returncode, 0, first.stderr)
        link = self.op_link()
        target = link.readlink()
        inode = link.lstat().st_ino
        first_calls = self.calls()

        second = self.install("op")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(link.readlink(), target)
        self.assertEqual(link.lstat().st_ino, inode)
        self.assertEqual(self.calls()[len(first_calls):], [
            "hostname -s",
            self.flock_call(self.data_dir()),
            self.mise_call(self.data_dir()),
        ])

    def test_op_on_another_host_refuses_before_linking(self) -> None:
        result = self.install("op", STUB_HOSTNAME="sami-devbox")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), ["hostname -s"])
        self.assertFalse(self.op_link().exists())

    def test_op_rejects_unknown_or_repeated_arguments_before_hostname(self) -> None:
        for arguments in (("unknown",), ("op", "op")):
            with self.subTest(arguments=arguments):
                result = self.install(*arguments)

                self.assertEqual(result.returncode, 2)
                self.assertIn("usage:", result.stderr)
                self.assertEqual(self.calls(), [])
                self.assertFalse(self.op_link().exists())

    def test_op_uses_home_mise_data_dir_when_its_data_dir_is_unset(self) -> None:
        data_dir = self.home / ".mise"

        result = self.install("op", unset=("MISE_DATA_DIR",))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), ["hostname -s", self.flock_call(data_dir), self.mise_call(data_dir)])

    def test_op_resolves_a_relative_mise_data_dir_before_changing_to_home(self) -> None:
        cwd = self.home.parent / "relative-data-cwd"
        cwd.mkdir()
        data_dir = cwd / "relative-data"

        result = self.install("op", cwd=cwd, MISE_DATA_DIR="relative-data")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), ["hostname -s", self.flock_call(data_dir), self.mise_call(data_dir)])

    def test_op_under_a_callers_lock_runs_mise_without_flock(self) -> None:
        result = self.install("op", DOTFILES_INSTALL_LOCK_HELD="1")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), ["hostname -s", self.mise_call(self.data_dir())])

    def test_op_exits_75_when_its_install_lock_times_out(self) -> None:
        result = self.install("op", STUB_FLOCK="timeout")

        self.assertEqual(result.returncode, 75)
        self.assertIn(str(self.data_dir() / ".dotfiles-install.lock"), result.stderr)
        self.assertEqual(self.calls(), ["hostname -s", self.flock_call(self.data_dir())])

    def test_op_treats_mises_75_as_a_failed_install_with_or_without_the_lock_marker(self) -> None:
        for held in (False, True):
            with self.subTest(held=held):
                _ = self.log.write_text("")
                if held:
                    result = self.install("op", STUB_MISE_RC="75", DOTFILES_INSTALL_LOCK_HELD="1")
                else:
                    result = self.install("op", STUB_MISE_RC="75")

                self.assertEqual(result.returncode, 1)
                self.assertNotIn(".dotfiles-install.lock", result.stderr)
                self.assertNotIn("600 s", result.stderr)
                expected = ["hostname -s"]
                if not held:
                    expected.append(self.flock_call(self.data_dir()))
                expected.append(self.mise_call(self.data_dir()))
                self.assertEqual(self.calls(), expected)

    def test_full_run_stops_before_arming_when_op_mise_fails(self) -> None:
        result = self.install(STUB_MISE_RC="1")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [
            "hostname -s",
            self.flock_call(self.data_dir()),
            self.mise_call(self.data_dir()),
        ])
        self.assertFalse((self.home / ".config/systemd/user/dotfiles-follow.service.d/oryx.conf").exists())
        self.assertFalse((self.home / ".config/systemd/user/dotfiles-follow.service").exists())
        self.assertFalse((self.home / ".config/systemd/user/dotfiles-follow.timer").exists())


if __name__ == "__main__":
    unittest.main()
