#!/usr/bin/env python3
"""scripts/agent-session refuses to resume a session `agentbox move --to` moved off this machine.

One session id must never run on two machines at once (dispatch://INFRA-993, R4). The move leaves
`agentbox-moved` in the session's own directory (its transcript path without `.jsonl`) and the same
file as ~/.omp/agentbox-moved/<id>; every omp launcher here comes through agent-session, which
refuses a resume of that session by path, by id or by a prefix of it, exit 3. A marker counts only
on the machine it names as `from=`, so one a later bulk copy carries to the target is inert there.

Technique, as test_agent_session_spend.py: a scratch DOTFILES_DIR whose agent-secrets-session execs
its argv, and an agent named `omp` that prints LAUNCHED.
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
SCRIPT = DOTFILES / "scripts" / "agent-session"
SID = "01a19700-0000-7000-8000-00000000d001"


def executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class AgentSessionMovedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agent-session-moved-"))
        self.addCleanup(shutil.rmtree, self.root)
        dotfiles = self.root / "dotfiles"
        (dotfiles / "scripts").mkdir(parents=True)
        (dotfiles / "shims").mkdir()
        (dotfiles / "agent-spend").mkdir()
        executable(dotfiles / "scripts" / "agent-secrets-session", 'exec "$@"')
        executable(dotfiles / "scripts" / "gh-agent-config", "echo /nonexistent")
        (dotfiles / "scripts" / "agent-spend-env.sh").write_text("agent_spend_env() { :; }\n", encoding="utf-8")
        self.agent = self.root / "bin" / "omp"
        self.agent.parent.mkdir()
        executable(self.agent, 'echo LAUNCHED "$@"')
        self.transcript = self.root / ".omp/agent/sessions/-lane" / f"2026-11-02T10-00-00-000Z_{SID}.jsonl"
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "DOTFILES_DIR": str(dotfiles),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "AGENTBOX_HOST": "oldbox",
        }

    def mark(self, origin: str = "oldbox") -> None:
        body = f"from={origin}\nto=sami-devbox\ntime=2026-11-02T12:00:00Z\nsession={SID}\ntranscript={self.transcript}\n"
        own = self.transcript.with_suffix("")
        own.mkdir(parents=True)
        (own / "agentbox-moved").write_text(body, encoding="utf-8")
        index = self.root / ".omp/agentbox-moved"
        index.mkdir(parents=True)
        (index / SID).write_text(body, encoding="utf-8")

    def launch(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(SCRIPT), str(self.agent), *args], env=self.env, cwd=self.root,
                              capture_output=True, text=True, timeout=60, check=False)

    def test_a_moved_session_is_refused_by_path_by_id_and_by_prefix(self) -> None:
        self.mark()
        for args in (("--resume", str(self.transcript)), (f"--resume={self.transcript}",), ("-r", SID),
                     ("--session", SID[:13])):
            result = self.launch(*args)
            self.assertEqual(result.returncode, 3, (args, result.stdout, result.stderr))
            self.assertNotIn("LAUNCHED", result.stdout)
            self.assertIn("this session moved to sami-devbox at 2026-11-02T12:00:00Z", result.stderr)

    def test_the_marker_counts_only_on_the_machine_it_was_moved_from(self) -> None:
        self.mark(origin="sami-leanperf")
        result = self.launch("--resume", str(self.transcript))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LAUNCHED --resume", result.stdout)

    def test_another_session_a_new_one_and_a_prompt_naming_the_id_start(self) -> None:
        self.mark()
        for args in (("--resume", "01a19700-0000-7000-8000-00000000d002"), ("--model", "x"), ("--", "--resume", SID)):
            result = self.launch(*args)
            self.assertEqual(result.returncode, 0, (args, result.stderr))
            self.assertIn("LAUNCHED", result.stdout)

    def test_a_session_whose_move_was_rolled_back_resumes(self) -> None:
        # The rollback removes both files; nothing else is needed for the session to resume here.
        result = self.launch("--resume", str(self.transcript))
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
