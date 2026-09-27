#!/usr/bin/env python3
"""scripts/docs-pr-gate against stub gh and git binaries."""
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

GATE = Path(__file__).parents[1] / "docs-pr-gate"
REPO = "trajectory-labs-pbc/agent-c"

GH_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_LOG"
# the real shim announces a token mint on stderr; the gate must not read it as data
echo "gh-app-token: minting token for profile agent (owner=trajectory-labs-pbc)..." >&2
case "$2" in
  */compare/*)
    if [ -n "$STUB_COMPARE_FAIL" ]; then echo "HTTP 404: Not Found" >&2; exit 1; fi
    printf '%s' "$STUB_FILES" ;;
  */pulls\?*)
    if [ -n "$STUB_PULLS_FAIL" ]; then echo '{"message":"Bad credentials","status":"401"}'; exit 1; fi
    printf '%s' "$STUB_MERGED" ;;
  repos/*) echo main ;;
esac
"""

GIT_STUB = """#!/bin/sh
case "$1" in
  remote) echo "$STUB_ORIGIN" ;;
  symbolic-ref) [ -n "$STUB_BRANCH" ] && echo "$STUB_BRANCH" || exit 1 ;;
esac
"""


def today(offset_days=0):
    return (datetime.now(timezone.utc) + timedelta(days=offset_days)).strftime("%Y-%m-%d")


class DocsPrGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        stubs = Path(self.tmp.name)
        for name, body in (("gh", GH_STUB), ("git", GIT_STUB)):
            path = stubs / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
        self.log = stubs / "calls.log"
        self.env = {
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "STUB_LOG": str(self.log),
            "STUB_FILES": "",
            "STUB_ORIGIN": "",
            "STUB_BRANCH": "",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def gate(self, *args, files=(), **env):
        self.env["STUB_FILES"] = "\n".join(files)
        self.env.update(env)
        return subprocess.run([str(GATE), *args], env=self.env, capture_output=True, text=True, check=False)

    def test_docs_only_pr_from_an_ordinary_branch_is_refused_with_todays_batch_name(self):
        result = self.gate("--repo", REPO, "--head", "sami/postmortem", files=["docs/solutions/x.md", ".claude/skills/a/SKILL.md"])
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"docs-batch/<topic>/{today()}", result.stderr)
        self.assertIn("2 files", result.stderr)

    def test_docs_only_pr_from_todays_batch_branch_passes(self):
        result = self.gate(f"--repo={REPO}", "-H", f"docs-batch/solutions/{today()}", files=["docs/solutions/x.md"])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_batch_whose_pull_request_already_merged_today_is_refused(self):
        result = self.gate("--repo", REPO, "--head", f"docs-batch/dpi/{today()}", files=["docs/a.md"],
                           STUB_MERGED=f"#20344 at {today()}T02:55:00Z")
        self.assertEqual(result.returncode, 1)
        self.assertIn("already merged (#20344", result.stderr)
        self.assertIn(f"pulls?head=trajectory-labs-pbc:docs-batch/dpi/{today()}&state=closed", self.log.read_text(encoding="utf-8"))

    def test_an_unreadable_merge_check_refuses_rather_than_waving_through(self):
        result = self.gate("--repo", REPO, "--head", f"docs-batch/dpi/{today()}", files=["docs/a.md"], STUB_PULLS_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not check whether", result.stderr)

    def test_a_stale_batch_branch_is_refused(self):
        result = self.gate("-R", REPO, "--head", f"docs-batch/solutions/{today(-1)}", files=["docs/solutions/x.md"])
        self.assertEqual(result.returncode, 1)

    def test_any_non_documentation_file_lets_the_pr_through(self):
        for files in (["docs/a.md", "src/app.py"], [".claude/skills/a/scripts/check.py"]):
            with self.subTest(files=files):
                self.assertEqual(self.gate("--repo", REPO, "--head", "feature", files=files).returncode, 0)

    def test_other_repositories_are_never_consulted(self):
        result = self.gate("--repo", "sjawhar/dotfiles", "--head", "x", files=["README.md"])
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.log.exists(), "gh was called for a repo outside the rule")

    def test_an_unreadable_file_list_refuses_rather_than_waving_through(self):
        result = self.gate("--repo", REPO, "--head", "unpushed", STUB_COMPARE_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not read the file list", result.stderr)

    def test_repo_and_head_default_to_the_checkout_as_gh_does(self):
        result = self.gate(
            files=["docs/solutions/x.md"],
            STUB_ORIGIN="git@github.com:trajectory-labs-pbc/agent-c.git",
            STUB_BRANCH="sami/postmortem",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"compare/main...sami/postmortem", self.log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
