#!/usr/bin/env python3
"""`agentbox` runs no SQL and has no code for omp's session database (dispatch://INFRA-1720, R6).

Sami, dispatch://INFRA-1720/ask/16322992: agentbox touches no SQL. Before this change it relayed
the host's database proxy port 5432 into every box, mounted the database connection file's
directory `/run/user/<uid>/omp-session-sql` into every box, copied `OMP_SESSION_STORAGE` and
`OMP_SESSION_SQL_DSN_FILE` into a moved box, resumed a session by a path the database's import
sweep recorded (`deleted.tsv`), and read omp's `agent.db` with `sqlite3` before a move to another
machine.

Technique, as test_agentbox_no_host_writes.py: source scripts/agentbox with the host tools
stubbed, run cmd_new with the host steps replaced but mount_args and session_env real, and read
the arguments of the `docker run` it makes.
"""

from __future__ import annotations

import re
import socket
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"
GATEWAY = "172.31.0.1"
SID = "01a1f00d-0000-7000-8000-00000000a001"

# Everything that needs a real host, EXCEPT mount_args and session_env, which are under test.
NO_HOST = (
    "image_tag() { echo agentbox:test; }; build_image() { :; }; ensure_forwarders() { :; }\n"
    f"ensure_network() {{ NETWORK_GATEWAY={GATEWAY} NETWORK_SUBNET=172.31.0.0/16; }}\n"
    "clone_checkout() { :; }; close_box() { :; }; box_cache_mounts() { :; }\n"
    "forward_channels() { FORWARD_CHANNELS=(); }\n"
    "collect_start() { :; }\n"
)


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class NoSqlInAgentbox(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        (self.home / ".config").mkdir(parents=True)
        (self.home / ".omp" / "agent" / "sessions").mkdir(parents=True)
        (self.root / "src" / "repo1").mkdir(parents=True)
        (self.root / "durable").mkdir()
        self.stubs = self.root / "bin"
        self.run_args = self.root / "run-args"
        write_stub(self.stubs, "docker", r'[[ "$1" == run ]] && { shift; printf "%s\n" "$@" >"$STUB_RUN_ARGS"; }; exit 0')
        for name in ("git", "sleep"):
            write_stub(self.stubs, name, "exit 0")
        write_stub(self.stubs, "curl", "exit 7")
        write_stub(self.stubs, "agent-secrets", "echo none; exit 1")
        write_stub(self.stubs, "sudo", "exit 0")
        write_stub(self.stubs, "df", "printf 'Avail\\n%s\\n' 1099511627776")
        write_stub(self.stubs, "mountpoint", "exit 1")
        # The host listens on the database proxy's port and on MLflow's: only MLflow's is relayed.
        write_stub(self.stubs, "ss", "printf 'LISTEN 0 4096 127.0.0.1:5432 0.0.0.0:*\\nLISTEN 0 4096 127.0.0.1:5005 0.0.0.0:*\\n'")
        self.sqlite = self.root / "SQLITE"
        write_stub(self.stubs, "sqlite3", f'echo "$*" >>"{self.sqlite}"; exit 0')
        dot = self.root / "dotfiles"
        write_stub(dot / "scripts", "envoy-nats-seed", "exit 1")
        # The real snapshot, which writes the box's private Docker config and its (empty) bind
        # list into the fixture's box tree: mount_args reads that list, and a stub that writes
        # nothing made this test depend on the host's own ~/.docker.
        (dot / "scripts" / "agentbox-docker-snapshot").symlink_to(DOTFILES / "scripts" / "agentbox-docker-snapshot")
        write_stub(dot / "scripts", "on-google-vm", "exit 1")
        (dot / "agent-secrets").symlink_to(DOTFILES / "agent-secrets")
        (dot / "agentbox").symlink_to(DOTFILES / "agentbox")
        # mount_args refuses a box with no secretsd socket to bind. The fixture's own socket, so the
        # test does not depend on the host running secretsd (a Legion pod does not).
        self.secretsd = self.root / "secretsd.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.secretsd))
        self.addCleanup(listener.close)
        self.env = {
            "AGENTBOX_IDENTITY_FILE": str(self.root / "not-a-box"),
            "AGENTBOX_BOX": "",
            "HOME": str(self.home),
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "DOTFILES_DIR": str(dot),
            "AGENTBOX_SRC": str(self.root / "src"),
            "AGENTBOX_DURABLE": str(self.root / "durable"),
            "AGENTBOX_RUN_BASE": str(self.root / "run"),
            "AGENTBOX_BOXES": str(self.root / "boxes"),
            "AGENTBOX_DISKS": str(self.root / "disks"),
            "AGENTBOX_DISK": "0",
            "AGENT_SECRETS_URL": "https://broker.example",
            "STUB_RUN_ARGS": str(self.run_args),
        }

    def run_bash(self, body: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", "-c", f'source "{AGENTBOX}"\n{body}'], capture_output=True, text=True,
                              check=False, timeout=120, env={**self.env, **(env or {})})

    def test_no_sql_in_agentbox(self) -> None:
        got = self.run_bash(f"SECRETSD_SOCK={self.secretsd}\n{NO_HOST}cmd_new repo1 -- omp")
        self.assertEqual(got.returncode, 0, got.stderr[-3000:])
        args = self.run_args.read_text(encoding="utf-8").splitlines()
        self.assertFalse([a for a in args if "omp-session-sql" in a], "no mount of the database connection file")
        self.assertFalse([a for a in args if a.startswith(("OMP_SESSION_STORAGE", "OMP_SESSION_SQL"))],
                         "no session-store setting")
        channels = [a for a in args if a.startswith("AGENTBOX_FORWARD_CHANNELS=")]
        self.assertEqual(len(channels), 1, args)
        self.assertIn("127.0.0.1:5005", channels[0], "the control: a loopback service is relayed")
        self.assertNotIn(":5432", channels[0], "the database proxy is not relayed")

    def test_a_move_copies_no_database_setting_and_reads_no_sweep_record(self) -> None:
        proc = self.root / "proc" / "1101"
        (proc / "fd").mkdir(parents=True)
        (proc / "environ").write_bytes(b"OMP_PROFILE=pbc\0OMP_SESSION_STORAGE=sql\0OMP_SESSION_SQL_DSN_FILE=/run/dsn\0")
        got = self.run_bash("move_session_env 1101", {"AGENTBOX_PROC": str(self.root / "proc")})
        self.assertEqual(got.stdout, "-e\nOMP_PROFILE=pbc\n", got.stderr)
        # A transcript the import sweep took is a session no move can resume: its record is not read.
        record = self.root / "deleted.tsv"
        swept = self.home / ".omp/agent/sessions/-lane" / f"2026-10-25T02-17-36-102Z_{SID}.jsonl"
        record.write_text(f"{swept}\t100\tabc\t\t2026-11-02T08:00:00Z\n", encoding="utf-8")
        (proc / "cmdline").write_bytes(f"omp\0--resume\0{SID}\0".encode())
        got = self.run_bash(f"move_transcript 1101 {SID}",
                            {"AGENTBOX_PROC": str(self.root / "proc"), "AGENTBOX_SWEEP_RECORD": str(record)})
        self.assertEqual((got.returncode, got.stdout), (1, ""), "the sweep's record is not read")

    def test_no_line_of_agentbox_runs_an_sql_client(self) -> None:
        """omp's own agent.db was read with sqlite3 to warn of MCP logins a target machine lacks
        (move_to_mcp_logins). No line runs sqlite3 or psql now; the one mention left is the box
        doctor's list of tools a box has. Any code line that names either counts, however it calls
        it: a call through a variable (`"${SQLITE:-sqlite3}" …`) passed the older match, which
        looked for the name followed by a space, a `)` or after a quote."""
        text = AGENTBOX.read_text(encoding="utf-8").splitlines()
        runs = [line for line in text
                if re.search(r"sqlite|\bpsql\b", line, re.IGNORECASE)
                and not line.lstrip().startswith("#")
                and not line.lstrip().startswith("for t in ")]
        self.assertEqual(runs, [], "scripts/agentbox runs no SQL client")
        self.assertEqual(self.run_bash("declare -F move_to_mcp_logins move_to_warn_mcp_logins").stdout, "")

    def test_agent_session_exports_no_fallback_ledger(self) -> None:
        """scripts/agent-session pointed every session, in a box too, at a fallback ledger of omp's
        database store on the durable disk, which omp at the pinned build does not read and which
        made a directory omp-session-sql there; scripts/omp-session-fallbacks read it."""
        dot = self.root / "dotfiles-session"
        (dot / "scripts").mkdir(parents=True)
        (dot / "shims").mkdir()
        write_stub(dot / "scripts", "agent-secrets-session", 'exec "$@"')
        if (DOTFILES / "scripts" / "agent-spend-env.sh").exists():
            (dot / "scripts" / "agent-spend-env.sh").write_bytes((DOTFILES / "scripts" / "agent-spend-env.sh").read_bytes())
        agent = self.root / "omp"
        agent.write_text('#!/bin/bash\nprintf "LEDGER=%s\\n" "${OMP_SESSION_SQL_FALLBACK_LEDGER-unset}"\n', encoding="utf-8")
        agent.chmod(0o755)
        durable = self.root / "durable"
        result = subprocess.run(
            [str(DOTFILES / "scripts" / "agent-session"), str(agent)],
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.root), "USER": "agent-session-no-sql-test-user",
                 "DOTFILES_DIR": str(dot), "XDG_CONFIG_HOME": str(self.root / "config"),
                 "XDG_CACHE_HOME": str(self.root / "cache"), "AGENTBOX_DURABLE": str(durable),
                 "AGENT_SESSION_NVME_HOME": str(self.root / "nvme")},
            cwd=self.root, capture_output=True, text=True, check=False, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LEDGER=unset", result.stdout, "the control: the agent ran and printed")
        self.assertFalse((durable / "omp-session-sql").exists())
        self.assertFalse((DOTFILES / "scripts" / "omp-session-fallbacks").exists())


if __name__ == "__main__":
    unittest.main()
