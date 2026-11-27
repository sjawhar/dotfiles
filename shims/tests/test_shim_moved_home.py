#!/usr/bin/env python3
"""A shim finds its real binary whatever HOME and PATH its caller set.

A Legion test pane starts Oh My Pi with exactly two variables: HOME, a temporary directory, and
PATH, `<bin>:/usr/local/bin:/usr/bin:/bin`, where `<bin>/jj` is a link to the jj the test process
found, which on a dotfiles machine is `shims/jj`. Until 2026-11-17 the shim looked past PATH only
at `$HOME/.dotfiles/bin/jj` and `$HOME/.mise/shims/jj`, both under the temporary HOME, so every jj
in such a pane exited 127 with `jj shim: no real jj found on PATH`, and ten pi-legion rows that use
the real binary failed on sami-devbox while CI passed them.

Each case below runs a shim the way that pane does: the environment holds only what the case names,
and the command runs in a cgroup of its own under a task limit and a timeout (`LeakTestCase.launch`),
so a lookup that finds the shim again fails the case instead of forking without end. The machine
cases need this machine's own install of the pinned tool, under the uid's passwd home, and are
skipped without it; the fixture cases build a dotfiles checkout and a mise data dir of their own.

So a shim takes the dotfiles checkout from DOTFILES_DIR, else from its own path (`readlink -f`),
and mise, its configuration and its data dirs from its copy of mise_paths, never through HOME or
the path it was run by. State that the caller's HOME owns (caches, the gh agent config, tmux
sockets) stays under HOME. A shim that adds a lookup adds a case here.
"""
from __future__ import annotations

import os
import pwd
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from leak_terms_fixture import fixture_environment
from test_leak_terms import LeakTestCase

ROOT = Path(__file__).resolve().parents[2]
SHIMS = ROOT / "shims"
PASSWD_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"
# A tools-section pin, as the shims read one: an exact version, quoted, the key quoted or bare.
PIN = re.compile(r'^\s*"?(?P<key>[^"=\s]+)"?\s*=\s*"(?P<pin>[0-9][0-9A-Za-z.+-]*)"')
SECTION = re.compile(r"^\s*\[([^]]*)\]")


def pinned_version(tool: str, config: Path = ROOT / "mise.toml") -> str | None:
    section = ""
    for line in config.read_text(encoding="utf-8").splitlines():
        if (header := SECTION.match(line)) is not None:
            section = header.group(1)
        elif section == "tools" and (pin := PIN.match(line)) is not None and pin.group("key") == tool:
            return pin.group("pin")
    return None


