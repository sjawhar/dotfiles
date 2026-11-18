#!/usr/bin/env python3
"""scripts/secret-run-reason: the reason a broker credential request carries.

Every reason must pass the broker's own check, so each case runs it through broker_accepts, which
is validateReason (sjawhar/legion packages/envoy/internal/broker/record/record.go): at most 400
runes, none of category Cc, Cf, Cs, Co, Zl or Zp. The cases cover the session sources (omp's
variables and transcript, Envoy, Claude Code, none), what of a command may appear (its first words
and the ids it targets, never a flag's value or anything that looks like a credential), and the
length rule: the session, the command and the host are never cut, only the why shortens, and the
builder refuses (exit 3) when the first three alone leave no room for it.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
import unicodedata
import unittest
from pathlib import Path

# sami-devbox's own name, 54 runes: until 2026-11-17 the host's 32-rune cap cut it on every card.
LONG_HOST = "sami-devbox.us-west4-a.c.theorem-agent-infra.internal"

SCRIPT = Path(__file__).resolve().parents[1] / "secret-run-reason"
LOADER = importlib.machinery.SourceFileLoader("secret_run_reason", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
REASON = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(REASON)

SESSION = "01a16a9e-a90d-7148-95ed-0e9e7839dbd2"
OTHER_SESSION = "01a17b06-9430-7245-8284-bad29b104512"
STAMP = "2026-10-23T21-15-45-293Z"
DOC = "1PTCUrEOLmYbHUx9Zf-ruQ5P5GhDAnpsEptQWOvRcKZk"
GWS = ["/home/u/.dotfiles/omp/plugins/node_modules/gws-accounts/bin/gws", "docs", "documents", "batchUpdate"]
REFUSED = {"Cc", "Cf", "Cs", "Co", "Zl", "Zp"}


def broker_accepts(text: str) -> bool:
    return len(text) <= 400 and not any(unicodedata.category(c) in REFUSED for c in text)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name)
        self.sessions = self.home / ".omp" / "agent" / "sessions" / "-src-platform"
        self.sessions.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def transcript(self, session_id: str = SESSION, title: str = "TASKCOACH", slot: bool = True) -> Path:
        path = self.sessions / f"{STAMP}_{session_id}.jsonl"
        lines = [json.dumps({"type": "session", "id": session_id, "title": title})]
        if slot:
            lines.insert(0, json.dumps({"type": "title", "v": 1, "title": title, "pad": " " * 40}))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def subagent(self, name: str = "CarryCensus", session_id: str = SESSION) -> Path:
        path = self.sessions / f"{STAMP}_{session_id}" / f"{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"type": "title", "v": 1, "title": ""}) + "\n", encoding="utf-8")
        return path

    def build(self, caller: str = "gws theorem modify", command: list[str] | None = None, **environment: str):
        export, text = REASON.build(
            caller, command, {"HOME": str(self.home), **environment}, 4242, host="devbox", cwd="~/src/platform"
        )
        self.assertTrue(broker_accepts(text), text)
        self.assertNotIn("\n", text)
        return export, text


class Session(Base):
    def test_omp_variables_name_the_session_its_title_and_subagent(self) -> None:
        self.transcript()
        export, text = self.build(OMP_SESSION_ID=SESSION, PI_SESSION_FILE=str(self.subagent()))
        self.assertEqual(export, "")
        self.assertEqual(
            text,
            f'gws theorem modify | session: omp {SESSION} "TASKCOACH" subagent CarryCensus '
            "| runs: request only, no command | host: devbox pid 4242",
        )

    def test_the_eval_kernel_session_file_alone_is_exported(self) -> None:
        """omp's eval kernel keeps PI_SESSION_FILE and drops OMP_SESSION_ID."""
        self.transcript()
        export, text = self.build(PI_SESSION_FILE=str(self.subagent()))
        self.assertEqual(export, SESSION)
        self.assertIn(f'session: omp {SESSION} "TASKCOACH" subagent CarryCensus |', text)

    def test_a_top_level_session_file_has_no_subagent(self) -> None:
        export, text = self.build(PI_SESSION_FILE=str(self.transcript()))
        self.assertEqual(export, SESSION)
        self.assertIn(f'session: omp {SESSION} "TASKCOACH" |', text)

    def test_a_nested_subagent_is_named_by_its_path(self) -> None:
        self.transcript()
        nested = self.sessions / f"{STAMP}_{SESSION}" / "Lead" / "Worker.jsonl"
        nested.parent.mkdir(parents=True)
        nested.write_text("{}\n", encoding="utf-8")
        _, text = self.build(PI_SESSION_FILE=str(nested))
        self.assertIn("subagent Lead.Worker |", text)

    def test_an_omp_session_id_finds_its_transcript_when_the_file_names_another(self) -> None:
        self.transcript(OTHER_SESSION, title="ctx")
        self.transcript()
        export, text = self.build(OMP_SESSION_ID=OTHER_SESSION, PI_SESSION_FILE=str(self.subagent()))
        self.assertEqual(export, "")
        self.assertIn(f'session: omp {OTHER_SESSION} "ctx" |', text)
        self.assertNotIn("CarryCensus", text)

    def test_an_omp_session_with_no_transcript_still_names_its_id(self) -> None:
        _, text = self.build(OMP_SESSION_ID=SESSION)
        self.assertIn(f"session: omp {SESSION} |", text)

    def test_an_older_transcript_keeps_its_title_in_the_session_header(self) -> None:
        _, text = self.build(PI_SESSION_FILE=str(self.transcript(title="old title", slot=False)))
        self.assertIn('"old title"', text)

    def test_a_damaged_transcript_costs_only_the_title(self) -> None:
        path = self.sessions / f"{STAMP}_{SESSION}.jsonl"
        path.write_bytes(b"\xff not json\n")
        export, text = self.build(PI_SESSION_FILE=str(path))
        self.assertEqual(export, SESSION)
        self.assertIn(f"session: omp {SESSION} |", text)

    def test_envoy_and_claude_sessions_are_named_and_never_replaced(self) -> None:
        subagent = self.subagent()
        for variable, label in (("ENVOY_SESSION_ID", "envoy"), ("CLAUDE_CODE_SESSION_ID", "claude")):
            with self.subTest(variable=variable):
                export, text = self.build(PI_SESSION_FILE=str(subagent), **{variable: "ses-1"})
                self.assertEqual(export, "")
                self.assertIn(f"session: {label} ses-1 |", text)

    def test_no_session_says_so_with_pid_and_cwd(self) -> None:
        export, text = self.build()
        self.assertEqual(export, "")
        self.assertEqual(
            text,
            "gws theorem modify | session: no omp session; pid 4242, cwd ~/src/platform "
            "| runs: request only, no command | host: devbox",
        )

    def test_a_session_file_outside_omp_layout_names_nothing(self) -> None:
        stray = self.home / "elsewhere.jsonl"
        stray.write_text("{}\n", encoding="utf-8")
        export, text = self.build(PI_SESSION_FILE=str(stray))
        self.assertEqual(export, "")
        self.assertIn("session: no omp session;", text)

    def test_cwd_under_home_is_written_from_tilde(self) -> None:
        inside = self.home / "src" / "x"
        inside.mkdir(parents=True)
        before = os.getcwd()
        os.chdir(inside)
        try:
            self.assertEqual(REASON.working_directory(self.home), "~/src/x")
        finally:
            os.chdir(before)


