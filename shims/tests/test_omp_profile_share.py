#!/usr/bin/env python3
"""shims/omp: a profile copy that differs only in a write stamp is a duplicate, not divergence.

A named profile shares its state with the default through symlinks. A writer that renames a
temporary file over one of those paths replaces the link with a real file — that is how every
atomic writer behaves — and `share_links` then compares the two sides: equal content means the
copy is moved aside and the link remade, and a real difference stops the launch, because a
human must decide which copy is real.

On 2026-11-08 at 02:15:41Z that happened to the pbc profile's `marketplaces.json`, and the copy
differed from the default in a nested `updatedAt` alone. The comparison called that divergence,
so every omp launch under the profile refused from 02:15Z until the link was restored by hand
at 02:19Z; three review arms of the grading lane died on it.

`same_content` is tested here rather than `share_links`, because it is where the judgement is
and because running `share_links` means running the launcher. The function is read out of the
shim so the test cannot drift from the shipped text.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SHIM = DOTFILES / "shims" / "omp"

# The real specimen's shape: the stamp that broke it is nested inside `marketplaces`, not at the
# top level. A test that puts it at the top level passes while the real file still fails — the
# first reconstruction of this case did exactly that.
DEFAULT = {
    "version": 1,
    "marketplaces": {
        "legion-plugins": {"url": "https://example.invalid/legion", "updatedAt": "2026-11-08T02:17:45.524Z"},
    },
}
STAMP_ONLY = {
    "version": 1,
    "marketplaces": {
        "legion-plugins": {"url": "https://example.invalid/legion", "updatedAt": "2026-11-08T02:15:41.817Z"},
    },
}
REALLY_DIFFERENT = {
    "version": 1,
    "marketplaces": {
        "legion-plugins": {"url": "https://example.invalid/legion", "updatedAt": "2026-11-08T02:17:45.524Z"},
        "a-marketplace-the-profile-added": {"url": "https://example.invalid/other"},
    },
}


def same_content(a: Path, b: Path) -> bool:
    """Run the shim's own same_content, read out of the shipped file."""
    body = []
    inside = False
    for line in SHIM.read_text().splitlines():
        if line.startswith("same_content() {"):
            inside = True
        if inside:
            body.append(line)
            if line == "}":
                break
    assert body, "same_content was not found in shims/omp"
    script = "\n".join(body) + f'\nsame_content "{a}" "{b}"\n'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True).returncode == 0


class SameContent(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name: str, doc: dict) -> Path:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=1))
        return path

    def test_a_copy_differing_only_in_a_nested_write_stamp_is_the_same(self) -> None:
        default = self.write("default/marketplaces.json", DEFAULT)
        copy = self.write("profile/marketplaces.json", STAMP_ONLY)
        self.assertTrue(same_content(copy, default),
                        "a stale duplicate must relink, not stop every launch of the profile")

    def test_a_copy_the_profile_really_changed_still_stops_the_launch(self) -> None:
        default = self.write("default/marketplaces.json", DEFAULT)
        copy = self.write("profile/marketplaces.json", REALLY_DIFFERENT)
        self.assertFalse(same_content(copy, default),
                         "a real per-profile change must never be discarded silently")

    def test_identical_bytes_are_the_same(self) -> None:
        default = self.write("default/marketplaces.json", DEFAULT)
        copy = self.write("profile/marketplaces.json", DEFAULT)
        self.assertTrue(same_content(copy, default))

    def test_a_file_that_is_not_json_is_different(self) -> None:
        default = self.dir / "default.conf"
        copy = self.dir / "profile.conf"
        default.write_text("a\n")
        copy.write_text("b\n")
        self.assertFalse(same_content(copy, default),
                         "only JSON is compared as a document; anything else is bytes")

    def test_json_that_will_not_parse_is_different(self) -> None:
        default = self.write("default/marketplaces.json", DEFAULT)
        copy = self.dir / "profile" / "marketplaces.json"
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text("{ not json")
        self.assertFalse(same_content(copy, default))


if __name__ == "__main__":
    unittest.main()
