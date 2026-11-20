#!/usr/bin/env python3
"""shims/gh refuses a gh call that would send a listed leak term to a public repository.

The leak-term check (scripts/leak-terms-check) already refuses such a term in a commit and in a push to a public
repository. Text sent through gh passed no check: on 2026-11-17 an agent's `gh pr edit` put a term of the private list
into a public pull request's body, and GitHub keeps that text in the body's edit history (dispatch://INFRA-4102).

Here a fake gh, first on PATH, records each call it gets (its arguments, its stdin and what kind of file that stdin
is) and answers the visibility reads of scripts/github-repo-visibility from a directory of answers. The term list is
the encrypted test list of leak_terms_fixture.py, so the canary below is a listed term and no private term appears
anywhere. A refusal names the field, the line in it, the text that matched as it is written, the list that holds the
term and the line it is on (dispatch://INFRA-4383, R2): the caller wrote that text, so showing it copies nothing to a
new place. Each refusal is shown with its control: the same text to a private repository goes out unchanged, with the
same stdin."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

from leak_terms_fixture import (ALLOWED_TEST_TOKENS, REGISTRY_TEST_TERMS, SCOPED_TEST_TERMS, TEST_TERMS,
                                fixture_environment)

SHIMS = Path(__file__).resolve().parents[1]
DOTFILES = SHIMS.parent
GH = SHIMS / "gh"
CHECK = DOTFILES / "scripts" / "leak-terms-check"
CANARY = TEST_TERMS[2]
GLOBAL = "the global list"
# A term the list holds for cybermonklabs/* repositories only (leak_terms_fixture.SCOPED_TEST_TERMS).
SCOPE, (SCOPED,) = next(iter(SCOPED_TEST_TERMS.items()))
# The canary as a body would carry it: another letter case, in a sentence.
TEXT = f"See {CANARY.upper()} for the details.\n"
CLEAN = "Reviewed at abc123; nothing to add.\n"
PUBLIC = "o/pub"
PRIVATE = "o/priv"
UNREAD = "o/unread"
# The release repository of knives's ygo-fork entry, whose own name holds `theorem-labs`, a term of that entry's list.
YGO = "theorem-labs/ygo"
YGO_LIST = "the knives ygo-fork list"
# The one target of every raw GraphQL mutation (GH_GRAPHQL_TARGET): the check reads no mutation as going anywhere else.
MUTATION = "a raw GraphQL mutation, which is read as public"
# The documents of the tests that read a call as GraphQL or not by where gh sends it, each an `-f` value: a raw mutation
# whose body spells the canary's first letter with a GraphQL `\u` escape, which GitHub decodes, so only a call read as
# GraphQL is refused for it; and a query, a read, whose search holds the canary as written.
ESCAPED_MUTATION = ('query=mutation { addComment(input: {subjectId: "I_1", body: "see \\u%04x%s"}) '
                    '{ clientMutationId } }' % (ord(CANARY[0]), CANARY[1:]))
SEARCH_QUERY = 'query=query { search(query: "see %s", type: ISSUE, first: 1) { issueCount } }' % CANARY
# The variables that make a shim read its caller as an agent (shims/AGENTS.md, "Who is an agent").
AGENT_MARKERS = ("OMP_SESSION_ID", "PI_SESSION_FILE", "PI_TOOL_BRIDGE_URL", "AGENTBOX_SESSION", "CLAUDECODE",
                 "ENVOY_SESSION_ID", "AGENT_SHIM_AGENT", "SSH_CONNECTION", "SSH_TTY")

FAKE_GH = r"""#!/bin/bash
# A visibility read (scripts/github-repo-visibility): answered from $VIS_DIR/<owner>_<repo>, or refused.
if [[ "${1:-}" == api && "${2:-}" == repos/* && "${3:-}" == --jq && "${4:-}" == .visibility ]]; then
    printf '%s\n' "$2" >> "$VIS_LOG"
    answer="$VIS_DIR/${2#repos/}"
    answer="${answer%/*}_${answer##*/}"
    [[ -f "$answer" ]] || exit 1
    cat -- "$answer"
    exit 0
fi
n=$(find "$CALLS" -name '*.args' | wc -l)
printf '%s\0' "$@" > "$CALLS/$n.args"
stat -L -c %F /dev/stdin > "$CALLS/$n.kind"
# What gh would read from a --body-file other than `-` (a pipe can be read only once).
prev=""; for a in "$@"; do [[ "$prev" == --body-file && "$a" != - ]] && cat -- "$a" > "$CALLS/$n.body"; prev="$a"; done
cat > "$CALLS/$n.stdin"
exit 0
"""


def write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


class GhLeakTerms(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gh-leak-terms-"))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        write_executable(self.bin / "gh", FAKE_GH)
        self.calls = self.tmp / "calls"
        self.calls.mkdir()
        self.vis = self.tmp / "visibility"
        self.vis.mkdir()
        self.vis_log = self.tmp / "visibility.log"
        self.vis_log.touch()
        self.work = self.tmp / "work"
        self.work.mkdir()
        for name in ("home", "config", "cache", "t"):
            (self.tmp / name).mkdir()
        self.answer(PUBLIC, "public")
        self.answer(PRIVATE, "private")
        inherited = {
            key: value for key, value in os.environ.items()
            if key not in AGENT_MARKERS and not key.startswith(("PI_", "LEGION_", "GIT_CONFIG_", "GH_", "PUBLIC_LEAK_CHECK",
                                                                "DOTFILES_LEAK_TERMS_GH"))
        }
        self.env = {
            **inherited,
            **fixture_environment(),
            # The shim takes no gh above its own PATH entry (shims/AGENTS.md, "Adding to a shim"), so this checkout's
            # shims/, which a run inside the checkout has on PATH, is left out: the shim runs from outside PATH and
            # takes the fake gh first.
            "PATH": os.pathsep.join([str(self.bin), *(entry for entry in os.environ["PATH"].split(os.pathsep)
                                                      if Path(entry).resolve() != SHIMS.resolve())]),
            "HOME": str(self.tmp / "home"),
            "XDG_CONFIG_HOME": str(self.tmp / "config"),
            "XDG_CACHE_HOME": str(self.tmp / "cache"),
            "TMPDIR": str(self.tmp / "t"),
            "DOTFILES_DIR": str(DOTFILES),
            "KNIVES_GH_BYPASS": "1",
            "AGENT_SHIM_PERSON": "1",
            "CALLS": str(self.calls),
            "VIS_DIR": str(self.vis),
            "VIS_LOG": str(self.vis_log),
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers -----------------------------------------------------------------------------------------------

    def answer(self, slug: str, visibility: str) -> None:
        """Answer each visibility read of SLUG with VISIBILITY from now on: the leak check caches the answer it read
        last (`$XDG_CACHE_HOME/public-leak-check/<owner>_<repo>`), so that entry is deleted too."""
        (self.vis / slug.replace("/", "_")).write_text(visibility + "\n", encoding="utf-8")
        (self.tmp / "cache" / "public-leak-check" / slug.replace("/", "_")).unlink(missing_ok=True)

    def cached(self, slug: str) -> str:
        """The visibility the leak check's cache holds for SLUG, the answer it read last."""
        return (self.tmp / "cache" / "public-leak-check" / slug.replace("/", "_")).read_text(encoding="utf-8")

    def agent(self, **extra: str) -> dict[str, str]:
        """An omp session's environment: the agent block and the public text gate (a stub that passes) run before the
        term check."""
        env = {key: value for key, value in self.env.items() if key != "AGENT_SHIM_PERSON"}
        env.update({"OMP_SESSION_ID": "01a0fa64-b3a5-7415-b143-c2a338b5da8b", "PUBLIC_LEAK_CHECK": "/bin/true"})
        env.update(extra)
        return env

    def run_gh(self, argv: list[str], stdin: bytes = b"", env: dict[str, str] | None = None,
               cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run([str(GH), *argv], input=stdin, env=env or self.env, cwd=cwd or self.work,
                              capture_output=True, timeout=240, check=False)

    def gh_calls(self) -> list[tuple[list[str], bytes, str]]:
        """Each call the fake gh got, but visibility reads: its arguments, its stdin and the kind of file that was."""
        found = []
        for index in range(len(list(self.calls.glob("*.args")))):
            arguments = (self.calls / f"{index}.args").read_bytes().split(b"\0")[:-1]
            found.append(([a.decode("utf-8", "surrogateescape") for a in arguments],
                          (self.calls / f"{index}.stdin").read_bytes(),
                          (self.calls / f"{index}.kind").read_text(encoding="utf-8").strip()))
        return found

    def visibility_reads(self) -> list[str]:
        return self.vis_log.read_text(encoding="utf-8").splitlines()

    def file(self, name: str, text: str) -> str:
        path = self.work / name
        path.write_text(text, encoding="utf-8")
        return name

    def assert_shown(self, result: subprocess.CompletedProcess[bytes], field: str, matched: str,
                     term_list: str = GLOBAL, line: int | None = None) -> str:
        """The refusal has a line naming FIELD, the line in it (LINE, when given), MATCHED as the text wrote it (C-escaped,
        as the refusal prints it), the list that holds the term, and the line of the text it is on, which holds MATCHED;
        returns that line of the text."""
        stderr = result.stderr.decode("utf-8", "replace")
        number = r"\d+" if line is None else str(line)
        found = re.search(rf'^leak terms: gh [^\n]*: {re.escape(field)} line {number}: "{re.escape(matched)}" '
                          rf"\({re.escape(term_list)}\), in: ([^\n]*)$", stderr, re.MULTILINE)
        self.assertIsNotNone(found, stderr)
        assert found is not None
        self.assertIn(matched, found.group(1))
        return found.group(1)

    def assert_refused(self, result: subprocess.CompletedProcess[bytes], field: str, matched: str = CANARY.upper(),
                       term_list: str = GLOBAL, line: int | None = None) -> str:
        stderr = result.stderr.decode("utf-8", "replace")
        self.assertEqual(result.returncode, 2, stderr)
        self.assertEqual(self.gh_calls(), [], "gh ran although the text holds a listed term")
        self.assertIn("nothing was sent", stderr)
        return self.assert_shown(result, field, matched, term_list, line)

    def assert_sent(self, result: subprocess.CompletedProcess[bytes], argv: list[str], stdin: bytes = b"") -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        calls = self.gh_calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0][0], argv)
        self.assertEqual(calls[0][1], stdin)

    # --- every input form: refused for a public repository, sent unchanged to a private one ----------------------

    def forms(self, repo: str, text: str) -> list[tuple[str, list[str], bytes, str]]:
        """(name, argv, stdin, the field the refusal names) for each way gh takes text, all aimed at REPO."""
        f = self.file
        escaped = "".join(f"\\u{ord(c):04x}" for c in text)
        return [
            ("pr edit --body", ["pr", "edit", "11", "-R", repo, "--body", text], b"", "--body"),
            ("-R REPO pr comment", ["-R", repo, "pr", "comment", "11", "--body", text], b"", "--body"),
            ("pr --repo REPO comment", ["pr", "--repo", repo, "comment", "11", "--body", text], b"", "--body"),
            ("pr create --title", ["pr", "create", "-R", repo, "--head", "h", "--title", text, "--body", "x"], b"", "--title"),
            ("pr create -dF FILE", ["pr", "create", "-R", repo, "-t", "t", "-dF", f("a.md", text)], b"", "--body-file a.md"),
            ("pr comment --body-file FILE", ["pr", "comment", "11", "-R", repo, "--body-file", f("b.md", text)], b"",
             "--body-file b.md"),
            ("pr comment --body-file -", ["pr", "comment", "11", "-R", repo, "--body-file", "-"], text.encode(),
             "--body-file - (standard input)"),
            ("pr review -b", ["pr", "review", "11", "-R", repo, "--comment", "-b", text], b"", "--body"),
            ("pr merge --subject", ["pr", "merge", "11", "-R", repo, "--squash", "--subject", text], b"", "--subject"),
            ("pr close --comment", ["pr", "close", "11", "-R", repo, "--comment", text], b"", "--comment"),
            ("issue create --body=", ["issue", "create", "-R", repo, "-t", "t", f"--body={text}"], b"", "--body"),
            ("issue edit -tTEXT", ["issue", "edit", "4", f"-R{repo}", f"-t{text}"], b"", "--title"),
            ("issue comment -F -", ["issue", "comment", "4", "-R", repo, "-F", "-"], text.encode(),
             "--body-file - (standard input)"),
            ("release create --notes", ["release", "create", "v1", "-R", repo, "--notes", text], b"", "--notes"),
            ("release edit --notes-file -", ["release", "edit", "v1", "-R", repo, "--notes-file", "-"], text.encode(),
             "--notes-file - (standard input)"),
            ("discussion comment --body", ["discussion", "comment", "3", "-R", repo, "--body", text], b"", "--body"),
            ("label create --description", ["label", "create", "x", "-R", repo, "--description", text], b"",
             "--description"),
            ("workflow run -f", ["workflow", "run", "w.yml", "-R", repo, "-f", f"note={text}"], b"", "--raw-field note"),
            ("agent-task create", ["agent-task", "create", text, "-R", repo], b"", "argument 1"),
            ("api -f body=", ["api", "-X", "PATCH", f"repos/{repo}/pulls/11", "-f", f"body={text}"], b"",
             "--raw-field body"),
            ("api -F body=@FILE", ["api", f"repos/{repo}/issues/1/comments", "-F", f"body=@{f('c.md', text)}"], b"",
             "--field body=@c.md"),
            ("api -F body=@-", ["api", f"repos/{repo}/issues/1/comments", "-F", "body=@-"], text.encode(),
             "--field body=@- (standard input)"),
            ("api --input FILE, JSON escapes", ["api", "--method", "PATCH", f"repos/{repo}/pulls/11", "--input",
                                                 f("d.json", json.dumps({"body": "x"}).replace('"x"', f'"{escaped}"'))],
             b"", "--input d.json .body"),
            ("api --input -", ["api", f"/repos/{repo}/pulls/11/reviews", "--input", "-"],
             json.dumps({"event": "COMMENT", "body": text}).encode(), "--input - (standard input) .body"),
        ]

    def test_a_listed_term_in_every_input_form_is_refused_for_a_public_repository(self) -> None:
        for name, argv, stdin, field in self.forms(PUBLIC, TEXT):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_refused(self.run_gh(argv, stdin), field)

    def test_control_the_same_text_goes_to_a_private_repository_unchanged(self) -> None:
        for name, argv, stdin, _ in self.forms(PRIVATE, TEXT):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, stdin), argv, stdin)

    def test_text_with_no_listed_term_goes_to_a_public_repository_unread_for_visibility(self) -> None:
        for name, argv, stdin, _ in self.forms(PUBLIC, CLEAN):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, stdin), argv, stdin)
        # Clean text costs no visibility read: only a found term (or a list that cannot be read) asks.
        self.assertEqual(self.visibility_reads(), [])

    def setUp_calls(self) -> None:
        shutil.rmtree(self.calls)
        self.calls.mkdir()

    # --- visibility ---------------------------------------------------------------------------------------------

    def test_a_repository_whose_visibility_cannot_be_read_is_read_as_public(self) -> None:
        refused = self.run_gh(["pr", "edit", "11", "-R", UNREAD, "--body", TEXT])
        self.assert_refused(refused, "--body")
        self.assertIn(f"repos/{UNREAD}", self.visibility_reads())
        self.assertIn("read as public", refused.stderr.decode())
        # Control: the same repository with an answer of private sends the call.
        self.answer(UNREAD, "private")
        sent = self.run_gh(["pr", "edit", "11", "-R", UNREAD, "--body", TEXT])
        self.assert_sent(sent, ["pr", "edit", "11", "-R", UNREAD, "--body", TEXT])

    def test_a_visibility_is_read_once_per_repository_and_cached(self) -> None:
        for _ in range(2):
            self.assert_refused(self.run_gh(["issue", "comment", "1", "-R", PUBLIC, "--body", TEXT]), "--body")
        self.assertEqual(self.visibility_reads(), [f"repos/{PUBLIC}"])
        cached = self.tmp / "cache" / "public-leak-check" / "o_pub"
        self.assertEqual(cached.read_text(encoding="utf-8"), "public")
        # Control: with the cache entry gone, the next call reads the visibility again.
        cached.unlink()
        self.assert_refused(self.run_gh(["issue", "comment", "1", "-R", PUBLIC, "--body", TEXT]), "--body")
        self.assertEqual(self.visibility_reads(), [f"repos/{PUBLIC}"] * 2)

    def test_with_no_repository_named_the_directory_s_remotes_decide(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.work)], check=True, env=self.env)
        git = ["git", "-C", str(self.work)]
        subprocess.run([*git, "remote", "add", "origin", f"https://github.com/{PRIVATE}.git"], check=True, env=self.env)
        argv = ["pr", "comment", "11", "--body", TEXT]
        self.assert_sent(self.run_gh(argv), argv)
        # A second remote that is public makes the target uncertain, so the text is read as public.
        subprocess.run([*git, "remote", "add", "upstream", f"git@github.com:{PUBLIC}.git"], check=True, env=self.env)
        self.setUp_calls()
        self.assert_refused(self.run_gh(argv), "--body")
        # gh's own default (`gh repo set-default`) names the one remote gh uses.
        subprocess.run([*git, "config", "remote.origin.gh-resolved", "base"], check=True, env=self.env)
        self.assert_sent(self.run_gh(argv), argv)

    def test_a_graphql_mutation_is_read_as_public_and_a_graphql_query_is_a_read(self) -> None:
        mutation = 'mutation { addComment(input: {subjectId: "I_1", body: "%s"}) { clientMutationId } }' % TEXT.strip()
        self.assert_refused(self.run_gh(["api", "graphql", "-f", f"query={mutation}"]), "--raw-field query string 2")
        query = 'query { search(query: "%s", type: ISSUE, first: 1) { issueCount } }' % CANARY
        argv = ["api", "graphql", "-f", f"query={query}"]
        self.assert_sent(self.run_gh(argv), argv)

    def test_a_gh_alias_is_read_as_the_command_it_expands_to(self) -> None:
        config = self.tmp / "config" / "gh"
        config.mkdir()
        (config / "config.yml").write_text(
            "version: 1\naliases:\n    pc: pr comment\n    'say': \"issue comment $1 --body\"\n", encoding="utf-8")
        self.assert_refused(self.run_gh(["pc", "11", "-R", PUBLIC, "--body", TEXT]), "--body")
        self.assert_refused(self.run_gh(["say", "4", TEXT, "-R", PUBLIC]), "--body")
        argv = ["pc", "11", "-R", PRIVATE, "--body", TEXT]
        self.assert_sent(self.run_gh(argv), argv)

    # --- a term listed for some repositories only ---------------------------------------------------------------

    def test_a_scoped_term_is_refused_only_for_a_public_repository_of_its_scope(self) -> None:
        text = f"Ask {SCOPED.upper()} first.\n"
        self.answer("cybermonklabs/legion", "public")
        self.answer("cybermonklabs/inner", "private")
        refused = self.run_gh(["pr", "comment", "11", "-R", "cybermonklabs/legion", "--body", text])
        self.assert_refused(refused, "--body", SCOPED.upper(), f"the {SCOPE} list", line=1)
        # Controls: the same text to a public repository outside the scope, and to a private one inside it, goes out.
        for repo in (PUBLIC, "cybermonklabs/inner"):
            with self.subTest(repo=repo):
                self.setUp_calls()
                argv = ["pr", "comment", "11", "-R", repo, "--body", text]
                self.assert_sent(self.run_gh(argv), argv)
        # A raw GraphQL mutation is read as public and as any repository, so it may reach the scope: it is refused.
        mutation = 'mutation { addComment(input: {subjectId: "PR_1", body: "%s"}) { clientMutationId } }' % text.strip()
        self.setUp_calls()
        unnamed = self.run_gh(["api", "graphql", "-f", f"query={mutation}"])
        self.assert_refused(unnamed, "--raw-field query string 2", SCOPED.upper(), f"the {SCOPE} list")

    def test_an_allowed_token_holds_a_scoped_term_only_in_its_repository(self) -> None:
        (repository, (token, escaped)), = ALLOWED_TEST_TOKENS.items()
        self.answer(repository, "public")
        self.answer("cybermonklabs/legion", "public")
        for text in (f"The filter names `{token}`.\n", f"/(?:{escaped}|x)/i\n"):
            with self.subTest(text=text):
                self.setUp_calls()
                argv = ["pr", "comment", "11", "-R", repository, "--body", text]
                self.assert_sent(self.run_gh(argv), argv)
                self.setUp_calls()
                refused = self.run_gh(["pr", "comment", "11", "-R", "cybermonklabs/legion", "--body", text])
                self.assert_refused(refused, "--body", SCOPED, f"the {SCOPE} list")
        # The scope with another package is no allowed token, in the fork either.
        self.setUp_calls()
        other = self.run_gh(["pr", "comment", "11", "-R", repository, "--body", f"uses @{SCOPED}/other-package\n"])
        self.assertEqual(other.returncode, 2, other.stderr.decode())

    def test_a_knives_registry_term_is_refused_in_a_fork_pull_request_and_goes_elsewhere(self) -> None:
        # The registry's oh-my-pi entry forbids one term in repos.toml and one in the private repos.local.toml; its
        # origin still names sjawhar/oh-my-pi, and the fork the bodies leaked into is cybermonklabs/oh-my-pi.
        self.answer("cybermonklabs/oh-my-pi", "public")
        self.answer("o/oh-my-pi", "private")
        for number, term in enumerate(REGISTRY_TEST_TERMS["oh-my-pi"], start=1):
            body = f"Built with {term.upper()} on the fork.\n"
            for argv in (["pr", "create", "-R", "cybermonklabs/oh-my-pi", "--head", "h", "--title", "t", "--body", body],
                         ["pr", "edit", "12", "-R", "cybermonklabs/oh-my-pi", "--body-file", self.file("fork.md", body)]):
                with self.subTest(term=number, command=argv[1]):
                    self.setUp_calls()
                    self.assert_refused(self.run_gh(argv), "--body" if argv[1] == "create" else "--body-file fork.md",
                                        term.upper(), "the knives oh-my-pi list", line=1)
            # Controls: a public repository of another name, and a private one named oh-my-pi, get the same body.
            for repo in (PUBLIC, "o/oh-my-pi"):
                with self.subTest(term=number, repo=repo):
                    self.setUp_calls()
                    argv = ["pr", "edit", "12", "-R", repo, "--body", body]
                    self.assert_sent(self.run_gh(argv), argv)

    # --- a term inside the own name of a public destination sends nothing new (dispatch://INFRA-3365) -----------------

    def ygo_fork(self) -> dict[str, str]:
        """The environment with a knives registry holding only the real ygo-fork entry: its forbidden list holds
        `theorem-labs`, which the name of the entry's release repository, theorem-labs/ygo, holds too."""
        knives = self.tmp / "knives"
        knives.mkdir()
        (knives / "repos.toml").write_text(
            '[repos.ygo-fork]\nupstream = "https://github.com/reearth/ygo.git"\n'
            'origin = "https://github.com/sjawhar/ygo.git"\nrelease = "https://github.com/theorem-labs/ygo.git"\n'
            'forbidden = ["dispatch://", "INFRA-", "theorem-labs"]\n', encoding="utf-8")
        return {**self.env, "DOTFILES_LEAK_TERMS_KNIVES_DIR": str(knives)}

    def ygo_writes(self) -> list[tuple[str, list[str]]]:
        """(name, argv) of each write that names YGO as where it goes, in each spelling, and sends no listed term."""
        return [
            ("pr comment -R", ["pr", "comment", "1", "-R", YGO, "--body", "hello"]),
            ("release create -R", ["release", "create", "v1.55.1-theorem.9", "-R", YGO, "--notes", "cut"]),
            ("pr create -R", ["pr", "create", "-R", YGO, "--title", "t", "--body", "b", "--head", "x"]),
            ("pr comment --repo=", ["pr", "comment", "1", f"--repo={YGO}", "--body", "hello"]),
        ]

    def test_a_term_inside_the_name_of_the_public_repository_a_call_goes_to_counts_for_nothing(self) -> None:
        # Given the knives ygo-fork list and its public release repository theorem-labs/ygo, whose name holds a term of
        # that list: the value of -R is text the call sends, read as every text is, and the term in it lies inside the
        # own name of a repository read as public, which sends nothing new, so the call goes out (gh_own_name). Until
        # 2026-11-19 every write there was refused for that name.
        env = self.ygo_fork()
        self.answer(YGO, "public")
        for name, argv in self.ygo_writes():
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)
        # Each call's text names the repository, whose name holds a term of the list, so its visibility is read, and no
        # other: the match lies inside the own name of a repository read as public, and counts for nothing.
        self.assertEqual(set(self.visibility_reads()), {f"repos/{YGO}"})
        # Control: a term of the list in the text the call sends there is refused, and the refusal names that text's
        # field, never the repository.
        for name, argv, field, matched in (
                ("--title", ["pr", "create", "-R", YGO, "--head", "x", "--title", "Fixes INFRA-1", "--body", "b"],
                 "--title", "INFRA-"),
                ("--body", ["pr", "comment", "1", "-R", YGO, "--body", "Fixes INFRA-1"], "--body", "INFRA-"),
                ("--body dispatch://", ["pr", "comment", "1", "-R", YGO, "--body", "See dispatch://x"], "--body",
                 "dispatch://")):
            with self.subTest(form=name):
                self.setUp_calls()
                refused = self.run_gh(argv, env=env)
                self.assert_refused(refused, field, matched, YGO_LIST, line=1)
                self.assertNotIn("--repo line", refused.stderr.decode())

    def test_a_write_to_a_private_repository_named_with_a_listed_term_goes_out(self) -> None:
        # Given the same list and theorem-labs/ygo private: no target is public, so the calls go out, a term of the
        # list in a body too.
        env = self.ygo_fork()
        self.answer(YGO, "private")
        termed = ["pr", "comment", "1", "-R", YGO, "--body", "Fixes INFRA-1"]
        for name, argv in (*self.ygo_writes(), ("--body INFRA-1", termed)):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)

    def test_an_option_naming_the_destination_sends_nothing_new(self) -> None:
        # Given the knives ygo-fork list and theorem-labs/ygo public: each option whose value gh 2.98.0's help gives as
        # an issue number or URL, or a branch, can name the destination itself, here by its URL or as `owner:branch`.
        # That name is public already, so the term of the list it holds sends nothing new, and the call goes out. The
        # exemptions the rule replaced left the name out of each spelling the check knew, and each of these options
        # was one more (dispatch://INFRA-3365, review finding 2).
        env = self.ygo_fork()
        self.answer(YGO, "public")
        create = ["issue", "create", "-R", YGO, "--title", "t", "--body", "b"]
        edit = ["issue", "edit", "1", "-R", YGO]
        rows = [*((f"issue create {option}", [*create, option]) for option in ("--parent", "--blocked-by", "--blocking")),
                *((f"issue edit {option}", [*edit, option])
                  for option in ("--parent", "--add-blocked-by", "--remove-blocked-by", "--add-blocking",
                                 "--remove-blocking", "--add-sub-issue", "--remove-sub-issue")),
                ("issue close --duplicate-of", ["issue", "close", "1", "-R", YGO, "--duplicate-of"])]
        for name, argv in rows:
            with self.subTest(form=name):
                self.setUp_calls()
                sent = [*argv, f"https://github.com/{YGO}/issues/5"]
                self.assert_sent(self.run_gh(sent, env=env), sent)
        with self.subTest(form="pr create --head"):
            argv = ["pr", "create", "-R", YGO, "--title", "t", "--body", "b", "--head", "theorem-labs:x"]
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv, env=env), argv)
        # Controls: the same option naming another repository, whose name holds a term of the list, is refused on that
        # option; and so is a body holding one.
        for name, argv in rows:
            with self.subTest(control=name):
                self.setUp_calls()
                refused = self.run_gh([*argv, "https://github.com/sjawhar/INFRA-notes/issues/5"], env=env)
                self.assert_refused(refused, argv[-1], "INFRA-", YGO_LIST, line=1)
        with self.subTest(control="--body INFRA-1"):
            self.setUp_calls()
            refused = self.run_gh([*create[:-1], "Fixes INFRA-1"], env=env)
            self.assert_refused(refused, "--body", "INFRA-", YGO_LIST, line=1)

    def test_the_owner_alone_is_the_destination_s_name_only_as_a_whole_word(self) -> None:
        # Given the knives ygo-fork list and theorem-labs/ygo public: the owner alone is the destination's own name only
        # as a whole word by GitHub's login characters, so `theorem-labs/other` goes out, and `theorem-labs-internal`,
        # in which the term begins another word, is refused.
        env = self.ygo_fork()
        self.answer(YGO, "public")
        with self.subTest(form="theorem-labs-internal"):
            refused = self.run_gh(["pr", "comment", "1", "-R", YGO, "--body", "see theorem-labs-internal"], env=env)
            self.assert_refused(refused, "--body", "theorem-labs", YGO_LIST, line=1)
        with self.subTest(form="theorem-labs/other"):
            argv = ["pr", "comment", "1", "-R", YGO, "--body", "see theorem-labs/other"]
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv, env=env), argv)

    def test_the_destination_s_own_name_counts_unless_it_is_public_already(self) -> None:
        # A term inside the destination's own name counts for nothing only where that name is public already, its
        # visibility read as public: a visibility that cannot be read, and a repository the call makes public, leave the
        # name as any text. Each row reads the visibility afresh: its answer() deletes the cache of the row before.
        env = self.ygo_fork()
        body = ["pr", "comment", "1", "-R", YGO, "--body", "see theorem-labs"]
        with self.subTest(form="visibility unread"):
            refused = self.run_gh(body, env=env)
            self.assert_refused(refused, "--body", "theorem-labs", YGO_LIST, line=1)
            self.assertIn(f"{YGO} (visibility unread, so read as public)", refused.stderr.decode())
        with self.subTest(form="gh api PATCH visibility=public"):
            self.answer(YGO, "private")
            self.setUp_calls()
            refused = self.run_gh(["api", "-X", "PATCH", f"repos/{YGO}", "-f", "visibility=public"], env=env)
            self.assert_refused(refused, "argument 1", "theorem-labs", YGO_LIST, line=1)
            self.assertIn(f"{YGO} (made public by the call)", refused.stderr.decode())
        # Control: the same body to the repository read as public goes out.
        with self.subTest(control="public"):
            self.answer(YGO, "public")
            self.setUp_calls()
            self.assert_sent(self.run_gh(body, env=env), body)
            self.assertEqual(self.cached(YGO), "public")

    def test_a_text_is_judged_only_by_the_targets_it_goes_to(self) -> None:
        # Given the knives ygo-fork list and a checkout of its three repositories, each public, with no `gh repo
        # set-default`: gh may take any remote as the base repository, and a text gh fills from one goes out only when
        # gh takes that one, so it is judged only by the targets the call reaches with it and those that depend on no
        # base; the value of -R goes only to the repository it names, and the value of --branch-repo to its own and the
        # issue's (the -R repository here). Until review round 2 of
        # dispatch://INFRA-3365 each text was judged by every target, so the theorem-labs/ygo filling of a reply's
        # endpoint was refused for sjawhar/ygo, whose name does not hold `theorem-labs`.
        env = self.ygo_fork()
        self.remotes(("origin", "sjawhar/ygo"), ("upstream", "reearth/ygo"), ("release", YGO))
        for repository in ("sjawhar/ygo", "reearth/ygo", YGO):
            self.answer(repository, "public")
        reply = ["api", "repos/{owner}/{repo}/pulls/5/comments/123/replies", "-f"]
        develop = ["issue", "develop", "1", "-R", YGO, "--branch-repo", "sjawhar/ygo", "--name"]
        for name, argv in (
                ("each base's endpoint", [*reply, "body=ok"]),
                ("each base's owner, to that base's issues",
                 ["api", "repos/{owner}/{repo}/issues", "-F", "title=from {owner}"]),
                ("-R and --branch-repo", [*develop, "x"])):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)
        # Controls: a term in a text that goes to every target, and a filling that goes to a repository whose name does
        # not hold the term, are refused.
        for name, argv, field, matched in (
                ("a term in the reply", [*reply, "body=see INFRA-1"], "--raw-field body", "INFRA-"),
                ("theorem-labs, to sjawhar/ygo's issues", ["api", "repos/sjawhar/ygo/issues", "-F", "title=from {owner}"],
                 "--field title", "theorem-labs"),
                ("a term in the branch's name", [*develop, "INFRA-1"], "--name", "INFRA-")):
            with self.subTest(control=name):
                self.setUp_calls()
                self.assert_refused(self.run_gh(argv, env=env), field, matched, YGO_LIST, line=1)

    def test_issue_develop_sends_each_text_to_the_issue_s_repository_too(self) -> None:
        # `gh issue develop` makes a branch in the --branch-repo repository and links it to the issue, whose page shows
        # the branch by its repository's name, so the issue's repository is always a target, however the call names it
        # (-R, the issue's URL, else the checkout's remote), and every text of the call, the --branch-repo value
        # included, goes to it as well as to the branch's repository; the value of -R goes only to its own. Until review
        # round 3 of dispatch://INFRA-3365 the --branch-repo value went only to the branch's repository, and with no -R
        # the issue's repository was no target at all, so a private name, or a term, went to a public issue unread.
        env = self.ygo_fork()
        self.answer(YGO, "public")
        self.answer("sjawhar/INFRA-notes", "private")
        self.answer(self.NOTES, "private")
        develop = ["issue", "develop", "1", "-R", YGO, "--branch-repo"]
        with self.subTest(form="-R, a private --branch-repo whose name holds a term"):
            refused = self.run_gh([*develop, "sjawhar/INFRA-notes", "--name", "x"], env=env)
            self.assert_refused(refused, "--branch-repo", "INFRA-", YGO_LIST, line=1)
            self.assertIn(f"{YGO} (public)", refused.stderr.decode())
        with self.subTest(control="-R, --branch-repo sjawhar/ygo"):
            argv = [*develop, "sjawhar/ygo", "--name", "x"]
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv, env=env), argv)
        with self.subTest(form="the issue's URL"):
            self.setUp_calls()
            refused = self.run_gh(["issue", "develop", f"https://github.com/{PUBLIC}/issues/1", "--branch-repo",
                                   self.NOTES, "--name", "fix"])
            self.assert_refused(refused, "--branch-repo", CANARY, line=1)
            self.assertIn(f"{PUBLIC} (public)", refused.stderr.decode())
        # From a checkout whose only remote is public, the issue is in that remote's repository.
        self.remotes(("origin", PUBLIC))
        for name, branch_repository, branch in (("--name", PRIVATE, f"fix-{CANARY}"), ("--branch-repo", self.NOTES, "fix")):
            with self.subTest(form=f"the checkout's remote, a term in {name}"):
                self.setUp_calls()
                refused = self.run_gh(["issue", "develop", "1", "--branch-repo", branch_repository, "--name", branch])
                self.assert_refused(refused, name, CANARY, line=1)
                self.assertIn("remote origin (public)", refused.stderr.decode())
        with self.subTest(control="the checkout's remote, no term"):
            argv = ["issue", "develop", "1", "--branch-repo", PRIVATE, "--name", "fix"]
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv), argv)

    def test_a_term_inside_the_public_destination_s_name_counts_for_nothing_however_the_call_names_it(self) -> None:
        # Given the knives ygo-fork list and theorem-labs/ygo public: a write that names the repository by its API
        # endpoint, by a pull request or issue URL, or as the repository `repo edit` changes goes there as the -R
        # forms do. `theorem-labs` lies inside the destination's own name, which is public already, so it counts for
        # nothing there (gh_own_name; dispatch://INFRA-3365, tester round 1).
        env = self.ygo_fork()
        self.answer(YGO, "public")
        url = f"https://github.com/{YGO}"
        for name, argv in (
                ("api comment", ["api", f"repos/{YGO}/issues/1/comments", "-f", "body=hello"]),
                ("api review reply, leading /", ["api", f"/repos/{YGO}/pulls/5/comments/123/replies", "-f", "body=ok"]),
                ("api -X POST release", ["api", "-X", "POST", f"repos/{YGO}/releases", "-f", "tag_name=v1.55.1-t.9"]),
                ("pr comment URL", ["pr", "comment", f"{url}/pull/1", "--body", "hello"]),
                ("pr review URL", ["pr", "review", f"{url}/pull/1", "--approve", "--body", "ok"]),
                ("issue comment URL", ["issue", "comment", f"{url}/issues/1", "--body", "hello"]),
                ("repo edit", ["repo", "edit", YGO, "--description", "a fork"])):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)
        # Control: a term of the list in the rest of what the call sends there is still refused: the part of an
        # endpoint after the repository (the path of a file a PUT makes), and a body sent to a URL's pull request.
        for name, argv, field in (
                ("api PUT contents", ["api", "-X", "PUT", f"repos/{YGO}/contents/notes-INFRA-1.md", "-f", "message=m",
                                      "-f", "content=aGk="], "argument 1"),
                ("pr comment URL --body", ["pr", "comment", f"{url}/pull/1", "--body", "Fixes INFRA-1"], "--body")):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_refused(self.run_gh(argv, env=env), field, "INFRA-", YGO_LIST, line=1)

    def test_the_name_of_a_repository_the_call_makes_public_is_read(self) -> None:
        # Given a private repository whose name holds a listed term: `repo edit --visibility public` publishes that
        # name, so the name is text the call sends, however the argument names the repository. The name of a
        # repository that is public already is not (dispatch://INFRA-3365, tester round 2).
        repo = f"o/notes-{CANARY}"
        self.answer(repo, "private")
        for name, argument in (("OWNER/REPO", repo), ("URL", f"https://github.com/{repo}")):
            with self.subTest(form=name):
                self.setUp_calls()
                refused = self.run_gh(["repo", "edit", argument, "--visibility", "public",
                                       "--accept-visibility-change-consequences"])
                self.assert_refused(refused, "argument 1", CANARY, line=1)
        # Control: the same repository, public already, edited with a clean description: its name is public, and the
        # call goes out (gh_own_name). The rows above cached the private answer for 60 s, so the visibility is read
        # afresh, and the cache shows the control was judged public: until 2026-11-19 it read the cached private
        # answer and went out whatever the rule did (dispatch://INFRA-3365, tester round 6).
        cached = self.tmp / "cache" / "public-leak-check" / repo.replace("/", "_")
        self.answer(repo, "public")
        cached.unlink(missing_ok=True)
        argv = ["repo", "edit", repo, "--description", "notes"]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)
        self.assertEqual(cached.read_text(encoding="utf-8"), "public")

    def test_a_gh_api_call_that_makes_a_repository_public_reads_its_name(self) -> None:
        # Given a private repository whose name holds a listed term: a call to the repository's own endpoint that sets
        # its visibility to public, or private to false, publishes the name as `repo edit --visibility public` does, so
        # the endpoint is read whole and the repository is read as public (dispatch://INFRA-3365, tester round 2).
        repo = f"o/notes-{CANARY}"
        self.answer(repo, "private")
        for name, argv in (
                ("-f visibility=public", ["api", "-X", "PATCH", f"repos/{repo}", "-f", "visibility=public"]),
                ("-F private=false", ["api", "--method", "PATCH", f"/repos/{repo}", "-F", "private=false"]),
                ("--input", ["api", "-X", "PATCH", f"repos/{repo}", "--input",
                             self.file("p.json", json.dumps({"private": False}))]),
                ("address", ["api", "-X", "PATCH", f"https://api.github.com/repos/{repo}", "-f", "visibility=public"])):
            with self.subTest(form=name):
                self.setUp_calls()
                refused = self.run_gh(argv)
                self.assert_refused(refused, "argument 1", CANARY, line=1)
                self.assertIn("(made public by the call)", refused.stderr.decode())
        # Controls: a call that keeps the repository private, and an edit of it once public, go out. Each reads the
        # visibility afresh, since its answer() deletes the answer the rows above cached, so the public control is
        # judged as public: the name in its endpoint is the repository's own, public already (gh_own_name).
        for visibility, argv in (("private", ["api", "-X", "PATCH", f"repos/{repo}", "-f", "visibility=private"]),
                                 ("public", ["api", "-X", "PATCH", f"repos/{repo}", "-f", "description=notes"])):
            with self.subTest(control=visibility):
                self.answer(repo, visibility)
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv), argv)
                self.assertEqual(self.cached(repo), visibility)

    def test_a_gh_api_call_that_makes_the_checkout_s_repository_public_reads_its_text(self) -> None:
        # Given a checkout whose one remote is a private repository: `repos/{owner}/{repo}` is that repository's own
        # endpoint, which gh fills in from the remote, so a call there that sets its visibility to public, or private to
        # false, publishes it as the same call to `repos/O/R` does, and its text goes to a public repository
        # (dispatch://INFRA-3365, tester round 3).
        subprocess.run(["git", "init", "-q", str(self.work)], check=True, env=self.env)
        subprocess.run(["git", "-C", str(self.work), "remote", "add", "origin", f"https://github.com/{PRIVATE}.git"],
                       check=True, env=self.env)
        for name, argv in (
                ("-f visibility=public", ["api", "-X", "PATCH", "repos/{owner}/{repo}", "-f", "visibility=public",
                                          "-f", f"description={TEXT}"]),
                ("-F private=false", ["api", "--method", "PATCH", "/repos/{owner}/{repo}", "-F", "private=false",
                                      "-f", f"description={TEXT}"])):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_refused(self.run_gh(argv), "--raw-field description")
        # Control: the same text to the same endpoint, with the repository kept private, goes out.
        argv = ["api", "-X", "PATCH", "repos/{owner}/{repo}", "-f", f"description={TEXT}"]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)

    # --- every way a call makes a repository public, by every way gh names it (dispatch://INFRA-3365, tester round 3)

    # A private repository whose name holds the canary, and the field a refusal names for the name a call publishes
    # that no text of the call gives whole.
    NOTES = f"o/notes-{CANARY}"
    PUBLISHED = "the name of the repository the call makes public"

    def remotes(self, *remotes: tuple[str, str], base: str = "") -> None:
        """Make the work directory a new checkout whose remotes are REMOTES, (name, owner/name) pairs on github.com,
        and whose `gh repo set-default` (a remote's gh-resolved) is the remote BASE when given."""
        shutil.rmtree(self.work / ".git", ignore_errors=True)
        subprocess.run(["git", "init", "-q", str(self.work)], check=True, env=self.env)
        git = ["git", "-C", str(self.work)]
        for name, repository in remotes:
            subprocess.run([*git, "remote", "add", name, f"https://github.com/{repository}.git"], check=True,
                           env=self.env)
        if base:
            subprocess.run([*git, "config", f"remote.{base}.gh-resolved", "base"], check=True, env=self.env)

    def test_repo_edit_publishes_the_name_of_the_repository_it_edits_however_gh_names_it(self) -> None:
        # Given a private repository whose name holds a listed term: `repo edit --visibility public` publishes its name,
        # given as an argument in each form gh reads, or taken from the checkout's remote, which gh takes whatever
        # GH_REPO says, and from the remote `gh repo set-default` chose.
        self.answer(self.NOTES, "private")
        edit = ["repo", "edit", "--visibility", "public", "--accept-visibility-change-consequences"]
        for name, argument in (("HOST/OWNER/REPO", f"github.com/{self.NOTES}"),
                               ("scp URL", f"git@github.com:{self.NOTES}.git"),
                               ("ssh URL", f"ssh://git@github.com/{self.NOTES}.git")):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_refused(self.run_gh([*edit[:2], argument, *edit[2:]]), "argument 1", CANARY, line=1)
        for name, remotes, base, env, remote in (
                ("remote", [("origin", self.NOTES)], "", self.env, "origin"),
                ("remote, GH_REPO public", [("origin", self.NOTES)], "", {**self.env, "GH_REPO": PUBLIC}, "origin"),
                ("gh repo set-default", [("origin", PRIVATE), ("upstream", self.NOTES)], "upstream", self.env,
                 "upstream")):
            with self.subTest(form=name):
                self.remotes(*remotes, base=base)
                self.setUp_calls()
                refused = self.run_gh(edit, env=env)
                self.assert_refused(refused, f"{self.PUBLISHED} (remote {remote})", CANARY, line=1)
                self.assertIn(f"to remote {remote} (made public by the call)", refused.stderr.decode())
        # Control: a private repository with a clean name is made public.
        with self.subTest(control="clean name"):
            self.remotes(("origin", PRIVATE))
            self.setUp_calls()
            self.assert_sent(self.run_gh(edit), edit)

    def test_repo_edit_takes_the_checkout_s_repository_whatever_gh_repo_says(self) -> None:
        # gh reads GH_REPO only for a command with -R/--repo and for the placeholders of `gh api`; `repo edit` has no
        # -R, so with no repository argument it edits the checkout's repository, here a public one.
        argv = ["repo", "edit", "--description", TEXT]
        with self.subTest(form="GH_REPO private, remote public"):
            self.remotes(("origin", PUBLIC))
            refused = self.run_gh(argv, env={**self.env, "GH_REPO": PRIVATE})
            self.assert_refused(refused, "--description")
            self.assertIn("to remote origin (public)", refused.stderr.decode())
        # Control: the checkout's repository private, GH_REPO public: the text goes out.
        with self.subTest(control="GH_REPO public, remote private"):
            self.remotes(("origin", PRIVATE))
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv, env={**self.env, "GH_REPO": PUBLIC}), argv)

    def test_repo_create_publishes_the_name_it_is_given_or_its_source_directory_s(self) -> None:
        for name, argv in (
                ("NAME", ["repo", "create", f"notes-{CANARY}", "--public"]),
                ("URL", ["repo", "create", f"https://github.com/{self.NOTES}", "--public"]),
                ("HOST/OWNER/REPO", ["repo", "create", f"github.com/{self.NOTES}", "--public"]),
                ("no visibility flag", ["repo", "create", self.NOTES])):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_refused(self.run_gh(argv), "argument 1", CANARY, line=1)
        # Given no name, gh names the repository after the directory --source names, here the working directory.
        source = self.tmp / f"notes-{CANARY}"
        source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True, env=self.env)
        with self.subTest(form="--source"):
            self.setUp_calls()
            refused = self.run_gh(["repo", "create", "--source", ".", "--public"], cwd=source)
            self.assert_refused(refused, "--source name", CANARY, line=1)
        # Control: the same repository made private.
        with self.subTest(control="--source --private"):
            argv = ["repo", "create", "--source", ".", "--private"]
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv, cwd=source), argv)

    def test_a_gh_api_call_makes_a_repository_public_by_every_way_gh_names_it(self) -> None:
        # A PATCH of a repository's own endpoint that sets its visibility to public, or private to false, publishes the
        # repository, however the endpoint names it: as written, or with placeholders gh fills from GH_REPO, else from
        # the checkout's remote (the one `gh repo set-default` chose, when it chose one). The endpoint is read as gh
        # fills it, so it gives the name whole.
        self.answer(self.NOTES, "private")
        patch = ["api", "-X", "PATCH"]
        visibility = self.file("visibility.txt", "public\n")
        for name, remotes, base, gh_repo, argv, fields in (
                ("-F visibility=@file", [], "", "", [*patch, f"repos/{self.NOTES}", "-F", f"visibility=@{visibility}"],
                 ["argument 1"]),
                ("{owner}/{repo}, remote", [("origin", self.NOTES)], "", "",
                 [*patch, "repos/{owner}/{repo}", "-f", "visibility=public"], ["argument 1"]),
                ("{owner}/{repo}, GH_REPO", [], "", PRIVATE,
                 [*patch, "repos/{owner}/{repo}", "-f", "visibility=public", "-f", f"description={TEXT}"],
                 ["--raw-field description"]),
                ("{owner}/{repo}, GH_REPO's name", [], "", self.NOTES,
                 [*patch, "repos/{owner}/{repo}", "-f", "visibility=public", "-f", "description=notes"],
                 ["argument 1"]),
                (":owner/:repo", [("origin", PRIVATE)], "", "",
                 [*patch, "repos/:owner/:repo", "-f", "visibility=public", "-f", f"description={TEXT}"],
                 ["--raw-field description"]),
                ("O/{repo}", [("origin", self.NOTES)], "", "", [*patch, "repos/o/{repo}", "-f", "private=false"],
                 ["argument 1"]),
                ("{owner}/R", [("origin", PRIVATE)], "", "",
                 [*patch, f"repos/{{owner}}/notes-{CANARY}", "-f", "visibility=public"],
                 ["argument 1"]),
                ("gh repo set-default", [("origin", PUBLIC), ("upstream", self.NOTES)], "upstream", "",
                 [*patch, "repos/{owner}/{repo}", "-f", "visibility=public"], ["argument 1"])):
            with self.subTest(form=name):
                self.remotes(*remotes, base=base)
                self.setUp_calls()
                refused = self.run_gh(argv, env={**self.env, "GH_REPO": gh_repo} if gh_repo else self.env)
                for field in fields:
                    self.assert_refused(refused, field, CANARY.upper() if "description" in field else CANARY)
                self.assertIn("(made public by the call)", refused.stderr.decode())
        # Control: the same text to the checkout's repository, kept private, goes out.
        with self.subTest(control=":owner/:repo kept private"):
            self.remotes(("origin", PRIVATE))
            argv = [*patch, "repos/:owner/:repo", "-f", f"description={TEXT}"]
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv), argv)

    def test_a_gh_api_call_that_makes_a_repository_is_read_as_public(self) -> None:
        # REST's `user/repos`, `orgs/O/repos` and a template's `repos/O/R/generate` make a repository that is public
        # unless the call says otherwise, whatever the visibility of the checkout's repository or of the template.
        self.remotes(("origin", PRIVATE))
        name = f"name=notes-{CANARY}"
        for form, argv, field in (
                ("orgs/{owner}/repos", ["api", "orgs/{owner}/repos", "-f", name, "-f", "visibility=public"],
                 "--raw-field name"),
                ("orgs/:owner/repos", ["api", "orgs/:owner/repos", "-f", name, "-f", "visibility=public"],
                 "--raw-field name"),
                ("user/repos", ["api", "user/repos", "-f", name], "--raw-field name"),
                ("a private template's generate", ["api", f"repos/{PRIVATE}/generate", "-f", "owner=o", "-f", name],
                 "--raw-field name"),
                ("{owner}/{repo}/generate", ["api", "repos/{owner}/{repo}/generate", "-f", "owner=o", "-f", name],
                 "--raw-field name")):
            with self.subTest(form=form):
                self.setUp_calls()
                refused = self.run_gh(argv)
                self.assert_refused(refused, field, CANARY)
                self.assertIn("read as public", refused.stderr.decode())
        # Control: a clean name goes out.
        with self.subTest(control="clean name"):
            argv = ["api", "orgs/{owner}/repos", "-f", "name=notes", "-f", "visibility=public"]
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv), argv)

    def test_the_text_gh_fills_into_an_api_field_is_read(self) -> None:
        # gh fills `{repo}` in an -F value from the base repository, here the checkout's private repository whose name
        # holds a listed term, so the value it sends holds the term: as the name of a repository it makes, and as text
        # sent to a public repository.
        self.answer(self.NOTES, "private")
        self.remotes(("origin", self.NOTES))
        issue = ["api", f"repos/{PUBLIC}/issues", "-F", "title={repo}", "-f", "body=b"]
        for form, argv, field in (
                ("orgs/O/repos", ["api", "orgs/o/repos", "-F", "name={repo}", "-f", "visibility=public"],
                 "--field name"),
                ("generate", ["api", f"repos/{PRIVATE}/generate", "-F", "owner=o", "-F", "name={repo}"], "--field name"),
                ("a public repository's issue", issue, "--field title")):
            with self.subTest(form=form):
                self.setUp_calls()
                self.assert_refused(self.run_gh(argv), field, CANARY, line=1)
        # Control: filled from a private repository with a clean name, the title goes out.
        with self.subTest(control="clean base name"):
            self.remotes(("origin", PRIVATE))
            self.setUp_calls()
            self.assert_sent(self.run_gh(issue), issue)

    def test_the_branch_gh_fills_into_an_api_field_is_read(self) -> None:
        # gh 2.98.0 fills `{branch}` and `:branch` in an -F value with the checkout's current branch (`gh api --help`,
        # and measured: `gh api -X GET … -F 'probe=refs/heads/{branch}' --verbose` sent the branch's name), so a branch
        # whose name holds a listed term sends the term to a public repository, as the name of a ref the call makes or
        # in a title (dispatch://INFRA-3365, tester round 4).
        self.remotes(("origin", PUBLIC))
        subprocess.run(["git", "-C", str(self.work), "symbolic-ref", "HEAD", f"refs/heads/notes-{CANARY}"], check=True,
                       env=self.env)
        for form, argv, field in (
                ("{branch} in a new ref", ["api", "-X", "POST", "repos/{owner}/{repo}/git/refs",
                                           "-F", "ref=refs/heads/{branch}", "-F", "sha=abc"], "--field ref"),
                ("{branch} in a title", ["api", f"repos/{PUBLIC}/issues", "-F", "title=Work from {branch}"],
                 "--field title"),
                (":branch in a title", ["api", f"repos/{PUBLIC}/issues", "-F", "title=Work from :branch"],
                 "--field title")):
            with self.subTest(form=form):
                self.setUp_calls()
                self.assert_refused(self.run_gh(argv), field, CANARY, line=1)
        # Control: -f sends its value as written, `{branch}` and all, so the same title goes out.
        argv = ["api", f"repos/{PUBLIC}/issues", "-f", "title=Work from {branch}"]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)

    def on_branch(self, branch: str = f"notes-{CANARY}") -> None:
        """Make the work directory a new checkout whose remote origin is PUBLIC and whose current branch is BRANCH, as
        the test above makes it."""
        self.remotes(("origin", PUBLIC))
        subprocess.run(["git", "-C", str(self.work), "symbolic-ref", "HEAD", f"refs/heads/{branch}"], check=True,
                       env=self.env)

    def test_the_branch_gh_fills_into_an_api_endpoint_is_read(self) -> None:
        # gh 2.98.0 fills `{branch}` and `:branch` in the endpoint too (`gh api --help`), so the rest of the endpoint
        # after `repos/O/R` sends the branch's name, here as the ref a PATCH moves; a Legion branch (`legion/<KEY>`)
        # holds a `/`, which the rest keeps (dispatch://INFRA-3365, tester round 4).
        for form, branch, endpoint in (
                ("{branch}", f"notes-{CANARY}", "repos/{owner}/{repo}/git/refs/heads/{branch}"),
                (":branch", f"notes-{CANARY}", "repos/{owner}/{repo}/git/refs/heads/:branch"),
                ("{branch} holding a /", f"legion/notes-{CANARY}", "repos/{owner}/{repo}/git/refs/heads/{branch}")):
            with self.subTest(form=form):
                self.on_branch(branch)
                self.setUp_calls()
                refused = self.run_gh(["api", "-X", "PATCH", endpoint, "-F", "sha=abc"])
                self.assert_refused(refused, "argument 1", CANARY, line=1)
                self.assertIn(f"to {PUBLIC}, filled in from remote origin (public)", refused.stderr.decode())
        # Control: the same ref of a branch with a clean name goes out.
        with self.subTest(control="clean branch"):
            self.on_branch("notes")
            argv = ["api", "-X", "PATCH", "repos/{owner}/{repo}/git/refs/heads/{branch}", "-F", "sha=abc"]
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv), argv)

    def test_the_branch_gh_fills_into_a_graphql_variable_is_read(self) -> None:
        # gh fills `{branch}` in an -F value of a GraphQL call too, so createCommitOnBranch's `branchName` sends the
        # branch's name, here in a raw mutation, which is read as public.
        query = ('query=mutation($b: CommittableBranch!) { createCommitOnBranch(input: {branch: $b, '
                 'expectedHeadOid: "x", message: {headline: "m"}}) { commit { oid } } }')
        argv = ["api", "graphql", "-f", query, "-F", f"b[repositoryNameWithOwner]={PUBLIC}",
                "-F", "b[branchName]={branch}"]
        with self.subTest(form="b[branchName]={branch}"):
            self.on_branch()
            refused = self.run_gh(argv)
            self.assert_refused(refused, "--field b[branchName]", CANARY, line=1)
            self.assertIn(f"to {MUTATION}:", refused.stderr.decode())
        # Control: the same commit from a branch with a clean name goes out.
        with self.subTest(control="clean branch"):
            self.on_branch("notes")
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv), argv)

    def branches_gh_cannot_fill(self) -> list[tuple[str, Callable[[], dict[str, str]]]]:
        """(name, make) for each checkout in which gh 2.98.0 cannot fill `{branch}` or `:branch`, and refuses the call
        before it sends anything (measured: exit 1, and no request): with GH_REPO set, on a detached HEAD, as a jj
        checkout that shares its git directory usually has, and outside a git repository. MAKE makes the work directory
        that checkout, checks that git names a current branch only in the one with GH_REPO set, and returns the
        environment to run gh in."""
        git = ["git", "-C", str(self.work)]

        def made(env: dict[str, str], named: bool) -> dict[str, str]:
            current = subprocess.run([*git, "symbolic-ref", "--quiet", "HEAD"], env=env, capture_output=True,
                                     check=False)
            self.assertEqual(current.returncode == 0, named, current)
            return env

        def gh_repo() -> dict[str, str]:
            self.on_branch()
            return made({**self.env, "GH_REPO": PUBLIC}, True)

        def detached() -> dict[str, str]:
            self.on_branch()
            subprocess.run([*git, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                            "commit", "-q", "--allow-empty", "-m", "x"], check=True, env=self.env)
            subprocess.run([*git, "checkout", "-q", "--detach"], check=True, env=self.env)
            return made(self.env, False)

        def no_repository() -> dict[str, str]:
            shutil.rmtree(self.work / ".git", ignore_errors=True)
            return made({**self.env, "GIT_CEILING_DIRECTORIES": str(self.tmp)}, False)

        return [("GH_REPO set", gh_repo), ("detached HEAD", detached), ("no git repository", no_repository)]

    def test_a_call_whose_field_holds_a_branch_gh_cannot_fill_is_passed_since_gh_sends_nothing(self) -> None:
        # gh 2.98.0 fills an -F value's `{branch}` or `:branch` before the endpoint (pkg/cmd/api/api.go apiRun:
        # parseFields, then fillPlaceholders), and refuses a call whose branch it cannot fill before it sends anything
        # (branches_gh_cannot_fill). The check passes a call with such a value whatever else it holds, here a body with
        # a listed term to a public repository. A call whose endpoint alone holds the branch is read as GraphQL instead
        # (test_an_endpoint_the_check_cannot_fill_for_certain_is_read_as_graphql).
        field = ["api", f"repos/{PUBLIC}/issues", "-F", "title={branch}", "-f", f"body={TEXT}"]
        raw = ["api", f"repos/{PUBLIC}/issues", "-f", "title={branch}", "-f", f"body={TEXT}"]
        for condition, make in self.branches_gh_cannot_fill():
            with self.subTest(condition=condition, form="-F title={branch}"):
                env = make()
                self.setUp_calls()
                self.assert_sent(self.run_gh(field, env=env), field)
            # Control: -f sends `{branch}` as written, so gh sends the call, and its body is read.
            with self.subTest(condition=condition, control="-f title={branch}"):
                env = make()
                self.setUp_calls()
                self.assert_refused(self.run_gh(raw, env=env), "--raw-field body")
        # Controls: with GH_REPO empty gh fills the branch, so the call is read; and a call with no branch placeholder
        # is read, here from the branch the other rows have.
        with self.subTest(control="GH_REPO empty"):
            self.on_branch()
            self.setUp_calls()
            refused = self.run_gh(["api", f"repos/{PUBLIC}/issues", "-F", "title={branch}"],
                                  env={**self.env, "GH_REPO": ""})
            self.assert_refused(refused, "--field title", CANARY, line=1)
        with self.subTest(control="no branch placeholder"):
            self.on_branch()
            self.setUp_calls()
            self.assert_refused(self.run_gh(["api", f"repos/{PUBLIC}/issues", "-f", "title=t", "-f", f"body={TEXT}"]),
                                "--raw-field body")

    def test_a_graphql_mutation_is_read_as_public_and_as_any_repository_whatever_node_it_names(self) -> None:
        # The check reads no node: a raw GraphQL mutation is one target, read as public and as any repository, so the
        # knives registry's oh-my-pi list counts for it, whichever pull request its subjectId names.
        term = REGISTRY_TEST_TERMS["oh-my-pi"][0]

        def mutation(node: str) -> list[str]:
            body = f"Reply: see {term}."
            return ["api", "graphql", "-f", f'query=mutation {{ addComment(input: {{subjectId: "{node}", body: "{body}"}}) '
                                              "{ clientMutationId } }"]

        refused = self.run_gh(mutation("PR_kwDOfork1234"))
        self.assertEqual(refused.returncode, 2, refused.stderr.decode())
        self.assertIn(f"to {MUTATION}:", refused.stderr.decode())
        self.assertEqual(self.gh_calls(), [])
        # Another node id changes nothing: the mutation is refused, read as public.
        self.setUp_calls()
        self.assert_refused(self.run_gh(mutation("PR_kwDOpriv5678")), "--raw-field query string 2", term,
                            "the knives oh-my-pi list", line=1)
        # And so is one whose id names no node.
        self.setUp_calls()
        unread = self.run_gh(mutation("PR_kwDOunknown1"))
        self.assertEqual(unread.returncode, 2, unread.stderr.decode())
        self.assertIn(f"to {MUTATION}:", unread.stderr.decode())

    # --- found by the review of the change that added this check (2026-11-17) ----------------------------------------

    def test_a_term_of_several_words_split_by_a_line_break_is_refused(self) -> None:
        # GitHub renders a line break inside a paragraph as a space, so a body wrapped inside a term shows the term.
        first, rest = TEST_TERMS[0].split(" ", 1)
        for name, text, matched in (("line break", f"See {first}\n{rest} soon.\n", f"{first}\\n{rest}"),
                                    ("CRLF", f"See {first}\r\n{rest}.\n", f"{first}\\r\\n{rest}"),
                                    ("no-break space", f"See {first}\u00a0{rest}.\n", f"{first}\u00a0{rest}"),
                                    ("hard break", f"See {first}  \n{rest}\n", f"{first}  \\n{rest}")):
            with self.subTest(form=name):
                self.setUp_calls()
                refused = self.run_gh(["pr", "comment", "11", "-R", PUBLIC, "--body", text])
                self.assert_refused(refused, "--body", matched, line=1)
                # Control: the same text goes to a private repository unchanged.
                self.setUp_calls()
                argv = ["pr", "comment", "11", "-R", PRIVATE, "--body", text]
                self.assert_sent(self.run_gh(argv), argv)
        # Controls: words that are not the term, wrapped the same way, and the term's words in two paragraphs, go to
        # the public repository.
        for text in (f"See {first}\n{rest[:-1]}8 soon.\n", f"See {first}\n\n{rest} soon.\n"):
            self.setUp_calls()
            argv = ["pr", "comment", "11", "-R", PUBLIC, "--body", text]
            self.assert_sent(self.run_gh(argv), argv)

    def test_the_options_of_gh_api_are_read_as_gh_reads_them(self) -> None:
        # pflag reads `-iXPOST` as -i and -X POST, the last -X winning; `-if` as -i and -f; and `-t -XGET` as a
        # template `-XGET`. gh POSTs each of these fields.
        endpoint = f"repos/{PUBLIC}/issues"
        for argv in (["api", "-X", "GET", "-iXPOST", endpoint, "-f", f"title={TEXT}"],
                     ["api", "-X", "GET", "-iX", "POST", endpoint, "-f", f"title={TEXT}"],
                     ["api", "-X", "GET", "-iX=POST", endpoint, "-f", f"title={TEXT}"],
                     ["api", endpoint, "-if", f"title={TEXT}"],
                     ["api", endpoint, "-f", f"title={TEXT}", "-t", "-XGET"],
                     ["api", endpoint, "-f", f"title={TEXT}", "-q", "-XGET"]):
            with self.subTest(argv=argv[1:5]):
                self.setUp_calls()
                self.assert_refused(self.run_gh(argv), "--raw-field title")
        # Control: a cluster that names GET last is a read, whose fields gh sends as a query, and is left untouched.
        self.setUp_calls()
        argv = ["api", "-X", "POST", "-iXGET", f"repos/{PUBLIC}/issues", "-f", f"labels={CANARY}"]
        self.assert_sent(self.run_gh(argv), argv)

    def test_a_graphql_mutation_spelled_with_a_json_escape_is_a_mutation(self) -> None:
        mutation = 'mutation { addComment(input: {subjectId: "I_1", body: "%s"}) { clientMutationId } }' % TEXT.strip()
        payload = json.dumps({"query": mutation}).replace("mutation", "\\u006dutation", 1)
        self.assert_refused(self.run_gh(["api", "graphql", "--input", self.file("m.json", payload)]),
                            "--input m.json .query string 2")
        # Control: a query whose escape spells another word (`mutations`) is a read.
        query = 'query { search(query: "%s mutations", type: ISSUE, first: 1) { issueCount } }' % CANARY
        argv = ["api", "graphql", "--input", self.file("q.json", json.dumps({"query": query}).replace(
            "mutations", "\\u006dutations"))]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)

    def test_an_escaped_term_counts_beside_a_term_its_target_does_not_take(self) -> None:
        # The registry's oh-my-pi term does not reach o/pub and is dropped there; the canary in the same payload,
        # spelled with JSON escapes, is still what GitHub stores.
        registry = REGISTRY_TEST_TERMS["oh-my-pi"][0]
        escaped = "".join(f"\\u{ord(c):04x}" for c in CANARY)
        body = json.dumps({"body": f"Built with {registry}; see NOTE."}).replace("NOTE", escaped)
        argv = ["api", "--method", "PATCH", f"repos/{PUBLIC}/pulls/11", "--input", self.file("e.json", body)]
        self.assert_refused(self.run_gh(argv), "--input e.json .body", CANARY)
        # Control: the registry term alone goes to o/pub.
        argv = ["api", "--method", "PATCH", f"repos/{PUBLIC}/pulls/11", "--input",
                self.file("r.json", json.dumps({"body": f"Built with {registry}."}))]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)

    def test_a_body_file_that_is_a_pipe_is_left_for_gh_to_read(self) -> None:
        # /dev/stdin on a pipe is read from the shim's buffer, and gh gets the same bytes on its stdin.
        argv = ["pr", "edit", "11", "-R", PRIVATE, "--body-file", "/dev/stdin"]
        self.assert_sent(self.run_gh(argv, TEXT.encode()), argv, TEXT.encode())
        self.assertEqual((self.calls / "0.body").read_bytes(), TEXT.encode())
        argv = ["pr", "edit", "11", "-R", PUBLIC, "--body-file", "/dev/stdin"]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv, CLEAN.encode()), argv, CLEAN.encode())
        self.setUp_calls()
        self.assert_refused(self.run_gh(argv, TEXT.encode()), "--body-file /dev/stdin (standard input)")
        # Another pipe, a process substitution, is not read: gh reads it for a private repository, and for any other
        # target the call is refused, since its text cannot be checked.
        script = '"$GH" pr edit 11 -R "$REPO" --body-file <(printf %s "$BODY")'
        for repo, body in ((PRIVATE, TEXT), (PUBLIC, CLEAN)):
            with self.subTest(repo=repo):
                self.setUp_calls()
                result = subprocess.run(["bash", "-c", script], env={**self.env, "GH": str(GH), "REPO": repo, "BODY": body},
                                        cwd=self.work, stdin=subprocess.DEVNULL, capture_output=True, timeout=240,
                                        check=False)
                stderr = result.stderr.decode("utf-8", "replace")
                if repo == PRIVATE:
                    self.assertEqual(result.returncode, 0, stderr)
                    self.assertEqual((self.calls / "0.body").read_bytes(), body.encode())
                else:
                    self.assertEqual(result.returncode, 2, stderr)
                    self.assertEqual(self.gh_calls(), [])
                    self.assertIn(": a pipe or a device, which the check cannot read", stderr)

    def test_a_graphql_mutation_is_read_as_public_whatever_repository_it_names(self) -> None:
        # createCommitOnBranch names its repository by name, in its document or in a variable, and a message may hold
        # a node id: the check reads neither, and the mutation is read as public.

        def commit(repository: str) -> list[str]:
            return ["api", "graphql", "-f", "query=mutation { createCommitOnBranch(input: {branch: {repositoryNameWithOwner: "
                    f'"{repository}", branchName: "main"}}, expectedHeadOid: "{"0" * 40}", message: {{headline: "x", '
                    f'body: "{TEXT.strip()} after PR_kwDOpriv5678"}}}}) {{ commit {{ oid }} }} }}']

        refused = self.run_gh(commit(PUBLIC))
        self.assert_refused(refused, "--raw-field query string 5")
        self.assertIn(f"to {MUTATION}:", refused.stderr.decode())
        # The same name as a JSON variable of an --input payload, and as a gh field.
        payload = json.dumps({"query": "mutation($b: CommittableBranch!) { createCommitOnBranch(input: {branch: $b, "
                                       f'message: {{headline: "{TEXT.strip()}"}}}}) {{ commit {{ oid }} }} }}',
                              "variables": {"b": {"repositoryNameWithOwner": PUBLIC, "branchName": "main"}}})
        self.setUp_calls()
        self.assert_refused(self.run_gh(["api", "graphql", "--input", self.file("v.json", payload)]),
                            "--input v.json .query string 1")
        # A mutation that makes a repository is read as public too.
        self.setUp_calls()
        made = self.run_gh(["api", "graphql", "-f", "query=mutation { createRepository(input: {name: \"x\", "
                                                      f'visibility: PUBLIC, description: "{TEXT.strip()} PR_kwDOpriv5678"}}) '
                                                      "{ repository { url } } }"])
        self.assertEqual(made.returncode, 2, made.stderr.decode())
        self.assertIn(f"to {MUTATION}:", made.stderr.decode())
        # The same commit to the private repository, named and by node id, is refused too, read as public.
        self.setUp_calls()
        self.assert_refused(self.run_gh(commit(PRIVATE)), "--raw-field query string 5")

    # --- every raw GraphQL mutation is read whole, as public (dispatch://INFRA-3365, review round 5) ----------------

    def test_a_raw_graphql_mutation_is_read_whole_as_public_and_its_refusal_names_what_to_use_instead(self) -> None:
        # The check does not read where a raw GraphQL mutation goes: each is one target, read as public, whatever
        # repository or node it names. So a mutation that holds a listed term is refused even to a private repository,
        # with no visibility read, and the own name of the public repository it names earns no exemption. Its refusal
        # names the gh subcommand or REST route to use instead, which is judged by its repository, and keeps the
        # advice every refusal gives.
        def commit(repository: str, headline: str) -> list[str]:
            return ["api", "graphql", "-f",
                    "query=mutation { createCommitOnBranch(input: {branch: {repositoryNameWithOwner: "
                    f'"{repository}", branchName: "main"}}, expectedHeadOid: "x", '
                    f'message: {{headline: "{headline}"}}}}) {{ commit {{ oid }} }} }}']

        contents = "for createCommitOnBranch use gh api -X PUT repos/{owner}/{repo}/contents/{path}, or git push"
        remove = "To send it, remove the term"
        with self.subTest(row="a global term, to a private repository"):
            refused = self.run_gh(commit(PRIVATE, TEXT.strip()))
            self.assert_refused(refused, "--raw-field query string 4", line=1)
            self.assertIn(f"to {MUTATION}:", refused.stderr.decode())
            self.assertEqual(self.visibility_reads(), [])
            for advice in (contents, remove):
                self.assertIn(advice, refused.stderr.decode())
        with self.subTest(row="the same mutation without a term"):
            argv = commit(PRIVATE, CLEAN.strip())
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv), argv)
        with self.subTest(row="a thread reply and a comment"):
            document = ("query=mutation { addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: "
                        '"PRRT_kwDOpriv5678", body: "%s"}) { comment { id } } addComment(input: {subjectId: '
                        '"PR_kwDOpriv5678", body: "ok"}) { clientMutationId } }' % TEXT.strip())
            self.setUp_calls()
            refused = self.run_gh(["api", "graphql", "-f", document])
            self.assert_refused(refused, "--raw-field query string 2", line=1)
            for advice in ("for addPullRequestReviewThreadReply use gh api "
                           "repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies -f body=…",
                           "for addComment use gh issue comment or gh pr comment", remove):
                self.assertIn(advice, refused.stderr.decode())
        with self.subTest(row="a mutation with no route"):
            self.setUp_calls()
            refused = self.run_gh(["api", "graphql", "-f", 'query=mutation { changeUserStatus(input: {message: "%s"}) '
                                                            "{ status { message } } }" % TEXT.strip()])
            self.assert_refused(refused, "--raw-field query string 1", line=1)
            self.assertIn("a gh subcommand or the REST API is judged by its repository", refused.stderr.decode())
        with self.subTest(row="the public destination's own name, under the knives ygo-fork list"):
            env = self.ygo_fork()
            self.answer(YGO, "public")
            self.setUp_calls()
            refused = self.run_gh(commit(YGO, "m"), env=env)
            self.assert_refused(refused, "--raw-field query string 1", "theorem-labs", YGO_LIST, line=1)
            self.assertIn(f"to {MUTATION}:", refused.stderr.decode())
            for advice in (contents, remove):
                self.assertIn(advice, refused.stderr.decode())

    def test_a_call_gh_sends_to_a_graphql_api_is_graphql_on_any_host_however_its_endpoint_is_spelled(self) -> None:
        # gh 2.98.0 fills the endpoint's placeholders first (pkg/cmd/api/api.go apiRun), then resolves it into an
        # address (pkg/cmd/api/http.go httpRequest): one holding `://` is the address itself, `graphql` the GraphQL API
        # of the host --hostname, GH_HOST or gh's config names, whatever host (internal/ghinstance GraphQLEndpoint), and
        # any other a path on that host's REST API, on api.github.com with one leading `/` dropped. GitHub ran each
        # github.com spelling below as GraphQL, its path `/graphql` once the query string and the fragment are left out
        # (measured 2026-11-19 with `query { viewer { login } }`; scripts/leak-terms-check, above gh_graphql_api). The
        # GraphQL API of an Enterprise host, of garage.github.com and of `github.com.`, which go-gh reads as an
        # Enterprise host, is `https://HOST/api/graphql`, and that of a GHE.com host `https://api.HOST/graphql`;
        # `{branch}` and `/{branch}` on a branch named graphql are GraphQL too. So a mutation to each is read as public,
        # with a GraphQL string's `\u` escape read as the letter GitHub decodes, and a query is a read. Until review
        # round 6 of dispatch://INFRA-3365 only `graphql` was GraphQL, and until review round 7 only on github.com,
        # decided before gh fills the endpoint: a mutation to any other spelling was read as a REST body, as written,
        # so a term spelled with an escape went out.
        # The checkout is on a branch named graphql, for the `{branch}` rows.
        self.on_branch("graphql")
        for spelling, extra in ((["graphql"], {}), (["/graphql"], {}), (["graphql?"], {}), (["graphql?x=1"], {}),
                                (["graphql#x"], {}), (["/graphql?x=1#y"], {}),
                                (["https://api.github.com/graphql?x=1"], {}),
                                (["https://api.github.com/graphql#x"], {}),
                                (["https://API.GITHUB.COM/graphql"], {}), (["HTTPS://api.github.com/graphql"], {}),
                                (["GRAPHQL"], {}), (["Graphql"], {}),
                                (["--hostname", "github.com", "graphql"], {}),
                                (["--hostname", "ghe.example.com", "graphql"], {}),
                                (["graphql"], {"GH_HOST": "ghe.example.com"}),
                                (["--hostname", "garage.github.com", "graphql"], {}),
                                (["--hostname", "theorem.ghe.com", "graphql"], {}),
                                (["--hostname", "github.com.", "graphql"], {}),
                                (["https://ghe.example.com/api/graphql"], {}),
                                (["{branch}"], {}),
                                (["/{branch}"], {})):
            name = " ".join([*(f"{key}={value}" for key, value in extra.items()), *spelling])
            env = {**self.env, **extra}
            with self.subTest(endpoint=name, document="a mutation"):
                self.setUp_calls()
                refused = self.run_gh(["api", *spelling, "-f", ESCAPED_MUTATION], env=env)
                self.assert_refused(refused, "--raw-field query string 2", CANARY, line=1)
                self.assertIn(f"to {MUTATION}:", refused.stderr.decode())
            with self.subTest(endpoint=name, document="a query"):
                argv = ["api", *spelling, "-f", SEARCH_QUERY]
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)
        # GitHub answered 502 to `GRAPHQL` and `Graphql`, which are read as GraphQL all the same, erring toward refusing.
        # Controls: GitHub answered 404 to `graphql/` and `//graphql` and 400 to
        # `https://uploads.github.com/graphql`, so none is GraphQL: a query there is read as a REST body, as written,
        # and its term is refused, for an endpoint that names no repository. Until review round 6 the address on
        # uploads.github.com was read as GraphQL, as `graphql` on github.com.
        for spelling in ("graphql/", "//graphql", "https://uploads.github.com/graphql"):
            with self.subTest(endpoint=spelling, control="not GraphQL"):
                self.setUp_calls()
                refused = self.run_gh(["api", spelling, "-f", SEARCH_QUERY])
                self.assert_refused(refused, "--raw-field query", CANARY, line=1)
                self.assertIn("to no repository, read as public:", refused.stderr.decode())

    def test_a_rest_call_to_an_enterprise_host_is_judged_as_a_repository_on_that_host(self) -> None:
        # A REST endpoint on an Enterprise host, which gh 2.98.0 sends under `https://HOST/api/v3/`, is no GraphQL API
        # (test_a_call_gh_sends_to_a_graphql_api_is_graphql_on_any_host_however_its_endpoint_is_spelled), so it is
        # judged as a REST call: a clean title goes out, and a listed term is refused for a repository on that host,
        # read as public.
        rest = ["api", "--hostname", "ghe.example.com", "repos/o/r/issues"]
        with self.subTest(control="a clean REST call to ghe.example.com"):
            argv = [*rest, "-f", "title=t"]
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv), argv)
        with self.subTest(control="a REST call to ghe.example.com with a term"):
            self.setUp_calls()
            refused = self.run_gh([*rest, "-f", f"title={TEXT}"])
            self.assert_refused(refused, "--raw-field title")
            self.assertIn("to a repository on ghe.example.com, read as public:", refused.stderr.decode())

    def test_an_endpoint_the_check_cannot_fill_for_certain_is_read_as_graphql(self) -> None:
        # Where the check cannot fill an endpoint's placeholder as gh does, it cannot tell whether gh sends the call to
        # a GraphQL API, so it reads the call as GraphQL, failing closed: a mutation is read whole, as public, and its
        # escaped term is refused (dispatch://INFRA-3365, review round 7), and a query is a read, which goes out. In
        # each checkout of branches_gh_cannot_fill gh fills no branch, so neither can the check, in the endpoint's
        # `{branch}` or `:branch`. gh then sends nothing, but the check passes only a call whose -F value holds the
        # branch for that (test_a_call_whose_field_holds_a_branch_gh_cannot_fill_is_passed_since_gh_sends_nothing):
        # a call whose endpoint alone holds it, here a REST route too, is read as GraphQL. A base repository the check
        # cannot read, as GH_REPO on another host is, may make the call a REST one instead, so a query there is judged
        # as that REST call too, and its term is refused.
        checkouts = dict(self.branches_gh_cannot_fill())
        rename = f"repos/{PUBLIC}/branches/:branch/rename"
        for condition, endpoint in (("detached HEAD", "{branch}"), *((condition, rename) for condition in checkouts)):
            with self.subTest(condition=condition, endpoint=endpoint, document="a mutation"):
                env = checkouts[condition]()
                self.setUp_calls()
                refused = self.run_gh(["api", "-X", "POST", endpoint, "-f", ESCAPED_MUTATION], env=env)
                self.assert_refused(refused, "--raw-field query string 2", CANARY, line=1)
                self.assertIn(f"to {MUTATION}:", refused.stderr.decode())
            with self.subTest(condition=condition, endpoint=endpoint, document="a query"):
                env = checkouts[condition]()
                argv = ["api", "-X", "POST", endpoint, "-f", SEARCH_QUERY]
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)
        with self.subTest(condition="GH_REPO on another host"):
            env = {**self.env, "GH_REPO": "ghe.example.com/o/r"}
            endpoint = ["api", "repos/{owner}/{repo}/issues"]
            self.setUp_calls()
            refused = self.run_gh([*endpoint, "-f", ESCAPED_MUTATION], env=env)
            self.assert_refused(refused, "--raw-field query string 2", CANARY, line=1)
            self.assertIn(f"to {MUTATION}:", refused.stderr.decode())
            self.setUp_calls()
            refused = self.run_gh([*endpoint, "-f", SEARCH_QUERY], env=env)
            self.assert_refused(refused, "--raw-field query", CANARY, line=1)
            self.assertIn("filled in from GH_REPO, which could not be read, read as public", refused.stderr.decode())

    def test_a_graphql_refusal_advises_a_route_for_each_mutation_its_document_names(self) -> None:
        # A raw GraphQL mutation's refusal for a term names the route for each mutation a name token of its document
        # names, and says that gh fills in only `{owner}`, `{repo}` and `{branch}`. A name inside a string or a comment
        # gives none. A call one of whose texts cannot be read is not known to be a mutation: it has a target of its
        # own, and no mutation's advice, which a refusal with no term gets none of either (dispatch://INFRA-3365,
        # review round 6).
        placeholders = "gh fills in only {owner}, {repo} and {branch}: write every other {…} by hand"
        for name, document, route in (
                ("a review comment", 'addPullRequestReviewComment(input: {pullRequestId: "PR_1", body: "%s"}) '
                 "{ comment { id } }", "for addPullRequestReviewComment use gh api "
                 "repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies -f body=… to reply to a comment, or "
                 "for a new review comment gh api repos/{owner}/{repo}/pulls/{number}/comments -f body=… "
                 "-f commit_id=… -f path=… -F line=…, or -f subject_type=file for the whole file"),
                ("a review", 'addPullRequestReview(input: {pullRequestId: "PR_1", body: "%s"}) { clientMutationId }',
                 "for addPullRequestReview use gh pr review, or for inline comments gh api "
                 "repos/{owner}/{repo}/pulls/{number}/reviews"),
                ("a review submitted", 'submitPullRequestReview(input: {pullRequestReviewId: "PRR_1", event: COMMENT, '
                 'body: "%s"}) { clientMutationId }', "for submitPullRequestReview use gh api "
                 "repos/{owner}/{repo}/pulls/{number}/reviews/{review_id}/events -f event=…")):
            with self.subTest(mutation=name):
                self.setUp_calls()
                refused = self.run_gh(["api", "graphql", "-f", "query=mutation { %s }" % (document % TEXT.strip())])
                self.assert_refused(refused, "--raw-field query string 2", line=1)
                for advice in (route, placeholders):
                    self.assertIn(advice, refused.stderr.decode())
        with self.subTest(row="a route's name inside a string and a comment"):
            self.setUp_calls()
            refused = self.run_gh(["api", "graphql", "-f", 'query=mutation { # createIssue\n changeUserStatus(input: '
                                                            '{message: "addComment: %s"}) { status { message } } }'
                                                            % TEXT.strip()])
            self.assert_refused(refused, "--raw-field query string 1", line=1)
            self.assertIn("a gh subcommand or the REST API is judged by its repository", refused.stderr.decode())
            for advice in ("for addComment use", "for createIssue use"):
                self.assertNotIn(advice, refused.stderr.decode())
        with self.subTest(row="a query with a text that cannot be read"):
            self.setUp_calls()
            refused = self.run_gh(["api", "graphql", "-f", "query=query { viewer { login } }", "-F", "x=@missing.txt"])
            stderr = refused.stderr.decode()
            self.assertEqual(refused.returncode, 2, stderr)
            self.assertEqual(self.gh_calls(), [])
            self.assertIn("to a GraphQL call whose text could not be read, read as public: cannot read "
                          "--field x=@missing.txt", stderr)
            self.assertNotIn(MUTATION, stderr)
            self.assertTrue(stderr.endswith("refused before gh ran, so nothing was sent.\n"), stderr)
        with self.subTest(row="a mutation with a text that cannot be read, and no term"):
            self.setUp_calls()
            refused = self.run_gh(["api", "graphql", "-f", 'query=mutation { addComment(input: {subjectId: "I_1", '
                                                            'body: "ok"}) { clientMutationId } }',
                                   "-F", "x=@missing.txt"])
            stderr = refused.stderr.decode()
            self.assertEqual(refused.returncode, 2, stderr)
            self.assertEqual(self.gh_calls(), [])
            self.assertIn(f"to {MUTATION}: cannot read --field x=@missing.txt", stderr)
            self.assertTrue(stderr.endswith("refused before gh ran, so nothing was sent.\n"), stderr)

    def test_a_raw_graphql_mutation_holding_a_term_is_refused_however_its_node_id_is_padded(self) -> None:
        # GitHub resolves a node id with trailing whitespace (a space, a tab, a line feed) or with `==` appended to the
        # node the id names (measured 2026-11-19 with `query($id: ID!) { node(id: $id) { ... } }` on a public review
        # comment of sjawhar/dotfiles#90, dispatch://INFRA-3365, tester round 9), and `-F reply=@file` sends a file
        # whose last line ends in a line feed, as `echo "$id" > file` writes it.
        body = f"see {CANARY}"

        def reply(comment: str) -> str:
            return ('query=mutation { addPullRequestReviewComment(input: {pullRequestId: "PR_kwDOpriv20003", '
                    'inReplyTo: "%s", body: "%s"}) { comment { id } } }' % (comment, body))

        variables = ("query=mutation($reply: ID, $body: String) { addPullRequestReviewComment(input: {pullRequestId: "
                     '"PR_kwDOpriv20003", inReplyTo: $reply, body: $body}) { comment { id } } }')
        for name, argv, field in (
                ("a trailing space", ["api", "graphql", "-f", reply("PRRC_kwDOpub10004 ")],
                 "--raw-field query string 3"),
                ("a trailing line feed, as a GraphQL escape", ["api", "graphql", "-f", reply("PRRC_kwDOpub10004\\n")],
                 "--raw-field query string 3"),
                ("== appended", ["api", "graphql", "-f", reply("PRRC_kwDOpub10004==")], "--raw-field query string 3"),
                ("-F reply=@file, the file ending in a line feed",
                 ["api", "graphql", "-f", variables, "-F", "reply=@" + self.file("reply.txt", "PRRC_kwDOpub10004\n"),
                  "-f", f"body={body}"], "--raw-field body"),
                ("-f reply with a trailing tab",
                 ["api", "graphql", "-f", variables, "-f", "reply=PRRC_kwDOpub10004\t", "-f", f"body={body}"],
                 "--raw-field body")):
            with self.subTest(write=name):
                self.setUp_calls()
                self.assert_refused(self.run_gh(argv), field, CANARY, line=1)
        # A sixth row: the reply to a comment whose id is written exactly is refused too, read as public.
        argv = ["api", "graphql", "-f", reply("PRRC_kwDOpriv20004")]
        self.setUp_calls()
        self.assert_refused(self.run_gh(argv), "--raw-field query string 3", CANARY, line=1)
        # Control: the same reply, its id padded with a trailing space, with a body that holds no term, goes out.
        argv = ["api", "graphql", "-f", reply("PRRC_kwDOpub10004 ").replace(body, "see you")]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)

    def test_a_gh_alias_under_a_command_is_read_as_the_command_it_expands_to(self) -> None:
        config = self.tmp / "config" / "gh"
        config.mkdir()
        (config / "config.yml").write_text("version: 1\naliases:\n    pr say: pr comment\n", encoding="utf-8")
        self.assert_refused(self.run_gh(["pr", "say", "11", "-R", PUBLIC, "--body", TEXT]), "--body")
        # Control: the same alias to a private repository goes out.
        argv = ["pr", "say", "11", "-R", PRIVATE, "--body", TEXT]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)

    def test_a_graphql_mutation_is_read_as_public_however_its_node_id_is_spelled(self) -> None:
        # The check reads no node id, so one spelled with a JSON escape (`\u0050R_…`) changes nothing: the mutation is
        # refused, read as public.
        mutation = ('mutation { addComment(input: {subjectId: "NODE", body: "%s after PR_kwDOpriv5678"}) '
                    "{ clientMutationId } }" % TEXT.strip())
        payload = json.dumps({"query": mutation}).replace("NODE", "\\u0050R_kwDOpub09876")
        refused = self.run_gh(["api", "graphql", "--input", self.file("n.json", payload)])
        self.assert_refused(refused, "--input n.json .query string 2")
        self.assertIn(f"to {MUTATION}:", refused.stderr.decode())
        # The same payload, its node id written plainly, is refused too, read as public.
        argv = ["api", "graphql", "--input", self.file("p.json", json.dumps({"query": mutation.replace(
            "NODE", "PR_kwDOpriv5678")}))]
        self.setUp_calls()
        self.assert_refused(self.run_gh(argv), "--input p.json .query string 2")

    def test_a_pull_request_url_with_a_user_or_a_port_names_its_repository(self) -> None:
        # gh takes the URL's host without its user and port, so these name o/pub, not the directory's private remote.
        subprocess.run(["git", "init", "-q", str(self.work)], check=True, env=self.env)
        subprocess.run(["git", "-C", str(self.work), "remote", "add", "origin", f"https://github.com/{PRIVATE}.git"],
                       check=True, env=self.env)
        for url in (f"https://x@github.com/{PUBLIC}/pull/11", f"https://github.com:443/{PUBLIC}/pull/11"):
            with self.subTest(url=url):
                self.setUp_calls()
                self.assert_refused(self.run_gh(["pr", "comment", url, "--body", TEXT]), "--body")
        # Control: the same URL form for the private repository goes out.
        argv = ["pr", "comment", f"https://x@github.com:443/{PRIVATE}/pull/11", "--body", TEXT]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)

    def test_a_url_on_a_host_gh_sends_to_github_com_names_its_repository(self) -> None:
        # gh 2.98.0 takes a URL argument's host as written (internal/ghrepo FromURL) and sends the call to github.com for
        # any host go-gh's auth.NormalizeHostname makes github.com: the host in lower case, and any host ending in
        # `.github.com`. Until 2026-11-19 only github.com and www.github.com named a repository, so such a URL of a
        # public repository from a checkout whose remote is private was judged by that remote, and the text went out
        # (dispatch://INFRA-3365, review finding 1).
        self.remotes(("origin", PRIVATE))
        for name, argv in (("foo.github.com", ["pr", "comment", f"https://foo.github.com/{PUBLIC}/pull/1"]),
                           ("GitHub.COM", ["pr", "comment", f"https://GitHub.COM/{PUBLIC}/pull/1"]),
                           ("WWW.GITHUB.COM", ["issue", "comment", f"https://WWW.GITHUB.COM/{PUBLIC}/issues/1"])):
            with self.subTest(form=name):
                self.setUp_calls()
                refused = self.run_gh([*argv, "--body", TEXT])
                self.assert_refused(refused, "--body")
                self.assertIn(f"to {PUBLIC} (public)", refused.stderr.decode())
            with self.subTest(form=name, control="clean body"):
                self.setUp_calls()
                self.assert_sent(self.run_gh([*argv, "--body", CLEAN]), [*argv, "--body", CLEAN])
        # A URL on any other host names a repository there, read as public, as `gh api --hostname` does.
        with self.subTest(form="ghe.example.com"):
            self.setUp_calls()
            refused = self.run_gh(["pr", "comment", "https://ghe.example.com/o/r/pull/1", "--body", TEXT])
            self.assert_refused(refused, "--body")
            self.assertIn("to a repository on ghe.example.com, read as public", refused.stderr.decode())
        # gh reads --hostname so too: FOO.GITHUB.COM is github.com, where the repository is private; and the control,
        # the same call to a public repository there, is refused.
        for repo in (PRIVATE, PUBLIC):
            with self.subTest(form="--hostname FOO.GITHUB.COM", repo=repo):
                argv = ["api", "--hostname", "FOO.GITHUB.COM", "-X", "POST", f"repos/{repo}/issues", "-f", f"title={TEXT}"]
                self.setUp_calls()
                if repo == PRIVATE:
                    self.assert_sent(self.run_gh(argv), argv)
                else:
                    refused = self.run_gh(argv)
                    self.assert_refused(refused, "--raw-field title")
                    self.assertIn(f"to {PUBLIC} (public)", refused.stderr.decode())

    def test_a_url_in_an_argument_that_is_text_names_no_repository(self) -> None:
        # `agent-task create` sends a task's description to the checkout's repository, here a public one, however the
        # description begins. Until 2026-11-19 one that began with a private repository's URL was read as going there,
        # and the term after the URL went out unread (dispatch://INFRA-3365).
        subprocess.run(["git", "init", "-q", str(self.work)], check=True, env=self.env)
        git = ["git", "-C", str(self.work)]
        subprocess.run([*git, "remote", "add", "origin", f"https://github.com/{PUBLIC}.git"], check=True, env=self.env)
        description = f"https://github.com/{PRIVATE}/issues/1 {TEXT}"
        refused = self.run_gh(["agent-task", "create", description])
        self.assert_refused(refused, "argument 1", line=1)
        self.assertIn("to remote origin (public)", refused.stderr.decode())
        # Controls: a URL is where `pr comment` goes, so the private repository's pull request gets the same text; and
        # the description goes out from a private checkout.
        argv = ["pr", "comment", f"https://github.com/{PRIVATE}/pull/1", "--body", TEXT]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)
        subprocess.run([*git, "remote", "set-url", "origin", f"https://github.com/{PRIVATE}.git"], check=True,
                       env=self.env)
        argv = ["agent-task", "create", description]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)

    def test_text_gh_keeps_to_itself_is_not_read(self) -> None:
        # A jq filter and a Go template shape gh's own output, and a dry run prints the pull request it would create.
        endpoint = f"repos/{PUBLIC}/issues"
        for argv in (["api", "-X", "POST", endpoint, "-f", "title=x", "--jq", f'select(.title == "{CANARY}")'],
                     ["api", "-X", "POST", endpoint, "-f", "title=x", "-t", f"{{{{.{CANARY}}}}}"],
                     ["pr", "create", "-R", PUBLIC, "--head", "h", "--title", TEXT, "--body", "x", "--dry-run"]):
            with self.subTest(argv=argv[:3]):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv), argv)
        # Controls: the term in the text the call sends, and a dry run turned off, are refused.
        self.setUp_calls()
        self.assert_refused(self.run_gh(["api", "-X", "POST", endpoint, "-f", f"title={TEXT}", "--jq", ".id"]),
                            "--raw-field title")
        self.setUp_calls()
        self.assert_refused(self.run_gh(["pr", "create", "-R", PUBLIC, "--head", "h", "--title", TEXT, "--body", "x",
                                         "--dry-run=false"]), "--title")

    def test_a_fork_s_new_name_goes_where_the_repository_it_forks_is(self) -> None:
        self.assert_refused(self.run_gh(["repo", "fork", PUBLIC, "--fork-name", TEXT.strip()]), "--fork-name")
        # Control: a fork of a private repository is private.
        argv = ["repo", "fork", PRIVATE, "--fork-name", TEXT.strip()]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)

    def git(self, *arguments: str) -> str:
        """/usr/bin/git in the work directory, past the shims' commit check."""
        done = subprocess.run(["/usr/bin/git", "-C", str(self.work), "-c", "user.name=Test", "-c",
                               "user.email=test@example.invalid", *arguments], env=self.env, capture_output=True,
                              text=True, check=True, timeout=120)
        return done.stdout.strip()

    def test_the_commits_pr_create_fills_from_are_read(self) -> None:
        self.git("init", "-q", "-b", "main")
        # A commit the base holds already: gh lists none of it.
        self.git("commit", "-q", "--allow-empty", "-m", f"base\n\n{TEXT}")
        self.git("remote", "add", "origin", f"https://github.com/{PUBLIC}.git")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.git("checkout", "-q", "-b", "feature")
        self.git("commit", "-q", "--allow-empty", "-m", "clean subject\n\nclean body")
        flags = ("--fill", "-f", "--fill-first", "--fill-verbose")
        for flag in flags:
            with self.subTest(flag=flag, text="clean"):
                self.setUp_calls()
                argv = ["pr", "create", "-R", PUBLIC, flag]
                self.assert_sent(self.run_gh(argv), argv)
        self.git("commit", "-q", "--allow-empty", "-m", f"subject\n\n{TEXT}")
        commit = self.git("rev-parse", "HEAD")[:12]
        for flag in flags:
            with self.subTest(flag=flag, text="term"):
                self.setUp_calls()
                long = "--fill" if flag == "-f" else flag
                self.assert_refused(self.run_gh(["pr", "create", "-R", PUBLIC, flag]), f"{long} commit {commit}")
                self.setUp_calls()
                self.assert_refused(self.run_gh(["pr", "create", "-R", PUBLIC, "--head", "feature", "--base", "main",
                                                 flag]), f"{long} commit {commit}")
        # Controls: the same branch to a private repository, and without a fill flag.
        for argv in (["pr", "create", "-R", PRIVATE, "--fill"],
                     ["pr", "create", "-R", PUBLIC, "--title", "t", "--body", "b"]):
            with self.subTest(argv=argv):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv), argv)
        # The head branch's name is the title of a pull request of more than one commit, `-` read as a space.
        self.git("checkout", "-q", "-b", TEST_TERMS[0].replace(" ", "-"), "main")
        self.git("commit", "-q", "--allow-empty", "-m", "one")
        self.git("commit", "-q", "--allow-empty", "-m", "two")
        self.setUp_calls()
        named = self.run_gh(["pr", "create", "-R", PUBLIC, "--fill"])
        self.assert_refused(named, "--fill head branch name", TEST_TERMS[0], line=1)

    def test_the_tag_message_release_notes_take_is_read(self) -> None:
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "base")
        self.git("remote", "add", "origin", f"https://github.com/{PUBLIC}.git")
        self.git("tag", "-a", "v1", "-m", f"Release\n\n{TEXT}")
        self.git("tag", "-a", "v2", "-m", "Release\n\nclean")
        self.assert_refused(self.run_gh(["release", "create", "v1", "--notes-from-tag"]), "--notes-from-tag tag v1")
        # Controls: a clean tag, the same tag without --notes-from-tag, and the same call to a private repository.
        for argv in (["release", "create", "v2", "--notes-from-tag"], ["release", "create", "v1", "--notes", "x"]):
            with self.subTest(argv=argv):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv), argv)
        self.git("remote", "set-url", "origin", f"https://github.com/{PRIVATE}.git")
        self.setUp_calls()
        argv = ["release", "create", "v1", "--notes-from-tag"]
        self.assert_sent(self.run_gh(argv), argv)

    def test_gh_api_with_an_address_is_judged_by_the_repository_the_address_names(self) -> None:
        # gh sends an endpoint holding `://` to that address, and GitHub's API answers at api.github.com and takes
        # release assets at uploads.github.com, so such an address names its repository as `repos/O/R/…` does.
        asset = self.file("asset.bin", TEXT)

        def upload(repo: str) -> list[str]:
            return ["api", "-X", "POST", "-H", "Content-Type: application/octet-stream", "--input", asset,
                    f"https://uploads.github.com/repos/{repo}/releases/7/assets?name=flac-bin.tar.zst"]

        def edit(repo: str) -> list[str]:
            return ["api", "-X", "PATCH", f"https://api.github.com/repos/{repo}/pulls/11", "-f", f"body={TEXT}"]

        for name, form in (("upload", upload), ("api address", edit)):
            with self.subTest(form=name, repo=PRIVATE):
                self.setUp_calls()
                self.assert_sent(self.run_gh(form(PRIVATE), env={**self.env, "GH_REPO": PRIVATE}), form(PRIVATE))
            with self.subTest(form=name, repo=PUBLIC):
                self.setUp_calls()
                refused = self.run_gh(form(PUBLIC), env={**self.env, "GH_REPO": PRIVATE})
                self.assert_refused(refused, "--input asset.bin" if name == "upload" else "--raw-field body")
                self.assertIn(f"to {PUBLIC} (public)", refused.stderr.decode())
        # A GraphQL address is GraphQL: a mutation, on a private repository's node too, is read as public, and a
        # query is a read.
        for query in ('mutation { addComment(input: {subjectId: "PR_kwDOpriv5678", body: "%s"}) '
                      "{ clientMutationId } }" % TEXT.strip(),
                      'query { search(query: "%s", type: ISSUE, first: 1) { issueCount } }' % CANARY):
            with self.subTest(graphql=query.split()[0]):
                self.setUp_calls()
                argv = ["api", "https://api.github.com/graphql", "-f", f"query={query}"]
                if query.startswith("mutation"):
                    self.assert_refused(self.run_gh(argv), "--raw-field query string 2")
                else:
                    self.assert_sent(self.run_gh(argv), argv)

    def test_gh_api_to_another_host_is_read_as_public(self) -> None:
        # An address on another host, and --hostname, send the text to that host, whatever repository the path names.
        for argv in (["api", "-X", "POST", f"https://other.example/repos/{PRIVATE}/issues", "-f", f"title={TEXT}"],
                     ["api", "-X", "POST", f"http://api.github.com.other.example/repos/{PRIVATE}/issues", "-f",
                      f"title={TEXT}"],
                     ["api", "--hostname", "ghe.example.com", "-X", "POST", f"repos/{PRIVATE}/issues", "-f",
                      f"title={TEXT}"],
                     ["api", "-X", "POST", f"repos/{PRIVATE}/issues", "--hostname=ghe.example.com", "-f",
                      f"title={TEXT}"]):
            with self.subTest(argv=argv[1:4]):
                self.setUp_calls()
                refused = self.run_gh(argv)
                self.assert_refused(refused, "--raw-field title")
                self.assertIn("read as public", refused.stderr.decode())
        # Control: --hostname github.com is github.com, where the repository is private.
        argv = ["api", "--hostname", "github.com", "-X", "POST", f"repos/{PRIVATE}/issues", "-f", f"title={TEXT}"]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)

    # The private repository the rows below write to, from a checkout of it, as the tester's probe does.
    AGENT_INFRA = "theorem-labs/agent-infra"

    def gh_config(self, name: str, config: str, hosts: str | None) -> dict[str, str]:
        """The environment with gh's configuration directory (GH_CONFIG_DIR) a new one, NAME, whose config.yml holds
        CONFIG and whose hosts.yml holds HOSTS, with no hosts.yml for None."""
        directory = self.tmp / "gh-config" / name
        directory.mkdir(parents=True)
        (directory / "config.yml").write_text(config, encoding="utf-8")
        if hosts is not None:
            (directory / "hosts.yml").write_text(hosts, encoding="utf-8")
        return {**self.env, "GH_CONFIG_DIR": str(directory)}

    def test_gh_api_goes_to_the_host_gh_s_config_names(self) -> None:
        # With no --hostname and no GH_HOST, gh 2.98.0 sends a `gh api` call to the one host its config lists when it
        # lists exactly one, else to github.com (go-gh v2.13.0 pkg/auth/auth.go defaultHost): the hosts of config.yml's
        # `hosts:` key, which wins over hosts.yml (pkg/config/config.go load), else those of hosts.yml, each file read
        # as YAML in whatever layout it has. The rows after the first are the tester's, each with the host gh 2.98.0
        # went to, measured with fake tokens (dispatch://INFRA-3365, review round 8): a write with a listed term to a
        # private repository, from a checkout of it, goes out where gh goes to github.com, and is refused as a
        # repository on ghe.example.com, read as public, where gh goes there. Until review round 8 only the keys at
        # column 0 of hosts.yml were read: five of the tester's rows went the other way, and the flow form's host was
        # read as `{"ghe.example.com"`.
        self.answer(self.AGENT_INFRA, "private")
        self.remotes(("origin", self.AGENT_INFRA))
        argv = ["api", f"repos/{self.AGENT_INFRA}/issues", "-f", "title=t", "-f", f"body={TEXT}"]
        host = "    users:\n        x:\n            oauth_token: gho_fake\n    user: x\n    oauth_token: gho_fake\n"
        ghe, github, version = "ghe.example.com:\n" + host, "github.com:\n" + host, 'version: "1"\n'

        def indented(text: str, by: str) -> str:
            return "".join(by + line for line in text.splitlines(keepends=True))

        def hosts_key(entry: str) -> str:
            return version + "hosts:\n" + indented(entry, "    ")

        def flow(name: str) -> str:
            # The check reads only the keys; a quoted token value would trip the push's secret scan.
            return '{"%s": {"users": {"x": {}}, "user": "x", "git_protocol": "https"}}\n' % name

        to_ghe, to_github = "ghe.example.com", "github.com"
        for number, (row, config, hosts, to) in enumerate((
                ("hosts.yml lists only ghe.example.com", version, ghe, to_ghe),
                ("config.yml hosts: key lists only ghe.example.com, no hosts.yml", hosts_key(ghe), None, to_ghe),
                ("hosts.yml indented two spaces, only ghe.example.com", version, indented(ghe, "  "), to_ghe),
                ("hosts.yml after a --- document start, only ghe.example.com", version, "---\n" + ghe, to_ghe),
                ("hosts.yml with the host quoted, only ghe.example.com", version, '"ghe.example.com":\n' + host,
                 to_ghe),
                ("hosts.yml in YAML flow form, only ghe.example.com", version, flow("ghe.example.com"), to_ghe),
                ("config.yml hosts: ghe.example.com, hosts.yml github.com", hosts_key(ghe), github, to_ghe),
                ("hosts.yml lists github.com and ghe.example.com", version, github + ghe, to_github),
                ("hosts.yml lists github.com only", version, github, to_github),
                ("hosts.yml in YAML flow form, github.com only", version, flow("github.com"), to_github),
                ("hosts.yml after a --- document start, github.com only", version, "---\n" + github, to_github),
                ("config.yml hosts: github.com, hosts.yml ghe.example.com", hosts_key(github), ghe, to_github))):
            with self.subTest(row=row, gh_goes_to=to):
                env = self.gh_config(f"row-{number}", config, hosts)
                self.setUp_calls()
                if to == "github.com":
                    self.assert_sent(self.run_gh(argv, env=env), argv)
                    continue
                refused = self.run_gh(argv, env=env)
                self.assert_refused(refused, "--raw-field body")
                self.assertIn(f"to a repository on {to}, read as public:", refused.stderr.decode())
        # Fail closed: where the check cannot be sure of the hosts gh reads, it reads the call as going to a host it
        # cannot name, read as public, so the write is refused, also where gh goes to github.com: measured, gh reads
        # only the first document of a hosts.yml, here github.com's, and goes to ghe.example.com past its anchor.
        for number, (row, hosts) in enumerate((
                ("hosts.yml lists github.com, then ghe.example.com in a second document", github + "---\n" + ghe),
                ("hosts.yml lists only ghe.example.com, with an anchor", "ghe.example.com: &host\n" + host))):
            with self.subTest(row=row, control="the check cannot be sure of the hosts"):
                env = self.gh_config(f"unread-{number}", version, hosts)
                self.setUp_calls()
                refused = self.run_gh(argv, env=env)
                self.assert_refused(refused, "--raw-field body")
                self.assertIn("to a repository on a host the check cannot read in gh's config, read as public:",
                              refused.stderr.decode())

    def test_a_repository_named_without_a_host_goes_to_gh_s_default_host(self) -> None:
        # gh 2.98.0 reads a repository named as `OWNER/REPO`, with no host, by -R, GH_REPO or a repository argument, as
        # a repository on its default host (ghrepo.FromFullName; go-gh v2.13.0 auth.DefaultHost: GH_HOST, else the one
        # host gh's config lists, else github.com). Measured 2026-11-20 with fake tokens: with hosts.yml listing only
        # ghe.example.com, `pr comment 1 -R cli/cli`, `issue create -R cli/cli`, `GH_REPO=cli/cli pr comment 1` and
        # `repo edit cli/cli` went to ghe.example.com, and with it listing only github.com to github.com. So a write
        # with a listed term to a private repository named so is refused as a repository on ghe.example.com, read as
        # public, where gh's config lists only that host, and goes out where it lists only github.com. With no
        # repository named, gh goes by the remote's host (measured: to github.com), so the same write from the checkout
        # of the private repository goes out. Where the check cannot read gh's config, the repository is on a host it
        # cannot name, read as public. Until review round 8 of dispatch://INFRA-3365 such a name was github.com's
        # whatever gh's config said.
        self.answer(self.AGENT_INFRA, "private")
        self.remotes(("origin", self.AGENT_INFRA))
        host, version = "    oauth_token: gho_fake\n    user: x\n", 'version: "1"\n'
        ghe = self.gh_config("only-ghe", version, "ghe.example.com:\n" + host)
        github = self.gh_config("only-github", version, "github.com:\n" + host)
        repository = self.AGENT_INFRA
        for form, argv, extra, field in (
                ("-R, pr comment", ["pr", "comment", "1", "-R", repository, "--body", TEXT], {}, "--body"),
                ("-R, issue create", ["issue", "create", "-R", repository, "-t", "t", "--body", TEXT], {}, "--body"),
                ("GH_REPO", ["pr", "comment", "1", "--body", TEXT], {"GH_REPO": repository}, "--body"),
                ("a repository argument", ["repo", "edit", repository, "--description", TEXT], {}, "--description")):
            with self.subTest(form=form, hosts_yml="only ghe.example.com"):
                self.setUp_calls()
                refused = self.run_gh(argv, env={**ghe, **extra})
                self.assert_refused(refused, field)
                self.assertIn("to a repository on ghe.example.com, read as public:", refused.stderr.decode())
            with self.subTest(form=form, hosts_yml="only github.com"):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env={**github, **extra}), argv)
        with self.subTest(form="no repository named, from the checkout", hosts_yml="only ghe.example.com"):
            argv = ["pr", "comment", "1", "--body", TEXT]
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv, env=ghe), argv)
        with self.subTest(form="-R, pr comment", control="hosts.yml lists only ghe.example.com, with an anchor"):
            env = self.gh_config("anchor", version, "ghe.example.com: &host\n" + host)
            self.setUp_calls()
            refused = self.run_gh(["pr", "comment", "1", "-R", repository, "--body", TEXT], env=env)
            self.assert_refused(refused, "--body")
            self.assertIn("to a repository on a host the check cannot read in gh's config, read as public:",
                          refused.stderr.decode())

    def checkout(self, *remotes: tuple[str, str], resolved: str = "") -> None:
        """Make the work directory a new checkout whose remotes are REMOTES, (name, URL) pairs, and whose origin's
        `gh-resolved` (`gh repo set-default`) is RESOLVED when given."""
        shutil.rmtree(self.work / ".git", ignore_errors=True)
        subprocess.run(["git", "init", "-q", str(self.work)], check=True, env=self.env)
        git = ["git", "-C", str(self.work)]
        for name, url in remotes:
            subprocess.run([*git, "remote", "add", name, url], check=True, env=self.env)
        if resolved:
            subprocess.run([*git, "config", "remote.origin.gh-resolved", resolved], check=True, env=self.env)

    def test_the_repository_gh_repo_set_default_names_is_on_its_remote_s_host(self) -> None:
        # `gh repo set-default` can name a repository that is no remote's, as a remote's `gh-resolved` value
        # (`OWNER/REPO`), and gh 2.98.0 takes that repository on the remote's host, not on its default host: measured
        # 2026-11-20 with fake tokens, with hosts.yml listing only ghe.example.com, a checkout whose origin is
        # github.com's and whose origin's `gh-resolved` is `other/repo` sent `pr comment 1` to github.com, and one whose
        # origin is on ghe.example.com sent it there. So, gh's config listing only ghe.example.com, a write with a
        # listed term goes out from a checkout whose github.com remote is resolved to a private repository, and is
        # refused from one whose remote on ghe.example.com is, as a repository on ghe.example.com, read as public
        # (gh_call_host, GH_GIVEN_REMOTE). Until review round 8 of dispatch://INFRA-3365 the value was github.com's
        # whatever its remote, and the second write went out.
        self.answer(self.AGENT_INFRA, "private")
        env = self.gh_config("only-ghe", 'version: "1"\n', "ghe.example.com:\n    oauth_token: gho_fake\n    user: x\n")
        argv = ["pr", "comment", "1", "--body", TEXT]
        for remote in ("https://github.com/o/fork.git", "https://ghe.example.com/o/fork.git"):
            with self.subTest(remote=remote):
                self.checkout(("origin", remote), resolved=self.AGENT_INFRA)
                self.setUp_calls()
                if remote.startswith("https://github.com/"):
                    self.assert_sent(self.run_gh(argv, env=env), argv)
                    continue
                refused = self.run_gh(argv, env=env)
                self.assert_refused(refused, "--body")
                self.assertIn("to a repository on ghe.example.com, read as public:", refused.stderr.decode())

    def test_gh_takes_a_remote_on_a_host_gh_s_config_lists(self) -> None:
        # gh 2.98.0 takes a remote on github.com, on GH_HOST's host or on a host its config lists, and drops any other
        # (gh_call_host, GH_GIVEN_REMOTE). Measured 2026-11-20 with fake tokens, `pr comment 1` from a checkout whose
        # remotes are a repository on github.com and o/r on ghe.example.com, origin and upstream either way round: with
        # hosts.yml listing only ghe.example.com gh went to ghe.example.com; listing both hosts, to the upstream's;
        # listing only github.com, to github.com; and with o/r on ghe.example.com as the one remote and hosts.yml
        # listing only github.com, gh refused the call, "none of the git remotes configured for this repository point
        # to a known GitHub host", and sent nothing. An ssh remote on ghe.example.com was the same: gh went to it with
        # hosts.yml listing only ghe.example.com, and to github.com with it listing only github.com. So a write with a
        # listed term from a checkout whose other remote is a private repository on github.com is refused, as a
        # repository on ghe.example.com, read as public, wherever gh may take the remote on ghe.example.com, and goes
        # out where gh drops it. The check keeps each remote gh may take: listing both hosts, gh may take either. An ssh
        # remote's host is read only by scripts/github-repo-visibility, which reads whether it is GitHub's, so one that
        # is not is on a host the check cannot read, kept wherever gh's config lists a host that is not github.com.
        # Until review round 8 of dispatch://INFRA-3365 a remote was kept only where its URL was GitHub's, so these
        # writes were judged by the private repository and went out, while gh sent them to ghe.example.com.
        self.answer(self.AGENT_INFRA, "private")
        github, ghe, ssh = (f"https://github.com/{self.AGENT_INFRA}.git", "https://ghe.example.com/o/r.git",
                            "git@ghe.example.com:o/r.git")
        host, version = "    oauth_token: gho_fake\n    user: x\n", 'version: "1"\n'
        only_ghe, only_github = "ghe.example.com:\n" + host, "github.com:\n" + host
        on_ghe = "a repository on ghe.example.com, read as public"
        unread_url = "a repository on a host the check cannot read in a URL that is not GitHub's, read as public"
        # git's ssh, which the helper runs as `ssh -G`, reads no ssh command of the caller's.
        env = {key: value for key, value in self.env.items() if key not in ("GIT_SSH_COMMAND", "GIT_SSH")}
        argv = ["pr", "comment", "1", "--body", TEXT]
        for number, (row, remotes, hosts, shown) in enumerate((
                ("hosts.yml lists only ghe.example.com, its remote origin", (("origin", ghe), ("upstream", github)),
                 only_ghe, on_ghe),
                ("hosts.yml lists only ghe.example.com, its remote upstream", (("origin", github), ("upstream", ghe)),
                 only_ghe, on_ghe),
                ("hosts.yml lists github.com and ghe.example.com", (("origin", github), ("upstream", ghe)),
                 only_github + only_ghe, on_ghe),
                ("hosts.yml lists only github.com", (("origin", github), ("upstream", ghe)), only_github, ""),
                ("the ghe.example.com remote alone, hosts.yml listing only github.com", (("origin", ghe),),
                 only_github, "no GitHub repository found for the call, read as public"),
                ("hosts.yml with an anchor", (("origin", github), ("upstream", ghe)),
                 "ghe.example.com: &host\n" + host, on_ghe),
                ("an ssh remote, hosts.yml listing only ghe.example.com", (("origin", github), ("upstream", ssh)),
                 only_ghe, unread_url),
                ("an ssh remote, hosts.yml listing only github.com", (("origin", github), ("upstream", ssh)),
                 only_github, ""))):
            with self.subTest(row=row):
                self.checkout(*remotes)
                run_env = {**env, **self.gh_config(f"remotes-{number}", version, hosts)}
                self.setUp_calls()
                if not shown:
                    self.assert_sent(self.run_gh(argv, env=run_env), argv)
                    continue
                refused = self.run_gh(argv, env=run_env)
                self.assert_refused(refused, "--body")
                self.assertIn(shown, refused.stderr.decode())

    def test_the_host_of_branch_repo_gh_host_and_a_git_url_is_read_as_gh_reads_it(self) -> None:
        # Each place the gate decides which host a call reaches reads that host through one function, gh_call_host.
        # The decision points the other tests leave out, each with gh's config listing the host the row says:
        # --branch-repo `OWNER/REPO`, on gh's default host (the issue's repository here named as
        # `github.com/OWNER/REPO`, so only the branch's repository is on ghe.example.com); GH_HOST, which a call that is
        # not `gh api` goes to as a whole; and -R as a git URL, on its own host (measured 2026-11-20 with fake tokens:
        # `pr comment 1 -R git@ghe.example.com:cli/cli.git` went to ghe.example.com with hosts.yml listing only
        # github.com), which only scripts/github-repo-visibility reads, so one that is not GitHub's is on a host the
        # check cannot read. Each is refused where gh sends it to ghe.example.com, read as public, and goes out where
        # gh sends it to the private repository on github.com.
        self.answer(self.AGENT_INFRA, "private")
        self.checkout(("origin", f"https://github.com/{self.AGENT_INFRA}.git"))
        host, version = "    oauth_token: gho_fake\n    user: x\n", 'version: "1"\n'
        ghe = self.gh_config("only-ghe", version, "ghe.example.com:\n" + host)
        github = self.gh_config("only-github", version, "github.com:\n" + host)
        env = {key: value for key, value in self.env.items() if key not in ("GIT_SSH_COMMAND", "GIT_SSH")}
        develop = ["issue", "develop", "1", "-R", f"github.com/{self.AGENT_INFRA}", "--branch-repo",
                   self.AGENT_INFRA, "--name", f"fix-{CANARY}"]
        comment = ["pr", "comment", "1", "-R", self.AGENT_INFRA, "--body", TEXT]
        to_ghe = "a repository on ghe.example.com, read as public"
        # (decision point, the call gh sends to ghe.example.com and its config, the field and the match its refusal
        # names, the target it shows; the call gh sends to github.com and its config)
        for point, refused_argv, refused_env, field, matched, shown, sent_argv, sent_env in (
                ("--branch-repo", develop, ghe, "--name", CANARY, to_ghe, develop, github),
                ("GH_HOST", comment, {**github, "GH_HOST": "ghe.example.com"}, "--body", CANARY.upper(), to_ghe,
                 comment, {**github, "GH_HOST": "github.com"}),
                ("-R as a git URL",
                 ["pr", "comment", "1", "-R", f"git@ghe.example.com:{self.AGENT_INFRA}.git", "--body", TEXT], github,
                 "--body", CANARY.upper(),
                 "a repository on a host the check cannot read in a URL that is not GitHub's, read as public",
                 ["pr", "comment", "1", "-R", f"git@github.com:{self.AGENT_INFRA}.git", "--body", TEXT], github)):
            with self.subTest(point=point, gh_sends_to="ghe.example.com"):
                self.setUp_calls()
                refused = self.run_gh(refused_argv, env={**env, **refused_env})
                self.assert_refused(refused, field, matched, line=1)
                self.assertIn(shown, refused.stderr.decode())
            with self.subTest(point=point, gh_sends_to="github.com"):
                self.setUp_calls()
                self.assert_sent(self.run_gh(sent_argv, env={**env, **sent_env}), sent_argv)

    def test_a_repository_argument_on_another_host_is_read_as_public(self) -> None:
        # `repo edit HOST/OWNER/REPO` edits a repository on HOST, where gh sends the call, so it is read as public, as
        # a URL argument on another host is. Until review round 2 of dispatch://INFRA-3365 it was read as a github.com
        # repository whose visibility could not be read.
        with self.subTest(form="HOST/OWNER/REPO"):
            refused = self.run_gh(["repo", "edit", "ghe.example.com/o/r", "--description", TEXT])
            self.assert_refused(refused, "--description")
            self.assertIn("gh repo edit to a repository on ghe.example.com, read as public: --description line 1:",
                          refused.stderr.decode())
        # Control: the same edit of a private repository named with github.com as its host goes out.
        with self.subTest(control="github.com/OWNER/REPO"):
            argv = ["repo", "edit", f"github.com/{PRIVATE}", "--description", TEXT]
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv), argv)

    def test_a_remote_on_an_ssh_alias_of_github_com_names_its_github_com_repository(self) -> None:
        # gh resolves the host of a remote's ssh URL as ssh does (ssh.NewTranslator, `ssh -G`), and so does
        # scripts/github-repo-visibility, which keeps the remote as GitHub's; its host is then github.com, in each scp
        # spelling. ssh reads its config from the passwd home, not from HOME, so an `ssh` first on PATH runs the real
        # one with `-F` naming the config under the test's HOME, `Host gh-work` / `HostName github.com`. Until review
        # round 3 of dispatch://INFRA-3365 a second parser read `git@gh-work:o/priv.git` as a repository on gh-work,
        # read as public, while `gh-work:o/priv.git` was o/priv.
        ssh = shutil.which("ssh")
        self.assertIsNotNone(ssh, "no ssh on PATH, which scripts/github-repo-visibility runs as `ssh -G`")
        (self.tmp / "home" / ".ssh").mkdir()
        (self.tmp / "home" / ".ssh" / "config").write_text("Host gh-work\n    HostName github.com\n", encoding="utf-8")
        write_executable(self.bin / "ssh", f'#!/bin/bash\nexec {ssh} -F "$HOME/.ssh/config" "$@"\n')
        env = {key: value for key, value in self.env.items() if key not in ("GIT_SSH_COMMAND", "GIT_SSH")}
        argv = ["pr", "comment", "1", "--body", f"see {CANARY}"]
        for name, url in (("git@gh-work:", f"git@gh-work:{PRIVATE}.git"), ("gh-work:", f"gh-work:{PRIVATE}.git")):
            shutil.rmtree(self.work / ".git", ignore_errors=True)
            subprocess.run(["git", "init", "-q", str(self.work)], check=True, env=env)
            subprocess.run(["git", "-C", str(self.work), "remote", "add", "origin", url], check=True, env=env)
            with self.subTest(form=name, visibility="private"):
                self.answer(PRIVATE, "private")
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)
            with self.subTest(form=name, visibility="public"):
                self.answer(PRIVATE, "public")
                self.setUp_calls()
                refused = self.run_gh(argv, env=env)
                self.assert_refused(refused, "--body", CANARY, line=1)
                self.assertIn("to remote origin (public):", refused.stderr.decode())
                self.assertEqual(self.visibility_reads()[-1], f"repos/{PRIVATE}")

    # --- each value read on its own, decoded, as GitHub stores it (dispatch://INFRA-4383, R1) ----------------------

    # One text as GitHub stores it, with the canary on its third line.
    STORED = f"First line.\n\nThe third line names {CANARY.upper()} once.\n"

    def transports(self, repo: str, text: str) -> list[tuple[str, list[str], bytes, str]]:
        """(name, argv, stdin, the field the refusal names) for each way a gh call carries the one stored TEXT, all
        aimed at REPO."""
        f = self.file
        comments = f"repos/{repo}/issues/1/comments"
        return [
            ("--input compact JSON", ["api", comments, "--input", f("compact.json", json.dumps({"body": text},
                                                                                            separators=(",", ":")))],
             b"", "--input compact.json .body"),
            ("--input indented JSON", ["api", comments, "--input", f("indented.json", json.dumps({"body": text}, indent=2))],
             b"", "--input indented.json .body"),
            ("-f", ["api", comments, "-f", f"body={text}"], b"", "--raw-field body"),
            ("-F", ["api", comments, "-F", f"body={text}"], b"", "--field body"),
            ("-F key=@file", ["api", comments, "-F", f"body=@{f('stored.md', text)}"], b"", "--field body=@stored.md"),
            ("--body-file", ["issue", "comment", "1", "-R", repo, "--body-file", f("body.md", text)], b"",
             "--body-file body.md"),
            ("stdin", ["issue", "comment", "1", "-R", repo, "--body-file", "-"], text.encode(),
             "--body-file - (standard input)"),
            ("--input - nested array", ["api", comments, "--input", "-"], json.dumps({"items": [["first", text]]}).encode(),
             "--input - (standard input) .items[0][1]"),
            ("workflow run --json", ["workflow", "run", "w.yml", "-R", repo, "--json"], json.dumps({"note": text}).encode(),
             "--json (standard input) .note"),
        ]

    def test_one_stored_text_gets_one_verdict_in_every_transport(self) -> None:
        # Each form names the value by its field or JSON path and the line in that value, the third, as GitHub shows it.
        for name, argv, stdin, field in self.transports(PUBLIC, self.STORED):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_refused(self.run_gh(argv, stdin), field, line=3)
        # Controls: the same text to a private repository, and the text without the canary to the public one, go out
        # unchanged in every form.
        for repo, text in ((PRIVATE, self.STORED), (PUBLIC, self.STORED.replace(CANARY.upper(), "nothing"))):
            for name, argv, stdin, _ in self.transports(repo, text):
                with self.subTest(form=name, repo=repo):
                    self.setUp_calls()
                    self.assert_sent(self.run_gh(argv, stdin), argv, stdin)

    def test_two_values_are_never_read_as_one_and_a_wrapped_value_is_one(self) -> None:
        first, rest = TEST_TERMS[0].split(" ", 1)
        comments = f"repos/{PUBLIC}/issues/1/comments"
        apart = {"items": [f"See {first}", f"{rest} soon."]}
        for name, argv in (("compact JSON", ["api", comments, "--input", self.file("apart.json", json.dumps(apart))]),
                           ("indented JSON", ["api", comments, "--input",
                                              self.file("apart2.json", json.dumps(apart, indent=2))]),
                           ("two fields", ["api", comments, "-f", f"title=See {first}", "-f", f"body={rest} soon."]),
                           ("a key and its value", ["api", comments, "-f", f"See {first}={rest} soon."])):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv), argv)
        # Control: the term's words wrapped inside one value are the term on the page.
        wrapped = {"items": [f"See {first}\n{rest} soon."]}
        for name, payload in (("compact JSON", json.dumps(wrapped)), ("indented JSON", json.dumps(wrapped, indent=2))):
            with self.subTest(form=name, wrapped=True):
                self.setUp_calls()
                argv = ["api", comments, "--input", self.file("wrapped.json", payload)]
                self.assert_refused(self.run_gh(argv), "--input wrapped.json .items[0]", f"{first}\\n{rest}", line=1)

    def test_a_literal_backslash_in_a_value_is_read_as_written(self) -> None:
        # GitHub stores a body's `\u0070` as those six characters, and so a JSON body's `\\u0070`: neither is the term.
        spelled = "See " + TEST_TERMS[1].replace("p", "\\u0070", 1) + " soon."
        comments = f"repos/{PUBLIC}/issues/1/comments"
        for name, argv in (("--body", ["issue", "comment", "1", "-R", PUBLIC, "--body", spelled]),
                           ("-f", ["api", comments, "-f", f"body={spelled}"]),
                           ("--input JSON", ["api", comments, "--input", self.file("lit.json", json.dumps({"body": spelled}))])):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv), argv)
        # Control: a JSON escape in an --input body is the letter GitHub stores.
        escaped = json.dumps({"body": f"See {TEST_TERMS[1]} soon."}).replace("p", "\\u0070", 1)
        self.setUp_calls()
        self.assert_refused(self.run_gh(["api", comments, "--input", self.file("esc.json", escaped)]), "--input esc.json .body",
                            TEST_TERMS[1], line=1)

    def test_a_term_spelled_with_markdown_escapes_is_read_as_github_renders_it(self) -> None:
        # GitHub renders a body as CommonMark, which undoes a backslash before an ASCII punctuation character and an
        # entity or numeric character reference, so `vintrapolis\-canary\-7` and `vintrapolis&#45;canary-7` show the
        # term. Until review round 2 of dispatch://INFRA-3365 such a body went to a public repository unread.
        env = self.ygo_fork()
        self.answer("sjawhar/ygo", "public")
        escaped = CANARY.replace("-", "\\-")
        comment = ["pr", "comment", "11", "-R", PUBLIC, "--body"]
        ygo = ["pr", "comment", "1", "-R", "sjawhar/ygo", "--body"]
        input_body = self.file("md.json", json.dumps({"body": f"See {escaped} soon."}))
        for name, argv, run_env, field, matched, term_list in (
                ("backslash escapes", [*comment, f"See {escaped} soon.\n"], self.env, "--body", CANARY, GLOBAL),
                ("a decimal reference", [*comment, CANARY.replace("-", "&#45;", 1)], self.env, "--body", CANARY, GLOBAL),
                ("a hexadecimal reference", [*comment, CANARY.replace("-", "&#x2D;", 1)], self.env, "--body", CANARY,
                 GLOBAL),
                ("a named reference", [*comment, TEST_TERMS[0].replace(" ", "&nbsp;", 1)], self.env, "--body",
                 TEST_TERMS[0].replace(" ", "\u00a0", 1), GLOBAL),
                ("a JSON body's string", ["api", f"repos/{PUBLIC}/issues/1/comments", "--input", input_body], self.env,
                 "--input md.json .body", CANARY, GLOBAL),
                ("INFRA\\-1 under the ygo list", [*ygo, "see INFRA\\-1"], env, "--body", "INFRA-", YGO_LIST),
                ("dispatch&colon;// under the ygo list", [*ygo, "see dispatch&colon;//x"], env, "--body", "dispatch://",
                 YGO_LIST)):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_refused(self.run_gh(argv, env=run_env), f"{field} (Markdown escapes undone)", matched,
                                    term_list, line=1)
        # A term written as is and with an escape too is shown once, as written: the escaped one is its twin.
        with self.subTest(form="as written and escaped"):
            self.setUp_calls()
            refused = self.run_gh([*ygo, "see INFRA-1 and INFRA\\-1"], env=env)
            self.assert_refused(refused, "--body", "INFRA-", YGO_LIST, line=1)
            self.assertNotIn("Markdown escapes undone", refused.stderr.decode())
        # Controls: the escaped term to a private repository; a clean body full of escapes and references; and
        # `&hyphen;`, which is U+2010, not the `-` the term holds, so the body does not show the term.
        for name, argv in (("private", ["pr", "comment", "11", "-R", PRIVATE, "--body", f"See {escaped} soon.\n"]),
                           ("clean", [*comment, "a\\*b &amp; c\\_d"]),
                           ("&hyphen;", [*comment, CANARY.replace("-", "&hyphen;", 1)])):
            with self.subTest(control=name):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv), argv)

    def test_an_escape_inside_a_graphql_string_is_read_as_the_letter_it_spells(self) -> None:
        # dispatch://BET-727: a GraphQL string decodes its own \u escape, inside the JSON string that carries the
        # document, so GitHub stores the term.
        literal = TEST_TERMS[1].replace("p", "\\u0070", 1)
        mutation = 'mutation { addComment(input: {subjectId: "I_1", body: "See %s soon."}) { clientMutationId } }' % literal
        payload = json.dumps({"query": mutation})
        for name, argv, stdin, field in (
                ("--input FILE", ["api", "graphql", "--input", self.file("bet.json", payload)], b"",
                 "--input bet.json .query string 2"),
                ("--input -", ["api", "graphql", "--input", "-"], payload.encode(), "--input - (standard input) .query string 2"),
                ("-f query=", ["api", "graphql", "-f", f"query={mutation}"], b"", "--raw-field query string 2")):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_refused(self.run_gh(argv, stdin), field, TEST_TERMS[1], line=1)
        # A block string, its common indentation removed, and a variable are values too.
        block = json.dumps({"query": 'mutation { addComment(input: {subjectId: "I_1", body: """\n    See\n    %s\n  """}) '
                                     "{ clientMutationId } }" % TEXT.strip()})
        self.setUp_calls()
        line = self.assert_refused(self.run_gh(["api", "graphql", "--input", self.file("block.json", block)]),
                                   "--input block.json .query string 2", line=2)
        self.assertEqual(line, TEXT.strip())
        variables = json.dumps({"query": 'mutation($b: String!) { addComment(input: {subjectId: "I_1", body: $b}) '
                                         "{ clientMutationId } }", "variables": {"b": TEXT}})
        self.setUp_calls()
        self.assert_refused(self.run_gh(["api", "graphql", "--input", self.file("var.json", variables)]),
                            "--input var.json .variables.b", line=1)
        # A document that does not parse (its string never ends) is read as before: as written and with its escapes
        # decoded.
        broken = json.dumps({"query": 'mutation { addComment(input: {subjectId: "I_1", body: "See %s soon.) }' % literal})
        self.setUp_calls()
        self.assert_refused(self.run_gh(["api", "graphql", "--input", self.file("broken.json", broken)]),
                            "--input broken.json .query (escapes decoded)", TEST_TERMS[1])
        # Control: a clean request with an escape in a value goes out, its bytes unchanged.
        clean = json.dumps({"query": mutation.replace(literal, "caf\\u00e9")}).encode()
        argv = ["api", "graphql", "--input", "-"]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv, clean), argv, clean)

    def test_a_release_asset_is_read_as_the_bytes_gh_uploads(self) -> None:
        # Found by the review of dispatch://INFRA-4383: GitHub stores a release asset as the bytes of the upload, and
        # never decodes them. Read as JSON, the asset's `\u002f` became the `/` of a token the repository allows, which
        # the bytes do not hold, and the asset went out with the scoped term in it. An allowed token counts only where
        # the bytes gh sends hold it.
        (repository, (token, _)), = ALLOWED_TEST_TOKENS.items()
        self.answer(repository, "public")
        uploads = f"https://uploads.github.com/repos/{repository}/releases/7/assets?name=asset"

        def upload(name: str, payload: str, endpoint: str = uploads) -> list[str]:
            return ["api", "-X", "POST", "-H", "Content-Type: application/octet-stream", "--input", self.file(name, payload),
                    endpoint]

        escaped = json.dumps({"x": token}).replace("/", "\\u002f")
        for name, argv in (("uploads.github.com", upload("escaped.json", escaped)),
                           ("a release's assets path", upload("escaped.json", escaped, f"repos/{repository}/releases/7/assets"))):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_refused(self.run_gh(argv), "--input escaped.json", SCOPED, f"the {SCOPE} list", line=1)
        # Control: the allowed token as the bytes hold it goes out, those bytes unchanged.
        argv = upload("allowed.json", json.dumps({"x": token}))
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)

    def test_a_graphql_comment_is_not_read_and_a_document_that_does_not_parse_is_read_both_ways(self) -> None:
        # GitHub stores a document's strings and variables; nobody stores its comments.
        commented = 'mutation { # %s\n addComment(input: {subjectId: "I_1", body: "x"}) { clientMutationId } }' % TEXT.strip()
        argv = ["api", "graphql", "-f", f"query={commented}"]
        self.assert_sent(self.run_gh(argv), argv)
        # A document that does not parse (its braces never close) is read as before, as written: the comment counts.
        self.setUp_calls()
        self.assert_refused(self.run_gh(["api", "graphql", "-f", f"query={commented[:-2]}"]), "--raw-field query", line=1)
        # And in an --input body, its `.query`.
        self.setUp_calls()
        payload = self.file("open.json", json.dumps({"query": commented[:-2]}))
        self.assert_refused(self.run_gh(["api", "graphql", "--input", payload]), "--input open.json .query", line=1)
        # A document whose brackets match but whose grammar does not (found by the review of dispatch://INFRA-4383):
        # a number where a field's name goes, and an argument with no `:`.
        for name, document, line in (
                ("a number for a field", "mutation { 123 # %s\n}" % TEXT.strip(), 1),
                ("an argument with no colon", 'mutation {\n  addComment(input {body: "clean"}) { clientMutationId }\n  # %s\n}'
                 % TEXT.strip(), 3),
                # A surrogate GraphQL rejects: alone, or as a code point in braces.
                ("an unpaired surrogate", 'mutation { a(b: "\\uD800") # %s\n}' % TEXT.strip(), 1),
                ("a lone low surrogate", 'mutation { a(b: "\\uDC00") # %s\n}' % TEXT.strip(), 1),
                ("a surrogate in braces", 'mutation { a(b: "\\u{D800}") # %s\n}' % TEXT.strip(), 1)):
            with self.subTest(document=name):
                self.setUp_calls()
                self.assert_refused(self.run_gh(["api", "graphql", "-f", f"query={document}"]), "--raw-field query",
                                    line=line)
        # Control: a surrogate pair is one letter, and the document parses, so its comment is not read.
        paired = 'mutation { a(b: "\\uD83D\\uDE00") # %s\n}' % TEXT.strip()
        argv = ["api", "graphql", "-f", f"query={paired}"]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)

    # --- what a refusal shows (dispatch://INFRA-4383, R2) -----------------------------------------------------------

    def test_a_refusal_shows_the_term_and_a_bounded_line(self) -> None:
        body = "a" * 500 + f" {CANARY.upper()} " + "b" * 500 + "\nsecond line\n"
        line = self.assert_refused(self.run_gh(["issue", "comment", "1", "-R", PUBLIC, "--body", body]), "--body", line=1)
        self.assertTrue(line.startswith("…a") and line.endswith("b…"), line)
        self.assertLess(len(line), 250, line)
        self.assertNotIn("second line", line)

    def test_in_an_agent_session_the_check_refuses_before_gh_runs(self) -> None:
        # The agent block (its config directory, the routing include, the public text gate) runs first, and the term
        # is still refused with gh never run.
        self.assert_refused(self.run_gh(["issue", "comment", "1", "-R", PUBLIC, "--body", TEXT], env=self.agent()),
                            "--body", line=1)
        # Control: the same text to a private repository goes out unchanged.
        argv = ["issue", "comment", "1", "-R", PRIVATE, "--body", TEXT]
        self.assert_sent(self.run_gh(argv, env=self.agent()), argv)

    # --- what the check does not touch --------------------------------------------------------------------------

    def test_gh_api_with_no_body_field_passes_untouched(self) -> None:
        # The term list cannot be read: a call the check read would be refused for that (control below).
        env = {**self.env, "DOTFILES_LEAK_TERMS_FILE": str(self.tmp / "missing.sops.yaml")}
        argv = ["api", f"repos/{PUBLIC}/issues?labels={CANARY}"]
        result = self.run_gh(argv, b"piped", env)
        self.assert_sent(result, argv, b"piped")
        self.assertEqual(self.gh_calls()[0][2], "fifo", "the shim buffered the stdin of a call it should not touch")
        self.assertEqual(self.visibility_reads(), [])
        # Control: the same endpoint with a body field is read, and refused because the list cannot be.
        self.setUp_calls()
        control = self.run_gh(["api", f"repos/{PUBLIC}/issues", "-f", "title=x"], b"", env)
        self.assertEqual(control.returncode, 2, control.stderr.decode())
        self.assertIn("the term list is missing", control.stderr.decode())
        self.assertEqual(self.gh_calls(), [])

    def test_a_file_path_is_not_text_gh_sends(self) -> None:
        (self.work / "notes").mkdir()
        path = f"notes/{CANARY}.md"
        (self.work / path).write_text(CLEAN, encoding="utf-8")
        argv = ["pr", "comment", "11", "-R", PUBLIC, "--body-file", path]
        self.assert_sent(self.run_gh(argv), argv)
        # Control: when the file holds the term, the refusal names the file as its path is written.
        (self.work / path).write_text(TEXT, encoding="utf-8")
        self.setUp_calls()
        refused = self.run_gh(argv)
        self.assert_refused(refused, f"--body-file notes/{CANARY}.md")

    # --- an agent session: the public text gate and this check read one stdin buffer -------------------------------

    def test_in_an_agent_session_both_checks_read_stdin_and_gh_gets_the_same_bytes(self) -> None:
        stub = self.bin / "public-check"
        # It appends: the visibility read the check makes runs this gate too, on its own empty stdin.
        write_executable(stub, "#!/bin/bash\n[ -n \"${PUBLIC_LEAK_CHECK_STDIN:-}\" ] || exit 4\n"
                               "cat \"$PUBLIC_LEAK_CHECK_STDIN\" >> \"$STUB_READ\"\n")
        env = self.agent(PUBLIC_LEAK_CHECK=str(stub), STUB_READ=str(self.tmp / "public-read"))
        refused = self.run_gh(["pr", "comment", "11", "-R", PUBLIC, "--body-file", "-"], TEXT.encode(), env)
        self.assert_refused(refused, "--body-file - (standard input)")
        self.assertEqual((self.tmp / "public-read").read_bytes(), TEXT.encode())
        argv = ["pr", "comment", "11", "-R", PRIVATE, "--body-file", "-"]
        sent = self.run_gh(argv, TEXT.encode(), env)
        self.assert_sent(sent, argv, TEXT.encode())
        self.assertEqual(list((self.tmp / "t").iterdir()), [], "the shim left a stdin buffer behind")