class Command(Base):
    def runs(self, command: list[str]) -> str:
        _, text = self.build(command=command)
        return text.split(" | runs: ", 1)[1].split(" | host: ", 1)[0]

    def test_gws_names_its_method_and_document_never_its_body(self) -> None:
        body = '{"requests":[{"insertText":{"text":"private text ya29.abcdef","objectId":"x"}}]}'
        runs = self.runs([*GWS, "--params", json.dumps({"documentId": DOC}), "--json", body])
        self.assertEqual(runs, f"gws docs documents batchUpdate documentId={DOC} +4 args")

    def test_json_ids_are_the_top_level_id_fields_only(self) -> None:
        params = {"userId": "me", "id": "18c2f", "pageToken": "abc", "apiKeyId": "k1", "q": "from:x", "nested": {"fileId": "f"}}
        runs = self.runs(["gws", "gmail", "users", "messages", "--params", json.dumps(params)])
        self.assertEqual(runs, "gws gmail users messages id=18c2f +2 args")

    def test_an_equals_form_flag_is_read_too(self) -> None:
        runs = self.runs(["gws", "drive", "files", "get", '--params={"fileId":"abc123","supportsAllDrives":true}'])
        self.assertEqual(runs, "gws drive files get fileId=abc123 +1 args")

    def test_github_repositories_and_google_urls_are_targets(self) -> None:
        self.assertEqual(self.runs(["gh", "pr", "view", "12", "--repo", "sjawhar/legion"]), "gh pr view 12 repo=sjawhar/legion +2 args")
        self.assertEqual(self.runs(["gh", "-R", "o/r", "issue"]), "gh repo=o/r +3 args")
        self.assertEqual(
            self.runs(["open", "https://github.com/o/r/pull/7?token=ghp_abcdefghijklmnopqrstuvwxyz12"]),
            "open repo=o/r#7 +1 args",
        )
        self.assertEqual(
            self.runs(["open", f"https://docs.google.com/document/d/{DOC}/edit?tab=t.0"]), f"open fileId={DOC} +1 args"
        )

    def test_words_stop_at_the_first_flag_so_no_flag_value_shows(self) -> None:
        self.assertEqual(self.runs(["tool", "--password", "hunter2", "deploy"]), "tool +3 args")
        self.assertEqual(self.runs(["curl", "-H", "Authorization: Bearer abc", "https://x.example/"]), "curl +3 args")
        self.assertEqual(self.runs(["sh", "-c", "TOKEN=abc run"]), "sh +2 args")

    def test_a_word_that_looks_like_a_credential_is_left_out(self) -> None:
        for word in ("ghp_abcdefghijklmnopqrstuvwxyz0123", "ya29.a0AfB_byC", "Zx9QpL2mN8vR4tY6wK1jH3gF5dS7aB0c"):
            with self.subTest(word=word):
                self.assertEqual(self.runs(["echo", word, "after"]), "echo +2 args")

    def test_words_are_kept_whole(self) -> None:
        self.assertEqual(self.runs(["printenv", "GITHUB_APP_SJAWHAR"]), "printenv GITHUB_APP_SJAWHAR")
        self.assertEqual(self.runs(["mailbox", "__mint", "--env", "GWS_THEOREM_READ_OAUTH"]), "mailbox __mint +2 args")
        long_path = "/home/u/" + "deep/" * 10 + "script.py"
        self.assertEqual(self.runs(["python3", long_path]), f"python3 {long_path}")
        self.assertEqual(self.runs(["a", "b", "c", "d", "e"]), "a b c d +1 args")

    def test_a_long_command_is_never_cut(self) -> None:
        words = [f"w{n:038d}" for n in range(3)]
        ids = [f"{n:050d}" for n in range(3)]
        runs = self.runs(["x", *words, *(f'--params={{"id{n}Id":"{ids[n]}"}}' for n in range(3))])
        self.assertEqual(runs, " ".join(["x", *words, *(f"id{n}Id={ids[n]}" for n in range(3)), "+3 args"]))


