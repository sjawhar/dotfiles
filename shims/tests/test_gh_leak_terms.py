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
# The repository of each node of a GraphQL `nodes(ids:)` read: $NODES/<id> holds it; an id with no file has none.
if [[ "${1:-}" == api && "${2:-}" == graphql && "$*" == *"nodes(ids:"* ]]; then
    for a in "$@"; do
        case "$a" in ids\[\]=*) id="${a#ids\[\]=}"; if [[ -f "$NODES/$id" ]]; then cat "$NODES/$id"; else echo; fi ;; esac
    done
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
        # The fallback-write gate of an agent session asks gh-app-token which identity an owner gets.
        write_executable(self.bin / "gh-app-token", "#!/bin/bash\necho app\n")
        self.calls = self.tmp / "calls"
        self.calls.mkdir()
        self.vis = self.tmp / "visibility"
        self.vis.mkdir()
        self.vis_log = self.tmp / "visibility.log"
        self.vis_log.touch()
        self.nodes = self.tmp / "nodes"
        self.nodes.mkdir()
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
            # The call log and read cache would otherwise go to the machine's own, canary and all.
            "GH_READ_CACHE_DIR": str(self.tmp / "gh-read-cache"),
            "GH_READ_CACHE_LOG": "0",
            "CALLS": str(self.calls),
            "VIS_DIR": str(self.vis),
            "VIS_LOG": str(self.vis_log),
            "NODES": str(self.nodes),
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers -----------------------------------------------------------------------------------------------

    def answer(self, slug: str, visibility: str) -> None:
        (self.vis / slug.replace("/", "_")).write_text(visibility + "\n", encoding="utf-8")

    def agent(self, **extra: str) -> dict[str, str]:
        """An omp session's environment: the agent block, the public text gate (a stub that passes) and the read
        cache all run before the term check."""
        env = {key: value for key, value in self.env.items() if key not in ("AGENT_SHIM_PERSON", "GH_READ_CACHE_LOG")}
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
        # A call that names no repository (a GraphQL mutation by node id) may reach the scope, so it is refused.
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

    # --- the repository a call goes to is its address, not its text (dispatch://INFRA-3365) ---------------------------

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

    def test_the_repository_a_call_goes_to_is_not_read_as_its_text(self) -> None:
        # Given the knives ygo-fork list and its public release repository theorem-labs/ygo, whose name holds a term of
        # that list: until 2026-11-19 the value of -R was read as a text, and every write there was refused for it.
        env = self.ygo_fork()
        self.answer(YGO, "public")
        for name, argv in self.ygo_writes():
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)
        # No term was found in what the calls send, so no visibility was read.
        self.assertEqual(self.visibility_reads(), [])
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

    def test_the_repository_an_endpoint_or_a_url_names_is_not_read_as_its_text(self) -> None:
        # Given the knives ygo-fork list and theorem-labs/ygo public: a write that names the repository by its API
        # endpoint, by a pull request or issue URL, or as the repository `repo edit` changes goes there as the -R
        # forms do. Its name is the call's address, not text it sends (dispatch://INFRA-3365, tester round 1).
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
        # call goes out.
        self.answer(repo, "public")
        argv = ["repo", "edit", repo, "--description", "notes"]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)

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
        # Controls: a call that keeps the repository private, and an edit of it once public, go out.
        for visibility, argv in (("private", ["api", "-X", "PATCH", f"repos/{repo}", "-f", "visibility=private"]),
                                 ("public", ["api", "-X", "PATCH", f"repos/{repo}", "-f", "description=notes"])):
            with self.subTest(control=visibility):
                self.answer(repo, visibility)
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv), argv)

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
        # the checkout's remote (the one `gh repo set-default` chose, when it chose one).
        self.answer(self.NOTES, "private")
        patch = ["api", "-X", "PATCH"]
        visibility = self.file("visibility.txt", "public\n")
        published = self.PUBLISHED
        for name, remotes, base, gh_repo, argv, fields in (
                ("-F visibility=@file", [], "", "", [*patch, f"repos/{self.NOTES}", "-F", f"visibility=@{visibility}"],
                 ["argument 1"]),
                ("{owner}/{repo}, remote", [("origin", self.NOTES)], "", "",
                 [*patch, "repos/{owner}/{repo}", "-f", "visibility=public"], [f"{published} (remote origin)"]),
                ("{owner}/{repo}, GH_REPO", [], "", PRIVATE,
                 [*patch, "repos/{owner}/{repo}", "-f", "visibility=public", "-f", f"description={TEXT}"],
                 ["--raw-field description"]),
                ("{owner}/{repo}, GH_REPO's name", [], "", self.NOTES,
                 [*patch, "repos/{owner}/{repo}", "-f", "visibility=public", "-f", "description=notes"],
                 [f"{published} (GH_REPO)"]),
                (":owner/:repo", [("origin", PRIVATE)], "", "",
                 [*patch, "repos/:owner/:repo", "-f", "visibility=public", "-f", f"description={TEXT}"],
                 ["--raw-field description"]),
                ("O/{repo}", [("origin", self.NOTES)], "", "", [*patch, "repos/o/{repo}", "-f", "private=false"],
                 [f"{published} (remote origin)"]),
                ("{owner}/R", [("origin", PRIVATE)], "", "",
                 [*patch, f"repos/{{owner}}/notes-{CANARY}", "-f", "visibility=public"],
                 ["argument 1", f"{published} (remote origin)"]),
                ("gh repo set-default", [("origin", PUBLIC), ("upstream", self.NOTES)], "upstream", "",
                 [*patch, "repos/{owner}/{repo}", "-f", "visibility=public"], [f"{published} (remote upstream)"])):
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
        # REST's `user/repos`, `orgs/O/repos` and a template's `repos/O/R/generate`, and GraphQL's
        # startRepositoryMigration, make a repository that is public unless the call says otherwise, whatever the
        # visibility of the checkout's repository or of the template.
        (self.nodes / "PR_kwDOpriv5678").write_text(f"{PRIVATE}\n", encoding="utf-8")
        self.remotes(("origin", PRIVATE))
        name = f"name=notes-{CANARY}"

        def migration(owner: str) -> str:
            return ('query=mutation{startRepositoryMigration(input:{sourceId:"x", ownerId:"%s", repositoryName:"%s", '
                    'sourceRepositoryUrl:"https://example.com/r", continueOnError:true, targetRepoVisibility:"public"})'
                    '{clientMutationId}}' % (owner, f"notes-{CANARY}"))

        for form, argv, field in (
                ("orgs/{owner}/repos", ["api", "orgs/{owner}/repos", "-f", name, "-f", "visibility=public"],
                 "--raw-field name"),
                ("orgs/:owner/repos", ["api", "orgs/:owner/repos", "-f", name, "-f", "visibility=public"],
                 "--raw-field name"),
                ("user/repos", ["api", "user/repos", "-f", name], "--raw-field name"),
                ("a private template's generate", ["api", f"repos/{PRIVATE}/generate", "-f", "owner=o", "-f", name],
                 "--raw-field name"),
                ("{owner}/{repo}/generate", ["api", "repos/{owner}/{repo}/generate", "-f", "owner=o", "-f", name],
                 "--raw-field name"),
                ("startRepositoryMigration", ["api", "graphql", "-f", migration("y")], "--raw-field query string 3"),
                ("startRepositoryMigration, a private node", ["api", "graphql", "-f", migration("PR_kwDOpriv5678")],
                 "--raw-field query string 3")):
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

    def test_a_graphql_mutation_is_judged_by_the_repositories_of_its_nodes(self) -> None:
        term = REGISTRY_TEST_TERMS["oh-my-pi"][0]
        self.answer("cybermonklabs/oh-my-pi", "public")
        (self.nodes / "PR_kwDOfork1234").write_text("cybermonklabs/oh-my-pi\n", encoding="utf-8")
        (self.nodes / "PR_kwDOpriv5678").write_text(f"{PRIVATE}\n", encoding="utf-8")
        (self.nodes / "PR_kwDOpub09876").write_text(f"{PUBLIC}\n", encoding="utf-8")

        def mutation(node: str) -> list[str]:
            body = f"Reply: see {term}."
            return ["api", "graphql", "-f", f'query=mutation {{ addComment(input: {{subjectId: "{node}", body: "{body}"}}) '
                                              "{ clientMutationId } }"]

        refused = self.run_gh(mutation("PR_kwDOfork1234"))
        self.assertEqual(refused.returncode, 2, refused.stderr.decode())
        self.assertIn("cybermonklabs/oh-my-pi (public)", refused.stderr.decode())
        self.assertEqual(self.gh_calls(), [])
        # A node of a private repository, and one of a public repository the registry entry does not name: sent.
        for node in ("PR_kwDOpriv5678", "PR_kwDOpub09876"):
            with self.subTest(node=node):
                self.setUp_calls()
                self.assert_sent(self.run_gh(mutation(node)), mutation(node))
        # A node whose repository cannot be read is any repository's.
        self.setUp_calls()
        unread = self.run_gh(mutation("PR_kwDOunknown1"))
        self.assertEqual(unread.returncode, 2, unread.stderr.decode())
        self.assertIn("could not be read, read as public", unread.stderr.decode())

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

    def test_a_graphql_mutation_that_names_its_repository_is_judged_by_that_repository_too(self) -> None:
        # createCommitOnBranch names its repository by name; the private node id in its message is not its only target.
        (self.nodes / "PR_kwDOpriv5678").write_text(f"{PRIVATE}\n", encoding="utf-8")

        def commit(repository: str) -> list[str]:
            return ["api", "graphql", "-f", "query=mutation { createCommitOnBranch(input: {branch: {repositoryNameWithOwner: "
                    f'"{repository}", branchName: "main"}}, expectedHeadOid: "{"0" * 40}", message: {{headline: "x", '
                    f'body: "{TEXT.strip()} after PR_kwDOpriv5678"}}}}) {{ commit {{ oid }} }} }}']

        refused = self.run_gh(commit(PUBLIC))
        self.assert_refused(refused, "--raw-field query string 5")
        self.assertIn(f"{PUBLIC} (public)", refused.stderr.decode())
        # The same name as a JSON variable of an --input payload, and as a gh field.
        payload = json.dumps({"query": "mutation($b: CommittableBranch!) { createCommitOnBranch(input: {branch: $b, "
                                       f'message: {{headline: "{TEXT.strip()}"}}}}) {{ commit {{ oid }} }} }}',
                              "variables": {"b": {"repositoryNameWithOwner": PUBLIC, "branchName": "main"}}})
        self.setUp_calls()
        self.assert_refused(self.run_gh(["api", "graphql", "--input", self.file("v.json", payload)]),
                            "--input v.json .query string 1")
        # A mutation that makes a repository goes to one nobody can name yet, read as public.
        self.setUp_calls()
        made = self.run_gh(["api", "graphql", "-f", "query=mutation { createRepository(input: {name: \"x\", "
                                                      f'visibility: PUBLIC, description: "{TEXT.strip()} PR_kwDOpriv5678"}}) '
                                                      "{ repository { url } } }"])
        self.assertEqual(made.returncode, 2, made.stderr.decode())
        self.assertIn("a GraphQL mutation that makes a repository", made.stderr.decode())
        # Control: the same commit to the private repository, named and by node id, goes out.
        self.setUp_calls()
        self.assert_sent(self.run_gh(commit(PRIVATE)), commit(PRIVATE))

    def test_the_repository_a_graphql_mutation_names_is_not_read_as_its_text(self) -> None:
        # Given the knives ygo-fork list and theorem-labs/ygo public: createCommitOnBranch names the repository it
        # commits to by name, as a gh field, a GraphQL argument or a JSON variable. That name is where the commit goes,
        # as -R's value is; until 2026-11-19 it was read as text and refused (dispatch://INFRA-3365).
        env = self.ygo_fork()
        self.answer(YGO, "public")

        def field(headline: str, branch: str = "main") -> list[str]:
            query = ('query=mutation($b: CommittableBranch!) { createCommitOnBranch(input: {branch: $b, '
                     f'expectedHeadOid: "x", message: {{headline: "{headline}"}}}}) {{ commit {{ oid }} }} }}')
            return ["api", "graphql", "-f", query, "-F", f"b[repositoryNameWithOwner]={YGO}", "-F",
                    f"b[branchName]={branch}"]

        def argument(headline: str) -> list[str]:
            return ["api", "graphql", "-f",
                    "query=mutation { createCommitOnBranch(input: {branch: {repositoryNameWithOwner: "
                    f'"{YGO}", branchName: "main"}}, expectedHeadOid: "x", message: {{headline: "{headline}"}}}}) '
                    "{ commit { oid } } }"]

        def variable(path: str) -> list[str]:
            payload = json.dumps({
                "query": "mutation($b: CommittableBranch!, $f: FileChanges!) { createCommitOnBranch(input: {"
                         'branch: $b, expectedHeadOid: "x", message: {headline: "m"}, fileChanges: $f}) '
                         "{ commit { oid } } }",
                "variables": {"b": {"repositoryNameWithOwner": YGO, "branchName": "main"},
                              "f": {"additions": [{"path": path, "contents": "aGk="}]}}})
            return ["api", "graphql", "--input", self.file("commit.json", payload)]

        for name, argv in (("gh field", field("m")), ("GraphQL argument", argument("m")),
                           ("JSON variable", variable("notes.md"))):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)
        # Control: a term of the list anywhere else in the commit is refused, by the field that holds it, and so is the
        # repository's name in a field that does not name the repository.
        for name, argv, refused_field, matched in (
                ("gh field, headline", field("Fixes INFRA-1"), "--raw-field query string 2", "INFRA-"),
                ("GraphQL argument, headline", argument("Fixes INFRA-1"), "--raw-field query string 4", "INFRA-"),
                ("JSON variable, file path", variable("dispatch://notes.md"),
                 "--input commit.json .variables.f.additions[0].path", "dispatch://"),
                ("gh field, branch", field("m", YGO), "--field b[branchName]", "theorem-labs")):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_refused(self.run_gh(argv, env=env), refused_field, matched, YGO_LIST, line=1)

    def test_an_owner_name_a_graphql_mutation_holds_elsewhere_is_read(self) -> None:
        # Given the knives ygo-fork list and theorem-labs/ygo public: only the value of the input field
        # repositoryNameWithOwner is the address a commit goes to. An owner/name anywhere else, after that field's own
        # name too, is text the commit stores, and is read (dispatch://INFRA-3365, tester round 2).
        env = self.ygo_fork()
        self.answer(YGO, "public")
        smuggled = "repositoryNameWithOwner=theorem-labs/INFRA-1 leaked"
        to_branch = ('mutation($b: CommittableBranch!) { createCommitOnBranch(input: {branch: $b, expectedHeadOid: "x", '
                     f'message: {{headline: "{smuggled}"}}}}) {{ commit {{ oid }} }} }}')
        with_message = ("mutation($b: CommittableBranch!, $m: CommitMessage!) { createCommitOnBranch(input: {branch: $b, "
                        'expectedHeadOid: "x", message: $m}) { commit { oid } } }')
        branch = {"repositoryNameWithOwner": YGO, "branchName": "main"}
        by_variable = ("query=mutation($repositoryNameWithOwner: String!) { createCommitOnBranch(input: {branch: "
                       f'{{repositoryNameWithOwner: "{YGO}", branchName: "main"}}, expectedHeadOid: "x", '
                       "message: {headline: $repositoryNameWithOwner}}) { commit { oid } } }")
        # Two `query` members: the one GitHub keeps names theorem-labs/ygo, and its headline holds what the other
        # gives the field at the same path.
        first = ("mutation { createCommitOnBranch(input: {branch: {repositoryNameWithOwner: \"theorem-labs/INFRA-1\", "
                 'branchName: "main"}, expectedHeadOid: "x", message: {headline: "m"}}) { commit { oid } } }')
        second = ('mutation { createCommitOnBranch(input: {message: {headline: "theorem-labs/INFRA-1"}, branch: '
                  f'{{repositoryNameWithOwner: "{YGO}", branchName: "main"}}, expectedHeadOid: "x"}}) '
                  "{ commit { oid } } }")
        for name, argv, field in (
                ("gh field, headline in the document",
                 ["api", "graphql", "-F", f"b[repositoryNameWithOwner]={YGO}", "-f", f"query={to_branch}"],
                 "--raw-field query string 2"),
                ("--input, headline in the document",
                 ["api", "graphql", "--input", self.file("d.json", json.dumps({"query": to_branch,
                                                                               "variables": {"b": branch}}))],
                 "--input d.json .query string 2"),
                ("--input, headline in a variable",
                 ["api", "graphql", "--input", self.file("v.json", json.dumps({
                     "query": with_message, "variables": {"b": branch, "m": {"headline": smuggled}}}))],
                 "--input v.json .variables.m.headline"),
                ("a variable named for the field, used as the headline",
                 ["api", "graphql", "-f", by_variable, "-F", "repositoryNameWithOwner=theorem-labs/INFRA-1"],
                 "--field repositoryNameWithOwner"),
                ("two query members",
                 ["api", "graphql", "--input",
                  self.file("t.json", '{"query": %s, "query": %s}' % (json.dumps(first), json.dumps(second)))],
                 "--input t.json .query string 1")):
            with self.subTest(form=name):
                self.setUp_calls()
                refused = self.run_gh(argv, env=env)
                self.assert_refused(refused, field, "INFRA-", YGO_LIST, line=1)
                self.assertIn(f"{YGO} (public)", refused.stderr.decode())
        # A variable the field takes and the headline uses too is read, and leaves where the commit goes unknown.
        both = ('query=mutation($r: String!) { createCommitOnBranch(input: {branch: {repositoryNameWithOwner: $r, '
                'branchName: "main"}, expectedHeadOid: "x", message: {headline: $r}}) { commit { oid } } }')
        self.setUp_calls()
        refused = self.run_gh(["api", "graphql", "-f", both, "-F", f"r={YGO}"], env=env)
        self.assert_refused(refused, "--field r", "theorem-labs", YGO_LIST, line=1)
        self.assertIn("could not be read, read as public", refused.stderr.decode())
        # Controls: a variable the document gives the field alone names where the commit goes, by any name.
        for variable in ("repositoryNameWithOwner", "r"):
            with self.subTest(control=variable):
                query = (f"query=mutation(${variable}: String!) {{ createCommitOnBranch(input: {{branch: "
                         f'{{repositoryNameWithOwner: ${variable}, branchName: "main"}}, expectedHeadOid: "x", '
                         'message: {headline: "m"}}) { commit { oid } } }')
                argv = ["api", "graphql", "-f", query, "-F", f"{variable}={YGO}"]
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)

    def test_a_gh_alias_under_a_command_is_read_as_the_command_it_expands_to(self) -> None:
        config = self.tmp / "config" / "gh"
        config.mkdir()
        (config / "config.yml").write_text("version: 1\naliases:\n    pr say: pr comment\n", encoding="utf-8")
        self.assert_refused(self.run_gh(["pr", "say", "11", "-R", PUBLIC, "--body", TEXT]), "--body")
        # Control: the same alias to a private repository goes out.
        argv = ["pr", "say", "11", "-R", PRIVATE, "--body", TEXT]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)

    def test_a_graphql_node_id_spelled_with_json_escapes_is_read(self) -> None:
        (self.nodes / "PR_kwDOpub09876").write_text(f"{PUBLIC}\n", encoding="utf-8")
        (self.nodes / "PR_kwDOpriv5678").write_text(f"{PRIVATE}\n", encoding="utf-8")
        mutation = ('mutation { addComment(input: {subjectId: "NODE", body: "%s after PR_kwDOpriv5678"}) '
                    "{ clientMutationId } }" % TEXT.strip())
        payload = json.dumps({"query": mutation}).replace("NODE", "\\u0050R_kwDOpub09876")
        refused = self.run_gh(["api", "graphql", "--input", self.file("n.json", payload)])
        self.assert_refused(refused, "--input n.json .query string 2")
        self.assertIn(f"{PUBLIC} (public)", refused.stderr.decode())
        # Control: the same payload whose node is the private repository's goes out.
        argv = ["api", "graphql", "--input", self.file("p.json", json.dumps({"query": mutation.replace(
            "NODE", "PR_kwDOpriv5678")}))]
        self.setUp_calls()
        self.assert_sent(self.run_gh(argv), argv)

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
        # A GraphQL address is GraphQL: a mutation on a private repository's node goes out, and a query is a read.
        (self.nodes / "PR_kwDOpriv5678").write_text(f"{PRIVATE}\n", encoding="utf-8")
        for query in ('mutation { addComment(input: {subjectId: "PR_kwDOpriv5678", body: "%s"}) '
                      "{ clientMutationId } }" % TEXT.strip(),
                      'query { search(query: "%s", type: ISSUE, first: 1) { issueCount } }' % CANARY):
            with self.subTest(graphql=query.split()[0]):
                self.setUp_calls()
                argv = ["api", "https://api.github.com/graphql", "-f", f"query={query}"]
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

    def test_in_an_agent_session_a_refused_line_is_not_read_as_github_s_answer(self) -> None:
        # The outer pass reads the call's stderr for GitHub's refusal of the App and for a spent rate limit. A refusal
        # before gh ran prints the caller's own line, which may say anything.
        body = f"Fixes the HTTP 403 and API rate limit exceeded answers for {CANARY.upper()}.\n"
        refused = self.run_gh(["issue", "comment", "1", "-R", PUBLIC, "--body", body], env=self.agent())
        self.assert_refused(refused, "--body", line=1)
        stderr = refused.stderr.decode("utf-8", "replace")
        self.assertNotIn("REFUSED by GitHub", stderr)
        self.assertNotIn("RATE LIMITED", stderr)

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


if __name__ == "__main__":
    unittest.main()
