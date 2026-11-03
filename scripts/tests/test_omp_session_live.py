#!/usr/bin/env python3
"""No resume path starts a session whose omp is live (scripts/omp-session-live).

On 2026-11-03 a second tmux server's restore resumed 51 live sessions a second time (00:30Z;
36 on the host, 15 in new boxes), and a pane restarter resumed one whose omp was alive because a
child of it had died (00:37Z). scripts/agent-session (every host or in-box omp launch) and
`agentbox new` (a box launched on the host) now ask omp-session-live and refuse, exit 3 and 1.

Technique: a fixture /proc (OMP_SESSION_LIVE_PROC) with comm, cmdline and fd links; for
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

    def omp(self, pid: int, cmdline: list[str], holds: Path | None = None, comm: str = "omp") -> None:
        d = self.proc / str(pid)
        (d / "fd").mkdir(parents=True)
        (d / "comm").write_text(comm + "\n", encoding="utf-8")
        (d / "cmdline").write_text("".join(a + "\0" for a in cmdline), encoding="utf-8")
        if holds:
            os.symlink(holds, d / "fd" / "21")

    def live(self, target: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(LIVE), target], env={**os.environ, "OMP_SESSION_LIVE_PROC": str(self.proc)},
                              capture_output=True, text=True, timeout=60, check=False)

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

    def test_agent_session_refuses_to_resume_a_live_session_and_resumes_a_gone_one(self) -> None:
        dotfiles = self.scratch_dotfiles()
        agent = self.root / "bin" / "omp"
        agent.parent.mkdir()
        executable(agent, 'echo LAUNCHED "$@"')
        env = {"PATH": os.environ["PATH"], "HOME": str(self.root), "DOTFILES_DIR": str(dotfiles),
               "XDG_CONFIG_HOME": str(self.root / "config"), "OMP_SESSION_LIVE_PROC": str(self.proc),
               "AGENT_SESSION_LIVE_WAIT": "0"}
        self.omp(300, ["omp", "--resume", SID])
        refused = subprocess.run([str(SESSION), str(agent), "--resume", SID], env=env, cwd=self.root,
                                 capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(refused.returncode, 3, refused.stdout + refused.stderr)
        self.assertNotIn("LAUNCHED", refused.stdout)
        self.assertIn(f"session {SID} is running already on this machine (300: its command line names", refused.stderr)
        shutil.rmtree(self.proc / "300")
        resumed = subprocess.run([str(SESSION), str(agent), "--resume", SID], env=env, cwd=self.root,
                                 capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertIn("LAUNCHED --resume", resumed.stdout)

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
