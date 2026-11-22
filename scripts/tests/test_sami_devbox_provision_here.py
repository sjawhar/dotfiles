#!/usr/bin/env python3
"""Tests for devbox/sami-devbox-provision on the machine it provisions (dispatch://INFRA-5140).

On sami-devbox itself (`hostname -s` is exactly $VM) `remote` runs its command in a local login shell in
$HOME and each copy to the machine writes the local path, with no SSH; step secrets copies nothing, steps
vm and host refuse, and step speed is skipped. From any other machine, a longer or empty host name and a
failing `hostname` included, every step makes the ssh, rsync and gcloud calls it made before.

The real script is cut before its entry point and sourced beside a leak-check-tag.env whose tag is that of
a stand-in agent-infra clone, each function run alone. `hostname` is a stub on PATH that prints FAKE_HOST
(or fails); `ssh`, `rsync`, `gcloud` and `tailscale` are stubs that log their calls (rsync its
destination), and with FAKE_SSH_RUNS the stand-in ssh runs its command as sshd would, in another HOME. A
step run whole gets a `remote` that only logs, so its copies are seen and nothing is installed.
"""

import hashlib
import os
import shlex
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
# Host names that are not the VM's, and a `hostname` that fails: each takes the SSH route.
ELSEWHERE = {"laptop": {"FAKE_HOST": "laptop"}, "a longer name": {"FAKE_HOST": "sami-devbox-2"},
             "an empty name": {"FAKE_HOST": ""}, "a failing hostname": {"FAKE_HOST": "sami-devbox", "FAKE_HOSTNAME_FAILS": "1"}}
ON_THE_BOX = {"FAKE_HOST": "sami-devbox"}
# The stand-in tag's leak-check/ files: the four step leakcheck installs, then hook files step prepush installs.
HOME_FILES = ("public-leak-check.sh", "public_leak_check.py", "README.md", "must-pass-lines.txt")
HOOK_FILES = ("install-prepush-hook.sh", "sweep-hooks.sh", "install-global-hooks.sh", "global-hooks/dispatch")


def write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def sums(root: Path, names: tuple[str, ...]) -> str:
    return "\n".join(f"{hashlib.sha256((root / n).read_bytes()).hexdigest()}  {n}" for n in names)


