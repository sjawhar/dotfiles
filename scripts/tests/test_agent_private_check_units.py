#!/usr/bin/env python3
"""The parts of scripts/agent-private-check, each tested alone: its HTTP layer, its cache writes,
its token commands, whoami's answer, and a resumed session's directory.

Technique: each case loads the check as a module from a copy in its own scratch directory
(private_check_support.load_check), with a scratch HOME, and starts only the stand-in servers it
needs. The launchers that run the check are tested in test_agent_private_check.py.
"""

from __future__ import annotations

import contextlib
import http.client
import http.server
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from private_check_support import DOTFILES, executable, launching, load_check
from private_project_stubs import PRIV_TOKEN, PUB_TOKEN, WHOAMI, DispatchStub


class Unit(unittest.TestCase):
    tmp: Path
    home: Path
    check: Any  # the check as a module: a case sets its constants
    launching: Any  # the agent_private_launch module that check imported

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="agent-private-check-unit-")
        self.addCleanup(scratch.cleanup)
        self.tmp = Path(scratch.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.check = load_check(self.tmp)
        self.launching = launching(self.check)

    def dispatch(self, answers: dict[str, Any] | None = None, routes: dict[str, Any] | None = None) -> DispatchStub:
        server = DispatchStub(answers, routes)
        self.addCleanup(server.stop)
        return server

    def environment(self, **extra: str) -> contextlib.AbstractContextManager[object]:
        """The check's view of a session: this case's HOME and caches, none of this process's own Dispatch,
        Envoy, omp or Claude Code settings, and EXTRA."""
        base = {key: value for key, value in os.environ.items()
                if not key.startswith(("DISPATCH_", "ENVOY_", "OMP_", "PI_", "CLAUDE_", "XDG_"))}
        return mock.patch.dict(os.environ, {**base, "HOME": str(self.home), "XDG_CACHE_HOME": str(self.home / ".cache"),
                                            "XDG_CONFIG_HOME": str(self.home / ".config"), **extra}, clear=True)


class TheHttpLayer(Unit):
    def test_invalid_http_responses_are_unanswered_without_secret_bytes(self) -> None:
        check = self.check
        with mock.patch.object(check.urllib.request.OpenerDirector, "open",
                               side_effect=http.client.IncompleteRead(PRIV_TOKEN.encode())):
            with self.assertRaises(check.NoAnswer) as failed:
                check.fetch("http://unused.test/", proxy=False)
        self.assertNotIn(PRIV_TOKEN, str(failed.exception))

    def test_dns_resolution_obeys_the_http_deadline(self) -> None:
        check = self.check
        check.TIMEOUT_S = 0.05
        with mock.patch("socket.getaddrinfo", side_effect=lambda *a, **k: time.sleep(0.5)):
            with self.assertRaises(check.NoAnswer):
                check.fetch("http://lookup.invalid/", proxy=False)

    def test_a_slow_response_body_obeys_the_http_deadline(self) -> None:
        check = self.check
        check.TIMEOUT_S = 0.05

        class SlowBody:
            def read(self, size: int = -1) -> bytes:
                time.sleep(0.5)
                return b"a body that outlived the request deadline"

        with mock.patch.object(check.urllib.request.OpenerDirector, "open",
                               return_value=contextlib.nullcontext(SlowBody())):
            with self.assertRaises(check.NoAnswer):
                check.fetch("http://unused.test/", proxy=False)

    def test_an_answer_larger_than_64_kib_is_no_answer(self) -> None:
        server = self.dispatch(routes={"/big": {"pad": "x" * (64 * 1024)}, "/small": {"pad": "x" * 60000}})
        with self.assertRaisesRegex(self.check.NoAnswer, "larger than 65536 bytes"):
            self.check.fetch(server.url + "/big", proxy=False)
        # Control: an answer under the bound is read whole.
        self.assertEqual(len(json.loads(self.check.fetch(server.url + "/small", proxy=False))["pad"]), 60000)

    def test_a_redirect_does_not_forward_the_bearer(self) -> None:
        check = self.check
        dispatch = self.dispatch({PUB_TOKEN: {"kind": "agent"}})
        destination = dispatch.url + WHOAMI
        # Control: the destination can see this bearer when contacted directly.
        check.fetch(destination, {"Authorization": f"Bearer {PUB_TOKEN}"}, proxy=False)
        self.assertEqual(dispatch.paths(), [WHOAMI])
        dispatch.calls.clear()

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server's name
                self.send_response(302)
                self.send_header("Location", destination)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - http.server's name
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(check.NoAnswer):
                check.fetch(f"http://127.0.0.1:{server.server_port}/", {"Authorization": f"Bearer {PUB_TOKEN}"}, proxy=False)
            self.assertEqual(dispatch.paths(), [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class WhoamisAnswer(Unit):
    def answer(self, body: dict[str, Any]) -> list[str]:
        dispatch = self.dispatch({PUB_TOKEN: body})
        return self.check.whoami(dispatch.url, PUB_TOKEN)

    def test_only_project_keys_are_taken_from_an_answer(self) -> None:
        for keys in (["\x1b]0;pwned\x07"], ["priv"], ["PRIV-1"], ["9LIVES"], [""]):
            with self.subTest(keys=keys), self.assertRaisesRegex(self.check.NoAnswer, "not a list of project keys"):
                self.answer({"kind": "agent", "projects": keys, "private_projects": keys})
        # Control: keys as Dispatch writes them, sorted.
        self.assertEqual(self.answer({"kind": "agent", "projects": ["ZZ9", "PRIV"], "private_projects": ["ZZ9", "PRIV"]}),
                         ["PRIV", "ZZ9"])

    def test_an_answer_naming_a_project_without_private_projects_is_no_answer(self) -> None:
        # Today's whoami names a scoped bearer's `project` and no lists at all.
        for body in ({"kind": "agent", "projects": ["PUB"]},
                     {"kind": "agent", "owner": "o", "project": "PRIV", "scopes": [], "service": None, "session": None}):
            with self.subTest(body=body), self.assertRaisesRegex(self.check.NoAnswer, "no private_projects"):
                self.answer(body)
        # Controls: both lists, an answer naming no project at all, and Go's nil slice.
        self.assertEqual(self.answer({"kind": "agent", "projects": ["PUB"], "private_projects": []}), [])
        self.assertEqual(self.answer({"kind": "agent"}), [])
        self.assertEqual(self.answer({"kind": "agent", "owner": "o", "project": None, "scopes": []}), [])
        self.assertEqual(self.answer({"kind": "agent", "projects": ["PUB"], "private_projects": None}), [])

    def test_a_kept_refusal_holds_only_project_keys(self) -> None:
        kept = self.tmp / "kept"
        kept.write_text(json.dumps({"private_projects": ["\x1b[2J"]}))
        self.assertEqual(self.check.kept_refusal(kept), [])
        kept.write_text(json.dumps({"private_projects": ["PRIV"]}))
        self.assertEqual(self.check.kept_refusal(kept), ["PRIV"])


class AResumedSession(Unit):
    ID = "01a1f00d-1234-7abc-8def-0123456789ab"

    def session(self, name: str, *lines: dict[str, Any]) -> Path:
        path = self.home / ".omp" / "agent" / "sessions" / "-somewhere" / f"2026-11-21T00-00-00-000Z_{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        return path

    def test_the_session_files_cwd_is_its_directory(self) -> None:
        agent = self.home / ".omp" / "agent"
        file = self.session(self.ID, {"type": "title", "title": "t"},
                            {"type": "session", "id": self.ID, "cwd": "/srv/checkout"}, {"type": "model_change"})
        for target in (str(file), self.ID, self.ID[:13]):
            with self.subTest(target=target):
                self.assertEqual(self.launching.resumed_cwd(target, self.tmp, agent), Path("/srv/checkout"))

    def test_what_cannot_be_resolved_is_none(self) -> None:
        agent = self.home / ".omp" / "agent"
        self.session(self.ID, {"type": "session", "cwd": "/srv/a"})
        self.session(self.ID[:8] + "-ffff-7abc-8def-0123456789ab", {"type": "session", "cwd": "/srv/b"})
        for target in (self.ID[:8], "not-an-id", "--model", str(self.tmp / "missing.jsonl")):
            with self.subTest(target=target):
                self.assertIsNone(self.launching.resumed_cwd(target, self.tmp, agent))
        relative = self.session("relative", {"type": "session", "cwd": "srv/a"})
        self.assertIsNone(self.launching.resumed_cwd(str(relative), self.tmp, agent))

    def test_a_session_dir_is_where_an_id_is_looked_up(self) -> None:
        agent = self.home / ".omp" / "agent"
        elsewhere = self.tmp / "session-dir"
        elsewhere.mkdir()
        (elsewhere / f"2026-11-21T00-00-00-000Z_{self.ID}.jsonl").write_text(
            json.dumps({"type": "session", "id": self.ID, "cwd": "/srv/elsewhere"}) + "\n")
        self.assertEqual(self.launching.resumed_cwd(self.ID, self.tmp, agent, elsewhere), Path("/srv/elsewhere"))
        # Control: without the session directory the id is under the agent's sessions only, where it is not.
        self.assertIsNone(self.launching.resumed_cwd(self.ID, self.tmp, agent))


class TheLaunch(Unit):
    """launch_for: what one harness start reads, from its harness, its caller and its argv."""

    def launch(self, harness: str, caller: str, *args: str, **extra: str) -> Any:
        with self.environment(**extra), mock.patch.object(Path, "cwd", return_value=self.tmp):
            launch = self.check.launch_for(harness, caller, list(args))
            # What it reads is read when first asked for: ask inside this case's environment.
            for name in ("envoy_json", "sources", "merged", "checkouts"):
                getattr(launch, name)
            return launch

    def test_omp_reads_its_cwd_profile_and_command(self) -> None:
        neutral = self.home / ".config" / "legion" / "envoy.json"
        project = self.tmp / "project"
        for args, cwd in ((("--cwd", str(project), "-p", "ok"), project), ((f"--cwd={project}",), project),
                          (("--cwd", "x", "--cwd", str(project)), project), (("-p", "--", "--cwd", str(project)), self.tmp)):
            with self.subTest(args=args):
                launch = self.launch("omp", "agent-session", *args)
                self.assertEqual((launch.cwd, launch.command, launch.envoy_json), (cwd, args[0], neutral))
        profile = self.home / ".omp" / "profiles" / "p" / "agent" / "envoy.json"
        self.assertEqual(self.launch("omp", "agent-session", "--profile", "p").envoy_json, profile)
        self.assertEqual(self.launch("omp", "agent-session", "-p", "ok", OMP_PROFILE="p").envoy_json, profile)
        # OMP_PROFILE, set even to nothing, wins over PI_PROFILE, as omp resolves it; `default` is no profile.
        self.assertEqual(self.launch("omp", "agent-session", OMP_PROFILE="", PI_PROFILE="p").envoy_json, neutral)
        self.assertEqual(self.launch("omp", "agent-session", "--profile=default", OMP_PROFILE="p").envoy_json, neutral)

    def test_omp_reads_the_session_a_resume_names(self) -> None:
        checkout = self.tmp / "resumed"
        sessions = self.home / ".omp" / "agent" / "sessions" / "-x"
        sessions.mkdir(parents=True)
        file = sessions / f"2026-11-21T00-00-00-000Z_{AResumedSession.ID}.jsonl"
        file.write_text(json.dumps({"type": "session", "cwd": str(checkout)}) + "\n")
        for args in (("--resume", str(file)), ("-r", AResumedSession.ID), (f"--session={AResumedSession.ID}",),
                     ("--restore", AResumedSession.ID[:13])):
            with self.subTest(args=args):
                self.assertIn(checkout, self.launch("omp", "agent-session", *args).checkouts)
        elsewhere = self.tmp / "session-dir"
        elsewhere.mkdir()
        file.rename(elsewhere / file.name)
        self.assertIn(checkout, self.launch("omp", "agent-session", "--session-dir", str(elsewhere), "-r",
                                            AResumedSession.ID).checkouts)
        self.assertEqual(self.launch("omp", "agent-session", "-r", AResumedSession.ID).checkouts, [self.tmp])

    RESUME_CASES = (("--resume", "a"), ("-r", "a"), ("--session=a",), ("--restore", "a"), ("--restore=a",),
                    ("--resume", "--resume", "a"), ("-p", "--", "--resume", "a"), ("--resume",),
                    ("x", "--restore=y", "-r", "z"), ("--session", "a", "--model", "m"))

    @staticmethod
    def bash_target(loop: str, args: tuple[str, ...]) -> str:
        """What LOOP, bash code that sets `target` from "$@", makes of ARGS."""
        done = subprocess.run(["bash", "-c", loop + '\nprintf "%s" "$target"', "_", *args], capture_output=True,
                              text=True, timeout=30, check=True)
        return done.stdout

    def test_omps_resume_target_is_agent_sessions(self) -> None:
        """scripts/agent-session parses the resume target for its moved-session and live-session checks;
        the check parses it for the resumed checkout. Both read it the same way."""
        text = (DOTFILES / "scripts" / "agent-session").read_text(encoding="utf-8")
        loop = re.search(r'^    prev="" target=""\n.*?^    done\n', text, re.M | re.S)
        self.assertIsNotNone(loop, "scripts/agent-session has no resume target loop")
        assert loop is not None
        for args in self.RESUME_CASES:
            with self.subTest(args=args):
                self.assertEqual(self.launch("omp", "agent-session", *args).resume or "", self.bash_target(loop.group(0), args))
        # Control: a loop that forgets --restore disagrees on a --restore case.
        forgets = loop.group(0).replace("|--restore)", ")").replace("|--restore=*)", ")")
        self.assertNotEqual(self.launch("omp", "agent-session", "--restore", "a").resume, self.bash_target(forgets, ("--restore", "a")))

    def test_cld_flags_are_skipped_only_on_agent_sessions_path(self) -> None:
        cld = ("--dangerously-skip-permissions", "--channels", "plugin:x@y", "--settings", "s.json", "--version")
        self.assertEqual(self.launch("claude", "agent-session", *cld).command, "--version")
        self.assertEqual(self.launch("claude", "claude", *cld).command, "--dangerously-skip-permissions")

    def test_claude_reads_each_settings_value_multi_line_inline_json_included(self) -> None:
        token_file = self.tmp / "named.json"
        token_file.write_text(json.dumps({"env": {"DISPATCH_TOKEN": "from-file"}}))
        inline = json.dumps({"model": "m", "env": {"DISPATCH_TOKEN": "from-inline"}}, indent=2)
        launch = self.launch("claude", "claude", "--settings", str(token_file), f"--settings={inline}", "-p", "ok")
        values = [values.get("DISPATCH_TOKEN") for _, values in launch.sources]
        self.assertIn("from-file", values)
        self.assertIn("from-inline", values)

    def test_claude_reads_project_settings_only_where_claude_applies_them(self) -> None:
        settings = self.tmp / ".claude" / "settings.json"
        settings.parent.mkdir()
        settings.write_text(json.dumps({"env": {"DISPATCH_TOKEN": "from-project"}}))

        def project_tokens(*args: str, **extra: str) -> list[str | None]:
            return [values["DISPATCH_TOKEN"] for where, values in self.launch("claude", "claude", *args, **extra).sources
                    if str(settings.parent) in where and "DISPATCH_TOKEN" in values]

        def refused(*args: str, **extra: str) -> None:
            # Claude Code applies the file once the folder's trust is accepted, which no check can see first.
            with self.assertRaisesRegex(self.check.Refused, "names DISPATCH_TOKEN for Dispatch.*trust is accepted"):
                self.launch("claude", "claude", *args, **extra)

        refused("ok")
        self.assertEqual(project_tokens("-p", "ok"), ["from-project"])
        self.assertEqual(project_tokens("--print", "ok"), ["from-project"])
        self.assertEqual(project_tokens("-cp", "ok"), ["from-project"])
        self.assertEqual(project_tokens("ok", CLAUDE_CODE_SANDBOXED="1"), ["from-project"])
        record = self.home / ".claude.json"
        record.write_text(json.dumps({"projects": {str(self.tmp): {"hasTrustDialogAccepted": True}}}))
        self.assertEqual(project_tokens("ok"), ["from-project"], "a folder whose trust is accepted")
        refused("ok", CLAUDE_CODE_IGNORE_PERSISTED_TRUST="1")
        # Claude Code keeps its record beside CLAUDE_CONFIG_DIR's settings when that names a directory.
        config = self.tmp / "claude-config"
        config.mkdir()
        refused("ok", CLAUDE_CONFIG_DIR=str(config))
        (config / ".claude.json").write_text(record.read_text())
        self.assertEqual(project_tokens("ok", CLAUDE_CONFIG_DIR=str(config)), ["from-project"])
        # A record that cannot be read for certain trusts nothing.
        record.write_text("{not json")
        refused("ok")
        # Control: in a folder not trusted yet, a project file that names no bearer is neither refused nor read.
        settings.write_text(json.dumps({"env": {"SOMETHING_ELSE": "x"}}))
        self.assertEqual([where for where, _ in self.launch("claude", "claude", "ok").sources if str(settings.parent) in where],
                         [])

    def test_codex_reads_its_cd_directory(self) -> None:
        project = self.tmp / "codex-project"
        for args in (("-C", str(project), "exec"), ("--cd", str(project)), (f"--cd={project}",), (f"-C{project}",),
                     ("-C", "x", "-C", str(project))):
            with self.subTest(args=args):
                self.assertEqual(self.launch("codex", "codex", *args).cwd, project)
        self.assertEqual(self.launch("codex", "codex", "exec", "--", "-C", str(project)).cwd, self.tmp)

    def test_an_unknown_harness_is_read_generically(self) -> None:
        # agent-session runs any agent it is given: one this file does not know keeps its launch
        # directory, applies no file, and reads the Dispatch client's own envoy.json.
        launch = self.launch("gemini", "agent-session", "--cwd", str(self.tmp / "elsewhere"), "-p", "ok")
        self.assertEqual((launch.command, launch.cwd, launch.sources, launch.checkouts),
                         ("--cwd", self.tmp, [], [self.tmp]))
        self.assertEqual(launch.envoy_json, self.home / ".config" / "legion" / "envoy.json")


class ClaudeCodesTrust(Unit):
    """claude_trusts against Claude Code 2.1.316's trust function: its environment switches, its
    record looked up from the folder up to the repository root and never past it, and a linked
    worktree's main repository as the repository's key."""

    def setUp(self) -> None:
        super().setUp()
        # The walk outside a repository goes to `/`: this case's folders must be in none.
        self.assertIsNone(self.launching.git_root(self.tmp), "the scratch folder is inside a repository")

    def trusts(self, cwd: Path, trusted: list[Path], **extra: str) -> bool:
        (self.home / ".claude.json").write_text(json.dumps(
            {"projects": {str(folder): {"hasTrustDialogAccepted": True} for folder in trusted}}))
        with self.environment(**extra):
            return self.launching.claude_trusts(cwd)

    def repository(self, name: str) -> Path:
        root = self.tmp / name
        (root / ".git").mkdir(parents=True)
        return root

    def test_a_folder_below_a_trusted_repository_root_is_trusted(self) -> None:
        repo = self.repository("repo")
        below = repo / "sub" / "deeper"
        below.mkdir(parents=True)
        self.assertTrue(self.trusts(below, [repo]))
        self.assertTrue(self.trusts(below, [repo / "sub"]))
        # Control: with no record in the repository, the same folder is not trusted.
        self.assertFalse(self.trusts(below, []))

    def test_the_walk_never_passes_the_repository_root(self) -> None:
        clone = self.repository("clone")
        (clone / "sub").mkdir()
        for folder in (clone, clone / "sub"):
            with self.subTest(folder=folder):
                self.assertFalse(self.trusts(folder, [self.tmp]), "a trusted folder above the clone's root")
        # Control: outside any repository the same record trusts the folder, since the walk goes to `/`.
        plain = self.tmp / "plain" / "sub"
        plain.mkdir(parents=True)
        self.assertTrue(self.trusts(plain, [self.tmp]))

    def test_a_linked_worktree_takes_its_main_repositorys_record(self) -> None:
        main = self.repository("main")
        tree = self.tmp / "tree"
        tree.mkdir()
        gitdir = main / ".git" / "worktrees" / "tree"
        gitdir.mkdir(parents=True)
        (gitdir / "commondir").write_text("../..\n")
        (gitdir / "gitdir").write_text(f"{tree / '.git'}\n")
        (tree / ".git").write_text(f"gitdir: {gitdir}\n")
        self.assertTrue(self.trusts(tree, [main]))
        # Control: a worktree whose gitdir file points at another folder is its own key.
        (gitdir / "gitdir").write_text(f"{self.tmp / 'other' / '.git'}\n")
        self.assertFalse(self.trusts(tree, [main]))

    def test_the_environment_switches(self) -> None:
        folder = self.tmp / "folder"
        folder.mkdir()
        self.assertTrue(self.trusts(folder, [folder]))
        self.assertFalse(self.trusts(folder, [folder], CLAUDE_CODE_IGNORE_PERSISTED_TRUST="1"))
        self.assertTrue(self.trusts(folder, [], CLAUDE_CODE_SANDBOXED="1"))
        self.assertTrue(self.trusts(folder, [], CLAUDE_CODE_SANDBOXED="1", CLAUDE_CODE_IGNORE_PERSISTED_TRUST="1"))
        self.assertTrue(self.trusts(folder, [], CLAUDE_BG_WORKSPACE_TRUSTED="1"))
        # Control: no record and no switch.
        self.assertFalse(self.trusts(folder, []))

    def test_print_mode_is_read_as_claude_codes_parser_reads_it(self) -> None:
        cld = ["--dangerously-skip-permissions", "--channels", "plugin:x@y", "--settings", "s.json"]
        for args in (["-p"], ["-cp", "hi"], ["-pc", "hi"], ["--print", "hi"], ["--model", "x", "-p", "hi"],
                     ["-r", "-p"], ["-pn", "x"], [*cld, "-p", "hi"], ["hi", "-p"]):
            with self.subTest(args=args):
                self.assertTrue(self.launching.prints(args))
        # A short option that takes a value takes the rest of its cluster, or the next argument:
        # `-nprod` names the session "prod"; `-n -prod` and `--append-system-prompt -plain` too.
        for args in (["-nprod"], ["-dapi"], ["-wpatch"], ["-rprep"], ["-n", "-prod"], ["-cn", "-p"], ["-c", "-np"],
                     ["--append-system-prompt", "-plain"], ["--model", "-p"]):
            with self.subTest(args=args):
                self.assertFalse(self.launching.prints(args))
        # Controls: no p; a prompt after `--`; help and version, which start no session; an option
        # Claude Code does not know, which starts none either.
        for args in (["-c", "hi"], ["hi"], ["--permission-mode", "plan"], ["-c", "--", "-p"], ["-hp"], ["-p", "-v"],
                     ["--help", "-p"], ["-xp"]):
            with self.subTest(args=args):
                self.assertFalse(self.launching.prints(args))


class TheCacheAndFaults(Unit):
    def test_a_failed_cache_write_and_cleanup_are_both_optional(self) -> None:
        with mock.patch.object(Path, "write_bytes", side_effect=PermissionError), \
                mock.patch.object(Path, "unlink", side_effect=PermissionError):
            self.check.keep(self.tmp / "copy", b'{"enabled": false}')

    def test_an_unexpected_exception_does_not_print_credentials(self) -> None:
        output = io.StringIO()
        with mock.patch.object(self.check, "check", side_effect=ValueError(PRIV_TOKEN)), contextlib.redirect_stdout(output):
            self.assertEqual(self.check.check_main(["omp", "agent-session"]), 0)
        self.assertTrue(output.getvalue().startswith("refuse "))
        self.assertNotIn(PRIV_TOKEN, output.getvalue())


class TokenCommands(Unit):
    def config(self, directory: Path, token: str, server: str) -> Path:
        path = directory / "envoy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"dispatch": {"enabled": True, "serverUrl": server, "token": token}}))
        return path

    def test_a_token_command_is_not_put_on_a_process_argument(self) -> None:
        dispatch = self.dispatch()
        config = self.config(self.home / ".config" / "legion", f"!printf '%s' '{PRIV_TOKEN}'", dispatch.url)
        real_popen = subprocess.Popen
        arguments = []

        def popen(argv, *args, **kwargs):
            arguments.append(argv)
            return real_popen(argv, *args, **kwargs)

        with self.environment(ENVOY_CONFIG_FILE=str(config)), \
                mock.patch.object(Path, "cwd", return_value=self.tmp), \
                mock.patch.object(self.check.subprocess, "Popen", side_effect=popen):
            self.assertEqual(self.check.bearers(self.check.launch_for("codex", "codex", [])),
                             [(dispatch.url, PRIV_TOKEN, f"dispatch.token in {config}")])
        self.assertTrue(arguments)
        self.assertNotIn(PRIV_TOKEN, repr(arguments))

    def test_a_token_commands_background_child_cannot_hold_the_launch_open(self) -> None:
        check = self.check
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
                config = self.config(directory, "!./token-child & wait", "http://127.0.0.1:9")
                started = directory / "child-started"
                executable(directory / "token-child", f"#!{sys.executable}\n"
                           "import os, time\nfrom pathlib import Path\n"
                           "marker = Path('child-started.tmp')\n"
                           "marker.write_text(str(os.getpid()))\n"
                           "marker.replace('child-started')\ntime.sleep(60)\n")
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
                    with self.environment(ENVOY_CONFIG_FILE=str(config)), \
                            mock.patch.object(Path, "cwd", return_value=self.tmp), \
                            mock.patch.object(check.subprocess, "Popen", side_effect=spawn), \
                            mock.patch.object(check.os, "killpg", side_effect=kill):
                        with self.assertRaises(check.Refused):
                            check.bearers(check.launch_for("codex", "codex", []))
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


class NoSessionWords(unittest.TestCase):
    """Each word NO_SESSION lets start with no check is a command the installed harness lists in its
    own `--help`, and each flag is one it answers and exits 0 on (omp's help does not list its own
    --help and --version): Claude Code and Codex update themselves, and a command they drop becomes a
    prompt that would start a session unchecked. Skipped where the harness is not installed."""

    @staticmethod
    def binary(name: str) -> str | None:
        """The real NAME, past any shims/ directory on PATH; for omp, mise's install as well."""
        path = os.pathsep.join(entry for entry in os.environ.get("PATH", "").split(os.pathsep)
                               if entry and not os.path.realpath(entry).endswith("/shims"))
        found = shutil.which(name, path=path)
        if found is None and name == "omp" and shutil.which("mise"):
            run = subprocess.run(["mise", "which", "omp"], capture_output=True, text=True, timeout=60)
            found = run.stdout.strip() if run.returncode == 0 and run.stdout.strip() else None
        return found

    @staticmethod
    def commands(help_text: str) -> set[str]:
        """The command words of a `--help`: each line indented two spaces under its Commands heading,
        its first word split on `|` (claude prints aliases as `update|upgrade`)."""
        words: set[str] = set()
        inside = False
        for line in help_text.splitlines():
            if line.strip() in ("COMMANDS", "Commands:"):
                inside = True
            elif inside and line and not line.startswith(" "):
                inside = False
            elif inside and line.startswith("  ") and not line.startswith("   ") and line.strip():
                words.update(line.split()[0].split("|"))
        return words

    @staticmethod
    def help_text(binary: str) -> str:
        """BINARY's `--help`, through a file: Claude Code exits before a pipe takes all of its help
        (8190 of 8 KiB+ bytes seen)."""
        with tempfile.TemporaryFile() as out:
            subprocess.run([binary, "--help"], stdout=out, stderr=subprocess.STDOUT, timeout=60,
                           stdin=subprocess.DEVNULL, check=False)
            out.seek(0)
            return out.read().decode(errors="replace")

    def test_each_no_session_word_is_a_command_of_the_installed_harness(self) -> None:
        check = load_check(Path(self.enterContext(tempfile.TemporaryDirectory())))
        ran = 0
        for harness, words in check.NO_SESSION.items():
            binary = self.binary(harness)
            if binary is None:
                continue
            ran += 1
            text = self.help_text(binary)
            listed = self.commands(text)
            with self.subTest(harness=harness, binary=binary):
                self.assertTrue(listed, f"{binary} --help listed no commands to compare with")
                self.assertEqual(sorted(w for w in words if not w.startswith("-") and w not in listed), [])
                for flag in sorted(w for w in words if w.startswith("-")):
                    answered = subprocess.run([binary, flag], capture_output=True, text=True, timeout=60,
                                              stdin=subprocess.DEVNULL)
                    self.assertEqual((answered.returncode, bool(answered.stdout.strip())), (0, True), f"{binary} {flag}")
        if not ran:
            self.skipTest("no omp, claude or codex is installed here")

    def test_control_a_word_no_harness_lists_is_caught(self) -> None:
        listed = self.commands("Commands:\n  mcp          Configure MCP\n  update|upgrade  Update\n\nArguments:\n  prompt\n")
        self.assertEqual(listed, {"mcp", "update", "upgrade"})
        self.assertNotIn("config", listed)


# An option line of Claude Code's `--help`: its names (`-x, --long`, or two long ones), then its
# value, `<v>` (required, `<v...>` too) or `[v]` (optional), or none (a flag).
CLAUDE_OPTION = re.compile(r"^((?:-{1,2}[\w-]+)(?:, -{1,2}[\w-]+)*)(?: ([<\[]).*[>\]])?$")


def claude_option_tables(help_text: str) -> tuple[set[str], set[str], dict[str, str | None]]:
    """The no-value long options, the optional-value long options and the short options (letter:
    None, "required" or "optional") of a Claude Code `--help`, --help and --version set aside."""
    section = help_text.split("\nOptions:\n", 1)[1].split("\nCommands:\n", 1)[0]
    no_value: set[str] = set()
    optional: set[str] = set()
    short: dict[str, str | None] = {}
    for line in section.splitlines():
        if not line.startswith("  -"):
            continue
        head = re.split(r"\s{2,}", line.strip())[0]
        match = CLAUDE_OPTION.match(head)
        assert match, f"an option line the parse does not know: {head!r}"
        names = match[1].split(", ")
        if {"--help", "--version"} & set(names):
            continue
        kind = {"<": "required", "[": "optional", None: None}[match[2]]
        for name in names:
            if name.startswith("--"):
                if kind is None:
                    no_value.add(name)
                elif kind == "optional":
                    optional.add(name)
            else:
                short[name[1:]] = kind
    return no_value, optional, short


class ClaudeOptionTables(unittest.TestCase):
    """agent_private_launch's tables of Claude Code's options, which prints() reads print mode with,
    are the installed claude's own `--help`: Claude Code updates itself, and an option that moves
    between tables would let a value read as `-p`, or `-p` read as a value. Skipped where claude is
    not installed."""

    def tables(self) -> tuple[Any, tuple[set[str], set[str], dict[str, str | None]]]:
        binary = NoSessionWords.binary("claude")
        if binary is None:
            self.skipTest("claude is not installed here")
        launching_module = launching(load_check(Path(self.enterContext(tempfile.TemporaryDirectory()))))
        return launching_module, claude_option_tables(NoSessionWords.help_text(binary))

    def test_the_option_tables_are_the_installed_claudes(self) -> None:
        module, (no_value, optional, short) = self.tables()
        self.assertEqual(set(module.CLAUDE_NO_VALUE), no_value)
        self.assertEqual(set(module.CLAUDE_OPTIONAL_VALUE), optional)
        self.assertEqual(module.CLAUDE_SHORT, short)

    def test_control_a_flag_swapped_for_an_option_with_a_value_is_caught(self) -> None:
        module, (no_value, _, _) = self.tables()
        swapped = (set(module.CLAUDE_NO_VALUE) - {"--verbose"}) | {"--effort"}
        self.assertNotEqual(swapped, no_value)
        # What the swap would do: a value read as print mode, and print mode read as a value.
        self.assertFalse(module.prints(["--effort", "-p", "hi"]))
        self.assertTrue(module.prints(["--verbose", "-p", "hi"]))


if __name__ == "__main__":
    unittest.main()
