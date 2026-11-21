#!/usr/bin/env python3
"""Every agent session runs CodeGraph with its usage telemetry off.

The codegraph CLI (pinned in mise.toml; omp's codegraph tool runs it) records each command and sends
the counts to telemetry.getcodegraph.com at its next init, index or sync, unless DO_NOT_TRACK or
CODEGRAPH_TELEMETRY=0 is set or `codegraph telemetry off` was run. scripts/agent-session (every host
launcher) and session_env in scripts/agentbox (every box, whose ~/.codegraph is its own) export
CODEGRAPH_TELEMETRY=0. A value the caller set is kept.

Technique: as test_agent_session_gcloud_file_logging.py, agent-session launches a stand-in agent that
prints its environment or runs the pinned codegraph by its path; codegraph's own endpoint override
(CODEGRAPH_TELEMETRY_ENDPOINT) points at a listener in this test, under a HOME of the test's own
where no choice is stored. The control runs the same command without agent-session and must reach
the listener, so a quiet listener means codegraph sent nothing, not that it could not send.
session_env is sourced from scripts/agentbox and its `-e` arguments read.
"""

from __future__ import annotations

import http.server
import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "scripts" / "agent-session"
AGENTBOX = DOTFILES / "scripts" / "agentbox"
USER = "agent-session-codegraph-telemetry-test-user"


def executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def pinned_codegraph() -> str | None:
    """The codegraph mise.toml pins, not a shim; None where it is not installed."""
    found = subprocess.run(["mise", "which", "codegraph"], cwd=DOTFILES, capture_output=True, text=True, check=False) \
        if shutil.which("mise") else None
    if found is not None and found.returncode == 0 and found.stdout.strip():
        return found.stdout.strip()
    path = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep) if "shims" not in p)
    return shutil.which("codegraph", path=path)


CODEGRAPH = pinned_codegraph()


class Listener:
    """An HTTP server on 127.0.0.1 that records each POST body."""

    def __init__(self) -> None:
        self.posts: list[bytes] = []
        posts = self.posts

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 (the http.server name)
                posts.append(self.rfile.read(int(self.headers.get("content-length", "0"))))
                self.send_response(204)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}/v1/events"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class AgentSessionCodegraphTelemetry(unittest.TestCase):
    root: Path
    base: dict[str, str]

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agent-session-codegraph-telemetry-"))
        self.addCleanup(shutil.rmtree, self.root)
        dotfiles = self.root / "dotfiles"
        (dotfiles / "scripts").mkdir(parents=True)
        (dotfiles / "shims").mkdir()
        (dotfiles / "agent-spend").mkdir()
        executable(dotfiles / "scripts" / "agent-secrets-session", 'exec "$@"')
        if (DOTFILES / "scripts" / "agent-spend-env.sh").exists():
            shutil.copy(DOTFILES / "scripts" / "agent-spend-env.sh", dotfiles / "scripts" / "agent-spend-env.sh")
        nvme = self.root / "nvme" / USER
        nvme.mkdir(parents=True)
        self.base = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "USER": USER,
            "DOTFILES_DIR": str(dotfiles),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_CACHE_HOME": str(self.root / "cache"),
            "AGENT_SESSION_NVME_HOME": str(nvme),
        }
        for name in ("CODEGRAPH_TELEMETRY", "DO_NOT_TRACK", "CODEGRAPH_TELEMETRY_ENDPOINT"):
            self.base.pop(name, None)

    def launch(self, body: str, env: dict[str, str] | None = None) -> str:
        agent = self.root / "omp"
        executable(agent, body)
        result = subprocess.run([str(SCRIPT), str(agent)], env={**self.base, **(env or {})}, cwd=self.root,
                                capture_output=True, text=True, timeout=300, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_the_session_exports_telemetry_off(self) -> None:
        out = self.launch('printf "TELEMETRY=%s\\n" "${CODEGRAPH_TELEMETRY-<unset>}"')
        self.assertIn("TELEMETRY=0", out.splitlines())

    def test_a_value_the_caller_set_is_kept(self) -> None:
        out = self.launch('printf "TELEMETRY=%s\\n" "${CODEGRAPH_TELEMETRY-<unset>}"', {"CODEGRAPH_TELEMETRY": "1"})
        self.assertIn("TELEMETRY=1", out.splitlines())

    @unittest.skipUnless(CODEGRAPH, "the pinned codegraph is not installed")
    def test_a_codegraph_index_in_a_session_sends_nothing_and_without_it_sends_its_event(self) -> None:
        assert CODEGRAPH is not None
        listener = Listener()
        self.addCleanup(listener.close)
        project = self.root / "project"
        project.mkdir()
        (project / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        command = f"{shlex.quote(CODEGRAPH)} init {shlex.quote(str(project))}"
        endpoint = {"CODEGRAPH_TELEMETRY_ENDPOINT": listener.endpoint}

        self.launch(command, endpoint)
        self.assertEqual(listener.posts, [], "codegraph sent telemetry from an agent session")
        self.assertFalse((self.root / ".codegraph").exists(), "codegraph recorded usage in an agent session")

        # The control: the same index, another HOME with no stored choice, no agent-session.
        shutil.rmtree(project / ".codegraph", ignore_errors=True)
        other_home = self.root / "control-home"
        other_home.mkdir()
        result = subprocess.run(["bash", "-c", command], env={**self.base, **endpoint, "HOME": str(other_home)},
                                capture_output=True, text=True, timeout=300, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = [event["event"] for body in listener.posts for event in json.loads(body)["events"]]
        self.assertIn("index", events, f"the control sent nothing, so the check above could not fail: {result.stderr}")


class AgentboxSessionEnvCodegraphTelemetry(unittest.TestCase):
    def run_session_env(self, **env: str) -> list[str]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        base = {**os.environ, "AGENTBOX_IDENTITY_FILE": str(root / "not-a-box"), "AGENTBOX_BOX": "",
                "AGENTBOX_HOST": "sami-devbox", "AGENTBOX_BOXES": str(root / "boxes"),
                "AGENTBOX_DISKS": str(root / "disks"), "AGENTBOX_CACHE_ROOT": str(root / "box-cache"),
                "XDG_CONFIG_HOME": str(root / "config"), "DOTFILES_DIR": str(AGENTBOX.parents[1])}
        base.pop("CODEGRAPH_TELEMETRY", None)
        body = ("NETWORK_GATEWAY=172.31.0.1; forward_channels() { :; }; loopback_services() { :; }; "
                "session_env agentbox-telemetrytest")
        result = subprocess.run(["bash", "-c", f"source {shlex.quote(str(AGENTBOX))}\n{body}"],
                                env={**base, **env}, text=True, capture_output=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def test_a_box_session_gets_telemetry_off(self) -> None:
        self.assertIn("CODEGRAPH_TELEMETRY=0", self.run_session_env())

    def test_the_launchers_own_value_is_kept(self) -> None:
        args = self.run_session_env(CODEGRAPH_TELEMETRY="1")
        self.assertIn("CODEGRAPH_TELEMETRY=1", args)
        self.assertNotIn("CODEGRAPH_TELEMETRY=0", args)


if __name__ == "__main__":
    unittest.main()
