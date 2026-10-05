#!/usr/bin/env python3
"""scripts/cld --profile: the config dir claude runs under, what a profile links, and the model route."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
CLD = DOTFILES / "scripts" / "cld"

# What .gitignore tracks under .claude, plus plugins; a trailing "/" marks a directory.
TRACKED = tuple(
    line.strip()[len("!.claude/"):]
    for line in (DOTFILES / ".gitignore").read_text(encoding="utf-8").splitlines()
    if line.strip().startswith("!.claude/")
)
SHARED = (*TRACKED, "plugins/")
PER_ACCOUNT = (".claude.json", ".credentials.json", "policy-limits.json", "remote-settings.json", "projects/")
GATEWAY = "https://middleman.hawk.internal.trajectorylabs.com/anthropic"
# The stub claude reports the --settings value it got and the credential variables it inherited.
STUB = """#!/bin/bash
echo "CONFIG=${CLAUDE_CONFIG_DIR:-unset}"
echo "ARGS=$*"
settings=unset
while (($#)); do [[ $1 == --settings ]] && settings=$2; shift; done
echo "SETTINGS=$settings"
echo "KEYS=${ANTHROPIC_API_KEY-unset} ${ANTHROPIC_AUTH_TOKEN-unset}"
"""


class CldProfiles(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.dotfiles = root / "dotfiles"
        self.dotfiles.mkdir()
        (self.dotfiles / "scripts").symlink_to(DOTFILES / "scripts")
        self.canonical = self.dotfiles / ".claude"
        self.canonical.mkdir()
        for name in SHARED + PER_ACCOUNT:
            if name.endswith("/"):
                (self.canonical / name).mkdir()
            else:
                (self.canonical / name).write_text("{}", encoding="utf-8")
        home = root / "home"
        (home / ".config").mkdir(parents=True)
        stubs = root / "bin"
        stubs.mkdir()
        claude = stubs / "claude"
        claude.write_text(STUB, encoding="utf-8")
        claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
        self.env = {
            "DOTFILES_DIR": str(self.dotfiles),
            "HOME": str(home),
            "PATH": f"{stubs}:/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(home / ".config"),
            "NATS_NKEY_SEED_FILE": "/dev/null",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def cld(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(CLD), *args],
            capture_output=True,
            text=True,
            env={**self.env, **extra},
            check=False,
            timeout=60,
        )

    def fields(self, result: subprocess.CompletedProcess[str]) -> dict[str, str]:
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(line.split("=", 1) for line in result.stdout.splitlines())

    def launched(self, result: subprocess.CompletedProcess[str]) -> tuple[str, str]:
        fields = self.fields(result)
        return fields["CONFIG"], fields["ARGS"]

    def test_default_profile_reaches_claude_through_the_hawk_gateway(self) -> None:
        keys = {"ANTHROPIC_API_KEY": "sk-ant-stale", "ANTHROPIC_AUTH_TOKEN": "stale-bearer"}
        for args, extra in (((), keys), (("--profile", "default"), keys), ((), {})):
            with self.subTest(args=args, env=extra):
                result = self.cld(*args, "-p", "hi", **extra)
                fields = self.fields(result)
                self.assertEqual(
                    json.loads(fields["SETTINGS"]),
                    {"apiKeyHelper": str(self.dotfiles / "scripts" / "hawk-token"), "env": {"ANTHROPIC_BASE_URL": GATEWAY}},
                )
                self.assertTrue(fields["ARGS"].endswith("-p hi"), fields["ARGS"])
                # A key in the environment outranks apiKeyHelper, and every tool subprocess would hold it.
                self.assertEqual(fields["KEYS"], "unset unset")
                for name in extra:
                    self.assertIn(name, result.stderr)
                self.assertNotIn("sk-ant-stale", result.stderr)

    def test_named_profile_keeps_its_own_login_and_keys(self) -> None:
        fields = self.fields(self.cld("--profile", "theorem", ANTHROPIC_API_KEY="exported-by-the-caller"))
        self.assertEqual(fields["SETTINGS"], "unset")
        self.assertEqual(fields["KEYS"], "exported-by-the-caller unset")

    def test_profile_runs_claude_in_its_own_dir_without_the_flag(self) -> None:
        profile = self.dotfiles / ".claude-profiles" / "theorem"
        for flag in (["--profile", "theorem"], ["--profile=theorem"]):
            with self.subTest(flag=flag):
                config, args = self.launched(self.cld("--resume", "abc", *flag, "-p", "hi"))
                self.assertEqual(config, str(profile))
                self.assertTrue(args.endswith("--resume abc -p hi"), args)
                self.assertNotIn("--profile", args)
                self.assertNotIn("theorem", args)

    def test_profile_shares_configuration_but_not_login_or_org_state(self) -> None:
        profile = self.dotfiles / ".claude-profiles" / "theorem"
        self.launched(self.cld("--profile", "theorem"))
        self.assertEqual(stat.S_IMODE(profile.stat().st_mode), 0o700)
        for name in SHARED:
            with self.subTest(shared=name):
                self.assertEqual(os.readlink(profile / name), str(self.canonical / name))
        for name in PER_ACCOUNT:
            with self.subTest(per_account=name):
                self.assertFalse((profile / name).exists() or (profile / name).is_symlink())

    def test_entries_already_in_a_profile_are_left_alone(self) -> None:
        profile = self.dotfiles / ".claude-profiles" / "own"
        profile.mkdir(parents=True)
        (profile / "CLAUDE.md").write_text("the profile's own", encoding="utf-8")
        self.launched(self.cld("--profile", "own"))
        self.assertFalse((profile / "CLAUDE.md").is_symlink())
        self.assertEqual((profile / "CLAUDE.md").read_text(encoding="utf-8"), "the profile's own")
        self.assertEqual(os.readlink(profile / "settings.json"), str(self.canonical / "settings.json"))

    def test_flag_beats_inherited_dir_which_beats_the_canonical_one(self) -> None:
        inherited = str(self.dotfiles / ".claude-profiles" / "parent")
        cases = [
            ((), {}, str(self.canonical)),
            ((), {"CLAUDE_CONFIG_DIR": inherited}, inherited),
            (("--profile", "default"), {"CLAUDE_CONFIG_DIR": inherited}, str(self.canonical)),
            (("--profile", "theorem"), {"CLAUDE_CONFIG_DIR": inherited}, str(self.dotfiles / ".claude-profiles" / "theorem")),
        ]
        for args, extra, expected in cases:
            with self.subTest(args=args, env=extra):
                config, _ = self.launched(self.cld(*args, **extra))
                self.assertEqual(config, expected)

    def test_inherited_named_profile_gets_no_gateway_route(self) -> None:
        inherited = str(self.dotfiles / ".claude-profiles" / "parent")
        self.assertEqual(self.fields(self.cld(CLAUDE_CONFIG_DIR=inherited))["SETTINGS"], "unset")

    def test_bad_profile_names_refuse_before_claude_runs(self) -> None:
        for args in (("--profile", ".."), ("--profile", ".hidden"), ("--profile", "a/b"), ("--profile",)):
            with self.subTest(args=args):
                result = self.cld(*args)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("CONFIG=", result.stdout)
                self.assertIn("cld:", result.stderr)
        self.assertFalse((self.dotfiles / ".claude-profiles").exists())


if __name__ == "__main__":
    unittest.main()
