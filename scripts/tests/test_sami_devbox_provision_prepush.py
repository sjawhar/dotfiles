#!/usr/bin/env python3
"""Tests for devbox/sami-devbox-provision's step prepush (dispatch://BET-706).

The step installs the tag's hook files on the machine and runs one pass of the hook sweep over its own
roots, then says every checkout of a public repository has the hook. The sweep of BET-706 looks up at
most 40 visibilities a pass and writes `DEFERRED <path>` for a checkout past that cap, counted in the
summary; such a pass is not complete coverage, and the step must not say it is.

step_prepush is run from the real script, cut before its entry point and copied beside a
leak-check-tag.env whose tag and sha256 are those of a stand-in agent-infra clone (a git repository
whose tag holds a stand-in sweep and installer). `remote` runs its script here with the test's HOME and
the machine's staging path mapped into the test's directory, and `rsync` is a stub that copies there.
"""

import hashlib
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "devbox" / "sami-devbox-provision"

FAKE_SWEEP = r"""#!/usr/bin/env bash
here=$(dirname "$(readlink -f "$0")")
log=$here/sweep-hooks.log
fast_roots=(/fast)
slow_roots=(/slow)
sweep() {
  case "${FAKE_SWEEP:-complete}" in
    complete) echo "2026-11-05T00:00:00Z sweep [$*]: 41 checkouts hooked or public or unreadable, 0 deferred, 40 installed, 0 failed" >>"$log" ;;
    deferred) echo "2026-11-05T00:00:00Z DEFERRED /src/later" >>"$log"
      echo "2026-11-05T00:00:00Z sweep [$*]: 41 checkouts hooked or public or unreadable, 1 deferred, 40 installed, 0 failed" >>"$log" ;;
    deferred_count) echo "2026-11-05T00:00:00Z sweep [$*]: 41 checkouts hooked or public or unreadable, 2 deferred, 39 installed, 0 failed" >>"$log" ;;
    failed) echo "2026-11-05T00:00:00Z FAILED /src/broken" >>"$log"
      echo "2026-11-05T00:00:00Z sweep [$*]: 41 checkouts hooked or public or unreadable, 0 deferred, 40 installed, 1 failed" >>"$log" ;;
  esac
}

i=0
while true; do sweep "${fast_roots[@]}"; sleep 300; done
"""


def write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class StepPrepush(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        (self.home / ".dotfiles").mkdir(parents=True)
        self.stage = self.root / "pph-stage"
        # The stand-in agent-infra clone, its tag holding the two hook files.
        infra = self.root / "agent-infra"
        files = {"sweep-hooks.sh": FAKE_SWEEP, "install-prepush-hook.sh": "#!/bin/sh\nexit 0\n"}
        for name, body in files.items():
            write_exe(infra / "leak-check" / name, body)
        git = ["git", "-C", str(infra), "-c", "user.name=T", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]
        subprocess.run(["git", "init", "-q", str(infra)], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-qm", "hooks"], check=True)
        subprocess.run([*git, "tag", "leak-check-vtest"], check=True)
        sums = "\n".join(f"{hashlib.sha256(body.encode()).hexdigest()}  {name}" for name, body in sorted(files.items(), key=lambda kv: kv[0] != "install-prepush-hook.sh"))
        devbox = self.root / "devbox"
        devbox.mkdir()
        (devbox / "leak-check-tag.env").write_text(f"LEAK_CHECK_TAG=leak-check-vtest\nLEAK_CHECK_HOOK_SHA256='{sums}'\n", encoding="utf-8")
        text = SCRIPT.read_text(encoding="utf-8")
        (devbox / "lib.sh").write_text(text[:text.index('from="" only=""\n')], encoding="utf-8")
        self.lib = devbox / "lib.sh"
        bin_ = self.root / "bin"
        # rsync -t -e <ssh> FILES... sami@<vm>:/tmp/pph-stage/: the files go to the test's staging directory.
        write_exe(bin_ / "rsync", '#!/bin/bash\nshift 3\nargs=("$@"); unset "args[${#args[@]}-1]"\n'
                  f'mkdir -p "{self.stage}" && cp "${{args[@]}}" "{self.stage}/"\n')
        self.env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", AGENT_INFRA=str(infra))
        self.env.pop("FAKE_SWEEP", None)

    def step(self, outcome: str) -> subprocess.CompletedProcess[str]:
        script = (f'source "{self.lib}"\n'
                  f'remote() {{ local s="${{*//\\/tmp\\/pph-stage/{self.stage}}}"; HOME="{self.home}" bash -c "$s"; }}\n'
                  'step_prepush\n')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=120, check=False,
                              env={**self.env, "FAKE_SWEEP": outcome})

    def test_a_complete_pass_says_every_checkout_has_the_hook(self) -> None:
        r = self.step("complete")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("every checkout of a public repository has the pre-push hook", r.stdout)
        self.assertTrue((self.home / ".local/share/public-leak-check-hooks/sweep-hooks.sh").is_file())

    def test_a_failed_checkout_fails_the_step(self) -> None:
        r = self.step("failed")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("the pre-push hook install on sami-devbox failed", r.stderr)
        self.assertNotIn("every checkout of a public repository has the pre-push hook", r.stdout)

    def test_a_deferred_checkout_is_not_complete_coverage(self) -> None:
        for outcome in ("deferred", "deferred_count"):
            r = self.step(outcome)
            self.assertNotEqual(r.returncode, 0, outcome)
            self.assertIn("deferred", r.stderr, outcome)
            self.assertIn("run step prepush again", r.stderr, outcome)
            self.assertNotIn("every checkout of a public repository has the pre-push hook", r.stdout, outcome)


if __name__ == "__main__":
    unittest.main()
