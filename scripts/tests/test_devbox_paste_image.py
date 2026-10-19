#!/usr/bin/env python3
"""devbox-paste-image sends the image to the machine the focused window's title names, and only there."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "devbox-paste-image"

TAILNET = {
    "Self": {"HostName": "sami", "DNSName": "sami.tail12ec1c.ts.net."},
    "Peer": {
        "a": {"HostName": "sami-rocqperf", "DNSName": "sami-rocqperf.tail12ec1c.ts.net."},
        "b": {"HostName": "oryx", "DNSName": "oryx.tail12ec1c.ts.net."},
    },
}


def write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class PasteTarget(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.home = root / "home"
        self.home.mkdir()
        dotfiles = root / "dotfiles"
        (dotfiles / "scripts").mkdir(parents=True)
        (dotfiles / "voxtype").mkdir()
        stubs = root / "bin"
        stubs.mkdir()
        self.log = root / "calls.log"
        self.pasted = root / "pasted"
        (root / "tailnet.json").write_text(json.dumps(TAILNET), encoding="utf-8")
        write_executable(dotfiles / "scripts" / "focused-window-title", 'printf "%s\\n" "$STUB_TITLE"')
        write_executable(dotfiles / "voxtype" / "voxtype-paste", f'cat > "{self.pasted}"')
        write_executable(stubs / "hostname", "echo sami")
        write_executable(stubs / "tailscale", f'cat "{root / "tailnet.json"}"')
        write_executable(stubs / "notify-send", f'echo "notify $*" >> "{self.log}"')
        write_executable(
            stubs / "wl-paste",
            'if [ "${1:-}" = --list-types ]; then echo image/png; else printf PNGDATA; fi',
        )
        # The upload lands in a per-host directory, so a test sees which machine got the file.
        write_executable(
            stubs / "ssh",
            f'host="${{@: -2:1}}"; echo "ssh $host" >> "{self.log}"\n'
            f'HOME="{root}/remote/$host" bash -c "${{@: -1}}"',
        )
        self.remote = root / "remote"
        self.env = {
            **os.environ,
            "DOTFILES_DIR": str(dotfiles),
            "HOME": str(self.home),
            "PATH": f"{stubs}:{os.environ['PATH']}",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def paste(self, title: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT)], capture_output=True, text=True, env={**self.env, "STUB_TITLE": title}, check=False
        )

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def machines_with_image(self) -> set[str]:
        found = {p.parts[-5] for p in self.remote.glob("*/.cache/omp/pasted-images/*.png")}
        if list((self.home / ".cache/omp/pasted-images").glob("*.png")):
            found.add("laptop")
        return found

    def test_innermost_machine_gets_the_image_and_the_tilde_path(self) -> None:
        """A laptop tmux pane running mosh to a remote tmux: the remote is the target, not the laptop."""
        result = self.paste("sami: [mosh] sami-rocqperf: π omp")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.machines_with_image(), {"sami-rocqperf"})
        pasted = self.pasted.read_text(encoding="utf-8")
        self.assertRegex(pasted, r"^~/\.cache/omp/pasted-images/\d{8}-\d{6}\.png$")
        self.assertEqual((self.remote / "sami-rocqperf" / pasted[2:]).read_bytes(), b"PNGDATA")

    def test_shell_prompt_and_command_titles_name_the_machine(self) -> None:
        for title, machine in (
            ("[mosh] sami@sami-rocqperf: ~/src", "sami-rocqperf"),
            ("mosh agent@oryx", "oryx"),
            ("devbox sami-rocqperf -- tm", "sami-rocqperf"),
            ("ssh -p 2222 agent@oryx", "oryx"),
        ):
            with self.subTest(title=title):
                self.tearDown()
                self.setUp()
                result = self.paste(title)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.machines_with_image(), {machine})

    def test_a_title_naming_no_machine_is_the_laptop(self) -> None:
        """Including a pane title whose words look like "name: text" but name no tailnet machine."""
        for title in ("sami: π ⠧ DEVBOX: make devbox configurable", "Threads - Brave"):
            with self.subTest(title=title):
                self.tearDown()
                self.setUp()
                result = self.paste(title)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.machines_with_image(), {"laptop"})
                self.assertNotIn("ssh", " ".join(self.calls()))

    def test_a_remote_window_whose_machine_is_unknown_pastes_nothing(self) -> None:
        result = self.paste("sami: [mosh] π omp running without tmux")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.machines_with_image(), set())
        self.assertFalse(self.pasted.exists())
        self.assertTrue(any("Cannot tell which machine" in call for call in self.calls()), self.calls())

    def test_a_failed_upload_names_the_machine_and_pastes_nothing(self) -> None:
        stubs = Path(self.env["PATH"].split(":")[0])
        write_executable(stubs / "ssh", 'echo "ssh: connect to host sami-rocqperf: timed out" >&2; exit 255')
        result = self.paste("[mosh] sami-rocqperf: shell")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.pasted.exists())
        self.assertTrue(any("upload to sami-rocqperf failed" in call for call in self.calls()), self.calls())


if __name__ == "__main__":
    unittest.main()
