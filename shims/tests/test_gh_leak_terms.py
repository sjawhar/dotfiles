#!/usr/bin/env python3
"""shims/gh refuses a gh call that would send a listed leak term to a public repository.

The leak-term check (scripts/leak-terms-check) already refuses such a term in a commit and in a push to a public
repository. Text sent through gh passed no check: on 2026-11-17 an agent's `gh pr edit` put a term of the private list
into a public pull request's body, and GitHub keeps that text in the body's edit history (dispatch://INFRA-4102).

Here a fake gh, first on PATH, records each call it gets (its arguments, its stdin and what kind of file that stdin
is) and answers the visibility reads of scripts/github-repo-visibility from a directory of answers. The term list is
the encrypted test list of leak_terms_fixture.py, so the canary below is a listed term (term 3 of that list) and no
private term appears anywhere. Each refusal is shown with its control: the same text to a private repository goes out
unchanged, with the same stdin."""
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
CANARY_INDEX = 3
# A term the list holds for cybermonklabs/* repositories only (leak_terms_fixture.SCOPED_TEST_TERMS).
SCOPE, (SCOPED,) = next(iter(SCOPED_TEST_TERMS.items()))
# The canary as a body would carry it: another letter case, in a sentence.
TEXT = f"See {CANARY.upper()} for the details.\n"
CLEAN = "Reviewed at abc123; nothing to add.\n"
PUBLIC = "o/pub"
PRIVATE = "o/priv"
UNREAD = "o/unread"
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
            "PATH": os.pathsep.join([str(self.bin), os.environ["PATH"]]),
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

    def assert_hidden(self, result: subprocess.CompletedProcess[bytes]) -> None:
        """No output of the call holds either canary, in any letter case."""
        for stream in (result.stdout, result.stderr):
            for canary in (CANARY, SCOPED, *REGISTRY_TEST_TERMS["oh-my-pi"]):
                self.assertNotIn(canary.casefold(), stream.decode("utf-8", "replace").casefold())

    def assert_refused(self, result: subprocess.CompletedProcess[bytes], field: str) -> None:
        stderr = result.stderr.decode("utf-8", "replace")
        self.assertEqual(result.returncode, 2, stderr)
        self.assertEqual(self.gh_calls(), [], "gh ran although the text holds a listed term")
        self.assertRegex(stderr, rf"leak terms: gh [^\n]*: {re.escape(field)} line \d+: listed leak term {CANARY_INDEX}\n")
        self.assertIn("nothing was sent", stderr)
        self.assert_hidden(result)

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
             b"", "--input d.json (escapes decoded)"),
            ("api --input -", ["api", f"/repos/{repo}/pulls/11/reviews", "--input", "-"],
             json.dumps({"event": "COMMENT", "body": text}).encode(), "--input - (standard input)"),
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
        self.assert_refused(self.run_gh(["api", "graphql", "-f", f"query={mutation}"]), "--raw-field query")
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
        stderr = refused.stderr.decode("utf-8", "replace")
        self.assertEqual(refused.returncode, 2, stderr)
        self.assertEqual(self.gh_calls(), [])
        self.assertIn(f"--body line 1: listed leak term 1 of {SCOPE}\n", stderr)
        self.assert_hidden(refused)
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
        self.assertEqual(unnamed.returncode, 2, unnamed.stderr.decode())
        self.assertIn(f"listed leak term 1 of {SCOPE}", unnamed.stderr.decode())
        self.assertEqual(self.gh_calls(), [])

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
                self.assertEqual(refused.returncode, 2, refused.stderr.decode())
                self.assertIn(f"listed leak term 1 of {SCOPE}", refused.stderr.decode())
                self.assert_hidden(refused)
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
                    refused = self.run_gh(argv)
                    stderr = refused.stderr.decode("utf-8", "replace")
                    self.assertEqual(refused.returncode, 2, stderr)
                    self.assertEqual(self.gh_calls(), [])
                    self.assertIn(f"line 1: listed leak term {number} of knives oh-my-pi\n", stderr)
                    self.assert_hidden(refused)
            # Controls: a public repository of another name, and a private one named oh-my-pi, get the same body.
            for repo in (PUBLIC, "o/oh-my-pi"):
                with self.subTest(term=number, repo=repo):
                    self.setUp_calls()
                    argv = ["pr", "edit", "12", "-R", repo, "--body", body]
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
        # Control: when the file holds the term, the refusal names the file with the term masked.
        (self.work / path).write_text(TEXT, encoding="utf-8")
        self.setUp_calls()
        refused = self.run_gh(argv)
        self.assert_refused(refused, "--body-file notes/<listed term>.md")

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
    """The check reads gh's arguments as gh does, from a table of each command's options."""

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
        for (group, sub), table in module.GH_TEXT_COMMANDS.items():
            with self.subTest(command=f"{group} {sub}"):
                text = subprocess.run([gh, group, *([sub] if sub else []), "--help"], capture_output=True, text=True,
                                      check=True, timeout=60).stdout
                values, flags, section = set(), set(), ""
                for help_line in text.splitlines():
                    if help_line and not help_line.startswith(" "):
                        section = help_line.strip()
                    elif section in ("FLAGS", "INHERITED FLAGS") and (m := line.match(help_line)):
                        (values if m.group(3) else flags).update(n for n in m.group(1, 2) if n)
                options = module.gh_options(table)
                self.assertEqual({name for name, takes in options.items() if takes}, values)
                self.assertEqual({name for name, takes in options.items() if not takes}, flags)

    def test_the_shim_hands_every_text_command_to_the_check(self) -> None:
        text = GH.read_text(encoding="utf-8")
        functions = ""
        for name in ("gh_shim_is_write", "gh_shim_may_send_text"):
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
