#!/usr/bin/env python3
"""shims/basedpyright and shims/pyright reach mise's basedpyright when no venv has one.

Technique: a fake mise data dir holding the "release" basedpyright, and a `mise` stub that
resolves the command the way `mise exec` does (mise 2026.8.10, src/cli/exec.rs): on PATH, with the
tool dir it adds ahead of the PATH it was given, while a tool dir already on that PATH keeps its
place. The shims dir sits ahead of the inherited install dir, as in a login shell.
"""

from __future__ import annotations

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SHIMS = DOTFILES / "shims"
LOOP_TIMEOUT = 10

MISE_EXEC = r"""
[[ "$1" == exec && "$3" == -- ]] || { echo "unexpected mise $*" >&2; exit 99; }
tool="$MISE_DATA_DIR/installs/npm-basedpyright/1.0.0/bin"
case ":$PATH:" in
    *":$tool:"*) lookup="$PATH" ;;
    *) lookup="$tool:$PATH" ;;
esac
program="$(PATH="$lookup" command -v "$4")"
PATH="$lookup" exec "$program" "${@:5}"
"""


def write_stub(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class PyrightShims(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.stub_dir = self.root / "bin"
        write_stub(self.stub_dir, "mise", MISE_EXEC)
        self.mise_data = self.root / "mise"
        self.release_dir = self.mise_data / "installs" / "npm-basedpyright" / "1.0.0" / "bin"
        write_stub(self.release_dir, "basedpyright", 'echo "RELEASE basedpyright $*"')
        self.work = self.root / "work"
        self.work.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_an_install_dir_already_on_path_still_reaches_the_release(self) -> None:
        path = f"{self.stub_dir}:{SHIMS}:{self.release_dir}:/usr/bin:/bin"
        for shim in ("basedpyright", "pyright"):
            with self.subTest(shim=shim):
                try:
                    result = subprocess.run(
                        [str(SHIMS / shim), "--version"],
                        capture_output=True,
                        text=True,
                        cwd=self.work,
                        env={
                            "HOME": str(self.root),
                            "MISE_DATA_DIR": str(self.mise_data),
                            "PATH": path,
                        },
                        check=False,
                        timeout=LOOP_TIMEOUT,
                    )
                except subprocess.TimeoutExpired:
                    self.fail(
                        f"shims/{shim} was still running after {LOOP_TIMEOUT}s: mise execs it again"
                    )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "RELEASE basedpyright --version\n")


if __name__ == "__main__":
    unittest.main()
