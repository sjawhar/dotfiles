"""pytest-expected-skips fails a run that ran less than it should, on reports pytest itself writes.

Each case writes a small test directory, runs pytest on it (the interpreter running this file, with the pytest it
imports) to get a real junit report, and runs the script on that report with a skip list. A report written by hand
would show only what its author believed the report holds.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "pytest-expected-skips"

SUITE = {
    "test_a.py": "import unittest\n\n\nclass A(unittest.TestCase):\n    def test_runs(self):\n        pass\n\n"
                 "    @unittest.skip('needs a thing')\n    def test_skips(self):\n        pass\n",
    "test_b.py": "import pytest\n\n\n@pytest.mark.parametrize('n', [1, 2])\ndef test_p(n):\n    pytest.skip('needs n')\n\n\n"
                 "def test_runs():\n    pass\n\n\n@pytest.mark.xfail(strict=True)\ndef test_expected_failure():\n"
                 "    assert False\n",
    "sub/test_c.py": "def test_runs():\n    pass\n",
}
LISTED = "# A reason:\nt/test_a.py::A::test_skips\n# Another reason:\nt/test_b.py::test_p\n"


def run(tmp: Path, files: dict[str, str], listed: str, *pytest_args: str) -> subprocess.CompletedProcess[str]:
    for name, text in files.items():
        (tmp / "t" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp / "t" / name).write_text(text, encoding="utf-8")
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "junit_family=xunit1",
                    "--junitxml=report.xml", "t", *pytest_args], cwd=tmp, capture_output=True, text=True)
    return subprocess.run([sys.executable, str(SCRIPT), "report.xml", "t"], cwd=tmp, capture_output=True, text=True,
                          env={**os.environ, "EXPECTED_SKIPS": listed})


def test_a_run_that_ran_what_it_should_passes(tmp_path):
    r = run(tmp_path, SUITE, LISTED)
    assert r.returncode == 0, r.stdout
    assert "    1 ran  t/sub/test_c.py" in r.stdout.splitlines()
    assert "    2 ran  t/test_b.py" in r.stdout.splitlines()  # test_runs, and the expected failure
    assert "7 collected, 4 ran, 3 skipped, in 3 files" in r.stdout


def test_a_skip_the_list_does_not_name_fails(tmp_path):
    r = run(tmp_path, SUITE, "# A reason:\nt/test_a.py::A::test_skips\n")
    assert r.returncode == 1
    assert "FAIL: t/test_b.py::test_p[1] skipped, and the skip list does not name it: needs n" in r.stdout


def test_a_listed_test_that_ran_fails(tmp_path):
    r = run(tmp_path, SUITE, LISTED + "t/test_a.py::A\n")
    assert r.returncode == 1
    assert "FAIL: t/test_a.py::A::test_runs ran, and the skip list names it (t/test_a.py::A)" in r.stdout


def test_an_entry_that_names_no_skip_fails(tmp_path):
    r = run(tmp_path, SUITE, LISTED + "t/test_a.py::A::test_renamed\n")
    assert r.returncode == 1
    assert "FAIL: the skip list names t/test_a.py::A::test_renamed, which did not skip" in r.stdout


@pytest.mark.parametrize("files,args", [
    ({**SUITE, "test_empty.py": "x = 1\n"}, ()),
    (SUITE, ("--ignore=t/sub/test_c.py",)),
    (SUITE, ("-k", "no_test_has_this_name")),
])
def test_a_file_that_ran_no_test_fails(tmp_path, files, args):
    r = run(tmp_path, files, LISTED, *args)
    assert r.returncode == 1
    assert "ran no test: it collected none, or skipped every one" in r.stdout


def test_no_report_is_no_pass(tmp_path):
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "report.xml", "t"], cwd=tmp_path, capture_output=True, text=True,
                       env={**os.environ, "EXPECTED_SKIPS": LISTED})
    assert r.returncode == 1
    assert "FAIL: cannot tell what ran: 1 test files under t, and the report report.xml is missing" in r.stdout


def test_an_entry_under_no_reason_is_refused(tmp_path):
    r = run(tmp_path, SUITE, "t/test_a.py::A::test_skips\n")
    assert (r.returncode, r.stdout.strip()) == (1, "FAIL: the skip list names t/test_a.py::A::test_skips under no reason")
