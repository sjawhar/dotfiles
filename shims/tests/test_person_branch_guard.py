#!/usr/bin/env python3
"""shims/git and shims/jj, run from a copy of this tree with a stub git, a stub jj and a local bare
remote per case: which pushes that would create a branch named with a person token are refused, and
which pushes and commands go through."""
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_jj_lock_shim import real_binary, real_jj, run_bounded  # noqa: E402

DOTFILES = Path(__file__).resolve().parents[2]

# Each call is recorded (the tool, then each argument, tab-separated) and goes to the real git. A push
# that carries --dry-run, as the guard's read of the remote does, first sleeps STUB_SLEEP_READ seconds
# when that is set, and exits 128 when STUB_FAIL_READ=1.
STUB_GIT = r"""#!/bin/bash
{ printf git; for a in "$@"; do printf '\t%s' "$a"; done; printf '\n'; } >> "$STUB_LOG"
push=0 dry=0
for a in "$@"; do
    case "$a" in
        push) push=1 ;;
        --dry-run) dry=1 ;;
    esac
done
if ((push && dry)); then
    [[ -z "${STUB_SLEEP_READ:-}" ]] || sleep "$STUB_SLEEP_READ"
    if [[ "${STUB_FAIL_READ:-}" == 1 ]]; then
        echo "fatal: stub git: the read of the remote failed" >&2
        exit 128
    fi
fi
exec @REAL@ "$@"
"""
STUB_JJ = r"""#!/bin/bash
{ printf jj; for a in "$@"; do printf '\t%s' "$a"; done; printf '\n'; } >> "$STUB_LOG"
exec @REAL@ "$@"
"""
# The commit identity of every fixture commit, given on the commit command alone, so a case can run with
# no user.* in its git configuration.
FIXTURE_IDENTITY = ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com")
GITCONFIG = "[push]\n\tautoSetupRemote = true\n[init]\n\tdefaultBranch = main\n"
IDENTITY = "[user]\n\tname = Stub Person\n\temail = stub@example.com\n"
JJ_CONFIG = '[user]\nname = "Stub Person"\nemail = "stub@example.com"\n\n[signing]\nbehavior = "drop"\n'


