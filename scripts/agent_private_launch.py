"""How one agent start reads its arguments and its files, for scripts/agent-private-check (whose
header has the rule). launch_for(harness, caller, argv) returns a Launch: the start's first argument,
the directory it starts in and its session's directory; and, read only when the check asks, the
envoy.json its Envoy plugin reads, the files the harness applies to its own environment, and the
directories whose .envoy/envoy.json its session reads. The check imports this module from its own
directory, as scripts/disk-fill-watch imports envoy_client.py.
"""

from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path


class Refused(Exception):
    """The session may not start; the message is why, for the person who started it."""


def env(name: str) -> str:
    """A variable's value; an empty one counts as unset."""
    return os.environ.get(name) or ""


def absolute(name: str, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise Refused(f"{name} must name an absolute path; fix it, then start again")
    return path


def neutral_envoy_json() -> Path:
    """${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json, the file of no harness (envoy/AGENTS.md)."""
    config_home = absolute("XDG_CONFIG_HOME", env("XDG_CONFIG_HOME")) if env("XDG_CONFIG_HOME") else Path.home() / ".config"
    return config_home / "legion" / "envoy.json"


def handed_envoy_json() -> Path:
    """The file ENVOY_CONFIG_FILE names, else the neutral one: what a plugin reads when its own
    harness names no directory."""
    return absolute("ENVOY_CONFIG_FILE", env("ENVOY_CONFIG_FILE")) if env("ENVOY_CONFIG_FILE") else neutral_envoy_json()


def command_envoy_json() -> Path:
    """envoyConfigPath("command") from the Dispatch client: ENVOY_CONFIG_FILE, a named omp profile's
    PI_CODING_AGENT_DIR, Claude Code's CLAUDE_CONFIG_DIR inside a Claude Code session, the neutral
    file. (scripts/envoy still reads OPENCODE_CONFIG_DIR, which the client no longer does.)"""
    if env("ENVOY_CONFIG_FILE"):
        return absolute("ENVOY_CONFIG_FILE", env("ENVOY_CONFIG_FILE"))
    if env("OMP_PROFILE") not in ("", "default") and env("PI_CODING_AGENT_DIR"):
        return absolute("PI_CODING_AGENT_DIR", env("PI_CODING_AGENT_DIR")) / "envoy.json"
    if env("CLAUDE_CODE_CHILD_SESSION") == "1" and env("CLAUDE_CONFIG_DIR"):
        return absolute("CLAUDE_CONFIG_DIR", env("CLAUDE_CONFIG_DIR")) / "envoy.json"
    return neutral_envoy_json()


# The keys of a file the harness applies that name a Dispatch bearer, or the envoy.json one comes
# from: the variables agent-private-check's bearers() reads, and its CONFIG_KEYS.
BEARER_KEYS = frozenset({"DISPATCH_TOKEN", "DISPATCH_TOKEN_FILE", "ENVOY_CONFIG_FILE", "XDG_CONFIG_HOME", "HOME"})


def options(args: list[str], names: set[str]) -> list[tuple[str, str]]:
    """Each (name, value) ARGS gives an option of NAMES before `--`, in order: the argument after
    `NAME`, whatever it looks like, or `NAME=VALUE` for a long one. It scans as scripts/agent-session's
    resume `target` loop does, argument by argument against the one before, so the two always agree
    on omp's resume target."""
    found: list[tuple[str, str]] = []
    prev = ""
    for arg in args:
        if arg == "--":
            break
        if prev in names:
            found.append((prev, arg))
        name, eq, value = arg.partition("=")
        if eq and name.startswith("--") and name in names:
            found.append((name, value))
        prev = arg
    return found


def last(found: list[tuple[str, str]], *names: str) -> str | None:
    """The last value FOUND gives one of NAMES."""
    values = [value for name, value in found if name in names]
    return values[-1] if values else None


# Bun's node:util.parseEnv grammar: export, comments, quotes/backticks, multiline values, and
# literal escaped quotes. Consume every assignment, not just Dispatch's: a multiline note may
# contain text that looks like a token assignment. Nothing from a dotenv file is executed.
DOTENV_ENTRY = re.compile(
    r'''^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(?:"((?:\\[\s\S]|[^"\\])*)"|'((?:\\[\s\S]|[^'\\])*)'|`((?:\\[\s\S]|[^`\\])*)`|([^\r\n#]*))''',
    re.M,
)


def dotenv(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as e:
        raise Refused(f"{path} cannot be read as a dotenv file; retry") from e
    values = {}
    for entry in DOTENV_ENTRY.finditer(text):
        key = entry[1]
        value = next(part for part in entry.groups()[1:] if part is not None)
        if entry[2] is not None:
            value = value.replace(r"\n", "\n").replace(r"\r", "\r")
        elif entry[5] is not None:
            value = value.strip()
        values[key] = value
    # omp mirrors OMP_* to PI_* after parsing each file.
    values.update({f"PI_{key[4:]}": value for key, value in list(values.items()) if key.startswith("OMP_")})
    return values


def omp_dirs(launch: Path, profile: str) -> tuple[Path, Path]:
    """omp's config root and agent directory as utils/dirs.ts resolves them at import: the root is
    $HOME/${PI_CONFIG_DIR:-.omp} (shims/omp's rule too), a named PROFILE's under profiles/<name>;
    the default profile's agent directory is PI_CODING_AGENT_DIR, relative to LAUNCH, unless that
    names the agent directory of the profile PI_PROFILE names."""
    root = Path(os.path.normpath(str(Path.home() / (env("PI_CONFIG_DIR") or ".omp").lstrip("/"))))
    if profile:
        return root / "profiles" / profile, root / "profiles" / profile / "agent"
    agent = Path(os.path.abspath(launch / env("PI_CODING_AGENT_DIR"))) if env("PI_CODING_AGENT_DIR") else root / "agent"
    if env("PI_PROFILE") not in ("", "default") and agent == root / "profiles" / env("PI_PROFILE") / "agent":
        agent = root / "agent"
    return root, agent


# A resumed session's file names its directory on its `session` line, within its first few lines.
SESSION_HEADER_BYTES = 64 * 1024
SESSION_ID = re.compile(r"[0-9a-f][0-9a-f-]*")


def resumed_cwd(target: str, launch: Path, agent: Path, session_dir: Path | None = None) -> Path | None:
    """The directory `omp --resume TARGET` (also -r, --session, --restore) moves to: the `cwd` of its
    session file's `session` line. The file is TARGET when it names a .jsonl, else the one file whose
    name ends in that id or prefix: in SESSION_DIR (`--session-dir`) when given, else under
    AGENT/sessions. None when it cannot be resolved: a picker (no TARGET), an unknown or ambiguous id,
    an unreadable file."""
    if target.endswith(".jsonl"):
        files = [launch / target]
    elif SESSION_ID.fullmatch(target):
        pattern = (session_dir / f"*_{target}*.jsonl" if session_dir is not None
                   else agent / "sessions" / "*" / f"*_{target}*.jsonl")
        files = [Path(p) for p in glob.glob(str(pattern))]
    else:
        return None
    if len(files) != 1:
        return None
    try:
        with open(files[0], "rb") as session:
            head = session.read(SESSION_HEADER_BYTES)
    except OSError:
        return None
    for line in head.splitlines()[:8]:
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(entry, dict) and entry.get("type") == "session":
            cwd = entry.get("cwd")
            return Path(cwd) if isinstance(cwd, str) and os.path.isabs(cwd) else None
    return None


# Linux's managed settings, which outrank the others (installers/claude.sh writes them).
CLAUDE_MANAGED_SETTINGS = Path("/etc/claude-code/managed-settings.json")


def git_root(path: Path) -> Path | None:
    """The repository root Claude Code's roo() finds for PATH: the nearest folder from PATH up, PATH
    included, that holds a `.git` (a directory, or a linked worktree's file); None outside one."""
    here = path
    while not (here / ".git").exists():
        if here.parent == here:
            return None
        here = here.parent
    return here


def canonical_root(root: Path) -> Path:
    """The key Claude Code's dOe() records a repository's trust under: a linked worktree's main
    repository, followed from ROOT's `.git` file through the worktree's gitdir, its commondir and
    the gitdir file pointing back at ROOT; ROOT itself whenever any of that does not hold."""
    try:
        text = (root / ".git").read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir:"):
            return root
        gitdir = Path(os.path.normpath(root / text[7:].strip()))
        common = Path(os.path.normpath(gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()))
        back = (gitdir / "gitdir").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):  # a .git directory, or a worktree's files missing
        return root
    if gitdir.parent != common / "worktrees" or os.path.realpath(gitdir / back) != os.path.realpath(root / ".git"):
        return root
    if common.name != ".git":  # a bare repository's worktree
        return root if (common / ".git").exists() else common
    return common.parent


def claude_trusts(cwd: Path) -> bool:
    """Whether Claude Code 2.1.316 trusts CWD before its start asks anything, as its trust function
    (UI() in the binary) decides: CLAUDE_CODE_SANDBOXED trusts every folder; CLAUDE_BG_WORKSPACE_TRUSTED
    marks a background session's folder trusted at startup; else, unless CLAUDE_CODE_IGNORE_PERSISTED_TRUST
    makes no record count, `projects[<folder>].hasTrustDialogAccepted` in ${CLAUDE_CONFIG_DIR:-$HOME}/.claude.json
    for the repository's key (canonical_root), or for CWD or a folder above it up to and including
    the repository root (git_root), or `/` outside a repository: never past the root, so a clone under
    a trusted home folder is untrusted. A record that cannot be read trusts nothing.

    The function's other branch, a session flag (VMe() in the binary), is the caller's: print mode
    sets it at startup, and so does accepting the folder's trust dialog later in the session, which
    no check before the start can see (ClaudeLaunch.sources refuses a file that branch would apply)."""
    if env("CLAUDE_CODE_SANDBOXED") or env("CLAUDE_BG_WORKSPACE_TRUSTED"):
        return True
    if env("CLAUDE_CODE_IGNORE_PERSISTED_TRUST"):
        return False
    try:
        record = json.loads((Path(env("CLAUDE_CONFIG_DIR") or Path.home()) / ".claude.json").read_text(encoding="utf-8"))
        projects = record["projects"]
    except (OSError, UnicodeError, ValueError, RecursionError, KeyError, TypeError):
        return False
    if not isinstance(projects, dict):
        return False

    def trusted(folder: Path) -> bool:
        entry = projects.get(str(folder))
        return isinstance(entry, dict) and bool(entry.get("hasTrustDialogAccepted"))

    root = git_root(cwd)
    if trusted(canonical_root(root) if root is not None else cwd):
        return True
    here = cwd
    while not trusted(here):
        if here == root or here.parent == here:
            return False
        here = here.parent
    return True


def settings_file_env(file: Path) -> dict[str, str]:
    """The `env` block of the settings FILE, {} when it has none or there is no file."""
    try:
        text = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as e:
        raise Refused(f"{file} cannot be read as Claude Code settings; fix it, then start again") from e
    return settings_env(text, file)


def settings_env(text: str, where: object) -> dict[str, str]:
    """The `env` block of settings TEXT (from WHERE), {} when it has none."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as e:
        raise Refused(f"{where} cannot be read as Claude Code settings; fix it, then start again") from e
    block = data.get("env") if isinstance(data, dict) else None
    return {k: v for k, v in block.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(block, dict) else {}


@dataclass
class Launch:
    """One harness start, as its argv says: its first argument (`command`), the directory it starts
    in (`launch`) and its session's directory (`cwd`). What it reads (its envoy.json, the files it
    applies to its environment, the checkouts' .envoy/envoy.json) is read only when asked for, once
    the flag says to: a start the flag lets through reads none of them."""

    command: str
    launch: Path
    cwd: Path

    @cached_property
    def envoy_json(self) -> Path:
        """The envoy.json the harness's Envoy plugin reads."""
        return command_envoy_json()

    @cached_property
    def sources(self) -> list[tuple[str, dict[str, str]]]:
        """Each file the harness applies to its environment, with its values."""
        return []

    @cached_property
    def merged(self) -> tuple[dict[str, str], dict[str, str]]:
        """The session's environment, those files applied, and the file each value it took came from."""
        return dict(os.environ), {}

    @cached_property
    def checkouts(self) -> list[Path]:
        """The directories whose .envoy/envoy.json the session reads."""
        return [self.cwd]


@dataclass
class OmpLaunch(Launch):
    profile: str = ""
    resume: str | None = None
    session_dir: Path | None = None

    @classmethod
    def parse(cls, caller: str, args: list[str]) -> OmpLaunch:
        launch = Path(os.path.abspath(Path.cwd()))
        found = options(args, {"--cwd", "--profile", "--resume", "-r", "--session", "--restore", "--session-dir"})
        cwd, session_dir = last(found, "--cwd"), last(found, "--session-dir")
        # As omp resolves it: --profile, else OMP_PROFILE when it is set at all, else PI_PROFILE.
        profile = last(found, "--profile")
        profile = os.environ.get("OMP_PROFILE", env("PI_PROFILE")) if profile is None else profile
        return cls(args[0] if args else "", launch, Path(os.path.abspath(launch / cwd)) if cwd else launch,
                   "" if profile == "default" else profile, last(found, "--resume", "-r", "--session", "--restore"),
                   Path(os.path.abspath(launch / session_dir)) if session_dir else None)

    @cached_property
    def dirs(self) -> tuple[Path, Path]:
        return omp_dirs(self.launch, self.profile)

    @cached_property
    def envoy_json(self) -> Path:
        # pi-envoy reads a named profile's agent directory's file, else ENVOY_CONFIG_FILE's or the neutral one.
        return self.dirs[1] / "envoy.json" if self.profile else handed_envoy_json()

    @cached_property
    def sources(self) -> list[tuple[str, dict[str, str]]]:
        # omp reads these four at import, from the directory it starts in, before `--cwd` or a resume
        # moves it; it never reads the --cwd directory's .env.
        root, agent = self.dirs
        files = [self.launch / ".env", agent / ".env", root / ".env", Path.home() / ".env"]
        return [(str(file), dotenv(file)) for file in dict.fromkeys(files)]

    @cached_property
    def merged(self) -> tuple[dict[str, str], dict[str, str]]:
        # Each file fills only what the environment left unset, the first file naming a key first.
        environment, applied = dict(os.environ), {}
        for where, values in self.sources:
            for key, value in values.items():
                if not environment.get(key):
                    environment[key], applied[key] = value, where
        return environment, applied

    @cached_property
    def checkouts(self) -> list[Path]:
        resumed = resumed_cwd(self.resume, self.launch, self.dirs[1], self.session_dir) if self.resume else None
        return [self.cwd] if resumed is None else [self.cwd, resumed]


# The flags scripts/cld puts before the person's arguments, with the number of values each takes.
CLD_FLAGS = {"--dangerously-skip-permissions": 0, "--channels": 1, "--settings": 1}


def prints(args: list[str]) -> bool:
    """Whether ARGS start Claude Code in print mode: `--print`, or a single-dash cluster holding `p`
    (`-p`, `-cp`, `-pc`), which Claude Code splits into its short flags."""
    before = args[:args.index("--")] if "--" in args else args
    return any(arg == "--print" or (arg.startswith("-") and not arg.startswith("--") and "p" in arg[1:])
               for arg in before)


@dataclass
class ClaudeLaunch(Launch):
    settings: list[str] = field(default_factory=list)
    printing: bool = False

    @classmethod
    def parse(cls, caller: str, args: list[str]) -> ClaudeLaunch:
        launch = Path(os.path.abspath(Path.cwd()))
        first = 0
        if caller == "agent-session":  # only scripts/cld runs claude through agent-session
            while first < len(args) and args[first] in CLD_FLAGS:
                first += 1 + CLD_FLAGS[args[first]]
        return cls(args[first] if first < len(args) else "", launch, launch,
                   [value for _, value in options(args, {"--settings"})], prints(args))

    @cached_property
    def envoy_json(self) -> Path:
        # The claude-envoy bridge reads CLAUDE_CONFIG_DIR's file, else ENVOY_CONFIG_FILE's or the neutral one.
        return (absolute("CLAUDE_CONFIG_DIR", env("CLAUDE_CONFIG_DIR")) / "envoy.json" if env("CLAUDE_CONFIG_DIR")
                else handed_envoy_json())

    @cached_property
    def sources(self) -> list[tuple[str, dict[str, str]]]:
        """The `env` blocks Claude Code applies at startup, lowest precedence first: the user's settings;
        the project's and its local file where Claude Code applies them (a print-mode run, or a folder
        claude_trusts); each `--settings` file (relative to the launch directory) or inline JSON; the
        managed settings.

        In a folder not trusted yet, Claude Code applies the project's files once the person accepts
        its trust dialog in the session, which this check cannot see before the start. So such a file
        that names a bearer refuses the start; nothing it names is read or sent."""
        user = absolute("CLAUDE_CONFIG_DIR", env("CLAUDE_CONFIG_DIR")) if env("CLAUDE_CONFIG_DIR") else Path.home() / ".claude"
        project = [self.cwd / ".claude" / "settings.json", self.cwd / ".claude" / "settings.local.json"]
        sources = [(f"the env block of {file}", settings_file_env(file)) for file in [user / "settings.json"]]
        if self.printing or claude_trusts(self.cwd):
            sources += [(f"the env block of {file}", settings_file_env(file)) for file in project]
        else:
            for file in project:
                named = sorted(BEARER_KEYS & settings_file_env(file).keys())
                if named:
                    raise Refused(f"{file} names {', '.join(named)} for Dispatch, and Claude Code applies it once this "
                                  "folder's trust is accepted, which this check cannot see before the start; run "
                                  "Claude Code here with -p, or take that from the file, then start again")
        for value in self.settings:
            if value.lstrip().startswith("{"):
                sources.append(("the env block of the inline --settings", settings_env(value, "the inline --settings")))
            else:
                file = Path(os.path.abspath(self.launch / value))
                sources.append((f"the env block of {file}", settings_file_env(file)))
        sources.append((f"the env block of {CLAUDE_MANAGED_SETTINGS}", settings_file_env(CLAUDE_MANAGED_SETTINGS)))
        return sources

    @cached_property
    def merged(self) -> tuple[dict[str, str], dict[str, str]]:
        # Each block overrides the environment and the blocks below it.
        environment, applied = dict(os.environ), {}
        for where, values in self.sources:
            for key, value in values.items():
                environment[key], applied[key] = value, where
        return environment, applied


@dataclass
class CodexLaunch(Launch):
    @classmethod
    def parse(cls, caller: str, args: list[str]) -> CodexLaunch:
        launch = Path(os.path.abspath(Path.cwd()))
        before = args[:args.index("--")] if "--" in args else args
        cwd, i = None, 0
        while i < len(before):  # -C DIR, --cd DIR, --cd=DIR, -C=DIR, -CDIR; the last one counts
            arg = before[i]
            if arg in ("-C", "--cd") and i + 1 < len(before):
                cwd, i = before[i + 1], i + 1
            elif arg.startswith(("--cd=", "-C=")):
                cwd = arg.partition("=")[2]
            elif arg.startswith("-C") and len(arg) > 2:
                cwd = arg[2:]
            i += 1
        return cls(args[0] if args else "", launch, Path(os.path.abspath(launch / cwd)) if cwd else launch)


LAUNCHES = {"omp": OmpLaunch.parse, "claude": ClaudeLaunch.parse, "codex": CodexLaunch.parse}


def launch_for(harness: str, caller: str, args: list[str]) -> Launch:
    """HARNESS's start with ARGS, run by CALLER (the launcher: agent-session, or the shim's own name).
    A harness this file does not know (agent-session runs any agent it is given) is read generically:
    its launch directory, and the envoy.json the Dispatch client's own commands read."""
    parse = LAUNCHES.get(harness)
    if parse is None:
        launch = Path(os.path.abspath(Path.cwd()))
        return Launch(args[0] if args else "", launch, launch)
    return parse(caller, args)
