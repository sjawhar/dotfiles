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
# The variables that make a shim read its caller as an agent (shims/tests/test_shim_agent_context.py).
AGENT_MARKERS = ("OMP_SESSION_ID", "PI_SESSION_FILE", "PI_TOOL_BRIDGE_URL", "AGENTBOX_SESSION", "CLAUDECODE",
                 "ENVOY_SESSION_ID", "AGENT_SHIM_AGENT", "SSH_CONNECTION", "SSH_TTY")
# gh's config files, in the layout gh 2.98.0 writes them, with a fake token: config.yml's version line, and the entry
# of a host in hosts.yml, which the hosts.yml texts below list.
GH_VERSION = 'version: "1"\n'
GH_HOST_ENTRY = "    users:\n        x:\n            oauth_token: gho_fake\n    user: x\n    oauth_token: gho_fake\n"
HOSTS_GHE = "ghe.example.com:\n" + GH_HOST_ENTRY
HOSTS_GITHUB = "github.com:\n" + GH_HOST_ENTRY
# The target of a repository on ghe.example.com, a host a call names itself.
ON_GHE = "a repository on ghe.example.com, read as public"


def gh_config_yml(aliases: str) -> str:
    """A config.yml as gh 2.98.0 writes one (its comments left out), with ALIASES after its `aliases:`."""
    return ("version: 1\ngit_protocol: https\neditor:\nprompt: enabled\nprefer_editor_prompt: disabled\npager:\n"
            f"aliases:{aliases}\nhttp_unix_socket:\nbrowser:\ncolor_labels: disabled\naccessible_colors: disabled\n"
            "accessible_prompter: disabled\nspinner: enabled\n")


def gh_tester_config_yml(aliases: str) -> str:
    """gh_config_yml with the tester's four lines outside the aliases, each one gh 2.98.0 wrote (dispatch://INFRA-3365,
    review round 10): `editor: ?x`, a pager holding U+2028, `browser: 'a: b'` and `http_unix_socket: '#x'`."""
    return (gh_config_yml(aliases).replace("editor:\n", "editor: ?x\n").replace("pager:\n", "pager: less\u2028-R\n")
            .replace("browser:\n", "browser: 'a: b'\n").replace("http_unix_socket:\n", "http_unix_socket: '#x'\n"))


