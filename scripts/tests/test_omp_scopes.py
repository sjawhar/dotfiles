#!/usr/bin/env python3
"""shims/omp loads an MCP server or a skill only where omp/scopes.yml says it is used, and starts omp
without CLAUDE_CONFIG_DIR for every profile it sets up.

A session's context is its profile when that is `personal` or `theorem`, else the word in the
machine's $XDG_CONFIG_HOME/omp/context. No file, or a word that is neither, is UNKNOWN, which loads
every context-scoped entry. A `host:` entry loads only on that short hostname, in every context. The
shim builds the profile's mcp.json from omp/mcp.json without the servers excluded here, and names a
settings overlay right after the dotfiles overlays whose skills.ignoredSkills is omp/config.yml's
list, the machine's local overlay's, and every skill excluded here."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.tests import test_omp_profile_links as links

DOTFILES = links.DOTFILES
REAL_YQ = links.REAL_YQ
# The short hostname, as the shim reads it (`hostname -s`), and one that is not this machine's.
HOST = os.uname().nodename.split(".")[0]
ELSEWHERE = f"not-{HOST}"

SERVERS: dict[str, dict[str, object]] = {
    "everywhere": {"command": "true"},
    "here": {"command": "here"},
    "elsewhere": {"command": "elsewhere"},
    "work": {"type": "http", "url": "https://work.example/mcp"},
    "home": {"command": "home", "args": ["--flag"]},
}
MANIFEST = f"""# The test's omp/scopes.yml.
mcp:
  here: {{host: "{HOST}"}}
  elsewhere: {{host: "{ELSEWHERE}"}}
  work: {{context: theorem}}
  home: {{context: personal}}
  # A plugin's server, which omp/mcp.json does not define.
  "plugin:work": {{context: theorem}}
skills:
  here-skill: {{host: "{HOST}"}}
  elsewhere-skill: {{host: "{ELSEWHERE}"}}
  "work-*": {{context: theorem}}
  home-skill: {{context: personal}}
"""
# omp/mcp.json's own denylist, which the built file keeps.
DISABLED = ["kept"]
CONFIG_IGNORED = ["config-a", "config-b"]
UNKNOWN = "UNKNOWN"
# Per context: the servers a profile's mcp.json keeps, the servers its disabledServers adds after
# omp/mcp.json's own, and the skills its overlay adds after the configured ones. Host entries count
# in every context.
LOADS: dict[str, tuple[set[str], list[str], list[str]]] = {
    "personal": ({"everywhere", "here", "home"}, ["elsewhere", "work", "plugin:work"], ["elsewhere-skill", "work-*"]),
    "theorem": ({"everywhere", "here", "work"}, ["elsewhere", "home"], ["elsewhere-skill", "home-skill"]),
    UNKNOWN: ({"everywhere", "here", "work", "home"}, ["elsewhere"], ["elsewhere-skill"]),
}


def yaml_json(path: Path) -> object:
    """PATH's YAML as JSON, read by the yq the shim uses."""
    return json.loads(subprocess.run([REAL_YQ, "-o=json", ".", str(path)], capture_output=True, text=True,
                                     check=True).stdout)


def fields_of(stdout: str) -> dict[str, str]:
    """The stub omp's lines, each `NAME VALUE`, as a mapping."""
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        name, _, value = line.partition(" ")
        fields[name] = value
    return fields


