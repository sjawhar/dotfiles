"""The scratch developer box the launcher tests of scripts/agent-private-check run on, and a loader
for the check's own functions.

Machine: a dotfiles tree holding copies of the launchers (shims/omp, shims/claude and its link
shims/codex, scripts/cld, scripts/agent-session) and of the scripts they run, its check reading the
stand-in flag (private_project_stubs.with_flag_url); a scratch HOME; stand-in agents that print
STARTED and their arguments; and the stand-ins of private_project_stubs.py for the flag object and
Dispatch, the flag read with gce_metadata_stub's token. A refusal is exit 2, the refusal on stderr,
and no STARTED line. Case starts every launcher through LeakTestCase.launch (shims/AGENTS.md).

load_check: the check as a module, from a copy whose flag is FLAG_URL, for the tests of its parts.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from gce_metadata_stub import MetadataStub
from private_project_stubs import (
    FLAG_ON,
    PRIV_TOKEN,
    PUB_TOKEN,
    WHOAMI_PRIV,
    WHOAMI_PUB,
    DispatchStub,
    FlagObject,
    with_flag_url,
)
from test_leak_terms import LeakTestCase

DOTFILES = Path(__file__).resolve().parents[2]
CHECK = DOTFILES / "scripts" / "agent-private-check"
# What the launchers run, copied into each scratch tree.
TREE = (
    "shims/omp", "shims/claude", "scripts/agent-session", "scripts/cld", "scripts/agent-private-check",
    "scripts/agent-private-guard", "scripts/agent_private_launch.py", "scripts/agent-private.sh",
    "scripts/agent-secrets-session", "scripts/agent-spend-env.sh", "scripts/envoy",
    "scripts/on-google-vm", "scripts/omp-session-live", "scripts/gh-agent-config", "agent-secrets/broker-url.sh",
    "agent-secrets/broker.env",
)
# The launchers whose private-project check is one marked block; with every block removed they are
# the launchers as they were before the check (test_r9_*).
MARKED = ("scripts/agent-session", "shims/claude")
BLOCK = re.compile(r"(?:^\n)?^[ \t]*# >>> private-project check.*?^[ \t]*# <<< private-project check[^\n]*\n", re.M | re.S)
# The launchers run the check with python3, so the control's check is Python too.
ALWAYS_START = "#!/usr/bin/env python3\nprint('start')\n"
AGENT = '#!/bin/bash\necho "STARTED ${0##*/} $*"\n[[ -z "${AGENT_ENV_OUT:-}" ]] || env -0 > "$AGENT_ENV_OUT"\n'
# A port nothing listens on: "no metadata server here", or no flag object.
CLOSED = "127.0.0.1:9"
# Names and values the fixtures use, kept out of `name=value` shapes the push's secret scan reads as
# a credential: the variable holding a plain bearer, a login's refresh field, the stand-in IAM's first
# minted token (iam_credentials_stub.py). None is a credential.
PLAIN_BEARER_VAR = "DISPATCH_TOKEN"
REFRESH_FIELD = "refresh_token"
MINTED_FIRST = "stub-minted-access-0"
PRIVATE = "PRIV is private. Its sessions run only in its own cluster namespace: `theorem-session launch --project PRIV"
UNANSWERED = "Dispatch did not answer; retry"
# A stand-in agent-secrets: login state `issued`; `register … --exec -- ARGV` refuses a session that
# lists PRIV as the broker does (private_project_pod_only) unless STUB_BROKER_ADMITS is set, else execs ARGV.
AGENT_SECRETS = r"""#!/bin/bash
if [[ "$1 $2" == "machine login-status" ]]; then echo issued; exit 0; fi
if [[ "$1" == register ]]; then
    while (($#)) && [[ "$1" != -- ]]; do shift; done
    shift
    if [[ ",${AGENT_SECRETS_PROJECTS:-}," == *,PRIV,* && -z "${STUB_BROKER_ADMITS:-}" ]]; then
        echo "agent-secrets: register: the broker refused: private_project_pod_only: PRIV" >&2
        exit 1
    fi
    exec "$@"
fi
exit 64
"""


def executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def strip_check(text: str) -> str:
    return BLOCK.sub("", text)


def load_check(directory: Path, flag_url: str = f"http://{CLOSED}/no-flag") -> ModuleType:
    """scripts/agent-private-check as a module, from a copy in DIRECTORY whose flag is FLAG_URL, beside a
    copy of the agent_private_launch.py it imports; launching(check) is that module."""
    copy = directory / "agent-private-check"
    copy.write_text(with_flag_url(CHECK.read_text(encoding="utf-8"), flag_url), encoding="utf-8")
    shutil.copyfile(CHECK.parent / "agent_private_launch.py", directory / "agent_private_launch.py")
    sys.modules.pop("agent_private_launch", None)  # each copy imports its own
    loader = importlib.machinery.SourceFileLoader(f"agent_private_check_{hashlib.sha256(str(copy).encode()).hexdigest()[:8]}",
                                                  str(copy))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module  # its dataclasses look their module up by name
    loader.exec_module(module)
    return module


def launching(check: ModuleType) -> ModuleType:
    """The agent_private_launch module CHECK imported."""
    return sys.modules[check.launch_for.__module__]


class Machine:
    """A scratch developer box: a dotfiles tree, a HOME, stand-in agents, and the flag object,
    Dispatch and metadata server stand-ins its check reads. LAUNCH starts a command (a shim runs
    only through LeakTestCase.launch: a cgroup with a task limit, shims/AGENTS.md)."""

    def __init__(self, root: Path, launch: Callable[..., subprocess.CompletedProcess[str]]) -> None:
        self.root = root
        self.launch = launch
        self.home = root / "home"
        self.config = self.home / ".config"
        (self.home / ".omp" / "agent").mkdir(parents=True)
        self.config.mkdir(parents=True)
        self.stubs = root / "bin"
        tool = root / "tool"
        for name in ("claude", "codex"):
            executable(self.stubs / name, AGENT)
        executable(tool / "omp", AGENT)
        # `mise env -s bash` puts the pinned release's install dir on PATH (test_omp_profile_links.py).
        executable(self.stubs / "mise", f"#!/bin/bash\necho 'export PATH=\"{tool}:$PATH\"'\n")
        self.metadata = MetadataStub.start()
        self.flag = FlagObject(FLAG_ON)
        self.dispatch = DispatchStub({PRIV_TOKEN: WHOAMI_PRIV, PUB_TOKEN: WHOAMI_PUB})
        self.tree = self.make_tree("dotfiles")
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "CLOUDSDK_CONFIG": str(self.config / "gcloud"),
            # No memory scope, no big-disk scratch, no durable disk: every write stays under ROOT.
            "OMP_MEMORY_SCOPE": "off",
            "AGENT_SESSION_NVME_HOME": str(root / "no-nvme"),
            "AGENTBOX_DURABLE": "none",
            "GCE_METADATA_HOST": self.metadata.host,
        }

    def close(self) -> None:
        for server in (self.metadata, self.flag, self.dispatch):
            server.stop()

    def make_tree(self, name: str, *, check: str | None = None, strip: bool = False) -> Path:
        """A copy of the launchers under ROOT/NAME, the check reading this machine's flag; CHECK
        replaces the check, and STRIP removes each marked private-project block."""
        tree = self.root / name
        for rel in TREE:
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DOTFILES / rel, tree / rel)
        (tree / "shims" / "codex").symlink_to("claude")
        (tree / ".claude").mkdir()
        copied = tree / "scripts" / "agent-private-check"
        if check is None:
            copied.write_text(with_flag_url(copied.read_text(encoding="utf-8"), self.flag.url), encoding="utf-8")
        else:
            executable(copied, check)
        if strip:
            for rel in MARKED:
                text = (tree / rel).read_text(encoding="utf-8")
                assert len(BLOCK.findall(text)) == 1, f"{rel} holds no single marked private-project check"
                (tree / rel).write_text(strip_check(text), encoding="utf-8")
        return tree

    def token_file(self, token: str) -> str:
        # By the whole token: the stand-in tokens share their last characters.
        path = self.root / f"token-{hashlib.sha256(token.encode()).hexdigest()[:16]}"
        path.write_text(token + "\n", encoding="utf-8")
        return str(path)

    def bearer(self, token: str) -> dict[str, str]:
        """A session whose bearer is TOKEN through DISPATCH_TOKEN_FILE, at the stand-in Dispatch."""
        return {"DISPATCH_TOKEN_FILE": self.token_file(token), "DISPATCH_URL": self.dispatch.url}

    def envoy_json(self, path: Path, token: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"dispatch": {"enabled": True, "serverUrl": self.dispatch.url, "token": token}}))

    def run(self, argv: list[str], env: dict[str, str], tree: Path, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return self.launch(argv, cwd=cwd or self.root, env={**self.env, "DOTFILES_DIR": str(tree), **env}, timeout=120)

    # Each launcher runs from TREE (the machine's own tree when None) with the variables ENV on top
    # of the machine's. TREE is typed str too, since an ENV unpacked into the call could name it.
    def omp(self, *args: str, tree: Path | str | None = None, **env: str) -> subprocess.CompletedProcess[str]:
        root = Path(tree or self.tree)
        return self.run([str(root / "shims" / "omp"), *args], env, root)

    def cld(self, *args: str, tree: Path | str | None = None, **env: str) -> subprocess.CompletedProcess[str]:
        root = Path(tree or self.tree)
        return self.run([str(root / "scripts" / "cld"), *args], env, root)

    def shim(self, name: str, *args: str, tree: Path | str | None = None, **env: str) -> subprocess.CompletedProcess[str]:
        root = Path(tree or self.tree)
        return self.run([str(root / "shims" / name), *args], env, root)


class Case(LeakTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = self.root
        self.m = Machine(self.tmp, self.launch)
        self.addCleanup(self.m.close)

    def started(self, result: subprocess.CompletedProcess[str], agent: str) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"STARTED {agent}", result.stdout)

    def refused(self, result: subprocess.CompletedProcess[str], why: str) -> None:
        self.assertNotIn("STARTED", result.stdout, "the harness started")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(why, result.stderr)


def kernel_passthrough() -> tuple[set[str], tuple[str, ...]]:
    """The names and prefixes omp/config.yml's eval.envPassthrough admits into a Python kernel."""
    text = (DOTFILES / "omp" / "config.yml").read_text(encoding="utf-8")
    block = re.search(r"^eval:\n  envPassthrough:\n((?:    .*\n|      .*\n)+)", text, re.M)
    assert block, "omp/config.yml has no eval.envPassthrough"
    names: set[str] = set()
    prefixes: list[str] = []
    into: set[str] | list[str] = names
    for line in block.group(1).splitlines():
        if line.strip() == "names:":
            into = names
        elif line.strip() == "prefixes:":
            into = prefixes
        elif line.strip().startswith("- "):
            value = line.strip()[2:]
            names.add(value) if into is names else prefixes.append(value)
    assert {"DISPATCH_"} <= set(prefixes), prefixes
    return names, tuple(prefixes)


# What a kernel keeps of any environment besides envPassthrough: the process basics. The other
# names are this test's seams: the metadata server stands for the machine's own, and the rest keep
# the launch's writes in the scratch tree.
KERNEL_BASE = {"PATH", "HOME", "USER", "LANG", "TERM", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
               "GCE_METADATA_HOST", "OMP_MEMORY_SCOPE", "AGENT_SESSION_NVME_HOME", "AGENTBOX_DURABLE"}


def run_cell(m: Machine, argv: list[str], path: str = "") -> subprocess.CompletedProcess[str]:
    """A Python eval cell that runs ARGV by name, in the environment a kernel of a session with a
    PRIV bearer gets: the session's variables, filtered as envPassthrough filters them. PATH is
    the session's: the tree's shims first, then the stand-in agents, then PATH, then the system."""
    names, prefixes = kernel_passthrough()
    session = {**m.env, "DOTFILES_DIR": str(m.tree), "PATH": f"{m.tree / 'shims'}:{m.stubs}:{path}/usr/bin:/bin",
               **m.bearer(PRIV_TOKEN), "SOME_SESSION_VARIABLE": "dropped by the kernel"}
    kernel = {k: v for k, v in session.items() if k in KERNEL_BASE or k in names or k.startswith(prefixes)}
    assert "DISPATCH_TOKEN_FILE" in kernel and "SOME_SESSION_VARIABLE" not in kernel and "DOTFILES_DIR" not in kernel
    cell = ("import subprocess, sys\n"
            f"done = subprocess.run({argv!r}, capture_output=True, text=True)\n"
            "sys.stdout.write(done.stdout); sys.stderr.write(done.stderr); sys.exit(done.returncode)\n")
    return m.launch([sys.executable, "-c", cell], cwd=m.root, env=kernel, timeout=120)
