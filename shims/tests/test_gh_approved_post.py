#!/usr/bin/env python3
"""A post Sami instructed goes out as him only on a citation that holds: GH_SHIM_APPROVAL names
the Dispatch ask he answered, and shims/gh (through scripts/gh-approved-post) checks the answer
and that the title and body are the draft he was shown, logs the post, and only then lets the call
go on its route. A citation that does not hold refuses the call before it reaches GitHub.

On 2026-11-02 he answered dispatch://OSSV-232/ask/a7d1ac41-45c4-4504-a985-7c92998d2158 "Post it"
for an issue on google/oss-fuzz, and the issue still did not go out.

Dispatch is a stub that prints the outputs of `dispatch read` and `dispatch doc-read` in their
real shape; knives is a stub that records the call it would route and prints an issue URL, and
gh-app-token is a stub that names the route the owner's token comes from (the App, here).
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).parents[1]
DOTFILES = SHIMS.parent
ASK = "dispatch://OSSV-1/ask/a7d1ac41-45c4-4504-a985-7c92998d2158"
DOC = "dispatch://OSSV-1/artifact/issue-md"
QUESTION = "Post the issue above on o/upstream? It asks one question."
TITLE = "UBSan array-bounds stopped checking trailing arrays"
BODY = "Since #11714, `-fsanitize=array-bounds` does not check trailing arrays.\n\nShould it?"


def executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)


OPTIONS = ("Post it — post the title and body above, unchanged.", "Do not post — nothing goes out.")


def ask_text(state="answered", by="sami@theorem.dev", selected="Post it", text=None, options=OPTIONS):
    answer = "" if state != "answered" else (
        f"Answer:\n- By: {by}\n- Selected: {selected}\n" + (f"- Text: {text}\n" if text else ""))
    listed = "".join(f"- {option}\n" for option in options)
    return (
        f"Question: {QUESTION}\nOptions:\n{listed}"
        f"State: {state}\nAsk id: a7d1ac41-45c4-4504-a985-7c92998d2158\n"
        f"{answer}Replies:\n8999b6a1 · user sami@theorem.dev\nBody: State: answered\n"
        f"Node: ask {ASK} · made by session s at 2026-11-02T17:11:51Z · part of {DOC} (version 1)\n"
        f"Links:\n- anchored_to artifact {DOC} (issue.md · 2026-11-02T17:11:51Z)\n"
    )


def doc_text(title=TITLE, body=BODY, block='state="open"'):
    return (
        "# Issue for o/upstream\n\n**The issue, exactly as it would be posted:**\n\n"
        f"Title: {title}\n\n---\n\n{body}\n\n---\n\n**If you say no:** nothing is posted.\n\n"
        f':::ask{{#52c6b03a urgency="low" multiple="false" {block}}}\n{QUESTION}\n\n'
        "- Post it: post the title and body above, unchanged.\n- Do not post: nothing goes out.\n:::\n"
    )


ANSWERED = 'state="answered" answered_by="sami@theorem.dev" selected="[&#x22;Post it&#x22;]"'


class CitedPost(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gh-approved-post-"))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.fixtures = self.tmp / "dispatch"
        self.fixtures.mkdir()
        self.calls = self.tmp / "knives-calls"
        self.cache = self.tmp / "cache"
        # The route of the owner's token, which the fallback-write gate asks gh-app-token for.
        self.source = self.tmp / "token-source"
        self.source.write_text("app\n")
        # gh resolves a real gh before it hands the call to knives; this one is never run.
        executable(self.bin / "gh", "exit 99")
        executable(self.bin / "knives",
                   '[ "$1" = gh ] && shift; [ "$1" = -- ] && shift\n'
                   f'printf "%s\\n" "$*" >> {self.calls}\n'
                   'echo https://github.com/o/upstream/issues/12')
        # Ahead of shims/ on PATH: shims/dispatch (the omp plugin's CLI) would shadow a stub behind it.
        self.front = self.tmp / "front"
        self.front.mkdir()
        executable(self.front / "dispatch",
                   f'd={self.fixtures}\n'
                   'case "$1 $3" in\n'
                   f'  "read {ASK}") cat "$d/ask.txt" ;;\n'
                   f'  "read {DOC}") echo "Document: OSSV-1 / issue.md"; echo "Versions: 2" ;;\n'
                   f'  "doc-read {DOC}@v1") cat "$d/v1.md" ;;\n'
                   f'  "doc-read {DOC}@v2") cat "$d/v2.md" ;;\n'
                   '  *) echo "dispatch stub: no fixture for $*" >&2; exit 1 ;;\n'
                   'esac')
        executable(self.front / "gh-app-token", f'cat {self.source}')
        self.body_file = self.tmp / "body.md"
        self.body_file.write_text(BODY + "\n")
        self.fixture()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fixture(self, ask=None, v1=None, v2=None):
        (self.fixtures / "ask.txt").write_text(ask or ask_text())
        (self.fixtures / "v1.md").write_text(v1 or doc_text())
        (self.fixtures / "v2.md").write_text(v2 or doc_text(block=ANSWERED))

    def run_gh(self, argv, cite=True, **extra):
        env = {
            "HOME": str(self.tmp), "USER": os.environ.get("USER", "u"), "LANG": "C.UTF-8",
            "PATH": os.pathsep.join([str(self.front), str(SHIMS), str(self.bin), "/usr/bin", "/bin"]),
            "TMPDIR": str(self.tmp), "DOTFILES_DIR": str(DOTFILES), "OMP_SESSION_ID": "sess-1",
            "GH_READ_CACHE": "0", "GH_READ_CACHE_LOG": "0", "GH_READ_CACHE_DIR": str(self.cache),
            "PUBLIC_LEAK_CHECK": "/bin/true",
        }
        if cite:
            env["GH_SHIM_APPROVAL"] = ASK
        env.update(extra)
        env = {k: v for k, v in env.items() if v is not None}
        return subprocess.run([str(SHIMS / "gh"), *argv], env=env, capture_output=True, text=True,
                              cwd=self.tmp, timeout=60, check=False)

    def issue(self, title=TITLE, body_file=None):
        return ["issue", "create", "-R", "o/upstream", "--title", title,
                "--body-file", str(body_file or self.body_file)]

    def pull(self, *extra, head="sjawhar:fix/trailing-arrays", title=TITLE):
        return ["pr", "create", "-R", "o/upstream", "--title", title,
                "--body-file", str(self.body_file), "--head", head, "--base", "main", *extra]

    def routed(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def log(self):
        path = self.cache / "log" / "approved-posts.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def assert_refused(self, result, why, call="issue create -R o/upstream"):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"gh shim: REFUSED: `gh {call}", result.stderr)
        self.assertIn(why, result.stderr)
        self.assertEqual(self.routed(), [], "a refused citation must not reach GitHub")
        [record] = self.log()
        self.assertEqual(record["verdict"], "refused")
        self.assertEqual(record["ask"], ASK)

    def test_a_valid_citation_runs_the_call_and_logs_who_which_ask_and_what(self):
        result = self.run_gh(self.issue())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "https://github.com/o/upstream/issues/12")
        self.assertEqual(self.routed(), [" ".join(self.issue())])
        self.assertIn(f"gh shim: cited post: {ASK} answered by sami@theorem.dev", result.stderr)
        allowed, done = self.log()
        self.assertEqual(allowed["verdict"], "allowed")
        self.assertEqual((allowed["sid"], allowed["ask"], allowed["repo"]), ("sess-1", ASK, "o/upstream"))
        self.assertEqual((allowed["title"], allowed["body"]), (TITLE, BODY + "\n"))
        self.assertEqual(allowed["draft"], f"{DOC}@v2")
        self.assertEqual((done["ev"], done["rc"], done["pid"]), ("result", 0, allowed["pid"]))
        self.assertEqual(oct((self.cache / "log").stat().st_mode & 0o777), "0o700")

    def test_a_comment_cites_the_same_way(self):
        argv = ["issue", "comment", "https://github.com/o/upstream/issues/5", "--body", BODY]
        result = self.run_gh(argv)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log()[0]["repo"], "o/upstream")

    def test_an_ask_answered_by_someone_else_is_refused(self):
        self.fixture(ask=ask_text(by="someone@theorem.dev"))
        self.assert_refused(self.run_gh(self.issue()), "answered by someone@theorem.dev, not sami@theorem.dev")

    def test_an_unanswered_ask_is_refused(self):
        self.fixture(ask=ask_text(state="open"), v2=doc_text())
        self.assert_refused(self.run_gh(self.issue()), "the ask is open, not answered")

    def test_an_ask_answered_with_the_other_option_is_refused(self):
        self.fixture(ask=ask_text(selected="Do not post"),
                     v2=doc_text(block=ANSWERED.replace("Post it", "Do not post")))
        self.assert_refused(self.run_gh(self.issue()), "the selected option 'Do not post' does not approve a post")

    def test_options_printed_with_their_block_reference_still_match(self):
        # Dispatch prints each option's own reference after it (INFRA-3904, 2026-11-16).
        options = (f"Post it ({ASK}#b-b2)", f"Do not post ({ASK}#b-b3)")
        self.fixture(ask=ask_text(options=options))
        result = self.run_gh(self.issue())
        self.assertEqual(result.returncode, 0, result.stderr)

    def answered_with(self, selected, options):
        quoted = selected.replace('"', "&#x22;")
        self.fixture(ask=ask_text(selected=selected, options=options),
                     v2=doc_text(block=ANSWERED.replace("Post it", quoted)))

    TWO_STEPS = ("Push the commit and post both comments", "Push the commit only — no comments",
                 "Push the commit and do not post", "Do nothing")

    def test_a_clause_that_posts_approves_the_post(self):
        self.answered_with("Push the commit and post both comments", self.TWO_STEPS)
        result = self.run_gh(self.issue())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_option_whose_clauses_post_nothing_is_refused(self):
        self.answered_with("Push the commit only", self.TWO_STEPS)
        self.assert_refused(self.run_gh(self.issue()),
                            "the selected option 'Push the commit only' does not approve a post")

    def test_an_option_that_posts_but_says_not_is_refused(self):
        self.answered_with("Post the comments, not the commit", ("Post the comments, not the commit", "Do nothing"))
        self.assert_refused(self.run_gh(self.issue()),
                            "the selected option 'Post the comments, not the commit' does not approve a post")

    def test_the_document_must_show_his_answer_too(self):
        self.fixture(v2=doc_text(block=ANSWERED.replace("sami@theorem.dev", "agent@theorem.dev")))
        self.assert_refused(self.run_gh(self.issue()), "shows the ask answered by agent@theorem.dev")

    def test_a_body_that_differs_from_the_approved_draft_is_refused(self):
        changed = self.tmp / "changed.md"
        changed.write_text(BODY + "\n\nOne more line he never saw.\n")
        self.assert_refused(self.run_gh(self.issue(body_file=changed)),
                            f"the body is not, exactly, the text between two `---` lines in {DOC}@v2")

    def test_the_draft_is_the_one_he_answered_not_a_later_edit(self):
        # An edit after his answer (v2 here) changes the live document, not what he approved (v1).
        self.fixture(ask=ask_text(), v1=doc_text(block=ANSWERED),
                     v2=doc_text(body=BODY + "\n\nAdded later.", block=ANSWERED))
        changed = self.tmp / "later.md"
        changed.write_text(BODY + "\n\nAdded later.\n")
        self.assert_refused(self.run_gh(self.issue(body_file=changed)), f"{DOC}@v1")

    def test_a_title_that_differs_from_the_approved_draft_is_refused(self):
        self.assert_refused(self.run_gh(self.issue(title="Another title")),
                            "the title 'Another title' is not the `Title:` line of that draft")

    def test_a_flag_the_draft_does_not_cover_is_refused(self):
        self.assert_refused(self.run_gh(self.issue() + ["--label", "bug"]),
                            "`--label` is not checked against the approved draft")

    def test_a_repository_the_ask_does_not_name_is_refused(self):
        # o/up is only the start of o/upstream, which the ask names.
        for repo in ("o/elsewhere", "o/up"):
            argv = ["issue", "create", "-R", repo, "--title", TITLE, "--body-file", str(self.body_file)]
            result = self.run_gh(argv)
            self.assertEqual(result.returncode, 1, repo)
            self.assertIn(f"neither the ask nor its draft ({DOC}@v2) names {repo}", result.stderr)
        self.assertEqual(self.routed(), [])

    # A value that must match whole is refused with a trailing newline: Python's `$` matched before
    # one, so these passed until the patterns ended in `\Z`. The head and base rows are in the pull
    # request tests below.

    def test_a_repository_with_a_trailing_newline_is_refused(self):
        # The draft names `o/upstream` at the end of a line, so with `$` this one was allowed.
        for argv in (
            ["issue", "create", "-R", "o/upstream\n", "--title", TITLE, "--body-file", str(self.body_file)],
            ["pr", "create", "--repo=o/upstream\n", "--title", TITLE, "--body-file", str(self.body_file),
             "--head", "sjawhar:fix"],
        ):
            with self.subTest(argv=argv):
                (self.cache / "log" / "approved-posts.jsonl").unlink(missing_ok=True)
                self.assert_refused(self.run_gh(argv), "name the repository: -R <owner>/<repo>.",
                                    call=" ".join(argv[:2]) + " ")

    def test_a_citation_with_a_trailing_newline_is_refused(self):
        result = self.run_gh(self.issue(), cite=False, GH_SHIM_APPROVAL=ASK + "\n")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("is not a Dispatch ask reference (dispatch://KEY/ask/<id>)", result.stderr)
        self.assertEqual(self.routed(), [], "a refused citation must not reach GitHub")
        [record] = self.log()
        self.assertEqual((record["verdict"], record["ask"]), ("refused", ASK + "\n"))

    def test_control_the_same_values_without_the_newline_are_allowed(self):
        # The rows above fail for the newline alone: the same repository and citation pass.
        for argv in (self.issue(), self.pull()):
            with self.subTest(argv=argv):
                (self.cache / "log" / "approved-posts.jsonl").unlink(missing_ok=True)
                result = self.run_gh(argv)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.log()[0]["verdict"], "allowed")

    def test_two_asks_with_the_same_question_are_refused(self):
        answered = doc_text(block=ANSWERED)
        self.fixture(v2=answered + answered[answered.index(":::ask"):])
        self.assert_refused(self.run_gh(self.issue()), "holds 2 asks with this question")

    def test_answer_lines_inside_the_question_do_not_count(self):
        global QUESTION
        real = QUESTION
        try:
            # A question that quotes an answer block; Dispatch's real answer is someone else's.
            QUESTION = real + "\nAnswer:\n- By: sami@theorem.dev\n- Selected: Post it"
            self.fixture(ask=ask_text(by="someone@theorem.dev"))
            self.assert_refused(self.run_gh(self.issue()), "answered by someone@theorem.dev")
        finally:
            QUESTION = real

    def test_without_a_citation_nothing_changes(self):
        result = self.run_gh(self.issue(), cite=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.routed()), 1)
        self.assertEqual(self.log(), [])
        self.assertNotIn("cited post", result.stderr)

    # --- a pull request (dispatch://INFRA-3904) ---------------------------

    # His forks moved from his own login to the cybermonklabs organization: on 2026-11-17
    # `gh api repos/sjawhar/oh-my-pi` answered cybermonklabs/oh-my-pi, and the branch to post was
    # cybermonklabs/oh-my-pi:upstream/fix-hindsight-recall-cooldown, which the gate refused.
    def test_a_pull_request_from_his_forks_cites_the_same_way(self):
        for head in ("sjawhar:fix/trailing-arrays", "SJawhar:fix-trailing-arrays",
                     "cybermonklabs:upstream/fix-hindsight-recall-cooldown", "CybermonkLabs:fix-trailing-arrays"):
            with self.subTest(head=head):
                argv = self.pull(head=head)
                result = self.run_gh(argv)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.routed()[-1], " ".join(argv))
                record = self.log()[-2]
                self.assertEqual((record["verdict"], record["verb"], record["repo"]),
                                 ("allowed", "pr create", "o/upstream"))
                self.assertEqual((record["head"], record["base"]), (head, "main"))

    def test_a_pull_request_takes_its_flags_in_either_spelling(self):
        for argv, base in (
            (["pr", "create", "--repo=o/upstream", "-t", TITLE, "-F", str(self.body_file),
              "--head=sjawhar:fix/trailing-arrays"], ""),
            (["pr", "create", "-R", "o/upstream", f"--title={TITLE}", "-b", BODY,
              "--head", "sjawhar:fix/trailing-arrays", "-B", "main"], "main"),
            (["pr", "create", "-R", "o/upstream", "--title", TITLE, "--body", BODY,
              "--head", "sjawhar:fix/trailing-arrays", "--base=main"], "main"),
        ):
            with self.subTest(argv=argv):
                (self.cache / "log" / "approved-posts.jsonl").unlink(missing_ok=True)
                result = self.run_gh(argv)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((self.log()[0]["verdict"], self.log()[0]["base"]), ("allowed", base))

    def test_a_body_given_both_ways_is_refused(self):
        # gh 2.98.0 posts the file and drops --body whatever their order, so the text checked
        # could be the one not posted.
        changed = self.tmp / "changed.md"
        changed.write_text("Text he was not shown.\n")
        for argv, call in (
            (self.pull("--body", BODY), "pr create -R o/upstream"),
            (["pr", "create", "-R", "o/upstream", "--title", TITLE, "--body-file", str(changed),
              "--body", BODY, "--head", "sjawhar:fix"], "pr create -R o/upstream"),
            (self.issue() + ["--body", BODY], "issue create -R o/upstream"),
        ):
            with self.subTest(argv=argv):
                (self.cache / "log" / "approved-posts.jsonl").unlink(missing_ok=True)
                self.assert_refused(self.run_gh(argv), "pass the body once", call=call)

    def test_the_short_head_flag_is_refused(self):
        # knives 3.2.3 reads only --head, and in a jj checkout adds its own, which gh takes over -H.
        argv = ["pr", "create", "-R", "o/upstream", "--title", TITLE,
                "--body-file", str(self.body_file), "-H", "sjawhar:fix"]
        self.assert_refused(self.run_gh(argv), "write the head as --head <owner>:<branch>, not -H",
                            call="pr create -R o/upstream")

    def test_a_title_that_reads_dash_h_is_not_the_flag(self):
        self.fixture(v2=doc_text(title="-H", block=ANSWERED))
        argv = ["pr", "create", "-R", "o/upstream", "--title", "-H", "--body-file", str(self.body_file),
                "--head", "sjawhar:fix"]
        result = self.run_gh(argv)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.routed(), [" ".join(argv)])

    def test_a_pull_request_without_a_title_is_refused(self):
        argv = ["pr", "create", "-R", "o/upstream", "--body-file", str(self.body_file),
                "--head", "sjawhar:fix"]
        self.assert_refused(self.run_gh(argv), "a pull request needs its title in -t/--title",
                            call="pr create -R o/upstream")

    def test_a_base_that_is_not_a_branch_name_is_refused(self):
        for base in ("", "ma in", "main\n"):
            with self.subTest(base=base):
                (self.cache / "log" / "approved-posts.jsonl").unlink(missing_ok=True)
                self.assert_refused(self.run_gh(self.pull("--base", base)), "is not a branch name",
                                    call="pr create -R o/upstream")

    def test_a_pull_request_body_or_title_he_was_not_shown_is_refused(self):
        changed = self.tmp / "changed.md"
        changed.write_text(BODY + "\n\nOne more line he never saw.\n")
        argv = ["pr", "create", "-R", "o/upstream", "--title", TITLE, "--body-file", str(changed),
                "--head", "sjawhar:fix/trailing-arrays"]
        self.assert_refused(self.run_gh(argv), "the body is not, exactly, the text between two `---` lines",
                            call="pr create -R o/upstream")

    def test_a_pull_request_title_he_was_not_shown_is_refused(self):
        self.assert_refused(self.run_gh(self.pull(title="Another title")),
                            "the title 'Another title' is not the `Title:` line of that draft",
                            call="pr create -R o/upstream")

    def test_a_head_that_is_not_his_own_fork_is_refused(self):
        not_his = "is not a branch of one of sjawhar's own forks"
        for head, why in (
            ("someone:fix/trailing-arrays", f"the head someone:fix/trailing-arrays {not_his}"),
            ("o:fix/trailing-arrays", f"the head o:fix/trailing-arrays {not_his}"),
            ("sjawhar-bot:fix", f"the head sjawhar-bot:fix {not_his}"),
            # A third owner: the upstream of the fork, and names that only start like the org's.
            ("can1357:upstream/fix-hindsight-recall-cooldown",
             f"the head can1357:upstream/fix-hindsight-recall-cooldown {not_his}"),
            ("cybermonklabs-bot:fix", f"the head cybermonklabs-bot:fix {not_his}"),
            ("cybermonk:fix", f"the head cybermonk:fix {not_his}"),
            ("fix/trailing-arrays", "names its head as --head <owner>:<branch>"),
            ("sjawhar:", "names its head as --head <owner>:<branch>"),
            ("cybermonklabs:", "names its head as --head <owner>:<branch>"),
            ("sjawhar:fix with space", "names its head as --head <owner>:<branch>"),
            ("sjawhar:fix/trailing-arrays\n", "names its head as --head <owner>:<branch>"),
            ("cybermonklabs:fix/trailing-arrays\n", "names its head as --head <owner>:<branch>"),
        ):
            with self.subTest(head=head):
                (self.cache / "log" / "approved-posts.jsonl").unlink(missing_ok=True)
                self.assert_refused(self.run_gh(self.pull(head=head)), why, call="pr create -R o/upstream")

    def test_a_refused_head_names_every_owner_it_may_have(self):
        self.assert_refused(self.run_gh(self.pull(head="someone:fix")),
                            "as --head <owner>:<branch> with the owner sjawhar or cybermonklabs",
                            call="pr create -R o/upstream")

    def test_a_pull_request_without_a_head_is_refused(self):
        argv = ["pr", "create", "-R", "o/upstream", "--title", TITLE, "--body-file", str(self.body_file)]
        self.assert_refused(self.run_gh(argv), "names its head as --head <owner>:<branch>",
                            call="pr create -R o/upstream")

    def test_a_pull_request_flag_the_draft_does_not_cover_is_refused(self):
        for flag in (["--draft"], ["--label", "bug"], ["--fill"], ["--reviewer", "x"], ["-d"]):
            with self.subTest(flag=flag):
                (self.cache / "log" / "approved-posts.jsonl").unlink(missing_ok=True)
                self.assert_refused(self.run_gh(self.pull(*flag)),
                                    f"`{flag[0]}` is not checked against the approved draft",
                                    call="pr create -R o/upstream")

    def test_a_pull_request_takes_no_target(self):
        self.assert_refused(self.run_gh(self.pull("12")), "unexpected argument `12`",
                            call="pr create -R o/upstream")

    def test_head_and_base_belong_to_a_pull_request_only(self):
        for flag in (["--head", "sjawhar:x"], ["-B", "main"]):
            with self.subTest(flag=flag):
                (self.cache / "log" / "approved-posts.jsonl").unlink(missing_ok=True)
                self.assert_refused(self.run_gh(self.issue() + flag),
                                    f"`{flag[0]}` is not checked against the approved draft")


class CitedPostOnTheUpstreamToken(CitedPost):
    """Every case above again, for an owner with no GitHub App installation, through the whole shim.

    Such an owner's write goes out on Sami's upstream token, and the fallback-write gate
    (dispatch://INFRA-2006) refuses it unless the citation passed. The citation is checked on the
    outermost pass; the same call then runs the shim again (the pass that keeps stderr, and the read
    cache's re-entry), and until 2026-11-16 none of those passes knew the check had passed, so the
    gate refused every such post after an `allowed` verdict (dispatch://INFRA-3904: no `result` line
    with rc 0 in the log since the gate landed). Here gh-app-token names the fallback, knives hands
    the call to gh with the upstream token, as it does for such an owner, and gh is a stub that
    records what reached it.
    """

    def setUp(self):
        super().setUp()
        self.route_on_the_upstream_token()

    def route_on_the_upstream_token(self):
        self.source.write_text("fallback-personal-token\n")
        # One file per call, each argument NUL-ended, so argument boundaries survive the record.
        self.sent = self.tmp / "gh-calls"
        self.sent.mkdir()
        executable(self.bin / "gh", f'printf "%s\\0" "$GH_TOKEN" "$@" > "$(mktemp -p {self.sent})"\n'
                   'echo https://github.com/o/upstream/issues/12')
        # knives hands gh the route's token in GH_TOKEN, as it does for such an owner; the value
        # is a placeholder, set without a literal assignment the public leak check reads as a key.
        executable(self.bin / "knives",
                   '[ "$1" = gh ] && shift; [ "$1" = -- ] && shift\n'
                   f'printf "%s\\n" "$*" >> {self.calls}\n'
                   'printf -v GH_TOKEN %s upstream-route; export GH_TOKEN\n'
                   'exec "$KNIVES_REAL_GH" "$@"')

    def sent_calls(self):
        return [path.read_bytes().decode().split("\0")[:-1] for path in self.sent.iterdir()]

    def assert_sent(self, result, argv, logged_result=True):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "https://github.com/o/upstream/issues/12")
        self.assertEqual(self.sent_calls(), [["upstream-route", *argv]])
        records = self.log()
        self.assertEqual(records[0]["verdict"], "allowed")
        if logged_result:
            self.assertEqual((records[1]["ev"], records[1]["rc"]), ("result", 0))

    def test_without_a_citation_nothing_changes(self):
        # Overrides the App case: on the upstream token an uncited write is the gate's to refuse.
        result = self.run_gh(self.issue(), cite=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("which the GitHub App is not installed on", result.stderr)
        self.assertIn("GH_SHIM_APPROVAL=dispatch://<KEY>/ask/<id>", result.stderr)
        self.assertEqual((self.routed(), self.sent_calls(), self.log()), ([], [], []))

    def test_an_approved_issue_reaches_gh_on_the_upstream_token(self):
        self.assert_sent(self.run_gh(self.issue()), self.issue())

    def test_an_approved_pull_request_reaches_gh_on_the_upstream_token(self):
        self.assert_sent(self.run_gh(self.pull()), self.pull())

    def test_an_approved_pull_request_from_the_cybermonklabs_fork_reaches_gh_on_the_upstream_token(self):
        argv = self.pull(head="cybermonklabs:upstream/fix-hindsight-recall-cooldown")
        self.assert_sent(self.run_gh(argv), argv)

    def test_with_the_read_cache_on_it_reaches_gh_through_the_cache(self):
        # The cache is on in every session; its re-entry is one more pass of the same call.
        self.assert_sent(self.run_gh(self.pull(), GH_READ_CACHE=None), self.pull())

    def test_when_the_first_pass_cannot_keep_stderr_the_cache_pass_still_counts(self):
        # No temporary directory: the outermost pass runs on without its stderr-keeping re-run, so
        # the only later pass is the cache's re-entry, which is not marked GH_SHIM_INNER.
        self.assert_sent(self.run_gh(self.pull(), GH_READ_CACHE=None, TMPDIR=str(self.tmp / "absent")),
                         self.pull(), logged_result=False)

    def test_a_refused_citation_sends_nothing_on_the_upstream_token(self):
        result = self.run_gh(self.pull(head="someone:fix"))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual((self.routed(), self.sent_calls()), ([], []))


# --- a kind of post (dispatch://INFRA-3412) -----------------------------------

KIND_ASK = "dispatch://BET-422/ask/48d87cd6-f3d0-44d5-9a79-30c8b38f28d0"
# The ask BET-422 opened next, with the same question; it is not in KIND_APPROVALS.
OTHER_ASK = "dispatch://BET-422/ask/57841a7e-d374-491a-b367-0f61b0d420b7"
# The ask's question in short: it names the repository as rocq-prover/rocq#22456, as the real one does.
KIND_QUESTION = "May this report go to the two authors of rocq-prover/rocq#22456, and who sends it?"
# The first sentences of his answer, word for word (2026-11-12T19:33:21Z).
KIND_ANSWER = (
    "I need to stop being asked the same question. The answer is the same every single time for rock. "
    "Just open an issue and a PR. And you have to follow the three-step process for anything going to "
    "the Rock repo."
)
APPROVES = "`gh issue create` and `gh pr create` on rocq-prover/rocq"
ROCQ_TITLE = "An issue of the kind his answer approves"
ROCQ_BODY = "### Description of the problem\n\nA body no draft of the ask holds.\n"
ROCQ_SHA = hashlib.sha256(ROCQ_BODY.encode()).hexdigest()
VERDICT = "dispatch://BET-1269/comment/5f0c2a7e-9b1d-4c3e-8a6f-2d4b7e9c1a03"
REVIEWER = "01a1f00d-0000-7000-8000-00000000beef"


def kind_ask_text(state="answered", by="sami@theorem.dev", selected="Send it as a public issue instead",
                  text=KIND_ANSWER, question=KIND_QUESTION, ref=KIND_ASK):
    """`dispatch read --ref` of the ask in the shape Dispatch printed it on 2026-11-17: each option
    with its block reference, the answer with its own, and no document."""
    b = f"{ref}#b-"
    answer = "" if state != "answered" else (
        f"Answer:\n- By: {by}\n- Selected: {selected}\n- Text: {text}\n- Reference: {b}b6\n")
    return (
        f"Question: {question}\nOptions:\n"
        f"- Send as written, by you — You send the report. ({b}b2)\n"
        f"- Send it as a public issue instead — The report goes out as an issue. ({b}b3)\n"
        f"- Change it (say what) — The changed text goes back through a fresh review. ({b}b4)\n"
        f"- Do not send — Nothing goes out. ({b}b5)\n"
        f"State: {state}\nAsk id: {ref.rsplit('/', 1)[1]}\n{answer}Replies:\n- none\n"
        f"Node: ask {ref} · made by session 01a1c95f-5b41-7581-a9fe-265ede4e53bd at "
        "2026-11-12T19:17:44.578071Z · part of dispatch://BET-422\nReferenced by:\n- none\n"
        f"Links:\n- mentions issue dispatch://BET-885 (an issue · 2026-11-12T19:17:44Z) (block {b}b1)\n"
    )


def verdict_text(author=f"session {REVIEWER}", first=None, title=ROCQ_TITLE, sha=ROCQ_SHA, word="APPROVE"):
    """`dispatch read --ref` of a reviewer's verdict comment, in the shape Dispatch prints a comment."""
    first = f"VERDICT: {word} at sha256:{sha}" if first is None else first
    body = first + "\n\n" + (f"Title: {title}\n\n" if title else "") + \
        "The reviewer removed one sentence on a release the branch does not hold."
    return (
        f"Comment:\n{VERDICT.rsplit('/', 1)[1]} · {author}\nBody: {body}\nReply chain:\n- none\n"
        f"Node: comment {VERDICT} · made by {author} at 2026-11-17T10:00:00Z · part of dispatch://BET-1269\n"
        "Referenced by:\n- none\nLinks:\n- none\n"
    )


class KindOfPost(unittest.TestCase):
    """A citation of an ask whose answer approves a kind of post, with no fixed text.

    On dispatch://BET-422/ask/48d87cd6 Sami answered "Just open an issue and a PR. And you have to
    follow the three-step process for anything going to the Rock repo", and the shim still refused
    the issue, for a body that was not a draft of that ask (dispatch://INFRA-3412). The script lists
    that ask in KIND_APPROVALS as `gh issue create` and `gh pr create` on rocq-prover/rocq: such a
    post passes for that repository and those kinds only, and only with the verdict of a reviewer
    session that did not write it, a Dispatch comment named in GH_SHIM_VERDICT whose first line
    names the body's SHA-256. The harness is CitedPost's; its dispatch stub answers these asks and
    the verdict too.
    """

    tearDown = CitedPost.tearDown
    fixture = CitedPost.fixture
    run_gh = CitedPost.run_gh
    routed = CitedPost.routed
    log = CitedPost.log

    def setUp(self):
        CitedPost.setUp(self)
        executable(self.front / "dispatch",
                   f'd={self.fixtures}\n'
                   'case "$1 $3" in\n'
                   f'  "read {KIND_ASK}") cat "$d/kind-ask.txt" ;;\n'
                   f'  "read {OTHER_ASK}") cat "$d/other-ask.txt" ;;\n'
                   f'  "read {VERDICT}") cat "$d/verdict.txt" ;;\n'
                   f'  "read {ASK}") cat "$d/ask.txt" ;;\n'
                   f'  "read {DOC}") echo "Document: OSSV-1 / issue.md"; echo "Versions: 2" ;;\n'
                   f'  "doc-read {DOC}@v1") cat "$d/v1.md" ;;\n'
                   f'  "doc-read {DOC}@v2") cat "$d/v2.md" ;;\n'
                   '  *) echo "dispatch stub: no fixture for $*" >&2; exit 1 ;;\n'
                   'esac')
        self.rocq_body = self.tmp / "rocq-body.md"
        self.rocq_body.write_text(ROCQ_BODY)
        self.kind()

    def kind(self, ask=None, verdict=None):
        (self.fixtures / "kind-ask.txt").write_text(ask or kind_ask_text())
        (self.fixtures / "other-ask.txt").write_text(kind_ask_text(ref=OTHER_ASK))
        (self.fixtures / "verdict.txt").write_text(verdict or verdict_text())

    def post(self, argv, verdict=VERDICT, ask=KIND_ASK, **extra):
        return self.run_gh(argv, cite=False, GH_SHIM_APPROVAL=ask, GH_SHIM_VERDICT=verdict, **extra)

    def issue(self, repo="rocq-prover/rocq", title=ROCQ_TITLE):
        return ["issue", "create", "-R", repo, "--title", title, "--body-file", str(self.rocq_body)]

    def pull(self, repo="rocq-prover/rocq", title=ROCQ_TITLE):
        return ["pr", "create", "-R", repo, "--title", title, "--body-file", str(self.rocq_body),
                "--head", "sjawhar:rocqchk-forged-aliases", "--base", "master"]

    def assert_refused(self, result, why, call="issue create -R rocq-prover/rocq", ask=KIND_ASK):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"gh shim: REFUSED: `gh {call}", result.stderr)
        self.assertIn(why, result.stderr)
        self.assertEqual(self.routed(), [], "a refused citation must not reach GitHub")
        [record] = self.log()
        self.assertEqual((record["verdict"], record["ask"]), ("refused", ask))

    def fresh_log(self):
        (self.cache / "log" / "approved-posts.jsonl").unlink(missing_ok=True)

    # R1: the repository and the kinds his answer names, and only those.

    def test_an_issue_and_a_pull_request_of_the_kinds_his_answer_names_go_out(self):
        for argv in (self.issue(), self.pull(), self.issue(repo="Rocq-Prover/Rocq")):
            with self.subTest(argv=argv[:4]):
                self.fresh_log()
                result = self.post(argv)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.routed()[-1], " ".join(argv))
                allowed = self.log()[0]
                self.assertEqual((allowed["verdict"], allowed["ask"], allowed["repo"]),
                                 ("allowed", KIND_ASK, argv[3]))
                self.assertEqual((allowed["approves"], allowed["review"], allowed["reviewer"]),
                                 (APPROVES, VERDICT, REVIEWER))
                self.assertEqual(allowed["body_sha256"], ROCQ_SHA)
                self.assertIn(f"his answer approves {APPROVES}", result.stderr)
                self.assertIn(f"a reviewer's verdict {VERDICT} (session {REVIEWER})", result.stderr)

    def test_a_kind_his_answer_does_not_name_is_refused_naming_what_it_approves(self):
        for argv in (
            ["issue", "comment", "https://github.com/rocq-prover/rocq/issues/22637",
             "--body-file", str(self.rocq_body)],
            ["pr", "comment", "22644", "-R", "rocq-prover/rocq", "--body-file", str(self.rocq_body)],
        ):
            with self.subTest(verb=argv[:2]):
                self.fresh_log()
                self.assert_refused(
                    self.post(argv),
                    f"his answer approves {APPROVES}, and no other post; this call is "
                    f"`gh {argv[0]} {argv[1]}` on rocq-prover/rocq",
                    call=" ".join(argv[:3]))

    # R4: a repository the answer does not name is refused, and the refusal names what it approves.

    def test_a_repository_his_answer_does_not_name_is_refused_naming_what_it_approves(self):
        # A prefix and an extension of the name, his fork, Rocq's old home, and another repository.
        for repo in ("rocq-prover/roc", "rocq-prover/rocq-elpi", "sjawhar/rocq", "coq/coq", "o/upstream"):
            for argv in (self.issue(repo=repo), self.pull(repo=repo)):
                with self.subTest(repo=repo, verb=argv[:2]):
                    self.fresh_log()
                    self.assert_refused(
                        self.post(argv),
                        f"his answer approves {APPROVES}, and no other post; this call is "
                        f"`gh {argv[0]} {argv[1]}` on {repo}",
                        call=f"{argv[0]} {argv[1]} -R {repo}")

    # R2: a verdict on record from a reviewer session that did not write the text.

    def test_without_a_reviewers_verdict_it_is_refused(self):
        for verdict in (None, ""):
            with self.subTest(verdict=verdict):
                self.fresh_log()
                self.assert_refused(self.post(self.issue(), verdict=verdict),
                                    "needs a fresh reviewer's verdict on record")
        self.assertIn(ROCQ_SHA, self.log()[0]["why"])

    def test_a_verdict_that_is_not_a_comment_reference_is_refused(self):
        for verdict in (KIND_ASK, VERDICT + "\n", "dispatch://BET-1269/comment/5f0c2a7e"):
            with self.subTest(verdict=verdict):
                self.fresh_log()
                self.assert_refused(self.post(self.issue(), verdict=verdict),
                                    "is not a Dispatch comment reference (dispatch://KEY/comment/<id>)")

    def test_a_verdict_written_by_the_posting_session_is_refused(self):
        # A subagent writes to Dispatch as its parent session, so its verdict reads as the poster's.
        for extra in ({"OMP_SESSION_ID": REVIEWER}, {"DISPATCH_SESSION_ID": REVIEWER},
                      {"PI_SESSION_ID": REVIEWER}, {"ENVOY_SESSION_ID": REVIEWER}):
            with self.subTest(extra=extra):
                self.fresh_log()
                self.assert_refused(self.post(self.issue(), **extra),
                                    f"was written by session {REVIEWER}, which is the session making this post")

    def test_a_session_with_no_id_cannot_tell_its_reviewer_apart(self):
        # An agent box with no session id set: the shim still checks the citation.
        self.assert_refused(self.post(self.issue(), OMP_SESSION_ID=None, AGENTBOX_SESSION="box-1"),
                            "this call runs under no session id")

    def test_a_verdict_a_person_wrote_is_not_a_reviewer_sessions(self):
        self.kind(verdict=verdict_text(author="user sami@theorem.dev"))
        self.assert_refused(self.post(self.issue()), "was written by user sami@theorem.dev, not by a session")

    def test_a_verdict_for_another_text_is_refused(self):
        later = self.tmp / "later.md"
        later.write_text(ROCQ_BODY + "\nA sentence added after the review.\n")
        argv = ["issue", "create", "-R", "rocq-prover/rocq", "--title", ROCQ_TITLE, "--body-file", str(later)]
        self.assert_refused(self.post(argv),
                            f"approves the text with SHA-256 {ROCQ_SHA}, and this body's is "
                            f"{hashlib.sha256(later.read_bytes()).hexdigest()}")

    def test_a_verdict_for_another_title_is_refused(self):
        for title, why in (("Another title", "names the title 'Another title', not"),
                           (None, "has no `Title: <title>` line")):
            with self.subTest(title=title):
                self.fresh_log()
                self.kind(verdict=verdict_text(title=title))
                self.assert_refused(self.post(self.issue()), why)

    def test_a_verdict_that_does_not_approve_or_does_not_open_with_its_line_is_refused(self):
        for verdict, why in (
            (verdict_text(word="CHANGES"), "is CHANGES, not APPROVE"),
            (verdict_text(first="Looks good.\nVERDICT: APPROVE at sha256:" + ROCQ_SHA),
             "does not begin with a `VERDICT: APPROVE at sha256:<64 hex>` line"),
            (verdict_text(first="VERDICT: APPROVE at " + ROCQ_SHA),
             "does not begin with a `VERDICT: APPROVE at sha256:<64 hex>` line"),
        ):
            with self.subTest(why=why, verdict=verdict[:80]):
                self.fresh_log()
                self.kind(verdict=verdict)
                self.assert_refused(self.post(self.issue()), why)

    # The other checks hold for a kind of post too.

    def test_the_ask_must_be_answered_by_him_with_an_approving_option(self):
        for ask, why in (
            (kind_ask_text(by="someone@theorem.dev"), "answered by someone@theorem.dev, not sami@theorem.dev"),
            (kind_ask_text(selected="Do not send"), "the selected option 'Do not send' does not approve a post"),
            (kind_ask_text(state="open"), "the ask is open, not answered"),
        ):
            with self.subTest(why=why):
                self.fresh_log()
                self.kind(ask=ask)
                self.assert_refused(self.post(self.issue()), why)

    def test_an_answer_without_the_words_the_list_reads_is_refused(self):
        # KIND_APPROVALS reads his words; an answer that does not say them approves nothing by kind.
        self.kind(ask=kind_ask_text(text="Post it."))
        self.assert_refused(self.post(self.issue()), "his answer does not say 'Just open an issue and a PR.'")

    def test_an_ask_that_does_not_name_the_repository_is_refused(self):
        self.kind(ask=kind_ask_text(question="May this report go to the authors of the alias check?"))
        self.assert_refused(self.post(self.issue()), "neither the ask nor its answer names rocq-prover/rocq")

    def test_a_pull_request_of_the_kind_still_opens_from_his_own_fork(self):
        argv = self.pull()
        argv[argv.index("--head") + 1] = "someone:rocqchk-forged-aliases"
        self.assert_refused(self.post(argv), "is not a branch of one of sjawhar's own forks",
                            call="pr create -R rocq-prover/rocq")

    # R3: the word-for-word citation works as before, and an ask id not in KIND_APPROVALS gets only
    # that path.

    def test_an_ask_id_not_in_the_list_gets_only_the_word_for_word_path(self):
        # BET-422's next ask, with the same question, the same answer and a verdict: its id is not in
        # KIND_APPROVALS, so the body must be a draft between two `---` lines of it, and is not.
        self.assert_refused(self.post(self.issue(), ask=OTHER_ASK),
                            f"the body is not, exactly, the text between two `---` lines in {OTHER_ASK}",
                            ask=OTHER_ASK)
        self.assertNotIn("approves", self.log()[0])

    def test_an_ask_not_in_the_list_still_needs_the_exact_text(self):
        # OSSV-1's ask answered in the same words: only KIND_APPROVALS makes an answer approve a kind
        # of post, so the text he was shown is still the whole approval, verdict or not.
        self.fixture(ask=ask_text(text=KIND_ANSWER))
        changed = self.tmp / "changed.md"
        changed.write_text(BODY + "\n\nOne more line he never saw.\n")
        result = self.run_gh(CitedPost.issue(self, body_file=changed), GH_SHIM_VERDICT=VERDICT)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"the body is not, exactly, the text between two `---` lines in {DOC}@v2", result.stderr)
        self.assertEqual(self.routed(), [])

    def test_the_exact_text_needs_no_verdict(self):
        result = self.run_gh(CitedPost.issue(self))
        self.assertEqual(result.returncode, 0, result.stderr)
        [allowed, _] = self.log()
        self.assertEqual((allowed["verdict"], allowed["draft"]), ("allowed", f"{DOC}@v2"))
        self.assertNotIn("review", allowed)


