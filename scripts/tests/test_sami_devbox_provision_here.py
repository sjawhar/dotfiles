#!/usr/bin/env python3
"""Tests for devbox/sami-devbox-provision on the machine it provisions (dispatch://INFRA-5140).

On sami-devbox itself (`hostname -s` is $VM) `remote` runs its command in a local login shell in $HOME
and each copy to the machine writes the local path, with no SSH; steps vm, host and speed, which reach
the machine from outside, refuse there. From any other machine `remote` is the same ssh call as before.

The real script is cut before its entry point and sourced beside a copy of its leak-check-tag.env, each
function run alone. `hostname` is a stub on PATH that prints FAKE_HOST; `ssh`, `gcloud` and `tailscale`
are stubs that log their arguments, and the stand-in ssh runs its command as sshd would, in another HOME.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "devbox" / "sami-devbox-provision"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new", "sami@sami-devbox"]
# A command with spaces, quotes, $ and a newline: what crosses `remote` must reach bash -lc unchanged.
COMMAND = 'pwd\nprintf "%s|" "a b" "$HOME" \'$x\'; shopt -q login_shell && echo " login"'


def write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class ProvisionHere(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.far_home = self.root / "far-home"
        self.home.mkdir()
        self.far_home.mkdir()
        self.log = self.root / "calls.log"
        devbox = self.root / "devbox"
        devbox.mkdir()
        shutil.copy2(DOTFILES / "devbox" / "leak-check-tag.env", devbox / "leak-check-tag.env")
        text = SCRIPT.read_text(encoding="utf-8")
        self.lib = devbox / "lib.sh"
        self.lib.write_text(text[:text.index('from="" only=""\n')], encoding="utf-8")
        bin_ = self.root / "bin"
        write_exe(bin_ / "hostname", '#!/bin/sh\n[ "$1" = -s ] && echo "$FAKE_HOST"\n')
        # sshd runs the last argument with the user's shell in that user's home.
        write_exe(bin_ / "ssh", f'#!/bin/bash\nprintf "ssh %s\\n" "$*" >> "{self.log}"\n'
                  f'printf "%s\\n" "$@" > "{self.root}/ssh.args"\ncd "{self.far_home}" && HOME="{self.far_home}" bash -c "${{@: -1}}"\n')
        for name in ("gcloud", "tailscale"):
            write_exe(bin_ / name, f'#!/bin/sh\necho "{name} $*" >> "{self.log}"\nexit 1\n')
        self.env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", HOME=str(self.home))

    def run_lib(self, host: str, call: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", "-c", f'source "{self.lib}"\ncd /\n{call}'], capture_output=True, text=True,
                              timeout=60, check=False, env={**self.env, "FAKE_HOST": host})

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def test_on_the_box_remote_runs_here_in_home_with_no_ssh(self) -> None:
        r = self.run_lib("sami-devbox", f"remote {quoted(COMMAND)}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[-2:], [str(self.home), f"a b|{self.home}|$x| login"])
        self.assertEqual(self.calls(), [])

    def test_elsewhere_remote_is_the_ssh_call_it_was(self) -> None:
        r = self.run_lib("laptop", f"remote {quoted(COMMAND)}")
        self.assertEqual(r.returncode, 0, r.stderr)
        args = (self.root / "ssh.args").read_text(encoding="utf-8").split("\n")
        self.assertEqual(args[:len(SSH_OPTS)], SSH_OPTS)
        self.assertTrue(args[len(SSH_OPTS)].startswith("bash -lc "), args)
        # The command arrives whole: the far side prints what the box itself would, in its own home.
        self.assertEqual(r.stdout.splitlines()[-2:], [str(self.far_home), f"a b|{self.far_home}|$x| login"])

    def test_copies_write_the_local_path_on_the_box_and_sami_at_vm_elsewhere(self) -> None:
        call = "on_vm /tmp/plc-stage/; on_vm /tmp/pph-stage/; on_vm ''"
        here = self.run_lib("sami-devbox", call)
        far = self.run_lib("laptop", call)
        self.assertEqual(here.stdout.splitlines(), ["/tmp/plc-stage/", "/tmp/pph-stage/", f"{self.home}/"], here.stderr)
        self.assertEqual(far.stdout.splitlines(), ["sami@sami-devbox:/tmp/plc-stage/", "sami@sami-devbox:/tmp/pph-stage/",
                                                   "sami@sami-devbox:"], far.stderr)

    def test_every_rsync_to_the_machine_goes_through_on_vm(self) -> None:
        lines = [line for line in SCRIPT.read_text(encoding="utf-8").splitlines() if "rsync -" in line and not line.lstrip().startswith("#")]
        self.assertEqual(len(lines), 3, lines)
        for line in lines:
            self.assertIn('"$(on_vm ', line)
            self.assertNotIn("sami@$VM:", line)

    def test_the_outside_steps_refuse_on_the_box_and_call_nothing(self) -> None:
        for step in ("step_vm", "step_host", "step_speed"):
            r = self.run_lib("sami-devbox", step)
            self.assertEqual(r.returncode, 1, step)
            self.assertIn("run it from another machine", r.stderr, step)
            self.assertEqual(self.calls(), [], step)

    def test_elsewhere_step_vm_still_asks_gcloud(self) -> None:
        # The control for the refusal: off the box the same step reaches gcloud (a stub that fails).
        r = self.run_lib("laptop", "step_vm")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("run it from another machine", r.stderr)
        self.assertEqual(self.calls()[:1], ["gcloud compute instances describe sami-devbox --zone us-west4-a --project "
                                            "theorem-agent-infra --format=value(status,machineType.basename())"])


def quoted(s: str) -> str:
    """S as one bash word."""
    return "'" + s.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    unittest.main()
