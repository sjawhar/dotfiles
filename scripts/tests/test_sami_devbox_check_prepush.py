#!/usr/bin/env python3
"""Tests for devbox/sami-devbox-check's pre-push hook line (prepush_hook_host_check, dispatch://BET-706).

The line runs one pass of the hook sweep's own code over ~/src with an installer that installs nothing
and fails, so a checkout without the hook is a FAILED line. The sweep of BET-706 looks up at most 40
visibilities per pass and writes `DEFERRED <path>` for a checkout it could not decide, and such a
checkout is not known to have the hook: the line must not pass on it.

The function is run from the real script, copied beside a leak-check-tag.env whose pinned sha256 are
those of a stand-in sweep (the script reads that file from its own directory), with HOME the test's.
The stand-in's `sweep` writes the lines FAKE_SWEEP names and the summary line, in its log.
"""

import hashlib
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "devbox" / "sami-devbox-check"

FAKE_SWEEP = r"""#!/usr/bin/env bash
here=$(dirname "$(readlink -f "$0")")
log=$here/sweep-hooks.log
fast_roots=(/fast)
slow_roots=(/slow)
sweep() {
  local line n=0 d=0 f=0
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    # `COUNT n`: n deferred checkouts counted in the summary with no line of their own.
    case $line in COUNT\ *) d=$((d + ${line#COUNT })); continue ;; esac
    echo "2026-11-05T00:00:00Z $line" >>"$log"
    case $line in FAILED*) f=$((f + 1)) ;; DEFERRED*) d=$((d + 1)) ;; esac
    n=$((n + 1))
  done <<<"${FAKE_SWEEP:-}"
  echo "2026-11-05T00:00:00Z sweep [$*]: $n checkouts hooked or public or unreadable, $d deferred, 0 installed, $f failed" >>"$log"
}

i=0
while true; do sweep "${fast_roots[@]}"; sleep 300; done
"""