class KindOfPostOnTheUpstreamToken(KindOfPost):
    """Every case above again for an owner the App does not cover, rocq-prover's case: the post
    goes out on the upstream token, and the fallback-write gate lets it through only because the
    citation passed."""

    route_on_the_upstream_token = CitedPostOnTheUpstreamToken.route_on_the_upstream_token
    sent_calls = CitedPostOnTheUpstreamToken.sent_calls

    def setUp(self):
        super().setUp()
        self.route_on_the_upstream_token()

    def test_an_approved_kind_of_post_reaches_gh_on_the_upstream_token(self):
        for argv in (self.issue(), self.pull()):
            with self.subTest(verb=argv[:2]):
                self.fresh_log()
                for sent in self.sent.iterdir():
                    sent.unlink()
                result = self.post(argv, GH_READ_CACHE=None)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.sent_calls(), [["upstream-route", *argv]])
                records = self.log()
                self.assertEqual(records[0]["verdict"], "allowed")
                self.assertEqual((records[1]["ev"], records[1]["rc"]), ("result", 0))

    def test_a_refused_kind_of_post_sends_nothing_on_the_upstream_token(self):
        result = self.post(["issue", "comment", "https://github.com/rocq-prover/rocq/issues/22637",
                            "--body-file", str(self.rocq_body)])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual((self.routed(), self.sent_calls()), ([], []))


if __name__ == "__main__":
    unittest.main()