def workspace_bullet() -> str:
    """The bullet of `## Workspace` in .claude/CLAUDE.md that starts "A branch name names", without its `- `."""
    section = False
    for line in (DOTFILES / ".claude" / "CLAUDE.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line == "## Workspace"
        elif section and line.startswith("- A branch name names"):
            return line.removeprefix("- ")
    raise AssertionError(f"{DOTFILES / '.claude' / 'CLAUDE.md'} has no `## Workspace` bullet that starts 'A branch name names'")


def write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


class PersonBranchGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.git_bin = real_binary("git")
        cls.jj_bin = real_jj()
        cls.bullet = workspace_bullet()
        # Three copies of shims/ and scripts/: as they are; with an empty shims/person-names; with a
        # scripts/person-branch-guard that exits 2. The shims run from a copy, never from this tree.
        cls.root = Path(tempfile.mkdtemp(prefix="person-branch-guard-"))
        try:
            for copy in ("full", "empty-list", "failing-check"):
                for part in ("shims", "scripts"):
                    shutil.copytree(DOTFILES / part, cls.root / copy / part, symlinks=True,
                                    ignore=shutil.ignore_patterns("tests", "__pycache__"))
            (cls.root / "empty-list" / "shims" / "person-names").write_text("", encoding="utf-8")
            write_executable(cls.root / "failing-check" / "scripts" / "person-branch-guard", "#!/bin/bash\nexit 2\n")
        except BaseException:
            shutil.rmtree(cls.root)
            raise

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="case-", dir=self.root))
        self.addCleanup(shutil.rmtree, self.tmp)
        for d in ("home", "tmp", "stubs"):
            (self.tmp / d).mkdir()
        self.stubs = self.tmp / "stubs"
        write_executable(self.stubs / "git", STUB_GIT.replace("@REAL@", shlex.quote(str(self.git_bin))))
        write_executable(self.stubs / "jj", STUB_JJ.replace("@REAL@", shlex.quote(str(self.jj_bin))))
        self.log = self.tmp / "stub.log"
        self.gitconfig = self.tmp / "gitconfig"
        self.gitconfig.write_text(IDENTITY + GITCONFIG, encoding="utf-8")
        self.gitconfig_noid = self.tmp / "gitconfig-noid"
        self.gitconfig_noid.write_text(GITCONFIG, encoding="utf-8")
        self.jjconfig = self.tmp / "jj.toml"
        self.jjconfig.write_text(JJ_CONFIG, encoding="utf-8")
        self.origin = self.bare("remote.git")

    # The environment, built from nothing: no marker, pin or identity of whoever runs the suite decides a case.
    def env(self, agent: bool = True, copy: str = "full", user: str | None = "stub", gitconfig: Path | None = None,
            **extra: str) -> dict[str, str]:
        env = {
            "PATH": os.pathsep.join([str(self.root / copy / "shims"), str(self.stubs), "/usr/bin", "/bin"]),
            "HOME": str(self.tmp / "home"),
            "TMPDIR": str(self.tmp / "tmp"),
            "GIT_CONFIG_GLOBAL": str(gitconfig or self.gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "JJ_CONFIG": str(self.jjconfig),
            "GIT_ALLOW_PROTOCOL": "file",
            "STUB_LOG": str(self.log),
        }
        if user is not None:
            env["USER"] = user
        if agent:
            env["AGENT_SHIM_AGENT"] = "1"
        env.update(extra)
        return env

    def fixture(self, cwd: Path, *argv: str, **env) -> str:
        """A fixture command, in a person's session and unrecorded; it must succeed."""
        result = run_bounded(list(argv), cwd, {**self.env(agent=False, **env), "STUB_LOG": os.devnull})
        self.assertEqual(result.returncode, 0, f"{argv}: {result.stderr}")
        return result.stdout

    def run_case(self, argv: list[str], cwd: Path, **env) -> subprocess.CompletedProcess[str]:
        """The command under test, with the stub log emptied first."""
        self.log.write_text("", encoding="utf-8")
        return run_bounded(argv, cwd, self.env(**env))

    def git(self, cwd: Path, *args: str, **env) -> subprocess.CompletedProcess[str]:
        return self.run_case(["git", *args], cwd, **env)

    def jj(self, cwd: Path, *args: str, **env) -> subprocess.CompletedProcess[str]:
        return self.run_case(["jj", *args], cwd, **env)

    def bare(self, name: str) -> Path:
        path = self.tmp / name
        self.fixture(self.tmp, "git", "init", "-q", "--bare", str(path))
        return path

    def git_repo(self, branch: str | None = None, **env) -> Path:
        """A git repository with one commit and `origin` at this case's bare remote, on BRANCH when given."""
        w = self.tmp / "w"
        self.fixture(self.tmp, "git", "init", "-q", str(w), **env)
        self.fixture(w, "git", *FIXTURE_IDENTITY, "commit", "-q", "--allow-empty", "-m", "one", **env)
        self.fixture(w, "git", "remote", "add", "origin", str(self.origin), **env)
        if branch:
            self.fixture(w, "git", "switch", "-q", "-c", branch, **env)
        return w

    def jj_repo(self) -> Path:
        """A jj repository whose @- holds one commit, with `origin` at this case's bare remote."""
        w = self.tmp / "w"
        self.fixture(self.tmp, "jj", "git", "init", str(w))
        self.fixture(w, "jj", "git", "remote", "add", "origin", str(self.origin))
        (w / "x").write_text("x\n", encoding="utf-8")
        self.fixture(w, "jj", "commit", "-m", "one")
        return w

    def branches(self, remote: Path | None = None) -> set[str]:
        out = self.fixture(self.tmp, "git", "--git-dir", str(remote or self.origin), "for-each-ref",
                           "--format=%(refname)", "refs/heads/")
        return {line.removeprefix("refs/heads/") for line in out.splitlines()}

    def tip(self, remote: Path, name: str) -> str:
        return self.fixture(self.tmp, "git", "--git-dir", str(remote), "rev-parse", f"refs/heads/{name}").strip()

    def calls(self, tool: str) -> list[list[str]]:
        """The recorded calls of the stub TOOL, each its argument list."""
        lines = [line.split("\t") for line in self.log.read_text(encoding="utf-8").splitlines()]
        return [line[1:] for line in lines if line[0] == tool]

    def pushes(self, tool: str) -> list[list[str]]:
        """The recorded calls of the stub TOOL that push and carry no --dry-run."""
        return [c for c in self.calls(tool) if "push" in c and "--dry-run" not in c]

    def assert_refused(self, result: subprocess.CompletedProcess[str], name: str, remote: Path | None = None) -> None:
        """Exit 1; stderr names the branch, holds the Workspace bullet of .claude/CLAUDE.md and `Nothing was
        pushed.`; the stub git ran no push without --dry-run; the remote does not hold the name."""
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"would create the branch {name} on ", result.stderr)
        self.assertIn(self.bullet, result.stderr)
        self.assertIn("Nothing was pushed.", result.stderr)
        self.assertEqual(self.pushes("git"), [])
        self.assertNotIn(name, self.branches(remote))

    def assert_no_refusal(self, result: subprocess.CompletedProcess[str]) -> None:
        for text in ("would create the branch", self.bullet, "Nothing was pushed."):
            self.assertNotIn(text, result.stderr)

    def assert_passed(self, result: subprocess.CompletedProcess[str], name: str, remote: Path | None = None) -> None:
        """Exit 0, no refusal text, the stub git ran a push without --dry-run (so the log that assert_refused
        reads records one), and the remote holds the name."""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_refusal(result)
        self.assertNotEqual(self.pushes("git"), [])
        self.assertIn(name, self.branches(remote))

    def test_the_dotfiles_gitconfig_sets_push_auto_setup_remote(self):
        out = self.fixture(self.tmp, "git", "config", "--file", str(DOTFILES / ".gitconfig"), "--type=bool", "--get",
                           "push.autoSetupRemote")
        self.assertEqual(out.strip(), "true")

    # Refused, git.

    def test_git_push_with_no_refspec_on_a_token_branch_is_refused(self):
        w = self.git_repo("stub/slug")
        self.assert_refused(self.git(w, "push"), "stub/slug")

    def test_git_push_remote_head_on_a_token_branch_is_refused(self):
        w = self.git_repo("stub/slug")
        self.assert_refused(self.git(w, "push", "origin", "HEAD"), "stub/slug")

    def test_git_push_head_to_a_full_ref_with_a_token_is_refused(self):
        w = self.git_repo()
        self.assert_refused(self.git(w, "push", "origin", "HEAD:refs/heads/sami-x"), "sami-x")

    def test_git_push_set_upstream_of_a_token_branch_is_refused(self):
        w = self.git_repo()
        self.fixture(w, "git", "branch", "sami-x")
        self.assert_refused(self.git(w, "push", "-u", "origin", "sami-x"), "sami-x")

    def test_git_push_of_a_local_branch_to_a_token_name_is_refused(self):
        w = self.git_repo()
        self.fixture(w, "git", "branch", "topic")
        self.assert_refused(self.git(w, "push", "origin", "topic:stub-y"), "stub-y")

    def test_git_push_all_with_a_new_token_branch_is_refused(self):
        w = self.git_repo()
        self.fixture(w, "git", "branch", "stub/z")
        self.assert_refused(self.git(w, "push", "origin", "--all"), "stub/z")
        self.assertEqual(self.branches(), set())

    def test_git_push_of_branches_by_a_prefix_with_a_new_token_branch_is_refused(self):
        w = self.git_repo()
        self.fixture(w, "git", "branch", "stub/m")
        self.assert_refused(self.git(w, "push", "origin", "--bra"), "stub/m")
        self.assertEqual(self.branches(), set())

    def test_git_push_of_a_full_local_ref_with_a_token_is_refused(self):
        w = self.git_repo()
        self.fixture(w, "git", "branch", "sami/full")
        self.assert_refused(self.git(w, "push", "origin", "refs/heads/sami/full"), "sami/full")

    def test_git_push_quiet_is_refused(self):
        w = self.git_repo()
        self.fixture(w, "git", "branch", "stub/q")
        self.assert_refused(self.git(w, "push", "-q", "origin", "stub/q"), "stub/q")

    def test_git_push_with_no_dry_run_is_refused_and_the_read_pushes_nothing(self):
        w = self.git_repo()
        self.fixture(w, "git", "branch", "stub/n")
        self.assert_refused(self.git(w, "push", "origin", "stub/n", "--no-dry-run"), "stub/n")

    def test_git_push_whose_dry_run_a_prefix_of_no_dry_run_clears_is_refused(self):
        w = self.git_repo()
        self.fixture(w, "git", "branch", "stub/y")
        self.assert_refused(self.git(w, "push", "--dry-run", "origin", "stub/y", "--no-dry"), "stub/y")

    def test_git_push_whose_dry_run_is_the_value_of_a_prefix_of_repo_is_refused(self):
        w = self.git_repo()
        self.assert_refused(self.git(w, "push", "--rep", "--dry-run", "origin", "HEAD:refs/heads/sami-r"), "sami-r")

    def test_git_push_after_end_of_options_is_refused(self):
        w = self.git_repo()
        self.assert_refused(self.git(w, "push", "--end-of-options", "origin", "HEAD:refs/heads/sami-e"), "sami-e")

    def test_a_token_in_another_case_is_refused(self):
        w = self.git_repo("Stub/Upper")
        self.assert_refused(self.git(w, "push", "origin", "Stub/Upper"), "Stub/Upper")

    def test_a_remote_push_glob_that_writes_a_token_name_is_refused(self):
        w = self.git_repo("fix/a")
        self.fixture(w, "git", "config", "remote.origin.push", "refs/heads/fix/*:refs/heads/sami/*")
        self.assert_refused(self.git(w, "push"), "sami/a")

    # One case per token source; the other sources are set and cannot match the name.

    def test_a_token_from_user_alone_is_refused(self):
        w = self.git_repo()
        result = self.git(w, "push", "origin", "HEAD:refs/heads/useronly/x", user="useronly")
        self.assert_refused(result, "useronly/x")
        self.assertIn('starts with "useronly" (USER)', result.stderr)

    def test_a_token_from_the_mail_alone_is_refused(self):
        w = self.git_repo()
        self.fixture(w, "git", "config", "user.email", "mailonly@example.com")
        result = self.git(w, "push", "origin", "HEAD:refs/heads/mailonly/x", user="other")
        self.assert_refused(result, "mailonly/x")
        self.assertIn('starts with "mailonly" (user.email)', result.stderr)

    def test_a_token_from_a_word_of_the_name_alone_is_refused(self):
        w = self.git_repo()
        result = self.git(w, "push", "origin", "HEAD:refs/heads/person_x")
        self.assert_refused(result, "person_x")
        self.assertIn('starts with "person" (user.name)', result.stderr)

    def test_a_token_from_the_name_list_alone_is_refused(self):
        w = self.git_repo()
        result = self.git(w, "push", "origin", "HEAD:refs/heads/sami-x")
        self.assert_refused(result, "sami-x")
        self.assertIn('starts with "sami" (shims/person-names)', result.stderr)

    # The length rule: an identity token counts from three characters.

    def test_user_of_three_characters_is_a_token_and_of_two_is_not(self):
        w = self.git_repo()
        self.assert_refused(self.git(w, "push", "origin", "HEAD:refs/heads/abc/x", user="abc"), "abc/x")
        self.assert_passed(self.git(w, "push", "origin", "HEAD:refs/heads/ab/x", user="ab"), "ab/x")

    def test_a_mail_part_of_three_characters_is_a_token_and_of_two_is_not(self):
        w = self.git_repo()
        self.fixture(w, "git", "config", "user.email", "ali@example.com")
        self.assert_refused(self.git(w, "push", "origin", "HEAD:refs/heads/ali-x"), "ali-x")
        self.fixture(w, "git", "config", "user.email", "al@example.com")
        self.assert_passed(self.git(w, "push", "origin", "HEAD:refs/heads/al-x"), "al-x")

    def test_a_name_word_of_three_characters_or_more_is_a_token_and_of_two_is_not(self):
        w = self.git_repo()
        self.fixture(w, "git", "config", "user.name", "Jo Person")
        self.assert_refused(self.git(w, "push", "origin", "HEAD:refs/heads/person-y"), "person-y")
        self.assert_passed(self.git(w, "push", "origin", "HEAD:refs/heads/jo-x"), "jo-x")

    # Refused, jj through shims/jj: shims/jj refuses before the real jj pushes.

    def assert_refused_by_jj_shim(self, result: subprocess.CompletedProcess[str], name: str) -> None:
        """assert_refused, and the stub jj ran no push without --dry-run."""
        self.assert_refused(result, name)
        self.assertEqual(self.pushes("jj"), [])

    def test_jj_push_named_with_a_token_is_refused(self):
        w = self.jj_repo()
        self.assert_refused_by_jj_shim(self.jj(w, "git", "push", "--named", "sami-x=@-"), "sami-x")

    def test_jj_push_of_a_new_untracked_token_bookmark_is_refused(self):
        w = self.jj_repo()
        self.fixture(w, "jj", "bookmark", "create", "stub/x", "-r", "@-")
        self.assert_refused_by_jj_shim(self.jj(w, "git", "push", "-b", "stub/x"), "stub/x")

    def test_jj_push_of_a_bookmark_glob_is_refused(self):
        w = self.jj_repo()
        self.fixture(w, "jj", "bookmark", "create", "stub/x", "-r", "@-")
        self.assert_refused_by_jj_shim(self.jj(w, "git", "push", "-b", "glob:*"), "stub/x")

    def test_jj_push_all_is_refused(self):
        w = self.jj_repo()
        self.fixture(w, "jj", "bookmark", "create", "stub/x", "-r", "@-")
        self.assert_refused_by_jj_shim(self.jj(w, "git", "push", "--all"), "stub/x")

    def test_jj_push_tracked_of_a_tracked_token_bookmark_is_refused(self):
        w = self.jj_repo()
        self.fixture(w, "jj", "bookmark", "create", "stub/x", "-r", "@-")
        self.fixture(w, "jj", "bookmark", "track", "stub/x@origin")
        self.assert_refused_by_jj_shim(self.jj(w, "git", "push", "--tracked"), "stub/x")

    def test_jj_push_with_no_arguments_of_an_auto_tracked_token_bookmark_is_refused(self):
        w = self.jj_repo()
        self.fixture(w, "jj", "config", "set", "--repo", "remotes.origin.auto-track-bookmarks", '"glob:*"')
        self.fixture(w, "jj", "bookmark", "create", "stub/x", "-r", "@-")
        self.assert_refused_by_jj_shim(self.jj(w, "git", "push"), "stub/x")

    # Refused by shims/git alone.

    def test_the_jj_binary_run_by_its_path_is_refused_by_shims_git(self):
        w = self.jj_repo()
        result = self.run_case([str(self.jj_bin), "git", "push", "--named", "stub-j=@-"], w)
        self.assertEqual(self.calls("jj"), [])
        self.assert_refused(result, "stub-j")
        self.assertIn("error: jj shim: Push the same revision under a name that names the work", result.stderr)

    # Passed; each with its refused twin, which differs in the one thing the case is about.

    def test_a_git_push_to_a_branch_the_remote_holds_passes(self):
        w = self.git_repo("stub/held")
        self.fixture(w, "git", "push", "-q", "origin", "stub/held")
        self.fixture(w, "git", *FIXTURE_IDENTITY, "commit", "-q", "--allow-empty", "-m", "two")
        other = self.bare("other.git")
        self.fixture(w, "git", "remote", "add", "other", str(other))
        self.assert_refused(self.git(w, "push", "other", "stub/held"), "stub/held", other)
        self.assert_passed(self.git(w, "push", "origin", "stub/held"), "stub/held")
        self.assertEqual(self.tip(self.origin, "stub/held"), self.fixture(w, "git", "rev-parse", "HEAD").strip())

    def test_a_jj_push_to_a_bookmark_the_remote_holds_passes(self):
        w = self.jj_repo()
        self.fixture(w, "jj", "bookmark", "create", "stub/held", "-r", "@-")
        self.fixture(w, "jj", "git", "push", "-b", "stub/held")
        (w / "y").write_text("y\n", encoding="utf-8")
        self.fixture(w, "jj", "commit", "-m", "two")
        self.fixture(w, "jj", "bookmark", "set", "stub/held", "-r", "@-")
        other = self.bare("other.git")
        self.fixture(w, "jj", "git", "remote", "add", "other", str(other))
        self.fixture(w, "jj", "bookmark", "track", "stub/held@other")
        self.assert_refused(self.jj(w, "git", "push", "-b", "stub/held", "--remote", "other"), "stub/held", other)
        before = self.tip(self.origin, "stub/held")
        self.assert_passed(self.jj(w, "git", "push", "-b", "stub/held"), "stub/held")
        self.assertNotEqual(self.tip(self.origin, "stub/held"), before)

    def test_a_name_with_no_token_passes(self):
        w = self.git_repo("fix/slug")
        self.assert_passed(self.git(w, "push"), "fix/slug")
        self.fixture(w, "git", "switch", "-q", "-c", "stub/slug")
        self.assert_refused(self.git(w, "push"), "stub/slug")

    def test_a_token_with_nothing_after_it_passes(self):
        w = self.git_repo("stub")
        self.assert_passed(self.git(w, "push"), "stub")
        self.fixture(w, "git", "switch", "-q", "-c", "stub-a")
        self.assert_refused(self.git(w, "push"), "stub-a")

    def test_a_token_with_no_separator_after_it_passes(self):
        w = self.git_repo("stubborn/x")
        self.assert_passed(self.git(w, "push"), "stubborn/x")
        self.fixture(w, "git", "switch", "-q", "-c", "stub-born/x")
        self.assert_refused(self.git(w, "push"), "stub-born/x")

    def test_a_git_push_in_a_persons_session_passes(self):
        w = self.git_repo("stub/slug")
        self.assert_refused(self.git(w, "push"), "stub/slug")
        self.assert_passed(self.git(w, "push", agent=False), "stub/slug")

    def test_a_jj_push_in_a_persons_session_passes(self):
        w = self.jj_repo()
        self.assert_refused(self.jj(w, "git", "push", "--named", "sami-x=@-"), "sami-x")
        self.assert_passed(self.jj(w, "git", "push", "--named", "sami-x=@-", agent=False), "sami-x")

    def test_jj_push_change_passes(self):
        w = self.jj_repo()
        self.fixture(w, "jj", "describe", "-m", "two")
        self.assert_refused(self.jj(w, "git", "push", "--named", "stub-c=@"), "stub-c")
        result = self.jj(w, "git", "push", "--change", "@")
        made = [b for b in self.branches() if b.startswith("push-")]
        self.assertEqual(len(made), 1, (self.branches(), result.stderr))
        self.assert_passed(result, made[0])

    def test_jj_push_named_with_no_token_passes(self):
        w = self.jj_repo()
        self.assert_passed(self.jj(w, "git", "push", "--named", "fix-j=@-"), "fix-j")
        self.assert_refused(self.jj(w, "git", "push", "--named", "stub-j=@-"), "stub-j")

    def test_a_git_dry_run_passes_and_creates_nothing(self):
        w = self.git_repo()
        self.fixture(w, "git", "branch", "stub/d")
        result = self.git(w, "push", "--dry-run", "origin", "stub/d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_refusal(result)
        self.assertNotIn("stub/d", self.branches())
        self.assert_refused(self.git(w, "push", "origin", "stub/d"), "stub/d")

    def test_a_git_dry_run_by_a_prefix_passes_and_creates_nothing(self):
        w = self.git_repo()
        self.fixture(w, "git", "branch", "stub/d")
        result = self.git(w, "push", "--dry", "origin", "stub/d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_refusal(result)
        self.assertNotIn("stub/d", self.branches())
        self.assert_refused(self.git(w, "push", "origin", "stub/d"), "stub/d")

    def test_a_jj_dry_run_passes_and_creates_nothing(self):
        w = self.jj_repo()
        result = self.jj(w, "git", "push", "--named", "stub-d=@-", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_refusal(result)
        self.assertNotIn("stub-d", self.branches())
        self.assert_refused(self.jj(w, "git", "push", "--named", "stub-d=@-"), "stub-d")

    def test_git_branch_is_not_a_push(self):
        w = self.git_repo()
        result = self.git(w, "branch", "stub/local")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_refusal(result)
        self.assert_refused(self.git(w, "push", "origin", "stub/local"), "stub/local")

    def test_jj_bookmark_create_is_not_a_push(self):
        w = self.jj_repo()
        result = self.jj(w, "bookmark", "create", "stub/local", "-r", "@-")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_refusal(result)
        self.assert_refused(self.jj(w, "git", "push", "-b", "stub/local"), "stub/local")

    def test_a_git_push_whose_read_of_the_remote_fails_passes(self):
        w = self.git_repo("stub/slug")
        self.assert_refused(self.git(w, "push", "origin", "HEAD"), "stub/slug")
        result = self.git(w, "push", "origin", "HEAD", STUB_FAIL_READ="1")
        self.assert_passed(result, "stub/slug")
        self.assertIn("error: git shim: git's dry run of this push failed (exit 128), so the branch-name check checked"
                      " no name; the push goes on.", result.stderr)

    def test_a_jj_push_whose_read_of_the_remote_fails_passes(self):
        w = self.jj_repo()
        self.assert_refused(self.jj(w, "git", "push", "--named", "stub-f=@-"), "stub-f")
        result = self.jj(w, "git", "push", "--named", "stub-f=@-", STUB_FAIL_READ="1")
        self.assert_passed(result, "stub-f")
        self.assertIn("error: jj shim: git's dry run of this push failed (exit 128), so the branch-name check checked"
                      " no name; the push goes on.", result.stderr)

    def test_a_read_of_the_remote_past_its_bound_lets_the_push_through(self):
        w = self.git_repo("stub/slug")
        self.assert_refused(self.git(w, "push", "origin", "HEAD", STUB_SLEEP_READ="1"), "stub/slug")
        start = time.monotonic()
        result = self.git(w, "push", "origin", "HEAD", STUB_SLEEP_READ="15")
        elapsed = time.monotonic() - start
        self.assert_passed(result, "stub/slug")
        self.assertIn("git's dry run of this push gave no answer within 10 s, so the branch-name check checked no name;"
                      " the push goes on.", result.stderr)
        self.assertGreaterEqual(elapsed, 10)
        self.assertLess(elapsed, 60)

    def test_a_remote_at_a_path_that_does_not_exist_gets_gits_own_error(self):
        w = self.git_repo("stub/slug")
        self.assert_refused(self.git(w, "push", "origin", "HEAD"), "stub/slug")
        self.fixture(w, "git", "remote", "set-url", "origin", str(self.tmp / "missing.git"))
        result = self.git(w, "push", "origin", "HEAD")
        self.assertEqual(result.returncode, 128, result.stderr)
        self.assert_no_refusal(result)
        self.assertIn("git's dry run of this push failed (exit 128), so the branch-name check checked no name; the push"
                      " goes on.", result.stderr)
        self.assertIn("does not appear to be a git repository", result.stderr)

    def test_an_https_remote_gets_gits_own_error_and_no_network(self):
        w = self.git_repo("stub/slug")
        self.assert_refused(self.git(w, "push", "origin", "HEAD"), "stub/slug")
        self.fixture(w, "git", "remote", "set-url", "origin", "https://example.invalid/remote.git")
        result = self.git(w, "push", "origin", "HEAD")
        self.assertEqual(result.returncode, 128, result.stderr)
        self.assert_no_refusal(result)
        self.assertIn("transport 'https' not allowed", result.stderr)

    def test_with_no_identity_and_an_empty_name_list_nothing_is_refused(self):
        w = self.git_repo(gitconfig=self.gitconfig_noid, user=None)
        for name in ("stub/x", "sami/x"):
            self.assert_refused(self.git(w, "push", "origin", f"HEAD:refs/heads/{name}"), name)
        for name in ("stub/x", "sami/x", "_x"):
            with self.subTest(name=name):
                result = self.git(w, "push", "origin", f"HEAD:refs/heads/{name}", copy="empty-list", user=None,
                                  gitconfig=self.gitconfig_noid)
                self.assert_passed(result, name)

    def test_a_branch_name_check_that_fails_lets_the_push_through(self):
        w = self.git_repo("stub/slug")
        self.assert_refused(self.git(w, "push", "origin", "HEAD"), "stub/slug")
        result = self.git(w, "push", "origin", "HEAD", copy="failing-check")
        self.assert_passed(result, "stub/slug")
        self.assertIn("error: git shim: the branch-name check failed (exit 2); the push goes on.", result.stderr)

    # A command that is not a push, and a push git fails by itself, get git's own output: no line says the
    # remote could not be asked.

    def test_git_push_help_on_a_token_branch_is_not_a_push(self):
        w = self.git_repo("stub/slug")
        person = self.git(w, "push", "-h", agent=False)
        result = self.git(w, "push", "-h")
        self.assertEqual(result.returncode, 129, result.stderr)
        self.assertEqual((result.stdout, result.stderr), (person.stdout, person.stderr))
        self.assert_refused(self.git(w, "push"), "stub/slug")

    def test_git_push_help_in_a_short_option_cluster_is_not_a_push(self):
        w = self.git_repo("stub/slug")
        for cluster in ("-uh", "-hv"):
            with self.subTest(cluster=cluster):
                person = self.git(w, "push", cluster, agent=False)
                result = self.git(w, "push", cluster)
                self.assertEqual(result.returncode, 129, result.stderr)
                self.assertEqual((result.stdout, result.stderr), (person.stdout, person.stderr))
        self.assert_refused(self.git(w, "push"), "stub/slug")

    def test_a_push_git_fails_by_itself_is_not_called_a_remote_that_could_not_be_asked(self):
        w = self.git_repo()
        self.fixture(w, "git", "branch", "sami-x")
        self.assert_refused(self.git(w, "push", "origin", "sami-x"), "sami-x")
        result = self.git(w, "push", "origin", "sami-typo")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assert_no_refusal(result)
        self.assertIn("error: src refspec sami-typo does not match any", result.stderr)
        self.assertNotIn("could not ask origin which branches it holds", result.stderr)


if __name__ == "__main__":
    unittest.main()
