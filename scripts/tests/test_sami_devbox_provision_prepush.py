#!/usr/bin/env python3
"""Tests for devbox/sami-devbox-provision's step prepush (dispatch://BET-706).

The step installs the tag's hook files on the machine and runs one pass of the hook sweep over its own
roots, then says every checkout of a public repository has the hook. The sweep of BET-706 looks up at
most 40 visibilities a pass and writes `DEFERRED <path>` for a checkout past that cap, counted in the
summary; such a pass is not complete coverage, and the step must not say it is. The same tag's global
hooks dispatcher (R6: install-global-hooks.sh and global-hooks/) is installed beside them and switched on
by running install-global-hooks.sh, every file checked against the sha256 in leak-check-tag.env first.

step_prepush is run from the real script, cut before its entry point and copied beside a
leak-check-tag.env whose tag and sha256 are those of a stand-in agent-infra clone (a git repository
whose tag holds a stand-in sweep and installers). `remote` runs its script here with the test's HOME and
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

# A stand-in for the tag's install-global-hooks.sh <dir>: it records its call, refuses unless global-hooks/ sits
# beside it with every entry and the sweep has not run yet, and writes <dir>/global/ then <dir>/global.gitconfig.
FAKE_GLOBAL_INSTALLER = r"""#!/usr/bin/env bash
here=$(dirname "$(readlink -f "$0")")
echo "install-global-hooks.sh $*" >> "$here/global-install.log"
[ "${FAKE_GLOBAL:-}" != fail ] || { echo "stand-in: told to fail" >&2; exit 1; }
[ -x "$here/global-hooks/dispatch" ] && [ -x "$here/global-hooks/pre-push" ] || { echo "stand-in: no global-hooks/ beside me" >&2; exit 1; }
[ ! -e "$here/provision-sweep.log" ] || [ ! -s "$here/provision-sweep.log" ] || echo "after the sweep" >> "$here/global-install.log"
mkdir -p "$1/global" && cp "$here"/global-hooks/* "$1/global/"
printf '[core]\n\thooksPath = %s/global\n' "$1" > "$1/global.gitconfig"
"""

# The hook files of the stand-in tag, in leak-check-tag.env's order: the two of step prepush before R6, then R6's.
HOOK_FILES = {
    "install-prepush-hook.sh": "#!/bin/sh\nexit 0\n",
    "sweep-hooks.sh": FAKE_SWEEP,
    "install-global-hooks.sh": FAKE_GLOBAL_INSTALLER,
    "global-hooks/dispatch": "#!/bin/sh\nexit 0\n",
    "global-hooks/pre-push": '#!/bin/sh\nexec "$(dirname "$0")/dispatch" pre-push "$@"\n',
}


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
        # The stand-in agent-infra clone, its tag holding the hook files.
        infra = self.root / "agent-infra"
        for name, body in HOOK_FILES.items():
            write_exe(infra / "leak-check" / name, body)
        git = ["git", "-C", str(infra), "-c", "user.name=T", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]
        subprocess.run(["git", "init", "-q", str(infra)], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-qm", "hooks"], check=True)
        subprocess.run([*git, "tag", "leak-check-vtest"], check=True)
        self.devbox = self.root / "devbox"
        self.devbox.mkdir()
        self.write_tag_env({})
        text = SCRIPT.read_text(encoding="utf-8")
        (self.devbox / "lib.sh").write_text(text[:text.index('from="" only=""\n')], encoding="utf-8")
        self.lib = self.devbox / "lib.sh"
        self.hooks = self.home / ".local/share/public-leak-check-hooks"
        bin_ = self.root / "bin"
        # rsync -rt -e <ssh> SOURCE... sami@<vm>:/tmp/pph-stage/: each source goes to the test's staging directory,
        # a directory's contents (trailing /) as rsync copies them.
        write_exe(bin_ / "rsync", '#!/bin/bash\nshift 3\nargs=("$@"); unset "args[${#args[@]}-1]"\n'
                  f'mkdir -p "{self.stage}" && for a in "${{args[@]}}"; do if [[ "$a" == */ ]]; then cp -a "$a." "{self.stage}/"; '
                  f'else cp -a "$a" "{self.stage}/"; fi; done\n')
        self.env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", AGENT_INFRA=str(infra))
        self.env.pop("FAKE_SWEEP", None)
        self.env.pop("FAKE_GLOBAL", None)

    def write_tag_env(self, sums: dict[str, str]) -> None:
        """leak-check-tag.env with the stand-in tag's sha256 of each hook file, but those SUMS gives."""
        lines = "\n".join(f"{sums.get(n, hashlib.sha256(b.encode()).hexdigest())}  {n}" for n, b in HOOK_FILES.items())
        (self.devbox / "leak-check-tag.env").write_text(f"LEAK_CHECK_TAG=leak-check-vtest\nLEAK_CHECK_HOOK_SHA256='{lines}'\n", encoding="utf-8")

    def step(self, outcome: str, **extra: str) -> subprocess.CompletedProcess[str]:
        script = (f'source "{self.lib}"\n'
                  f'remote() {{ local s="${{*//\\/tmp\\/pph-stage/{self.stage}}}"; HOME="{self.home}" bash -c "$s"; }}\n'
                  'step_prepush\n')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=120, check=False,
                              env={**self.env, "FAKE_SWEEP": outcome, **extra})

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

    def test_the_global_hooks_are_installed_beside_the_others_and_switched_on(self) -> None:
        r = self.step("complete")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for name, body in HOOK_FILES.items():
            self.assertEqual((self.hooks / name).read_text(encoding="utf-8"), body, name)
            self.assertTrue(os.access(self.hooks / name, os.X_OK), name)
        # Once, on the hooks directory, before the sweep: a sweep that defers or fails leaves the dispatcher on.
        self.assertEqual((self.hooks / "global-install.log").read_text(encoding="utf-8").splitlines(), [f"install-global-hooks.sh {self.hooks}"])
        self.assertTrue((self.hooks / "global.gitconfig").is_file())
        self.assertTrue(os.access(self.hooks / "global" / "pre-push", os.X_OK))

    def test_a_deferring_sweep_still_leaves_the_dispatcher_on(self) -> None:
        r = self.step("deferred")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((self.hooks / "global.gitconfig").is_file(), r.stdout + r.stderr)

    def test_a_global_hook_file_not_the_tags_sha256_installs_nothing(self) -> None:
        self.write_tag_env({"global-hooks/dispatch": "0" * 64})
        r = self.step("complete")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("hook files are not the sha256 in devbox/leak-check-tag.env", r.stderr)
        self.assertFalse(self.hooks.exists(), "files were installed from a tag that does not match")

    def test_a_placeholder_sha256_installs_nothing(self) -> None:
        # A TBD-<file> placeholder is no sha256 line; `sha256sum -c` alone passes the other lines and warns.
        self.write_tag_env({"install-global-hooks.sh": "TBD-install-global-hooks.sh"})
        r = self.step("complete")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("hook files are not the sha256 in devbox/leak-check-tag.env", r.stderr)
        self.assertFalse(self.hooks.exists(), "files were installed with a placeholder sha256")

    def test_a_failing_global_installer_fails_the_step(self) -> None:
        r = self.step("complete", FAKE_GLOBAL="fail")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("install-global-hooks.sh", r.stdout + r.stderr)
        self.assertFalse((self.hooks / "global.gitconfig").exists())
        self.assertNotIn("every checkout of a public repository has the pre-push hook", r.stdout)


if __name__ == "__main__":
    unittest.main()
