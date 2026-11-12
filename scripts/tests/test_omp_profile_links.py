#!/usr/bin/env python3
"""shims/omp under a named profile: the profile's symlinks track the installer's in ~/.omp, unless
the profile holds .no-shared-setup."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SHIM = DOTFILES / "shims" / "omp"


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@dataclass
class Scratch:
    """A scratch HOME holding the installer's default omp layout, and the shim's environment."""

    home: Path
    targets: Path
    omp: Path
    stubs: Path
    env: dict[str, str]

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SHIM), *args], capture_output=True, text=True, env={**self.env, **extra}, check=False, timeout=60
        )


def default_layout(root: Path, config_dir: str = ".omp") -> Scratch:
    """ROOT/home/CONFIG_DIR as installers/omp.sh leaves it, its links into ROOT/dotfiles-omp."""
    home = root / "home"
    (home / ".config").mkdir(parents=True)
    targets = root / "dotfiles-omp"
    for name in ("plugins", "agents", "hooks", "prompts"):
        (targets / name).mkdir(parents=True)
    for name in ("mcp.json", "viewport.ts"):
        (targets / name).write_text("", encoding="utf-8")
    omp = home / config_dir
    agent = omp / "agent"
    (agent / "extensions").mkdir(parents=True)
    (omp / "plugins").symlink_to(targets / "plugins")
    for name in ("agents", "hooks", "prompts", "mcp.json"):
        (agent / name).symlink_to(targets / name)
    (agent / "extensions" / "viewport.ts").symlink_to(targets / "viewport.ts")
    for name in ("config.yml", "models.yml", "agent.db"):
        (agent / name).write_text("{}", encoding="utf-8")
    stubs = root / "bin"
    stubs.mkdir()
    # `mise env -s bash` puts the pinned release's install dir on PATH.
    tool = home / ".mise" / "installs" / "github-sjawhar-oh-my-pi" / "1.0.0" / "bin"
    tool.mkdir(parents=True)
    # The stub omp prints its arguments and the profile variables it was started with.
    write_stub(
        tool,
        "omp",
        'echo "AGENT omp $*"\necho "PROFILE ${OMP_PROFILE-unset} ${OMP_IN_DEFAULT_PROFILE-unset}"\n'
        'echo "PI ${PI_PROFILE-unset}"',
    )
    write_stub(stubs, "mise", f"echo 'export PATH=\"{tool}:$PATH\"'")
    env = {
        "DOTFILES_DIR": str(DOTFILES),
        "HOME": str(home),
        "PATH": f"{stubs}:/usr/bin:/bin",
        "XDG_CONFIG_HOME": str(home / ".config"),
        # No memory scope: with no user manager here each launch would wait out the shim's
        # 10 s scope retry, and the memory ceiling has its own test.
        "OMP_MEMORY_SCOPE": "off",
        # scripts/agent-session makes directories under /mnt/nvme/<user> (or /mnt/scratch/<user>)
        # and /mnt/durable/<user> where they exist. A big-disk home that does not exist and no
        # durable disk keep every launch's writes inside ROOT.
        "AGENT_SESSION_NVME_HOME": str(root / "no-nvme"),
        "AGENTBOX_DURABLE": "none",
    }
    if config_dir != ".omp":
        env["PI_CONFIG_DIR"] = config_dir
    return Scratch(home, targets, omp, stubs, env)


# The ways a launch names its profile; the machine file is $XDG_CONFIG_HOME/omp/profile.
NAMINGS = ("--profile NAME", "--profile=NAME", "OMP_PROFILE=NAME", "PI_PROFILE=NAME", "machine file")


def mark(scratch: Scratch, overlay: bool) -> Path:
    """Profile p, opted out, holding an entry for each kind of write the shared setup makes."""
    profile = scratch.omp / "profiles" / "p"
    agent = profile / "agent"
    (agent / ".mirror-src").mkdir(parents=True)
    (profile / ".no-shared-setup").touch()
    # The overlay setup opens the lock for writing, which empties it.
    (profile / ".share.lock").write_text("held", encoding="utf-8")
    # The mirror prunes a link the default lacks and re-points one the default holds.
    (agent / "retired").symlink_to(scratch.targets / "retired")
    (agent / "agents").symlink_to(scratch.targets / "old-agents")
    # The overlay setup moves a real file into the default and links to it there.
    (agent / "own.txt").write_text("the profile's own", encoding="utf-8")
    # A copy as last copied, which the mirror refreshes when the shared file changes. The overlay
    # setup refuses a real mcp.json beside the default's link and fails the launch, which would hide
    # what it wrote, so the overlay profile's copy is lsp.json, which the default lacks and it moves.
    copy = "lsp.json" if overlay else "mcp.json"
    for path in (agent / copy, agent / ".mirror-src" / copy):
        path.write_text("old copy", encoding="utf-8")
    if overlay:
        (agent / "models.overlay.yml").write_text("providers: {}\n", encoding="utf-8")
        (agent / "models.yml").write_text("old: models\n", encoding="utf-8")
    else:
        (scratch.targets / "mcp.json").write_text("new shared", encoding="utf-8")
    return profile


def shared_profile(scratch: Scratch, name: str) -> Path:
    """Profile NAME, unmarked, with a models overlay: the shared setup's profile. Its agent dir."""
    agent = scratch.omp / "profiles" / name / "agent"
    agent.mkdir(parents=True)
    (agent / "models.overlay.yml").write_text("providers: {}\n", encoding="utf-8")
    return agent