def machine_install(tool: str, binary: str) -> Path:
    """BINARY in this machine's install of TOOL at the version this checkout's mise.toml pins, in the
    passwd home's mise data dir, where .bashrc keeps it; the case is skipped without one."""
    pin = pinned_version(tool)
    if pin is None:
        raise unittest.SkipTest(f"mise.toml pins no exact version of {tool}")
    install = PASSWD_HOME / ".mise" / "installs" / re.sub(r"[:/]", "-", tool).replace("@", "") / pin
    for candidate in (install / binary, install / "bin" / binary, *install.glob(f"*/bin/{binary}")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise unittest.SkipTest(f"{tool} {pin} is not installed under {install}")


class MovedHome(LeakTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()

    def pane(self, shim: Path, name: str, *, path: str | None = None, **extra: str) -> dict[str, str]:
        """A Legion test pane's environment: `<bin>/NAME` a link to SHIM, HOME a temporary directory, PATH
        `<bin>:/usr/local/bin:/usr/bin:/bin` unless PATH is given, and nothing else but EXTRA."""
        (self.bin / name).symlink_to(shim)
        return {"HOME": str(self.home), "PATH": path or f"{self.bin}:{SYSTEM_PATH}", **extra}


class JjInATestPane(MovedHome):
    """shims/jj under a test pane's HOME and PATH runs the jj mise.toml pins."""

    def setUp(self) -> None:
        super().setUp()
        self.real = machine_install("github:sjawhar/jj", "jj")
        self.version = subprocess.run([self.real, "--version"], capture_output=True, text=True, check=True).stdout

    def run_jj(self, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
        return self.launch(["jj", *args], cwd=self.root, env=env)

    def test_a_link_in_the_panes_bin_runs_the_pinned_jj(self) -> None:
        result = self.run_jj(self.pane(SHIMS / "jj", "jj"), "--version")
        self.assertEqual((result.returncode, result.stdout), (0, self.version), result.stderr)

    def test_the_shims_dir_itself_on_path_runs_the_pinned_jj(self) -> None:
        env = {"HOME": str(self.home), "PATH": f"{SHIMS}:{SYSTEM_PATH}"}
        result = self.run_jj(env, "--version")
        self.assertEqual((result.returncode, result.stdout), (0, self.version), result.stderr)

    def test_mises_own_shim_on_path_is_passed_for_the_pinned_jj(self) -> None:
        # mise's shim reads mise's config and its trust list under HOME: under a temporary one it
        # refuses ("Config files in ~/.config/mise/config.toml are not trusted") or hands the call back.
        mise_shims = PASSWD_HOME / ".mise" / "shims"
        if not (mise_shims / "jj").exists():
            self.skipTest(f"no jj in {mise_shims}")
        env = self.pane(SHIMS / "jj", "jj", path=f"{self.bin}:{mise_shims}:{SYSTEM_PATH}")
        result = self.run_jj(env, "--version")
        self.assertEqual((result.returncode, result.stdout), (0, self.version), result.stderr)

    def test_the_pinned_jj_does_real_work_in_the_pane(self) -> None:
        env = self.pane(SHIMS / "jj", "jj", JJ_USER="Moved Home", JJ_EMAIL="moved-home@example.invalid")
        repo = self.root / "repo"
        init = self.run_jj(env, "git", "init", str(repo))
        self.assertEqual(init.returncode, 0, init.stderr)
        log = self.launch(["jj", "log", "--no-graph", "-r", "@", "-T", "author.email()"], cwd=repo, env=env)
        self.assertEqual((log.returncode, log.stdout), (0, "moved-home@example.invalid"), log.stderr)

    def test_a_commit_through_the_link_runs_the_leak_term_check(self) -> None:
        # The shim finds scripts/leak-terms-check beside itself, not beside the pane's link to it, and the scanner
        # finds the pinned sops in mise's data dirs, since the pane's PATH holds none: until 2026-11-17 every commit
        # through such a link was refused ("cannot read the diff for the leak-term check").
        env = self.pane(SHIMS / "jj", "jj", JJ_USER="Moved Home", JJ_EMAIL="moved-home@example.invalid",
                        **fixture_environment())
        repo = self.root / "repo"
        self.assertEqual(self.run_jj(env, "git", "init", str(repo)).returncode, 0)
        (repo / "clean.txt").write_text("clean\n", encoding="utf-8")
        clean = self.launch(["jj", "commit", "-m", "a clean commit"], cwd=repo, env=env)
        self.assertEqual(clean.returncode, 0, clean.stderr)
        (repo / "leak.txt").write_text("zorbleplex\n", encoding="utf-8")
        leak = self.launch(["jj", "commit", "-m", "a commit with a term"], cwd=repo, env=env)
        self.assertNotEqual(leak.returncode, 0, leak.stderr)
        self.assertIn('leak terms: leak.txt:1: "zorbleplex" (the global list), in: zorbleplex\n', leak.stderr)
        # A jj template's single-quoted string has no escapes; a double-quoted one reads "\n" as a newline.
        log = self.launch(["jj", "log", "--no-graph", "-r", "::@", "-T", 'description.first_line() ++ "\\n"'], cwd=repo, env=env)
        self.assertEqual(log.stdout.split("\n").count("a clean commit"), 1, log.stdout)
        self.assertNotIn("a commit with a term", log.stdout)


class GitInATestPane(MovedHome):
    """shims/git reached by a link in a pane's bin runs its leak-term hooks from beside itself: until 2026-11-17
    it gave git `core.hooksPath=<bin>/git-hooks`, which does not exist, so the commit ran with no scan."""

    def test_a_commit_with_a_term_is_refused(self) -> None:
        env = self.pane(SHIMS / "git", "git", GIT_AUTHOR_NAME="Moved Home", GIT_AUTHOR_EMAIL="moved-home@example.invalid",
                        GIT_COMMITTER_NAME="Moved Home", GIT_COMMITTER_EMAIL="moved-home@example.invalid",
                        GIT_CONFIG_NOSYSTEM="1", **fixture_environment())
        repo = self.root / "repo"
        repo.mkdir()
        self.assertEqual(self.launch(["git", "init", "-q"], cwd=repo, env=env).returncode, 0)
        # A remote, so the repository is no throwaway one (scripts/throwaway-repository.sh), whose commit runs without
        # the leak-term hooks.
        self.assertEqual(self.launch(["git", "remote", "add", "origin", "https://example.invalid/moved-home.git"], cwd=repo,
                                     env=env).returncode, 0)
        (repo / "clean.txt").write_text("clean\n", encoding="utf-8")
        self.launch(["git", "add", "clean.txt"], cwd=repo, env=env)
        clean = self.launch(["git", "commit", "-q", "-m", "a clean commit"], cwd=repo, env=env)
        self.assertEqual(clean.returncode, 0, clean.stderr)
        (repo / "leak.txt").write_text("zorbleplex\n", encoding="utf-8")
        self.launch(["git", "add", "leak.txt"], cwd=repo, env=env)
        leak = self.launch(["git", "commit", "-q", "-m", "a commit with a term"], cwd=repo, env=env)
        self.assertNotEqual(leak.returncode, 0, leak.stderr)
        self.assertIn('leak terms: leak.txt:1: "zorbleplex" (the global list), in: zorbleplex\n', leak.stderr)
        log = self.launch(["git", "log", "--format=%s"], cwd=repo, env=env)
        self.assertEqual(log.stdout, "a clean commit\n")


class GhInATestPane(MovedHome):
    """shims/gh in an agent session under a moved HOME runs its agent route from the checkout it is in: until
    2026-11-17 it looked for scripts/gh-agent-config under $HOME/.dotfiles and gave no answer at all."""

    def test_auth_status_names_the_placeholder_login_of_the_agent_config(self) -> None:
        # The real gh reads the agent config directory, whose only login is the placeholder (scripts/gh-agent-config).
        # GitHub refuses that placeholder, so the exit is not 0; a gh that never got the directory names no login.
        result = self.launch(["gh", "auth", "status"], cwd=self.root, env=self.pane(SHIMS / "gh", "gh", LEGION_ROLE="worker"))
        self.assertIn("no-user-login-in-agent-sessions", f"{result.stdout}{result.stderr}")



class JjFixtureCheckout(MovedHome):
    """shims/jj in a dotfiles checkout of the test's own: the pin comes from that checkout's mise.toml,
    found from the shim's own path, never from $HOME/.dotfiles."""

    def setUp(self) -> None:
        super().setUp()
        self.dotfiles = self.root / "dotfiles"
        (self.dotfiles / "shims").mkdir(parents=True)
        shutil.copy2(SHIMS / "jj", self.dotfiles / "shims" / "jj")
        self.data = self.root / "mise-data"
        self.data.mkdir()

    def pin(self, version: str) -> None:
        (self.dotfiles / "mise.toml").write_text(f'[tools]\n"github:sjawhar/jj" = "{version}"  # test pin\n', encoding="utf-8")

    def install(self, version: str) -> Path:
        stub = self.data / "installs" / "github-sjawhar-jj" / version / "jj"
        stub.parent.mkdir(parents=True)
        stub.write_text('#!/bin/sh\necho "STUB jj $*"\n', encoding="utf-8")
        stub.chmod(0o755)
        return stub

    def test_the_checkouts_own_pin_runs_from_the_data_dir(self) -> None:
        self.pin("9.9.9-test")
        self.install("9.9.9-test")
        env = self.pane(self.dotfiles / "shims" / "jj", "jj", MISE_DATA_DIR=str(self.data))
        result = self.launch(["jj", "--version"], cwd=self.root, env=env)
        self.assertEqual((result.returncode, result.stdout), (0, "STUB jj --version\n"), result.stderr)

    def test_no_installed_pin_is_refused_in_one_line_naming_what_was_searched(self) -> None:
        self.pin("9.9.9-test")
        env = self.pane(self.dotfiles / "shims" / "jj", "jj", MISE_DATA_DIR=str(self.data))
        result = self.launch(["jj", "--version"], cwd=self.root, env=env)
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertEqual(result.stdout, "")
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), 1, result.stderr)
        self.assertIn("jj shim: no real jj found on PATH", lines[0])
        self.assertIn(env["PATH"], lines[0])
        self.assertIn(str(self.dotfiles / "mise.toml"), lines[0])
        self.assertIn(str(self.data / "installs" / "github-sjawhar-jj" / "9.9.9-test"), lines[0])
        self.assertIn("mise install", lines[0])

    def test_no_pin_is_refused_in_one_line_naming_the_config(self) -> None:
        (self.dotfiles / "mise.toml").write_text('[tools]\nbat = "0.26.1"\n', encoding="utf-8")
        env = self.pane(self.dotfiles / "shims" / "jj", "jj", MISE_DATA_DIR=str(self.data))
        result = self.launch(["jj", "--version"], cwd=self.root, env=env)
        self.assertEqual(result.returncode, 127, result.stderr)
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), 1, result.stderr)
        self.assertIn("jj shim: no real jj found on PATH", lines[0])
        self.assertIn(f"no exact version of github:sjawhar/jj pinned in {self.dotfiles / 'mise.toml'}", lines[0])


