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


if __name__ == "__main__":
    unittest.main()
