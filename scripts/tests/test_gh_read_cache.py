#!/usr/bin/env python3
"""scripts/gh-read-cache against a stub gh that counts the calls it receives."""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

CACHE = Path(__file__).parents[1] / "gh-read-cache"

# Counts every call, and answers whatever the test put in STUB_BODY. With
# --include it answers like `gh api --include`: a status line, headers, a blank
# line, the body; STUB_STATUS and an If-None-Match match drive the 304 path.
GH_STUB = r"""#!/bin/sh
printf '%s\n' "$*" >> "$STUB_LOG"
if [ -n "$STUB_FAIL" ]; then
  echo "gh: something went wrong" >&2
  exit "$STUB_FAIL"
fi
want_include=0
cond=""
for a in "$@"; do
  case "$a" in
    --include) want_include=1 ;;
    If-None-Match:*) cond=${a#If-None-Match: } ;;
  esac
done
if [ "$want_include" = 1 ]; then
  if [ -n "$cond" ] && [ "$cond" = "$STUB_ETAG" ]; then
    printf 'HTTP/2.0 304 Not Modified\r\nEtag: %s\r\n\r\n' "$STUB_ETAG"
    echo "gh: HTTP 304" >&2
    exit 1
  fi
  printf 'HTTP/2.0 %s OK\r\nEtag: %s\r\n\r\n%s' "${STUB_STATUS:-200}" "$STUB_ETAG" "$STUB_BODY"
  [ "${STUB_STATUS:-200}" = 200 ] || exit 1
  exit 0
fi
printf '%s' "$STUB_BODY"
exit "${STUB_CODE:-0}"
"""


class GhReadCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.stub = self.dir / "gh"
        self.stub.write_text(GH_STUB, encoding="utf-8")
        self.stub.chmod(0o755)
        self.log = self.dir / "calls.log"
        self.cache = self.dir / "cache"
        self.work = self.dir / "work"
        self.work.mkdir()
        self.addCleanup(self.tmp.cleanup)

    def env(self, **extra):
        env = dict(os.environ)
        env.pop("GH_READ_CACHE_BYPASS", None)
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        env.pop("GH_REPO", None)
        env.update(
            {
                "GH_READ_CACHE_GH": str(self.stub),
                "GH_READ_CACHE_DIR": str(self.cache),
                "STUB_LOG": str(self.log),
                "STUB_BODY": "BODY-1",
                "STUB_ETAG": 'W/"abc"',
            }
        )
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def run_cache(self, *args, **extra):
        return subprocess.run(
            [str(CACHE), "--", *args],
            capture_output=True,
            text=True,
            cwd=str(self.work),
            env=self.env(**extra),
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [ln for ln in self.log.read_text(encoding="utf-8").splitlines() if ln]

    # -- what is cacheable at all ------------------------------------------

    def test_classifier_reads_and_writes(self):
        import importlib.machinery
        import importlib.util

        loader = importlib.machinery.SourceFileLoader("ghrc", str(CACHE))
        spec = importlib.util.spec_from_loader("ghrc", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)

        etag = [
            "api repos/o/r",
            "api repos/o/r/pulls/5 -H Accept:x",
            # --include and --jq compose: gh prints the header block, a blank
            # line, then the jq output, so the plain body is recoverable.
            "api repos/o/r --jq .name",
        ]
        window = [
            "pr view 5 -R o/r --json state",
            "pr checks 5 -R o/r",
            "run list -R o/r",
            "search prs --state open",
            "api repos/o/r --paginate",
            'api graphql -f query=query { repository(owner: "o", name: "r") { id } }',
        ]
        none = [
            "pr merge 5 --squash",
            "pr create --title x --body y",
            "pr edit 5 --add-label x",
            "pr ready 5",
            "pr comment 5 --body x",
            "pr review 5 --approve",
            "run rerun 12",
            "run cancel 12",
            "run watch 12",
            "pr checks 5 --watch",
            "pr view 5 --web",
            "api repos/o/r/pulls -X POST -f title=x",
            "api repos/o/r/issues/1/comments -f body=hi",
            "api --method DELETE repos/o/r/x",
            'api graphql -f query=mutation { enablePullRequestAutoMerge(input: {}) { x } }',
            "api graphql -F query=@file.graphql",
            "api rate_limit",
            "auth status",
            "repo clone o/r",
            "--version",
        ]
        for line in etag:
            self.assertEqual(mod.classify(line.split()), "etag", line)
        for line in window:
            self.assertEqual(mod.classify(line.split()), "window", line)
        for line in none:
            self.assertEqual(mod.classify(line.split()), "none", line)

    # -- the 60 s identical-read window ------------------------------------

    def test_identical_read_reuses_within_window(self):
        a = self.run_cache("pr", "view", "5", "-R", "o/r", "--json", "state")
        b = self.run_cache("pr", "view", "5", "-R", "o/r", "--json", "state")
        self.assertEqual(a.stdout, "BODY-1")
        self.assertEqual(b.stdout, "BODY-1")
        self.assertEqual(len(self.calls()), 1, self.calls())

    def test_window_expiry_asks_again(self):
        self.run_cache("pr", "view", "5", "-R", "o/r", GH_READ_CACHE_WINDOW=0.2)
        time.sleep(0.4)
        self.run_cache("pr", "view", "5", "-R", "o/r", GH_READ_CACHE_WINDOW=0.2)
        self.assertEqual(len(self.calls()), 2, self.calls())

    def test_a_different_repo_is_a_different_read(self):
        self.run_cache("pr", "view", "5", "-R", "o/r")
        self.run_cache("pr", "view", "5", "-R", "o/other")
        self.assertEqual(len(self.calls()), 2, self.calls())

    def test_exit_code_and_stderr_are_replayed(self):
        # `gh pr checks` answers 8 for pending; a replay that loses the code
        # would turn a pending gate into a pass.
        a = self.run_cache("pr", "checks", "5", "-R", "o/r", STUB_CODE=8)
        b = self.run_cache("pr", "checks", "5", "-R", "o/r", STUB_CODE=8)
        self.assertEqual((a.returncode, b.returncode), (8, 8))
        self.assertEqual(a.stdout, b.stdout)
        self.assertEqual(len(self.calls()), 1, self.calls())

    def test_graphql_read_is_reused_and_a_mutation_is_not(self):
        q = 'query { repository(owner: "o", name: "r") { id } }'
        self.run_cache("api", "graphql", "-f", "query=" + q)
        self.run_cache("api", "graphql", "-f", "query=" + q)
        self.assertEqual(len(self.calls()), 1, self.calls())
        m = "mutation { mergePullRequest(input: {}) { clientMutationId } }"
        self.run_cache("api", "graphql", "-f", "query=" + m)
        self.run_cache("api", "graphql", "-f", "query=" + m)
        self.assertEqual(len(self.calls()), 3, self.calls())

    def test_uncomputed_mergeability_is_never_stored(self):
        # GitHub answers UNKNOWN while it computes mergeability, and the merge
        # rule is to read again; a cached UNKNOWN would answer the re-read.
        body = '{"mergeStateStatus":"UNKNOWN","number":5}'
        self.run_cache("pr", "view", "5", "-R", "o/r", "--json", "mergeStateStatus", STUB_BODY=body)
        self.run_cache("pr", "view", "5", "-R", "o/r", "--json", "mergeStateStatus", STUB_BODY=body)
        self.assertEqual(len(self.calls()), 2, self.calls())
        settled = '{"mergeStateStatus":"BLOCKED","number":5}'
        before = len(self.calls())
        self.run_cache("pr", "view", "5", "-R", "o/r", "--json", "mergeStateStatus", STUB_BODY=settled)
        self.run_cache("pr", "view", "5", "-R", "o/r", "--json", "mergeStateStatus", STUB_BODY=settled)
        self.assertEqual(len(self.calls()) - before, 1, self.calls())

    def test_null_mergeable_is_never_stored(self):
        body = '{"mergeable":null,"number":5}'
        self.run_cache("api", "repos/o/r/pulls/5", STUB_BODY=body)
        self.run_cache("api", "repos/o/r/pulls/5", STUB_BODY=body)
        self.assertEqual(len(self.calls()), 2, self.calls())

    # -- writes are never cached -------------------------------------------

    def test_writes_always_reach_github(self):
        for args in (
            ("pr", "merge", "5", "--squash"),
            ("api", "repos/o/r/pulls", "-X", "POST", "-f", "title=x"),
            ("run", "rerun", "12"),
            ("pr", "edit", "5", "--add-label", "x"),
        ):
            before = len(self.calls())
            self.run_cache(*args)
            self.run_cache(*args)
            self.assertEqual(len(self.calls()) - before, 2, args)

    # -- conditional requests ----------------------------------------------

    def test_etag_revalidation_serves_the_stored_body(self):
        a = self.run_cache("api", "repos/o/r", GH_READ_CACHE_WINDOW=0)
        self.assertEqual(a.stdout, "BODY-1")
        self.assertEqual(a.returncode, 0)
        # The stub answers 304 when the request carries the stored ETag.
        b = self.run_cache("api", "repos/o/r", GH_READ_CACHE_WINDOW=0, STUB_BODY="BODY-2")
        self.assertEqual(b.stdout, "BODY-1")
        self.assertEqual(b.returncode, 0)
        self.assertNotIn("HTTP 304", b.stderr)
        self.assertIn("If-None-Match", self.calls()[1])

    def test_etag_miss_takes_the_new_body(self):
        self.run_cache("api", "repos/o/r", GH_READ_CACHE_WINDOW=0)
        c = self.run_cache(
            "api", "repos/o/r", GH_READ_CACHE_WINDOW=0, STUB_BODY="BODY-2", STUB_ETAG='W/"zzz"'
        )
        self.assertEqual(c.stdout, "BODY-2")

    def test_include_headers_never_reach_the_caller(self):
        a = self.run_cache("api", "repos/o/r")
        self.assertEqual(a.stdout, "BODY-1")
        self.assertNotIn("HTTP/2.0", a.stdout)
        self.assertNotIn("Etag", a.stdout)

    # -- every failure falls through ---------------------------------------

    def test_error_is_reported_and_not_stored(self):
        a = self.run_cache("api", "repos/o/r", STUB_FAIL=1)
        self.assertEqual(a.returncode, 1)
        self.assertIn("something went wrong", a.stderr)
        self.run_cache("api", "repos/o/r", STUB_FAIL=1)
        self.assertEqual(len(self.calls()), 2, self.calls())

    def test_non_2xx_is_not_stored(self):
        a = self.run_cache("api", "repos/o/missing", STUB_STATUS=404, STUB_BODY='{"message":"Not Found"}')
        self.assertEqual(a.returncode, 1)
        self.run_cache("api", "repos/o/missing", STUB_STATUS=404, STUB_BODY='{"message":"Not Found"}')
        self.assertEqual(len(self.calls()), 2, self.calls())

    def test_unreadable_cache_dir_still_answers(self):
        blocked = self.dir / "blocked"
        blocked.mkdir(mode=0o500)
        self.addCleanup(blocked.chmod, 0o700)
        a = self.run_cache("pr", "view", "5", "-R", "o/r", GH_READ_CACHE_DIR=str(blocked / "c"))
        self.assertEqual(a.returncode, 0)
        self.assertEqual(a.stdout, "BODY-1")

    def test_off_switch_passes_through(self):
        self.run_cache("pr", "view", "5", "-R", "o/r", GH_READ_CACHE=0)
        self.run_cache("pr", "view", "5", "-R", "o/r", GH_READ_CACHE=0)
        self.assertEqual(len(self.calls()), 2, self.calls())

    # -- the store itself ---------------------------------------------------

    def test_cache_is_private_and_logged(self):
        self.run_cache("pr", "view", "5", "-R", "o/r")
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o700)
        logs = sorted((self.cache / "log").glob("*.jsonl"))
        self.assertTrue(logs)
        events = [json.loads(ln) for ln in logs[0].read_text().splitlines() if ln]
        self.assertTrue(any(e["ev"] == "miss" for e in events))

    def test_two_identities_do_not_share_an_answer(self):
        self.run_cache("pr", "view", "5", "-R", "o/r", GH_TOKEN="ghs_app")
        self.run_cache("pr", "view", "5", "-R", "o/r", GH_TOKEN="ghp_person")
        self.assertEqual(len(self.calls()), 2, self.calls())

    def test_two_owners_do_not_share_an_answer(self):
        # The shim mints a different App installation token per repo owner, so
        # two owners are two identities even when the rest of the call matches.
        self.run_cache("pr", "list", "-R", "theorem-labs/x")
        self.run_cache("pr", "list", "-R", "rocq-prover/x")
        self.assertEqual(len(self.calls()), 2, self.calls())

    def test_a_hit_is_visible_when_asked(self):
        a = self.run_cache("pr", "view", "5", "-R", "o/r", GH_READ_CACHE_DEBUG=1)
        b = self.run_cache("pr", "view", "5", "-R", "o/r", GH_READ_CACHE_DEBUG=1)
        self.assertIn("gh-read-cache: miss", a.stderr)
        self.assertIn("gh-read-cache: hit", b.stderr)
        self.assertIn("no request made", b.stderr)
        # Silent unless asked: the stderr of a gh call is read by scripts.
        c = self.run_cache("pr", "view", "5", "-R", "o/r")
        self.assertNotIn("gh-read-cache", c.stderr)

    def test_a_corrupted_shim_is_never_stored(self):
        # The knives race leaves shims/gh holding the knives wrapper, and
        # `gh api ...` then answers that tool's usage text and exits 2
        # (measured 2026-11-01: rc 2, empty stdout, 109 bytes of stderr). A
        # ten-second corruption must not be served for another sixty, and the
        # same holds if a broken tool writes its usage to stdout instead.
        broken = self.dir / "knives-gh"
        broken.write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$*" >> "$STUB_LOG"\n'
            'if [ -n "$USAGE_ON_STDOUT" ]; then\n'
            "  echo \"error: unrecognized subcommand 'api'\"\n"
            "  echo 'Usage: knives [OPTIONS] <COMMAND>'\n"
            "else\n"
            "  echo \"error: unrecognized subcommand 'api'\" >&2\n"
            "  echo 'Usage: knives [OPTIONS] <COMMAND>' >&2\n"
            "fi\n"
            "exit 2\n",
            encoding="utf-8",
        )
        broken.chmod(0o755)
        for stdout_variant in ("", "1"):
            before = len(self.calls())
            for _ in range(2):
                r = self.run_cache(
                    "pr",
                    "view",
                    "5",
                    "-R",
                    "o/r",
                    GH_READ_CACHE_GH=str(broken),
                    USAGE_ON_STDOUT=stdout_variant,
                )
                self.assertEqual(r.returncode, 2)
            self.assertEqual(len(self.calls()) - before, 2, self.calls())
        # And the cache holds nothing, so the next real call is a real call.
        a = self.run_cache("pr", "view", "5", "-R", "o/r")
        self.assertEqual(a.stdout, "BODY-1")
        self.assertEqual(a.returncode, 0)


if __name__ == "__main__":
    unittest.main()