class Text(Base):
    def test_refused_characters_never_reach_the_broker(self) -> None:
        nasty = "a\tb\nc\x00d\u202ee\u200bf\u200dg\u2028h\u2029i\ue000j\U000e0001k\ufeffl\x85m"
        # A dropped format character joins its neighbours; a control or separator becomes a space.
        self.assertEqual(REASON.clean(nasty), "a b c defg h ijkl m")
        self.assertEqual(REASON.clean("🏋️ COACH\u00a0x"), "🏋️ COACH x")

    def test_credentials_in_a_caller_reason_or_a_title_are_redacted(self) -> None:
        self.transcript(title="token ya29.secretvalue in a title")
        _, text = self.build(
            caller="use ghp_abcdefghijklmnopqrstuvwxyz0123", PI_SESSION_FILE=str(self.subagent()),
            SECRET_RUN_REASON="AKIAABCDEFGHIJKLMNOP",
        )
        for secret in ("ya29", "ghp_", "AKIA"):
            self.assertNotIn(secret, text)
        self.assertEqual(text.count("[redacted]"), 3, text)

    def test_the_caller_and_the_environment_reason_join(self) -> None:
        self.assertTrue(self.build(SECRET_RUN_REASON="  retitle tabs ")[1].startswith("gws theorem modify; retitle tabs |"))
        self.assertTrue(self.build(caller="", SECRET_RUN_REASON="retitle tabs")[1].startswith("retitle tabs |"))
        self.assertTrue(self.build(caller="")[1].startswith("no reason given |"))

    def test_the_session_command_and_host_are_never_cut_and_only_the_why_shortens(self) -> None:
        # Each of these was cut until 2026-11-17: the command past 100 runes and the host past 32.
        title = "T" * 40
        self.transcript(title=title)
        subagent = "S" * 20
        words = ["w" * 30 for _ in range(3)]
        command = ["cmd", *words, "--params", json.dumps({"fileId": "v" * 40})]
        export, text = REASON.build(
            "why " * 200,
            command,
            {"HOME": str(self.home), "PI_SESSION_FILE": str(self.subagent(subagent)), "SECRET_RUN_REASON": "more " * 200},
            4194304,
            host=LONG_HOST,
            cwd="/x",
        )
        self.assertEqual(export, SESSION)
        self.assertTrue(broker_accepts(text), len(text))
        self.assertEqual(len(text), REASON.MAX_REASON)
        why, session, runs, host = text.split(" | ")
        self.assertTrue(why.startswith("why why"), why)
        self.assertTrue(why.endswith("…"), why)
        self.assertEqual(session, f'session: omp {SESSION} "{title}" subagent {subagent}')
        self.assertEqual(runs, f"runs: cmd {' '.join(words)} fileId={'v' * 40} +2 args")
        self.assertEqual(host, f"host: {LONG_HOST} pid 4194304")

    def test_a_long_host_shows_whole(self) -> None:
        """The broker request that named this host on 2026-11-17 cut it to 32 runes."""
        _, text = REASON.build("gws theorem modify", None, {"HOME": str(self.home), "OMP_SESSION_ID": SESSION}, 4242,
                               host=LONG_HOST, cwd="/x")
        self.assertTrue(broker_accepts(text), text)
        self.assertTrue(text.endswith(f"| host: {LONG_HOST} pid 4242"), text)

    def test_identifying_parts_that_alone_do_not_fit_are_refused_never_cut(self) -> None:
        self.transcript(title="T" * 400)
        with self.assertRaises(REASON.TooLong) as refused:
            REASON.build("why", None, {"HOME": str(self.home), "OMP_SESSION_ID": SESSION}, 4242, host="devbox", cwd="/x")
        self.assertIn("none of them is cut", str(refused.exception))
        self.assertIn("T" * 400, str(refused.exception))

    def test_the_why_shrinks_to_one_rune_before_anything_else_gives_way(self) -> None:
        # The session part takes exactly the room that leaves one rune for the why.
        fixed = f" | session: omp {SESSION} \"\" | runs: request only, no command | host: devbox pid 4242"
        self.transcript(title="T" * (REASON.MAX_REASON - 1 - len(fixed)))
        _, text = REASON.build("why", None, {"HOME": str(self.home), "OMP_SESSION_ID": SESSION}, 4242, host="devbox", cwd="/x")
        self.assertEqual(len(text), REASON.MAX_REASON)
        self.assertTrue(text.startswith("… | session: "), text[:20])

    def test_the_caller_reason_gives_way_so_session_command_and_pid_show(self) -> None:
        command = ["cmd", *(f"--params={{\"k{n}Id\":\"{'v' * 60}\"}}" for n in range(3))]
        _, text = self.build(caller="why " * 200, command=command, OMP_SESSION_ID=SESSION)
        why, _, runs, host = text.split(" | ")
        self.assertEqual(len(text), REASON.MAX_REASON)
        self.assertTrue(why.endswith("…"), why)
        self.assertIn(f"k2Id={'v' * 60}", runs)
        self.assertEqual(host, "host: devbox pid 4242")

    def test_clip_ends_in_an_ellipsis(self) -> None:
        self.assertEqual(REASON.clip("x" * 500, 400), "x" * 399 + "…")
        self.assertEqual(REASON.clip("short", 400), "short")