def record(top: Path) -> list[tuple[str, str, str, str | None, str | None]]:
    """Each entry under TOP, dot entries included and links not followed: its path, type, mode,
    link target, and a regular file's SHA-256."""
    entries = []
    for directory, dirs, files in os.walk(top):
        for name in (*dirs, *files):
            path = Path(directory) / name
            mode = stat.filemode(path.lstat().st_mode)
            entries.append((
                str(path.relative_to(top)),
                mode[0],
                mode[1:],
                os.readlink(path) if mode[0] == "l" else None,
                hashlib.sha256(path.read_bytes()).hexdigest() if mode[0] == "-" else None,
            ))
    return sorted(entries)


def link_target(path: Path) -> str | None:
    """Where the link PATH points, or None when PATH is no link."""
    return os.readlink(path) if path.is_symlink() else None


def calls(log: Path) -> list[str]:
    """The calls of the logging stubs, one `NAME ARGS` line each."""
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


# The tools the overlay setup runs, each a logging stub in logging_home's tree.
OVERLAY_TOOLS = {"omp-models-check", "flock", "yq"}


def tools(log: Path, since: int = 0) -> set[str]:
    """The names the logging stubs logged, from call SINCE on."""
    return {call.split()[0] for call in calls(log)[since:]}


class OmpProfileLinks(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.scratch = default_layout(Path(self.temp_dir.name))
        self.home, self.targets, self.omp = self.scratch.home, self.scratch.targets, self.scratch.omp

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def omp_run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return self.scratch.run(*args, **extra)

    def assert_launched(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("AGENT omp", result.stdout)

    def launched_env(self, *args: str, **extra: str) -> tuple[str, str]:
        """OMP_PROFILE and OMP_IN_DEFAULT_PROFILE as omp saw them ("unset" when absent)."""
        result = self.omp_run(*args, **extra)
        self.assert_launched(result)
        [line] = [line for line in result.stdout.splitlines() if line.startswith("PROFILE ")]
        omp_profile, in_default = line.split(" ")[1:]
        return omp_profile, in_default

    def assert_mirrors_canonical(self, profile: Path, targets: Path | None = None) -> None:
        """PROFILE holds the installer's entries as the mirror leaves them, from TARGETS (the test's
        own by default)."""
        targets = targets or self.targets
        self.assertEqual(link_target(profile / "plugins"), str(targets / "plugins"))
        for name in ("agents", "hooks", "prompts"):
            self.assertEqual(link_target(profile / "agent" / name), str(targets / name))
        # A shared config file is a copy, so a write to the profile's file cannot reach the shared one.
        self.assertFalse(os.path.islink(profile / "agent" / "mcp.json"))
        self.assertEqual((profile / "agent" / "mcp.json").read_bytes(), (targets / "mcp.json").read_bytes())
        # Extension code stays linked: a copy would resolve its imports from the profile.
        self.assertEqual(link_target(profile / "agent/extensions/viewport.ts"), str(targets / "viewport.ts"))

    def logging_home(self, config_dir: str = ".omp") -> tuple[Scratch, Path]:
        """A fresh tree whose overlay tools log: its DOTFILES_DIR links the repository's entries but
        for a logging omp-models-check, and flock and yq log too, each a `NAME ARGS` line in the
        returned log. Fresh, so no case sees another's writes: a shim that ignores the marker, for
        one, moves an overlay profile's entries, the marker too, into the default, where the next
        case would find them."""
        root = Path(tempfile.mkdtemp(dir=self.temp_dir.name))
        scratch = default_layout(root, config_dir)
        log = root / "calls.log"
        dotfiles = root / "dotfiles"
        (dotfiles / "scripts").mkdir(parents=True)
        # A link per entry, not one into the repository's scripts/, where the stub would replace the checker.
        for entry in DOTFILES.iterdir():
            if entry.name != "scripts":
                (dotfiles / entry.name).symlink_to(entry)
        for entry in (DOTFILES / "scripts").iterdir():
            if entry.name != "omp-models-check":
                (dotfiles / "scripts" / entry.name).symlink_to(entry)
        write_stub(dotfiles / "scripts", "omp-models-check", f'echo "omp-models-check $*" >>"{log}"')
        write_stub(scratch.stubs, "flock", f'echo "flock $*" >>"{log}"')
        write_stub(scratch.stubs, "yq", f'echo "yq $*" >>"{log}"\necho "a: 1"')
        scratch.env["DOTFILES_DIR"] = str(dotfiles)
        return scratch, log

    def launch(self, scratch: Scratch, how: str, name: str) -> None:
        """Start the shim on profile NAME, named HOW (one of NAMINGS), and check omp started on it."""
        args: tuple[str, ...] = ()
        extra: dict[str, str] = {}
        if how == "--profile NAME":
            args, started = ("--profile", name), f"AGENT omp --profile {name}"
        elif how == "--profile=NAME":
            args, started = (f"--profile={name}",), f"AGENT omp --profile={name}"
        elif how == "OMP_PROFILE=NAME":
            extra, started = {"OMP_PROFILE": name}, f"PROFILE {name} unset"
        elif how == "PI_PROFILE=NAME":
            extra, started = {"PI_PROFILE": name}, f"PI {name}"
        elif how == "machine file":
            (scratch.home / ".config" / "omp").mkdir(exist_ok=True)
            (scratch.home / ".config" / "omp" / "profile").write_text(f"{name}\n", encoding="utf-8")
            # The shim exports the file's name as OMP_PROFILE.
            started = f"PROFILE {name} unset"
        else:
            raise ValueError(f"unknown naming: {how!r}")
        result = scratch.run(*args, **extra)
        self.assert_launched(result)
        self.assertIn(started, result.stdout.splitlines())

    def assert_no_shared_setup(self, scratch: Scratch, log: Path, profile: Path, how: str = "--profile NAME") -> None:
        """The marked PROFILE, launched HOW: the shim writes nothing in it and runs none of the
        overlay's tools."""
        before = record(profile)
        # The launch first, so a shim that ignores the marker fails on what it did.
        self.launch(scratch, how, profile.name)
        self.assertEqual(record(profile), before, "the shim wrote in a marked profile")
        self.assertEqual(calls(log), [], "the shim ran the overlay's tools for a marked profile")

    def assert_shared_setup_runs(self, scratch: Scratch, log: Path, how: str) -> None:
        """Unmarked profiles beside a marked one, launched HOW, still get the shared setup."""
        self.launch(scratch, how, "q")
        self.assert_mirrors_canonical(scratch.omp / "profiles" / "q", scratch.targets)
        agent = shared_profile(scratch, "o")
        seen = len(calls(log))
        self.launch(scratch, how, "o")
        self.assertEqual(link_target(agent / "agents"), str(scratch.omp / "agent" / "agents"), "o's agent/agents")
        self.assertTrue((agent / "models.yml").is_file(), "o's agent/models.yml")
        self.assertEqual(tools(log, seen), OVERLAY_TOOLS, "the overlay setup's tools for o")

    def test_named_profile_gets_every_installer_link(self) -> None:
        for args, extra in ((("--profile", "theorem"), {}), ((), {"OMP_PROFILE": "theorem"})):
            with self.subTest(args=args, env=extra):
                self.assert_launched(self.omp_run(*args, **extra))
                profile = self.omp / "profiles" / "theorem"
                self.assert_mirrors_canonical(profile)
                for name in ("config.yml", "models.yml", "agent.db"):
                    self.assertFalse(os.path.lexists(profile / "agent" / name), name)

    def test_profile_follows_the_installer_and_keeps_its_own_files(self) -> None:
        agent = self.omp / "profiles" / "theorem" / "agent"
        (agent / "extensions").mkdir(parents=True)
        (agent / "agents").symlink_to(self.targets / "old-agents")
        (agent / "extensions" / "retired.ts").symlink_to(self.targets / "retired.ts")
        (agent / "models.yml").symlink_to(self.omp / "agent" / "models.yml")
        (agent / "mcp.json").write_text("the profile's own", encoding="utf-8")
        (agent / "agent.db").write_text("state", encoding="utf-8")
        self.assert_launched(self.omp_run("--profile", "theorem"))
        self.assertEqual(os.readlink(agent / "agents"), str(self.targets / "agents"))
        self.assertFalse(os.path.lexists(agent / "extensions" / "retired.ts"))
        self.assertFalse(os.path.lexists(agent / "models.yml"))
        self.assertEqual((agent / "mcp.json").read_text(encoding="utf-8"), "the profile's own")
        self.assertEqual((agent / "agent.db").read_text(encoding="utf-8"), "state")

    def test_a_write_to_the_profiles_mcp_json_does_not_reach_the_shared_file(self) -> None:
        """2026-11-01: a probe harness wrote `{ "mcpServers": {} }` to its profile's agent/mcp.json,
        a link to the shared file, and every session on the box started with no MCP servers."""
        (self.targets / "mcp.json").write_text('{"mcpServers": {"slack": {}}}', encoding="utf-8")
        agent = self.omp / "profiles" / "probe" / "agent"
        self.assert_launched(self.omp_run("--profile", "probe"))
        (agent / "mcp.json").write_text('{ "mcpServers": {} }\n', encoding="utf-8")
        self.assertEqual((self.targets / "mcp.json").read_text(encoding="utf-8"), '{"mcpServers": {"slack": {}}}')
        self.assert_launched(self.omp_run("--profile", "probe"))
        self.assertEqual((agent / "mcp.json").read_text(encoding="utf-8"), '{ "mcpServers": {} }\n')
        self.assertEqual((self.targets / "mcp.json").read_text(encoding="utf-8"), '{"mcpServers": {"slack": {}}}')

    def test_the_copy_follows_the_shared_file_until_the_profile_changes_it(self) -> None:
        agent = self.omp / "profiles" / "theorem" / "agent"
        (self.targets / "mcp.json").write_text("v1", encoding="utf-8")
        self.assert_launched(self.omp_run("--profile", "theorem"))
        (self.targets / "mcp.json").write_text("v2", encoding="utf-8")
        self.assert_launched(self.omp_run("--profile", "theorem"))
        self.assertEqual((agent / "mcp.json").read_text(encoding="utf-8"), "v2")
        (agent / "mcp.json").write_text("the profile's own", encoding="utf-8")
        (self.targets / "mcp.json").write_text("v3", encoding="utf-8")
        self.assert_launched(self.omp_run("--profile", "theorem"))
        self.assertEqual((agent / "mcp.json").read_text(encoding="utf-8"), "the profile's own")

    def test_an_old_link_to_the_shared_file_becomes_a_copy(self) -> None:
        agent = self.omp / "profiles" / "theorem" / "agent"
        agent.mkdir(parents=True)
        (self.targets / "mcp.json").write_text("shared", encoding="utf-8")
        (agent / "mcp.json").symlink_to(self.targets / "mcp.json")
        self.assert_launched(self.omp_run("--profile", "theorem"))
        self.assertFalse(os.path.islink(agent / "mcp.json"))
        self.assertEqual((agent / "mcp.json").read_text(encoding="utf-8"), "shared")
        (agent / "mcp.json").write_text("written", encoding="utf-8")
        self.assertEqual((self.targets / "mcp.json").read_text(encoding="utf-8"), "shared")

    def test_a_copy_whose_shared_file_is_gone_is_removed_unless_changed(self) -> None:
        agent = self.omp / "profiles" / "theorem" / "agent"
        self.assert_launched(self.omp_run("--profile", "theorem"))
        self.assertTrue((agent / "mcp.json").is_file())
        (self.omp / "agent" / "mcp.json").unlink()
        self.assert_launched(self.omp_run("--profile", "theorem"))
        self.assertFalse(os.path.lexists(agent / "mcp.json"))

    def test_a_profiles_envoy_json_is_its_own(self) -> None:
        """A named profile reads Dispatch and Envoy from its own agent dir's envoy.json, and has
        none without one: the shim neither prunes a profile's envoy.json link (one to the neutral
        file is how a profile gets them) nor gives a profile the default agent dir's."""
        neutral = self.home / ".config" / "legion" / "envoy.json"
        neutral.parent.mkdir(parents=True)
        neutral.write_text("{}", encoding="utf-8")
        own = self.omp / "profiles" / "legion" / "agent" / "envoy.json"
        own.parent.mkdir(parents=True)
        own.symlink_to(neutral)
        self.assert_launched(self.omp_run("--profile", "legion"))
        self.assertEqual(os.readlink(own), str(neutral))
        (self.omp / "agent" / "envoy.json").symlink_to(neutral)
        self.assert_launched(self.omp_run("--profile", "theorem"))
        self.assertFalse(os.path.lexists(self.omp / "profiles" / "theorem" / "agent" / "envoy.json"))

    def test_a_shared_profile_starts_again_while_legions_envoy_json_is_missing(self) -> None:
        """The shared setup links agent/envoy.json to $XDG_CONFIG_HOME/legion/envoy.json, which a
        machine may not have. That link points at nothing, so a test with `-e` called it absent and
        `ln` refused every later launch of the profile ("File exists")."""
        scratch, _ = self.logging_home()
        agent = shared_profile(scratch, "o")
        neutral = scratch.home / ".config" / "legion" / "envoy.json"
        self.assertFalse(os.path.lexists(neutral))
        for launch in ("first", "second"):
            self.launch(scratch, "--profile NAME", "o")
            self.assertEqual(link_target(agent / "envoy.json"), str(neutral),
                             f"o's envoy.json after the {launch} launch")

    def test_a_profiles_link_beside_a_real_default_entry_is_its_own(self) -> None:
        """2026-11-09 ~07:46Z on oryx: the legion profile's agent/agent.db, a link to the personal
        profile's agent.db that holds every Legion pane's provider logins, was pruned because the
        default's agent.db is a real file, not a link; the next run failed "No API key found".
        The shim mirrors only the default's links, so a link it finds beside a real default entry
        is one the profile made, and it stays. A mirrored link whose source is gone still goes."""
        logins = self.omp / "profiles" / "personal" / "agent" / "agent.db"
        logins.parent.mkdir(parents=True)
        logins.write_text("logins", encoding="utf-8")
        agent = self.omp / "profiles" / "legion" / "agent"
        agent.mkdir(parents=True)
        (agent / "agent.db").symlink_to(logins)
        (agent / "retired").symlink_to(self.targets / "retired")
        self.assert_launched(self.omp_run("--profile", "legion"))
        self.assertEqual(os.readlink(agent / "agent.db"), str(logins))
        self.assertFalse(os.path.lexists(agent / "retired"))

    def test_a_marked_profile_gets_no_shared_setup(self) -> None:
        """The shim's mirror linked the default's agents into the legion profile, and on 2026-11-09
        the Legion daemon's boot check refused on the dotfiles `oracle` agent (dispatch://HOME-163).
        A profile that holds .no-shared-setup is its owner's: however a launch names it, with a
        models overlay or without, the shim writes nothing in it and runs none of the overlay's tools.
        The controls are the unmarked profiles beside it, launched the same way in the same tree after
        it: a guard that skipped every profile, or every profile one naming reaches, fails them."""
        for how in NAMINGS:
            for overlay in (False, True):
                scratch, log = self.logging_home()
                profile = mark(scratch, overlay)
                with self.subTest(how=how, overlay=overlay, profile="marked"):
                    self.assert_no_shared_setup(scratch, log, profile, how)
                with self.subTest(how=how, overlay=overlay, profile="unmarked"):
                    self.assert_shared_setup_runs(scratch, log, how)

    def test_the_marker_is_read_under_the_launchs_config_dir(self) -> None:
        """omp and the shim's setup keep a launch's state under $HOME/$PI_CONFIG_DIR, so the marker
        is read there too. A guard that looked under ~/.omp fails the marked case, and a setup that
        did fails the unmarked one."""
        scratch, log = self.logging_home(".omp-alt")
        profile = mark(scratch, overlay=True)
        with self.subTest(profile="marked"):
            self.assert_no_shared_setup(scratch, log, profile)
        with self.subTest(profile="unmarked"):
            self.assert_shared_setup_runs(scratch, log, "--profile NAME")

    def test_the_marker_is_read_with_e(self) -> None:
        """`-e` follows a link: a directory named .no-shared-setup opts a profile out as the file
        does, and a link counts only while its target exists. A guard that took only a regular
        file (`-f`) fails the directory case, and one that took any link (`-e || -L`) the other."""
        for overlay in (False, True):
            with self.subTest(marker="directory", overlay=overlay):
                scratch, log = self.logging_home()
                profile = mark(scratch, overlay)
                (profile / ".no-shared-setup").unlink()
                (profile / ".no-shared-setup").mkdir()
                self.assert_no_shared_setup(scratch, log, profile)
            with self.subTest(marker="link to nothing", overlay=overlay):
                scratch, log = self.logging_home()
                profile = mark(scratch, overlay)
                (profile / ".no-shared-setup").unlink()
                (profile / ".no-shared-setup").symlink_to(profile / "gone")
                self.launch(scratch, "--profile NAME", "p")
                agents = scratch.omp / "agent" / "agents" if overlay else scratch.targets / "agents"
                self.assertEqual(link_target(profile / "agent" / "agents"), str(agents), "p's agent/agents")
                self.assertEqual(tools(log), OVERLAY_TOOLS if overlay else set(), "the overlay setup's tools for p")

    def test_a_marker_in_the_default_never_reaches_a_shared_profile(self) -> None:
        """A shim from before the guard moved a shared profile's real entries into ~/.omp and linked
        them back, so it moved a marked shared profile's .no-shared-setup there. The shared setup
        links none of ~/.omp's into a profile: that link would opt the profile out from its next
        launch on."""
        scratch, log = self.logging_home()
        (scratch.omp / ".no-shared-setup").touch()
        agent = shared_profile(scratch, "o")
        for launch in ("first", "second"):
            seen = len(calls(log))
            self.launch(scratch, "--profile NAME", "o")
            self.assertFalse(os.path.lexists(agent.parent / ".no-shared-setup"),
                             f"o's .no-shared-setup after the {launch} launch")
            self.assertEqual(tools(log, seen), OVERLAY_TOOLS, f"the overlay setup's tools for o on the {launch} launch")

    def test_default_profile_creates_nothing(self) -> None:
        for args, extra in (((), {}), (("--profile", "default"), {}), ((), {"OMP_PROFILE": ""})):
            with self.subTest(args=args, env=extra):
                self.assert_launched(self.omp_run(*args, **extra))
        self.assertFalse((self.omp / "profiles").exists())

    def test_a_machine_file_names_the_profile_of_a_bare_launch(self) -> None:
        """oryx, Sami's personal machine, holds `personal` in $XDG_CONFIG_HOME/omp/profile."""
        (self.home / ".config" / "omp").mkdir()
        (self.home / ".config" / "omp" / "profile").write_text("theorem\n", encoding="utf-8")
        self.assertEqual(self.launched_env(), ("theorem", "unset"))
        self.assert_mirrors_canonical(self.omp / "profiles" / "theorem")
        # A flag or a defined variable still wins over the file.
        self.assertEqual(self.launched_env("--profile", "other"), ("unset", "unset"))
        self.assertEqual(self.launched_env(OMP_PROFILE="other"), ("other", "unset"))
        self.assertEqual(self.launched_env(PI_PROFILE="other"), ("unset", "unset"))
        self.assertTrue((self.omp / "profiles" / "other").is_dir())

    def test_the_default_profile_stays_reachable_beside_a_machine_file(self) -> None:
        (self.home / ".config" / "omp").mkdir()
        (self.home / ".config" / "omp" / "profile").write_text("theorem\n", encoding="utf-8")
        # omp drops OMP_PROFILE in a default-profile session, so the marker is what keeps an omp
        # that session starts with no profile named in the default.
        for args, extra in ((("--profile", "default"), {}), (("--profile=default",), {}), ((), {"OMP_PROFILE": ""}),
                            ((), {"OMP_PROFILE": "default"}), ((), {"OMP_IN_DEFAULT_PROFILE": "1"})):
            with self.subTest(args=args, env=extra):
                omp_profile, in_default = self.launched_env(*args, **extra)
                self.assertEqual(in_default, "1")
                self.assertNotEqual(omp_profile, "theorem")
        self.assertFalse((self.omp / "profiles").exists())
        # Naming a profile clears the marker for that session's own children.
        self.assertEqual(self.launched_env("--profile", "theorem", OMP_IN_DEFAULT_PROFILE="1"), ("unset", "unset"))

    def test_a_machine_without_the_file_exports_nothing_new(self) -> None:
        for args in ((), ("--profile", "default")):
            with self.subTest(args=args):
                self.assertEqual(self.launched_env(*args), ("unset", "unset"))
        self.assertFalse((self.omp / "profiles").exists())

    def test_a_bad_name_in_the_machine_file_refuses_before_omp_runs(self) -> None:
        (self.home / ".config" / "omp").mkdir()
        (self.home / ".config" / "omp" / "profile").write_text("../x\n", encoding="utf-8")
        result = self.omp_run()
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("AGENT", result.stdout)

    def test_bad_profile_name_refuses_before_omp_runs(self) -> None:
        for name in ("..", ".hidden", "a/b"):
            with self.subTest(name=name):
                result = self.omp_run("--profile", name)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("AGENT", result.stdout)
        self.assertFalse((self.omp / "profiles").exists())


if __name__ == "__main__":
    unittest.main()
