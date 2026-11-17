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
        """The gate on `gh pr create ARGS…`."""
        return self.gate_argv("pr", "create", *args, files=files, **env)

    def gate_argv(self, *argv, files=(), **env):
        """The gate on the whole gh command line ARGV, as shims/gh hands it over."""
        self.env["STUB_FILES"] = "\n".join(files)
        self.env.update(env)
        return subprocess.run([str(GATE), *argv], env=self.env, capture_output=True, text=True, check=False)

    def test_options_before_the_command_or_its_verb_are_read_as_gh_reads_them(self):
        # `gh -R o/r pr create` is a pull request too: the shim matched only `pr create` as its first two arguments.
        for argv in (["-R", REPO, "pr", "create", "--head", "sami/postmortem"],
                     ["pr", "--repo", REPO, "create", "-Hsami/postmortem"],
                     ["pr", "new", f"-R{REPO}", "--head=sami/postmortem"],
                     ["pr", "create", "-dH", "sami/postmortem", f"--repo={REPO}"],
                     ["pr", "create", "--title", "-R", "-R", REPO, "--head", "sami/postmortem"]):
            with self.subTest(argv=argv):
                result = self.gate_argv(*argv, files=["docs/solutions/x.md"])
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(f"compare/main...sami/postmortem", self.log.read_text(encoding="utf-8"))
                self.log.unlink()

    def test_a_call_that_is_not_pr_create_is_never_judged(self):
        for argv in (["pr", "view", "-R", REPO], ["issue", "create", "-R", REPO, "--title", "create"],
                     ["-R", REPO, "pr", "comment", "1", "--body", "create"], ["api", "repos/x/y/pulls", "-f", "title=pr create"]):
            with self.subTest(argv=argv):
                result = self.gate_argv(*argv, files=["docs/solutions/x.md"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(self.log.exists(), "the gate read GitHub for a call that is no pull request")

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


class ShimHandsTheGateEveryPullRequest(unittest.TestCase):
    """shims/gh, in an agent session, gives the gate `gh -R o/r pr create` as well as `gh pr create -R o/r`. Its
    checkout here is this one with a stub gate that records its argv and refuses, so nothing reaches GitHub."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        dotfiles = Path(__file__).resolve().parents[2]
        fake = root / "dotfiles"
        (fake / "scripts").mkdir(parents=True)
        for entry in dotfiles.iterdir():
            if entry.name != "scripts":
                (fake / entry.name).symlink_to(entry)
        for entry in (dotfiles / "scripts").iterdir():
            if entry.name != "docs-pr-gate":
                (fake / "scripts" / entry.name).symlink_to(entry)
        self.gate_log = root / "gate.log"
        gate = fake / "scripts" / "docs-pr-gate"
        gate.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> {self.gate_log}\nexit 1\n', encoding="utf-8")
        gate.chmod(0o755)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        self.gh_log = root / "gh.log"
        for name, body in (("gh", f'printf "%s\\n" "$*" >> {self.gh_log}'), ("gh-app-token", "echo app")):
            (bin_dir / name).write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
            (bin_dir / name).chmod(0o755)
        self.gh = dotfiles / "shims" / "gh"
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith(("GIT_CONFIG_", "GH_", "PUBLIC_LEAK_CHECK"))},
            "PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(root), "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_CACHE_HOME": str(root / "cache"), "TMPDIR": str(root), "DOTFILES_DIR": str(fake),
            "OMP_SESSION_ID": "01a0fa64-b3a5-7415-b143-c2a338b5da8b", "PUBLIC_LEAK_CHECK": "/bin/true",
            "KNIVES_GH_BYPASS": "1", "GH_READ_CACHE": "0", "GH_READ_CACHE_LOG": "0",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def run_shim(self, *argv):
        return subprocess.run([str(self.gh), *argv], env=self.env, cwd=self.tmp.name, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=240, check=False)

    def test_a_pull_request_with_options_before_its_command_reaches_the_gate(self):
        for argv in (["-R", REPO, "pr", "create", "--head", "x", "--title", "t", "--body", "b"],
                     ["pr", "create", "-R", REPO, "--head", "x", "--title", "t", "--body", "b"]):
            with self.subTest(argv=argv):
                self.gate_log.unlink(missing_ok=True)
                result = self.run_shim(*argv)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(self.gate_log.read_text(encoding="utf-8").splitlines(), [" ".join(argv)])
                self.assertFalse(self.gh_log.exists(), "gh ran although the gate refused")
        # Control: a call that is no pull request never starts the gate.
        self.gate_log.unlink(missing_ok=True)
        self.assertEqual(self.run_shim("-R", REPO, "pr", "view", "1").returncode, 0)
        self.assertFalse(self.gate_log.exists())


if __name__ == "__main__":
    unittest.main()