class Cli(unittest.TestCase):
    def run_script(self, *args: str, **environment: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as home:
            return subprocess.run(
                [str(SCRIPT), *args],
                capture_output=True,
                text=True,
                check=False,
                env={"PATH": "/usr/bin:/bin", "HOME": home, **environment},
                timeout=30,
            )

    def test_prints_the_session_to_export_then_the_reason(self) -> None:
        result = self.run_script("--pid", "77", "--reason", "x", "--", "printenv", "KEY", PI_SESSION_FILE=f"/s/{STAMP}_{SESSION}.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        export, reason, rest = result.stdout.split("\n")
        self.assertEqual((export, rest), (SESSION, ""))
        self.assertRegex(reason, rf"^x \| session: omp {SESSION} \| runs: printenv KEY \| host: \S+ pid 77$")

    def test_identifying_parts_that_do_not_fit_exit_3_and_print_no_reason(self) -> None:
        session = "s" * 400
        result = self.run_script("--pid", "77", "--reason", "x", "--", "printenv", "KEY", OMP_SESSION_ID=session)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("secret-run-reason: the session, the command and the host are", result.stderr)
        self.assertIn(f"session: omp {session} |", result.stderr)

    def test_usage_errors_exit_2(self) -> None:
        for args in (("--pid",), ("--pid", "x"), ("--bogus",), ("--reason",)):
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage: secret-run-reason", result.stderr)


if __name__ == "__main__":
    unittest.main()