class ProvisionHere(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # What no test changes, made once: the stubs, the stand-in agent-infra clone and the cut script.
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.fixed = Path(tmp.name)
        bin_ = cls.fixed / "bin"
        write_exe(bin_ / "hostname", '#!/bin/sh\n[ -z "${FAKE_HOSTNAME_FAILS:-}" ] || exit 1\n[ "$1" = -s ] && echo "$FAKE_HOST"\n')
        # sshd runs the last argument with the user's shell in that user's home.
        write_exe(bin_ / "ssh", '#!/bin/bash\nprintf "ssh %s\\n" "$*" >> "$CALL_LOG"\n[ -n "${FAKE_SSH_RUNS:-}" ] || exit 0\n'
                  'printf "%s\\n" "$@" > "$SSH_ARGS"\ncd "$FAR_HOME" && HOME="$FAR_HOME" bash -c "${@: -1}"\n')
        write_exe(bin_ / "rsync", '#!/bin/bash\nprintf "rsync %s\\n" "${@: -1}" >> "$CALL_LOG"\n')
        for name in ("gcloud", "tailscale"):
            write_exe(bin_ / name, f'#!/bin/sh\necho "{name} $*" >> "$CALL_LOG"\nexit 1\n')
        cls.path = f"{bin_}:{os.environ['PATH']}"
        # The stand-in agent-infra clone: one commit holding the tag's files, both tags on it, origin itself.
        cls.infra = cls.fixed / "agent-infra"
        cls.lc = cls.infra / "leak-check"
        for name in HOME_FILES + HOOK_FILES:
            write_exe(cls.lc / name, f"#!/bin/sh\n# stand-in {name}\n")
        git = ["git", "-C", str(cls.infra), "-c", "user.name=T", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]
        subprocess.run(["git", "init", "-q", str(cls.infra)], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-qm", "leak-check"], check=True)
        for tag in ("leak-check-vtest", "leak-check-vprev"):
            subprocess.run([*git, "tag", tag], check=True)
        subprocess.run([*git, "remote", "add", "origin", str(cls.infra)], check=True)
        commit = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        devbox = cls.fixed / "devbox"
        devbox.mkdir()
        (devbox / "leak-check-tag.env").write_text(
            f"LEAK_CHECK_TAG=leak-check-vtest\nLEAK_CHECK_PREV=leak-check-vprev\nLEAK_CHECK_COMMIT={commit}\n"
            f"LEAK_CHECK_HOME_SHA256='{sums(cls.lc, HOME_FILES)}'\nLEAK_CHECK_HOOK_SHA256='{sums(cls.lc, HOOK_FILES)}'\n", encoding="utf-8")
        text = SCRIPT.read_text(encoding="utf-8")
        cls.lib = devbox / "lib.sh"
        cls.lib.write_text(text[:text.index('from="" only=""\n')], encoding="utf-8")
        listed = subprocess.run(["bash", "-c", f'source "{cls.lib}"; printf "%s\\n" "${{SECRET_FILES[@]}}"'],
                                check=True, capture_output=True, text=True, timeout=60).stdout.split()
        assert listed, "SECRET_FILES is empty"
        cls.secret_files = listed

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.far_home = self.root / "far-home"
        self.home.mkdir()
        self.far_home.mkdir()
        self.log = self.root / "calls.log"
        self.ssh_args = self.root / "ssh.args"
        self.env = dict(os.environ, PATH=self.path, HOME=str(self.home), CALL_LOG=str(self.log), SSH_ARGS=str(self.ssh_args),
                        FAR_HOME=str(self.far_home), AGENT_INFRA=str(self.infra))
        for name in ("FAKE_HOST", "FAKE_HOSTNAME_FAILS", "FAKE_SSH_RUNS"):
            self.env.pop(name, None)
        # Every SECRET_FILES path, in the test's HOME, so step secrets finds them all.
        for f in self.secret_files:
            path = self.home / f
            if f.endswith("/"):
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x\n", encoding="utf-8")
        # The shared path holds the tag, and the install record ends with a free slot.
        self.shared = self.root / "shared"
        (self.shared / ".editor-slot").mkdir(parents=True)
        for name in HOME_FILES:
            (self.shared / name).write_bytes((self.lc / name).read_bytes())
        self.record = self.shared / ".editor-slot" / "holder"
        self.record.write_text("RELEASED 2026-11-22T00:00:00Z by a test: slot FREE\n", encoding="utf-8")

    def run_lib(self, where: dict[str, str], call: str, log_remote: bool = False) -> subprocess.CompletedProcess[str]:
        stand_in = 'remote() { echo "remote" >> "$CALL_LOG"; }\n' if log_remote else ""
        script = (f'source "{self.lib}"\nLEAK_CHECK_SHARED="{self.shared}" LEAK_CHECK_RECORD="{self.record}"\n'
                  f'{stand_in}cd /\n{call}')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=120, check=False,
                              env={**self.env, **where})

    def calls(self) -> list[str]:
        lines = self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []
        self.log.unlink(missing_ok=True)
        return lines

    def test_on_the_box_remote_runs_here_in_home_with_no_ssh(self) -> None:
        r = self.run_lib(ON_THE_BOX, f"remote {shlex.quote(COMMAND)}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[-2:], [str(self.home), f"a b|{self.home}|$x| login"])
        self.assertEqual(self.calls(), [])

    def test_elsewhere_remote_is_the_ssh_call_it_was(self) -> None:
        for case, where in ELSEWHERE.items():
            with self.subTest(host=case):
                self.ssh_args.unlink(missing_ok=True)
                r = self.run_lib({**where, "FAKE_SSH_RUNS": "1"}, f"remote {shlex.quote(COMMAND)}")
                self.assertEqual(r.returncode, 0, r.stderr)
                args = self.ssh_args.read_text(encoding="utf-8").split("\n")
                self.assertEqual(args[:len(SSH_OPTS)], SSH_OPTS)
                self.assertTrue(args[len(SSH_OPTS)].startswith("bash -lc "), args)
                # The command arrives whole: the far side prints what the box itself would, in its own home.
                self.assertEqual(r.stdout.splitlines()[-2:], [str(self.far_home), f"a b|{self.far_home}|$x| login"])
                self.calls()

    def test_a_copy_writes_the_local_path_on_the_box_and_sami_at_vm_elsewhere(self) -> None:
        call = "on_vm /tmp/plc-stage/; on_vm /tmp/pph-stage/"
        r = self.run_lib(ON_THE_BOX, call)
        self.assertEqual(r.stdout.splitlines(), ["/tmp/plc-stage/", "/tmp/pph-stage/"], r.stderr)
        for case, where in ELSEWHERE.items():
            with self.subTest(host=case):
                r = self.run_lib(where, call)
                self.assertEqual(r.stdout.splitlines(), ["sami@sami-devbox:/tmp/plc-stage/", "sami@sami-devbox:/tmp/pph-stage/"], r.stderr)

    def run_steps(self, where: dict[str, str]) -> dict[str, tuple[subprocess.CompletedProcess[str], list[str]]]:
        return {step: (self.run_lib(where, step, log_remote=True), self.calls()) for step in ("step_secrets", "step_leakcheck", "step_prepush")}

    def test_on_the_box_the_steps_copy_to_local_paths_and_call_no_ssh(self) -> None:
        runs = self.run_steps(ON_THE_BOX)
        want = {"step_secrets": [], "step_leakcheck": ["rsync /tmp/plc-stage/"], "step_prepush": ["rsync /tmp/pph-stage/"]}
        for step, (r, calls) in runs.items():
            with self.subTest(step=step):
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual([c for c in calls if c.startswith("rsync ")], want[step])
                self.assertFalse([c for c in calls if "sami@" in c or c.startswith("ssh ")], calls)
                self.assertIn("remote", calls)
        self.assertIn("the secret files are already here; nothing copied", runs["step_secrets"][0].stdout)
        self.assertEqual([line.split()[0] for line in self.record.read_text(encoding="utf-8").splitlines()[-3:]], ["TAKEN", "INSTALLED", "RELEASED"])

    def test_elsewhere_the_steps_copy_to_sami_at_vm(self) -> None:
        # The control for the test above: off the box the same steps copy over SSH to sami@sami-devbox.
        runs = self.run_steps(ELSEWHERE["laptop"])
        want = {"step_secrets": ["rsync sami@sami-devbox:"], "step_leakcheck": ["rsync sami@sami-devbox:/tmp/plc-stage/"],
                "step_prepush": ["rsync sami@sami-devbox:/tmp/pph-stage/"]}
        for step, (r, calls) in runs.items():
            with self.subTest(step=step):
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual([c for c in calls if c.startswith("rsync ")], want[step])
        self.assertNotIn("already here", runs["step_secrets"][0].stdout)

    def test_vm_and_host_refuse_on_the_box_and_call_nothing(self) -> None:
        for step in ("step_vm", "step_host"):
            with self.subTest(step=step):
                r = self.run_lib(ON_THE_BOX, step)
                self.assertEqual(r.returncode, 1, step)
                self.assertIn("run it from another machine", r.stderr)
                self.assertEqual(self.calls(), [])

    def test_speed_is_skipped_on_the_box_and_calls_nothing(self) -> None:
        # Skipped, not refused, so a `--from` run on the box goes on to step check.
        r = self.run_lib(ON_THE_BOX, "step_speed && echo after")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("skipped on sami-devbox", r.stdout)
        self.assertEqual(r.stdout.splitlines()[-1], "after")
        self.assertEqual(self.calls(), [])

    def test_elsewhere_vm_host_and_speed_reach_the_machine(self) -> None:
        # The control for the two tests above: off the box each step makes its calls (stubs that fail or return at once).
        want = {"step_vm": "gcloud compute instances describe sami-devbox --zone us-west4-a --project theorem-agent-infra",
                "step_host": "gcloud compute ssh sami@sami-devbox --zone us-west4-a --project theorem-agent-infra",
                "step_speed": "ssh " + " ".join(SSH_OPTS) + " cat >/dev/null"}
        for step, first in want.items():
            with self.subTest(step=step):
                r = self.run_lib(ELSEWHERE["laptop"], step)
                self.assertNotIn("run it from another machine", r.stderr)
                self.assertNotIn("skipped", r.stdout)
                self.assertTrue([c for c in self.calls() if c.startswith(first)], r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
