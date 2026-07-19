#!/usr/bin/env python3
"""Tests for the gh shim's target-owner resolution (_gh_owner_from_api_args /
_gh_resolve_target_url). The shim is bash; each case sources it with GH_TOKEN
preset (skipping the top-level minting block) and calls the helper directly."""
import shlex
import subprocess
import unittest
from pathlib import Path

SHIM_PATH = Path(__file__).parents[1] / "gh"


def run_helper(helper: str, *args: str) -> str:
    quoted = " ".join(shlex.quote(a) for a in args)
    result = subprocess.run(
        ["bash", "-c", f"GH_TOKEN=test-skip-mint source '{SHIM_PATH}'; {helper} {quoted}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError(f"{helper} failed: {result.stderr}")
    return result.stdout.strip()


class TestOwnerFromApiArgs(unittest.TestCase):
    def test_rest_repos_path(self):
        self.assertEqual(
            run_helper("_gh_owner_from_api_args", "api", "repos/acme/widgets/contents/x"),
            "acme",
        )

    def test_rest_orgs_path_with_leading_slash(self):
        self.assertEqual(
            run_helper("_gh_owner_from_api_args", "api", "/orgs/acme/teams"),
            "acme",
        )

    def test_rest_users_path(self):
        self.assertEqual(
            run_helper("_gh_owner_from_api_args", "api", "users/octocat/repos"),
            "octocat",
        )

    def test_rest_orgs_bare_owner_with_query(self):
        self.assertEqual(
            run_helper("_gh_owner_from_api_args", "api", "orgs/acme?page=2"),
            "acme",
        )

    def test_graphql_organization_login(self):
        query = 'query{ organization(login:"acme"){ projectV2(number:2){ id } } }'
        self.assertEqual(
            run_helper("_gh_owner_from_api_args", "api", "graphql", "-f", f"query={query}"),
            "acme",
        )

    def test_graphql_repository_owner(self):
        query = 'query{ repository(owner:"acme", name:"widgets"){ issue(number:1){ id } } }'
        self.assertEqual(
            run_helper("_gh_owner_from_api_args", "api", "graphql", "-f", f"query={query}"),
            "acme",
        )

    def test_graphql_whitespace_and_single_quotes(self):
        query = "query{ organization( login : 'acme' ){ id } }"
        self.assertEqual(
            run_helper("_gh_owner_from_api_args", "api", "graphql", "-f", f"query={query}"),
            "acme",
        )

    def test_graphql_node_id_mutation_has_no_owner_signal(self):
        query = 'mutation{ addProjectV2ItemById(input:{projectId:"PVT_x", contentId:"I_y"}){ item{ id } } }'
        self.assertEqual(
            run_helper("_gh_owner_from_api_args", "api", "graphql", "-f", f"query={query}"),
            "",
        )

    def test_non_api_invocation_yields_nothing(self):
        self.assertEqual(
            run_helper("_gh_owner_from_api_args", "pr", "view", "123"),
            "",
        )


class TestResolveTargetUrl(unittest.TestCase):
    def test_api_rest_path_beats_remote(self):
        self.assertEqual(
            run_helper("_gh_resolve_target_url", "api", "repos/acme/widgets/labels"),
            "https://github.com/acme/gh-api.git",
        )

    def test_api_graphql_org_query_beats_remote(self):
        query = 'query{ organization(login:"acme"){ id } }'
        self.assertEqual(
            run_helper("_gh_resolve_target_url", "api", "graphql", "-f", f"query={query}"),
            "https://github.com/acme/gh-api.git",
        )

    def test_explicit_repo_flag_still_wins_for_non_api(self):
        self.assertEqual(
            run_helper("_gh_resolve_target_url", "issue", "view", "1", "-R", "acme/widgets"),
            "https://github.com/acme/widgets.git",
        )


if __name__ == "__main__":
    unittest.main()
