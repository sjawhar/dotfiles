#!/usr/bin/env python3
"""shims/claude and shims/codex: the model-spend environment (scripts/agent-spend-env.sh), then the real binary.

A claude a script or a session's bash tool starts by name inherits its omp parent's collector and
OTEL_LOGS_EXPORTER=none; through the shim it gets Claude Code's telemetry switches and the project the parent
session's file names, and the token in its logs header only (its settings on these machines are the tracked
.claude/settings.json, which holds no helper); it loses a generic, metrics or traces header it inherited, and Codex
gets none (its token is in its config.toml; Codex's built-in metrics exporter would send a generic or metrics
header to OpenAI). A caller on a different collector keeps everything. The shim takes the first real binary past
its own PATH entry, never mise's shims dir (which hands the call back to PATH) or another shims/ copy, and hands it
its arguments and PATH unchanged.

Technique: the real shims, run with DOTFILES_DIR at a scratch copy holding scripts/agent-spend-env.sh and a test
person, XDG_CONFIG_HOME holding the endpoint and token as agent-infra's agent-spend-setup writes them; PATH is the
shims dir, a scratch mise shims dir whose claude and codex would loop back to PATH, and a dir of stand-in binaries
that print their argv, PATH and spend environment.
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
SHIMS = DOTFILES / "shims"
ENDPOINT = "https://collector.example/otlp"
PARENT = "01a14b36-227b-7141-acdb-b5397251a0b1"
KEYS = (
    "THEOREM_PROJECT",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
    "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_RESOURCE_ATTRIBUTES",
    "OTEL_LOGS_EXPORTER",
    "OTEL_TRACES_EXPORTER",
    "OTEL_METRICS_EXPORTER",
    "CLAUDE_CODE_ENABLE_TELEMETRY",
)


def executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class AgentSpendShimTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agent-spend-shims-"))
        self.addCleanup(shutil.rmtree, self.root)
        dotfiles = self.root / "dotfiles"
        (dotfiles / "agent-spend").mkdir(parents=True)
        (dotfiles / "scripts").mkdir()
        shutil.copy(DOTFILES / "scripts" / "agent-spend-env.sh", dotfiles / "scripts" / "agent-spend-env.sh")
        (dotfiles / "agent-spend" / "person").write_text("sami@theorem.dev\n")
        self.config = self.root / "config"
        (self.config / "agent-spend" / "sessions").mkdir(parents=True)
        (self.config / "agent-spend" / "token").write_text("t0ken\n")
        (self.config / "agent-spend" / "endpoint").write_text(f"{ENDPOINT}\n")
        (self.config / "agent-spend" / "sessions" / PARENT).write_text("oss-verify\n")
        mise = self.root / "mise"
        (mise / "shims").mkdir(parents=True)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        report = 'printf "%s\\n" "NAME=$(basename "$0")" "ARGS=$*" "PATH=$PATH"' + "".join(
            f' "{k}=${{{k}-<unset>}}"' for k in KEYS
        )
        for name in ("claude", "codex"):
            executable(mise / "shims" / name, f'exec {name} "$@"')
            executable(bin_dir / name, report)
        self.path = f"{SHIMS}:{mise / 'shims'}:{bin_dir}:/usr/bin:/bin"
        self.base = {
            "PATH": self.path,
            "HOME": str(self.root),
            "DOTFILES_DIR": str(dotfiles),
            "XDG_CONFIG_HOME": str(self.config),
            "MISE_DATA_DIR": str(mise),
        }

    def run_shim(self, name: str, env: dict[str, str], *args: str) -> dict[str, str]:
        result = subprocess.run(
            [name, *args],
            env={**self.base, **env},
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return dict(line.split("=", 1) for line in result.stdout.splitlines())

    def test_claude_under_an_omp_session_reports_under_its_parents_project(self) -> None:
        out = self.run_shim(
            "claude",
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": ENDPOINT,
                "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer t0ken",
                "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "Authorization=Bearer t0ken",
                "OTEL_RESOURCE_ATTRIBUTES": "theorem.project=binary-equivalence,theorem.person=sami@theorem.dev",
                "OTEL_LOGS_EXPORTER": "none",
                "THEOREM_PROJECT": "binary-equivalence",
                "OMP_SESSION_ID": PARENT,
            },
            "-p",
            "say ok",
        )
        self.assertEqual(out["NAME"], "claude")
        self.assertEqual(out["ARGS"], "-p say ok")
        self.assertEqual(out["PATH"], self.path)
        self.assertEqual(out["CLAUDE_CODE_ENABLE_TELEMETRY"], "1")
        self.assertEqual(out["OTEL_LOGS_EXPORTER"], "otlp")
        self.assertEqual(out["OTEL_TRACES_EXPORTER"], "none")
        self.assertEqual(out["OTEL_METRICS_EXPORTER"], "none")
        self.assertEqual(out["OTEL_EXPORTER_OTLP_ENDPOINT"], ENDPOINT)
        self.assertEqual(out["OTEL_EXPORTER_OTLP_HEADERS"], "<unset>")
        self.assertEqual(out["OTEL_EXPORTER_OTLP_TRACES_HEADERS"], "<unset>")
        self.assertEqual(out["OTEL_EXPORTER_OTLP_LOGS_HEADERS"], "Authorization=Bearer t0ken")
        self.assertEqual(out["OTEL_EXPORTER_OTLP_PROTOCOL"], "http/protobuf")
        self.assertEqual(out["THEOREM_PROJECT"], "oss-verify")
        self.assertEqual(out["OTEL_RESOURCE_ATTRIBUTES"], "theorem.project=oss-verify,theorem.person=sami@theorem.dev")

    def test_claude_with_no_collector_set_gets_this_machines(self) -> None:
        out = self.run_shim("claude", {})
        self.assertEqual(out["OTEL_EXPORTER_OTLP_ENDPOINT"], ENDPOINT)
        self.assertEqual(out["CLAUDE_CODE_ENABLE_TELEMETRY"], "1")
        self.assertEqual(out["THEOREM_PROJECT"], "shared")

    def test_a_different_collector_keeps_everything(self) -> None:
        env = {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://lane:4318",
            "OTEL_RESOURCE_ATTRIBUTES": "theorem.project=oss-verify,theorem.lane=a",
            "OTEL_LOGS_EXPORTER": "none",
            "OMP_SESSION_ID": PARENT,
        }
        out = self.run_shim("claude", env)
        self.assertEqual(out["OTEL_EXPORTER_OTLP_ENDPOINT"], "http://lane:4318")
        self.assertEqual(out["OTEL_RESOURCE_ATTRIBUTES"], env["OTEL_RESOURCE_ATTRIBUTES"])
        self.assertEqual(out["OTEL_LOGS_EXPORTER"], "none")
        self.assertEqual(out["CLAUDE_CODE_ENABLE_TELEMETRY"], "<unset>")

    def test_codex_skips_mises_shims_dir_and_gets_the_tags(self) -> None:
        out = self.run_shim(
            "codex",
            {"OMP_SESSION_ID": PARENT, "OTEL_LOGS_EXPORTER": "none", "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer x",
             "OTEL_EXPORTER_OTLP_METRICS_HEADERS": "Authorization=Bearer x"},
            "exec",
            "hi",
        )
        self.assertEqual((out["NAME"], out["ARGS"]), ("codex", "exec hi"))
        self.assertEqual(out["OTEL_RESOURCE_ATTRIBUTES"], "theorem.project=oss-verify,theorem.person=sami@theorem.dev")
        self.assertEqual(out["OTEL_LOGS_EXPORTER"], "<unset>")
        self.assertEqual(out["CLAUDE_CODE_ENABLE_TELEMETRY"], "<unset>")
        self.assertEqual((out["OTEL_EXPORTER_OTLP_HEADERS"], out["OTEL_EXPORTER_OTLP_METRICS_HEADERS"]), ("<unset>", "<unset>"))

    def test_a_machine_without_the_token_runs_claude_untouched(self) -> None:
        (self.config / "agent-spend" / "token").unlink()
        out = self.run_shim("claude", {"OTEL_LOGS_EXPORTER": "none"}, "--version")
        self.assertEqual(out["ARGS"], "--version")
        self.assertEqual(out["OTEL_LOGS_EXPORTER"], "none")
        self.assertEqual(out["OTEL_EXPORTER_OTLP_ENDPOINT"], "<unset>")

    def test_no_real_binary_is_exit_127_not_a_loop(self) -> None:
        result = subprocess.run(
            ["claude"],
            env={**self.base, "PATH": f"{SHIMS}:{self.root / 'mise' / 'shims'}:/usr/bin:/bin"},
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 127, result.stderr)


if __name__ == "__main__":
    unittest.main()
