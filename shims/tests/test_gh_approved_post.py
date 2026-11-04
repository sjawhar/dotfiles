#!/usr/bin/env python3
"""A post Sami instructed goes out as him only on a citation that holds: GH_SHIM_APPROVAL names
the Dispatch ask he answered, and shims/gh (through scripts/gh-approved-post) checks the answer
and that the title and body are the draft he was shown, logs the post, and only then lets the call
go on its route. A citation that does not hold refuses the call before it reaches GitHub.

On 2026-11-02 he answered dispatch://OSSV-232/ask/a7d1ac41-45c4-4504-a985-7c92998d2158 "Post it"
for an issue on google/oss-fuzz, and the issue still did not go out.

Dispatch is a stub that prints the outputs of `dispatch read` and `dispatch doc-read` in their
real shape; knives is a stub that records the call it would route and prints an issue URL.
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


def ask_text(state="answered", by="sami@theorem.dev", selected="Post it", text=None):
    answer = "" if state != "answered" else (
        f"Answer:\n- By: {by}\n- Selected: {selected}\n" + (f"- Text: {text}\n" if text else ""))
    return (
        f"Question: {QUESTION}\nOptions:\n- Post it — post the title and body above, unchanged.\n"
        f"- Do not post — nothing goes out.\nState: {state}\nAsk id: a7d1ac41-45c4-4504-a985-7c92998d2158\n"
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
        self.body_file = self.tmp / "body.md"
        self.body_file.write_text(BODY + "\n")
        self.fixture()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fixture(self, ask=None, v1=None, v2=None):
        (self.fixtures / "ask.txt").write_text(ask or ask_text())
        (self.fixtures / "v1.md").write_text(v1 or doc_text())
        (self.fixtures / "v2.md").write_text(v2 or doc_text(block=ANSWERED))

    def run_gh(self, argv, cite=True):
        env = {
            "HOME": str(self.tmp), "USER": os.environ.get("USER", "u"), "LANG": "C.UTF-8",
            "PATH": os.pathsep.join([str(self.front), str(SHIMS), str(self.bin), "/usr/bin", "/bin"]),
            "TMPDIR": str(self.tmp), "DOTFILES_DIR": str(DOTFILES), "OMP_SESSION_ID": "sess-1",
            "GH_READ_CACHE": "0", "GH_READ_CACHE_LOG": "0", "GH_READ_CACHE_DIR": str(self.cache),
            "PUBLIC_LEAK_CHECK": "/bin/true",
        }
        if cite:
            env["GH_SHIM_APPROVAL"] = ASK
        return subprocess.run([str(SHIMS / "gh"), *argv], env=env, capture_output=True, text=True,
                              cwd=self.tmp, timeout=60, check=False)

    def issue(self, title=TITLE, body_file=None):
        return ["issue", "create", "-R", "o/upstream", "--title", title,
                "--body-file", str(body_file or self.body_file)]

    def routed(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def log(self):
        path = self.cache / "log" / "approved-posts.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def assert_refused(self, result, why):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"gh shim: REFUSED: `gh issue create -R o/upstream", result.stderr)
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


if __name__ == "__main__":
    unittest.main()
