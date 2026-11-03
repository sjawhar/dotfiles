#!/usr/bin/env python3
"""No resume path starts a session whose omp is live (scripts/omp-session-live).

On 2026-11-03 a second tmux server's restore resumed 51 live sessions a second time (00:30Z;
36 on the host, 15 in new boxes), and a pane restarter resumed one whose omp was alive because a
child of it had died (00:37Z). scripts/agent-session (every host or in-box omp launch) and
`agentbox new` (a box launched on the host) now ask omp-session-live and refuse, exit 3 and 1.

A transcript path names one file, not every file of that session: scripts/omp-fork resumes a
copy of a live session in its own --session-dir, and from f007ab4d the guard refused every such
fork by the id in the copy's name (the librarian's rounds 213 and 214, 2026-11-03: 26 of 26).

Technique: a fixture /proc (OMP_SESSION_LIVE_PROC) with comm, cmdline, cwd and fd links; for
agent-session, a scratch DOTFILES_DIR as test_agent_session_moved.py builds it.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
LIVE = DOTFILES / "scripts" / "omp-session-live"
SESSION = DOTFILES / "scripts" / "agent-session"
AGENTBOX = DOTFILES / "scripts" / "agentbox"
SID = "01a19336-7537-7291-98db-3b4c6de1d7de"
OTHER = "01a19336-0000-7000-8000-000000000002"
BOX = "2f03dfecf19dab0cf93409361f42c0496fa2dd8c685498693a3061bc10e8086d"


def executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class OmpSessionLive(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="omp-session-live-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.transcript = self.root / ".omp/agent/sessions/-lane" / f"2026-11-02T09-18-52-000Z_{SID}.jsonl"
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text("{}\n", encoding="utf-8")
        self.real = os.path.realpath(self.transcript)
        self.copy = self.root / "omp-fork-scratch" / self.transcript.name
        self.copy.parent.mkdir()
        shutil.copyfile(self.transcript, self.copy)

    def omp(self, pid: int, cmdline: list[str], holds: Path | None = None, comm: str = "omp",
            cwd: Path | None = None) -> None:
        d = self.proc / str(pid)
        (d / "fd").mkdir(parents=True)
        (d / "comm").write_text(comm + "\n", encoding="utf-8")
        (d / "cmdline").write_text("".join(a + "\0" for a in cmdline), encoding="utf-8")
        if holds:
            os.symlink(holds, d / "fd" / "21")
        if cwd:
            os.symlink(cwd, d / "cwd")

    def box(self, pid: int, nspid: int) -> Path:
        """Put omp PID in an agent box: its cgroup names the container, its status its pid there.
        A stub `docker` answers `docker exec BOX ...` from BOX's own /proc, box-proc/ here, where
        the omp holds the transcript as fd 32; it logs each call to DOCKER."""
        d = self.proc / str(pid)
        (d / "cgroup").write_text(f"0::/user.slice/user-1000.slice/user-1000-agentbox.slice/docker-{BOX}.scope/init.scope\n",
                                  encoding="utf-8")
        (d / "status").write_text(f"Name:\tomp\nNSpid:\t{pid}\t{nspid}\n", encoding="utf-8")
        (self.root / "box-proc" / str(nspid) / "fd").mkdir(parents=True)
        os.symlink(self.transcript, self.root / "box-proc" / str(nspid) / "fd" / "32")
        stubs = self.root / "stubs"
        stubs.mkdir()
        executable(stubs / "docker", f'echo "$*" >>"{self.root}/DOCKER"\n'
                                     f'[[ "$1 $2" == "exec {BOX}" && ! -e "{self.root}/BOX_GONE" ]] || exit 1\n'
                                     f'shift 2\nexec "${{@//\\/proc\\//{self.root}/box-proc/}}"')
        return stubs

    def live(self, target: str, path: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "OMP_SESSION_LIVE_PROC": str(self.proc)}
        if path:
            env["PATH"] = f"{path}:{env['PATH']}"
        return subprocess.run([str(LIVE), target], env=env, capture_output=True, text=True, timeout=60, check=False)

    def test_a_session_is_live_by_its_resume_argument_its_path_or_the_file_it_holds(self) -> None:
        self.omp(100, ["omp", "--resume", SID])
        self.assertEqual(self.live(SID).returncode, 0)
        self.assertIn("100\tits command line names", self.live(str(self.transcript)).stdout)
        self.assertEqual(self.live(SID[:13]).returncode, 0)
        shutil.rmtree(self.proc / "100")
        self.omp(101, ["omp"], holds=self.transcript)
        self.assertIn(f"101\tit holds {self.transcript} open", self.live(SID).stdout)

    def test_a_helper_another_session_and_another_program_are_not_it(self) -> None:
        self.omp(200, ["omp", "__omp_worker_daemon_broker", SID])
        self.omp(201, ["omp", "--resume", OTHER])
        self.omp(202, ["bash", "--resume", SID], comm="bash")
        result = self.live(SID)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stdout, "")

    def scratch_dotfiles(self) -> Path:
        dotfiles = self.root / "dotfiles"
        (dotfiles / "scripts").mkdir(parents=True)
        (dotfiles / "shims").mkdir()
        executable(dotfiles / "scripts" / "agent-secrets-session", 'exec "$@"')
        executable(dotfiles / "scripts" / "gh-agent-config", "echo /nonexistent")
        (dotfiles / "scripts" / "agent-spend-env.sh").write_text("agent_spend_env() { :; }\n", encoding="utf-8")
        shutil.copy(LIVE, dotfiles / "scripts" / "omp-session-live")
        return dotfiles

    def resume(self, *args: str) -> subprocess.CompletedProcess[str]:
        """agent-session launching a stub omp that prints LAUNCHED and its arguments."""
        dotfiles = self.root / "dotfiles"
        agent = self.root / "bin" / "omp"
        if not dotfiles.exists():
            self.scratch_dotfiles()
            agent.parent.mkdir()
            executable(agent, 'echo LAUNCHED "$@"')
        env = {"PATH": os.environ["PATH"], "HOME": str(self.root), "DOTFILES_DIR": str(dotfiles),
               "XDG_CONFIG_HOME": str(self.root / "config"), "OMP_SESSION_LIVE_PROC": str(self.proc),
               "AGENT_SESSION_LIVE_WAIT": "0"}
        return subprocess.run([str(SESSION), str(agent), *args], env=env, cwd=self.root,
                              capture_output=True, text=True, timeout=60, check=False)

    def assert_refused(self, result: subprocess.CompletedProcess[str], how: str) -> None:
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertNotIn("LAUNCHED", result.stdout)
        self.assertIn(how, result.stderr)

    def assert_not_live(self, target: str) -> None:
        result = self.live(target)
        self.assertEqual((result.returncode, result.stdout), (1, ""), result.stderr)

    def test_agent_session_refuses_to_resume_a_live_session_and_resumes_a_gone_one(self) -> None:
        self.omp(300, ["omp", "--resume", SID])
        self.assert_refused(self.resume("--resume", SID),
                            f"session {SID} is running already on this machine (300: its command line names")
        shutil.rmtree(self.proc / "300")
        resumed = self.resume("--resume", SID)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertIn("LAUNCHED --resume", resumed.stdout)

    def test_a_copy_of_a_live_transcript_is_not_live_and_its_fork_resumes(self) -> None:
        """omp-fork's resume: the live omp names the original path and holds it open."""
        self.omp(500, ["omp", "--resume", str(self.transcript)], holds=self.transcript)
        self.assert_not_live(str(self.copy))
        forked = self.resume("-p", "--mode", "json", "--session-dir", str(self.copy.parent), "--resume", str(self.copy))
        self.assertEqual(forked.returncode, 0, forked.stderr)
        self.assertIn(f"LAUNCHED -p --mode json --session-dir {self.copy.parent} --resume {self.copy}", forked.stdout)

    def test_the_live_transcript_is_live_by_any_spelling_of_its_path(self) -> None:
        self.omp(500, ["omp", "--resume", str(self.transcript)], holds=self.transcript)
        link = self.root / "link-to-lane"
        link.symlink_to(self.transcript.parent)
        for spelling in (self.transcript, link / self.transcript.name):
            self.assertIn(f"500\tits command line names {self.real}", self.live(str(spelling)).stdout)
        self.assert_refused(self.resume("--resume", str(link / self.transcript.name)),
                            f"is running already on this machine (500: its command line names {self.real}")

    def test_a_relative_path_on_a_command_line_is_read_from_that_omps_directory(self) -> None:
        self.omp(510, ["omp", "--resume", f"./{self.transcript.name}"], cwd=self.transcript.parent)
        self.assertIn(f"510\tits command line names {self.real}", self.live(str(self.transcript)).stdout)
        self.assert_not_live(str(self.copy))

    def test_an_omp_resumed_by_id_runs_the_file_it_holds_and_not_a_copy(self) -> None:
        self.omp(600, ["omp", "--resume", SID], holds=self.transcript)
        self.assert_refused(self.resume("--resume", SID),
                            f"session {SID} is running already on this machine (600: its command line names {SID}")
        self.assertIn(f"600\tit holds {self.real} open", self.live(str(self.transcript)).stdout)
        self.assert_not_live(str(self.copy))
        forked = self.resume("-p", "--session-dir", str(self.copy.parent), "--resume", str(self.copy))
        self.assertEqual(forked.returncode, 0, forked.stderr)
        self.assertIn("LAUNCHED -p", forked.stdout)

    def test_a_boxed_omp_resumed_by_id_is_asked_in_its_box_which_file_it_holds(self) -> None:
        """A boxed omp runs under the box's uid: the host cannot read its fd links, so the box
        is asked (rounds 213 and 214 forked 01a175f1 and 01a19225, both boxed and resumed by id)."""
        self.omp(620, ["omp", "--resume", SID])
        stubs = self.box(620, 884)
        self.assertIn(f"620\tit holds {self.real} open in its box", self.live(str(self.transcript), stubs).stdout)
        result = self.live(str(self.copy), stubs)
        self.assertEqual((result.returncode, result.stdout), (1, ""), result.stderr)
        self.assertIn(f"exec {BOX} find /proc/884/fd", (self.root / "DOCKER").read_text(encoding="utf-8"))

    def test_an_omp_resumed_by_id_whose_transcript_cannot_be_seen_may_run_any_file_of_it(self) -> None:
        """No transcript of it in sight: an omp just started has not opened one yet, and a box
        that does not answer shows none. Its command line is all there is."""
        self.omp(610, ["omp", "--resume", SID])
        self.assertIn(f"610\tits command line names {SID}", self.live(str(self.transcript)).stdout)
        self.assertIn(f"610\tits command line names {SID}", self.live(str(self.copy)).stdout)
        stubs = self.box(610, 885)
        (self.root / "BOX_GONE").touch()
        self.assertIn(f"610\tits command line names {SID}", self.live(str(self.copy), stubs).stdout)
        self.assertIn(f"exec {BOX} find /proc/885/fd", (self.root / "DOCKER").read_text(encoding="utf-8"))

    def test_the_two_resumes_of_2026_11_03_are_still_refused(self) -> None:
        """00:30Z: a second tmux server's restore ran tmux-resurrect-omp's `omp --resume <id>` for
        a session whose omp was alive. 00:37Z: omp-memory-watch's pane restarter ran
        `omp --resume <id> .` for an omp that was alive because only a child of it had died."""
        self.omp(700, ["omp", "--resume", SID], holds=self.transcript)
        self.omp(701, ["bash", "-c", "sleep 1"], comm="bash")
        self.assert_refused(self.resume("--resume", SID), "(700: its command line names")
        shutil.rmtree(self.proc / "701")
        self.assert_refused(self.resume("--resume", SID, "."), "(700: its command line names")
        self.assert_refused(self.resume("--resume", str(self.transcript)), "is running already on this machine (700: ")

    def test_agentbox_new_starts_no_box_for_a_live_session(self) -> None:
        self.omp(400, ["omp", "--resume", str(self.transcript)])
        lane = self.root / "lane"
        lane.mkdir()
        stubs = self.root / "stubs"
        stubs.mkdir()
        executable(stubs / "docker", f'echo "docker $*" >>"{self.root}/DOCKER"; exit 0')
        result = subprocess.run(
            ["bash", "-c", f'source "{AGENTBOX}"\ncmd_new --dir "{lane}" -- omp --resume "{self.transcript}"'],
            env={"HOME": str(self.root), "PATH": f"{stubs}:/usr/bin:/bin", "DOTFILES_DIR": str(DOTFILES),
                 "AGENTBOX_BOXES": str(self.root / "boxes"), "AGENTBOX_DISK": "0",
                 "OMP_SESSION_LIVE_PROC": str(self.proc), "AGENT_SESSION_LIVE_WAIT": "0"},
            capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"session {self.transcript} is running already on this machine (400:", result.stderr)
        self.assertFalse((self.root / "DOCKER").exists())


if __name__ == "__main__":
    unittest.main()
