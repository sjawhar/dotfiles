#!/usr/bin/python3
"""post-review names the model that wrote each verdict.

A verdict that does not say which model wrote it cannot be told from a same-family one: on
2026-11-03 several gates asked for a cross-family reviewer ran on Claude while the Codex window
was spent, and nothing on the pull request said so. These tests run the script with --dry-run,
which reads the body and builds the review but mints, reads and posts nothing, against a stub
omp transcript in PI_SESSION_FILE.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "post-review"
HEAD = "0123456789abcdef0123456789abcdef01234567"


def assistant(provider: str, model: str) -> str:
    return json.dumps(
        {"type": "message", "message": {"role": "assistant", "provider": provider, "model": model}}
    )


class ModelFooter(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.body = self.dir / "body.md"
        self.body.write_text("VERDICT: APPROVE\n\nLooks right.\n")
        self.session = self.dir / "session.jsonl"

    def run_script(self, *extra: str, session: str | None = None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != "PI_SESSION_FILE"}
        if session is not None:
            env["PI_SESSION_FILE"] = session
        return subprocess.run(
            ["python3", str(SCRIPT), "--dry-run", *extra, "7", HEAD, "APPROVE", str(self.body), "acme/widgets"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def test_explicit_model_wins(self) -> None:
        self.session.write_text(assistant("anthropic", "claude-opus-5-5") + "\n")
        result = self.run_script("--model", "openai-codex/gpt-6-astra", session=str(self.session))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(
            result.stdout.endswith("\n\n---\nReviewed by model: `openai-codex/gpt-6-astra` (given with --model)\n"),
            result.stdout,
        )

    def test_every_answering_model_in_order_from_the_transcript(self) -> None:
        lines = [
            json.dumps({"type": "model_change", "model": "openai-codex/gpt-6-astra"}),
            assistant("openai-codex", "gpt-6-astra"),
            json.dumps({"type": "model_usage", "provider": "anthropic", "model": "claude-haiku-5-5"}),
            json.dumps({"type": "message", "message": {"role": "user", "content": "assistant"}}),
            "not json, but \"assistant\"",
            assistant("anthropic", "claude-opus-5-5"),
            assistant("openai-codex", "gpt-6-astra"),
        ]
        self.session.write_text("\n".join(lines) + "\n")
        result = self.run_script(session=str(self.session))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "Reviewed by model: `openai-codex/gpt-6-astra`, then `anthropic/claude-opus-5-5`"
            " (read from the posting session's transcript)\n",
            result.stdout,
        )
        self.assertNotIn("haiku", result.stdout)

    def test_unknown_model_still_posts_and_says_so(self) -> None:
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Reviewed by model: unknown", result.stdout)
        self.assertIn("Pass --model", result.stderr)

    def test_dry_run_touches_no_broker_or_github(self) -> None:
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        calls = self.dir / "calls"
        for name in ("secret-run", "gh", "envoy"):
            stub = bin_dir / name
            stub.write_text(f'#!/bin/bash\necho {name} >> "{calls}"\nexit 1\n')
            stub.chmod(0o755)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
        result = subprocess.run(
            ["python3", str(SCRIPT), "7", HEAD, "APPROVE", str(self.body), "acme/widgets", "--dry-run"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(calls.exists(), calls.read_text() if calls.exists() else "")


if __name__ == "__main__":
    unittest.main()