class CheckoutWithoutMise(MovedHome):
    """A dotfiles checkout other than the installed one, a copy or a worktree, has no bin/mise, since bin/ is not
    committed: a shim there runs the mise that the shims in mise's data dir link to. Until 2026-11-17 it ran
    <checkout>/bin/mise, so in a clean clone every case of SiblingShimsInATestPane that asks mise exited 127."""

    def test_a_shim_runs_the_mise_a_data_dirs_shims_link_to(self) -> None:
        if shutil.which("mise", path=SYSTEM_PATH):
            self.skipTest(f"a mise on {SYSTEM_PATH} answers before the checkout's")
        checkout = self.root / "dotfiles"
        (checkout / "shims").mkdir(parents=True)
        shutil.copy2(SHIMS / "pyright", checkout / "shims" / "pyright")
        stub = self.root / "machine" / "mise"
        stub.parent.mkdir()
        stub.write_text('#!/bin/sh\necho "STUB mise $*"\n', encoding="utf-8")
        stub.chmod(0o755)
        data = self.root / "mise-data"
        (data / "shims").mkdir(parents=True)
        (data / "shims" / "basedpyright").symlink_to(stub)
        env = self.pane(checkout / "shims" / "pyright", "pyright", MISE_DATA_DIR=str(data))
        result = self.launch(["pyright", "--version"], cwd=self.root, env=env)
        self.assertEqual((result.returncode, result.stdout), (0, "STUB mise exec npm:basedpyright -- basedpyright --version\n"),
                         result.stderr)


