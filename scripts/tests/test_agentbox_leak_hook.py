#!/usr/bin/env python3
"""A box clone, and a clone a move makes on its target, get the leak check's pre-push hook when their
origin is GUARD (dispatch://BET-706 R2/A2): a GitHub repository that is public or whose visibility
cannot be read. A private one does not. Whether the source checkout has the hook plays no part.

`agentbox new` copies a repo's template (refresh_template, clone_checkout); a template made before
this rule, without the hook, gets it at the next launch. `agentbox move --to` clones a repository the
target lacks there (move_to_clone_missing).

Technique, as test_agentbox_move.py: source scripts/agentbox (its source guard runs nothing). A git
stub sends `clone` and `fetch` of https://github.com/x/<name> to a local bare repository, so nothing
reaches GitHub, while every other git command, `remote get-url` among them, sees the GitHub URL. The
visibility comes from the leak check's cache in the test's HOME or from a fake gh, first on PATH, that
scripts/github-repo-visibility reaches through shims/gh; the installer is a stub in the per-machine
hooks directory that writes the wrapper's marker. Each hook is read where git runs it, through
`git rev-parse --git-path hooks/pre-push`.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"


def write_stub(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class AgentboxLeakHook(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.src = self.root / "src"
        self.src.mkdir()
        self.bare = self.root / "github"
        self.templates = self.root / "templates"
        self.stubs = self.root / "bin"
        self.gh_log = self.root / "gh.log"
        self.installs = self.root / "installs.log"
        write_stub(self.stubs, "git", "\n".join([
            'for a in "$@"; do',
            f'    case "$a" in clone | fetch) exec /usr/bin/git -c "url.{self.bare}/.insteadOf=https://github.com/" "$@" ;; esac',
            'done',
            'exec /usr/bin/git "$@"',
        ]))
        # No answer: a repository without a cache entry is of unknown visibility.
        write_stub(self.stubs, "gh", f'printf "%s\\n" "$*" >> "{self.gh_log}"\nexit 1')
        # As install-prepush-hook.sh does it: at git's effective hooks path, an existing hook that is not the wrapper
        # kept as pre-push.local (and a refusal when pre-push.local is there already), then the wrapper, which here
        # refuses every push, so a test can push to see whether git runs it.
        self.installer = self.home / ".local/share/public-leak-check-hooks/install-prepush-hook.sh"
        write_stub(self.installer.parent, self.installer.name, "\n".join([
            f'printf "%s\\n" "$1" >> "{self.installs}"',
            'd="$(git -C "$1" rev-parse --path-format=absolute --git-path hooks)" || exit 1',
            'mkdir -p "$d"',
            'if [ -e "$d/pre-push" ] && ! grep -q PUBLIC-LEAK-CHECK-WRAPPER "$d/pre-push"; then',
            '    [ -e "$d/pre-push.local" ] && { echo "SKIP $1: $d has pre-push AND pre-push.local"; exit 1; }',
            '    mv "$d/pre-push" "$d/pre-push.local"',
            'fi',
            'printf "#!/bin/sh\\n# PUBLIC-LEAK-CHECK-WRAPPER\\necho refused by the stand-in wrapper >&2\\nexit 1\\n" > "$d/pre-push" && chmod +x "$d/pre-push"',
        ]))
        self.cache = self.home / ".cache" / "public-leak-check"
        self.cache.mkdir(parents=True)
        self.env = {
            "AGENTBOX_IDENTITY_FILE": str(self.root / "not-a-box"),
            "AGENTBOX_BOX": "",
            "HOME": str(self.home),
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "DOTFILES_DIR": str(DOTFILES),
            "AGENTBOX_SRC": str(self.src),
            "AGENTBOX_CLONE_TEMPLATES": str(self.templates),
            "AGENTBOX_BOXES": str(self.root / "boxes"),
            "AGENTBOX_DISK": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "KNIVES_GH_BYPASS": "1",
            "GH_READ_CACHE": "0",
            "GH_READ_CACHE_LOG": "0",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ── fixtures ────────────────────────────────────────────────────────────────────────────
    def repo(self, name: str, visibility: str | None, files: dict[str, str] | None = None,
             links: dict[str, str] | None = None) -> Path:
        """A GitHub repository x/NAME (a local bare one behind the git stub) with one commit holding FILES (by
        default one file f; one starting `#!` is executable, as a hook git runs is) and the symlinks LINKS (path: link
        text), the leak check's fresh cache entry VISIBILITY for it (none when None), and its source checkout
        ~/src/NAME, which has no pre-push hook."""
        bare = self.bare / "x" / name
        work = self.root / "seed" / name
        subprocess.run(["/usr/bin/git", "init", "-q", "-b", "main", str(work)], check=True)
        for path, text in (files or {"f": "x\n"}).items():
            (work / path).parent.mkdir(parents=True, exist_ok=True)
            (work / path).write_text(text, encoding="utf-8")
            if text.startswith("#!"):
                (work / path).chmod(0o755)
        for path, target in (links or {}).items():
            (work / path).parent.mkdir(parents=True, exist_ok=True)
            (work / path).symlink_to(target)
        for args in (["add", "-A"], ["-c", "user.name=T", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "commit", "-qm", "init"]):
            subprocess.run(["/usr/bin/git", "-C", str(work), *args], check=True)
        subprocess.run(["/usr/bin/git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
        source = self.src / name
        subprocess.run(["/usr/bin/git", "clone", "-q", str(bare), str(source)], check=True)
        subprocess.run(["/usr/bin/git", "-C", str(source), "remote", "set-url", "origin", f"https://github.com/x/{name}"], check=True)
        self.assertFalse(self.hooked(source))
        if visibility:
            (self.cache / f"x_{name}").write_text(visibility, encoding="utf-8")
        return source

    def run_bash(self, script: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", "-c", f'source "{AGENTBOX}"\n{script}'], capture_output=True, text=True,
                              env={**self.env, **extra}, check=False, timeout=120)

    def launch(self, name: str) -> subprocess.CompletedProcess[str]:
        """clone_checkout, as `agentbox new` runs it for one repo: the template, refreshed, copied into a box clone."""
        return self.run_bash(f'clone_checkout {name} "{self.root}/boxes/b/{name}"')

    def hooked(self, checkout: Path) -> bool:
        """True when the pre-push hook git runs in CHECKOUT is the wrapper and git can run it (executable). Read with the
        test's environment, so a core.hooksPath in the test HOME's .gitconfig counts, as it does for agentbox."""
        hook = subprocess.run(["/usr/bin/git", "-C", str(checkout), "rev-parse", "--path-format=absolute", "--git-path", "hooks/pre-push"],
                              capture_output=True, text=True, check=True, env=self.env).stdout.strip()
        return (Path(hook).is_file() and os.access(hook, os.X_OK)
                and "PUBLIC-LEAK-CHECK-WRAPPER" in Path(hook).read_text(encoding="utf-8"))

    def gh_calls(self) -> list[str]:
        return self.gh_log.read_text(encoding="utf-8").splitlines() if self.gh_log.exists() else []

    def installed(self) -> list[str]:
        return self.installs.read_text(encoding="utf-8").splitlines() if self.installs.exists() else []

    # ── agentbox new: the template and the box clone ───────────────────────────────────────
    def test_a_new_template_of_a_public_repository_gets_the_hook(self) -> None:
        self.repo("pub", "public")
        r = self.launch("pub")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.hooked(self.templates / "pub"))
        self.assertTrue(self.hooked(self.root / "boxes/b/pub"))
        self.assertEqual(self.installed(), [f"{self.templates}/pub.partial"])

    def test_a_new_template_of_an_unreadable_repository_gets_the_hook(self) -> None:
        # Spec R4: unknown counts as public.
        self.repo("dark", None)
        r = self.launch("dark")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.hooked(self.root / "boxes/b/dark"))
        self.assertEqual(self.gh_calls(), ["api repos/x/dark --jq .visibility"])

    def test_a_new_template_of_a_private_repository_gets_none(self) -> None:
        self.repo("priv", "private")
        r = self.launch("priv")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.hooked(self.templates / "priv"))
        self.assertFalse(self.hooked(self.root / "boxes/b/priv"))
        self.assertEqual(self.installed(), [])

    def test_an_existing_template_without_the_hook_gets_it_at_the_next_launch(self) -> None:
        # Spec A2: a template made while its repository was private, or before BET-706, and public now.
        self.repo("old", "private")
        self.assertEqual(self.launch("old").returncode, 0)
        self.assertFalse(self.hooked(self.templates / "old"))
        (self.cache / "x_old").write_text("public", encoding="utf-8")
        r = self.run_bash(f'clone_checkout old "{self.root}/boxes/c/old"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.hooked(self.templates / "old"))
        self.assertTrue(self.hooked(self.root / "boxes/c/old"))
        self.assertEqual(self.installed(), [f"{self.templates}/old"])

    def test_a_hooked_template_costs_no_visibility_lookup(self) -> None:
        self.repo("pub", "public")
        self.assertEqual(self.launch("pub").returncode, 0)
        (self.cache / "x_pub").unlink()
        r = self.run_bash(f'clone_checkout pub "{self.root}/boxes/c/pub"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.hooked(self.root / "boxes/c/pub"))
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(len(self.installed()), 1)

    def test_a_template_whose_wrapper_git_cannot_run_is_repaired(self) -> None:
        # The deep review's finding 2: a wrapper without execute permission is one git skips, so the template is not
        # hooked; the box clone copies its mode.
        self.repo("pub", "public")
        self.assertEqual(self.launch("pub").returncode, 0)
        hook = self.templates / "pub" / ".git" / "hooks" / "pre-push"
        hook.chmod(0o644)
        r = self.run_bash(f'clone_checkout pub "{self.root}/boxes/c/pub"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.hooked(self.templates / "pub"))
        self.assertTrue(self.hooked(self.root / "boxes/c/pub"))
        self.assertEqual(self.installed(), [f"{self.templates}/pub.partial", f"{self.templates}/pub"])

    def test_an_existing_template_with_a_public_push_url_gets_the_hook(self) -> None:
        # The red team's finding 2: a template whose origin fetches from a private repository and pushes to a public
        # one (remote.origin.pushurl), which the box clone copies with the rest of .git/config.
        self.repo("priv", "private")
        self.repo("pub", "public")
        self.assertEqual(self.launch("priv").returncode, 0)
        self.assertFalse(self.hooked(self.templates / "priv"))
        subprocess.run(["/usr/bin/git", "-C", str(self.templates / "priv"), "config", "remote.origin.pushurl", "https://github.com/x/pub"], check=True)
        r = self.run_bash(f'clone_checkout priv "{self.root}/boxes/c/priv"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.hooked(self.templates / "priv"))
        self.assertTrue(self.hooked(self.root / "boxes/c/priv"))

    def test_a_template_with_a_relative_hooks_path_gives_its_clone_the_hook(self) -> None:
        # The cross-family review's finding 4: core.hooksPath=.hooks puts the template's wrapper in its working tree,
        # which the clone (a copy of .git, then a checkout) does not get, while the copied config points the clone's
        # git at its own .hooks. The clone must hold the wrapper where its git runs it.
        self.repo("rel", "public")
        self.assertEqual(self.launch("rel").returncode, 0)
        subprocess.run(["/usr/bin/git", "-C", str(self.templates / "rel"), "config", "core.hooksPath", ".hooks"], check=True)
        self.assertFalse(self.hooked(self.templates / "rel"))
        r = self.run_bash(f'clone_checkout rel "{self.root}/boxes/c/rel"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.hooked(self.templates / "rel"))
        clone = self.root / "boxes/c/rel"
        hooks_path = subprocess.run(["/usr/bin/git", "-C", str(clone), "config", "core.hooksPath"], capture_output=True, text=True, check=True)
        self.assertEqual(hooks_path.stdout.strip(), ".hooks")
        self.assertTrue(self.hooked(clone))
        self.assertTrue((clone / ".hooks" / "pre-push").is_file())
        self.assertEqual(self.installed()[-2:], [f"{self.templates}/rel", str(clone)])
        # An installer that fails at the clone (the template is hooked already, so it runs only there): the launch
        # fails with the clone's error.
        write_stub(self.installer.parent, self.installer.name, "exit 1")
        r = self.run_bash(f'clone_checkout rel "{self.root}/boxes/f/rel"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no leak-check pre-push hook where the box clone", r.stderr)
        # Without the installer the launch fails rather than hand over a clone that pushes unchecked: the template must
        # keep the hook, so the launch stops at the template, before any clone is made.
        self.installer.unlink()
        r = self.run_bash(f'clone_checkout rel "{self.root}/boxes/d/rel"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"no leak-check pre-push hook for the template {self.templates}/rel: {self.installer} is missing", r.stderr)
        self.assertFalse((self.root / "boxes/d/rel/.git").exists())

    def git_status(self, checkout: Path) -> list[str]:
        return subprocess.run(["/usr/bin/git", "-C", str(checkout), "status", "--porcelain"], capture_output=True, text=True,
                              check=True).stdout.splitlines()

    def assert_push_refused(self, checkout: Path) -> None:
        """A plain git push from CHECKOUT to a local bare repository: the stand-in wrapper refuses it, nothing lands."""
        target = self.root / f"receiver-{checkout.parent.name}.git"
        subprocess.run(["/usr/bin/git", "init", "-q", "--bare", str(target)], check=True)
        r = subprocess.run(["/usr/bin/git", "-C", str(checkout), "push", str(target), "HEAD:refs/heads/canary"],
                           capture_output=True, text=True, check=False, env=self.env)
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("refused by the stand-in wrapper", r.stderr)
        refs = subprocess.run(["/usr/bin/git", "--git-dir", str(target), "for-each-ref"], capture_output=True, text=True, check=True)
        self.assertEqual(refs.stdout, "", "the push landed")

    def test_a_template_whose_hooks_path_holds_a_tracked_hook_keeps_the_wrapper(self) -> None:
        # The cross-family review's round-3 delta: the repository tracks .hooks/pre-push and the template's
        # core.hooksPath is .hooks. Installing before the template's forced checkout let that checkout restore the
        # tracked hook, so neither the template nor its clone had the wrapper and a push from the clone landed. The
        # wrapper is installed after the checkout, at the template's and the clone's own effective path, on every
        # launch, and git runs it.
        tracked = "#!/bin/sh\nexit 0\n"
        self.repo("trk", "public", {"f": "x\n", ".hooks/pre-push": tracked})
        self.assertEqual(self.launch("trk").returncode, 0)
        tpl = self.templates / "trk"
        subprocess.run(["/usr/bin/git", "-C", str(tpl), "config", "core.hooksPath", ".hooks"], check=True)
        self.assertFalse(self.hooked(tpl))
        for n in ("c", "d"):  # a second launch too: the template's checkout restores the tracked hook each time
            clone = self.root / "boxes" / n / "trk"
            r = self.run_bash(f'clone_checkout trk "{clone}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(self.hooked(tpl), n)
            self.assertTrue(self.hooked(clone), n)
            self.assertEqual((clone / ".hooks" / "pre-push.local").read_text(encoding="utf-8"), tracked)
            # The tracked hook, replaced by the wrapper, shows as modified; the repository's own hook, kept, as new.
            self.assertEqual(sorted(self.git_status(clone)), [" M .hooks/pre-push", "?? .hooks/pre-push.local"])
            self.assert_push_refused(clone)
        # Without the installer the launch fails rather than hand over a clone that pushes unchecked.
        self.installer.unlink()
        r = self.run_bash(f'clone_checkout trk "{self.root}/boxes/e/trk"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is missing or not executable", r.stderr)

    def test_a_templates_own_ssh_command_makes_an_alias_unknown(self) -> None:
        # The deep audit's round-3 finding 4: a private template whose origin pushes to an ssh alias (pushurl) that only
        # the template's core.sshCommand could resolve. Without the command the alias names corp-like host
        # `review-core-alias`, not GitHub, and the template stays as it is; with it the alias is unknown, so the
        # template and its box clone get the hook, and git runs it, as the git gate and the sweep decide.
        self.repo("priv", "private")
        self.assertEqual(self.launch("priv").returncode, 0)
        tpl = self.templates / "priv"
        subprocess.run(["/usr/bin/git", "-C", str(tpl), "config", "remote.origin.pushurl", "git@review-core-alias:x/pub.git"], check=True)
        r = self.run_bash(f'clone_checkout priv "{self.root}/boxes/c/priv"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.hooked(tpl), "the control: without an ssh command of its own the alias is not GitHub")
        subprocess.run(["/usr/bin/git", "-C", str(tpl), "config", "core.sshCommand", "ssh -F /nonexistent"], check=True)
        clone = self.root / "boxes/d/priv"
        r = self.run_bash(f'clone_checkout priv "{clone}"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.hooked(tpl))
        self.assertTrue(self.hooked(clone))
        self.assert_push_refused(clone)

    def test_an_existing_template_with_a_public_second_remote_gets_the_hook(self) -> None:
        # The second cross-family review's finding 1: a private origin and a public `publish` remote in an existing
        # template, which the box clone copies with the rest of .git/config. Every URL of every remote decides, as
        # in the git gate and the sweep.
        self.repo("priv", "private")
        self.repo("pub", "public")
        self.assertEqual(self.launch("priv").returncode, 0)
        tpl = self.templates / "priv"
        self.assertFalse(self.hooked(tpl))
        subprocess.run(["/usr/bin/git", "-C", str(tpl), "remote", "add", "publish", "https://github.com/x/pub"], check=True)
        clone = self.root / "boxes/c/priv"
        r = self.run_bash(f'clone_checkout priv "{clone}"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.hooked(tpl))
        self.assertTrue(self.hooked(clone))
        self.assert_push_refused(clone)

    def test_a_customized_saved_local_hook_is_never_discarded(self) -> None:
        # The second cross-family review's finding 4: after the installer kept the tracked .hooks/pre-push as
        # pre-push.local, its owner edits pre-push.local. The next launch must not move it over the tracked file
        # (which the forced checkout then rewrites, losing the edit): it refuses, leaving every file as it was.
        tracked = "#!/bin/sh\nexit 0\n"
        self.repo("trk", "public", {"f": "x\n", ".hooks/pre-push": tracked})
        self.assertEqual(self.launch("trk").returncode, 0)
        tpl = self.templates / "trk"
        subprocess.run(["/usr/bin/git", "-C", str(tpl), "config", "core.hooksPath", ".hooks"], check=True)
        self.assertEqual(self.run_bash(f'clone_checkout trk "{self.root}/boxes/c/trk"').returncode, 0)
        local = tpl / ".hooks" / "pre-push.local"
        self.assertEqual(local.read_text(encoding="utf-8"), tracked)
        custom = "#!/bin/sh\n# the owner's own check\nexit 0\n"
        local.write_text(custom, encoding="utf-8")
        wrapper = (tpl / ".hooks" / "pre-push").read_text(encoding="utf-8")
        r = self.run_bash(f'clone_checkout trk "{self.root}/boxes/d/trk"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("differs from the committed", r.stderr)
        self.assertEqual(local.read_text(encoding="utf-8"), custom)
        self.assertEqual((tpl / ".hooks" / "pre-push").read_text(encoding="utf-8"), wrapper)
        self.assertFalse((self.root / "boxes/d/trk/.git").exists())

    def test_a_tracked_symlink_hook_keeps_working_launch_after_launch(self) -> None:
        # The deep audit's round-5 finding 4: the repository tracks .hooks/pre-push as a symlink (to .hooks/impl). The
        # installer keeps that link as pre-push.local; comparing the index's link text with what the link points at
        # called the unchanged link someone's edit, so every launch after the first failed. A symlink is compared as a
        # symlink, by its link text, and moved back as the link itself.
        self.repo("lnk", "public", {"f": "x\n", ".hooks/impl": "#!/bin/sh\nexit 0\n"}, {".hooks/pre-push": "impl"})
        self.assertEqual(self.launch("lnk").returncode, 0)
        tpl = self.templates / "lnk"
        subprocess.run(["/usr/bin/git", "-C", str(tpl), "config", "core.hooksPath", ".hooks"], check=True)
        for n in ("c", "d", "e"):
            clone = self.root / "boxes" / n / "lnk"
            r = self.run_bash(f'clone_checkout lnk "{clone}"')
            self.assertEqual(r.returncode, 0, (n, r.stderr))
            for co in (tpl, clone):
                self.assertTrue(self.hooked(co), (n, co))
                local = co / ".hooks" / "pre-push.local"
                self.assertTrue(local.is_symlink(), (n, co))
                self.assertEqual(os.readlink(local), "impl", (n, co))
            self.assertEqual(sorted(self.git_status(clone)), [" T .hooks/pre-push", "?? .hooks/pre-push.local"])
            self.assert_push_refused(clone)
        # The control: a saved link pointed elsewhere is someone's edit, left as it is, and the launch stops.
        local = tpl / ".hooks" / "pre-push.local"
        local.unlink()
        local.symlink_to("impl-elsewhere")
        wrapper = (tpl / ".hooks" / "pre-push").read_text(encoding="utf-8")
        r = self.run_bash(f'clone_checkout lnk "{self.root}/boxes/f/lnk"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("differs from the committed", r.stderr)
        self.assertEqual(os.readlink(local), "impl-elsewhere")
        self.assertEqual((tpl / ".hooks" / "pre-push").read_text(encoding="utf-8"), wrapper)
        self.assertFalse((self.root / "boxes/f/lnk/.git").exists())

    def test_a_saved_link_differing_only_by_a_trailing_newline_is_refused(self) -> None:
        # The round-6 reviews' P2: command substitution drops trailing newlines, so a saved link re-pointed from `impl`
        # to `impl<newline>` compared equal to the committed link, was moved over the tracked hook, and the forced
        # checkout replaced the owner's choice. Link texts compare byte for byte: the launch stops, every file as it was.
        # Both the dangling link and one whose target exists (a real file with a newline in its name).
        self.repo("lnk", "public", {"f": "x\n", ".hooks/impl": "#!/bin/sh\nexit 0\n"}, {".hooks/pre-push": "impl"})
        self.assertEqual(self.launch("lnk").returncode, 0)
        tpl = self.templates / "lnk"
        subprocess.run(["/usr/bin/git", "-C", str(tpl), "config", "core.hooksPath", ".hooks"], check=True)
        self.assertEqual(self.run_bash(f'clone_checkout lnk "{self.root}/boxes/c/lnk"').returncode, 0)
        local = tpl / ".hooks" / "pre-push.local"
        self.assertEqual(os.readlink(local), "impl")
        wrapper = (tpl / ".hooks" / "pre-push").read_text(encoding="utf-8")
        policy = tpl / ".hooks" / "impl\n"
        for n, exists in (("d", False), ("e", True)):
            if exists:
                policy.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                policy.chmod(0o755)
            local.unlink()
            local.symlink_to("impl\n")
            r = self.run_bash(f'clone_checkout lnk "{self.root}/boxes/{n}/lnk"')
            self.assertNotEqual(r.returncode, 0, n)
            self.assertIn("differs from the committed", r.stderr, n)
            self.assertEqual(os.readlink(local), "impl\n", n)
            self.assertEqual((tpl / ".hooks" / "pre-push").read_text(encoding="utf-8"), wrapper, n)
            self.assertFalse((self.root / f"boxes/{n}/lnk/.git").exists(), n)
        # And the shared comparison itself, as the move's duplicate cleanup calls it: the committed link compares equal,
        # the newline-different one does not.
        judge = (f'prepush_saved_is_committed "{tpl}" .hooks/pre-push "{tpl}/.hooks/pre-push.local" && echo SAME || echo DIFF\n'
                 f'ln -sfn impl "{self.root}/same-link"\n'
                 f'prepush_saved_is_committed "{tpl}" .hooks/pre-push "{self.root}/same-link" && echo SAME || echo DIFF')
        self.assertEqual(self.run_bash(judge).stdout.split(), ["DIFF", "SAME"])

    def test_an_existing_template_with_an_option_like_remote_name_gets_the_hook(self) -> None:
        # The deep audit's round-5 finding 2: git allows a remote named `-publish`; without `--` before it,
        # `git remote get-url` read the name as an option and failed, and the public URL dropped out silently.
        self.repo("priv", "private")
        self.repo("pub", "public")
        self.assertEqual(self.launch("priv").returncode, 0)
        tpl = self.templates / "priv"
        self.assertFalse(self.hooked(tpl))
        subprocess.run(["/usr/bin/git", "-C", str(tpl), "remote", "add", "--", "-publish", "https://github.com/x/pub"], check=True)
        clone = self.root / "boxes/c/priv"
        r = self.run_bash(f'clone_checkout priv "{clone}"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.hooked(tpl))
        self.assertTrue(self.hooked(clone))
        self.assert_push_refused(clone)

    def test_remote_urls_git_cannot_list_count_as_guard(self) -> None:
        # The same finding's remedy: a URL listing that fails is unknown, so GUARD (spec R4), never an empty list.
        source = self.repo("priv", "private")
        judge = f'if remote_urls_guarded "{source}"; then echo Y; else echo N; fi'
        self.assertEqual(self.run_bash(judge).stdout.split(), ["N"])
        write_stub(self.stubs, "git", 'case " $* " in *" get-url "*) exit 1 ;; esac\nexec /usr/bin/git "$@"')
        self.assertEqual(self.run_bash(judge).stdout.split(), ["Y"])

    def test_a_helper_that_fails_counts_as_unknown(self) -> None:
        # The deep review's finding 3 (spec R4): a helper exit other than 0 is unknown whatever it printed. The script
        # is sourced with the real DOTFILES_DIR, then pointed at a dotfiles dir whose helper is a stand-in.
        dotfiles = self.root / "dotfiles"
        judge = (f'DOTFILES_DIR="{dotfiles}"\n'
                 'for u in https://github.com/x/a git@corp.example:x/a; do if origin_guarded "$PWD" "$u"; then echo Y; else echo N; fi; done')
        write_stub(dotfiles / "scripts", "github-repo-visibility", "echo private\nexit 7")
        r = self.run_bash(judge)
        self.assertEqual(r.stdout.split(), ["Y", "Y"], r.stderr)
        write_stub(dotfiles / "scripts", "github-repo-visibility", "echo private")
        r = self.run_bash(judge)
        self.assertEqual(r.stdout.split(), ["N", "N"], r.stderr)

    def test_without_the_installer_a_guarded_template_is_not_made(self) -> None:
        self.repo("pub", "public")
        self.installer.unlink()
        r = self.launch("pub")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"no leak-check pre-push hook for the template {self.templates}/pub: {self.installer} is missing", r.stderr)
        self.assertFalse((self.templates / "pub").exists())
        self.assertFalse((self.templates / "pub.partial").exists())

    def test_without_the_installer_an_unhooked_guarded_template_is_not_copied(self) -> None:
        self.repo("old", "private")
        self.assertEqual(self.launch("old").returncode, 0)
        (self.cache / "x_old").write_text("public", encoding="utf-8")
        self.installer.unlink()
        r = self.run_bash(f'clone_checkout old "{self.root}/boxes/c/old"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"no leak-check pre-push hook for the template {self.templates}/old", r.stderr)
        self.assertFalse((self.root / "boxes/c/old/.git").exists())

    # ── agentbox move --to: the clone made on the target ───────────────────────────────────
    def move_clone(self, source: Path, then: str = "", target_bin: Path | None = None,
                   **extra: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        """move_to_clone_missing for SOURCE, with a target whose paths are the same under another
        root: move_remote runs the script here, the source root mapped onto the target's, with TARGET_BIN (the target's
        own tools: its ssh, say) first on PATH there only. THEN runs after it in the same shell (with $STUB_SRC and
        $STUB_FAR set), for the move's later steps. EXTRA is more environment for the whole run."""
        far = self.root / "target"
        far.mkdir(exist_ok=True)
        r = self.run_bash('move_remote() { local s="${2//$STUB_SRC/$STUB_FAR}"; PATH="${STUB_TARGET_BIN:+$STUB_TARGET_BIN:}$PATH" bash -c "$s"; }\n'
                          f'MOVE_TO_MIRROR=("{source}")\nmove_to_clone_missing devbox || exit $?\n{then}',
                          STUB_SRC=str(self.src), STUB_FAR=str(far), STUB_TARGET_BIN=str(target_bin or ""), **extra)
        return r, far / source.name

    def test_a_move_clone_of_a_public_repository_gets_the_hook(self) -> None:
        source = self.repo("pub", "public")
        r, clone = self.move_clone(source)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"cloned https://github.com/x/pub into {source} on devbox", r.stdout)
        self.assertTrue(self.hooked(clone))

    def test_a_move_clone_of_an_unreadable_repository_gets_the_hook(self) -> None:
        source = self.repo("dark", None)
        r, clone = self.move_clone(source)
        self.assertIn("cloned https://github.com/x/dark", r.stdout)
        self.assertTrue(self.hooked(clone))

    def test_a_move_clone_of_a_private_repository_gets_none_whatever_the_source_has(self) -> None:
        source = self.repo("priv", "private")
        hooks = source / ".git" / "hooks"
        (hooks / "pre-push").write_text("#!/bin/sh\n# PUBLIC-LEAK-CHECK-WRAPPER\n", encoding="utf-8")
        r, clone = self.move_clone(source)
        self.assertIn("cloned https://github.com/x/priv", r.stdout)
        self.assertFalse(self.hooked(clone))
        self.assertEqual(self.installed(), [])

    def test_without_the_installer_a_guarded_move_clone_is_not_made(self) -> None:
        # Round 5: no longer a fallback to rsync, which would hand the moved session an unhooked tree; the move is
        # refused before the hang-up.
        source = self.repo("pub", "public")
        self.installer.unlink()
        r, clone = self.move_clone(source)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is missing or not executable", r.stdout)
        self.assertNotIn("copied with rsync", r.stdout)
        self.assertFalse(clone.exists())

    def test_a_move_clone_whose_hook_install_fails_stops_the_move_and_is_removed(self) -> None:
        # The second cross-family review's finding 3: an installer that fails, one that exits 0 but leaves no wrapper
        # where git runs it, and the real installer's refusal of a hooks directory holding pre-push and pre-push.local
        # (both tracked here, under a global core.hooksPath). Each refuses the move before the hang-up (the remote
        # script's exit 5 and its line), and the half-made clone is removed, so no later step can select it.
        cases = [("fails", "exit 7", None), ("writes-nothing", "exit 0", None),
                 ("collides", None, {"f": "x\n", ".hooks/pre-push": "#!/bin/sh\nexit 0\n", ".hooks/pre-push.local": "#!/bin/sh\nexit 0\n"})]
        stand_in = self.installer.read_text(encoding="utf-8")
        for name, body, files in cases:
            source = self.repo(name, "public", files)
            if files:
                (self.home / ".gitconfig").write_text("[core]\n\thooksPath = .hooks\n", encoding="utf-8")
                self.installer.write_text(stand_in, encoding="utf-8")
            else:
                write_stub(self.installer.parent, self.installer.name, body)
            r, clone = self.move_clone(source)
            self.assertNotEqual(r.returncode, 0, (name, r.stdout))
            self.assertIn("pre-push hook install", r.stdout, name)
            self.assertIn("refusing the move", r.stdout, name)
            self.assertFalse(clone.exists(), name)
            (self.home / ".gitconfig").unlink(missing_ok=True)

    def test_a_move_reinstalls_the_hook_after_the_copy_overwrites_it(self) -> None:
        # The second cross-family review's finding 2: under a core.hooksPath in the work tree (`.hooks`, here in the
        # global config, which the target clone reads too) the repository tracks .hooks/pre-push. The target clone is
        # hooked when it is made, the move's later mirror of the work tree writes the tracked hook back over the
        # wrapper, and move_to_git_native_finish, which runs after the last copy, must put the wrapper back where git
        # runs it, or fail the move.
        tracked = "#!/bin/sh\nexit 0\n"
        source = self.repo("trk", "public", {"f": "x\n", ".hooks/pre-push": tracked})
        (self.home / ".gitconfig").write_text("[core]\n\thooksPath = .hooks\n", encoding="utf-8")
        mirror = ('cp "$STUB_SRC/trk/.hooks/pre-push" "$STUB_FAR/trk/.hooks/pre-push"\n'  # what the work-tree copy does
                  f'MOVE_TO_NATIVE=("{source}\t{source}/.git")\n'
                  'move_to_git_native_finish devbox || { echo "FINISH FAILED: $MOVE_TO_WHY"; exit 9; }\n')
        r, clone = self.move_clone(source, mirror)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.hooked(clone))  # with the test's HOME, whose .gitconfig names .hooks
        self.assertEqual((clone / ".hooks" / "pre-push.local").read_text(encoding="utf-8"), tracked)
        self.assert_push_refused(clone)
        # A wrapper it cannot put back fails the move.
        r, clone = self.move_clone(self.repo("trk2", "public", {"f": "x\n", ".hooks/pre-push": tracked}),
                                   mirror.replace("trk", "trk2").replace("move_to_git_native_finish",
                                                                          f'rm -f "{self.installer}"; move_to_git_native_finish'))
        self.assertEqual(r.returncode, 9, r.stdout + r.stderr)
        self.assertIn("FINISH FAILED", r.stdout)
        self.assertFalse(self.hooked(clone))

    FINISH = 'move_to_git_native_finish devbox || { echo "FINISH FAILED: $MOVE_TO_WHY"; exit 9; }\n'

    def test_a_move_whose_target_clone_fails_hooks_the_tree_it_copies(self) -> None:
        # The round-5 reviews' P1: the target's `git clone` fails on its transport (here the origin cannot be reached),
        # so the tree goes by rsync, .git and all; nothing recorded it, and the copied public checkout pushed unchecked.
        # Every tree that lands on the target is judged, and the finish after the last copy hooks this one.
        source = self.repo("pub", "public")
        origin = self.bare / "x" / "pub"
        away = origin.with_name("pub.away")
        origin.rename(away)
        copy = 'rsync -a "$STUB_SRC/pub/" "$STUB_FAR/pub/"\n' + self.FINISH  # the move's copy of the tree, .git and all
        r, target = self.move_clone(source, copy)
        away.rename(origin)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("git clone failed", r.stdout)
        self.assertIn(f"{source} is copied with rsync", r.stdout)
        self.assertTrue(self.hooked(target))
        self.assert_push_refused(target)

    def test_a_retried_move_hooks_the_target_clone_an_earlier_attempt_left(self) -> None:
        # The deep audit's round-5 finding 1: a move that failed after its clone leaves that clone on the target, the
        # copy's tracked hook written back over the wrapper (core.hooksPath=.hooks). The retry clones nothing, since
        # the target has the repository, and the finish checked only clones the same attempt made: the retried move
        # succeeded with the tree unhooked. Every tree that goes through git into a repository the target has is
        # judged by its origin, whoever cloned it.
        tracked = "#!/bin/sh\nexit 0\n"
        source = self.repo("trk", "public", {"f": "x\n", ".hooks/pre-push": tracked})
        (self.home / ".gitconfig").write_text("[core]\n\thooksPath = .hooks\n", encoding="utf-8")
        mirror = ('cp "$STUB_SRC/trk/.hooks/pre-push" "$STUB_FAR/trk/.hooks/pre-push"\n'  # what the work-tree copy does
                  f'MOVE_TO_NATIVE=("{source}\t{source}/.git")\n')
        # The first attempt clones and hooks, and fails after its copy: the finish never runs.
        r, clone = self.move_clone(source, mirror)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("cloned https://github.com/x/trk", r.stdout)
        self.assertFalse(self.hooked(clone))
        # The retry.
        r, clone = self.move_clone(source, mirror + self.FINISH)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("cloned", r.stdout)
        self.assertTrue(self.hooked(clone))
        self.assertEqual((clone / ".hooks" / "pre-push.local").read_text(encoding="utf-8"), tracked)
        self.assert_push_refused(clone)
        # A retry to a target without the installer is refused before the hang-up, before any copy.
        self.installer.unlink()
        r, clone = self.move_clone(source, 'echo COPIED\n' + mirror + self.FINISH)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing the move", r.stdout)
        self.assertNotIn("COPIED", r.stdout)

    def test_a_move_never_replaces_an_owners_saved_hook_symlink(self) -> None:
        # The round-5 reviews' P2: a global core.hooksPath names the owner's hooks directory, outside every checkout,
        # holding a plain pre-push and a pre-push.local that is a symlink to the owner's policy script, byte for byte
        # the same today. The duplicate cleanup followed the link, found the bytes equal and no tracked file, and
        # removed the link; the installer then saved pre-push as a regular file, which no longer follows the policy.
        # Nothing outside the work tree is a duplicate: the installer refuses the pair, so does the move, and both
        # files stay as they were.
        hook = "#!/bin/sh\nexit 0\n"
        owner = self.root / "owner-hooks"
        policy = self.root / "owner-policy"
        for path in (owner / "pre-push", policy):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(hook, encoding="utf-8")
            path.chmod(0o755)
        (owner / "pre-push.local").symlink_to(policy)
        (self.home / ".gitconfig").write_text(f"[core]\n\thooksPath = {owner}\n", encoding="utf-8")
        source = self.repo("pub", "public")
        r, clone = self.move_clone(source)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("refusing the move", r.stdout)
        self.assertTrue((owner / "pre-push.local").is_symlink())
        self.assertEqual(os.readlink(owner / "pre-push.local"), str(policy))
        self.assertEqual((owner / "pre-push").read_text(encoding="utf-8"), hook)
        self.assertFalse(clone.exists())

    def test_a_move_keeps_a_tracked_symlink_hook_a_symlink(self) -> None:
        # The duplicate the copy leaves, compared as git keeps it: a tracked symlink hook, written back over the
        # wrapper by the copy beside the link the first install saved, is the same link, so the finish removes that
        # duplicate and the installer saves the link again, a link.
        source = self.repo("lnk", "public", {"f": "x\n", ".hooks/impl": "#!/bin/sh\nexit 0\n"}, {".hooks/pre-push": "impl"})
        (self.home / ".gitconfig").write_text("[core]\n\thooksPath = .hooks\n", encoding="utf-8")
        mirror = ('rm -f "$STUB_FAR/lnk/.hooks/pre-push"; cp -P "$STUB_SRC/lnk/.hooks/pre-push" "$STUB_FAR/lnk/.hooks/pre-push"\n'
                  f'MOVE_TO_NATIVE=("{source}\t{source}/.git")\n' + self.FINISH)
        r, clone = self.move_clone(source, mirror)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.hooked(clone))
        self.assertTrue((clone / ".hooks" / "pre-push.local").is_symlink())
        self.assertEqual(os.readlink(clone / ".hooks" / "pre-push.local"), "impl")
        self.assert_push_refused(clone)

    def test_a_move_into_an_existing_target_is_decided_by_the_targets_own_remotes(self) -> None:
        # The round-6 reviews' P1: a tree that goes through git into a repository the target has keeps the target's
        # .git, remotes and all, yet only the source's origin decided it. Each target here was made by an earlier move
        # of a private source; then only the target gains a public destination. The retried move must hook it, and a
        # plain push from it is refused. A target whose remotes stay private is left alone.
        self.repo("pub", "public")
        native = 'MOVE_TO_NATIVE=("$STUB_SRC/{n}\t$STUB_SRC/{n}/.git")\n'
        cases = {
            # a push URL only the target's origin has
            "pushurl": ('git -C "{far}" config remote.origin.pushurl https://github.com/x/pub', ""),
            # a second remote only the target has
            "second": ('git -C "{far}" remote add publish https://github.com/x/pub', ""),
            # the source has no origin at all; the target's points at the public repository
            "noorigin": ('git -C "{far}" remote set-url origin https://github.com/x/pub', 'git -C "{src}" remote remove origin'),
            # the target gains its public push URL after the decision before the hang-up: the finish reads it again
            "late": ("", ""),
        }
        for n, (on_target, on_source) in cases.items():
            source = self.repo(n, "private")
            r, clone = self.move_clone(source)
            self.assertEqual(r.returncode, 0, (n, r.stdout + r.stderr))
            self.assertIn(f"cloned https://github.com/x/{n}", r.stdout, n)
            self.assertFalse(self.hooked(clone), n)
            for cmd in (on_target, on_source):
                if cmd:
                    subprocess.run(["bash", "-c", cmd.format(far=clone, src=source)], check=True, env=self.env)
            late = f'git -C "{clone}" config remote.origin.pushurl https://github.com/x/pub\n' if n == "late" else ""
            r, clone = self.move_clone(source, late + native.format(n=n) + self.FINISH)
            self.assertEqual(r.returncode, 0, (n, r.stdout + r.stderr))
            self.assertNotIn("cloned", r.stdout, n)
            self.assertTrue(self.hooked(clone), n)
            self.assert_push_refused(clone)
        # The control: an earlier move's target of a private source, whose remotes stay private.
        source = self.repo("stays", "private")
        for then in ("", native.format(n="stays") + self.FINISH):
            r, clone = self.move_clone(source, then)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.hooked(clone))
        self.assertNotIn(str(clone), self.installed())

    def test_a_move_never_removes_a_saved_link_differing_only_by_a_trailing_newline(self) -> None:
        # The round-6 reviews' P2, in the move's duplicate cleanup: a saved pre-push.local re-pointed to `impl<newline>`
        # is not the committed link `impl`, so it is no duplicate. The finish leaves it, and the move fails: with the
        # link's target present the installer refuses the pair; with it dangling (which the installer's `[ -e ]` does not
        # see, and would rename pre-push over) ensure_hook refuses first.
        for n, target_exists in (("lnk", True), ("dangle", False)):
            source = self.repo(n, "public", {"f": "x\n", ".hooks/impl": "#!/bin/sh\nexit 0\n"}, {".hooks/pre-push": "impl"})
            (self.home / ".gitconfig").write_text("[core]\n\thooksPath = .hooks\n", encoding="utf-8")
            mirror = (f'rm -f "$STUB_FAR/{n}/.hooks/pre-push"; cp -P "$STUB_SRC/{n}/.hooks/pre-push" "$STUB_FAR/{n}/.hooks/pre-push"\n'
                      + (f'printf "#!/bin/sh\\nexit 0\\n" > "$STUB_FAR/{n}/.hooks/impl\n"; chmod +x "$STUB_FAR/{n}/.hooks/impl\n"\n' if target_exists else "")
                      + f'ln -sfn "impl\n" "$STUB_FAR/{n}/.hooks/pre-push.local"\n'
                      f'MOVE_TO_NATIVE=("{source}\t{source}/.git")\n' + self.FINISH)
            r, clone = self.move_clone(source, mirror)
            self.assertEqual(r.returncode, 9, (n, r.stdout + r.stderr))
            self.assertIn("FINISH FAILED", r.stdout, n)
            self.assertEqual(os.readlink(clone / ".hooks" / "pre-push.local"), "impl\n", n)
            self.assertEqual(os.readlink(clone / ".hooks" / "pre-push"), "impl", n)

    def target_tools(self, name: str, ssh_config: str = "", git_body: str = "") -> Path:
        """The target's own tools, first on PATH there only (move_clone's TARGET_BIN): an ssh that reads SSH_CONFIG as its
        configuration (the source's ssh reads none of it), and, given GIT_BODY, a git stub ahead of the real one."""
        tools = self.root / f"target-bin-{name}"
        cfg = self.root / f"target-ssh-config-{name}"
        cfg.write_text(ssh_config, encoding="utf-8")
        write_stub(tools, "ssh", f'exec /usr/bin/ssh -F "{cfg}" "$@"')
        if git_body:
            write_stub(tools, "git", git_body)
        return tools

    def test_a_targets_own_ssh_alias_is_resolved_on_the_target(self) -> None:
        # The round-7 reviews' P1: a target-only remote `git@target-only-alias:x/pub.git`, an alias only the target's
        # ssh configuration maps to github.com, was classified here with this machine's ssh -G, which maps it nowhere:
        # not-github, so no hook, and a push from the moved tree went out unchecked. Its hosts are resolved on the
        # target now, by the visibility helper run there. Each target is first made by an earlier move of a private
        # source.
        self.repo("pub", "public")
        native = 'MOVE_TO_NATIVE=("$STUB_SRC/{n}\t$STUB_SRC/{n}/.git")\n'
        tools = self.target_tools("aliases", "Host target-only-alias\n  HostName github.com\nHost corp-alias\n  HostName corp.example\n")
        helper = DOTFILES / "scripts" / "github-repo-visibility"
        here = subprocess.run([str(helper), "--slug", "git@target-only-alias:x/pub.git"], capture_output=True, text=True, env=self.env, check=False)
        self.assertEqual(here.stdout.strip(), "not-github", "this machine's ssh must not resolve the alias")
        cases = {"alias": ("git@target-only-alias:x/pub.git", True),
                 # The control: an alias the target maps to another host.
                 "corp": ("git@corp-alias:x/pub.git", False)}
        for n, (url, want) in cases.items():
            source = self.repo(n, "private")
            r, clone = self.move_clone(source, target_bin=tools)
            self.assertEqual(r.returncode, 0, (n, r.stdout + r.stderr))
            self.assertFalse(self.hooked(clone), n)
            subprocess.run(["/usr/bin/git", "-C", str(clone), "remote", "add", "publish", url], check=True)
            r, clone = self.move_clone(source, native.format(n=n) + self.FINISH, target_bin=tools)
            self.assertEqual(r.returncode, 0, (n, r.stdout + r.stderr))
            self.assertEqual(self.hooked(clone), want, n)
            if want:
                self.assert_push_refused(clone)
        # A target that cannot answer (its git cannot list the remotes): the tree must have the hook.
        source = self.repo("dark", "private")
        r, clone = self.move_clone(source)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        failing = self.target_tools("failing", git_body='case " $* " in *" get-url "*) exit 1 ;; esac\nexec /usr/bin/git "$@"')
        r, clone = self.move_clone(source, native.format(n="dark") + self.FINISH, target_bin=failing)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.hooked(clone))

    def tracked_dangling(self, name: str, visibility: str) -> Path:
        """A repository tracking `.hooks/pre-push`, a plain hook, and `.hooks/pre-push.local`, a symlink to nothing."""
        return self.repo(name, visibility, {"f": "x\n", ".hooks/pre-push": "#!/bin/sh\nexit 0\n"},
                         {".hooks/pre-push.local": "owner-policy-missing"})

    def assert_dangling_kept(self, hooks: Path) -> None:
        self.assertTrue((hooks / "pre-push.local").is_symlink())
        self.assertEqual(os.readlink(hooks / "pre-push.local"), "owner-policy-missing")
        self.assertEqual((hooks / "pre-push").read_text(encoding="utf-8"), "#!/bin/sh\nexit 0\n")

    def test_no_installer_call_replaces_a_dangling_saved_hook(self) -> None:
        # The round-7 reviews' P2: the installer tests pre-push.local with `[ -e ]`, which does not see a symlink to
        # nothing, and renames pre-push over it. Every installer call refuses that first (prepush_install_preflight),
        # every file left as it is.
        # 1. A new template's first install, into an owner's hooks directory a global core.hooksPath names.
        owner = self.root / "owner-hooks"
        owner.mkdir()
        (owner / "pre-push").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (owner / "pre-push").chmod(0o755)
        (owner / "pre-push.local").symlink_to("owner-policy-missing")
        (self.home / ".gitconfig").write_text(f"[core]\n\thooksPath = {owner}\n", encoding="utf-8")
        self.repo("new", "public")
        r = self.launch("new")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("pre-push.local is a symlink to nothing", r.stderr)
        self.assert_dangling_kept(owner)
        self.assertFalse((self.templates / "new").exists())
        (self.home / ".gitconfig").unlink()
        # 2. An existing template's first install after its checkout: made while private, its hooks path `.hooks` in
        # the work tree, which tracks the pair.
        self.tracked_dangling("old", "private")
        self.assertEqual(self.launch("old").returncode, 0)
        tpl = self.templates / "old"
        subprocess.run(["/usr/bin/git", "-C", str(tpl), "config", "core.hooksPath", ".hooks"], check=True)
        (self.cache / "x_old").write_text("public", encoding="utf-8")
        r = self.run_bash(f'clone_checkout old "{self.root}/boxes/c/old"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("pre-push.local is a symlink to nothing", r.stderr)
        self.assert_dangling_kept(tpl / ".hooks")
        self.assertFalse((self.root / "boxes/c/old/.git").exists())
        # 3. A box clone's install: the template is hooked in .git/hooks, while the clone's git, by a global include
        # for box paths, runs its hook from the tracked `.hooks`.
        include = self.root / "boxes.gitconfig"
        include.write_text("[core]\n\thooksPath = .hooks\n", encoding="utf-8")
        (self.home / ".gitconfig").write_text(f'[includeIf "gitdir:{self.root}/boxes/"]\n\tpath = {include}\n', encoding="utf-8")
        self.tracked_dangling("box", "public")
        clone = self.root / "boxes/d/box"
        r = self.run_bash(f'clone_checkout box "{clone}"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("pre-push.local is a symlink to nothing", r.stderr)
        self.assertIn("where the box clone", r.stderr)
        self.assertTrue(self.hooked(self.templates / "box"))
        self.assert_dangling_kept(clone / ".hooks")
        (self.home / ".gitconfig").unlink()
        # 4. A move's clone on its target: the owner's hooks directory, its pre-push a symlink to nothing, which the
        # installer would write its wrapper over.
        (owner / "pre-push").unlink()
        (owner / "pre-push").symlink_to("owner-hook-missing")
        (owner / "pre-push.local").unlink()
        (self.home / ".gitconfig").write_text(f"[core]\n\thooksPath = {owner}\n", encoding="utf-8")
        source = self.repo("mv", "public")
        r, target = self.move_clone(source)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("pre-push is a symlink to nothing", r.stdout)
        self.assertEqual(os.readlink(owner / "pre-push"), "owner-hook-missing")
        self.assertFalse(target.exists())

    def test_install_preflight_cases(self) -> None:
        # prepush_install_preflight on its own: a symlink to nothing as pre-push, or as pre-push.local beside a pre-push
        # the installer would rename, refuses; beside the wrapper, which the installer only rewrites, it does not.
        co = self.root / "co"
        subprocess.run(["/usr/bin/git", "init", "-q", str(co)], check=True)
        hooks = co / ".git" / "hooks"
        judge = f'prepush_install_preflight "{co}" >/dev/null && echo OK || echo REFUSED'
        cases = [("dangling pre-push", {"pre-push": None}, "REFUSED"),
                 ("dangling local beside a hook", {"pre-push": "#!/bin/sh\n", "pre-push.local": None}, "REFUSED"),
                 ("dangling local beside the wrapper", {"pre-push": "#!/bin/sh\n# PUBLIC-LEAK-CHECK-WRAPPER\n", "pre-push.local": None}, "OK"),
                 ("a plain pair (the installer refuses it itself)", {"pre-push": "#!/bin/sh\n", "pre-push.local": "#!/bin/sh\n"}, "OK"),
                 ("nothing", {}, "OK")]
        for name, files, want in cases:
            for f in ("pre-push", "pre-push.local"):
                (hooks / f).unlink(missing_ok=True)
            for f, text in files.items():
                if text is None:
                    (hooks / f).symlink_to("missing")
                else:
                    (hooks / f).write_text(text, encoding="utf-8")
            self.assertEqual(self.run_bash(judge).stdout.split(), [want], name)

    def test_the_target_lists_remote_urls_with_the_same_reader(self) -> None:
        # The round-7 quality review: the move's target listing had a second implementation of remote_urls; the target
        # now runs remote_urls itself, carried by move_to_urls_sh. The two give the same URLs, here for a remote named
        # `-publish`, a push URL and an insteadOf.
        co = self.repo("urls", "private")
        for args in (["remote", "add", "--", "-publish", "https://github.com/x/pub"],
                     ["config", "remote.origin.pushurl", "https://github.com/x/pushed"],
                     ["config", "url.https://github.com/x/.insteadOf", "short:"],
                     ["remote", "add", "s", "short:rewritten"]):
            subprocess.run(["/usr/bin/git", "-C", str(co), *args], check=True)
        here = self.run_bash(f'remote_urls -C "{co}"').stdout.split("\n")
        there = self.run_bash(f'eval "$(move_to_urls_sh)"\nremote_urls -C "{co}"').stdout.split("\n")
        self.assertEqual(here, there)
        self.assertEqual(sorted(u for u in here if u), sorted(["https://github.com/x/urls", "https://github.com/x/pushed",
                                                                "https://github.com/x/pub", "https://github.com/x/rewritten"]))


    def fake_helper_dotfiles(self, name: str, slug_answer: str) -> Path:
        """A dotfiles dir that is this one (each entry a symlink to it, agentbox sources several) but for its visibility
        helper: a stand-in that runs the shell SLUG_ANSWER for --slug and passes every other call to the real helper,
        so this machine's own lookups are as before. A move carries it to the target as it carries the real one."""
        dotfiles = self.root / f"fake-dotfiles-{name}"
        for entry in DOTFILES.iterdir():
            if entry.name != "scripts":
                (dotfiles / entry.name).parent.mkdir(parents=True, exist_ok=True)
                (dotfiles / entry.name).symlink_to(entry)
        for entry in (DOTFILES / "scripts").iterdir():
            if entry.name != "github-repo-visibility":
                (dotfiles / "scripts").mkdir(parents=True, exist_ok=True)
                (dotfiles / "scripts" / entry.name).symlink_to(entry)
        real = DOTFILES / "scripts" / "github-repo-visibility"
        write_stub(dotfiles / "scripts", "github-repo-visibility",
                   f'if [ "$1" = --slug ]; then {slug_answer}; exit 0; fi\nexec "{real}" "$@"')
        return dotfiles

    def assert_target_answer_decides(self, cases: list[tuple[str, Path, dict[str, str], bool, bool]]) -> None:
        """For each (name, dotfiles, environment, hook wanted, late): a target made by an earlier move of a private source
        gains the public remote `publish` only there (late: just before the finish, so only the finish's second read
        sees it); the retried move, with that dotfiles dir, must leave the tree hooked as wanted, and a hooked tree
        refuses a push."""
        self.repo("pub", "public")
        native = 'MOVE_TO_NATIVE=("$STUB_SRC/{n}\t$STUB_SRC/{n}/.git")\n'
        for n, dotfiles, env, want, late in cases:
            source = self.repo(n, "private")
            r, clone = self.move_clone(source)
            self.assertEqual(r.returncode, 0, (n, r.stdout + r.stderr))
            self.assertFalse(self.hooked(clone), n)
            add = f'/usr/bin/git -C "{clone}" remote add publish https://github.com/x/pub\n'
            if not late:
                subprocess.run(["bash", "-c", add], check=True)
            r, clone = self.move_clone(source, (add if late else "") + native.format(n=n) + self.FINISH,
                                       DOTFILES_DIR=str(dotfiles), **env)
            self.assertEqual(r.returncode, 0, (n, r.stdout + r.stderr))
            self.assertEqual(self.hooked(clone), want, n)
            if want:
                self.assert_push_refused(clone)

    def test_a_malformed_answer_from_the_target_needs_the_hook(self) -> None:
        # The round-8 reviews: the target's --slug answer was taken by its first line, so an answer starting
        # `not-github` exempted the tree whatever followed, and a public checkout stayed unhooked. The helper's whole
        # output is the answer now: exactly one line, `not-github`, `unknown` or an owner/name; anything else is unknown.
        # The stand-in answers with FAKE_SLUG (printf '%s\n').
        dotfiles = self.fake_helper_dotfiles("env", 'printf "%s\\n" "$FAKE_SLUG"')
        self.assert_target_answer_decides([
            ("extraline", dotfiles, {"FAKE_SLUG": "not-github\nunexpected-output"}, True, False),
            ("twice", dotfiles, {"FAKE_SLUG": "not-github\nnot-github"}, True, False),
            ("trailing", dotfiles, {"FAKE_SLUG": "not-github trailing"}, True, False),
            ("empty", dotfiles, {"FAKE_SLUG": ""}, True, False),
            ("slugjunk", dotfiles, {"FAKE_SLUG": "x/pub junk"}, True, False),
            # the same malformed answer read only by the finish
            ("late", dotfiles, {"FAKE_SLUG": "not-github\nunexpected-output"}, True, True),
            # the control: a clean `not-github` is the exemption it says, whatever the URL
            ("clean", dotfiles, {"FAKE_SLUG": "not-github"}, False, False),
        ])

    def test_an_answer_holding_a_nul_byte_needs_the_hook(self) -> None:
        # The round-9 deep audit: bash drops NUL bytes from a `$(...)` capture, so a helper printing `not-github`, a NUL
        # and a newline read as a clean `not-github` and exempted a public tree. The stand-in writes those bytes itself
        # (no environment variable can hold a NUL); the target reads the answer from a file and calls a NUL unknown.
        nul = self.fake_helper_dotfiles("nul", "printf 'not-github\\000\\n'")
        clean = self.fake_helper_dotfiles("clean", "printf 'not-github\\n'")
        probe = subprocess.run([str(nul / "scripts" / "github-repo-visibility"), "--slug", "x"], capture_output=True, check=True)
        self.assertEqual(probe.stdout, b"not-github\x00\n")
        self.assert_target_answer_decides([
            ("nul", nul, {}, True, False),
            ("nullate", nul, {}, True, True),
            # the control
            ("clean", clean, {}, False, False),
        ])
        # And where this machine reads the target's answers: a NUL in them guards, whatever they say.
        judge = ('move_remote() { printf "R\\tnot-github\\000\\n"; }\n'
                 'rc=0; move_to_target_guarded devbox list_urls || rc=$?; echo "rc=$rc"\n'
                 'move_remote() { printf "R\\tnot-github\\n"; }\n'
                 'rc=0; move_to_target_guarded devbox list_urls || rc=$?; echo "rc=$rc"')
        self.assertEqual(self.run_bash(judge).stdout.split(), ["rc=0", "rc=1"])


if __name__ == "__main__":
    unittest.main()