class PrepushHookLine(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        hooks = self.home / ".local/share/public-leak-check-hooks"
        hooks.mkdir(parents=True)
        (hooks / "sweep-hooks.sh").write_text(FAKE_SWEEP, encoding="utf-8")
        (hooks / "install-prepush-hook.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        sums = "\n".join(f"{hashlib.sha256((hooks / n).read_bytes()).hexdigest()}  {n}" for n in ("install-prepush-hook.sh", "sweep-hooks.sh"))
        devbox = self.root / "devbox"
        devbox.mkdir()
        (devbox / "leak-check-tag.env").write_text(f"LEAK_CHECK_TAG=leak-check-vtest\nLEAK_CHECK_HOOK_SHA256='{sums}'\n", encoding="utf-8")
        # The script up to its entry point, beside the tag file it sources from its own directory.
        text = SCRIPT.read_text(encoding="utf-8")
        (devbox / "lib.sh").write_text(text[:text.index('case "${1:-}" in\n')], encoding="utf-8")
        self.lib = devbox / "lib.sh"

    def judge(self, lines: str) -> str:
        r = subprocess.run(["bash", "-c", f'source "{self.lib}"; prepush_hook_host_check; echo "fails=$fails"'],
                           capture_output=True, text=True, timeout=60, check=False,
                           env=dict(os.environ, HOME=str(self.home), FAKE_SWEEP=lines))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_every_checkout_hooked_passes(self) -> None:
        out = self.judge("")
        self.assertRegex(out, r"(?m)^ok   pre-push hook: every public checkout under ~/src has it")
        self.assertIn("fails=0", out)

    def test_a_checkout_without_the_hook_fails(self) -> None:
        out = self.judge("FAILED /src/a")
        self.assertRegex(out, r"(?m)^FAIL pre-push hook: missing in /src/a ")
        self.assertIn("fails=1", out)

    def test_a_deferred_checkout_is_not_reported_as_hooked(self) -> None:
        out = self.judge("DEFERRED /src/b\nDEFERRED /src/c")
        self.assertRegex(out, r"(?m)^FAIL pre-push hook: .*not verified .*/src/b /src/c")
        self.assertNotIn("ok   pre-push hook", out)
        self.assertIn("fails=1", out)

    def test_missing_and_deferred_are_both_named(self) -> None:
        out = self.judge("FAILED /src/a\nDEFERRED /src/b")
        self.assertRegex(out, r"(?m)^FAIL pre-push hook: missing in /src/a .*not verified .*/src/b")
        self.assertIn("fails=1", out)

    def test_a_deferred_count_in_the_summary_alone_is_not_reported_as_hooked(self) -> None:
        # The summary's `<D> deferred` decides too, as in scripts/dotfiles-follow and step prepush.
        out = self.judge("COUNT 3")
        self.assertRegex(out, r"(?m)^FAIL pre-push hook: .*not verified")
        self.assertNotIn("ok   pre-push hook", out)
        self.assertIn("fails=1", out)

    def test_a_placeholder_sha256_is_not_the_tags_files(self) -> None:
        # `sha256sum -c` alone passes the lines it can read and only warns about a TBD-<file> placeholder.
        env = self.root / "devbox" / "leak-check-tag.env"
        env.write_text(env.read_text(encoding="utf-8").replace("sweep-hooks.sh'", "sweep-hooks.sh\nTBD-install-global-hooks.sh  install-global-hooks.sh'"),
                       encoding="utf-8")
        out = self.judge("")
        self.assertRegex(out, r"(?m)^FAIL pre-push hook: .* is not leak-check-vtest's hook files")
        self.assertIn("fails=1", out)


# The R6 global hooks dispatcher (global_hooks_host_check): the hooks directory's global/ holds, executable and
# byte-identical, every entry of the global-hooks/ provisioning installed from the tag and nothing else, global.gitconfig
# is there, and the user's git config includes it, so git's hooks directory is global/.
GLOBAL_ENTRIES = {
    "dispatch": "#!/bin/sh\nexit 0\n",
    "pre-push": '#!/bin/sh\nexec "$(dirname "$0")/dispatch" pre-push "$@"\n',
    "commit-msg": '#!/bin/sh\nexec "$(dirname "$0")/dispatch" commit-msg "$@"\n',
}


class GlobalHooksLine(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.hooks = self.home / ".local/share/public-leak-check-hooks"
        for d in ("global-hooks", "global"):
            (self.hooks / d).mkdir(parents=True)
            for name, body in GLOBAL_ENTRIES.items():
                (self.hooks / d / name).write_text(body, encoding="utf-8")
                (self.hooks / d / name).chmod(0o755)
        (self.hooks / "global.gitconfig").write_text("[core]\n\thooksPath = ~/.local/share/public-leak-check-hooks/global\n", encoding="utf-8")
        (self.home / ".gitconfig").write_text(
            re.search(r"(?m)^\[include\]\n[ \t]+path = ~/\.local/share/public-leak-check-hooks/global\.gitconfig\n",
                      (DOTFILES / ".gitconfig").read_text(encoding="utf-8")).group(0), encoding="utf-8")
        devbox = self.root / "devbox"
        devbox.mkdir()
        (devbox / "leak-check-tag.env").write_text("LEAK_CHECK_TAG=leak-check-vtest\n", encoding="utf-8")
        text = SCRIPT.read_text(encoding="utf-8")
        (devbox / "lib.sh").write_text(text[:text.index('case "${1:-}" in\n')], encoding="utf-8")
        self.lib = devbox / "lib.sh"

    def judge(self) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG")}
        r = subprocess.run(["bash", "-c", f'source "{self.lib}"; global_hooks_host_check; echo "fails=$fails"'],
                           capture_output=True, text=True, timeout=60, check=False,
                           env={**env, "HOME": str(self.home), "GIT_CONFIG_NOSYSTEM": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def assert_fails(self, pattern: str) -> None:
        out = self.judge()
        self.assertRegex(out, rf"(?m)^FAIL global hooks: {pattern}")
        self.assertNotIn("ok   global hooks", out)
        self.assertIn("fails=1", out)

    def test_a_complete_install_passes(self) -> None:
        out = self.judge()
        self.assertRegex(out, r"(?m)^ok   global hooks: git runs .*/global/ for every repository without a hooks path of its own \(3 entries\)")
        self.assertIn("fails=0", out)

    def test_a_missing_global_gitconfig_fails(self) -> None:
        (self.hooks / "global.gitconfig").unlink()
        self.assert_fails(r".*global\.gitconfig is missing")

    def test_a_missing_entry_fails(self) -> None:
        (self.hooks / "global" / "commit-msg").unlink()
        self.assert_fails(r".*incomplete.* commit-msg")

    def test_an_entry_that_is_not_executable_fails(self) -> None:
        (self.hooks / "global" / "pre-push").chmod(0o644)
        self.assert_fails(r".*incomplete.* pre-push")

    def test_an_entry_that_differs_from_the_tags_fails(self) -> None:
        (self.hooks / "global" / "dispatch").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        self.assert_fails(r".*incomplete.* dispatch")

    def test_an_entry_the_tag_does_not_have_fails(self) -> None:
        # An entry the tag's global-hooks/ does not have (a stray file, or one an older tag had): git would run it.
        (self.hooks / "global" / "pre-push.old").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (self.hooks / "global" / "pre-push.old").chmod(0o755)
        self.assert_fails(r".*pre-push\.old \(not the tag's\)")

    def test_a_missing_global_directory_fails(self) -> None:
        for name in GLOBAL_ENTRIES:
            (self.hooks / "global" / name).unlink()
        (self.hooks / "global").rmdir()
        self.assert_fails(r".*incomplete")

    def test_no_tag_entries_fails(self) -> None:
        for name in GLOBAL_ENTRIES:
            (self.hooks / "global-hooks" / name).unlink()
        self.assert_fails(r".*global-hooks/ .*no entries")

    def test_an_install_the_git_config_does_not_include_fails(self) -> None:
        (self.home / ".gitconfig").write_text("[user]\n\tname = T\n", encoding="utf-8")
        self.assert_fails(r".*not switched on")


if __name__ == "__main__":
    unittest.main()
