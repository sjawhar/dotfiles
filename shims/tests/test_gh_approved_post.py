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

    def test_a_pull_request_from_his_fork_cites_the_same_way(self):
        for head in ("sjawhar:fix/trailing-arrays", "SJawhar:fix-trailing-arrays"):
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
        for head, why in (
            ("someone:fix/trailing-arrays", "the head someone:fix/trailing-arrays is not a branch of sjawhar's own fork"),
            ("o:fix/trailing-arrays", "the head o:fix/trailing-arrays is not a branch of sjawhar's own fork"),
            ("sjawhar-bot:fix", "the head sjawhar-bot:fix is not a branch of sjawhar's own fork"),
            ("fix/trailing-arrays", "names its head as --head <owner>:<branch>"),
            ("sjawhar:", "names its head as --head <owner>:<branch>"),
            ("sjawhar:fix with space", "names its head as --head <owner>:<branch>"),
            ("sjawhar:fix/trailing-arrays\n", "names its head as --head <owner>:<branch>"),
        ):
            with self.subTest(head=head):
                (self.cache / "log" / "approved-posts.jsonl").unlink(missing_ok=True)
                self.assert_refused(self.run_gh(self.pull(head=head)), why, call="pr create -R o/upstream")

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


if __name__ == "__main__":
    unittest.main()
