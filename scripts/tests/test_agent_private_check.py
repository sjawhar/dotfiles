#!/usr/bin/env python3
"""scripts/agent-private-check and the launchers that run it: a developer box refuses a session
whose Dispatch bearer names a private project, and fails closed when Dispatch cannot answer.

Technique: every case builds a scratch machine (`Machine`): a dotfiles tree holding copies of the
launchers (shims/omp, shims/claude and its link shims/codex, scripts/cld, scripts/agent-session) and
of the scripts they run, so the control can swap the check; a scratch HOME; stand-in agents that
print STARTED and their arguments; and the stand-ins of private_project_stubs.py for the flag object
and Dispatch, the flag read with gce_metadata_stub's token. A refusal is exit 2, the refusal on
stderr, and no STARTED line.

The control (`test_control_a_check_that_always_starts_fails_every_refusal_case`) runs every refusal
case on a machine whose check always prints `start`, and fails naming each case that still passes.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from gce_metadata_stub import STUB_BEARER, MetadataStub
from iam_credentials_stub import IamCredentialsStub
from private_project_stubs import (
    FLAG_OFF,
    FLAG_ON,
    PRIV_TOKEN,
    PUB_TOKEN,
    WHOAMI,
    WHOAMI_PRIV,
    WHOAMI_PUB,
    DispatchStub,
    FederationStub,
    FlagObject,
)
from test_leak_terms import LeakTestCase

DOTFILES = Path(__file__).resolve().parents[2]
# What the launchers run, copied into each scratch tree.
TREE = (
    "shims/omp", "shims/claude", "scripts/agent-session", "scripts/cld", "scripts/agent-private-check",
    "scripts/agent-private-guard", "scripts/agent-secrets-session", "scripts/agent-spend-env.sh", "scripts/envoy",
    "scripts/on-google-vm", "scripts/omp-session-live", "scripts/gh-agent-config", "agent-secrets/broker-url.sh",
    "agent-secrets/broker.env",
)
# The launchers whose private-project check is one marked block; with every block removed they are
# the launchers as they were before the check (test_r9_*).
MARKED = ("scripts/agent-session", "shims/omp", "shims/claude")
BLOCK = re.compile(r"(?:^\n)?^[ \t]*# >>> private-project check.*?^[ \t]*# <<< private-project check[^\n]*\n", re.M | re.S)
# The launchers run the check with python3, so the control's check is Python too.
ALWAYS_START = "#!/usr/bin/env python3\nprint('start')\n"
AGENT = '#!/bin/bash\necho "STARTED ${0##*/} $*"\n[[ -z "${AGENT_ENV_OUT:-}" ]] || env -0 > "$AGENT_ENV_OUT"\n'
# A port nothing listens on: "no metadata server here".
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
        self.tree = self.make_tree("dotfiles")
        self.metadata = MetadataStub.start()
        self.flag = FlagObject(FLAG_ON)
        self.dispatch = DispatchStub({PRIV_TOKEN: WHOAMI_PRIV, PUB_TOKEN: WHOAMI_PUB})
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
            "AGENT_PRIVATE_FLAG_URL": self.flag.url,
        }

    def close(self) -> None:
        for server in (self.metadata, self.flag, self.dispatch):
            server.stop()

    def make_tree(self, name: str, *, check: str | None = None, strip: bool = False) -> Path:
        tree = self.root / name
        for rel in TREE:
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DOTFILES / rel, tree / rel)
        (tree / "shims" / "codex").symlink_to("claude")
        (tree / ".claude").mkdir()
        if check is not None:
            executable(tree / "scripts" / "agent-private-check", check)
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


# The refusal cases. Each takes the machine its launchers run on, so the control can run every one
# of them against a check that always starts.
def omp_with_a_priv_bearer(t: Case, m: Machine) -> None:
    t.refused(m.omp("-p", "ok", **m.bearer(PRIV_TOKEN)), PRIVATE)


def omp_profile_whose_envoy_json_lists_priv(t: Case, m: Machine) -> None:
    m.envoy_json(m.config / "legion" / "envoy.json", PUB_TOKEN)
    m.envoy_json(m.home / ".omp" / "profiles" / "p" / "agent" / "envoy.json", PRIV_TOKEN)
    t.refused(m.omp("--profile", "p", "-p", "ok"), PRIVATE)


def cld_profile_whose_envoy_json_lists_priv(t: Case, m: Machine) -> None:
    m.envoy_json(m.tree / ".claude" / "envoy.json", PUB_TOKEN)
    m.envoy_json(m.tree / ".claude-profiles" / "p" / "envoy.json", PRIV_TOKEN)
    t.refused(m.cld("--profile", "p", "-p", "ok"), PRIVATE)


def claude_started_directly(t: Case, m: Machine) -> None:
    t.refused(m.shim("claude", "-p", "ok", **m.bearer(PRIV_TOKEN)), PRIVATE)


def codex_started_directly(t: Case, m: Machine) -> None:
    t.refused(m.shim("codex", "exec", "ok", **m.bearer(PRIV_TOKEN)), PRIVATE)


def token_patched_between_two_starts(t: Case, m: Machine) -> None:
    env = m.bearer(PUB_TOKEN)
    t.started(m.omp("-p", "ok", **env), "omp")
    m.dispatch.answers[PUB_TOKEN] = WHOAMI_PRIV
    t.refused(m.omp("-p", "ok", **env), PRIVATE)


def whoami_down_after_a_refusing_answer(t: Case, m: Machine) -> None:
    env = m.bearer(PRIV_TOKEN)
    t.refused(m.omp("-p", "ok", **env), PRIVATE)
    m.dispatch.down = True
    t.refused(m.omp("-p", "ok", **env), PRIVATE)


def whoami_down_with_no_cached_refusal(t: Case, m: Machine) -> None:
    m.dispatch.down = True
    t.refused(m.omp("-p", "ok", **m.bearer(PRIV_TOKEN)), UNANSWERED)


def flag_unreadable_and_whoami_down(t: Case, m: Machine) -> None:
    m.flag.body = None
    m.dispatch.down = True
    t.refused(m.omp("-p", "ok", **m.bearer(PUB_TOKEN)), UNANSWERED)


def eval_cell_nested_omp(t: Case, m: Machine) -> None:
    done = run_cell(m, ["omp", "-p", "ok"])
    t.refused(done, PRIVATE)


REFUSALS: tuple[Callable[[Case, Machine], None], ...] = (
    omp_with_a_priv_bearer,
    omp_profile_whose_envoy_json_lists_priv,
    cld_profile_whose_envoy_json_lists_priv,
    claude_started_directly,
    codex_started_directly,
    token_patched_between_two_starts,
    whoami_down_after_a_refusing_answer,
    whoami_down_with_no_cached_refusal,
    flag_unreadable_and_whoami_down,
    eval_cell_nested_omp,
)


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
# names are this test's seams: the flag object's URL and the metadata server stand for the
# machine's own, and the rest keep the launch's writes in the scratch tree.
KERNEL_BASE = {"PATH", "HOME", "USER", "LANG", "TERM", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
               "AGENT_PRIVATE_FLAG_URL", "GCE_METADATA_HOST", "OMP_MEMORY_SCOPE", "AGENT_SESSION_NVME_HOME",
               "AGENTBOX_DURABLE"}


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


class Refusals(Case):
    def test_omp_with_a_priv_bearer_is_refused(self) -> None:
        omp_with_a_priv_bearer(self, self.m)

    def test_omp_profile_whose_envoy_json_lists_priv_is_refused_and_the_default_starts(self) -> None:
        omp_profile_whose_envoy_json_lists_priv(self, self.m)
        self.started(self.m.omp("-p", "ok"), "omp")

    def test_cld_profile_whose_envoy_json_lists_priv_is_refused_and_the_default_starts(self) -> None:
        cld_profile_whose_envoy_json_lists_priv(self, self.m)
        self.started(self.m.cld("-p", "ok"), "claude")

    def test_claude_and_codex_started_directly_are_refused(self) -> None:
        claude_started_directly(self, self.m)
        codex_started_directly(self, self.m)

    def test_a_token_patched_between_two_starts_is_refused_the_second_time(self) -> None:
        token_patched_between_two_starts(self, self.m)

    def test_an_admitting_answer_replaces_a_cached_refusal(self) -> None:
        env = self.m.bearer(PRIV_TOKEN)
        self.refused(self.m.omp("-p", "ok", **env), PRIVATE)
        self.m.dispatch.answers[PRIV_TOKEN] = WHOAMI_PUB
        self.started(self.m.omp("-p", "ok", **env), "omp")
        self.m.dispatch.down = True
        self.refused(self.m.omp("-p", "ok", **env), UNANSWERED)

    def test_whoami_down_after_a_refusing_answer_refuses_with_that_answer(self) -> None:
        whoami_down_after_a_refusing_answer(self, self.m)

    def test_whoami_down_with_no_cached_refusal_refuses(self) -> None:
        whoami_down_with_no_cached_refusal(self, self.m)

    def test_flag_unreadable_and_whoami_down_refuses(self) -> None:
        flag_unreadable_and_whoami_down(self, self.m)

    def test_a_cached_refusal_lasts_24_hours(self) -> None:
        env = self.m.bearer(PRIV_TOKEN)
        self.refused(self.m.omp("-p", "ok", **env), PRIVATE)
        cached = list((self.m.home / ".cache" / "agent-private" / "refusals").iterdir())
        self.assertEqual(len(cached), 1)
        old = time.time() - 24 * 3600 - 5
        os.utime(cached[0], (old, old))
        self.m.dispatch.down = True
        self.refused(self.m.omp("-p", "ok", **env), UNANSWERED)

    def test_the_eval_cell_omp_is_refused(self) -> None:
        eval_cell_nested_omp(self, self.m)

    def test_the_eval_cell_dispatch_whoami_is_refused(self) -> None:
        # The `dispatch` CLI is the omp plugin pin's (shims/dispatch, @sjawhar/pi-envoy in
        # omp/plugins/package.json). It refuses a private bearer only from the release that carries
        # the client's check, whose refusal is `private_project_refused`; until the pin carries it,
        # this case says so and does not run.
        cli = DOTFILES / "omp" / "plugins" / "node_modules" / "@sjawhar" / "pi-envoy" / "bin" / "dispatch"
        bundle = cli.parent.parent / "dist" / "dispatch.js"
        if not bundle.is_file():
            self.skipTest(f"the Dispatch CLI is not installed in this checkout ({bundle})")
        if "private_project_refused" not in bundle.read_text(encoding="utf-8", errors="replace"):
            self.skipTest("the pinned Dispatch CLI predates the client's private-project check (no "
                          "private_project_refused in its dist/dispatch.js); this case runs once "
                          "omp/plugins/package.json pins a release that carries it")
        bun = shutil.which("bun")
        self.assertIsNotNone(bun, "bun, which runs the Dispatch CLI, is not on PATH")
        done = run_cell(self.m, [str(cli), "whoami"], path=f"{Path(str(bun)).parent}:")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("PRIV", done.stderr)
        self.assertNotIn("PRIV", done.stdout)


class StartsAsToday(Case):
    def test_a_pub_bearer_starts_with_one_whoami(self) -> None:
        self.started(self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN)), "omp")
        self.assertEqual(self.m.dispatch.paths(), [WHOAMI])

    def test_r9_flag_false_and_dispatch_down_the_launch_is_the_merge_bases(self) -> None:
        """Flag false, Dispatch down: no Dispatch call, and the agent's whole environment (env -0) is
        what the launcher gives it with every marked private-project block removed, which is the
        launcher before this check."""
        m = self.m
        m.flag.body = FLAG_OFF
        m.dispatch.down = True
        base = m.make_tree("base", strip=True)
        env = m.bearer(PRIV_TOKEN)
        m.envoy_json(m.config / "legion" / "envoy.json", PRIV_TOKEN)

        def agent_env(launch: Callable[..., subprocess.CompletedProcess[str]], tree: Path, *args: str) -> dict[str, str]:
            out = m.root / f"env-{tree.name}"
            done = launch(*args, tree=tree, AGENT_ENV_OUT=str(out), **env)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("STARTED", done.stdout)
            pairs = [item.split("=", 1) for item in out.read_text().split("\0") if item]
            # AGENT_ENV_OUT is this test's own variable, and names a file per tree.
            return {k: v.replace(str(tree), "<TREE>") for k, v in pairs if k != "AGENT_ENV_OUT"}

        for name, launch, args in (("omp", m.omp, ("-p", "ok")), ("cld", m.cld, ("-p", "ok")),
                                   ("claude", lambda *a, **k: m.shim("claude", *a, **k), ("--version",)),
                                   ("codex", lambda *a, **k: m.shim("codex", *a, **k), ("--version",))):
            with self.subTest(launcher=name):
                self.assertEqual(agent_env(launch, m.tree, *args), agent_env(launch, base, *args))
        self.assertEqual(m.dispatch.paths(), [])
        self.assertEqual(m.flag.auth, [f"Bearer {STUB_BEARER}"])  # read once, then its copy
        # Control: a launcher that hands the check's variable on to the session is told apart.
        leaky = m.make_tree("leaky")
        script = leaky / "scripts" / "agent-session"
        script.write_text(script.read_text().replace("unset AGENT_PRIVATE_ENVOY_JSON\n", "", 1))
        leaked = agent_env(m.omp, leaky, "--profile", "p", "-p", "ok")
        self.assertIn("AGENT_PRIVATE_ENVOY_JSON", leaked)
        self.assertNotEqual(leaked, agent_env(m.omp, base, "--profile", "p", "-p", "ok"))

    def test_r9_claude_version_runs_through_the_shim(self) -> None:
        self.m.flag.body = FLAG_OFF
        self.m.dispatch.down = True
        done = self.m.shim("claude", "--version", **self.m.bearer(PRIV_TOKEN))
        self.started(done, "claude --version")
        self.assertEqual(self.m.dispatch.paths(), [])

    def test_r9_flag_unreadable_and_whoami_answering_nothing_private_starts_with_one_call(self) -> None:
        self.m.flag.body = None
        self.started(self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN)), "omp")
        self.assertEqual(self.m.dispatch.paths(), [WHOAMI])

    def test_no_bearer_starts_with_no_call(self) -> None:
        self.started(self.m.omp("-p", "ok"), "omp")
        self.assertEqual(self.m.dispatch.paths(), [])

    def test_the_flag_copy_is_kept_at_most_five_minutes(self) -> None:
        self.m.flag.body = FLAG_OFF
        for _ in range(2):
            self.started(self.m.omp("-p", "ok"), "omp")
        self.assertEqual(len(self.m.flag.auth), 1)
        copies = list((self.m.home / ".cache" / "agent-private").glob("flag*.json"))
        self.assertEqual(len(copies), 1)
        copy = copies[0]
        old = time.time() - 301
        os.utime(copy, (old, old))
        self.started(self.m.omp("-p", "ok"), "omp")
        self.assertEqual(len(self.m.flag.auth), 2)

    def test_a_failed_flag_read_is_not_repeated_by_every_launch(self) -> None:
        # Launches started together would otherwise each wait on flag.lock and read again in turn.
        self.m.flag.body = None
        env = self.m.bearer(PUB_TOKEN)
        for _ in range(2):
            self.started(self.m.omp("-p", "ok", **env), "omp")
        self.assertEqual(len(self.m.flag.auth), 1)
        self.assertEqual(self.m.dispatch.paths(), [WHOAMI, WHOAMI])  # each launch still asks whoami
        (marker,) = (self.m.home / ".cache" / "agent-private").glob("flag*.unreadable")
        old = time.time() - 31
        os.utime(marker, (old, old))
        self.started(self.m.omp("-p", "ok", **env), "omp")
        self.assertEqual(len(self.m.flag.auth), 2)


class LaunchCandidates(Case):
    def test_omp_checks_checkout_bearers_at_its_effective_directory(self) -> None:
        project = self.tmp / "private-checkout"
        self.m.envoy_json(project / ".envoy" / "envoy.json", PRIV_TOKEN)
        for args in (("--cwd", str(project)), (f"--cwd={project}",),
                     ("--cwd", str(self.tmp), "--cwd", str(project))):
            with self.subTest(args=args):
                self.refused(self.m.omp(*args, "-p", "ok"), PRIVATE)
        self.refused(self.m.run([str(self.m.tree / "shims" / "omp"), "-p", "ok"], {}, self.m.tree,
                               cwd=project), PRIVATE)
        # A flag in the prompt, after --, must not change the launch directory.
        self.started(self.m.omp("-p", "--", "--cwd", str(project)), "omp")

    def test_omp_checks_project_dotenv_with_and_without_cwd(self) -> None:
        project = self.tmp / "private-dotenv"
        project.mkdir()
        (project / ".env").write_text(f'export DISPATCH_TOKEN="{PRIV_TOKEN}" # comment\n'
                                    f'DISPATCH_URL={self.m.dispatch.url}\n')
        self.refused(self.m.omp("--cwd", str(project), "-p", "ok"), PRIVATE)
        self.refused(self.m.run([str(self.m.tree / "shims" / "omp"), "-p", "ok"], {}, self.m.tree,
                               cwd=project), PRIVATE)

    def test_omp_checks_the_launch_directorys_dotenv_when_cwd_moves_it(self) -> None:
        # omp reads the project .env at import, from the directory it started in, before --cwd applies.
        launch = self.tmp / "launch-with-private-dotenv"
        launch.mkdir()
        (launch / ".env").write_text(f"DISPATCH_TOKEN={PRIV_TOKEN}\nDISPATCH_URL={self.m.dispatch.url}\n")
        public = self.tmp / "public-checkout"
        public.mkdir()
        self.refused(self.m.run([str(self.m.tree / "shims" / "omp"), "--cwd", str(public), "-p", "ok"], {},
                                self.m.tree, cwd=launch), PRIVATE)

    def test_a_bearer_dispatch_rejects_is_named_by_where_it_comes_from(self) -> None:
        stale = "dsp_test_revoked_0123456789abcdef"
        (self.m.home / ".env").write_text(f"DISPATCH_TOKEN={stale}\n")
        done = self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN))
        self.refused(done, f"Dispatch rejects the bearer from DISPATCH_TOKEN in {self.m.home / '.env'} (HTTP 401)")
        self.assertNotIn(stale, done.stderr)
        self.assertNotIn(UNANSWERED, done.stderr)

    def test_a_machine_with_no_identity_names_the_offline_way_out(self) -> None:
        # No metadata server and no box file (a laptop): the flag can never be read, so a start that
        # holds a bearer asks Dispatch and is refused while Dispatch cannot answer.
        self.m.dispatch.down = True
        done = self.m.omp("-p", "ok", GCE_METADATA_HOST=CLOSED, **self.m.bearer(PUB_TOKEN))
        self.refused(done, UNANSWERED)
        self.assertIn("this machine has no Google identity to read the private-projects flag with", done.stderr)
        self.assertIn("To work offline, start without the Dispatch bearer from DISPATCH_TOKEN_FILE.", done.stderr)
        self.assertEqual(self.m.flag.auth, [])
        # Control: with no bearer the same machine starts, and asks Dispatch nothing.
        calls = len(self.m.dispatch.paths())
        self.started(self.m.omp("-p", "ok", GCE_METADATA_HOST=CLOSED), "omp")
        self.assertEqual(len(self.m.dispatch.paths()), calls)

    def test_agent_dotenv_private_token_file_is_checked_beside_a_public_environment_bearer(self) -> None:
        agent = self.tmp / "custom-agent"
        agent.mkdir()
        (agent / ".env").write_text(f"DISPATCH_TOKEN_FILE='{self.m.token_file(PRIV_TOKEN)}'\n")
        for name in ("PI_CODING_AGENT_DIR", "OMP_CODING_AGENT_DIR"):
            with self.subTest(name=name):
                self.refused(self.m.omp("-p", "ok", **{name: str(agent)}, **self.m.bearer(PUB_TOKEN)), PRIVATE)

    def test_config_root_and_home_dotenv_bearers_are_also_checked(self) -> None:
        for directory in (self.m.home / ".omp", self.m.home):
            with self.subTest(directory=directory):
                dotenv = directory / ".env"
                dotenv.write_text(f"DISPATCH_TOKEN={PRIV_TOKEN}\nDISPATCH_URL={self.m.dispatch.url}\n")
                try:
                    self.refused(self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN)), PRIVATE)
                finally:
                    dotenv.unlink()

    def test_named_profile_dotenv_files_are_checked(self) -> None:
        profile = self.m.home / ".omp" / "profiles" / "p"
        self.m.envoy_json(profile / "agent" / "envoy.json", PUB_TOKEN)
        for directory in (profile, profile / "agent"):
            with self.subTest(directory=directory):
                dotenv = directory / ".env"
                dotenv.write_text(f"DISPATCH_TOKEN={PRIV_TOKEN}\nDISPATCH_URL={self.m.dispatch.url}\n")
                try:
                    self.refused(self.m.omp("--profile", "p", "-p", "ok", **self.m.bearer(PUB_TOKEN)), PRIVATE)
                finally:
                    dotenv.unlink()

    def test_an_unreadable_dotenv_or_its_token_file_refuses(self) -> None:
        dotenv = self.m.root / ".env"
        dotenv.mkdir()
        self.refused(self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN)), "dotenv file")
        dotenv.rmdir()
        dotenv.write_text("DISPATCH_TOKEN_FILE=missing-token\n")
        self.refused(self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN)), "DISPATCH_TOKEN_FILE")
        self.assertEqual(self.m.dispatch.paths(), [])

    def test_flag_false_does_not_read_dotenv_or_its_bearers(self) -> None:
        self.m.flag.body = FLAG_OFF
        self.m.dispatch.down = True
        (self.m.root / ".env").mkdir()
        self.started(self.m.omp("-p", "ok", DISPATCH_TOKEN_FILE="missing-token"), "omp")
        self.assertEqual(self.m.dispatch.paths(), [])

    def test_a_multiline_note_is_not_an_extra_bearer(self) -> None:
        (self.m.root / ".env").write_text(f'NOTE="first\nDISPATCH_TOKEN={PRIV_TOKEN}\nlast"\n'
                                         f"DISPATCH_TOKEN={PUB_TOKEN} # comment\n")
        self.started(self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN)), "omp")
        self.assertEqual(self.m.dispatch.paths(), [WHOAMI])

    def test_dotenv_can_select_a_private_envoy_file(self) -> None:
        selected = self.tmp / "selected-envoy.json"
        self.m.envoy_json(selected, PRIV_TOKEN)
        (self.m.root / ".env").write_text(f"ENVOY_CONFIG_FILE='{selected}'\n")
        self.refused(self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN)), PRIVATE)

    def test_codex_checks_the_checkout_selected_by_cd(self) -> None:
        project = self.tmp / "codex-project"
        self.m.envoy_json(project / ".envoy" / "envoy.json", PRIV_TOKEN)
        for args in (("-C", str(project)), ("--cd", str(project)), (f"--cd={project}",),
                     ("-C", str(self.tmp), "-C", str(project))):
            with self.subTest(args=args):
                self.refused(self.m.shim("codex", *args, "exec", "ok"), PRIVATE)
        self.started(self.m.shim("codex", "exec", "--", "-C", str(project)), "codex")

    def test_public_candidates_start_with_one_whoami_per_distinct_pair(self) -> None:
        other = "dsp_second_public_bearer_0123456789abcdef"
        self.m.dispatch.answers[other] = WHOAMI_PUB
        self.m.envoy_json(self.m.config / "legion" / "envoy.json", other)
        self.m.envoy_json(self.m.root / ".envoy" / "envoy.json", PUB_TOKEN)
        (self.m.root / ".env").write_text(f"DISPATCH_TOKEN={other}\nDISPATCH_URL={self.m.dispatch.url}\n")
        agent_dotenv = self.m.home / ".omp" / "agent" / ".env"
        agent_dotenv.parent.mkdir(parents=True, exist_ok=True)
        agent_dotenv.write_text(f"DISPATCH_TOKEN={PUB_TOKEN}\n")
        self.started(self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN)), "omp")
        self.assertCountEqual(self.m.dispatch.calls,
                              [("GET", WHOAMI, f"Bearer {PUB_TOKEN}"), ("GET", WHOAMI, f"Bearer {other}")])

    def test_each_files_bearer_stays_with_its_own_server(self) -> None:
        other = DispatchStub({PUB_TOKEN: WHOAMI_PUB})
        self.addCleanup(other.stop)
        self.m.envoy_json(self.m.root / ".envoy" / "envoy.json", PUB_TOKEN)
        selected = self.m.config / "legion" / "envoy.json"
        selected.parent.mkdir(parents=True, exist_ok=True)
        selected.write_text(json.dumps({"dispatch": {"serverUrl": other.url, "token": PUB_TOKEN}}))
        self.started(self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN)), "omp")
        self.assertEqual(self.m.dispatch.paths(), [WHOAMI])
        self.assertEqual(other.paths(), [WHOAMI])


class GuardBoundaries(Case):
    """The production guard, including failures below its HTTP and cache boundaries."""

    def test_every_launcher_rejects_a_broken_check(self) -> None:
        checks = {
            "crashes": "raise SystemExit(1)\n",
            "empty": "pass\n",
            "garbage": "print('not a verdict')\n",
            "start_then_crashes": "print('start'); raise SystemExit(1)\n",
        }
        for name, body in checks.items():
            tree = self.m.make_tree(f"broken-{name}", check=body)
            for launcher in ("omp", "cld", "claude", "codex"):
                with self.subTest(check=name, launcher=launcher):
                    if launcher == "omp":
                        result = self.m.omp("-p", "ok", tree=tree)
                    elif launcher == "cld":
                        result = self.m.cld("-p", "ok", tree=tree)
                    else:
                        result = self.m.shim(launcher, "--version", tree=tree)
                    self.refused(result, "gave no answer")

    def load_check(self):
        loader = importlib.machinery.SourceFileLoader("private_guard_boundaries", str(DOTFILES / "scripts" / "agent-private-check"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module

    def test_token_command_non_utf8_stderr_does_not_change_its_bearer(self) -> None:
        config = self.m.config / "legion" / "envoy.json"
        self.m.envoy_json(config, f"!printf '\\377' >&2; printf '%s' '{PUB_TOKEN}'")
        self.started(self.m.omp("-p", "ok"), "omp")
        self.assertEqual(self.m.dispatch.paths(), [WHOAMI])

    def test_omp_checks_the_config_selected_by_the_machine_environment(self) -> None:
        self.m.envoy_json(self.m.config / "legion" / "envoy.json", PUB_TOKEN)
        selected = self.m.config / "selected" / "envoy.json"
        self.m.envoy_json(selected, PRIV_TOKEN)
        environment = self.m.config / "agent-session" / "env"
        environment.parent.mkdir(parents=True)
        environment.write_text(f"export ENVOY_CONFIG_FILE='{selected}'\n")
        self.refused(self.m.omp("-p", "ok"), PRIVATE)

    def test_cld_checks_the_profile_selected_by_the_machine_environment(self) -> None:
        self.m.envoy_json(self.m.tree / ".claude" / "envoy.json", PUB_TOKEN)
        selected = self.m.config / "selected-claude"
        self.m.envoy_json(selected / "envoy.json", PRIV_TOKEN)
        environment = self.m.config / "agent-session" / "env"
        environment.parent.mkdir(parents=True)
        environment.write_text(f"export CLAUDE_CONFIG_DIR='{selected}'\n")
        self.refused(self.m.cld("-p", "ok"), PRIVATE)

    def test_a_checkout_private_bearer_is_checked_instead_of_the_public_profile(self) -> None:
        self.m.envoy_json(self.m.config / "legion" / "envoy.json", PUB_TOKEN)
        self.m.envoy_json(self.m.root / ".envoy" / "envoy.json", PRIV_TOKEN)
        self.refused(self.m.omp("-p", "ok"), PRIVATE)

    def test_codex_ignores_the_retired_opencode_config_directory(self) -> None:
        neutral = self.m.config / "legion" / "envoy.json"
        retired = self.m.config / "opencode"
        self.m.envoy_json(neutral, PRIV_TOKEN)
        self.m.envoy_json(retired / "envoy.json", PUB_TOKEN)
        self.refused(self.m.shim("codex", "exec", "ok", OPENCODE_CONFIG_DIR=str(retired)), PRIVATE)
        # Reversing the pair must not refuse the neutral file's public bearer either.
        self.m.envoy_json(neutral, PUB_TOKEN)
        self.m.envoy_json(retired / "envoy.json", PRIV_TOKEN)
        self.started(self.m.shim("codex", "exec", "ok", OPENCODE_CONFIG_DIR=str(retired)), "codex")

    def test_null_private_projects_names_no_private_project(self) -> None:
        self.m.dispatch.answers[PUB_TOKEN] = {"kind": "agent", "private_projects": None}
        self.started(self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN)), "omp")

    def test_malformed_box_credentials_make_the_flag_unreadable(self) -> None:
        box = self.m.config / "gcloud" / "box_credential_config.json"
        box.parent.mkdir(parents=True)
        box.write_text("[]")
        self.started(self.m.omp("-p", "ok", GCE_METADATA_HOST=CLOSED, **self.m.bearer(PUB_TOKEN)), "omp")
        self.assertEqual(self.m.flag.auth, [])
        self.assertEqual(self.m.dispatch.paths(), [WHOAMI])

    def test_invalid_http_responses_are_unanswered_without_secret_bytes(self) -> None:
        import http.client

        check = self.load_check()
        with mock.patch.object(check.urllib.request.OpenerDirector, "open",
                               side_effect=http.client.IncompleteRead(PRIV_TOKEN.encode())):
            with self.assertRaises(check.NoAnswer) as failed:
                check.fetch("http://unused.test/", proxy=False)
        self.assertNotIn(PRIV_TOKEN, str(failed.exception))

    def test_every_environment_and_config_bearer_is_checked_even_when_shadowed(self) -> None:
        config = self.m.config / "legion" / "envoy.json"
        for file_token, plain_token, config_token, refused in (
                (PUB_TOKEN, PRIV_TOKEN, PRIV_TOKEN, True),
                (PRIV_TOKEN, PUB_TOKEN, PUB_TOKEN, True),
                (None, PUB_TOKEN, PRIV_TOKEN, True),
                (None, PRIV_TOKEN, PUB_TOKEN, True),
                (PUB_TOKEN, PUB_TOKEN, PUB_TOKEN, False)):
            with self.subTest(file_token=file_token, plain_token=plain_token, config_token=config_token):
                self.m.envoy_json(config, config_token)
                result = self.m.omp("-p", "ok", DISPATCH_URL=self.m.dispatch.url, **{PLAIN_BEARER_VAR: plain_token},
                                    DISPATCH_TOKEN_FILE=self.m.token_file(file_token) if file_token else "")
                if refused:
                    self.refused(result, PRIVATE)
                else:
                    self.started(result, "omp")

    def test_an_unreadable_token_file_does_not_fall_through(self) -> None:
        result = self.m.omp("-p", "ok", DISPATCH_URL=self.m.dispatch.url, DISPATCH_TOKEN=PUB_TOKEN,
                            DISPATCH_TOKEN_FILE=str(self.tmp / "missing-token"))
        self.refused(result, "DISPATCH_TOKEN_FILE")
        self.assertEqual(self.m.dispatch.paths(), [])

    def test_a_failing_token_command_refuses_without_printing_its_streams(self) -> None:
        self.m.envoy_json(self.m.config / "legion" / "envoy.json", f"!printf '%s' '{PRIV_TOKEN}' >&2; exit 7")
        result = self.m.omp("-p", "ok")
        self.refused(result, "dispatch.token command")
        self.assertNotIn(PRIV_TOKEN, result.stdout + result.stderr)
        self.assertEqual(self.m.dispatch.paths(), [])

    def test_a_token_command_private_bearer_is_refused(self) -> None:
        self.m.envoy_json(self.m.config / "legion" / "envoy.json", f"!printf '%s' '{PRIV_TOKEN}'")
        self.refused(self.m.omp("-p", "ok"), PRIVATE)

    def test_two_private_keys_are_named_in_the_refusal(self) -> None:
        self.m.dispatch.answers[PRIV_TOKEN] = {"kind": "agent", "private_projects": ["ZZZ", "PRIV"]}
        result = self.m.omp("-p", "ok", **self.m.bearer(PRIV_TOKEN))
        self.refused(result, "PRIV, ZZZ are private")
        self.assertIn("theorem-session launch --project <KEY>", result.stderr)

    def test_a_corrupt_flag_copy_is_read_again(self) -> None:
        self.m.flag.body = FLAG_OFF
        self.started(self.m.omp("-p", "ok", **self.m.bearer(PRIV_TOKEN)), "omp")
        copies = list((self.m.home / ".cache" / "agent-private").glob("flag*.json"))
        self.assertEqual(len(copies), 1)
        copies[0].write_text("not JSON")
        self.m.flag.body = FLAG_ON
        self.refused(self.m.omp("-p", "ok", **self.m.bearer(PRIV_TOKEN)), PRIVATE)
        self.assertEqual(len(self.m.flag.auth), 2)

    def test_an_override_flag_copy_is_not_used_without_the_override(self) -> None:
        check = self.load_check()
        check.FLAG_URL = self.m.flag.url
        alternate = FlagObject(FLAG_OFF)
        self.addCleanup(alternate.stop)
        config = self.m.config / "legion" / "envoy.json"
        environment = {**self.m.env, "AGENT_PRIVATE_ENVOY_JSON": str(config), "DISPATCH_TOKEN_FILE": "",
                       "DISPATCH_TOKEN": PRIV_TOKEN, "DISPATCH_URL": self.m.dispatch.url}
        with mock.patch.dict(os.environ, environment), mock.patch.object(Path, "cwd", return_value=self.m.root):
            with mock.patch.dict(os.environ, {"AGENT_PRIVATE_FLAG_URL": alternate.url}):
                self.assertEqual(check.check(), "start")
            self.assertEqual(self.m.dispatch.paths(), [])
            with mock.patch.dict(os.environ, {"AGENT_PRIVATE_FLAG_URL": ""}):
                with self.assertRaisesRegex(check.Refused, "PRIV is private"):
                    check.check()
        self.assertEqual(len(alternate.auth), 1)
        self.assertEqual(len(self.m.flag.auth), 1)

    def test_an_explicit_public_bearer_does_not_hide_the_checkout_private_bearer(self) -> None:
        self.m.envoy_json(self.m.root / ".envoy" / "envoy.json", PRIV_TOKEN)
        self.refused(self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN)), PRIVATE)

    def test_a_disabled_dispatch_section_does_not_hide_an_explicit_private_bearer(self) -> None:
        config = self.m.config / "legion" / "envoy.json"
        self.m.envoy_json(config, PUB_TOKEN)
        config.write_text(json.dumps({"dispatch": {"enabled": False}}))
        self.refused(self.m.omp("-p", "ok", **self.m.bearer(PRIV_TOKEN)), PRIVATE)

    def test_an_empty_token_file_does_not_fall_through_to_a_different_bearer(self) -> None:
        result = self.m.omp("-p", "ok", DISPATCH_TOKEN_FILE=self.m.token_file(""),
                            DISPATCH_TOKEN=PUB_TOKEN, DISPATCH_URL=self.m.dispatch.url)
        self.refused(result, "bearer")
        self.assertEqual(self.m.dispatch.paths(), [])

    def test_a_malformed_whoami_is_not_an_admitting_answer(self) -> None:
        for answer in ({}, {"error": "upstream unavailable"}, {"kind": "unexpected"},
                       {"kind": "agent", "private_projects": False},
                       {"kind": "agent", "private_projects": "PRIV"},
                       {"kind": "agent", "private_projects": {}},
                       {"kind": "agent", "private_projects": [""]}):
            with self.subTest(answer=answer):
                self.m.dispatch.answers[PUB_TOKEN] = answer
                self.refused(self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN)), UNANSWERED)

    def test_a_relative_token_command_uses_its_config_directory(self) -> None:
        config = self.m.config / "legion" / "envoy.json"
        self.m.envoy_json(config, "!cat token")
        config.with_name("token").write_text(PRIV_TOKEN)
        (self.m.root / "token").write_text(PUB_TOKEN)
        self.refused(self.m.omp("-p", "ok"), PRIVATE)

    def test_a_public_bearer_starts_when_the_refusal_cache_cannot_be_removed(self) -> None:
        import hashlib

        kept = self.m.home / ".cache" / "agent-private" / "refusals" / hashlib.sha256(PUB_TOKEN.encode()).hexdigest()
        kept.mkdir(parents=True)
        self.started(self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN)), "omp")

    def test_a_private_bearer_keeps_its_reason_when_the_cache_is_unwritable(self) -> None:
        cache = self.m.home / ".cache" / "agent-private"
        cache.mkdir(parents=True)
        (cache / "refusals").write_text("not a directory")
        self.refused(self.m.omp("-p", "ok", **self.m.bearer(PRIV_TOKEN)), PRIVATE)

    def test_a_failed_cache_write_and_cleanup_are_both_optional(self) -> None:
        check = self.load_check()
        with mock.patch.object(Path, "write_bytes", side_effect=PermissionError), \
                mock.patch.object(Path, "unlink", side_effect=PermissionError):
            check.keep(self.tmp / "copy", b'{"enabled": false}')

    def test_an_invalid_bearer_never_appears_in_the_refusal(self) -> None:
        token = PRIV_TOKEN + "\ninvalid-header"
        result = self.m.omp("-p", "ok", DISPATCH_TOKEN=token, DISPATCH_URL=self.m.dispatch.url)
        self.refused(result, "bearer")
        self.assertNotIn(PRIV_TOKEN, result.stdout + result.stderr)
        self.assertEqual(self.m.dispatch.paths(), [])

    def test_an_unexpected_exception_does_not_print_credentials(self) -> None:
        import contextlib
        import io

        check = self.load_check()
        output = io.StringIO()
        with mock.patch.object(check, "check", side_effect=ValueError(PRIV_TOKEN)), contextlib.redirect_stdout(output):
            self.assertEqual(check.check_main([]), 0)
        self.assertTrue(output.getvalue().startswith("refuse "))
        self.assertNotIn(PRIV_TOKEN, output.getvalue())

    def test_dns_resolution_obeys_the_http_deadline(self) -> None:
        check = self.load_check()
        check.TIMEOUT_S = 0.05
        with mock.patch("socket.getaddrinfo", side_effect=lambda *a, **k: time.sleep(0.5)):
            with self.assertRaises(check.NoAnswer):
                check.fetch("http://lookup.invalid/", proxy=False)

    def test_a_redirect_does_not_forward_the_bearer(self) -> None:
        import http.server
        import threading

        check = self.load_check()
        destination = self.m.dispatch.url + WHOAMI
        # Control: the destination can see this bearer when contacted directly.
        check.fetch(destination, {"Authorization": f"Bearer {PUB_TOKEN}"}, proxy=False)
        self.assertEqual(self.m.dispatch.paths(), [WHOAMI])
        self.m.dispatch.calls.clear()

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", destination)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(check.NoAnswer):
                check.fetch(f"http://127.0.0.1:{server.server_port}/", {"Authorization": f"Bearer {PUB_TOKEN}"}, proxy=False)
            self.assertEqual(self.m.dispatch.paths(), [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_a_slow_response_body_obeys_the_http_deadline(self) -> None:
        import contextlib

        check = self.load_check()
        check.TIMEOUT_S = 0.05

        class SlowBody:
            def read(self):
                time.sleep(0.5)
                return b"a body that outlived the request deadline"

        with mock.patch.object(check.urllib.request.OpenerDirector, "open",
                               return_value=contextlib.nullcontext(SlowBody())):
            with self.assertRaises(check.NoAnswer):
                check.fetch("http://unused.test/", proxy=False)

    def test_a_token_commands_background_child_cannot_hold_the_launch_open(self) -> None:
        import contextlib
        import signal

        check = self.load_check()
        check.COMMAND_TIMEOUT_S = 0.05
        real_popen = subprocess.Popen
        real_killpg = os.killpg

        def exists(pid: int) -> bool:
            # Reap it ourselves if the test runner is a subreaper; otherwise init does so.
            with contextlib.suppress(ChildProcessError):
                os.waitpid(pid, os.WNOHANG)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            return True

        def await_gone(pid: int) -> None:
            deadline = time.monotonic() + 5
            while exists(pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(exists(pid), f"token-command child {pid} still exists")

        # Run the blind implementation first: killing only the shell must leave its child alive.
        for shell_only in (True, False):
            with self.subTest(shell_only=shell_only):
                directory = self.tmp / ("shell-only" if shell_only else "whole-group")
                directory.mkdir()
                config = directory / "envoy.json"
                started = directory / "child-started"
                executable(directory / "token-child", f"#!{sys.executable}\n"
                           "import os, time\nfrom pathlib import Path\n"
                           "marker = Path('child-started.tmp')\n"
                           "marker.write_text(str(os.getpid()))\n"
                           "marker.replace('child-started')\ntime.sleep(60)\n")
                self.m.envoy_json(config, "!./token-child & wait")
                processes = []

                def spawn(*args, **kwargs):
                    process = real_popen(*args, **kwargs)
                    processes.append(process)
                    # The production timeout begins only after the child has actually started.
                    deadline = time.monotonic() + 10
                    while not started.is_file() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(started.is_file(), "the token-command child never started")
                    return process

                kill = (lambda pid, sig: os.kill(pid, sig)) if shell_only else real_killpg
                try:
                    with mock.patch.dict(os.environ, {"AGENT_PRIVATE_ENVOY_JSON": str(config), "DISPATCH_TOKEN_FILE": "", "DISPATCH_TOKEN": "", "DISPATCH_URL": ""}), \
                            mock.patch.object(Path, "cwd", return_value=self.m.root), \
                            mock.patch.object(check.subprocess, "Popen", side_effect=spawn), \
                            mock.patch.object(check.os, "killpg", side_effect=kill):
                        with self.assertRaises(check.Refused):
                            check.bearers()
                    pid = int(started.read_text())
                    if shell_only:
                        self.assertTrue(exists(pid), "the shell-only control did not leave a live child")
                    else:
                        await_gone(pid)
                finally:
                    for process in processes:
                        with contextlib.suppress(ProcessLookupError):
                            real_killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                    if started.is_file():
                        await_gone(int(started.read_text()))

    def test_a_token_command_is_not_put_on_a_process_argument(self) -> None:
        command = f"printf '%s' '{PRIV_TOKEN}'"
        self.m.envoy_json(self.m.config / "legion" / "envoy.json", "!" + command)
        check = self.load_check()
        real_popen = subprocess.Popen
        arguments = []

        def popen(argv, *args, **kwargs):
            arguments.append(argv)
            return real_popen(argv, *args, **kwargs)

        with mock.patch.dict(os.environ, {**self.m.env, "AGENT_PRIVATE_ENVOY_JSON": str(self.m.config / "legion" / "envoy.json"), "DISPATCH_TOKEN_FILE": "", "DISPATCH_TOKEN": "", "DISPATCH_URL": ""}), \
                mock.patch.object(Path, "cwd", return_value=self.m.root), \
                mock.patch.object(check.subprocess, "Popen", side_effect=popen):
            config = self.m.config / "legion" / "envoy.json"
            self.assertEqual(check.bearers(), [(self.m.dispatch.url, PRIV_TOKEN, f"dispatch.token in {config}")])
        self.assertTrue(arguments)
        self.assertNotIn(PRIV_TOKEN, repr(arguments))


class TheFlagsCredential(Case):
    def test_the_flag_is_read_with_the_metadata_servers_token(self) -> None:
        self.started(self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN)), "omp")
        self.assertEqual(self.m.flag.auth, [f"Bearer {STUB_BEARER}"])

    def test_a_persons_login_is_never_used(self) -> None:
        """A person's application_default_credentials.json, no metadata server, no box file: the
        flag is unreadable (never asked for), the login's token endpoint is never called, and the
        launch takes the whoami path."""
        m = self.m
        oauth = DispatchStub()  # records any call to the login's token endpoint
        self.addCleanup(oauth.stop)
        adc = m.config / "gcloud" / "application_default_credentials.json"
        adc.parent.mkdir(parents=True)
        adc.write_text(json.dumps({"type": "authorized_user", "client_id": "x.apps.googleusercontent.com",
                                   "client_secret": "not-a-secret", REFRESH_FIELD: "not-a-real-value",
                                   "token_uri": f"{oauth.url}/token"}))
        done = m.omp("-p", "ok", GCE_METADATA_HOST=CLOSED, GOOGLE_APPLICATION_CREDENTIALS=str(adc),
                     **m.bearer(PUB_TOKEN))
        self.started(done, "omp")
        self.assertEqual(m.flag.auth, [])
        self.assertEqual(oauth.calls, [])
        self.assertEqual(m.dispatch.paths(), [WHOAMI])

    def test_off_google_the_flag_is_read_as_the_box_files_account(self) -> None:
        """No metadata server and the box file installers/gcloud-box.sh writes: its federated token,
        then the impersonated account's token, which the flag is read with."""
        m = self.m
        federation = FederationStub()
        iam = IamCredentialsStub.start()
        self.addCleanup(federation.stop)
        self.addCleanup(iam.stop)
        account = "dev-box@example.iam.gserviceaccount.com"
        box = m.config / "gcloud" / "box_credential_config.json"
        box.parent.mkdir(parents=True)
        box.write_text(json.dumps(federation.credential(
            f"{iam.url}/v1/projects/-/serviceAccounts/{account}:generateAccessToken")))
        self.started(m.omp("-p", "ok", GCE_METADATA_HOST=CLOSED, **m.bearer(PUB_TOKEN)), "omp")
        self.assertEqual([c[:3] for c in federation.calls], [("GET", "/imds/token", "True"), ("POST", "/v1/token", "")])
        self.assertEqual(urllib.parse.parse_qs(federation.calls[1][3]), {
            "grant_type": ["urn:ietf:params:oauth:grant-type:token-exchange"],
            "audience": ["//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/azure/providers/entra"],
            "scope": ["https://www.googleapis.com/auth/cloud-platform"],
            "requested_token_type": ["urn:ietf:params:oauth:token-type:access_token"],
            "subject_token_type": ["urn:ietf:params:oauth:token-type:jwt"],
            "subject_token": ["stub-azure-subject"],
        })
        self.assertEqual(iam.calls, [("generateAccessToken", account, f"Bearer {FederationStub.FEDERATED}",
                                     {"scope": ["https://www.googleapis.com/auth/devstorage.read_only"], "lifetime": "300s"})])
        self.assertEqual(m.flag.auth, [f"Bearer {MINTED_FIRST}"])


