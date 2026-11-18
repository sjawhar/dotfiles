#!/usr/bin/env python3
"""shims/gh never takes a gh wrapper that runs it again for the real gh, and a wrapper that hands it the same call
back is skipped, so the call reaches the real gh or fails at once with exit 127 (shims/AGENTS.md, "Adding to a shim").
Where the shim cannot tell that the call is the same (no sha256sum on PATH, or an argv that changes on each pass), the
hop count in its marker ends the chain at its bound with exit 127.

On 2026-11-18 cybermonklabs/legion's TestRealGhAndGitSendTheTargetOwnersToken put a pane's `gh` wrapper, which runs
the test binary as `legion gh`, first on PATH and, with LEGION_GH_PATH unset, gave `legion gh` the first gh it found,
this shim, by its path, as the real gh; `legion gh` reads that gh's help by starting it as a child. The shim took the
first gh from the top of PATH, the wrapper, for the real gh, and the chain of wrapper, `legion gh` and shim grew about
2,000 processes deep (4,275 processes) until the user's memory limit stopped it. Nothing on the caller's side keeps
shims/ off that PATH: mise.toml's `_.path` puts it first in every PATH mise builds, so a test run through mise that
puts its wrapper first on its own PATH has the wrapper, then shims/.

The stand-in for `legion gh` below is a script that runs a gh as a child with the environment unchanged: the first gh
on PATH that is not its own wrapper, as legion's controller finds it, or the gh STUB_GH names by its path, as a test
of legion gives it LEGION_GH_PATH. Each case runs in a cgroup of its own under a task limit and a timeout
(`LeakTestCase.launch`), so a lookup that loops fails the case instead of forking without end. The environment is built
from nothing: no agent context, no knives (KNIVES_GH_BYPASS=1), and a mise data dir and configuration of the test's
own, so the only gh is the one each case puts on PATH or installs as the pin.
"""
from __future__ import annotations

import os
import re
import shutil
import unittest
from pathlib import Path

from test_leak_terms import LeakTestCase

SHIMS = Path(__file__).resolve().parents[1]
GH = SHIMS / "gh"
# The most hand-offs to a gh one chain of processes may make through the shim, as the shim sets it. A shim with no
# such bound fails the cases that need it, not this import.
_BOUND = re.search(r"^_max_hops=(\d+)$", GH.read_text(encoding="utf-8"), re.MULTILINE)
MAX_HOPS = int(_BOUND.group(1)) if _BOUND else 8
# A loop of these shell scripts stays far below this; the shim's own lookup needs a handful of tasks.
TASK_LIMIT = 128
TIMEOUT = 20
STAND_IN = """#!/bin/bash
[[ "${1:-}" == gh ]] && shift
if [[ -n "${STUB_GH:-}" ]]; then
    "$STUB_GH" "$@"
    exit $?
fi
IFS=: read -ra dirs <<< "$PATH"
for d in "${dirs[@]}"; do
    [[ "$d" == "$STUB_WRAP_DIR" ]] && continue
    if [[ -x "$d/gh" ]]; then
        "$d/gh" "$@"
        exit $?
    fi
done
echo "stand-in: no gh on PATH past the wrapper" >&2
exit 127
"""


def executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


