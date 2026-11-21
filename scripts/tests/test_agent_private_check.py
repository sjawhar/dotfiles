#!/usr/bin/env python3
"""The launchers that run scripts/agent-private-check: a developer box refuses a session whose
Dispatch bearer names a private project, and fails closed when Dispatch cannot answer.

Technique: every case runs the real launchers on a scratch machine (private_check_support.Machine)
against stand-ins for the flag object, Dispatch and the metadata server. The check's own parts are
tested alone in test_agent_private_check_units.py.

The control (`test_control_a_check_that_always_starts_fails_every_refusal_case`) runs every refusal
case on a machine whose check always prints `start`, and fails naming each case that still passes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import unittest
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from gce_metadata_stub import STUB_BEARER
from iam_credentials_stub import IamCredentialsStub
from private_check_support import (
    AGENT_SECRETS,
    ALWAYS_START,
    CLOSED,
    DOTFILES,
    MINTED_FIRST,
    PLAIN_BEARER_VAR,
    PRIVATE,
    REFRESH_FIELD,
    UNANSWERED,
    Case,
    Machine,
    executable,
    run_cell,
)
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
                                   ("claude", lambda *a, **k: m.shim("claude", *a, **k), ("-p", "ok")),
                                   ("codex", lambda *a, **k: m.shim("codex", *a, **k), ("exec", "ok"))):
            with self.subTest(launcher=name):
                self.assertEqual(agent_env(launch, m.tree, *args), agent_env(launch, base, *args))
        self.assertEqual(m.dispatch.paths(), [])
        self.assertEqual(m.flag.auth, [f"Bearer {STUB_BEARER}"])  # read once, then its copy
        # Control: a launcher that hands one of the check's variables on to the session is told apart.
        leaky = m.make_tree("leaky")
        script = leaky / "scripts" / "agent-session"
        text = script.read_text()
        call = 'agent_private_check "${agent##*/}" agent-session "$@" || exit 2\n'
        self.assertEqual(text.count(call), 1)
        script.write_text(text.replace(call, "export AGENT_PRIVATE_LEAK=1\n" + call))
        leaked = agent_env(m.omp, leaky, "-p", "ok")
        self.assertEqual(leaked.get("AGENT_PRIVATE_LEAK"), "1")
        self.assertNotEqual(leaked, agent_env(m.omp, base, "-p", "ok"))

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
        copy = self.m.home / ".cache" / "agent-private" / "flag.json"
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
        marker = self.m.home / ".cache" / "agent-private" / "flag.unreadable"
        old = time.time() - 31
        os.utime(marker, (old, old))
        self.started(self.m.omp("-p", "ok", **env), "omp")
        self.assertEqual(len(self.m.flag.auth), 2)

    def test_no_variable_moves_the_flag(self) -> None:
        # The check reads its own flag (FLAG_ON here): a variable naming another (FLAG_OFF) is ignored.
        other = FlagObject(FLAG_OFF)
        self.addCleanup(other.stop)
        self.refused(self.m.omp("-p", "ok", AGENT_PRIVATE_FLAG_URL=other.url, **self.m.bearer(PRIV_TOKEN)), PRIVATE)
        self.assertEqual(other.auth, [])
        # Control: the check's own flag reading false starts the same launch with no Dispatch call.
        self.m.flag.body = FLAG_OFF
        shutil.rmtree(self.m.home / ".cache")
        calls = len(self.m.dispatch.paths())
        self.started(self.m.omp("-p", "ok", AGENT_PRIVATE_FLAG_URL=other.url, **self.m.bearer(PRIV_TOKEN)), "omp")
        self.assertEqual(len(self.m.dispatch.paths()), calls)


class CommandsThatStartNoSession(Case):
    """A management command, help or version starts no agent session: no check, so no flag read and
    no Dispatch call, even with a PRIV bearer and Dispatch down."""

    LAUNCHES = (("omp", ("session", "pack", "x")), ("omp", ("plugin", "list")), ("omp", ("--version",)),
                ("omp", ("config",)), ("cld", ("--version",)), ("claude", ("--version",)), ("claude", ("mcp", "list")),
                ("codex", ("login", "status")), ("codex", ("--version",)))

    def start(self, launcher: str, args: tuple[str, ...], **env: str) -> subprocess.CompletedProcess[str]:
        if launcher == "omp":
            return self.m.omp(*args, **env)
        if launcher == "cld":
            return self.m.cld(*args, **env)
        return self.m.shim(launcher, *args, **env)

    def test_management_commands_start_with_no_check(self) -> None:
        self.m.dispatch.down = True
        for launcher, args in self.LAUNCHES:
            with self.subTest(launcher=launcher, args=args):
                self.started(self.start(launcher, args, **self.m.bearer(PRIV_TOKEN)),
                             "claude" if launcher == "cld" else launcher)
        self.assertEqual(self.m.dispatch.paths(), [])
        self.assertEqual(self.m.flag.auth, [])

    def test_control_an_agent_start_and_a_hoisted_subcommand_are_checked(self) -> None:
        for launcher, args in (("omp", ("-p", "ok")), ("omp", ("--model", "x", "session")),
                               ("claude", ("-p", "ok")), ("codex", ("exec", "ok")), ("cld", ("-p", "ok"))):
            with self.subTest(launcher=launcher, args=args):
                self.refused(self.start(launcher, args, **self.m.bearer(PRIV_TOKEN)), PRIVATE)


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

    def test_omp_checks_the_project_dotenv_of_the_directory_it_starts_in_only(self) -> None:
        project = self.tmp / "private-dotenv"
        project.mkdir()
        (project / ".env").write_text(f'export DISPATCH_TOKEN="{PRIV_TOKEN}" # comment\n'
                                    f'DISPATCH_URL={self.m.dispatch.url}\n')
        self.refused(self.m.run([str(self.m.tree / "shims" / "omp"), "-p", "ok"], {}, self.m.tree,
                               cwd=project), PRIVATE)
        # omp reads the .env of the directory it starts in, never the --cwd directory's.
        self.started(self.m.omp("--cwd", str(project), "-p", "ok"), "omp")
        self.assertEqual(self.m.dispatch.paths(), [WHOAMI])  # the first launch's only

    def test_a_cwd_directorys_dotenv_runs_nothing_and_names_no_server(self) -> None:
        """A checkout's .env, which omp never reads under --cwd, can neither run a command nor send
        the session's bearer, or any file, to a server it names."""
        listener = DispatchStub({PUB_TOKEN: WHOAMI_PUB})
        self.addCleanup(listener.stop)
        # The session's own config names the real Dispatch, so the environment's bearer goes there.
        chosen = self.m.config / "legion" / "envoy.json"
        chosen.parent.mkdir(parents=True, exist_ok=True)
        chosen.write_text(json.dumps({"dispatch": {"enabled": True, "serverUrl": self.m.dispatch.url}}))
        checkout = self.tmp / "hostile-checkout"
        checkout.mkdir()
        pwned = self.tmp / "PWNED"
        (checkout / "envoy.json").write_text(json.dumps({"dispatch": {
            "enabled": True, "serverUrl": listener.url, "token": f"!touch '{pwned}'; echo attacker-token"}}))
        secret = self.tmp / "some-secret-file"
        file_token = "dsp_a_file_the_checkout_names"  # made up
        secret.write_text(file_token + "\n")
        (checkout / ".env").write_text(f"PI_CODING_AGENT_DIR=.\nDISPATCH_URL={listener.url}\n"
                                       f"DISPATCH_TOKEN_FILE={secret}\n")
        self.started(self.m.omp("--cwd", str(checkout), "-p", "ok", **{PLAIN_BEARER_VAR: PUB_TOKEN,
                                                                         "DISPATCH_URL": ""}), "omp")
        self.assertFalse(pwned.exists(), "the checkout's token command ran")
        self.assertEqual(listener.calls, [])
        # Control: launched from inside the checkout, omp does read that .env, and so does the check.
        self.m.run([str(self.m.tree / "shims" / "omp"), "-p", "ok"], {PLAIN_BEARER_VAR: PUB_TOKEN, "DISPATCH_URL": ""},
                   self.m.tree, cwd=checkout)
        self.assertIn(("GET", WHOAMI, f"Bearer {file_token}"), listener.calls)
        self.assertFalse(pwned.exists(), "a config only a dotenv file names ran its token command")

    def test_each_bearer_goes_to_its_own_sources_server(self) -> None:
        # The environment's bearer is never sent to a server a dotenv file names.
        listener = DispatchStub({PUB_TOKEN: WHOAMI_PUB})
        self.addCleanup(listener.stop)
        self.m.envoy_json(self.m.config / "legion" / "envoy.json", PUB_TOKEN)
        (self.m.root / ".env").write_text(f"DISPATCH_URL={listener.url}\n")
        self.started(self.m.omp("-p", "ok", **{PLAIN_BEARER_VAR: PUB_TOKEN, "DISPATCH_URL": ""}), "omp")
        self.assertEqual(listener.calls, [])
        self.assertEqual(self.m.dispatch.paths(), [WHOAMI])
        # Control: the file's own bearer goes to the server written beside it.
        (self.m.root / ".env").write_text(f"DISPATCH_URL={listener.url}\nDISPATCH_TOKEN={PUB_TOKEN}\n")
        self.started(self.m.omp("-p", "ok", DISPATCH_URL=""), "omp")
        self.assertEqual(listener.paths(), [WHOAMI])

    def test_a_config_only_a_dotenv_file_names_runs_no_command(self) -> None:
        ran = self.tmp / "ran"
        selected = self.tmp / "selected" / "envoy.json"
        self.m.envoy_json(selected, f"!touch '{ran}'; printf '%s' '{PUB_TOKEN}'")
        (self.m.root / ".env").write_text(f"ENVOY_CONFIG_FILE='{selected}'\n")
        self.refused(self.m.omp("-p", "ok"), f"{selected}, which only ENVOY_CONFIG_FILE in {self.m.root / '.env'} names")
        self.assertFalse(ran.exists())
        # Control: the same file named by the environment runs its command, and its bearer starts.
        (self.m.root / ".env").unlink()
        self.started(self.m.omp("-p", "ok", ENVOY_CONFIG_FILE=str(selected)), "omp")
        self.assertTrue(ran.exists())

    def test_a_dotenv_key_counts_only_where_omp_applies_it(self) -> None:
        private_home = self.tmp / "private-home"
        self.m.envoy_json(private_home / ".config" / "legion" / "envoy.json", PRIV_TOKEN)
        # HOME is always set, so omp never takes it from a dotenv file; nor XDG_CONFIG_HOME, set here.
        (self.m.root / ".env").write_text(f"HOME={private_home}\nXDG_CONFIG_HOME={private_home / '.config'}\n")
        self.started(self.m.omp("-p", "ok"), "omp")
        # Control: with XDG_CONFIG_HOME unset, omp takes the dotenv file's, and so does the check.
        self.refused(self.m.omp("-p", "ok", XDG_CONFIG_HOME=""), PRIVATE)

    def test_the_launch_directorys_dotenv_is_checked_when_cwd_moves_omp(self) -> None:
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

    def test_a_malformed_bearer_names_its_source(self) -> None:
        done = self.m.omp("-p", "ok", DISPATCH_TOKEN=PRIV_TOKEN + " trailing-word", DISPATCH_URL=self.m.dispatch.url)
        self.refused(done, "the Dispatch bearer from DISPATCH_TOKEN is empty or holds whitespace or a control "
                           "character; fix or remove it")
        self.assertNotIn(PRIV_TOKEN, done.stdout + done.stderr)
        self.assertEqual(self.m.dispatch.paths(), [])

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
        self.refused(self.m.omp("-p", "ok", PI_CODING_AGENT_DIR=str(agent), **self.m.bearer(PUB_TOKEN)), PRIVATE)
        # Control: omp reads no OMP_CODING_AGENT_DIR from its environment (only a dotenv file's mirrors).
        self.started(self.m.omp("-p", "ok", OMP_CODING_AGENT_DIR=str(agent), **self.m.bearer(PUB_TOKEN)), "omp")

    def test_the_config_root_is_pi_config_dirs_as_in_the_shim(self) -> None:
        for name, refused in (("PI_CONFIG_DIR", True), ("OMP_CONFIG_DIR", False)):
            root = self.m.home / f".omp-{name.lower()}"
            (root / "agent").mkdir(parents=True)
            (root / "agent" / ".env").write_text(f"DISPATCH_TOKEN={PRIV_TOKEN}\nDISPATCH_URL={self.m.dispatch.url}\n")
            with self.subTest(name=name):
                result = self.m.omp("-p", "ok", **{name: root.name}, **self.m.bearer(PUB_TOKEN))
                self.refused(result, PRIVATE) if refused else self.started(result, "omp")

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

    def test_a_profile_the_machine_environment_names_gets_its_profile_envoy_json(self) -> None:
        self.m.envoy_json(self.m.config / "legion" / "envoy.json", PUB_TOKEN)
        self.m.envoy_json(self.m.home / ".omp" / "profiles" / "p" / "agent" / "envoy.json", PRIV_TOKEN)
        # Control: with no profile named, the neutral file's public bearer starts.
        self.started(self.m.omp("-p", "ok"), "omp")
        environment = self.m.config / "agent-session" / "env"
        environment.parent.mkdir(parents=True)
        environment.write_text("export OMP_PROFILE=p\n")
        self.refused(self.m.omp("-p", "ok"), PRIVATE)

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

    def test_a_file_dispatch_is_off_for_is_not_asked_about(self) -> None:
        # The client's dispatchTarget: no serverUrl, no `enabled: true` and no DISPATCH_URL is Dispatch off.
        config = self.m.config / "legion" / "envoy.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"dispatch": {"token": PRIV_TOKEN}}))
        self.started(self.m.omp("-p", "ok"), "omp")
        self.assertEqual(self.m.dispatch.paths(), [])
        # Controls: a serverUrl, or DISPATCH_URL naming its server, makes the file's bearer one the
        # session sends, and it is refused.
        config.write_text(json.dumps({"dispatch": {"serverUrl": self.m.dispatch.url, "token": PRIV_TOKEN}}))
        self.refused(self.m.omp("-p", "ok"), PRIVATE)
        config.write_text(json.dumps({"dispatch": {"token": PRIV_TOKEN}}))
        shutil.rmtree(self.m.home / ".cache")  # the refusal kept above would answer for it
        self.refused(self.m.omp("-p", "ok", DISPATCH_URL="http://localhost:9"), UNANSWERED)