class TheBroker(Case):
    """No bearer and AGENT_SECRETS_PROJECTS=PRIV: the check starts the session (step 1 decides), and
    the broker's refusal at `agent-secrets register --exec` stops it before the harness runs."""

    def setUp(self) -> None:
        super().setUp()
        unit = self.m.home / ".config" / "systemd" / "user" / "agent-secrets-helper.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("")
        executable(self.m.stubs / "agent-secrets", AGENT_SECRETS)

    def test_the_brokers_refusal_stops_the_launch(self) -> None:
        done = self.m.omp("-p", "ok", AGENT_SECRETS_PROJECTS="PRIV")
        self.assertNotIn("STARTED", done.stdout)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("private_project_pod_only", done.stderr)

    def test_control_the_broker_admits_and_it_starts(self) -> None:
        self.started(self.m.omp("-p", "ok", AGENT_SECRETS_PROJECTS="PRIV", STUB_BROKER_ADMITS="1"), "omp")


class Control(Case):
    def test_control_a_check_that_always_starts_fails_every_refusal_case(self) -> None:
        """The always-start mutant must start every forbidden harness, not fail for some other reason."""
        m = self.m
        m.tree = m.make_tree("always-starts", check=ALWAYS_START)

        def lost_refusal(result: subprocess.CompletedProcess[str], why: str) -> None:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("STARTED ", result.stdout, "the mutant did not start the harness")
            with self.assertRaisesRegex(AssertionError, "the harness started"):
                Case.refused(self, result, why)

        for case in REFUSALS:
            with self.subTest(case=case.__name__):
                m.dispatch.answers = {PRIV_TOKEN: WHOAMI_PRIV, PUB_TOKEN: WHOAMI_PUB}
                m.dispatch.down = False
                m.flag.body = FLAG_ON
                shutil.rmtree(m.home / ".cache", ignore_errors=True)
                with mock.patch.object(self, "refused", side_effect=lost_refusal) as refusal:
                    case(self, m)
                self.assertGreater(refusal.call_count, 0, "the case exercised no refusal")


if __name__ == "__main__":
    unittest.main()