class Tables(unittest.TestCase):
    """The check reads gh's arguments as gh does, from a table of each command's options and of the commands whose first
    argument is the repository the call goes to."""

    def scanner(self):
        import importlib.machinery
        import importlib.util
        import sys
        loader = importlib.machinery.SourceFileLoader("leak_terms_check_gh", str(CHECK))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[loader.name] = module
        loader.exec_module(module)
        return module

    def real_gh(self) -> str | None:
        """The gh the shim hands a call to: the first gh on PATH that is not a shim."""
        for directory in os.environ["PATH"].split(os.pathsep):
            candidate = Path(directory) / "gh"
            if candidate.is_file() and os.access(candidate, os.X_OK) and b"knives-gh-shim" not in candidate.read_bytes()[:512]:
                return str(candidate)
        return None

    def test_the_option_tables_are_the_installed_gh_s_own(self) -> None:
        gh = self.real_gh()
        if gh is None:
            self.skipTest("no gh on PATH")
        module = self.scanner()
        line = re.compile(r"^\s+(?:(-[A-Za-z0-9]), )?(--[A-Za-z0-9][A-Za-z0-9-]*)( \S+)?(?:\s{2,}|$)")
        commands = set(module.GH_TEXT_COMMANDS)
        self.assertLessEqual(module.GH_REPOSITORY_ARGUMENT, commands)
        self.assertLessEqual(module.GH_URL_ARGUMENT, commands)
        for (group, sub), table in module.GH_TEXT_COMMANDS.items():
            with self.subTest(command=f"{group} {sub}"):
                text = subprocess.run([gh, group, *([sub] if sub else []), "--help"], capture_output=True, text=True,
                                      check=True, timeout=60).stdout
                values, flags, section, usage = set(), set(), "", []
                for help_line in text.splitlines():
                    if help_line and not help_line.startswith(" "):
                        section = help_line.strip()
                    elif section in ("FLAGS", "INHERITED FLAGS") and (m := line.match(help_line)):
                        (values if m.group(3) else flags).update(n for n in m.group(1, 2) if n)
                    elif section == "USAGE" and not usage:
                        usage = help_line.split()
                options = module.gh_options(table)
                self.assertEqual({name for name, takes in options.items() if takes}, values)
                self.assertEqual({name for name, takes in options.items() if not takes}, flags)
                # The command's usage line, such as `gh repo edit [<repository>] [flags]`: a command whose first
                # argument is `<repository>` is in GH_REPOSITORY_ARGUMENT, and no other; one whose arguments it names
                # by URL, such as `gh pr comment [<number> | <url> | <branch>]`, is in GH_URL_ARGUMENT, and no other.
                words = ["gh", group, *([sub] if sub else [])]
                self.assertEqual(usage[:len(words)], words, text)
                arguments = [word.strip("[]{}.") for word in usage[len(words):]]
                first = arguments[0] if arguments else ""
                self.assertEqual((group, sub) in module.GH_REPOSITORY_ARGUMENT, first == "<repository>", usage)
                urls = {"<url>", "<urls>", "<discussion-url>", "<comment-url>"}
                self.assertEqual((group, sub) in module.GH_URL_ARGUMENT, bool(urls.intersection(arguments)), usage)

    def test_the_placeholders_the_check_fills_are_the_installed_gh_s_own(self) -> None:
        # `gh api --help` lists the placeholders gh fills in the endpoint and in an -F value; the check fills each one
        # (GH_PLACEHOLDERS), so a gh that adds one fails here rather than sending its value unread.
        gh = self.real_gh()
        if gh is None:
            self.skipTest("no gh on PATH")
        text = " ".join(subprocess.run([gh, "api", "--help"], capture_output=True, text=True, check=True,
                                       timeout=60).stdout.split())
        filled = set(self.scanner().GH_PLACEHOLDERS)
        for where, sentence in (("endpoint", r"Placeholder values (.*?) in the endpoint argument"),
                                ("-F", r"placeholder values (.*?) get populated")):
            with self.subTest(where=where):
                found = re.search(sentence, text)
                self.assertIsNotNone(found, text)
                assert found is not None
                self.assertEqual(set(re.findall(r"`\{([a-z]+)\}`", found[1])), filled, found[0])

    def test_the_shim_hands_every_text_command_to_the_check(self) -> None:
        text = GH.read_text(encoding="utf-8")
        functions = ""
        for name in ("gh_shim_is_write", "gh_shim_may_send_text", "gh_shim_has_nested_alias"):
            start = text.index(f"{name}() {{")
            functions += text[start:text.index("\n}\n", start) + 3]
        for group, sub in self.scanner().GH_TEXT_COMMANDS:
            argv = [group, *([sub] if sub else ["-X", "POST", "repos/o/r/issues"])]
            with self.subTest(command=" ".join(argv)):
                script = functions + "\ngh_shim_may_send_text " + " ".join(f"'{a}'" for a in argv) + " && echo yes"
                out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60).stdout
                self.assertEqual(out.strip(), "yes")