class AResumedSession(Case):
    ID = "01a1f00d-1234-7abc-8def-0123456789ab"

    def session(self, cwd: Path) -> Path:
        path = self.m.home / ".omp" / "agent" / "sessions" / "-x" / f"2026-11-21T00-00-00-000Z_{self.ID}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"type": "title", "title": "t"}) + "\n" +
                        json.dumps({"type": "session", "id": self.ID, "cwd": str(cwd)}) + "\n")
        return path

    def test_the_resumed_sessions_checkout_is_checked(self) -> None:
        checkout = self.tmp / "resumed-checkout"
        self.m.envoy_json(checkout / ".envoy" / "envoy.json", PRIV_TOKEN)
        file = self.session(checkout)
        for args in (("--resume", str(file)), ("--resume", self.ID), (f"--session={self.ID}",),
                     ("-r", self.ID[:13]), ("--restore", self.ID)):
            with self.subTest(args=args):
                self.refused(self.m.omp(*args), PRIVATE)
        # Control: a resumed session whose directory holds no private bearer starts.
        public = self.tmp / "public"
        public.mkdir()
        self.session(public)
        self.started(self.m.omp("--resume", self.ID), "omp")

    def test_a_session_under_session_dir_is_found_there(self) -> None:
        checkout = self.tmp / "resumed-checkout"
        self.m.envoy_json(checkout / ".envoy" / "envoy.json", PRIV_TOKEN)
        store = self.tmp / "session-store"
        store.mkdir()
        (store / f"2026-11-21T00-00-00-000Z_{self.ID}.jsonl").write_text(
            json.dumps({"type": "session", "id": self.ID, "cwd": str(checkout)}) + "\n")
        self.refused(self.m.omp("--session-dir", str(store), "--resume", self.ID), PRIVATE)
        self.refused(self.m.omp(f"--session-dir={store}", "-r", self.ID[:13]), PRIVATE)
        # Control: without --session-dir the id names no session the check can find, so nothing private is read.
        self.started(self.m.omp("--resume", self.ID), "omp")
        self.assertEqual(self.m.dispatch.paths(), [WHOAMI, WHOAMI])