def mise_which(binary: str) -> Path:
    """BINARY as this machine's own mise resolves its pin, under this run's HOME; the case is skipped
    without one."""
    mise = shutil.which("mise") or str(PASSWD_HOME / ".dotfiles" / "bin" / "mise")
    found = subprocess.run([mise, "which", "-C", "/", binary], capture_output=True, text=True, check=False)
    if found.returncode != 0 or not found.stdout.strip():
        raise unittest.SkipTest(f"mise resolves no {binary} on this machine: {found.stderr.strip()}")
    return Path(found.stdout.strip())


def sdk_binary(binary: str) -> Path:
    """BINARY in the Cloud SDK's documented install location under the passwd home."""
    path = PASSWD_HOME / ".gcloud" / "google-cloud-sdk" / "bin" / binary
    if not os.access(path, os.X_OK):
        raise unittest.SkipTest(f"no Cloud SDK {binary} at {path}")
    return path


class SiblingShimsInATestPane(MovedHome):
    """Every other shim whose real binary was found through HOME, or past PATH as shims/jj's is, finds
    it under a test pane's HOME and PATH: the first line it prints is the real binary's own, run
    directly in the same environment."""

    def first_line(self, argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
        result = self.launch(argv, cwd=self.root, env=env)
        return result.returncode, (result.stdout.splitlines() or [""])[0], result.stderr

    def assert_runs_the_real(self, shim: str, real: Path, *args: str, path: str | None = None) -> None:
        env = self.pane(SHIMS / shim, shim, path=path)
        want = self.first_line([str(real), *args], {"HOME": str(self.home), "PATH": SYSTEM_PATH})
        self.assertEqual(want[0], 0, f"{real} itself failed: {want[2]}")
        got = self.first_line([shim, *args], env)
        self.assertEqual(got[:2], want[:2], got[2])

    def test_gh(self) -> None:
        self.assert_runs_the_real("gh", mise_which("gh"), "--version")

    def test_knives(self) -> None:
        self.assert_runs_the_real("knives", mise_which("knives"), "--version")

    def test_az(self) -> None:
        self.assert_runs_the_real("az", mise_which("az"), "--version")

    def test_ctx(self) -> None:
        self.assert_runs_the_real("ctx", mise_which("ctx"), "--version")

    def test_pulumi(self) -> None:
        self.assert_runs_the_real("pulumi", mise_which("pulumi"), "version")

    def test_secrets(self) -> None:
        self.assert_runs_the_real("secrets", mise_which("secrets"), "--help")

    def test_bq(self) -> None:
        self.assert_runs_the_real("bq", sdk_binary("bq"), "version")

    def test_gcloud(self) -> None:
        self.assert_runs_the_real("gcloud", sdk_binary("gcloud"), "--version")

    def test_tmux_past_a_path_with_no_tmux_on_it(self) -> None:
        # The pane's PATH holds the system tmux; this one holds only bash, for the shim's own
        # `#!/usr/bin/env bash`, so the tmux that answers is the pinned one past PATH.
        bash_only = self.root / "bash-only"
        bash_only.mkdir()
        (bash_only / "bash").symlink_to("/bin/bash")
        self.assert_runs_the_real("tmux", mise_which("tmux"), "-V", path=f"{self.bin}:{bash_only}")

    def test_pyright_and_basedpyright(self) -> None:
        for shim in ("pyright", "basedpyright"):
            with self.subTest(shim=shim):
                code, line, stderr = self.first_line([shim, "--version"], self.pane(SHIMS / shim, shim))
                self.assertEqual(code, 0, stderr)
                self.assertRegex(line, r"^basedpyright \d")
                (self.bin / shim).unlink()

    def test_omp(self) -> None:
        # shims/omp execs scripts/agent-session from the checkout it is in, which runs the omp mise.toml pins;
        # OMP_MEMORY_SCOPE=off keeps the launch out of a systemd scope of its own.
        real = mise_which("omp")
        want = self.first_line([str(real), "--version"], {"HOME": str(self.home), "PATH": SYSTEM_PATH})
        env = {**self.pane(SHIMS / "omp", "omp"), "OMP_MEMORY_SCOPE": "off"}
        got = self.first_line(["omp", "--version"], env)
        self.assertEqual(got[:2], want[:2], got[2])

    def test_codex_is_resolved(self) -> None:
        # codex is an npm package: its own launcher then needs node, which the pane's PATH lacks and
        # the shim passes on unchanged, so only the lookup is checked.
        mise_which("codex")
        result = self.launch(["codex", "--version"], cwd=self.root, env=self.pane(SHIMS / "claude", "codex"))
        self.assertNotIn("no codex on PATH past this shim", result.stderr)


def function_source(shim: str, name: str) -> str | None:
    text = (SHIMS / shim).read_text(encoding="utf-8")
    start = text.find(f"\n{name}() {{\n")
    if start < 0:
        return None
    return text[start + 1 : text.index("\n}\n", start) + 3]


class SharedFunctionCopies(unittest.TestCase):
    """shims/ has no sourced library, so these functions are copied into each shim that needs them
    and kept identical by hand, as agent_context is (test_shim_agent_context.py)."""

    HOLDERS = {
        "mise_paths": ("jj", "tmux", "gh", "knives", "az", "claude", "ctx", "pyright", "basedpyright", "secrets", "pulumi", "omp"),
        "is_mise_shim": ("jj", "tmux", "gh", "az", "claude", "secrets", "pulumi"),
        "mise_pinned": ("jj", "tmux", "gh"),
        "find_leak_check": ("git", "jj"),
    }

    def test_every_copy_is_identical_and_held_where_listed(self) -> None:
        shims = sorted(p.name for p in SHIMS.iterdir() if p.is_file() and not p.is_symlink() and os.access(p, os.X_OK))
        for name, holders in self.HOLDERS.items():
            with self.subTest(function=name):
                copies = {shim: function_source(shim, name) for shim in shims}
                self.assertEqual(sorted(s for s, body in copies.items() if body is not None), sorted(holders))
                first = copies[holders[0]]
                for shim in holders[1:]:
                    self.assertEqual(copies[shim], first, f"shims/{shim}'s {name} has drifted")


if __name__ == "__main__":
    unittest.main()
