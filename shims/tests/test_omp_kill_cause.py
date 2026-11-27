#!/usr/bin/env python3
"""shims/omp names a ceiling kill only when the session's scope recorded an OOM kill.

Until 2026-11-22 every exit 137 was reported as "killed at its memory ceiling". At 21:05Z two sessions whose
scopes peaked at 40.6G and 5.1G of their 109G ceiling were told so, after another session wrote cgroup.kill
on their scopes, and a reader would have raised the ceiling for nothing.

Technique: run the shim's own child_scope and scope_oom functions, taken from shims/omp, against real
systemd user scopes: one whose main process the kernel OOM-kills at a 64M MemoryMax (with and without a
process that keeps the scope alive, so both memory.events and the journal are read), and one killed by a
SIGKILL from outside.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIM = Path(__file__).resolve().parents[1] / "omp"
EATER = "import time\ntime.sleep(3)\nx = bytearray()\nwhile True: x += bytearray(8*1024*1024)\n"
EATER_WITH_CHILD = "import os, time\nif os.fork() == 0:\n    time.sleep(20); os._exit(0)\n" + EATER.split("\n", 1)[1]


def functions() -> str:
    text = SHIM.read_text(encoding="utf-8")
    out = []
    for name in ("child_scope", "scope_oom"):
        m = re.search(rf"^    {name}\(\) \{{\n.*?^    \}}\n", text, re.S | re.M)
        assert m, f"shims/omp defines no {name}()"
        out.append(m.group(0))
    return "".join(out)


def user_scopes_work() -> bool:
    if not shutil.which("systemd-run"):
        return False
    return subprocess.run(["systemd-run", "--user", "--scope", "-q", "--", "true"], capture_output=True).returncode == 0


@unittest.skipUnless(user_scopes_work(), "needs systemd user scopes")
class KillCause(unittest.TestCase):
    def verdict(self, program: str, kill_from_outside: bool) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            fns = Path(tmp) / "fns.sh"
            fns.write_text(functions(), encoding="utf-8")
            script = (
                'set -euo pipefail; source "$1"\n'
                "systemd-run --user --scope -q -p MemoryMax=64M -p MemorySwapMax=0 -p OOMPolicy=continue "
                '-- python3 -c "$2" &\n'
                'pid=$! since=$(date +%s); sc=$(child_scope "$pid")\n'
                + ('sleep 1; kill -9 "$pid"\n' if kill_from_outside else "")
                + 'st=0; wait "$pid" 2>/dev/null || st=$?\n'
                'echo "$st $(scope_oom "$sc" "$since")"\n'
            )
            run = subprocess.run(["bash", "-c", script, "bash", str(fns), program],
                                 capture_output=True, text=True, timeout=120)
            status, verdict = run.stdout.split()
            return int(status), verdict

    def test_an_oom_kill_at_the_ceiling_is_named_so(self) -> None:
        self.assertEqual(self.verdict(EATER, False), (137, "oom"))

    def test_an_oom_kill_is_read_while_another_process_keeps_the_scope(self) -> None:
        self.assertEqual(self.verdict(EATER_WITH_CHILD, False), (137, "oom"))

    def test_a_sigkill_from_outside_is_not_a_ceiling_kill(self) -> None:
        self.assertEqual(self.verdict("import time; time.sleep(600)", True), (137, "none"))


if __name__ == "__main__":
    unittest.main()