class ClaudeCodeSettings(Case):
    TRUST = "for Dispatch, and Claude Code applies it once this folder's trust is accepted"

    def claude(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return self.m.run([str(self.m.tree / "shims" / "claude"), *args], {}, self.m.tree, cwd=cwd)

    def trust(self, *folders: Path) -> None:
        (self.m.home / ".claude.json").write_text(json.dumps(
            {"projects": {str(folder): {"hasTrustDialogAccepted": True} for folder in folders}}))

    def private_settings(self, folder: Path) -> None:
        (folder / ".claude").mkdir(parents=True)
        (folder / ".claude" / "settings.json").write_text(json.dumps(
            {"env": {"DISPATCH_TOKEN": PRIV_TOKEN, "DISPATCH_URL": self.m.dispatch.url}}))

    def test_an_untrusted_clones_settings_refuse_an_interactive_start_and_are_not_sent(self) -> None:
        """Claude Code applies a project's `env` block in a -p run, in a trusted folder, or once the
        person accepts the folder's trust dialog, which no check can see before the start: so an
        interactive start in an untrusted clone whose settings name a bearer is refused, and nothing
        the file names is read or sent."""
        listener = DispatchStub({PUB_TOKEN: WHOAMI_PUB})
        self.addCleanup(listener.stop)
        clone = self.tmp / "untrusted-clone"
        (clone / ".claude").mkdir(parents=True)
        secret = self.tmp / "single-line-secret"
        secret.write_text(PUB_TOKEN + "\n")
        (clone / ".claude" / "settings.json").write_text(json.dumps({"env": {
            "DISPATCH_TOKEN_FILE": str(secret), "DISPATCH_URL": listener.url}}))
        self.refused(self.claude(clone, "hello"), self.TRUST)
        self.refused(self.m.run([str(self.m.tree / "scripts" / "cld"), "hello"], {}, self.m.tree, cwd=clone), self.TRUST)
        self.assertEqual(listener.calls, [])
        # Controls: a -p run applies the block, and so does an interactive start once the folder is trusted.
        self.started(self.claude(clone, "-p", "hello"), "claude")
        self.assertEqual(listener.paths(), [WHOAMI])
        self.trust(clone)
        self.started(self.claude(clone, "hello"), "claude")
        self.assertEqual(listener.paths(), [WHOAMI, WHOAMI])

    def test_a_subfolder_of_a_trusted_repository_is_checked(self) -> None:
        repo = self.tmp / "trusted-repo"
        (repo / ".git").mkdir(parents=True)
        self.private_settings(repo / "sub")
        self.trust(repo)
        self.refused(self.claude(repo / "sub", "hello"), PRIVATE)
        # Control: a record above the repository's root does not reach it, so its settings are not
        # read: the start is refused for trust, and Dispatch is asked nothing.
        self.trust(self.tmp)
        self.refused(self.claude(repo / "sub", "hello"), self.TRUST)
        self.assertEqual(self.m.dispatch.paths(), [WHOAMI])

    def test_a_clone_under_a_trusted_parent_is_not_trusted(self) -> None:
        parent = self.tmp / "trusted-parent"
        clone = parent / "clone"
        (clone / ".git").mkdir(parents=True)
        self.private_settings(clone)
        self.trust(parent)
        self.refused(self.claude(clone, "hello"), self.TRUST)
        self.assertEqual(self.m.dispatch.paths(), [])
        # Control: the same folder outside a repository is trusted through its parent, and checked.
        shutil.rmtree(clone / ".git")
        self.refused(self.claude(clone, "hello"), PRIVATE)

    def test_a_short_flag_cluster_holding_p_is_print_mode(self) -> None:
        folder = self.tmp / "untrusted-folder"
        self.private_settings(folder)
        for flags in ("-cp", "-pc", "--print"):
            with self.subTest(flags=flags):
                self.refused(self.claude(folder, flags, "hi"), PRIVATE)
        # Control: `-c` is an interactive start, so the settings are not read: refused for trust, never asked.
        calls = len(self.m.dispatch.paths())
        self.refused(self.claude(folder, "-c", "hi"), self.TRUST)
        self.assertEqual(len(self.m.dispatch.paths()), calls)

    def test_a_settings_env_block_bearer_is_checked(self) -> None:
        project = self.tmp / "claude-project"
        settings = project / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"env": {"DISPATCH_TOKEN": PRIV_TOKEN, "DISPATCH_URL": self.m.dispatch.url}}))
        for argv in ([str(self.m.tree / "shims" / "claude"), "-p", "ok"], [str(self.m.tree / "scripts" / "cld"), "-p", "ok"]):
            with self.subTest(launcher=Path(argv[0]).name):
                self.refused(self.m.run(argv, {}, self.m.tree, cwd=project), PRIVATE)
        user = self.tmp / "claude-config"
        (user / "settings.json").parent.mkdir(parents=True)
        (user / "settings.json").write_text(settings.read_text())
        self.refused(self.m.shim("claude", "-p", "ok", CLAUDE_CONFIG_DIR=str(user)), PRIVATE)
        # Control: codex reads no Claude Code settings.
        self.started(self.m.run([str(self.m.tree / "shims" / "codex"), "exec", "ok"], {}, self.m.tree, cwd=project), "codex")

    def test_a_settings_files_env_block_is_checked(self) -> None:
        # scripts/cld passes its profile's cld-settings.json with --settings, after its own flags.
        cld_settings = self.m.tree / ".claude" / "cld-settings.json"
        cld_settings.write_text(json.dumps({"env": {"DISPATCH_TOKEN": PRIV_TOKEN, "DISPATCH_URL": self.m.dispatch.url}}))
        self.refused(self.m.cld("-p", "ok"), PRIVATE)
        # Control: `cld --version` is a version, past cld's own flags, so it starts with no check.
        self.started(self.m.cld("--version"), "claude")
        cld_settings.unlink()
        self.started(self.m.cld("-p", "ok"), "claude")
        inline = json.dumps({"env": {"DISPATCH_TOKEN": PRIV_TOKEN, "DISPATCH_URL": self.m.dispatch.url}})
        multiline = json.dumps({"model": "sonnet", "env": {"DISPATCH_TOKEN": PRIV_TOKEN, "DISPATCH_URL": self.m.dispatch.url}},
                               indent=2)
        (self.tmp / "named.json").write_text(inline)
        for args in (("--settings", str(self.tmp / "named.json")), (f"--settings={inline}",), ("--settings", multiline)):
            with self.subTest(args=args[0][:12], lines=args[-1].count("\n") + 1):
                self.refused(self.m.shim("claude", *args, "-p", "ok"), PRIVATE)
        # Control: a multi-line inline value with no bearer starts, through cld too.
        harmless = json.dumps({"model": "sonnet", "env": {"HARMLESS": "1"}}, indent=2)
        self.started(self.m.shim("claude", "--settings", harmless, "-p", "ok"), "claude")
        self.started(self.m.cld("--settings", harmless, "-p", "ok"), "claude")


