#!/usr/bin/env python3
"""A stream job gets the shims on its PATH (dispatch://INFRA-6).

A systemd USER unit inherits the manager's default PATH — no dotfiles shims and no mise shims.
`stream-job` passed its own environment straight through, so any job shelling out to `gh`, `jj`,
`knives` or a mise-managed tool failed, and failed as if the job were at fault.

Measured on 2026-11-07: the gate lane's daily publication scan ran four times in the 05Z hour and
died in about a second each, `gh auth git-credential get: 1: gh: not found`, then git could not
read a username for its fetch. The same script by hand from a pane takes 1,103 s and delivers.
Confirmed in a real unit: `systemd-run --user --wait --pipe /usr/bin/python3` prints
`/usr/local/sbin:…:/snap/bin` and finds no `gh`.

These pin the three things that made it wrong, and the one that would make it wrong again:
order, derivation, and not inventing directories that are not there.
"""
import importlib.machinery
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest
import unittest.mock

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "stream-job"
LOADER = importlib.machinery.SourceFileLoader("stream_job", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
stream_job = importlib.util.module_from_spec(SPEC)
sys.modules[LOADER.name] = stream_job
LOADER.exec_module(stream_job)

BARE = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin"
SHIMS = str((SCRIPT.parent / ".." / "shims").resolve())


class JobEnvPath(unittest.TestCase):
    def test_the_checkouts_shims_come_first(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"PATH": BARE}, clear=False):
            first = stream_job.job_env()["PATH"].split(os.pathsep)[0]
        self.assertEqual(first, SHIMS)

    def test_mise_shims_follow_and_are_taken_from_the_environment(self) -> None:
        data = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        (data / "shims").mkdir()
        with unittest.mock.patch.dict(
            os.environ, {"PATH": BARE, "MISE_DATA_DIR": str(data)}, clear=False
        ):
            parts = stream_job.job_env()["PATH"].split(os.pathsep)
        self.assertEqual(parts[1], str(data / "shims"))

    def test_the_inherited_path_is_kept_after_them(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"PATH": BARE}, clear=False):
            parts = stream_job.job_env()["PATH"].split(os.pathsep)
        for entry in BARE.split(os.pathsep):
            self.assertIn(entry, parts)

    def test_a_directory_that_does_not_exist_is_not_added(self) -> None:
        # A machine with no mise must not get a PATH entry pointing at nothing: a bogus entry
        # costs every lookup a stat and hides a real misconfiguration behind a plausible string.
        missing = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory())) / "gone"
        with unittest.mock.patch.dict(
            os.environ, {"PATH": BARE, "MISE_DATA_DIR": str(missing)}, clear=False
        ):
            parts = stream_job.job_env()["PATH"].split(os.pathsep)
        self.assertNotIn(str(missing / "shims"), parts)

    def test_the_rest_of_the_environment_is_passed_through(self) -> None:
        with unittest.mock.patch.dict(
            os.environ, {"PATH": BARE, "SRE_MARKER": "kept"}, clear=False
        ):
            env = stream_job.job_env()
        self.assertEqual(env["SRE_MARKER"], "kept")

    def test_the_shims_are_not_duplicated_when_already_present(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"PATH": f"{SHIMS}:{BARE}"}, clear=False):
            parts = stream_job.job_env()["PATH"].split(os.pathsep)
        self.assertEqual(parts.count(SHIMS), 1)


class AJobThatRunsSeesTheShims(unittest.TestCase):
    """The wiring, not the helper.

    Deleting `env=job_env()` from the subprocess call leaves every test above green, because
    each one calls job_env() directly. This one runs stream-job over a job directory of its own
    and reads what the job itself saw, so the test fails if the environment is not passed.
    """

    def test_the_job_resolves_gh_through_the_shims(self) -> None:
        import subprocess

        root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        jobs, state, work = root / "jobs", root / "state", root / "work"
        for d in (jobs, state, work):
            d.mkdir()
        (jobs / "sre-path-probe.job").write_text(
            "owner=01a18385-f8c8-74a9-b6ff-694fb5fe87a7\n"
            "command=command -v gh || echo GH-NOT-FOUND\n"
            f"workdir={work}\n"
            "every=1s\n"
            "timeout=30s\n"
            "persistent=no\n",
            encoding="utf-8",
        )
        env = {**os.environ, "PATH": BARE}
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--jobs", str(jobs), "--state", str(state)],
            capture_output=True, text=True, timeout=120, env=env,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        recorded = (state / "sre-path-probe" / "last-output").read_text(encoding="utf-8")
        self.assertNotIn("GH-NOT-FOUND", recorded, f"the job ran without the shims: {recorded!r}")
        self.assertTrue(recorded.strip().endswith("/shims/gh"), recorded)


if __name__ == "__main__":
    import unittest.mock  # noqa: F401

    unittest.main()