FAKE_GH = r"""#!/bin/bash
# A visibility read (scripts/github-repo-visibility): answered from $VIS_DIR/<owner>_<repo>, or refused.
if [[ "${1:-}" == api && "${2:-}" == --hostname && "${3:-}" == github.com && "${4:-}" == repos/* && "${5:-}" == --jq \
    && "${6:-}" == .visibility ]]; then
    printf '%s\n' "$4" >> "$VIS_LOG"
    answer="$VIS_DIR/${4#repos/}"
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
        # No ssh command of the caller's (GIT_SSH, GIT_SSH_COMMAND): scripts/github-repo-visibility resolves an ssh
        # remote's host as git's ssh does, as `ssh -G`, and with such a command set reads that host as unknown.
        inherited = {
            key: value for key, value in os.environ.items()
            if key not in AGENT_MARKERS and not key.startswith(("PI_", "LEGION_", "GIT_CONFIG_", "GIT_SSH", "GH_",
                                                                "PUBLIC_LEAK_CHECK", "DOTFILES_LEAK_TERMS_GH"))
        }
        self.env = {
            **inherited,
            **fixture_environment(),
            # The shim takes no gh above its own PATH entry (real_gh in shims/gh), so this checkout's
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

    def check(self, argv: list[str], env: dict[str, str], stdin: bytes | None = None
              ) -> subprocess.CompletedProcess[bytes]:
        """scripts/leak-terms-check on the call ARGV as shims/gh runs it, with ENV, for its own exit status (3 for a
        listed term, 4 for a text it cannot read): STDIN, when given, is the shim's buffer of standard input."""
        if stdin is not None:
            buffer = self.tmp / "stdin-buffer"
            buffer.write_bytes(stdin)
            env = {**env, "DOTFILES_LEAK_TERMS_GH_STDIN": str(buffer)}
        return subprocess.run([str(CHECK), "gh", "--", *argv], env=env, cwd=self.work, stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=240, check=False)

    def assert_alias_refused(self, argv: list[str], env: dict[str, str], shown: str, stdin: bytes = b"") -> None:
        """The call ARGV, which may run a gh alias the check cannot read, is refused before any text is read: the check
        exits 4, its refusal holding SHOWN and saying nothing was sent; and through shims/gh, given STDIN, which the
        check never asks for, the call exits 2 and gh never runs."""
        checked = self.check(argv, env, stdin or None)
        stderr = checked.stderr.decode("utf-8", "replace")
        self.assertEqual(checked.returncode, 4, stderr)
        self.assertIn(shown, stderr)
        self.assertIn("refused before gh ran, so nothing was sent.", stderr)
        self.setUp_calls()
        result = self.run_gh(argv, stdin, env=env)
        self.assertEqual(result.returncode, 2, result.stderr.decode("utf-8", "replace"))
        self.assertEqual(self.gh_calls(), [])

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
            'forbidden = ["INFRA-", "theorem-labs"]\n', encoding="utf-8")
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
        # own name of a repository read as public, which sends nothing new, so the call goes out (gh_own_name).
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
                ("--body, a Dispatch link that holds INFRA-1",
                 ["pr", "comment", "1", "-R", YGO, "--body", "See dispatch://INFRA-1"], "--body", "INFRA-")):
            with self.subTest(form=name):
                self.setUp_calls()
                refused = self.run_gh(argv, env=env)
                self.assert_refused(refused, field, matched, YGO_LIST, line=1)
                self.assertNotIn("--repo line", refused.stderr.decode())

    def test_a_dispatch_link_is_no_term_of_the_knives_registry(self) -> None:
        # Sami ruled on 2026-11-20 that a Dispatch link is no leak term
        # (dispatch://INFRA-2111/ask/40b0b5c5-4575-44da-9afb-dbcf2bb9214d), so this checkout's knives/repos.toml
        # does not list `dispatch://` for ygo-fork. With that registry, read whole: a write to the public
        # theorem-labs/ygo whose title holds only a Dispatch link goes out, as in the gh-gate-ygo check's row, and so
        # does a body that holds one. oh-my-pi's list still holds `dispatch://`: Legion's push check refuses any change
        # to that line, which names the company, so it stays as on main (the PR body's stated limits). Controls: a link
        # that holds INFRA-1, a term of the ygo-fork list, and a body that holds `envoy`, a term of the oh-my-pi list,
        # are refused, so the registry's lists are read.
        knives = self.tmp / "knives"
        knives.mkdir()
        shutil.copyfile(DOTFILES / "knives" / "repos.toml", knives / "repos.toml")
        env = {**self.env, "DOTFILES_LEAK_TERMS_KNIVES_DIR": str(knives)}
        omp = "cybermonklabs/oh-my-pi"
        self.answer(YGO, "public")
        self.answer(omp, "public")
        for name, argv in (
                ("a title that holds only a link, to theorem-labs/ygo",
                 ["pr", "create", "-R", YGO, "--title", "dispatch://x", "--body", "b", "--head", "x"]),
                ("a body, to theorem-labs/ygo", ["pr", "comment", "1", "-R", YGO, "--body", "See dispatch://x"])):
            with self.subTest(form=name):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)
        for name, argv, field, matched, term_list in (
                ("a link that holds INFRA-1, to theorem-labs/ygo",
                 ["pr", "create", "-R", YGO, "--title", "dispatch://INFRA-1", "--body", "b", "--head", "x"], "--title",
                 "INFRA-", YGO_LIST),
                ("envoy, to cybermonklabs/oh-my-pi", ["pr", "comment", "1", "-R", omp, "--body", "See envoy"], "--body",
                 "envoy", "the knives oh-my-pi list")):
            with self.subTest(control=name):
                self.setUp_calls()
                self.assert_refused(self.run_gh(argv, env=env), field, matched, term_list, line=1)

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
        # issue's (the -R repository here).
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
        # included, goes to it as well as to the branch's repository; the value of -R goes only to its own.
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
                                    ("no-break space", f"See {first}\u00a0{rest}.\n", f"{first}\\u00a0{rest}"),
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
        # with a GraphQL string's `\u` escape read as the letter GitHub decodes, and a query is a read.
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
        # and its term is refused, for an endpoint that names no repository.
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
        # `.github.com`. So such a URL of a public repository, from a checkout whose remote is private, names that
        # public repository, and the text is judged by it.
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
        # description begins: one that begins with a private repository's URL is judged by the checkout's repository,
        # and the term after the URL is refused.
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

    def gh_config(self, name: str, hosts: str | None, config: str = GH_VERSION) -> dict[str, str]:
        """The environment with gh's configuration directory (GH_CONFIG_DIR) a new one, NAME, whose hosts.yml holds
        HOSTS, with no hosts.yml for None, and whose config.yml holds CONFIG."""
        directory = self.tmp / "gh-config" / name
        directory.mkdir(parents=True)
        (directory / "config.yml").write_text(config, encoding="utf-8")
        if hosts is not None:
            (directory / "hosts.yml").write_text(hosts, encoding="utf-8")
        return {**self.env, "GH_CONFIG_DIR": str(directory)}

    def checkout(self, *remotes: tuple[str, str]) -> None:
        """Make the work directory a new checkout whose remotes are REMOTES, (name, URL) pairs."""
        shutil.rmtree(self.work / ".git", ignore_errors=True)
        subprocess.run(["git", "init", "-q", str(self.work)], check=True, env=self.env)
        git = ["git", "-C", str(self.work)]
        for name, url in remotes:
            subprocess.run([*git, "remote", "add", name, url], check=True, env=self.env)

    # --- the plain case: gh can send the call only where the call says (dispatch://INFRA-3365, review round 10) -----

    def test_outside_the_plain_case_a_call_holding_a_term_is_refused_and_names_why(self) -> None:
        # The check reads where a call goes only in the plain case (scripts/leak-terms-check, gh_plain_case): every
        # host gh's config lists is github.com, GH_HOST is unset or github.com, and every remote gh may send a call to
        # is on github.com. Outside it a call that holds a listed term is one target read as public, so even a write to
        # a private repository is refused, and the refusal names the condition; the same call with no term goes out.
        # Each row breaks one condition: hosts.yml lists ghe.example.com; config.yml has a `hosts` key; hosts.yml is
        # in a layout the check does not read (YAML flow form); GH_HOST is ghe.example.com; a remote is on
        # ghe.example.com, or on www.ghe.example.com (the reviewer's, review round 10); a remote fetches from
        # ghe.example.com and pushes to github.com.
        private, ghe, www = (f"https://github.com/{PRIVATE}.git", "https://ghe.example.com/o/r.git",
                             "https://www.ghe.example.com/o/r.git")
        hosts_key = GH_VERSION + "hosts:\n" + "".join("    " + line for line in HOSTS_GHE.splitlines(keepends=True))
        # (row, the environment, the checkout's remotes, the origin's own push URL or "", the condition it names)
        rows = (
            ("hosts.yml lists ghe.example.com", self.gh_config("ghe", HOSTS_GHE), [("origin", private)], "",
             "gh's config lists ghe.example.com"),
            ("config.yml has a hosts key", self.gh_config("hosts-key", None, hosts_key), [("origin", private)], "",
             "gh's config.yml has a hosts key"),
            ("hosts.yml in YAML flow form", self.gh_config("flow", '{"ghe.example.com": {"user": "x"}}\n'),
             [("origin", private)], "", "gh's hosts.yml is not in the layout the check reads"),
            ("GH_HOST is ghe.example.com", {**self.env, "GH_HOST": "ghe.example.com"}, [("origin", private)], "",
             "GH_HOST is ghe.example.com"),
            ("a remote on ghe.example.com", self.env, [("origin", private), ("upstream", ghe)], "",
             f"the remote upstream ({ghe}) is not on github.com"),
            ("a remote on www.ghe.example.com", self.env, [("origin", www)], "",
             f"the remote origin ({www}) is not on github.com"),
            ("a remote fetching from ghe.example.com, pushing to github.com", self.env, [("origin", ghe)], private,
             f"the remote origin ({ghe}) is not on github.com"))
        argv = ["pr", "comment", "1", "-R", PRIVATE, "--body"]
        for row, env, remotes, push, condition in rows:
            self.checkout(*remotes)
            if push:
                self.git("remote", "set-url", "--push", "origin", push)
            with self.subTest(row=row):
                self.setUp_calls()
                refused = self.run_gh([*argv, TEXT], env=env)
                self.assert_refused(refused, "--body")
                self.assertIn(f"to wherever gh sends it, read as public, since {condition}", refused.stderr.decode())
            with self.subTest(row=row, control="no term"):
                self.setUp_calls()
                self.assert_sent(self.run_gh([*argv, CLEAN], env=env), [*argv, CLEAN])
        # Control: in the plain case the same write goes out, judged by the private repository it names.
        with self.subTest(control="the plain case"):
            self.checkout(("origin", private))
            self.setUp_calls()
            self.assert_sent(self.run_gh([*argv, TEXT]), [*argv, TEXT])

    def test_a_remote_is_read_by_the_url_gh_reads(self) -> None:
        # gh 2.98.0 reads a remote by its fetch URL, and by its push URL only where the fetch URL gives no repository
        # (context.TranslateRemotes, ghrepo.FromURL: a host, and a path of two parts); a remote that gives none it
        # drops. The tester measured (dispatch://INFRA-3365, review round 11): with a path or a file:// remote beside
        # github.com/theorem-labs/agent-infra, `gh pr comment 1` went to that repository, and with only a path remote
        # gh sent nothing. The check reads each remote so (gh_remotes), in the plain case and for the base
        # repositories. Given the knives ygo-fork list and theorem-labs/ygo private, each checkout here whose remotes
        # give gh no repository but github.com's is in the plain case: the clean comment that names that repository
        # with -R, which holds `theorem-labs`, goes out, and so does one with a term. A fetch URL on another host,
        # beside a path too, is not, and neither is `./a:b`, which gh reads as scp-like, a/b on the host `.` (review
        # round 12): the term is refused, and the refusal names the remote. A push URL beside a github.com fetch URL,
        # `DISABLE` or one on another host, counts for nothing. In every checkout a clean comment to a public
        # repository goes out.
        env = self.ygo_fork()
        self.answer(YGO, "private")
        store = str(self.tmp / "store.git")
        github, ghe = f"https://github.com/{YGO}.git", "https://ghe.example.com/o/r"
        # (row, the remotes as (name, fetch URL), the origin's push URL or "", the remote the refusal names or "")
        rows = (
            ("origin a path, as `git clone /path` writes it", [("origin", store)], "", ""),
            ("file:// only", [("origin", f"file://{store}")], "", ""),
            ("a bundle", [("origin", "store.bundle")], "", ""),
            ("../local-store.git", [("origin", "../local-store.git")], "", ""),
            ("./a:b", [("origin", "./a:b")], "", "origin"),
            ("a path beside the github.com remote", [("origin", store), ("upstream", github)], "", ""),
            ("a github.com remote whose push URL is DISABLE", [("origin", github)], "DISABLE", ""),
            ("a github.com remote whose push URL is on ghe.example.com", [("origin", github)], f"{ghe}.git", ""),
            ("a fetch URL on ghe.example.com", [("origin", f"{ghe}.git")], github, "origin"),
            ("a path beside ghe.example.com", [("origin", store), ("upstream", ghe)], "", "upstream"),
            ("ssh://git.example.com", [("origin", "ssh://git.example.com/o/r.git")], "", "origin"),
            ("git://example.com", [("origin", "git://example.com/o/r")], "", "origin"),
            ("example.com:o/r.git", [("origin", "example.com:o/r.git")], "", "origin"))
        clean = ["pr", "comment", "1", "-R", YGO, "--body", "see you"]
        termed = ["pr", "comment", "1", "-R", YGO, "--body", TEXT]
        public = ["pr", "comment", "1", "-R", PUBLIC, "--body", CLEAN]
        for row, remotes, push, named in rows:
            self.checkout(*remotes)
            if push:
                self.git("remote", "set-url", "--push", "origin", push)
            if named:
                with self.subTest(row=row, call="a term"):
                    self.setUp_calls()
                    refused = self.run_gh(termed, env=env)
                    self.assert_refused(refused, "--body")
                    self.assertIn(f"since the remote {named} ({dict(remotes)[named]}) is not", refused.stderr.decode())
            for call, argv in (() if named else (("-R holding theorem-labs", clean), ("a term", termed))):
                with self.subTest(row=row, call=call):
                    self.setUp_calls()
                    self.assert_sent(self.run_gh(argv, env=env), argv)
            with self.subTest(row=row, control="a clean comment to a public repository"):
                self.setUp_calls()
                self.assert_sent(self.run_gh(public, env=env), public)
        # A comment that names no repository goes to the base repository, which gh takes from its remotes: here the
        # github.com one, theorem-labs/ygo, private, since gh drops the path remote beside it.
        with self.subTest(row="a path beside the github.com remote", call="a term, to the base repository"):
            self.checkout(("origin", store), ("upstream", github))
            argv = ["pr", "comment", "1", "--body", TEXT]
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv, env=env), argv)
        # A remote that fetches from a path, a local mirror, and pushes to github.com is that github.com repository
        # for gh, which reads its push URL, and sorts `upstream` before `origin`: a comment with a term that names no
        # repository is judged by the public repository there, and refused; the same comment with no term goes out.
        self.checkout(("upstream", "/mnt/mirror"), ("origin", f"https://github.com/{PRIVATE}.git"))
        self.git("remote", "set-url", "--push", "upstream", f"https://github.com/{PUBLIC}.git")
        mirror = "a mirror fetched from a path, pushed to a public github.com repository"
        with self.subTest(row=mirror):
            self.setUp_calls()
            refused = self.run_gh(["pr", "comment", "1", "--body", TEXT])
            self.assert_refused(refused, "--body")
            self.assertIn("remote upstream (public)", refused.stderr.decode())
        with self.subTest(row=mirror, control="no term"):
            argv = ["pr", "comment", "1", "--body", CLEAN]
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv), argv)

    def test_in_the_plain_case_a_write_is_judged_by_its_repository(self) -> None:
        # Each machine here is in the plain case, so a write with a listed term to a private repository goes out, and
        # the same write to a public one is refused for it: hosts.yml absent; holding only `{}`, which gh 2.98.0 writes
        # once `gh auth logout` logged out of the last host, and then takes github.com (measured by the tester,
        # dispatch://INFRA-3365, review round 10); listing github.com, its key plain as scripts/gh-agent-config writes
        # it, or quoted; GH_HOST github.com in another letter case, or api.github.com, which gh reads as github.com as
        # it reads `--hostname api.github.com` (review round 11); config.yml holding an alias named `hosts`, which `gh
        # alias set hosts 'auth status'` writes inside the aliases, in block and in flow form, and which is no `hosts`
        # key at the root (review round 11); and a directory in no git repository.
        self.checkout(("origin", f"https://github.com/{PRIVATE}.git"))

        def no_repository() -> dict[str, str]:
            shutil.rmtree(self.work / ".git")
            return {**self.env, "GIT_CEILING_DIRECTORIES": str(self.tmp)}

        for row, make in (
                ("no hosts.yml", lambda: self.gh_config("none", None)),
                ("hosts.yml holds {}", lambda: self.gh_config("logged-out", "# gh auth logout\n{}\n")),
                ("hosts.yml lists github.com", lambda: self.gh_config("github", HOSTS_GITHUB)),
                ("hosts.yml lists github.com, quoted", lambda: self.gh_config("quoted", '"github.com":\n' + GH_HOST_ENTRY)),
                ("GH_HOST is GitHub.COM", lambda: {**self.env, "GH_HOST": "GitHub.COM"}),
                ("GH_HOST is api.github.com", lambda: {**self.env, "GH_HOST": "api.github.com"}),
                ("an alias named hosts", lambda: self.gh_config("hosts-alias", HOSTS_GITHUB,
                                                                gh_config_yml("\n    hosts: auth status"))),
                ("an alias named hosts, in flow form",
                 lambda: self.gh_config("hosts-alias-flow", HOSTS_GITHUB, gh_config_yml(" {hosts: auth status}"))),
                ("no git repository", no_repository)):
            env = make()
            argv = ["pr", "comment", "1", "-R", PRIVATE, "--body", TEXT]
            with self.subTest(row=row, repository=PRIVATE):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)
            with self.subTest(row=row, repository=PUBLIC):
                self.setUp_calls()
                refused = self.run_gh(["pr", "comment", "1", "-R", PUBLIC, "--body", TEXT], env=env)
                self.assert_refused(refused, "--body")
                self.assertIn(f"to {PUBLIC} (public):", refused.stderr.decode())

    def test_in_the_plain_case_gh_resolved_names_the_base_repository_as_main_reads_it(self) -> None:
        # `gh repo set-default` writes a remote's `gh-resolved`: `base`, the remote itself, or the OWNER/REPO gh takes
        # in its place. In the plain case, every remote on github.com, the check reads it as main read it: the
        # resolved remotes, else every remote (gh_bases). So a write that names no repository goes out where the
        # resolution names a private repository, and is refused where it names a public one, or where nothing is
        # resolved and a remote is public.
        argv = ["pr", "comment", "1", "--body", TEXT]
        # (row, the remotes, the remote resolved and its gh-resolved, what the refusal shows, "" where it goes out)
        for row, remotes, remote, resolution, shown in (
                ("base on the private upstream", (("origin", PUBLIC), ("upstream", PRIVATE)), "upstream", "base", ""),
                ("origin resolved to a private repository", (("origin", PUBLIC),), "origin", PRIVATE, ""),
                ("origin resolved to a public repository", (("origin", PRIVATE),), "origin", PUBLIC,
                 f"to remote {PUBLIC} (public):"),
                ("nothing resolved, a remote public", (("origin", PRIVATE), ("upstream", PUBLIC)), "", "",
                 "remote upstream (public)")):
            self.remotes(*remotes)
            if remote:
                self.git("config", f"remote.{remote}.gh-resolved", resolution)
            with self.subTest(row=row):
                self.setUp_calls()
                if not shown:
                    self.assert_sent(self.run_gh(argv), argv)
                    continue
                refused = self.run_gh(argv)
                self.assert_refused(refused, "--body")
                self.assertIn(shown, refused.stderr.decode())

    def test_a_host_the_call_names_itself_is_that_host(self) -> None:
        # In the plain case a repository named with no host is github.com's, and a host the call names itself is that
        # host, read as public when it is not github.com (gh_repository_address): here --branch-repo `HOST/OWNER/REPO`,
        # and -R as a git URL, which only scripts/github-repo-visibility reads, so one that is not GitHub's is on a host
        # the check cannot read. Each is refused, and the same call naming github.com, where the repository is
        # private, goes out; with no host, a public repository is refused and the private one goes out.
        self.checkout(("origin", f"https://github.com/{PRIVATE}.git"))
        unread_url = "a repository on a host the check cannot read in a URL that is not GitHub's, read as public"

        def develop(branch_repository: str) -> list[str]:
            return ["issue", "develop", "1", "-R", PRIVATE, "--branch-repo", branch_repository, "--name",
                    f"fix-{CANARY}"]

        def comment(repository: str) -> list[str]:
            return ["pr", "comment", "1", "-R", repository, "--body", TEXT]

        # (the name, the refused call and the field, match and target its refusal names, the call that goes out)
        for name, refused_argv, field, matched, shown, sent_argv in (
                ("--branch-repo HOST/OWNER/REPO", develop(f"ghe.example.com/{PRIVATE}"), "--name", CANARY, ON_GHE,
                 develop(f"github.com/{PRIVATE}")),
                ("-R as a git URL", comment(f"git@ghe.example.com:{PRIVATE}.git"), "--body", CANARY.upper(),
                 unread_url, comment(f"git@github.com:{PRIVATE}.git")),
                ("-R OWNER/REPO, with no host", comment(PUBLIC), "--body", CANARY.upper(), f"{PUBLIC} (public)",
                 comment(PRIVATE))):
            with self.subTest(name=name, verdict="refused"):
                self.setUp_calls()
                refused = self.run_gh(refused_argv)
                self.assert_refused(refused, field, matched, line=1)
                self.assertIn(f"{shown}:", refused.stderr.decode())
            with self.subTest(name=name, verdict="goes out"):
                self.setUp_calls()
                self.assert_sent(self.run_gh(sent_argv), sent_argv)

    def test_a_repository_argument_on_another_host_is_read_as_public(self) -> None:
        # `repo edit HOST/OWNER/REPO` edits a repository on HOST, where gh sends the call, so it is read as public, as
        # a URL argument on another host is.
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
        # one with `-F` naming the config under the test's HOME, `Host gh-work` / `HostName github.com`. So
        # `git@gh-work:o/priv.git` and `gh-work:o/priv.git` are both github.com's o/priv.
        ssh = shutil.which("ssh")
        self.assertIsNotNone(ssh, "no ssh on PATH, which scripts/github-repo-visibility runs as `ssh -G`")
        (self.tmp / "home" / ".ssh").mkdir()
        (self.tmp / "home" / ".ssh" / "config").write_text("Host gh-work\n    HostName github.com\n", encoding="utf-8")
        write_executable(self.bin / "ssh", f'#!/bin/bash\nexec {ssh} -F "$HOME/.ssh/config" "$@"\n')
        argv = ["pr", "comment", "1", "--body", f"see {CANARY}"]
        for name, url in (("git@gh-work:", f"git@gh-work:{PRIVATE}.git"), ("gh-work:", f"gh-work:{PRIVATE}.git")):
            shutil.rmtree(self.work / ".git", ignore_errors=True)
            subprocess.run(["git", "init", "-q", str(self.work)], check=True, env=self.env)
            subprocess.run(["git", "-C", str(self.work), "remote", "add", "origin", url], check=True, env=self.env)
            with self.subTest(form=name, visibility="private"):
                self.answer(PRIVATE, "private")
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv), argv)
            with self.subTest(form=name, visibility="public"):
                self.answer(PRIVATE, "public")
                self.setUp_calls()
                refused = self.run_gh(argv)
                self.assert_refused(refused, "--body", CANARY, line=1)
                self.assertIn("to remote origin (public):", refused.stderr.decode())
                self.assertEqual(self.visibility_reads()[-1], f"repos/{PRIVATE}")

    def test_a_config_yml_the_reader_does_not_read_refuses_each_call_whose_alias_name_stands_in_it(self) -> None:
        # The check reads gh's aliases exactly in the forms gh 2.98.0 writes (gh_aliases). Where config.yml holds a
        # form the reader does not read, each call whose alias name stands in config.yml before a `:` is refused with
        # exit 4 before gh runs, the refusal naming the line and why, whatever the alias sends: `co 1`, `pp`, which
        # sends a listed term to a public repository, and `pi`, a clean comment to a private one. One such form gh
        # writes itself: the `? |` key of a name that ends with a line feed, as `gh alias set $'trail\n' 'pr view'`
        # wrote it after `co`, `pp` and `pi` (measured 2026-11-20 with gh 2.98.0; the key stays so through later
        # writes). The others gh does not write. Beside each, a call that runs no alias is judged as any
        # call: a clean `pr comment` to a public repository, `pr view 1`, and `zz 1`, a name that stands nowhere in
        # config.yml, go out, and the same comment with a term is refused for the term.
        pp = f"pp: issue comment 1 -R {PUBLIC} --body {CANARY}"
        pi = f"pi: issue comment 1 -R {PRIVATE} --body clean"
        rows = (
            ("the `? |` key gh writes", gh_config_yml(
                f"\n    co: pr checkout\n    {pp}\n    {pi}\n    ? |\n        trail\n    : pr view"),
             "an alias key", 11),
            ("a folded > scalar", gh_config_yml(
                f"\n    co: pr checkout\n    pp: >-\n        issue comment 1 -R {PUBLIC}\n        --body {CANARY}"
                f"\n    {pi}"), "a value", 9),
            ("an anchor and its alias", gh_config_yml(
                f"\n    co: pr checkout\n    pp: &x issue comment 1 -R {PUBLIC} --body {CANARY}\n    pa: *x\n    {pi}"),
             "a value", 9),
            ("a tag", gh_config_yml(
                f"\n    co: pr checkout\n    pp: !!str issue comment 1 -R {PUBLIC} --body {CANARY}\n    {pi}"),
             "a value", 9),
            ("an escape yaml.v3 does not write", gh_config_yml(
                f'\n    co: pr checkout\n    pp: "issue comment 1 -R {PUBLIC} --body \\q {CANARY}"\n    {pi}'),
             "a value", 9),
            ("a value continued on a more-indented line", gh_config_yml(
                f"\n    co: pr checkout\n    pp: issue comment 1 -R {PUBLIC}\n        --body {CANARY}\n    {pi}"),
             "a line indented otherwise than the aliases", 10),
            ("a key indented below an alias's empty value",
             gh_config_yml(f"\n    co: pr checkout\n    pc:\n        {pp}\n    {pi}"),
             "a line indented otherwise than the aliases", 10),
            ("an aliases key given twice", gh_config_yml(f"\n    co: pr checkout\n    {pp}\naliases:\n    {pi}"),
             "a second aliases key", 10),
            ("a line at column 0 that is no key at the root",
             gh_config_yml(f"\n    co: pr checkout\n    {pp}\n    {pi}\n- x"),
             "a line at column 0 that is no key at the root", 11),
            ("a flow mapping over several lines", gh_config_yml(f" {{co: pr checkout,\n    {pp},\n    {pi}}}"),
             "a flow mapping", 7),
            ("an aliases key with an anchor", gh_config_yml(f" &a\n    co: pr checkout\n    {pp}\n    {pi}"),
             "an aliases key written otherwise than `aliases:` at column 0", 7),
            ("a | block scalar that runs to the end of a file with no final line feed",
             f"version: 1\naliases:\n    co: pr checkout\n    {pi}\n    pp: |-\n        issue comment 1 -R {PUBLIC}\n"
             f"        --body {CANARY}", "a block scalar", 5))
        for number, (row, config, form, line) in enumerate(rows):
            env = self.gh_config(f"unread-{number}", None, config)
            shown = (f"{env['GH_CONFIG_DIR']}/config.yml holds {form} on line {line}, a form the check does not read, "
                     "so the check cannot read gh's aliases: fix that line")
            for argv in (["co", "1"], ["pp"], ["pi"]):
                with self.subTest(row=row, call=" ".join(argv)):
                    self.assert_alias_refused(argv, env, f"leak terms: gh {argv[0]}: {shown}")
            for argv in (["pr", "comment", "1", "-R", PUBLIC, "--body", "hello"], ["pr", "view", "1"], ["zz", "1"]):
                with self.subTest(row=row, call=" ".join(argv), control="no alias"):
                    self.setUp_calls()
                    self.assert_sent(self.run_gh(argv, env=env), argv)
            with self.subTest(row=row, call="pr comment", term=True):
                self.setUp_calls()
                self.assert_refused(self.run_gh(["pr", "comment", "1", "-R", PUBLIC, "--body", TEXT], env=env),
                                    "--body")
        # A name that only looks like a key inside another alias's value stands in config.yml before a `:` too, and
        # is refused: the check cannot know where yaml.v3 begins a key there (gh_alias_named).
        env = self.gh_config("unread-loose", None, gh_config_yml(
            f"\n    co: pr checkout\n    pp: >-\n        issue comment 1 -R {PRIVATE} --body 'notes,more: x'"))
        with self.subTest(row="a name inside a value", call="more"):
            self.assert_alias_refused(["more"], env, f"leak terms: gh more: {env['GH_CONFIG_DIR']}/config.yml holds a "
                                                     "value on line 9, a form the check does not read")
        # The `? |+` key gh writes for a name that ends with two line feeds puts an empty line between the name and the
        # `:`: `gh alias set $'trail\n\n' 'issue comment 1 -R o/pub'` wrote `? |+`, `        trail`, an empty line and
        # `    : issue comment 1 -R o/pub`, and gh ran `gh trail` as that alias (measured 2026-11-21 with gh 2.98.0).
        # `gh trail --body <term>` is refused. Control: the `? |` key of `$'trail\n'`, one line feed, refuses it too.
        for number, (row, key) in enumerate((("two line feeds, `? |+`", "? |+\n        trail\n"),
                                             ("control: one line feed, `? |`", "? |\n        trail"))):
            env = self.gh_config(f"unread-trail-{number}", None, gh_config_yml(
                f"\n    co: pr checkout\n    {key}\n    : issue comment 1 -R {PUBLIC}"))
            with self.subTest(row=row, call="trail"):
                self.assert_alias_refused(["trail", "--body", TEXT], env, (
                    f"leak terms: gh trail: {env['GH_CONFIG_DIR']}/config.yml holds an alias key on line 9, a form the "
                    "check does not read"))

    def test_an_alias_key_whose_name_holds_a_line_break_refuses_each_call_whose_name_stands_in_it(self) -> None:
        # gh writes the key of an alias name that holds a line break after `? `, double-quoted with yaml.v3's escape of
        # the break, in a flow mapping and, for a carriage return, in a block mapping, and runs the alias as the words
        # around the break. Measured 2026-11-21 with gh 2.98.0, under `env -i` with no token in a scratch
        # GH_CONFIG_DIR: after `gh alias delete co`, the last alias, `gh alias set $'pr say\n' …` wrote
        # `aliases: {? "pr say\n" : …}`, and so `$'\nlead'` and `$'trail\n\n'` wrote `? "\nlead"` and `? "trail\n\n"`;
        # `gh alias set $'cr\r' …` wrote `    ? "cr\r"`, then `    : …`; gh ran `gh pr say`, `gh lead`, `gh trail`
        # and `gh cr` as those aliases. Such a key is a form the reader does not read (gh_aliases), and its name
        # stands in config.yml before a `:` with the escape before or after it (gh_alias_named), so each call through
        # it is refused with exit 4, the refusal naming the line. Control: a flow mapping with only a key past 128
        # bytes, which gh writes after `? ` too and the reader reads, sends `gh pr say` and `gh trail` to gh.
        for number, (row, aliases, argv, line) in enumerate((
                ('flow, `? "pr say\\n"`', f' {{? "pr say\\n" : issue comment 1 -R {PUBLIC}}}', ["pr", "say"], 7),
                ('flow, `? "\\nlead"`', f' {{? "\\nlead" : issue comment 1 -R {PUBLIC}}}', ["lead"], 7),
                ('flow, `? "trail\\n\\n"`', f' {{? "trail\\n\\n" : issue comment 1 -R {PUBLIC}}}', ["trail"], 7),
                ('block, `? "cr\\r"`', f'\n    co: pr checkout\n    ? "cr\\r"\n    : issue comment 1 -R {PUBLIC}',
                 ["cr"], 9))):
            env = self.gh_config(f"break-key-{number}", None, gh_config_yml(aliases))
            with self.subTest(row=row, call=" ".join(argv)):
                self.assert_alias_refused([*argv, "--body", "x"], env, (
                    f"leak terms: gh {' '.join(argv)}: {env['GH_CONFIG_DIR']}/config.yml holds an alias key that holds "
                    f"a line break on line {line}, a form the check does not read"))
        env = self.gh_config("break-key-control", None, gh_config_yml(f" {{? pr {'x' * 130} : pr comment}}"))
        for argv in (["pr", "say", "--body", "x"], ["trail", "--body", "x"]):
            with self.subTest(control="a key past 128 bytes alone", call=argv[0]):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)

    def test_the_files_and_standard_input_of_a_call_through_an_alias_are_read(self) -> None:
        # A call through an alias has its files and standard input read as any call does (gh_contents): a
        # `--body-file` file, and `-` through the shim's buffer. From a checkout of a public repository, with `prc`
        # under `aliases: {prc: pr comment}`, which gh writes once `gh alias delete` removed the last alias: the term
        # in the file, or on standard input, is refused, and the same call with no term goes out.
        self.remotes(("origin", PUBLIC))
        env = self.gh_config("files-flow", None, gh_config_yml(" {prc: pr comment}"))
        for text in (TEXT, CLEAN):
            notes = self.file("notes.md", text)
            for source, argv, stdin, field in (
                    ("a file", ["prc", "1", "--body-file", notes], b"", f"--body-file {notes}"),
                    ("standard input", ["prc", "1", "--body-file", "-"], text.encode(),
                     "--body-file - (standard input)")):
                with self.subTest(source=source, term=text == TEXT):
                    self.setUp_calls()
                    result = self.run_gh(argv, stdin, env=env)
                    if text == CLEAN:
                        self.assert_sent(result, argv, stdin)
                        continue
                    self.assert_refused(result, field, line=1)
                    self.assertIn("gh pr comment to remote origin (public):", result.stderr.decode())

    def test_a_gh_alias_under_a_command_in_flow_form_is_read_as_the_command_it_expands_to(self) -> None:
        # gh writes its aliases as a flow mapping once the last alias was deleted, and an alias under a command goes
        # there too. Measured 2026-11-20 with fake tokens: with config.yml holding `aliases: {pr say: pr comment}`,
        # `gh pr say 1 -R o/r --body x` posted to "https://api.github.com/graphql", as `gh pr comment` does, and so did
        # `issue say` under `{issue say: issue comment}`, `repo say` under `{"repo say": issue comment}` and `run say`
        # after another key. shims/gh starts the check for such a call only when gh_shim_has_nested_alias finds the
        # alias's key in config.yml (NestedAliases), here after the flow mapping's `{` or a `,`. The check reads the
        # flow mapping as gh does (gh_aliases), so `pr say` is judged as `pr comment`: a term to a public repository is
        # refused, and the same call to a private one goes out, as does the call with no term to the public one.
        for number, (form, aliases) in enumerate((("flow", " {pr say: pr comment}"),
                                                  ("flow, quoted, after another key",
                                                   ' {prc: pr checkout, "pr say": pr comment}'))):
            env = self.gh_config(f"flow-nested-{number}", None, gh_config_yml(aliases))
            with self.subTest(form=form, repository=PUBLIC):
                self.setUp_calls()
                refused = self.run_gh(["pr", "say", "11", "-R", PUBLIC, "--body", TEXT], env=env)
                self.assert_refused(refused, "--body", line=1)
                self.assertIn(f"to {PUBLIC} (public):", refused.stderr.decode())
            for name, argv in (("a term, to a private repository", ["pr", "say", "11", "-R", PRIVATE, "--body", TEXT]),
                               ("no term", ["pr", "say", "11", "-R", PUBLIC, "--body", CLEAN])):
                with self.subTest(form=form, control=name):
                    self.setUp_calls()
                    self.assert_sent(self.run_gh(argv, env=env), argv)

    def test_each_form_gh_writes_an_alias_in_is_judged_by_where_it_goes(self) -> None:
        # The reader reads exactly each form gh 2.98.0 writes an alias in (gh_aliases), as `gh alias set` wrote them in
        # a scratch GH_CONFIG_DIR on 2026-11-20 (dispatch://INFRA-3365, review round 11): a block mapping, or the flow
        # mapping gh writes once `gh alias delete` removed the last alias; a value plain, a value of 200 characters
        # on one line, 'single-quoted' with `''` (a value with `: ` or a `#`), or "double-quoted" with yaml.v3's escapes
        # (a tab, a control character as `\x01`, a character outside the BMP as `\U0001F600`, a line break in flow
        # form); a value of several lines as a `|-`, `|` or `|+` block scalar, `|4-` where it starts with a space; an
        # alias named `aliases` or `hosts` among them. So such an alias whose expansion sends a listed term to a private
        # repository goes out, and the same alias to a public one is refused for it, its target named. The escapes
        # decode: the term spelled with `\x`, `\u` and `\U` is refused. A value gh writes with a line separator in it is
        # refused whatever it sends (test_a_line_break_other_than_a_line_feed_refuses_each_call_that_may_run_an_alias).
        words = " ".join(["word"] * 40)
        spelled = "\\x%02X\\u%04X\\U%08X%s" % (ord(CANARY[0]), ord(CANARY[1]), ord(CANARY[2]), CANARY[3:])
        rows = (
            ("block, plain, 200 characters", "\n    pc: issue comment 1 -R {repo} --body '{words} {body}'"),
            ("block, single-quoted with ''", "\n    pc: 'issue comment 1 -R {repo} --body ''note: {body}'''"),
            ("block, double-quoted, a tab", '\n    pc: "issue comment 1 -R {repo} --body \'a\\tb {body}\'"'),
            ("block, double-quoted, \\x, \\u and \\U escapes",
             '\n    pc: "issue comment 1 -R {repo} --body \'a\\x01\\U0001F600 {spelled}\'"'),
            ("block, |-", "\n    pc: |-\n        issue create -R {repo} --title x\n        --body {body}"),
            ("block, |", "\n    pc: |\n        issue create -R {repo} --title x\n        --body {body}"),
            ("block, |+ and an empty line",
             "\n    pc: |+\n        issue create -R {repo} --title x\n        --body {body}\n"),
            ("block, |4-", "\n    pc: |4-\n          issue create -R {repo} --title x\n        --body {body}"),
            ("block, beside aliases named aliases and hosts",
             "\n    aliases: pr list\n    pc: issue comment 1 -R {repo} --body {body}\n    hosts: auth status"),
            ("flow, plain", " {co: pr checkout, pc: issue comment 1 -R {repo} --body {body}}"),
            ("flow, single-quoted with ''", " {pc: 'issue comment 1 -R {repo} --body ''note: {body}'''}"),
            ("flow, double-quoted, a line break", ' {pc: "issue create -R {repo} --title x\\n--body {body}"}'),
            ("flow, beside aliases named aliases and hosts",
             " {aliases: pr list, pc: issue comment 1 -R {repo} --body {body}, hosts: auth status}"))
        for number, (row, aliases) in enumerate(rows):
            for repository in (PRIVATE, PUBLIC):
                text = (aliases.replace("{repo}", repository).replace("{words}", words).replace("{body}", CANARY)
                        .replace("{spelled}", spelled))
                env = self.gh_config(f"exact-{number}-{repository.replace('/', '-')}", None, gh_config_yml(text))
                with self.subTest(row=row, repository=repository):
                    self.setUp_calls()
                    if repository == PRIVATE:
                        self.assert_sent(self.run_gh(["pc"], env=env), ["pc"])
                        continue
                    refused = self.run_gh(["pc"], env=env)
                    self.assert_refused(refused, "--body", CANARY)
                    self.assertIn(f"to {PUBLIC} (public):", refused.stderr.decode())

    def test_a_line_break_other_than_a_line_feed_refuses_each_call_that_may_run_an_alias(self) -> None:
        # yaml.v3 begins a line at each of its line breaks, and the check reads config.yml's lines at its line feeds
        # alone, so it reads no config.yml that holds another break: each call that may run an alias, whose first word
        # or first two words stand in config.yml before a `:`, is refused with exit 4, the refusal naming the break and
        # its line (review round 14 of dispatch://INFRA-3365). The reviewer's config, as gh 2.98.0 wrote it on
        # 2026-11-20 in a scratch GH_CONFIG_DIR, under `env -i` with no token: `gh alias set note -` with `pr view 1`, a
        # line feed and a line separator on standard input, then `gh alias set pp 'issue comment 1 -R o/pub --body …'`.
        # gh writes `note: |+`, `        pr view 1`, then the separator and `    pp: …` on one line, and `gh alias list`
        # lists `pp`. `gh pp` is refused with a term and without one, and so it is with a paragraph separator, a next
        # line or a carriage return there, which gh reads as breaks too, though it writes those two as escapes; a clean
        # `pr comment` and `pr view 1`, which run no alias, go out. An alias name stands there only whole: `s`, which
        # ends `aliases:`, and `tor`, which ends `editor:`, are no alias, so `gh s x` and `gh tor x` go out to gh too.
        # `pp` is refused too after a line that holds only a separator, and where a separator hides the aliases key
        # itself (`editor: vim<U+2028>aliases:`, under which `gh alias list` lists `pp`); and so is `sq`, a
        # single-quoted value holding a separator as gh writes it.
        for number, (shown, separator) in enumerate((
                ("LINE SEPARATOR (U+2028)", "\u2028"), ("PARAGRAPH SEPARATOR (U+2029)", "\u2029"),
                ("NEXT LINE (U+0085)", "\x85"), ("CARRIAGE RETURN (U+000D)", "\r"))):
            for body in (CANARY, "clean"):
                env = self.gh_config(f"break-{number}-{body}", None, gh_config_yml(
                    f"\n    co: pr checkout\n    note: |+\n        pr view 1\n"
                    f"{separator}    pp: issue comment 1 -R {PUBLIC} --body {body}"))
                with self.subTest(separator=shown, call="pp", term=body == CANARY):
                    self.assert_alias_refused(["pp"], env, f"leak terms: gh pp: {env['GH_CONFIG_DIR']}/config.yml "
                                                           f"holds a {shown} on line 11, a line break other than a "
                                                           "line feed")
            for argv in (["pr", "comment", "1", "-R", PUBLIC, "--body", "hello"], ["pr", "view", "1"], ["s", "x"],
                         ["tor", "x"]):
                with self.subTest(separator=shown, call=" ".join(argv[:2])):
                    self.setUp_calls()
                    self.assert_sent(self.run_gh(argv, env=env), argv)
        pp = f"pp: issue comment 1 -R {PUBLIC} --body {CANARY}"
        for number, (row, config, name, line) in enumerate((
                ("after a line that holds only a line separator",
                 gh_config_yml(f"\n    note: pr view\n\u2028\n    {pp}"), "pp", 9),
                ("where a line separator hides the aliases key", f"version: 1\neditor: vim\u2028aliases:\n    {pp}\n",
                 "pp", 2),
                ("a single-quoted value holding a line separator, as gh writes it",
                 gh_config_yml(f"\n    co: pr checkout\n    sq: 'issue comment 1 -R {PRIVATE} --body #{CANARY}\u2028"
                               "        x'"), "sq", 9))):
            env = self.gh_config(f"break-row-{number}", None, config)
            with self.subTest(row=row, call=name):
                self.assert_alias_refused([name], env, f"leak terms: gh {name}: {env['GH_CONFIG_DIR']}/config.yml "
                                                       f"holds a LINE SEPARATOR (U+2028) on line {line}")

    def test_shims_gh_starts_the_check_for_an_alias_whose_key_follows_a_line_separator(self) -> None:
        # shims/gh starts the check for a call under a gh command only where gh_shim_has_nested_alias finds that
        # config.yml may hold an alias under that command, a key at an indented line's start or after a `{` or a `,`.
        # yaml.v3 begins a line at a line separator too, so `    pr say: pr comment` after one, on the same line, is an
        # alias gh runs (`gh alias list` lists it, measured 2026-11-20 with gh 2.98.0), at no line start the shim's
        # pattern finds. The shim starts the check for a config.yml that holds such a break, and the check refuses `gh
        # pr say`, clean as it is: gh never runs. Control: the same config with no separator, which the check reads,
        # sends the clean `gh pr say` to gh.
        aliases = "\n    co: pr checkout\n    note: |+\n        pr view 1\n{separator}    pr say: pr comment"
        argv = ["pr", "say", "1", "-R", PUBLIC, "--body", CLEAN]
        env = self.gh_config("say-separator", None, gh_config_yml(aliases.format(separator="\u2028")))
        with self.subTest(separator=True):
            result = self.run_gh(argv, env=env)
            stderr = result.stderr.decode("utf-8", "replace")
            self.assertEqual(result.returncode, 2, stderr)
            self.assertEqual(self.gh_calls(), [])
            self.assertIn(f"leak terms: gh pr say: {env['GH_CONFIG_DIR']}/config.yml holds a LINE SEPARATOR (U+2028) "
                          "on line 11", stderr)
        with self.subTest(control="no separator"):
            env = self.gh_config("say-plain", None, gh_config_yml(aliases.format(separator="")))
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv, env=env), argv)

    def test_shims_gh_starts_the_check_for_each_question_mark_key_gh_writes(self) -> None:
        # gh writes an alias key after `? ` where yaml.v3 does not write it as a simple key: `? |` in a block mapping
        # for a name that ends with a line feed, and a key past 128 bytes, in a block and in a flow mapping. Measured
        # 2026-11-21 with gh 2.98.0: `gh alias set $'pr say\n' …` wrote `    ? |`, `        pr say`, then `    : …`,
        # and gh ran `gh pr say` as that alias. shims/gh starts the check for a call under a gh command whenever
        # config.yml holds a `? ` key (gh_shim_has_nested_alias, NestedAliases): `gh pr say 1 -R o/pub --body <term>`
        # is refused (exit 2) on the `? |` config, which the check does not read, and the call through the 128-byte
        # key, which it reads, is refused for the term it sends to o/pub. In each, gh never runs. Control: with no
        # term, the call through the 128-byte key goes out.
        env = self.gh_config("question-say", None, gh_config_yml(
            "\n    co: pr checkout\n    ? |\n        pr say\n    : pr comment"))
        with self.subTest(key="? |, pr say"):
            self.setUp_calls()
            result = self.run_gh(["pr", "say", "1", "-R", PUBLIC, "--body", TEXT], env=env)
            stderr = result.stderr.decode("utf-8", "replace")
            self.assertEqual(result.returncode, 2, stderr)
            self.assertEqual(self.gh_calls(), [])
            self.assertIn(f"leak terms: gh pr say: {env['GH_CONFIG_DIR']}/config.yml holds an alias key on line 9, a "
                          "form the check does not read", stderr)
        long = "x" * 130
        for number, (form, aliases) in enumerate((
                ("block", f"\n    co: pr checkout\n    ? pr {long}\n    : pr comment"),
                ("flow", f" {{? pr {long} : pr comment}}"))):
            env = self.gh_config(f"question-long-{number}", None, gh_config_yml(aliases))
            with self.subTest(key=f"past 128 bytes, {form}"):
                self.setUp_calls()
                refused = self.run_gh(["pr", long, "1", "-R", PUBLIC, "--body", TEXT], env=env)
                self.assert_refused(refused, "--body", line=1)
                self.assertIn(f"to {PUBLIC} (public):", refused.stderr.decode())
            with self.subTest(key=f"past 128 bytes, {form}", control="no term"):
                argv = ["pr", long, "1", "-R", PUBLIC, "--body", CLEAN]
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)

    def test_a_crlf_config_yml_is_read_as_with_line_feeds(self) -> None:
        # yaml.v3 reads a carriage return and a line feed as one line break (is_crlf), and so does the check
        # (gh_config_text). With a gh-written config.yml whose lines end in CRLF, as an editor may save it, the aliases
        # are read exactly: a clean `note` goes out; with a term to a private repository it goes out, judged by that
        # repository; with a term to a public one it is refused (exit 3) for that repository, and so is the `|-` alias
        # `nt`.
        aliases = ("\n    co: pr checkout\n    note: pr comment 1 -R {repo} --body {body}\n    nt: |-\n"
                   "        issue create -R {repo} --title x\n        --body {body}")

        def crlf(name: str, repository: str, body: str) -> dict[str, str]:
            config = gh_config_yml(aliases.format(repo=repository, body=body))
            return self.gh_config(name, None, config.replace("\n", "\r\n"))

        for name, env in (("a clean note", crlf("crlf-clean", PUBLIC, "clean")),
                          ("a term to a private repository", crlf("crlf-private", PRIVATE, CANARY))):
            with self.subTest(call=name):
                self.setUp_calls()
                self.assert_sent(self.run_gh(["note"], env=env), ["note"])
        env = crlf("crlf-public", PUBLIC, CANARY)
        for name, shown in (("note", "gh pr comment"), ("nt", "gh issue create")):
            with self.subTest(call=name, term=True):
                refused = self.check([name], env)
                stderr = refused.stderr.decode("utf-8", "replace")
                self.assertEqual(refused.returncode, 3, stderr)
                self.assertIn(f"leak terms: {shown} to {PUBLIC} (public): --body line ", stderr)

    def test_a_line_break_other_than_a_line_feed_in_config_yml_is_outside_the_plain_case(self) -> None:
        # gh reads a key after a line separator as a key at the root (measured 2026-11-20 with gh 2.98.0: with
        # `editor: vim<U+2028>hosts:` and a host below it in config.yml, `gh auth status` lists that host), and the
        # check reads config.yml's lines at its line feeds alone, so it would not see that `hosts` key. A config.yml
        # that holds a line break other than a line feed is outside the plain case (gh_config_condition), and the
        # refusal names the break: a term to a public repository is refused, and so is one to a private repository,
        # which gh may look for on the host that key lists; the same call with no term goes out.
        self.checkout(("origin", f"https://github.com/{PRIVATE}.git"))
        env = self.gh_config("hosts-break", None, 'version: "1"\neditor: vim\u2028hosts:\n    ghe.example.com:\n'
                                                  "        user: x\n")
        condition = ("to wherever gh sends it, read as public, since gh's config.yml holds a LINE SEPARATOR (U+2028) "
                     "on line 2, a line break other than a line feed, which the check does not read")
        for repository in (PUBLIC, PRIVATE):
            with self.subTest(repository=repository):
                self.setUp_calls()
                refused = self.run_gh(["pr", "comment", "1", "-R", repository, "--body", TEXT], env=env)
                self.assert_refused(refused, "--body")
                self.assertIn(condition, refused.stderr.decode())
        with self.subTest(control="no term"):
            argv = ["pr", "comment", "1", "-R", PRIVATE, "--body", CLEAN]
            self.setUp_calls()
            self.assert_sent(self.run_gh(argv, env=env), argv)

    def test_an_alias_shlex_cannot_split_refuses_each_call_that_runs_it(self) -> None:
        # The check splits an alias as a shell line with Python's shlex; gh splits it with google/shlex, for which a `#`
        # begins a comment, so from `pr comment 1 -R o/pub --body-file - # Sami's alias` gh runs `pr comment 1 -R o/pub
        # --body-file -`, which reads standard input, where Python's shlex refuses the apostrophe. A call that runs such
        # an alias of a config.yml the reader reads, here a double-quoted value, is refused with exit 4, the refusal
        # naming the alias, the line of its key and shlex's message. Where the reader does not read config.yml, the
        # alias is refused for the form config.yml holds, before shlex reads it: a folded `>-` alias, and the shell
        # alias `sh1` beside the `? |` key, as gh 2.98.0 wrote them on 2026-11-20 (`gh alias set --shell sh1 "gh pr
        # comment … # don't wrap"`, then `gh alias set $'trail\n' 'pr view'`). A clean `pr comment`, which runs no
        # alias, goes out beside each. Control: the double-quoted alias with no apostrophe is read, and its standard
        # input with it: it is refused for the term on it (exit 3), and with a clean standard input it goes out.
        unsplit = ("leak terms: gh {name}: shlex cannot split gh's alias {name}, on line {line} of {config}, as a "
                   "shell line (No closing quotation)")
        unread = "leak terms: gh {name}: {config} holds {form} on line {line}, a form the check does not read"
        for number, (row, aliases, name, shown) in enumerate((
                ("a double-quoted alias the reader reads",
                 f"\n    co: pr checkout\n    note: \"pr comment 1 -R {PUBLIC} --body-file - # Sami's alias\"", "note",
                 unsplit.replace("{line}", "9")),
                ("a folded alias",
                 f"\n    co: pr checkout\n    note: >-\n        pr comment 1 -R {PUBLIC} --body-file - # Sami's alias",
                 "note", unread.replace("{form}", "a value").replace("{line}", "9")),
                ("a shell alias beside the `? |` key gh writes",
                 f"\n    co: pr checkout\n    sh1: '!gh pr comment 1 -R {PUBLIC} --body-file - # don''t wrap'\n"
                 "    ? |\n        trail\n    : pr view", "sh1",
                 unread.replace("{form}", "an alias key").replace("{line}", "10")))):
            env = self.gh_config(f"unsplit-{number}", None, gh_config_yml(aliases))
            with self.subTest(row=row, call=name):
                self.assert_alias_refused([name], env, shown.format(
                    name=name, config=f"{env['GH_CONFIG_DIR']}/config.yml"), TEXT.encode())
            with self.subTest(row=row, call="pr comment"):
                argv = ["pr", "comment", "1", "-R", PUBLIC, "--body", "hello"]
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)
        env = self.gh_config("unsplit-control", None, gh_config_yml(
            f"\n    co: pr checkout\n    note: \"pr comment 1 -R {PUBLIC} --body-file - # Sams alias\""))
        with self.subTest(control="no apostrophe", term=True):
            refused = self.check(["note"], env, TEXT.encode())
            stderr = refused.stderr.decode("utf-8", "replace")
            self.assertEqual(refused.returncode, 3, stderr)
            self.assertIn(": --body-file - (standard input) line 1: ", stderr)
        with self.subTest(control="no apostrophe", term=False):
            self.setUp_calls()
            self.assert_sent(self.run_gh(["note"], CLEAN.encode(), env), ["note"], CLEAN.encode())
        # gh refuses a call that leaves an alias's `$N` unfilled itself (`not enough arguments for alias`), so the check
        # tests the `$N` before it splits the alias: with `z: "api -X GET zen --jq $1 # Sami's"`, which shlex cannot
        # split, `gh z` with no argument goes out to gh (measured 2026-11-21 with gh 2.98.0, which refused it, exit 1).
        env = self.gh_config("unsplit-unfilled", None, gh_config_yml(
            "\n    co: pr checkout\n    z: \"api -X GET zen --jq $1 # Sami's\""))
        with self.subTest(row="an unfilled $N", call="z"):
            self.setUp_calls()
            self.assert_sent(self.run_gh(["z"], env=env), ["z"])

    def test_an_argument_that_makes_an_alias_unsplittable_refuses_the_call_and_names_the_arguments(self) -> None:
        # gh puts a call's arguments in place of an alias's `$N` before it splits the alias as a shell line, and so
        # does the check (gh_expand_alias), so an argument can make what the alias runs a text shlex cannot split,
        # though the alias's own line splits. With `note: pr comment 1 -R o/pub --body $1`, as `gh alias set` wrote
        # it (measured 2026-11-20 with gh 2.98.0), `gh note "it's"` is refused with exit 4 before gh runs, the refusal
        # naming the arguments to change and no line of config.yml to fix. Control: `gh note ok` goes out.
        env = self.gh_config("unsplit-argument", None, gh_config_yml(
            f"\n    co: pr checkout\n    note: pr comment 1 -R {PUBLIC} --body $1"))
        argv = ["note", "it's"]
        with self.subTest(call="note it's"):
            self.assert_alias_refused(argv, env, (
                "leak terms: gh note: the call's arguments, put in place of the $N of gh's alias note (line 9 of "
                f"{env['GH_CONFIG_DIR']}/config.yml), make its expansion a text shlex cannot split as a shell line (No "
                "closing quotation), so the check cannot judge the call: change the arguments"))
            self.assertNotIn("fix that line", self.check(argv, env).stderr.decode("utf-8", "replace"))
        with self.subTest(control="note ok"):
            self.setUp_calls()
            self.assert_sent(self.run_gh(["note", "ok"], env=env), ["note", "ok"])

    def test_a_call_through_an_alias_is_judged_by_that_alias_alone(self) -> None:
        # A call through an alias is judged by what that alias runs alone, so an alias that holds a listed term refuses
        # no call through another alias, a read included. With the reviewer's aliases, as gh wrote them, and the knives
        # ygo-fork list, which holds `theorem-labs`, `co 1` and the read `ai` go out. With a `|-` alias beside `co`,
        # and in flow form, `co 1` goes out, and the alias whose expansion sends a term to a public repository is
        # refused, its target named.
        reviewer = {**self.ygo_fork(), "GH_CONFIG_DIR": self.gh_config("reviewer", None, gh_config_yml(
            " {co: pr checkout, ai: pr list -R theorem-labs/agent-infra}"))["GH_CONFIG_DIR"]}
        for argv in (["co", "1"], ["ai"]):
            with self.subTest(aliases="the reviewer's", call=argv[0]):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=reviewer), argv)
        for number, (form, aliases, name) in enumerate((
                ("block, a |- alias beside co",
                 f"\n    co: pr checkout\n    nt: |-\n        issue create -R {PUBLIC} --title x\n        --body {CANARY}",
                 "nt"),
                ("flow", f" {{co: pr checkout, pc: pr comment 1 -R {PUBLIC} --body {CANARY}}}", "pc"))):
            env = self.gh_config(f"alone-{number}", None, gh_config_yml(aliases))
            with self.subTest(form=form, call="co 1"):
                self.setUp_calls()
                self.assert_sent(self.run_gh(["co", "1"], env=env), ["co", "1"])
            with self.subTest(form=form, call=name):
                self.setUp_calls()
                refused = self.run_gh([name], env=env)
                self.assert_refused(refused, "--body", CANARY, line=1)
                self.assertIn(f"to {PUBLIC} (public):", refused.stderr.decode())

    def test_each_key_form_gh_writes_unquoted_is_read_exactly(self) -> None:
        # `gh alias set` writes a key unquoted wherever yaml.v3 reads it as a plain scalar, and the reader reads each so
        # (gh_yaml_plain): a key with a `:` inside, or that begins with `?`, `:` or `-` before a character that is no
        # blank (`pr:mine`, `?mine`, `-x`, `:x`, `a::b`, `x:y:z`; in flow form `-x`), and after a `? ` a key longer
        # than 128 bytes, its value on the next line after a `:`, or after a ` :` in flow form. Each config here holds
        # the lines `gh alias set` wrote in a scratch GH_CONFIG_DIR (dispatch://INFRA-3365, review round 12, measured
        # 2026-11-20), with `pc`, an issue comment that sends a listed term, beside such a key. `pc` to a private
        # repository goes out, and to a public one is refused, its target named.
        long = "a" * 129
        pc = f"pc: issue comment 1 -R {{repo}} --body {CANARY}"
        rows = (
            ("pr:mine", f"\n    co: pr checkout\n    pr:mine: pr list --author @me\n    {pc}"),
            ("?mine", f"\n    co: pr checkout\n    ?mine: pr list --author @me\n    {pc}"),
            ("-x", f"\n    co: pr checkout\n    -x: pr list --author @me\n    {pc}"),
            ("a::b", f"\n    co: pr checkout\n    a::b: pr list --author @me\n    {pc}"),
            (":x and x:y:z",
             f"\n    co: pr checkout\n    :x: pr list --author @me\n    x:y:z: pr list --author @me\n    {pc}"),
            ("a key past 128 bytes", f"\n    co: pr checkout\n    ? {long}\n    : pr list --author @me\n    {pc}"),
            ("a key past 128 bytes, its value a |- block",
             f"\n    co: pr checkout\n    ? {long}\n    : |-\n        issue create --title x\n        --body y\n    {pc}"),
            ("-x, in flow form", f" {{-x: pr list --author @me, {pc}}}"),
            ("a key past 128 bytes, in flow form", f" {{? {long} : pr list --author @me, {pc}}}"))
        for number, (row, aliases) in enumerate(rows):
            for repository in (PRIVATE, PUBLIC):
                env = self.gh_config(f"key-{number}-{repository.replace('/', '-')}", None,
                                     gh_config_yml(aliases.replace("{repo}", repository)))
                with self.subTest(row=row, repository=repository):
                    self.setUp_calls()
                    if repository == PRIVATE:
                        self.assert_sent(self.run_gh(["pc"], env=env), ["pc"])
                        continue
                    refused = self.run_gh(["pc"], env=env)
                    self.assert_refused(refused, "--body", CANARY, line=1)
                    self.assertIn(f"to {PUBLIC} (public):", refused.stderr.decode())

    def test_a_file_an_alias_beside_a_key_with_a_colon_names_is_read(self) -> None:
        # The reviewer's measurement (dispatch://INFRA-3365, review round 12): from a checkout of a private repository,
        # gh wrote `pr:mine`, `note: pr comment -R o/pub --body-file NOTES.md` and `b: browse`, and NOTES.md holds a
        # listed term, which `note 1` sends to the public repository. The reader reads `pr:mine` exactly, so `note 1`
        # is judged by the file its alias names: it is refused, judged by the public repository, with the `pr:mine`
        # line and without it.
        self.remotes(("origin", PRIVATE))
        self.file("NOTES.md", TEXT)
        note = f"\n    note: pr comment -R {PUBLIC} --body-file NOTES.md\n    b: browse"
        for row, aliases in (("with pr:mine", f"\n    co: pr checkout\n    pr:mine: pr list --author @me{note}"),
                             ("without pr:mine", f"\n    co: pr checkout{note}")):
            env = self.gh_config(row.replace(" ", "-").replace(":", "-"), None, gh_config_yml(aliases))
            with self.subTest(row=row):
                self.setUp_calls()
                refused = self.run_gh(["note", "1"], env=env)
                self.assert_refused(refused, "--body-file NOTES.md", line=1)
                self.assertIn(f"gh pr comment to {PUBLIC} (public):", refused.stderr.decode())

    def test_an_alias_call_of_a_config_yml_the_reader_does_not_read_is_refused_before_any_text_is_read(self) -> None:
        # Where the reader does not read config.yml (here, folded `>-` aliases), a call whose alias name stands in it
        # is refused with exit 4 before the check reads any text of the call (gh_aliases), the refusal naming the first
        # line the reader does not read: whatever a file, standard input, a directory, a glob or a pipe that the alias
        # or the call names holds, and with no standard input asked of the shim. Refused so: `note`, which names
        # NOTES.md, `b`, which runs `note`, the alias keyed after a `? ` past 128 bytes, `folded` and `quoted`, which
        # name `my notes.md` in shell quotes in a folded and in a double-quoted value, `stdin`, which names standard
        # input, and `pipe`, a named pipe, each with a listed term in what it names and without; the reviewer's
        # `title_dash`, whose quoted title holds a bare `-`, and two aliases that read standard input after such a
        # title; and from a checkout with a `docs/` directory and `star.md`, which holds a listed term, `b docs`, `b .`,
        # `co docs`, `co --body '*'`, `b notes.md`, `g notes.md`, where `g` runs `gist create` and notes.md holds a
        # listed term, and `co --body-file <(…)`.
        os.mkfifo(self.work / "pipe")
        long = "a" * 129

        def shown(env: dict[str, str], name: str, line: int) -> str:
            return (f"leak terms: gh {name}: {env['GH_CONFIG_DIR']}/config.yml holds a value on line {line}, a form the "
                    "check does not read")

        env = self.gh_config("expansion", None, gh_config_yml(
            "\n    co: pr checkout\n    note: >-\n        pr comment 1 -R o/priv\n        --body-file NOTES.md"
            "\n    b: note\n    stdin: >-\n        pr comment 1 -R o/priv --body-file -"
            f"\n    pipe: >-\n        pr comment 1 -R o/priv --body-file pipe\n    ? {long}\n    : >-"
            "\n        pr comment 1 -R o/priv --body-file NOTES.md"
            "\n    folded: >-\n        pr comment 1 -R o/priv --body-file 'my notes.md'"
            "\n    quoted: \"pr comment 1 -R o/priv --body-file 'my notes.md'\""))
        for text in (TEXT, CLEAN):
            self.file("NOTES.md", text)
            self.file("my notes.md", text)
            for name in ("note", "b", long, "folded", "quoted", "stdin", "pipe"):
                with self.subTest(call=name[:20], term=text == TEXT):
                    stdin = text.encode() if name == "stdin" else b""
                    self.assert_alias_refused([name], env, shown(env, name, 9), stdin)
        env = self.gh_config("dash", None, gh_config_yml(
            "\n    co: pr checkout\n    title_dash: >-\n        pr create -R o/priv --title 'WIP - draft' --body x"
            "\n    folded: >-\n        pr create -R o/priv --title 'WIP - draft' --body-file -"
            '\n    quoted: "pr create -R o/priv --title \'WIP - draft\'\n        --body-file -"'))
        for name in ("title_dash", "folded", "quoted"):
            with self.subTest(call=name):
                self.assert_alias_refused([name], env, shown(env, name, 9))
        (self.work / "docs").mkdir()
        self.file("notes.md", TEXT)
        self.file("star.md", TEXT)
        env = self.gh_config("words", None, gh_config_yml(
            "\n    co: pr checkout\n    b: browse\n    g: >-\n        gist create"))
        for argv in (["b", "docs"], ["b", "."], ["co", "docs"], ["co", "--body", "*"], ["b", "notes.md"],
                     ["g", "notes.md"]):
            with self.subTest(call=" ".join(argv)):
                self.assert_alias_refused(argv, env, shown(env, argv[0], 10))
        with self.subTest(call="co --body-file <(…)"):
            self.setUp_calls()
            result = subprocess.run(["bash", "-c", '"$GH" co --body-file <(printf %s "$BODY")'],
                                    env={**env, "GH": str(GH), "BODY": TEXT}, cwd=self.work, stdin=subprocess.DEVNULL,
                                    capture_output=True, timeout=240, check=False)
            stderr = result.stderr.decode("utf-8", "replace")
            self.assertEqual(result.returncode, 2, stderr)
            self.assertEqual(self.gh_calls(), [])
            self.assertIn(shown(env, "co", 10), stderr)

    def test_a_remote_is_read_by_gh_s_own_rule_for_its_url(self) -> None:
        # gh 2.98.0 takes a remote's repository from its fetch URL, else from its push URL, by git.ParseURL and
        # ghrepo.FromURL: there must be a host, and a path of exactly two parts. That rule is not git's. Measured with
        # the real gh (`gh browse -n`, dispatch://INFRA-3365, review round 12): `file://github.com/O/R.git` is
        # github.com's O/R; a fetch URL `https::https://…`, whose scheme no `//` follows, has no host, so gh takes the
        # push URL; and `./a:b`, a path for git, is scp-like for gh, a/b on the host `.`, so the remote is not on
        # github.com. So a comment with a term is refused: by the public theorem-labs/ygo beside a private fork, by the
        # public push URL, and outside the plain case for `./a:b`, though it names a private repository. The same
        # comments with no term go out.
        self.answer(YGO, "public")
        for form, remotes, push, argv, shown in (
                ("file://github.com beside the private fork",
                 (("origin", f"file://github.com/{YGO}.git"), ("fork", f"https://github.com/{PRIVATE}.git")), "",
                 ["pr", "comment", "1", "--body"], "remote origin (public)"),
                ("https::https:// fetching, pushing to a public repository",
                 (("origin", f"https::https://github.com/{PRIVATE}.git"),), f"https://github.com/{PUBLIC}.git",
                 ["pr", "comment", "1", "--body"], "remote origin (public)"),
                ("./a:b fetching, pushing to a public repository", (("origin", "./a:b"),),
                 f"https://github.com/{PUBLIC}.git", ["pr", "comment", "1", "-R", PRIVATE, "--body"],
                 "since the remote origin (./a:b) is not on github.com")):
            self.checkout(*remotes)
            if push:
                self.git("remote", "set-url", "--push", "origin", push)
            with self.subTest(form=form):
                self.setUp_calls()
                refused = self.run_gh([*argv, TEXT])
                self.assert_refused(refused, "--body")
                self.assertIn(shown, refused.stderr.decode())
            with self.subTest(form=form, control="no term"):
                self.setUp_calls()
                self.assert_sent(self.run_gh([*argv, CLEAN]), [*argv, CLEAN])

    def test_a_config_line_outside_the_aliases_changes_nothing(self) -> None:
        # A line of config.yml outside its aliases changes neither how the aliases are read nor the plain case, unless
        # it holds a line break other than a line feed. These configs are ones gh 2.98.0 writes (the tester's, review
        # round 10 of dispatch://INFRA-3365, finding 4): one with `editor: ?x`, one with a pager holding U+2028, and one
        # with the tester's four lines. With the block aliases `co: pr checkout`, `pv: pr view`, `pc`, an internal
        # comment that sends a listed term to a private repository, and `pr say`, which makes shims/gh hand every `gh pr
        # …` call to the check, each call goes out under `editor: ?x`: `pr view 1`, `pr ready 1`, `co 1`, `pv 1`, and
        # `pc`, judged in the plain case by that repository. The pager's U+2028 is a line break the check does not read
        # (test_a_line_break_other_than_a_line_feed_refuses_each_call_that_may_run_an_alias): `pr view 1` and `pr ready
        # 1`, which run no alias, go out beside it, and `co 1`, `pv 1` and `pc` are refused, the refusal naming the
        # pager's line.
        aliases = (f"\n    co: pr checkout\n    pv: pr view\n    pc: pr comment 1 -R {PRIVATE} --body 'see {CANARY}'"
                   "\n    pr say: pr comment")
        for number, (row, config) in enumerate((
                ("editor: ?x", gh_config_yml(aliases).replace("editor:\n", "editor: ?x\n")),
                ("a pager holding U+2028", gh_config_yml(aliases).replace("pager:\n", "pager: less\u2028-R\n")),
                ("the tester's four lines", gh_tester_config_yml(aliases)))):
            env = self.gh_config(f"outside-{number}", None, config)
            for argv in (["pr", "view", "1"], ["pr", "ready", "1"], ["co", "1"], ["pv", "1"], ["pc"]):
                with self.subTest(row=row, call=" ".join(argv)):
                    self.setUp_calls()
                    if "\u2028" in config and argv[0] != "pr":
                        self.assert_alias_refused(argv, env, f"leak terms: gh {argv[0]}: {env['GH_CONFIG_DIR']}/"
                                                             "config.yml holds a LINE SEPARATOR (U+2028) on line 6")
                        continue
                    self.assert_sent(self.run_gh(argv, env=env), argv)

    def test_in_an_agent_session_the_agent_config_directory_is_the_plain_case(self) -> None:
        # In an agent's shell shims/gh points GH_CONFIG_DIR at the directory scripts/gh-agent-config makes: its hosts.yml
        # lists `github.com:` alone, with a placeholder login, and its config.yml links to the user's, here one gh wrote
        # with three of the tester's four lines and the aliases `co: pr checkout` and `pr say`, which makes shims/gh
        # hand every `gh pr …` call to the check. That directory is the plain case (the tester's agent rows, review
        # round 10 of dispatch://INFRA-3365): a comment with a term to a private repository goes out, the same comment
        # to a public one is refused, and `pr view 1` and `co 1` go out. The tester's fourth line, a pager holding
        # U+2028, is left out: such a line break takes config.yml out of the plain case
        # (test_a_line_break_other_than_a_line_feed_in_config_yml_is_outside_the_plain_case).
        user = self.tmp / "config" / "gh"
        user.mkdir()
        (user / "config.yml").write_text(gh_tester_config_yml("\n    co: pr checkout\n    pr say: pr comment").replace(
            "pager: less\u2028-R\n", "pager:\n"), encoding="utf-8")
        env = self.agent()
        for argv in (["pr", "comment", "1", "-R", PRIVATE, "--body", TEXT], ["pr", "view", "1"], ["co", "1"]):
            with self.subTest(call=" ".join(argv[:2])):
                self.setUp_calls()
                self.assert_sent(self.run_gh(argv, env=env), argv)
        with self.subTest(call="pr comment, to a public repository"):
            self.setUp_calls()
            refused = self.run_gh(["pr", "comment", "1", "-R", PUBLIC, "--body", TEXT], env=env)
            self.assert_refused(refused, "--body")
            self.assertIn(f"to {PUBLIC} (public):", refused.stderr.decode())
        agent = self.tmp / "config" / "gh-agent"
        self.assertTrue((agent / "config.yml").samefile(user / "config.yml"))
        self.assertTrue((agent / "hosts.yml").read_text(encoding="utf-8").startswith("github.com:\n"))

    def test_repo_create_makes_the_repository_on_the_host_of_its_name(self) -> None:
        # gh 2.98.0 makes a new repository on the host of its name (pkg/cmd/repo/create createFromScratch,
        # createFromLocal): `HOST/OWNER/NAME` on HOST, and in the plain case `OWNER/NAME` and a name with no `/` on
        # github.com; and it asks for a --template repository, read the same way, on that one's host. Measured
        # 2026-11-20 with fake tokens: `repo create ghe.example.com/o/r --private --description x` with hosts.yml
        # listing only github.com first asked "https://ghe.example.com/api/v3/users/o". A repository on another host
        # is read as public, made private or not (gh_created), so each refused row here is refused for its host.
        create = ["repo", "create"]
        template = [*create, "github.com/o/r", "--private", "--template"]
        # (row, the call, the field and the match its refusal names and the target it shows, "" where it goes out)
        for row, argv, field, matched, shown in (
                ("HOST/OWNER/NAME on ghe.example.com", [*create, "ghe.example.com/o/r", "--private", "--description",
                                                       TEXT], "--description", CANARY.upper(), ON_GHE),
                ("control: OWNER/NAME, made private on github.com", [*create, "o/r", "--private", "--description",
                                                                    TEXT], "", "", ""),
                ("control: NAME, made private on github.com", [*create, "r", "--private", "--description", TEXT], "",
                 "", ""),
                ("--template on ghe.example.com", [*template, f"ghe.example.com/o/t-{CANARY}"], "--template", CANARY,
                 "the template repository on ghe.example.com, read as public"),
                ("control: --template on github.com", [*template, f"o/t-{CANARY}"], "", "", "")):
            with self.subTest(row=row):
                self.setUp_calls()
                if not shown:
                    self.assert_sent(self.run_gh(argv), argv)
                    continue
                refused = self.run_gh(argv)
                self.assert_refused(refused, field, matched, line=1)
                self.assertIn(shown, refused.stderr.decode())

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
        # term.
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
                 TEST_TERMS[0].replace(" ", "\\u00a0", 1), GLOBAL),
                ("a JSON body's string", ["api", f"repos/{PUBLIC}/issues/1/comments", "--input", input_body], self.env,
                 "--input md.json .body", CANARY, GLOBAL),
                ("INFRA\\-1 under the ygo list", [*ygo, "see INFRA\\-1"], env, "--body", "INFRA-", YGO_LIST),
                ("INFRA&#45;1 under the ygo list", [*ygo, "see INFRA&#45;1"], env, "--body", "INFRA-", YGO_LIST)):
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