class GuardBoundaries(Case):
    """The launchers with a broken check, and the check's failures seen through them."""

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
                        result = self.m.shim(launcher, "exec" if launcher == "codex" else "-p", "ok", tree=tree)
                    self.refused(result, "gave no answer")

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
        self.m.dispatch.answers[PUB_TOKEN] = {"kind": "agent", "projects": ["PUB"], "private_projects": None}
        self.started(self.m.omp("-p", "ok", **self.m.bearer(PUB_TOKEN)), "omp")

    def test_malformed_box_credentials_make_the_flag_unreadable(self) -> None:
        box = self.m.config / "gcloud" / "box_credential_config.json"
        box.parent.mkdir(parents=True)
        box.write_text("[]")
        self.started(self.m.omp("-p", "ok", GCE_METADATA_HOST=CLOSED, **self.m.bearer(PUB_TOKEN)), "omp")
        self.assertEqual(self.m.flag.auth, [])
        self.assertEqual(self.m.dispatch.paths(), [WHOAMI])

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
        self.m.dispatch.answers[PRIV_TOKEN] = {"kind": "agent", "projects": ["ZZZ", "PRIV"], "private_projects": ["ZZZ", "PRIV"]}
        result = self.m.omp("-p", "ok", **self.m.bearer(PRIV_TOKEN))
        self.refused(result, "PRIV, ZZZ are private")
        self.assertIn("theorem-session launch --project <KEY>", result.stderr)

    def test_a_key_that_is_not_a_project_key_is_never_printed(self) -> None:
        self.m.dispatch.answers[PRIV_TOKEN] = {"kind": "agent", "projects": ["PRIV"], "private_projects": ["\x1b]0;x\x07PRIV"]}
        result = self.m.omp("-p", "ok", **self.m.bearer(PRIV_TOKEN))
        self.refused(result, UNANSWERED)
        self.assertNotIn("\x1b", result.stderr)

    def test_a_corrupt_flag_copy_is_read_again(self) -> None:
        self.m.flag.body = FLAG_OFF
        self.started(self.m.omp("-p", "ok", **self.m.bearer(PRIV_TOKEN)), "omp")
        copy = self.m.home / ".cache" / "agent-private" / "flag.json"
        copy.write_text("not JSON")
        self.m.flag.body = FLAG_ON
        self.refused(self.m.omp("-p", "ok", **self.m.bearer(PRIV_TOKEN)), PRIVATE)
        self.assertEqual(len(self.m.flag.auth), 2)

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
        self.refused(result, "the Dispatch bearer from DISPATCH_TOKEN_FILE is empty")
        self.assertEqual(self.m.dispatch.paths(), [])

    def test_a_malformed_whoami_is_not_an_admitting_answer(self) -> None:
        for answer in ({}, {"error": "upstream unavailable"}, {"kind": "unexpected"},
                       {"kind": "agent", "private_projects": False},
                       {"kind": "agent", "private_projects": "PRIV"},
                       {"kind": "agent", "private_projects": {}},
                       {"kind": "agent", "private_projects": [""]},
                       {"kind": "agent", "projects": ["PUB"]},
                       {"kind": "agent", "projects": [], "private_projects": [], "pad": "x" * 70000}):
            with self.subTest(answer=str(answer)[:60]):
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

    def test_an_invalid_bearer_never_appears_in_the_refusal(self) -> None:
        token = PRIV_TOKEN + "\ninvalid-header"
        result = self.m.omp("-p", "ok", DISPATCH_TOKEN=token, DISPATCH_URL=self.m.dispatch.url)
        self.refused(result, "bearer")
        self.assertNotIn(PRIV_TOKEN, result.stdout + result.stderr)
        self.assertEqual(self.m.dispatch.paths(), [])


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
