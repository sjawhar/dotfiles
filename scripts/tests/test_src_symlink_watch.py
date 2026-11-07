#!/usr/bin/env python3
"""Regression tests for src-symlink-watch: which links widen a box, and which do not.

A symlink in ~/src whose target is outside ~/src is mounted into every agent box on the machine.
One inside ~/src is not: the box already has the tree it points into. Reporting the second kind
would page the box-health owner for every ordinary worktree link, and missing the first kind is
how five lanes' private trees sat in 82 boxes for up to twelve days (dispatch://INFRA-675).

The first-run case is here too: a watch that pages about everything it finds the first time it
runs is a watch someone turns off.
"""
import importlib.machinery
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
import unittest.mock

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "src-symlink-watch"
LOADER = importlib.machinery.SourceFileLoader("src_symlink_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
watch = importlib.util.module_from_spec(SPEC)
sys.modules[LOADER.name] = watch
LOADER.exec_module(watch)


class WhichLinksWidenABox(unittest.TestCase):
    def setUp(self) -> None:
        self.root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.src = self.root / "src"
        (self.src / "a-repo" / ".worktrees").mkdir(parents=True)
        (self.root / "elsewhere").mkdir()

    def test_a_link_out_of_src_is_reported(self) -> None:
        (self.src / "lane-tree").symlink_to(self.root / "elsewhere")
        self.assertEqual(
            watch.outside_links(self.src),
            {"lane-tree": str(self.root / "elsewhere")},
        )

    def test_a_link_inside_src_is_not(self) -> None:
        (self.src / "shortcut").symlink_to(self.src / "a-repo" / ".worktrees")
        self.assertEqual(watch.outside_links(self.src), {})

    def test_a_real_directory_is_not_a_link(self) -> None:
        (self.src / "ordinary").mkdir()
        self.assertEqual(watch.outside_links(self.src), {})

    def test_a_link_whose_target_is_gone_still_counts(self) -> None:
        # agentbox binds the target path; a dangling link is a path that will be created in the
        # box and lost with it, which is the worse half of the hazard, not an excuse to ignore it.
        (self.src / "dangling").symlink_to(self.root / "never-made")
        self.assertEqual(
            watch.outside_links(self.src), {"dangling": str(self.root / "never-made")}
        )

    def test_src_that_does_not_exist_is_empty_not_an_error(self) -> None:
        self.assertEqual(watch.outside_links(self.root / "no-such-src"), {})


class FirstRunSaysNothing(unittest.TestCase):
    def test_remembers_what_is_there_and_sends_no_notice(self) -> None:
        root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        src = root / "src"
        src.mkdir()
        (root / "target").mkdir()
        (src / "pre-existing").symlink_to(root / "target")
        state = root / "state"
        sent: list[tuple[str, str]] = []

        class NoEnvoy:
            def publish(self, topic: str, message: str) -> None:
                sent.append((topic, message))

        argv = ["src-symlink-watch", "--src", str(src), "--state", str(state)]
        with unittest.mock.patch.object(sys, "argv", argv), \
                unittest.mock.patch.object(watch, "Envoy", NoEnvoy):
            self.assertEqual(watch.main(), 0)
        self.assertEqual(sent, [], "a first run must not page about what was already there")
        remembered = json.loads((state / "src-symlink-watch.json").read_text(encoding="utf-8"))
        self.assertEqual(remembered, {"pre-existing": str(root / "target")})


if __name__ == "__main__":
    import unittest.mock  # noqa: F401

    unittest.main()
