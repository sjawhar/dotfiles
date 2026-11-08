#!/usr/bin/env python3
"""An agent's gh WRITE never goes out on the user's personal access token unapproved.

gh-app-token answers an owner the GitHub App is not installed on with the profile's fallback
secret, the user's own PAT. So in an agent session a write to such an owner posts under the
user's name, and to the outside it reads as the user writing it. On 2026-11-08T17:50:32Z an
agent posted https://github.com/FreeMixer/plugin-hostd/issues/371#issuecomment-6724942068 that
way: FreeMixer has no installation, and shims/gh's approval check runs only when a call opts in
with GH_SHIM_APPROVAL (dispatch://INFRA-2006).

shims/gh now asks gh-app-token which identity the owner resolves to (`--source`) before a write
in an agent session, and refuses the fallback unless an approved ask already passed. These tests
drive the shim with a stub gh-app-token and a stub knives, so nothing contacts GitHub: the thing
asserted is whether the call ever reaches knives at all.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).resolve().parents[1]
GH_SHIM = SHIMS / "gh"


def stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class FallbackWriteGate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gh-fallback-gate-"))
        self.bin = self.tmp / "bin"
        self.reached = self.tmp / "reached-knives"
        # knives records that the call got past the gate, and runs nothing.
        stub(self.bin, "knives", f'printf "%s\\n" "$*" >> {self.reached}; exit 0')
        # A real-looking gh for real_gh(): it must NOT carry the shim marker.
        stub(self.bin, "gh", 'exit 0')
        # git exists so the profile lookup works; it finds no config in the scratch HOME.
        self.env = {
            "PATH": os.pathsep.join([str(self.bin), "/usr/bin", "/bin"]),
            "HOME": str(self.tmp),
            "XDG_CONFIG_HOME": str(self.tmp / "config"),
            "TMPDIR": str(self.tmp),
            "DOTFILES_DIR": str(SHIMS.parent),
            "GH_READ_CACHE": "0",
            # The public-text gate is a different control and refuses first in a scratch HOME;
            # these tests are about the token identity, so it is satisfied here.
            "PUBLIC_LEAK_CHECK": "/bin/true",
            "GH_READ_CACHE_LOG": "0",
            # Marks this process as an agent session, as omp does.
            "PI_SESSION_FILE": str(self.tmp / "session.jsonl"),
        }

    def tearDown(self) -> None:
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def source(self, answer: str) -> None:
        """Make gh-app-token answer `answer` for every owner."""
        stub(self.bin, "gh-app-token", f'printf "%s\\n" "{answer}"; exit 0')

    def run_gh(self, argv: list[str], **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(GH_SHIM), *argv], env={**self.env, **extra},
                              capture_output=True, text=True, cwd=self.tmp, timeout=60, check=False)

    def reached_knives(self) -> bool:
        return self.reached.exists() and self.reached.read_text().strip() != ""

    # --- the incident -----------------------------------------------------

    def test_a_write_to_an_uncovered_owner_is_refused(self):
        """The 2026-11-08 post: a comment on an owner with no App installation."""
        self.source("fallback-personal-token")
        result = self.run_gh(["issue", "comment", "371", "-R", "FreeMixer/plugin-hostd",
                              "--body", "a comment"])
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.reached_knives(), "the call reached knives; the gate did not hold")
        self.assertIn("FreeMixer", result.stderr)
        self.assertIn("personal access token", result.stderr)

    def test_the_refusal_names_the_approval_route(self):
        self.source("fallback-personal-token")
        result = self.run_gh(["pr", "create", "-R", "FreeMixer/plugin-hostd", "--title", "t",
                              "--body", "b"])
        self.assertIn("GH_SHIM_APPROVAL", result.stderr)

    # --- what must keep working ------------------------------------------

    def test_a_read_of_the_same_owner_is_untouched(self):
        self.source("fallback-personal-token")
        self.run_gh(["issue", "view", "371", "-R", "FreeMixer/plugin-hostd"])
        self.assertTrue(self.reached_knives(), "a read was gated; reads must be unaffected")

    def test_a_write_to_a_covered_owner_is_untouched(self):
        self.source("app")
        self.run_gh(["issue", "comment", "1", "-R", "theorem-labs/agent-infra", "--body", "x"])
        self.assertTrue(self.reached_knives(), "a write on the App's own token was gated")

    def test_a_non_agent_shell_is_untouched(self):
        """A person's own shell is not an agent session and keeps the old behaviour."""
        self.source("fallback-personal-token")
        env = dict(self.env)
        env.pop("PI_SESSION_FILE")
        result = subprocess.run([str(GH_SHIM), "issue", "comment", "371", "-R",
                                 "FreeMixer/plugin-hostd", "--body", "x"],
                                env=env, capture_output=True, text=True, cwd=self.tmp,
                                timeout=60, check=False)
        self.assertNotIn("personal access token", result.stderr, result.stderr)

    # --- the classifier ---------------------------------------------------

    @staticmethod
    def classifier_source() -> str:
        """The gh_shim_is_write function, lifted out of the shim.

        Sourcing shims/gh runs it, so the function is extracted the way
        test_agentbox_bind_check.py extracts its block: by text, from the real file, so a
        change to the shim is what these cases are checked against.
        """
        text = GH_SHIM.read_text(encoding="utf-8")
        start = text.index("gh_shim_is_write() {")
        end = text.index("\n}\n", start) + len("\n}\n")
        return text[start:end]

    def classify(self, argv: list[str]) -> bool:
        script = (self.classifier_source() + "\ngh_shim_is_write " +
                  " ".join(f"'{a}'" for a in argv) + "; echo RC=$?")
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                             timeout=60, check=False).stdout
        return "RC=0" in out

    def test_write_verbs_are_writes(self):
        for argv in (["issue", "comment"], ["issue", "create"], ["pr", "merge"], ["pr", "review"],
                     ["pr", "edit"], ["pr", "close"], ["release", "create"], ["workflow", "run"],
                     ["api", "-X", "POST", "repos/o/r/issues"],
                     ["api", "--method", "DELETE", "repos/o/r"],
                     ["api", "repos/o/r/issues", "-f", "title=x"]):
            with self.subTest(argv=argv):
                self.assertTrue(self.classify(argv), f"{argv} should be a write")

    def test_read_verbs_are_not_writes(self):
        for argv in (["issue", "view"], ["issue", "list"], ["pr", "view"], ["pr", "diff"],
                     ["repo", "view"], ["run", "view"], ["api", "repos/o/r"],
                     ["api", "-X", "GET", "repos/o/r"]):
            with self.subTest(argv=argv):
                self.assertFalse(self.classify(argv), f"{argv} should not be a write")


if __name__ == "__main__":
    unittest.main()