class Writes(unittest.TestCase):
    """gh_shim_is_write names every call that changes something on GitHub, so the check reads its text, and never a
    read."""

    @staticmethod
    def is_write(argv: list[str]) -> bool:
        """gh_shim_is_write, lifted out of the shim by text (sourcing shims/gh runs it), so these cases are checked
        against the real file."""
        text = GH.read_text(encoding="utf-8")
        start = text.index("gh_shim_is_write() {")
        script = text[start:text.index("\n}\n", start) + 3] + "\ngh_shim_is_write " + " ".join(f"'{a}'" for a in argv)
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                              check=False).returncode == 0

    def test_write_verbs_are_writes(self) -> None:
        for argv in (["issue", "comment"], ["issue", "create"], ["pr", "merge"], ["pr", "review"],
                     ["pr", "edit"], ["pr", "close"], ["release", "create"], ["workflow", "run"],
                     ["api", "-X", "POST", "repos/o/r/issues"],
                     ["api", "--method", "DELETE", "repos/o/r"],
                     ["api", "repos/o/r/issues", "-f", "title=x"]):
            with self.subTest(argv=argv):
                self.assertTrue(self.is_write(argv), f"{argv} should be a write")

    def test_read_verbs_are_not_writes(self) -> None:
        for argv in (["issue", "view"], ["issue", "list"], ["pr", "view"], ["pr", "diff"],
                     ["repo", "view"], ["run", "view"], ["api", "repos/o/r"],
                     ["api", "-X", "GET", "repos/o/r"]):
            with self.subTest(argv=argv):
                self.assertFalse(self.is_write(argv), f"{argv} should not be a write")


if __name__ == "__main__":
    unittest.main()