class OmpScopes(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.scratch = links.default_layout(self.root)
        self.dot = self.scratch.dotfiles / "omp"
        (self.dot / "config.yml").unlink()
        (self.dot / "config.yml").write_text(
            "theme:\n  dark: titanium\nskills:\n  ignoredSkills:\n    - config-a\n    - config-b\n", encoding="utf-8")
        (self.dot / "mcp.json").write_text(
            json.dumps({"$schema": "x", "mcpServers": SERVERS, "disabledServers": DISABLED}, indent=2) + "\n",
            encoding="utf-8")
        (self.dot / "scopes.yml").write_text(MANIFEST, encoding="utf-8")
        self.caller = self.root / "caller.yml"
        self.caller.write_text("{}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def context_file(self, text: str | None) -> None:
        """The machine's context file holds TEXT, or there is none."""
        path = self.scratch.home / ".config" / "omp" / "context"
        path.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(text, encoding="utf-8")

    def agent(self, profile: str | None) -> Path:
        return self.scratch.omp / "agent" if profile is None else self.scratch.omp / "profiles" / profile / "agent"

    def omp(self, *args: str, **extra: str) -> dict[str, str]:
        """Launch the shim; omp's view of the launch, a field per line the stub omp printed."""
        result = self.scratch.run(*args, **extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        fields = fields_of(result.stdout)
        self.assertIn("AGENT", fields, result.stdout)
        return fields

    def generated(self, profile: str | None, fields: dict[str, str], local: Path | None = None,
                  caller: tuple[str, ...] = ()) -> Path:
        """omp's overlay list is omp/config.yml, LOCAL when given, one overlay the shim built in the
        profile's agent directory, then CALLER's entries; that overlay."""
        entries = fields["CONFIG"].split(":")
        head = [str(self.dot / "config.yml"), *([str(local)] if local else [])]
        self.assertEqual(entries[:len(head)], head, fields["CONFIG"])
        self.assertEqual(entries[len(head) + 1:], list(caller), fields["CONFIG"])
        overlay = Path(entries[len(head)])
        self.assertEqual(overlay.parent, self.agent(profile), fields["CONFIG"])
        return overlay

    def ignored(self, overlay: Path) -> list[str]:
        """The overlay's skills.ignoredSkills; it sets nothing else, so it overrides nothing else."""
        data = yaml_json(overlay)
        assert isinstance(data, dict)
        self.assertEqual(list(data), ["skills"])
        self.assertEqual(list(data["skills"]), ["ignoredSkills"])
        return data["skills"]["ignoredSkills"]

    def mcp(self, profile: str | None) -> dict[str, object]:
        path = self.agent(profile) / "mcp.json"
        self.assertFalse(path.is_symlink(), path)
        data = links.read_json(path)
        assert isinstance(data, dict)
        return data

    def servers(self, profile: str | None) -> object:
        return self.mcp(profile)["mcpServers"]

    def assert_scoped(self, profile: str | None, fields: dict[str, str], context: str) -> None:
        """PROFILE's launch loaded what CONTEXT loads: its mcp.json keeps those servers, each as
        omp/mcp.json defines it, and denies the excluded ones from any source; its overlay ignores
        the configured skills and the excluded ones."""
        servers, disabled, skills = LOADS[context]
        self.assertEqual(self.servers(profile), {name: SERVERS[name] for name in sorted(servers)})
        self.assertEqual(self.mcp(profile)["disabledServers"], DISABLED + disabled)
        self.assertEqual(self.ignored(self.generated(profile, fields)), CONFIG_IGNORED + skills)

    def test_the_profile_name_is_the_context(self) -> None:
        for profile, machine in (("personal", "theorem\n"), ("theorem", "personal\n")):
            with self.subTest(profile=profile):
                self.context_file(machine)
                self.assert_scoped(profile, self.omp("--profile", profile), profile)

    def test_the_machine_file_is_the_context_of_any_other_profile(self) -> None:
        for word in ("personal", "theorem"):
            for profile in (None, "other"):
                with self.subTest(word=word, profile=profile):
                    self.context_file(f"  {word}\n")
                    args = ("--profile", profile) if profile else ()
                    self.assert_scoped(profile, self.omp(*args), word)

    def test_an_unknown_context_loads_every_context_entry(self) -> None:
        for text in (None, "", "work\n"):
            for profile in (None, "other"):
                with self.subTest(file=text, profile=profile):
                    self.context_file(text)
                    args = ("--profile", profile) if profile else ()
                    self.assert_scoped(profile, self.omp(*args), UNKNOWN)

    def test_a_host_entry_loads_only_on_its_host_in_every_context(self) -> None:
        for profile, text in (("personal", None), ("theorem", None), (None, None), (None, "personal")):
            with self.subTest(profile=profile, file=text):
                self.context_file(text)
                fields = self.omp(*(("--profile", profile) if profile else ()))
                servers = self.servers(profile)
                self.assertIn("here", servers)
                self.assertNotIn("elsewhere", servers)
                skills = self.ignored(self.generated(profile, fields))
                self.assertIn("elsewhere-skill", skills)
                self.assertNotIn("here-skill", skills)

    def test_an_agent_box_takes_its_machines_name(self) -> None:
        """A box shares its machine's ~/.omp, so it builds the same files under the machine's name
        (AGENTBOX_HOST, as for ENVOY_MACHINE_ID), not the box's own hostname."""
        fields = self.omp(AGENTBOX_HOST=f"{ELSEWHERE}.tail.example")
        servers = self.servers(None)
        self.assertIn("elsewhere", servers)
        self.assertNotIn("here", servers)
        skills = self.ignored(self.generated(None, fields))
        self.assertIn("here-skill", skills)
        self.assertNotIn("elsewhere-skill", skills)

    def test_the_defaults_link_becomes_a_built_mcp_json_that_writes_do_not_cross(self) -> None:
        """installers/omp.sh linked ~/.omp/agent/mcp.json to omp/mcp.json; a write through that
        link (omp's own /mcp disable) changed the tracked file for every session."""
        shared = self.dot / "mcp.json"
        before = shared.read_bytes()
        (self.agent(None) / "mcp.json").symlink_to(shared)
        self.context_file("personal\n")
        self.assert_scoped(None, self.omp(), "personal")
        own = '{"mcpServers": {}}\n'
        (self.agent(None) / "mcp.json").write_text(own, encoding="utf-8")
        self.assertEqual(shared.read_bytes(), before)
        self.omp()
        self.assertEqual((self.agent(None) / "mcp.json").read_text(encoding="utf-8"), own)
        self.assertEqual(shared.read_bytes(), before)

    def test_the_overlay_adds_the_local_overlays_ignored_skills(self) -> None:
        self.context_file("personal\n")
        for profile, name in ((None, "config.local.yml"), ("other", "config.other.local.yml")):
            with self.subTest(profile=profile):
                local = self.dot / name
                local.write_text("skills:\n  ignoredSkills:\n    - local-a\n    - config-a\n", encoding="utf-8")
                args = ("--profile", profile) if profile else ()
                fields = self.omp(*args, PI_CONFIG_FILES=str(self.caller))
                overlay = self.generated(profile, fields, local, (str(self.caller),))
                self.assertEqual(self.ignored(overlay), CONFIG_IGNORED + ["local-a", "elsewhere-skill", "work-*"])
                # A local overlay that sets no list adds none.
                local.write_text("theme:\n  dark: other\n", encoding="utf-8")
                overlay = self.generated(profile, self.omp(*args), local)
                self.assertEqual(self.ignored(overlay), CONFIG_IGNORED + ["elsewhere-skill", "work-*"])
                local.unlink()

    def test_a_nested_launch_names_one_overlay(self) -> None:
        """A session's PI_CONFIG_FILES reaches every omp it starts. The child drops the overlay its
        parent named and names its own; a child on a marked profile names none."""
        self.context_file("personal\n")
        parent = self.omp(PI_CONFIG_FILES=str(self.caller))
        self.generated(None, parent, caller=(str(self.caller),))
        child = self.omp(PI_CONFIG_FILES=parent["CONFIG"])
        self.generated(None, child, caller=(str(self.caller),))
        other = self.omp("--profile", "theorem", PI_CONFIG_FILES=parent["CONFIG"])
        self.assertEqual(self.ignored(self.generated("theorem", other, caller=(str(self.caller),))),
                         CONFIG_IGNORED + LOADS["theorem"][2])
        grandchild = self.omp("--profile", "theorem", PI_CONFIG_FILES=other["CONFIG"])
        self.generated("theorem", grandchild, caller=(str(self.caller),))
        (self.scratch.omp / "profiles" / "m").mkdir(parents=True)
        (self.scratch.omp / "profiles" / "m" / ".no-shared-setup").touch()
        marked = self.omp("--profile", "m", PI_CONFIG_FILES=parent["CONFIG"])
        self.assertEqual(marked["CONFIG"].split(":"), [str(self.dot / "config.yml"), str(self.caller)])

    def test_claude_config_dir_is_dropped_for_every_profile_the_shim_sets_up(self) -> None:
        """With CLAUDE_CONFIG_DIR set, omp reads Claude Code's plugin registry and loads its stale
        plugin copies as duplicate skills. A marked profile's owner manages its setup and keeps it."""
        claude = str(self.root / "claude")
        links.shared_profile(self.scratch, "shared")
        (self.scratch.omp / "profiles" / "m").mkdir(parents=True)
        (self.scratch.omp / "profiles" / "m" / ".no-shared-setup").touch()
        for args, expected in (((), "unset"), (("--profile", "theorem"), "unset"), (("--profile", "shared"), "unset"),
                               (("--profile", "m"), claude)):
            with self.subTest(args=args):
                self.assertEqual(self.omp(*args, CLAUDE_CONFIG_DIR=claude)["CLAUDE"], expected)

    def test_a_shared_state_profile_builds_its_own_mcp_json(self) -> None:
        """A profile with models.overlay.yml links every other entry to the default's; its mcp.json
        and overlay are its own, for its own context, and a second launch still starts."""
        self.context_file("personal\n")
        self.assert_scoped(None, self.omp(), "personal")
        links.shared_profile(self.scratch, "theorem")
        for launch in ("first", "second"):
            with self.subTest(launch=launch):
                self.assert_scoped("theorem", self.omp("--profile", "theorem"), "theorem")
        self.assert_scoped(None, self.omp(), "personal")

    def rules(self) -> Path:
        """The rules file every session reads: .claude/CLAUDE.md of the launch's checkout."""
        return self.scratch.dotfiles / ".claude" / "CLAUDE.md"

    def test_one_launch_links_the_users_rules_into_a_fresh_home(self) -> None:
        """Most machines take a change through scripts/dotfiles-follow, which never runs
        installers/omp.sh, so the shim links ~/.omp/agent/SAMI-RULES.md to .claude/CLAUDE.md itself
        (omp/config.yml names it in contextFiles.extra) before it drops CLAUDE_CONFIG_DIR. A mirrored
        profile then holds a copy of it and a profile with models.overlay.yml a link to it."""
        claude = str(self.root / "claude")
        link = self.agent(None) / "SAMI-RULES.md"
        self.assertFalse(os.path.lexists(link))
        self.assertEqual(self.omp("--profile", "theorem", CLAUDE_CONFIG_DIR=claude)["CLAUDE"], "unset")
        self.assertEqual(links.link_target(link), str(self.rules()))
        self.assertFalse((self.agent("theorem") / "SAMI-RULES.md").is_symlink())
        self.assertEqual((self.agent("theorem") / "SAMI-RULES.md").read_bytes(), self.rules().read_bytes())
        self.assertEqual(self.omp(CLAUDE_CONFIG_DIR=claude)["CLAUDE"], "unset")
        links.shared_profile(self.scratch, "shared")
        self.assertEqual(self.omp("--profile", "shared", CLAUDE_CONFIG_DIR=claude)["CLAUDE"], "unset")
        self.assertEqual(links.link_target(self.agent("shared") / "SAMI-RULES.md"), str(link))

    def test_a_launch_without_readable_rules_keeps_claude_config_dir(self) -> None:
        """With no .claude/CLAUDE.md to link to, CLAUDE_CONFIG_DIR is how the session gets Sami's
        rules: the launch keeps it and says so once."""
        claude = str(self.root / "claude")
        (self.scratch.dotfiles / ".claude").unlink()
        (self.scratch.dotfiles / ".claude").mkdir()
        for args in ((), ("--profile", "theorem")):
            with self.subTest(args=args):
                result = self.scratch.run(*args, CLAUDE_CONFIG_DIR=claude)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(fields_of(result.stdout)["CLAUDE"], claude)
                self.assertEqual(result.stderr.count("CLAUDE_CONFIG_DIR"), 1, result.stderr)

    def test_a_rules_file_someone_put_there_stays_and_a_stale_link_moves(self) -> None:
        link = self.agent(None) / "SAMI-RULES.md"
        link.write_text("# Own rules\n", encoding="utf-8")
        self.assertEqual(self.omp(CLAUDE_CONFIG_DIR=str(self.root / "claude"))["CLAUDE"], "unset")
        self.assertFalse(link.is_symlink())
        self.assertEqual(link.read_text(encoding="utf-8"), "# Own rules\n")
        link.unlink()
        link.symlink_to(self.root / "old-checkout" / ".claude" / "CLAUDE.md")
        self.omp()
        self.assertEqual(links.link_target(link), str(self.rules()))

    def test_without_a_working_yq_a_launch_starts_unscoped_and_says_so(self) -> None:
        # A failing stub, not a missing one: a runner image can carry its own yq further down PATH
        # (GitHub's ubuntu-24.04 has /usr/bin/yq), which a removed stub would let the shim find.
        links.write_stub(self.scratch.stubs, "yq", "exit 1")
        self.context_file("personal\n")
        for profile in (None, "theorem"):
            with self.subTest(profile=profile):
                result = self.scratch.run(*(("--profile", profile) if profile else ()))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("yq", result.stderr)
                self.assertEqual(fields_of(result.stdout)["CONFIG"], str(self.dot / "config.yml"))
                self.assertEqual((self.agent(profile) / "mcp.json").read_bytes(), (self.dot / "mcp.json").read_bytes())

    def test_concurrent_launches_never_meet_a_partial_file(self) -> None:
        """Each omp keeps reading its mcp.json and overlay until every launch has started omp, so
        every rebuild after its own happens while it reads. A rebuild that wrote in place (truncate,
        then write) shows them empty for the length of a yq run."""
        tool = next((self.scratch.home / ".mise" / "installs").glob("*/*/bin"))
        links.write_stub(tool, "omp", """agent="$HOME/.omp/agent"
[[ -n "${OMP_PROFILE:-}" ]] && agent="$HOME/.omp/profiles/$OMP_PROFILE/agent"
overlay=""
IFS=: read -ra entries <<<"$PI_CONFIG_FILES"
for entry in "${entries[@]}"; do [[ "$entry" != "$agent"/* ]] || overlay="$entry"; done
[[ -z "${STARTED:-}" ]] || echo started >>"$STARTED"
exec python3 - "$agent/mcp.json" "$overlay" <<'PY'
import json, os, sys, time
mcp, overlay = sys.argv[1:]
started, launches = os.environ.get("STARTED"), int(os.environ.get("LAUNCHES", "0"))
bad = set()
deadline = time.monotonic() + 30
while True:
    try:
        json.load(open(mcp))["mcpServers"]
    except Exception:
        bad.add("mcp.json")
    try:
        if not open(overlay).read().startswith("skills:"):
            bad.add("overlay")
    except Exception:
        bad.add("overlay")
    if not started or len(open(started).read().split()) >= launches or time.monotonic() > deadline:
        break
    time.sleep(0.001)
print("partial: " + " ".join(sorted(bad)) if bad else "whole")
PY""")
        self.context_file("personal\n")
        for env in ({}, {"OMP_PROFILE": "theorem"}):
            self.scratch.run(**env)
        before = {p: sorted(os.listdir(self.agent(p))) for p in (None, "theorem")}
        started = self.root / "started"
        envs = [{}, {"OMP_PROFILE": "theorem"}] * 12
        count = {"STARTED": str(started), "LAUNCHES": str(len(envs))}
        with ThreadPoolExecutor(len(envs)) as pool:
            futures = [pool.submit(self.scratch.run, **env, **count) for env in envs[:2]]
            # The rest start once an omp reads, so their rebuilds happen under its reads.
            deadline = time.monotonic() + 30
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            futures += [pool.submit(self.scratch.run, **env, **count) for env in envs[2:]]
            results = [future.result() for future in futures]
        self.assertEqual(len(started.read_text(encoding="utf-8").split()), len(envs))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "whole\n", result.stderr)
        # No launch left a temporary file behind.
        self.assertEqual({p: sorted(os.listdir(self.agent(p))) for p in (None, "theorem")}, before)


class CommittedScopes(unittest.TestCase):
    def test_the_manifest_names_known_servers_and_scopes(self) -> None:
        """A server entry names a server of omp/mcp.json, or a plugin's as `<plugin>:<server>`."""
        manifest = yaml_json(DOTFILES / "omp" / "scopes.yml")
        assert isinstance(manifest, dict)
        servers = links.read_json(DOTFILES / "omp" / "mcp.json")
        assert isinstance(servers, dict)
        self.assertLessEqual(set(manifest), {"mcp", "skills"})
        for kind, entries in manifest.items():
            for name, entry in entries.items():
                with self.subTest(kind=kind, name=name):
                    if kind == "mcp" and ":" not in name:
                        self.assertIn(name, servers["mcpServers"])
                    self.assertTrue(entry, "an entry with no scope loads everywhere: leave it out")
                    self.assertLessEqual(set(entry), {"context", "host"})
                    self.assertIn(entry.get("context", "personal"), ("personal", "theorem"))
                    self.assertIsInstance(entry.get("host", ""), str)

    def test_chathub_is_a_user_server_and_the_project_file_is_gone(self) -> None:
        servers = links.read_json(DOTFILES / "omp" / "mcp.json")
        assert isinstance(servers, dict)
        self.assertIn("chathub", servers["mcpServers"])
        self.assertFalse(os.path.lexists(DOTFILES / ".omp" / "mcp.json"))


if __name__ == "__main__":
    unittest.main()
