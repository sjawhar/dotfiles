#!/usr/bin/env python3
"""shims/gh's merge guard (scripts/gh-merge-guard) against a stub real gh, with knives bypassed."""
import base64
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).parents[2]
SHIM = DOTFILES / "shims" / "gh"
MARKERS = {"OMP_SESSION_ID": "s1", "PI_SESSION_FILE": "/tmp/s.jsonl", "PI_TOOL_BRIDGE_URL": "http://127.0.0.1:1",
           "AGENTBOX_SESSION": "box1"}

# The real gh: records its arguments and succeeds.
GH_STUB = '#!/bin/sh\nprintf "%s\\n" "$*" >> "$STUB_LOG"\n'


def node_id(prefix, repo_id, item_id):
    """A new-format GitHub node id: msgpack [0, repo_id, item_id], base64url without padding (its
    first characters, kw, are the msgpack array header)."""
    packed = bytes([0x93, 0x00, 0xCE]) + repo_id.to_bytes(4, "big") + bytes([0xCF]) + item_id.to_bytes(8, "big")
    return prefix + "_" + base64.urlsafe_b64encode(packed).decode().rstrip("=")


LEGION_PR = node_id("PR", 1146927990, 4893871408)
LEGION_2013 = "PR_kwDORFy7ds8AAAABI7KNMA"  # sjawhar/legion#2013's id as GitHub returned it
SMOKE_PR = node_id("PR", 999, 1)


class GhMergeGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "bin").mkdir()
        (d / "bin" / "gh").write_text(GH_STUB)
        (d / "bin" / "gh").chmod(0o755)
        self.log = d / "calls.log"
        self.cwd = d / "work"
        self.cwd.mkdir()
        # A clean environment, as a login shell has it: none of the agent markers.
        self.base = {"HOME": str(d), "XDG_CONFIG_HOME": str(d / "config"), "DOTFILES_DIR": str(DOTFILES),
                     "PATH": f"{d / 'bin'}:/usr/bin:/bin", "STUB_LOG": str(self.log), "KNIVES_GH_BYPASS": "1"}

    def tearDown(self):
        self.tmp.cleanup()

    def gh(self, *args, agent=True, cwd=None, **env):
        e = {**self.base, **({"OMP_SESSION_ID": "s1"} if agent else {}), **env}
        return subprocess.run([str(SHIM), *args], env=e, cwd=cwd or self.cwd, capture_output=True, text=True)

    def reached_gh(self):
        return self.log.exists() and self.log.read_text().strip() != ""

    def assert_refused(self, r):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("pr-gate merge", r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertFalse(self.reached_gh(), "the real gh ran")

    def assert_allowed(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.reached_gh(), "the real gh did not run")

    # --- the agent-context boundary ---

    def test_clean_environment_allows_merge(self):
        self.assert_allowed(self.gh("pr", "merge", "5", "-R", "sjawhar/legion", agent=False))

    def test_each_agent_marker_alone_refuses_merge(self):
        for name, value in MARKERS.items():
            with self.subTest(marker=name):
                self.assert_refused(self.gh("pr", "merge", "5", "-R", "sjawhar/legion", agent=False, **{name: value}))

    def test_routing_include_alone_refuses_merge(self):
        self.assert_refused(self.gh("pr", "merge", "5", "-R", "sjawhar/legion", agent=False, GIT_CONFIG_COUNT="1",
                                    GIT_CONFIG_KEY_0="include.path",
                                    GIT_CONFIG_VALUE_0=f"{DOTFILES}/gh-app-routes.gitconfig"))

    def test_pr_gate_merge_env_allows(self):
        self.assert_allowed(self.gh("pr", "merge", "5", "-R", "sjawhar/legion", "--squash", PR_GATE_MERGE="1"))

    # --- gh pr merge / review ---

    def test_pr_merge_forms_refused_on_gated_repos(self):
        for args in (["5", "-R", "sjawhar/legion"], ["--repo=sjawhar/legion", "5", "--squash"],
                     ["-Rsjawhar/legion", "5"], ["5", "--repo", "Sjawhar/Legion", "--auto", "--squash"],
                     ["https://github.com/sjawhar/legion/pull/5", "--squash"],
                     ["1", "-R", "theorem-labs/agent-infra", "--squash"],
                     ["5", "--subject", "-R", "-R", "sjawhar/legion"]):
            with self.subTest(args=args):
                self.assert_refused(self.gh("pr", "merge", *args))

    def test_pr_merge_on_another_repo_passes(self):
        self.assert_allowed(self.gh("pr", "merge", "5", "-R", "sjawhar/legion-smoke", "--squash"))

    def test_pr_merge_disable_auto_passes(self):
        self.assert_allowed(self.gh("pr", "merge", "5", "-R", "sjawhar/legion", "--disable-auto"))

    def test_pr_merge_target_from_origin_and_gh_repo(self):
        subprocess.run(["git", "init", "-q", str(self.cwd)], check=True)
        subprocess.run(["git", "-C", str(self.cwd), "remote", "add", "origin", "git@github.com:sjawhar/legion.git"], check=True)
        self.assert_refused(self.gh("pr", "merge", "5", "--squash"))
        self.assert_allowed(self.gh("pr", "merge", "5", "--squash", GH_REPO="sjawhar/legion-smoke"))
        self.log.unlink()
        self.assert_refused(self.gh("pr", "merge", "5", agent=True, cwd=self.cwd, GH_REPO="theorem-labs/agent-infra"))

    def test_pr_review_approve_refused_and_comment_allowed(self):
        self.assert_refused(self.gh("pr", "review", "5", "--approve", "-R", "sjawhar/legion"))
        self.assert_refused(self.gh("pr", "review", "5", "-a", "-b", "ok", "-R", "theorem-labs/agent-infra"))
        self.assert_allowed(self.gh("pr", "review", "5", "--request-changes", "-b", "-a", "-R", "sjawhar/legion"))

    # --- gh api ---

    def test_api_put_merge_refused(self):
        for args in (["-X", "PUT", "repos/sjawhar/legion/pulls/5/merge"],
                     ["--method=PUT", "/repos/sjawhar/legion/pulls/5/merge", "-f", "merge_method=squash"],
                     ["repos/theorem-labs/agent-infra/pulls/1/merge", "--method", "put"]):
            with self.subTest(args=args):
                self.assert_refused(self.gh("api", *args))

    def test_api_reads_of_a_merge_and_merges_elsewhere_pass(self):
        self.assert_allowed(self.gh("api", "repos/sjawhar/legion/pulls/5/merge"))
        self.assert_allowed(self.gh("api", "-X", "PUT", "repos/sjawhar/legion-smoke/pulls/5/merge"))

    def test_api_review_approve_refused(self):
        body = Path(self.tmp.name) / "review.json"
        body.write_text('{"event": "APPROVE", "commit_id": "abc", "body": "ok"}')
        for args in (["-X", "POST", "repos/sjawhar/legion/pulls/5/reviews", "-f", "event=APPROVE"],
                     ["repos/sjawhar/legion/pulls/5/reviews", "-f", "event=APPROVE", "-f", "body=ok"],
                     ["--method", "POST", "repos/sjawhar/legion/pulls/5/reviews", "--input", str(body)],
                     ["-X", "POST", "repos/sjawhar/legion/pulls/5/reviews/77/events", "-F", "event=APPROVE"]):
            with self.subTest(args=args):
                self.assert_refused(self.gh("api", *args))

    def test_api_review_comment_passes(self):
        self.assert_allowed(self.gh("api", "-X", "POST", "repos/sjawhar/legion/pulls/5/reviews",
                                    "-f", "event=REQUEST_CHANGES", "-f", "body=APPROVE once fixed"))

    def test_graphql_merge_mutations_refused_by_the_pr_id(self):
        for mutation in ("mergePullRequest", "enablePullRequestAutoMerge", "enqueuePullRequest"):
            q = f'mutation {{ {mutation}(input: {{pullRequestId: "{LEGION_PR}"}}) {{ clientMutationId }} }}'
            with self.subTest(mutation=mutation):
                self.assert_refused(self.gh("api", "graphql", "-f", f"query={q}"))
        q = "mutation($id: ID!) { mergePullRequest(input: {pullRequestId: $id}) { clientMutationId } }"
        for pr_id in (LEGION_PR, LEGION_2013):
            with self.subTest(pr_id=pr_id):
                r = self.gh("api", "graphql", "-f", f"query={q}", "-f", f"id={pr_id}")
                self.assert_refused(r)
                self.assertIn("on sjawhar/legion", r.stderr)

    def test_graphql_merge_of_another_repo_passes_and_an_opaque_one_refuses(self):
        q = "mutation($id: ID!) { mergePullRequest(input: {pullRequestId: $id}) { clientMutationId } }"
        self.assert_allowed(self.gh("api", "graphql", "-f", f"query={q}", "-f", f"id={SMOKE_PR}"))
        self.log.unlink()
        self.assert_refused(self.gh("api", "graphql", "-f", f"query={q}", "-F", "id=@/dev/null"))

    def test_graphql_reads_pass(self):
        self.assert_allowed(self.gh("api", "graphql", "-f", "query={ viewer { login } }"))

    def test_graphql_approve_review_refused(self):
        q = f'mutation {{ addPullRequestReview(input: {{pullRequestId: "{LEGION_PR}", event: APPROVE}}) {{ clientMutationId }} }}'
        self.assert_refused(self.gh("api", "graphql", "-f", f"query={q}"))


if __name__ == "__main__":
    unittest.main()