class NestedAliases(unittest.TestCase):
    """gh_shim_has_nested_alias makes shims/gh start the check for a call under a gh command whenever gh's config.yml
    may hold an alias under that command, in each form gh writes its aliases: a block mapping, and the flow mapping gh
    writes once the last alias was deleted. Matching too much only starts the check, which then finds no alias."""

    @staticmethod
    def has_nested_alias(command: str, config: str, locale: str = "") -> bool:
        """gh_shim_has_nested_alias COMMAND, lifted out of the shim by text (sourcing shims/gh runs it), with gh's
        config.yml, in a new GH_CONFIG_DIR, holding CONFIG, in LOCALE (LC_ALL) when given."""
        text = GH.read_text(encoding="utf-8")
        start = text.index("gh_shim_has_nested_alias() {")
        script = text[start:text.index("\n}\n", start) + 3] + f"\ngh_shim_has_nested_alias '{command}'"
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "config.yml").write_text(config, encoding="utf-8")
            env = {"PATH": os.environ["PATH"], "GH_CONFIG_DIR": directory, **({"LC_ALL": locale} if locale else {})}
            return subprocess.run(["bash", "-c", script], env=env, capture_output=True, timeout=60,
                                  check=False).returncode == 0

    def test_an_alias_under_a_command_is_found_in_each_form_gh_writes(self) -> None:
        for form, config in (
                ("block", "aliases:\n    pr say: pr comment\n"),
                ("flow", "aliases: {pr say: pr comment}\n"),
                ("flow, quoted", 'aliases: {"pr say": pr comment}\n'),
                ("flow, after another key", "aliases: {prc: pr checkout, 'pr say': pr comment}\n"),
                ("flow, over lines", "aliases: {\n    prc: pr checkout,\n    pr say: pr comment\n}\n")):
            with self.subTest(form=form):
                self.assertTrue(self.has_nested_alias("pr", config))
        for form, config in (("flow, no key under pr", "aliases: {prc: pr comment --body x}\n"),
                             ("block, no key under pr", "aliases:\n    prc: pr comment --body x\n")):
            with self.subTest(control=form):
                self.assertFalse(self.has_nested_alias("pr", config))

    def test_a_line_break_other_than_a_line_feed_starts_the_check(self) -> None:
        # yaml.v3 begins a line at a carriage return, a next line, a line separator and a paragraph separator, so a key
        # after one, on the same line, starts no line the shim's pattern finds. A config.yml that holds one starts the
        # check, which refuses a call that may run an alias
        # (GhLeakTerms.test_a_line_break_other_than_a_line_feed_refuses_each_call_that_may_run_an_alias). The bytes are
        # found in the C locale and in a UTF-8 one. Control: the same config with no break and no key under `pr` does
        # not start it.
        aliases = "aliases:\n    note: |+\n        pr view 1\n{separator}    {key}: pr comment\n"
        for name, separator in (("CARRIAGE RETURN", "\r"), ("NEXT LINE", "\x85"), ("LINE SEPARATOR", "\u2028"),
                                ("PARAGRAPH SEPARATOR", "\u2029")):
            for locale in ("C", "C.UTF-8"):
                with self.subTest(separator=name, locale=locale):
                    self.assertTrue(self.has_nested_alias("pr", aliases.format(separator=separator, key="pr say"),
                                                          locale))
        with self.subTest(control="no break, no key under pr"):
            self.assertFalse(self.has_nested_alias("pr", aliases.format(separator="", key="say")))

    def test_an_alias_under_each_command_is_found_in_flow_form(self) -> None:
        # Each command for which gh_shim_may_send_text asks gh_shim_has_nested_alias, as the shim lists them.
        text = GH.read_text(encoding="utf-8")
        listed = re.search(r"\n\s+(agent \|.*?)\)\n\s+gh_shim_has_nested_alias \"\$1\"",
                           text[text.index("gh_shim_may_send_text() {"):], re.DOTALL)
        self.assertIsNotNone(listed, "the commands of gh_shim_may_send_text were not found")
        assert listed is not None
        commands = [word for word in re.split(r"[\s|\\]+", listed[1]) if word]
        self.assertIn("pr", commands)
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(self.has_nested_alias(command, f"aliases: {{prc: pr checkout, {command} say: x}}\n"))
            with self.subTest(command=command, control="the command only in a value"):
                self.assertFalse(self.has_nested_alias(command, f"aliases: {{say: {command} say}}\n"))

    def test_a_question_mark_key_starts_the_check(self) -> None:
        # gh writes an alias key after `? ` where yaml.v3 does not write it as a simple key, a key past 128 bytes or a
        # name that holds a line break, at a line's start in a block mapping and after the `{` or a `,` of a flow
        # mapping (measured 2026-11-21 with gh 2.98.0). The pattern for a key under a command finds none of them, so
        # a config.yml that holds a `? ` key starts the check. Control: a config with no `? ` key and no key under
        # `pr`, in each form, does not.
        long = "pr " + "x" * 130
        for form, config in (
                ("block, `? |`", "aliases:\n    co: pr checkout\n    ? |\n        pr say\n    : pr comment\n"),
                ("block, past 128 bytes", f"aliases:\n    co: pr checkout\n    ? {long}\n    : pr comment\n"),
                ('flow, `? "pr say\\n"`', 'aliases: {? "pr say\\n" : pr comment}\n'),
                ('flow, `? "pr say\\n"` after another key',
                 'aliases: {prc: pr checkout, ? "pr say\\n" : pr comment}\n'),
                ("flow, past 128 bytes", f"aliases: {{? {long} : pr comment}}\n")):
            with self.subTest(form=form):
                self.assertTrue(self.has_nested_alias("pr", config))
        for form, config in (("block", "aliases:\n    co: pr checkout\n    say: issue comment 1 -R o/pub\n"),
                             ("flow", "aliases: {co: pr checkout, say: issue comment 1 -R o/pub}\n")):
            with self.subTest(control=form):
                self.assertFalse(self.has_nested_alias("pr", config))


if __name__ == "__main__":
    unittest.main()