class GhWrapperLoop(LeakTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task_limit = TASK_LIMIT
        self.wrap = self.root / "wrap"
        self.real = self.root / "real"
        self.wrap_log = self.root / "wrap.log"
        self.real_log = self.root / "real.log"
        self.stand_in = executable(self.root / "legion-gh", STAND_IN)
        # The pane's wrapper: it hands every call to `legion gh`, and logs each pass, one line per process in the chain.
        self.wrapper(f'exec {self.stand_in} gh "$@"')
        executable(self.real / "gh", f'#!/bin/bash\nprintf "%s\\n" "$*" >> {self.real_log}\n')
        (self.root / "mise-data").mkdir()
        (self.root / "mise.toml").write_text('[tools]\nnode = "24.1.0"\n', encoding="utf-8")

    def wrapper(self, line: str) -> None:
        executable(self.wrap / "gh", f'#!/bin/bash\necho pass >> {self.wrap_log}\n{line}\n')

    def run_gh(self, path: list[Path | str], *args: str, by_path: bool = False):
        env = {
            "HOME": str(self.root / "home"),
            "PATH": os.pathsep.join(map(str, path)),
            "KNIVES_GH_BYPASS": "1",
            "MISE_DATA_DIR": str(self.root / "mise-data"),
            "MISE_GLOBAL_CONFIG_FILE": str(self.root / "mise.toml"),
            "STUB_WRAP_DIR": str(self.wrap),
        }
        if by_path:
            env["STUB_GH"] = str(GH)
        return self.launch(["gh", *args], cwd=self.root, env=env, timeout=TIMEOUT)

    def passes(self) -> int:
        return len(self.wrap_log.read_text().splitlines()) if self.wrap_log.exists() else 0

    def real_calls(self) -> list[str]:
        return self.real_log.read_text().splitlines() if self.real_log.exists() else []

    def test_a_wrapper_above_the_shim_is_never_the_real_gh(self):
        """The PATH of legion's TestRealGhAndGitSendTheTargetOwnersToken: its pane wrapper, then shims/ (the directory
        of the git the test found), then the system's, with `legion gh`'s real gh the first gh the test found, this
        shim, by its path; the real gh is the pinned install past PATH, or a gh on PATH past shims/."""
        (self.root / "mise.toml").write_text('[tools]\ngh = "1.2.3"\n', encoding="utf-8")
        executable(self.root / "mise-data/installs/gh/1.2.3/bin/gh",
                   f'#!/bin/bash\nprintf "%s\\n" "$*" >> {self.real_log}\n')
        system_gh = shutil.which("gh", path="/usr/bin:/bin")
        for name, path in (("the pinned install past PATH", [self.wrap, SHIMS, "/usr/bin", "/bin"]),
                           ("a gh on PATH past shims/", [self.wrap, SHIMS, self.real, "/usr/bin", "/bin"])):
            with self.subTest(name):
                if system_gh is not None and self.real not in path:
                    self.skipTest(f"this machine's {system_gh} would answer in place of the pinned install")
                self.wrap_log.unlink(missing_ok=True)
                self.real_log.unlink(missing_ok=True)
                result = self.run_gh(path, "api", "repos/o/r", by_path=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.real_calls(), ["api repos/o/r"])
                self.assertEqual(self.passes(), 1, "the wrapper ran again: the shim took it for the real gh")

    def test_a_wrapper_that_runs_the_shim_by_its_path_gets_the_call_once(self):
        """legion's TestLegionGhRefusesACommandWhoseRepositoriesRouteToTwoDirectories: `legion gh`'s real gh runs this
        shim by its path for a help read, and shims/ is not on PATH at all, so the shim takes the first gh from the
        top, the wrapper; the same call handed back skips it."""
        result = self.run_gh([self.wrap, self.real, "/usr/bin", "/bin"], "--help", by_path=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.real_calls(), ["--help"])
        self.assertEqual(self.passes(), 2, "the wrapper's first pass and the shim's one hand-off, and no more")

    def test_a_wrapper_below_the_shim_that_hands_the_call_back_is_skipped(self):
        """A pass-through below the shim that runs `gh` by name from the top, as a child or in its own pid."""
        for how, line in (("child", 'gh "$@"'), ("exec", 'exec gh "$@"')):
            with self.subTest(how=how):
                self.wrap_log.unlink(missing_ok=True)
                self.real_log.unlink(missing_ok=True)
                self.wrapper(line)
                result = self.run_gh([SHIMS, self.wrap, self.real, "/usr/bin", "/bin"], "pr", "view", "1")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.real_calls(), ["pr view 1"])
                self.assertEqual(self.passes(), 1)

    def test_with_only_wrappers_it_fails_at_once_naming_path(self):
        if shutil.which("gh", path="/usr/bin:/bin") is not None:
            self.skipTest("this machine has a gh in /usr/bin or /bin, which the call would reach")
        path = os.pathsep.join(map(str, [self.wrap, "/usr/bin", "/bin"]))
        result = self.run_gh([self.wrap, "/usr/bin", "/bin"], "api", "user", by_path=True)
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertEqual(self.passes(), 2)
        refusal = [line for line in result.stderr.splitlines() if line.startswith("gh shim:")]
        self.assertEqual(len(refusal), 1, result.stderr)
        self.assertIn(f"no real gh found on PATH ({path})", refusal[0])
        self.assertIn(f"{self.wrap / 'gh'} handed the call back to this shim", refusal[0])

    def test_another_call_below_the_real_gh_still_reaches_it(self):
        """The control: gh runs git, and git runs gh's credential helper, a call of its own, through this shim. The
        marker the outer call left names the gh it was handed to, and must skip nothing for another call."""
        executable(self.real / "gh", f'#!/bin/bash\nprintf "%s\\n" "$*" >> {self.real_log}\n'
                                     '[[ "$1" == outer ]] && gh inner\nexit 0\n')
        for name, path, by_path in (("shims on PATH", [SHIMS, self.real, "/usr/bin", "/bin"], False),
                                    ("through a wrapper", [self.wrap, self.real, "/usr/bin", "/bin"], True)):
            with self.subTest(name):
                self.real_log.unlink(missing_ok=True)
                result = self.run_gh(path, "outer", by_path=by_path)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.real_calls(), ["outer", "inner"])

    def assert_stopped_by_the_hop_count(self, result, path: list[Path | str]) -> None:
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertEqual(self.real_calls(), [])
        self.assertEqual(self.passes(), MAX_HOPS + 1, "the wrapper's first pass, then one per hand-off to it")
        refusal = [line for line in result.stderr.splitlines() if line.startswith("gh shim:")]
        self.assertEqual(len(refusal), 1, result.stderr)
        self.assertIn(f"{MAX_HOPS} times through this shim", refusal[0])
        self.assertIn(f"a gh on PATH ({os.pathsep.join(map(str, path))})", refusal[0])

    def test_with_no_sha256sum_the_hop_count_stops_the_loop(self):
        """No sha256sum on PATH, so no digest of the call: the shim run by its path, a wrapper above it and PATH holding
        only that wrapper and a real gh. The same call cannot be told, and the hop count bash keeps ends the loop."""
        path = [self.wrap, self.real]
        self.assertIsNone(shutil.which("sha256sum", path=os.pathsep.join(map(str, path))))
        result = self.run_gh(path, "api", "user", by_path=True)
        self.assert_stopped_by_the_hop_count(result, path)

    def test_a_wrapper_that_changes_the_call_on_each_pass_is_stopped_by_the_hop_count(self):
        """Each pass adds an argument, so each hand-off is a new call that no digest matches."""
        self.wrapper(f'exec {self.stand_in} gh "$@" again')
        path = [self.wrap, self.real, "/usr/bin", "/bin"]
        result = self.run_gh(path, "api", "user", by_path=True)
        self.assert_stopped_by_the_hop_count(result, path)

    def test_the_hop_count_lets_every_hand_off_up_to_its_bound_through(self):
        """The control of the bound: calls nested one in another, each a call of its own (gh, git under it, its
        credential helper…), reach the real gh at every depth up to the bound, and the next one is refused."""
        executable(self.real / "gh", f'#!/bin/bash\nprintf "%s\\n" "$*" >> {self.real_log}\n'
                                     'if (($1 > 1)); then gh $(($1 - 1)) || exit $?; fi\nexit 0\n')
        path = [SHIMS, self.real, "/usr/bin", "/bin"]
        result = self.run_gh(path, str(MAX_HOPS))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.real_calls(), [str(n) for n in range(MAX_HOPS, 0, -1)])
        self.real_log.unlink()
        result = self.run_gh(path, str(MAX_HOPS + 1))
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertEqual(self.real_calls(), [str(n) for n in range(MAX_HOPS + 1, 1, -1)])
        self.assertIn(f"{MAX_HOPS} times through this shim", result.stderr)


if __name__ == "__main__":
    unittest.main()
