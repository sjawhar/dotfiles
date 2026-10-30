#!/usr/bin/env python3
"""scripts/agent-session's model-spend tags (dispatch://INFRA-165).

The project is the first of: an explicit THEOREM_PROJECT; the resumed session's own file under
~/.config/agent-spend/sessions; the parent session's file (OMP_SESSION_ID); the inherited
theorem.project, unless it is the machine default every shell gets; agent-spend/projects; the
machine default; `shared`. Old project names become their COS stream. A caller already on this
machine's collector is re-tagged, its inherited tags replaced; a caller on a different collector
keeps everything. omp gets the token in its traces header only, and the machine's lane. With no
token, the launch reruns the cached setup first, at most once in 10 minutes.

Technique: a scratch DOTFILES_DIR holding the real agent-spend/projects and a test person, and an
agent-secrets-session stub that execs its argv; a scratch XDG_CONFIG_HOME holding the endpoint, the
token and session files (as agent-infra's agent-spend-setup writes them); the agent is a script
that prints its environment.
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
ENDPOINT = "https://collector.example/otlp"
PARENT = "01a13dca-e979-7264-af32-f654ef57a6f5"
CHILD = "01a179b6-0b94-75ec-ac3b-2a5b132018c0"


def executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class AgentSessionSpendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agent-session-spend-"))
        self.addCleanup(shutil.rmtree, self.root)
        dotfiles = self.root / "dotfiles"
        (dotfiles / "agent-spend").mkdir(parents=True)
        (dotfiles / "scripts").mkdir()
        (dotfiles / "shims").mkdir()
        shutil.copy(DOTFILES / "agent-spend" / "projects", dotfiles / "agent-spend" / "projects")
        (dotfiles / "agent-spend" / "person").write_text("sami@theorem.dev\n")
        executable(dotfiles / "scripts" / "agent-secrets-session", 'exec "$@"')
        shutil.copy(DOTFILES / "scripts" / "agent-spend-env.sh", dotfiles / "scripts" / "agent-spend-env.sh")
        self.config = self.root / "config"
        (self.config / "agent-spend").mkdir(parents=True)
        (self.config / "agent-spend" / "endpoint").write_text(f"{ENDPOINT}\n")
        (self.config / "agent-spend" / "token").write_text("t0ken\n")
        self.sessions = self.config / "agent-spend" / "sessions"
        self.agent = self.root / "omp"
        executable(
            self.agent,
            'printf "%s\\n" "THEOREM_PROJECT=${THEOREM_PROJECT-}" "THEOREM_SUBPROJECT=${THEOREM_SUBPROJECT-}" '
            '"OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT-}" '
            '"OTEL_RESOURCE_ATTRIBUTES=${OTEL_RESOURCE_ATTRIBUTES-}" '
            '"OTEL_LOGS_EXPORTER=${OTEL_LOGS_EXPORTER-<unset>}" "OTEL_TRACES_EXPORTER=${OTEL_TRACES_EXPORTER-<unset>}" '
            '"CLAUDE_CODE_ENABLE_TELEMETRY=${CLAUDE_CODE_ENABLE_TELEMETRY-<unset>}" '
            '"OTEL_EXPORTER_OTLP_HEADERS=${OTEL_EXPORTER_OTLP_HEADERS-<unset>}" '
            '"OTEL_EXPORTER_OTLP_TRACES_HEADERS=${OTEL_EXPORTER_OTLP_TRACES_HEADERS-<unset>}"',
        )
        self.cwd = self.root / "work"
        self.cwd.mkdir()
        self.base = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "DOTFILES_DIR": str(dotfiles),
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_CACHE_HOME": str(self.root / "cache"),
        }

    def session_file(self, session: str, line: str) -> None:
        self.sessions.mkdir(mode=0o700, exist_ok=True)
        (self.sessions / session).write_text(f"{line}\n")

    def run_agent(self, env: dict[str, str], *args: str, cwd: Path | None = None) -> dict[str, str]:
        result = subprocess.run(
            [str(SCRIPT), str(self.agent), *args],
            env={**self.base, **env},
            cwd=cwd or self.cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return dict(line.split("=", 1) for line in result.stdout.splitlines())

    def test_explicit_project_tags_a_new_session(self) -> None:
        out = self.run_agent({"THEOREM_PROJECT": "rocq-speed"})
        self.assertEqual(out["THEOREM_PROJECT"], "rocq-speed")
        self.assertEqual(out["OTEL_EXPORTER_OTLP_ENDPOINT"], ENDPOINT)
        self.assertEqual(out["OTEL_RESOURCE_ATTRIBUTES"], "theorem.project=rocq-speed,theorem.person=sami@theorem.dev")

    def test_child_on_this_collector_takes_its_parent_sessions_project(self) -> None:
        self.session_file(PARENT, "agent-process")
        out = self.run_agent(
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": ENDPOINT,
                "OTEL_RESOURCE_ATTRIBUTES": "service.name=x,theorem.project=binary-equivalence,theorem.person=sami@theorem.dev",
                "OMP_SESSION_ID": PARENT,
            }
        )
        self.assertEqual(out["THEOREM_PROJECT"], "agent-process")
        self.assertEqual(
            out["OTEL_RESOURCE_ATTRIBUTES"],
            "service.name=x,theorem.project=agent-process,theorem.person=sami@theorem.dev",
        )

    def test_child_with_no_parent_file_maps_the_inherited_old_tag(self) -> None:
        out = self.run_agent(
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": ENDPOINT,
                "OTEL_RESOURCE_ATTRIBUTES": "theorem.project=binary-equivalence,theorem.subproject=pipe",
                "OMP_SESSION_ID": PARENT,
            }
        )
        self.assertEqual(out["THEOREM_PROJECT"], "task-making")
        self.assertEqual(out["THEOREM_SUBPROJECT"], "pipe")
        self.assertEqual(
            out["OTEL_RESOURCE_ATTRIBUTES"],
            "theorem.project=task-making,theorem.subproject=pipe,theorem.person=sami@theorem.dev",
        )

    def test_a_different_collector_keeps_its_tags(self) -> None:
        attrs = "theorem.project=oss-verify,theorem.person=lane"
        self.session_file(PARENT, "agent-process")
        out = self.run_agent(
            {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://lane:4318", "OTEL_RESOURCE_ATTRIBUTES": attrs, "OMP_SESSION_ID": PARENT}
        )
        self.assertEqual(out["OTEL_EXPORTER_OTLP_ENDPOINT"], "http://lane:4318")
        self.assertEqual(out["OTEL_RESOURCE_ATTRIBUTES"], attrs)
        self.assertEqual(out["THEOREM_PROJECT"], "")

    def test_a_resumed_sessions_own_file_beats_its_parents(self) -> None:
        self.session_file(PARENT, "agent-process")
        self.session_file(CHILD, "verified-grader lane")
        out = self.run_agent({"OMP_SESSION_ID": PARENT}, "--resume", CHILD[:13])
        self.assertEqual((out["THEOREM_PROJECT"], out["THEOREM_SUBPROJECT"]), ("verified-grader", "lane"))

    def test_an_old_name_in_a_session_file_becomes_its_stream(self) -> None:
        self.session_file(CHILD, "linux-verify")
        self.assertEqual(self.run_agent({}, "-r", CHILD)["THEOREM_PROJECT"], "verified-linux")

    def test_an_explicit_project_on_resume_is_recorded_under_its_stream_name(self) -> None:
        self.run_agent({"THEOREM_PROJECT": "agent-infra"}, f"--resume=/s/2026-10-26T19-35-36-084Z_{CHILD}.jsonl")
        self.assertEqual((self.sessions / CHILD).read_text(), "legion\n")
        self.assertEqual((self.sessions / CHILD).stat().st_mode & 0o777, 0o600)

    def test_an_inherited_theorem_project_is_not_explicit(self) -> None:
        self.session_file(CHILD, "verified-grader")
        out = self.run_agent(
            {
                "THEOREM_PROJECT": "agent-process",
                "OTEL_EXPORTER_OTLP_ENDPOINT": ENDPOINT,
                "OTEL_RESOURCE_ATTRIBUTES": "theorem.project=agent-process",
            },
            "--resume",
            CHILD,
        )
        self.assertEqual(out["THEOREM_PROJECT"], "verified-grader")
        self.assertEqual((self.sessions / CHILD).read_text(), "verified-grader\n")

    def test_an_omp_started_by_claude_code_turns_its_spans_back_on(self) -> None:
        out = self.run_agent(
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": ENDPOINT,
                "OTEL_LOGS_EXPORTER": "otlp",
                "OTEL_TRACES_EXPORTER": "none",
                "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
            }
        )
        self.assertEqual(out["OTEL_LOGS_EXPORTER"], "none")
        self.assertEqual(out["OTEL_TRACES_EXPORTER"], "<unset>")
        self.assertEqual(out["CLAUDE_CODE_ENABLE_TELEMETRY"], "<unset>")

    def test_the_working_directory_glob_then_shared(self) -> None:
        taiga = self.root / "src" / "binary-equivalence-taiga"
        taiga.mkdir(parents=True)
        self.assertEqual(self.run_agent({}, cwd=taiga)["THEOREM_PROJECT"], "task-making")
        self.assertEqual(self.run_agent({})["THEOREM_PROJECT"], "shared")

    def test_omp_gets_the_token_in_its_traces_header_only(self) -> None:
        # The generic header reaches every exporter of every program omp starts; Codex's metrics exporter sends it to
        # OpenAI. An inherited one is dropped.
        out = self.run_agent({"OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer old"})
        self.assertEqual(out["OTEL_EXPORTER_OTLP_TRACES_HEADERS"], "Authorization=Bearer t0ken")
        self.assertEqual(out["OTEL_EXPORTER_OTLP_HEADERS"], "<unset>")

    def test_the_machine_default_in_every_shell_is_not_an_inherited_project(self) -> None:
        # The setup's env.sh puts the machine's default project in every shell; the working directory still decides.
        (self.config / "agent-spend" / "default-project").write_text("legion\n")
        taiga = self.root / "src" / "binary-equivalence-taiga"
        taiga.mkdir(parents=True)
        shell = {"OTEL_RESOURCE_ATTRIBUTES": "theorem.project=legion,theorem.person=sami@theorem.dev"}
        self.assertEqual(self.run_agent(shell, cwd=taiga)["THEOREM_PROJECT"], "task-making")
        self.assertEqual(self.run_agent(shell)["THEOREM_PROJECT"], "legion")
        other = {"OTEL_RESOURCE_ATTRIBUTES": "theorem.project=rocq-speed"}
        self.assertEqual(self.run_agent(other, cwd=taiga)["THEOREM_PROJECT"], "rocq-speed")

    def test_the_machines_lane_unless_one_is_inherited(self) -> None:
        (self.config / "agent-spend" / "lane").write_text("box-1\n")
        out = self.run_agent({"THEOREM_PROJECT": "rocq-speed"})
        self.assertEqual(
            out["OTEL_RESOURCE_ATTRIBUTES"], "theorem.project=rocq-speed,theorem.person=sami@theorem.dev,theorem.lane=box-1"
        )
        out = self.run_agent({"OTEL_EXPORTER_OTLP_ENDPOINT": ENDPOINT, "OTEL_RESOURCE_ATTRIBUTES": "theorem.lane=l2,theorem.project=omp"})
        self.assertEqual(out["OTEL_RESOURCE_ATTRIBUTES"], "theorem.lane=l2,theorem.project=omp,theorem.person=sami@theorem.dev")

    def test_no_token_reruns_the_cached_setup_at_most_once_in_ten_minutes(self) -> None:
        (self.config / "agent-spend" / "token").unlink()
        cache = self.root / "cache" / "agent-spend"
        cache.mkdir(parents=True)
        (cache / "args").write_text("--person\nsami@theorem.dev\n--lane\nbox 1\n")
        (cache / "agent-spend-setup").write_text(
            "import os, sys\n"
            "d = os.path.join(os.environ['XDG_CONFIG_HOME'], 'agent-spend')\n"
            "open(os.path.join(os.environ['XDG_CACHE_HOME'], 'agent-spend', 'ran'), 'a').write("
            "repr(sys.argv[1:] + [os.environ.get('CLAUDE_CONFIG_DIR')]) + '\\n')\n"
            "open(os.path.join(d, 'token'), 'w').write('t0ken\\n')\n"
        )
        out = self.run_agent({"CLAUDE_CONFIG_DIR": str(self.root / "tracked-claude")})
        self.assertEqual(out["OTEL_EXPORTER_OTLP_ENDPOINT"], ENDPOINT)
        self.assertEqual(out["OTEL_EXPORTER_OTLP_TRACES_HEADERS"], "Authorization=Bearer t0ken")
        # The setup never sees CLAUDE_CONFIG_DIR, so it cannot write a tracked settings.json.
        self.assertEqual((cache / "ran").read_text(), "['--person', 'sami@theorem.dev', '--lane', 'box 1', '--no-test', None]\n")
        (self.config / "agent-spend" / "token").unlink()
        out = self.run_agent({})
        self.assertEqual(out["OTEL_EXPORTER_OTLP_ENDPOINT"], "")  # no token, no export, and no second run yet
        self.assertEqual(len((cache / "ran").read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
