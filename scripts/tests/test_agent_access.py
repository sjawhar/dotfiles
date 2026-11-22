#!/usr/bin/env python3
"""scripts/agent-access: reports what logins/credentials THIS session holds.

Stubs every external CLI on PATH. Successful `ant auth status` output contains
an access token and a run-time JWT; failing `fleetctl` output starts with a
GitHub token. The script must filter all of them, while it reports status,
valid `--json`, required fields, and finishes within its budget.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

DOTFILES = Path(__file__).resolve().parents[2]
AGENT_ACCESS = DOTFILES / "scripts" / "agent-access"


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


# The core utils the script and its private checks rely on, on the PATH run_access gives it.
CORE_TOOLS = (
    "awk", "basename", "bash", "cat", "cut", "date", "dirname", "find", "grep", "head", "id", "jq", "ls",
    "mktemp", "paste", "python3", "readlink", "rm", "sort", "tail", "timeout", "tr",
)


def real_binary(tool: str) -> str | None:
    """Resolve a tool before HOME changes, or return None if no binary is usable.

    Ask a mise python3 shim for its interpreter, and other mise shims for
    `mise which`. If resolution fails or names an unusable target, continue
    along PATH without putting the shim into the isolated environment.
    """
    for directory in os.get_exec_path():
        found = shutil.which(tool, path=directory or os.curdir)
        if found is None:
            continue
        found = os.path.abspath(found)
        resolved = Path(found).resolve()
        if resolved.name != "mise":
            return found
        if tool == "python3":
            command = [found, "-c", "import sys; print(sys.executable)"]
        else:
            command = [str(resolved), "which", tool]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            target = Path(result.stdout.strip())
            if (target.is_absolute() and target.is_file() and os.access(target, os.X_OK)
                    and target.resolve().name != "mise"):
                return str(target)
    return None


# Secret-shaped substrings this script must never print, however a stub or a
# real CLI answers: token-prefixed credentials.
SECRET_MARKERS = [
    "ghs_should_never_print",
    "sk-ant-should-never-print",
]

# `secrets grants` as the secrets v3.8.4 pin answers it: one table, headed KEY SCOPE AGE.
SECRETS_GRANTS_V384 = "KEY\tSCOPE\tAGE\nTEST_KEY\tsession\t10s\n"
# The notice `shims/secrets` writes to stderr before it runs the real client for an agent's call.
SECRETS_SHIM_NOTICE = (
    "secrets shim: non-interactive SSH, so an agent's call: agent-tier only, through the broker"
    " (set AGENT_SHIM_PERSON=1 for your own)\n"
)
# Each check that runs its command under `timeout`: its row name, and the command its stub replaces. gws runs once per
# account in its registry; the timeout tests give it one account, `work`.
TIMED_CHECKS = {
    "aws": "aws",
    "anthropic_ant": "ant",
    "vercel": "vercel",
    "datadog_pup": "pup",
    "gcloud": "gcloud",
    "google_workspace_work": "gws",
    "fleetctl": "fleetctl",
    "sentry": "sentry-cli",
    "kubectl": "kubectl",
    "secretsd_grants": "secrets",
    "agent_secrets_broker": "agent-secrets",
    "dispatch": "dispatch",
}
# What each command has printed when `timeout` stops it, on a stream its check reads (some checks read stdout and
# stderr merged, others apart). kubectl's current context answers, and `kubectl auth whoami` is the call that times out.
PARTIAL_OUTPUT = {
    "aws": """echo '{"UserId": "AROA123:i-abc", "Arn": "arn:aws:sts::123:'""",
    "ant": 'echo "  Logged in to Example Org as"',
    "vercel": 'echo "Vercel CLI 39.1.0" >&2',
    "pup": """echo '{"authenticated": true, "site": "us5.'""",
    "gcloud": """echo '[{"account": "sami@'""",
    "gws": """echo "Using keyring backend: keyring" >&2\necho '{"emailAddress": '""",
    "fleetctl": 'echo "Fetching config from https://fleet.example.com"',
    "sentry-cli": 'echo "Sentry Server: https://sentry.io"',
    "kubectl": '[[ "$1 $2" == "config current-context" ]] && { echo production; exit 0; }\necho "Username   arn:aws:sts::123:"',
    "secrets": f"printf %s {shlex.quote(SECRETS_SHIM_NOTICE)} >&2\nprintf 'SECRET GRANT\\tSCOPE\\n'",
    "agent-secrets": 'echo "agent-secrets: dialing the broker"',
    "dispatch": 'echo "dispatch: contacting the server"',
}


class RealBinary(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.shims = self.root / "shims"
        self.shims.mkdir()
        self.binaries = self.root / "bin"
        self.binaries.mkdir()
        write_stub(self.root, "mise", "exit 1")
        for tool in ("jq", "python3"):
            (self.shims / tool).symlink_to(self.root / "mise")
        environment = patch.dict(os.environ, PATH=os.pathsep.join(map(str, (self.shims, self.binaries))))
        environment.start()
        self.addCleanup(environment.stop)

    def test_plain_binary_needs_no_mise_installation(self) -> None:
        write_stub(self.binaries, "jq", "exit 0")
        with patch.dict(os.environ, PATH=str(self.binaries)):
            self.assertEqual(real_binary("jq"), str(self.binaries / "jq"))
            self.assertIsNone(real_binary("missing-tool"))

    def test_failed_shim_resolution_uses_the_next_binary(self) -> None:
        for tool in ("jq", "python3"):
            with self.subTest(tool=tool):
                write_stub(self.binaries, tool, "exit 0")
                self.assertEqual(real_binary(tool), str(self.binaries / tool))

    def test_shim_without_an_installed_target_is_missing(self) -> None:
        for tool in ("jq", "python3"):
            with self.subTest(tool=tool):
                self.assertIsNone(real_binary(tool))

    def test_resolution_errors_use_the_next_binary(self) -> None:
        for tool in ("jq", "python3"):
            write_stub(self.binaries, tool, "exit 0")
            for error in (OSError("cannot start mise"), subprocess.TimeoutExpired("mise", 10)):
                with self.subTest(tool=tool, error=type(error).__name__), patch(
                    "subprocess.run", side_effect=error
                ):
                    self.assertEqual(real_binary(tool), str(self.binaries / tool))

    def test_mise_which_selects_an_executable_target(self) -> None:
        write_stub(self.root, "managed-jq", "exit 0")
        write_stub(self.root, "mise", '[ "$1 $2" = "which jq" ] || exit 2\nprintf "%s\\n" "$REAL_BINARY_TARGET"')
        with patch.dict(os.environ, REAL_BINARY_TARGET=str(self.root / "managed-jq")):
            self.assertEqual(real_binary("jq"), str(self.root / "managed-jq"))

    def test_shim_output_must_name_a_real_executable_not_another_shim(self) -> None:
        write_stub(self.root, "mise", 'printf "%s\\n" "$REAL_BINARY_TARGET"')
        nonexecutable = self.root / "not-executable"
        nonexecutable.write_text("not executable", encoding="utf-8")
        for target in ("", "relative-target", str(self.root / "missing"), str(self.root),
                       str(nonexecutable), str(self.shims / "jq")):
            for tool in ("jq", "python3"):
                with self.subTest(target=target, tool=tool), patch.dict(os.environ, REAL_BINARY_TARGET=target):
                    self.assertIsNone(real_binary(tool))

    def test_python_shim_reports_its_interpreter_not_mise_which(self) -> None:
        write_stub(self.binaries, "python3.99", "exit 0")
        write_stub(self.root, "mise", '[ "$1" = "-c" ] || exit 2\nprintf "%s\\n" "$REAL_BINARY_TARGET"')
        with patch.dict(os.environ, REAL_BINARY_TARGET=str(self.binaries / "python3.99")):
            self.assertEqual(real_binary("python3"), str(self.binaries / "python3.99"))


class AgentAccess(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.stub_dir = self.root / "bin"
        self.stub_dir.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.addCleanup(self.temp_dir.cleanup)

    def run_access(self, *args: str, timeout: float = 20, script: Path = AGENT_ACCESS) -> subprocess.CompletedProcess:
        # Expose only named core tools, not their neighbors (which may include
        # real credential CLIs), and keep aliases such as python3 even when its
        # resolved interpreter is named python3.12.
        core_dir = self.root / "core-bin"
        core_dir.mkdir(exist_ok=True)
        for tool in CORE_TOOLS:
            found = real_binary(tool)
            self.assertIsNotNone(found, f"test host is missing required tool: {tool}")
            link = core_dir / tool
            if link.is_symlink():
                link.unlink()
            link.symlink_to(found)
        env = {
            "PATH": os.pathsep.join((str(self.stub_dir), str(core_dir))),
            "HOME": str(self.home),
            # Not this machine's user: check_github_app reads /mnt/nvme/$USER/gh-app-token when that disk holds the
            # user's directory, which made the github_app stub under HOME unread and this machine's owners the answer.
            "USER": "agent-access-test",
        }
        start = time.monotonic()
        result = subprocess.run(
            [str(script), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        self.elapsed = time.monotonic() - start
        return result

    def access_rows(self, script: Path = AGENT_ACCESS) -> dict[str, dict[str, str]]:
        """Run `agent-access --json` and return its rows by name."""
        result = self.run_access("--json", script=script)
        self.assertEqual(result.returncode, 0, result.stderr)
        return {row["name"]: row for row in json.loads(result.stdout)}

    def write_gws_registry(self, *accounts: str) -> None:
        """Write gws-accounts' registry under HOME, with one key per account."""
        gws_dir = self.home / ".config" / "gws"
        gws_dir.mkdir(parents=True, exist_ok=True)
        (gws_dir / "accounts.json").write_text(json.dumps({account: {} for account in accounts}), encoding="utf-8")

    def private_check_script(self, checks: str) -> Path:
        """Return a copy of agent-access in a dotfiles tree whose private agent-access.d/demo.sh holds CHECKS."""
        dotfiles = self.root / "dotfiles"
        (dotfiles / "scripts").mkdir(parents=True)
        script = dotfiles / "scripts" / "agent-access"
        shutil.copy(AGENT_ACCESS, script)
        private = dotfiles / ".secrets" / "dotfiles" / "agent-access.d"
        private.mkdir(parents=True)
        (private / "demo.sh").write_text(checks, encoding="utf-8")
        return script

    def test_core_tools_are_their_binaries_not_mise_shims(self) -> None:
        write_stub(self.root, "probe", 'for tool in "$@"; do command -v "$tool" || exit 1; done')
        result = self.run_access(*CORE_TOOLS, script=self.root / "probe")
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = result.stdout.splitlines()
        self.assertEqual(len(paths), len(CORE_TOOLS))
        for tool, found in zip(CORE_TOOLS, paths):
            with self.subTest(tool=tool):
                self.assertTrue(os.path.samefile(found, real_binary(tool)), found)
                self.assertNotEqual(Path(found).resolve().name, "mise", found)

    def test_versioned_python_is_available_as_python3_without_neighboring_clis(self) -> None:
        tools = self.root / "tools"
        tools.mkdir()
        write_stub(tools, "python3.99", 'printf "resolved-python\\n"')
        write_stub(tools, "python3", "exit 99")
        write_stub(tools, "aws", 'printf "host-identity\\n"')
        write_stub(self.root, "probe", 'python3 || exit\nif command -v aws; then exit 1; fi')
        resolve = real_binary
        with patch(f"{__name__}.real_binary", side_effect=lambda tool: (
            str(tools / "python3.99") if tool == "python3" else resolve(tool)
        )):
            result = self.run_access(script=self.root / "probe")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "resolved-python\n")

    def assert_no_secrets(self, *texts: str) -> None:
        for text in texts:
            for marker in SECRET_MARKERS:
                self.assertNotIn(marker, text)
            # No full three-part JWT of any shape ever reaches the output.
            self.assertNotRegex(
                text, r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{2,}"
            )

    def test_all_commands_missing_reports_status_without_crashing(self) -> None:
        """Nothing stubbed: every credential is missing/unknown, script still exits 0."""
        result = self.run_access()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("aws", result.stdout)
        self.assertIn("missing", result.stdout)
        self.assert_no_secrets(result.stdout, result.stderr)
        self.assertLess(self.elapsed, 15, "agent-access must finish in about 10s")

    def test_json_output_is_valid_with_required_fields(self) -> None:
        result = self.run_access("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 10)
        names = {row["name"] for row in rows}
        for required in ("aws", "github_app", "dispatch", "modal"):
            self.assertIn(required, names)
        for row in rows:
            for field in ("name", "status", "identity", "expiry", "command", "detail"):
                self.assertIn(field, row)
            self.assertIn(row["status"], {"live", "expired", "missing", "unknown", "unused"})
        self.assert_no_secrets(result.stdout)

    def test_rejects_unknown_flag(self) -> None:
        result = self.run_access("--bogus")
        self.assertEqual(result.returncode, 2)

    def test_gws_rows_follow_the_account_registry(self) -> None:
        """One google_workspace_<account> row per registry key; one missing row without it."""
        write_stub(
            self.stub_dir,
            "gws",
            # The probe is the Gmail profile, which every account's read tier can read.
            '[ "$1 $2 $3" = "gmail users getProfile" ] || { echo "unexpected gws call: $*" >&2; exit 1; }\n'
            'printf \'{"emailAddress":"%s@example.com"}\' "$GWS_ACCOUNT"',
        )
        result = self.run_access("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row["name"]: row for row in json.loads(result.stdout)}
        self.assertEqual(rows["google_workspace"]["status"], "missing")
        self.assertEqual([n for n in rows if n.startswith("google_workspace_")], [])

        gws_dir = self.home / ".config" / "gws"
        gws_dir.mkdir(parents=True)
        (gws_dir / "accounts.json").write_text(json.dumps({"work": {}}), encoding="utf-8")
        result = self.run_access("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row["name"]: row for row in json.loads(result.stdout)}
        self.assertNotIn("google_workspace", rows)
        self.assertEqual([n for n in rows if n.startswith("google_workspace_")], ["google_workspace_work"])
        self.assertEqual(rows["google_workspace_work"]["status"], "live")
        self.assertEqual(rows["google_workspace_work"]["identity"], "work@example.com")

    def test_gws_unusable_registry_reports_one_missing_row(self) -> None:
        """A registry jq cannot read as a non-empty object gives one missing google_workspace row."""
        write_stub(
            self.stub_dir,
            "gws",
            'printf \'{"emailAddress":"%s@example.com"}\' "$GWS_ACCOUNT"',
        )
        gws_dir = self.home / ".config" / "gws"
        gws_dir.mkdir(parents=True)
        cases = {
            "{not json": "parse error",
            '["work"]': "not a JSON object",
            '"work"': "not a JSON object",
            "": "no accounts in",
            "{}": "no accounts in",
        }
        for content, detail in cases.items():
            with self.subTest(content=content):
                (gws_dir / "accounts.json").write_text(content, encoding="utf-8")
                result = self.run_access("--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("jq:", result.stderr)
                rows = {row["name"]: row for row in json.loads(result.stdout)}
                self.assertEqual([n for n in rows if n.startswith("google_workspace_")], [])
                self.assertEqual(rows["google_workspace"]["status"], "missing")
                self.assertIn(detail, rows["google_workspace"]["detail"])

    def test_stubbed_clis_report_live_identity_without_leaking_token(self) -> None:
        write_stub(
            self.stub_dir,
            "aws",
            'echo \'{"Arn":"arn:aws:sts::123:assumed-role/test/i-abc"}\'',
        )
        write_stub(
            self.stub_dir,
            "ant",
            r"""
jwt_header="eyJhbGciOiJIUzI1NiJ9"
jwt_payload="eyJzdWIiOiJzYWZlLXRlc3QifQ"
jwt_signature="signature"
jwt="$(printf '%s.%s.%s' "$jwt_header" "$jwt_payload" "$jwt_signature")"
echo "  Logged in to Example Org as sami@trajectorylabs.net"
echo "               expires:        2099-01-01T00:00:00Z"
echo "access-token: sk-ant-should-never-print"
echo "session-token: $jwt"
""",
        )
        write_stub(self.stub_dir, "vercel", 'echo "noise"; echo "sami-2219"')
        write_stub(
            self.stub_dir,
            "pup",
            r"""
if [[ "$1 $2 $3" == "--no-agent auth status" ]]; then
    echo '{"authenticated":true,"site":"us5.datadoghq.com","expires_at":"2026-01-01T00:00:00Z"}'
    exit 0
fi
""",
        )
        write_stub(
            self.stub_dir,
            "gcloud",
            'echo \'[{"account":"sami@trajectorylabs.net","status":"ACTIVE"}]\'',
        )
        write_stub(
            self.stub_dir,
            "gws",
            # Real gws prints advisories on stderr next to the JSON on stdout.
            'echo "Using keyring backend: keyring" >&2\n'
            'printf \'{"emailAddress":"%s@example.com"}\' "${GWS_ACCOUNT:-unknown}"',
        )
        gws_dir = self.home / ".config" / "gws"
        gws_dir.mkdir(parents=True)
        (gws_dir / "accounts.json").write_text(
            json.dumps({name: {} for name in ("personal", "theorem", "trajectory")}), encoding="utf-8"
        )
        write_stub(
            self.stub_dir,
            "fleetctl",
            r"""
echo "ghs_should_never_print"
echo "not authenticated"
exit 1
""",
        )
        write_stub(
            self.stub_dir,
            "sentry-cli",
            'echo "  User: sami@trajectorylabs.net"',
        )
        write_stub(
            self.stub_dir,
            "kubectl",
            r"""
if [[ "$1 $2" == "config current-context" ]]; then echo production; exit 0; fi
if [[ "$1 $2" == "auth whoami" ]]; then
    echo "Username   arn:aws:sts::123:assumed-role/test/i-abc"
    exit 0
fi
""",
        )
        write_stub(
            self.stub_dir,
            "secrets",
            f"""
if [[ "$1" == grants ]]; then
    printf %s {shlex.quote(SECRETS_GRANTS_V384)}
    exit 0
fi
""",
        )
        write_stub(self.stub_dir, "agent-secrets", 'exit 0')

        # github_app cache: covered owners, one no real cache holds, so only the stub can answer it
        cache = self.home / ".cache" / "gh-app-token"
        cache.mkdir(parents=True)
        (cache / "covered-123.json").write_text(json.dumps({"stub-owner": 1}))

        result = self.run_access("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row["name"]: row for row in json.loads(result.stdout)}

        self.assertEqual(rows["aws"]["status"], "live")
        self.assertIn("assumed-role/test", rows["aws"]["identity"])
        self.assertEqual(rows["anthropic_ant"]["status"], "live")
        self.assertEqual(rows["github_app"]["status"], "live")
        self.assertIn("stub-owner", rows["github_app"]["identity"])
        self.assertEqual(rows["vercel"]["identity"], "sami-2219")
        self.assertEqual(rows["datadog_pup"]["status"], "live")
        self.assertEqual(rows["gcloud"]["status"], "live")
        self.assertEqual(rows["google_workspace_personal"]["status"], "live")
        self.assertEqual(rows["google_workspace_personal"]["identity"], "personal@example.com")
        self.assertEqual(rows["google_workspace_theorem"]["status"], "live")
        self.assertEqual(rows["google_workspace_theorem"]["identity"], "theorem@example.com")
        self.assertEqual(rows["google_workspace_trajectory"]["status"], "live")
        self.assertEqual(rows["google_workspace_trajectory"]["identity"], "trajectory@example.com")
        self.assertEqual(rows["sentry"]["status"], "live")
        self.assertEqual(rows["kubectl"]["status"], "live")
        self.assertEqual(rows["secretsd_grants"]["identity"], "TEST_KEY")
        self.assertEqual(rows["fleetctl"]["status"], "missing")
        self.assertEqual(rows["fleetctl"]["detail"], "not authenticated")
        self.assertEqual(rows["agent_secrets_broker"]["status"], "live")

        self.assert_no_secrets(result.stdout)

    def timed_out_rows(self, outputs: dict[str, str]) -> dict[str, dict[str, str]]:
        """Return the rows when each command in OUTPUTS runs its stub body, then exits 124 as `timeout` does."""
        for command, body in outputs.items():
            write_stub(self.stub_dir, command, f"{body}\nexit 124")
        self.write_gws_registry("work")
        return self.access_rows()

    def assert_timed_out(self, rows: dict[str, dict[str, str]], details: dict[str, str]) -> None:
        """Each check in TIMED_CHECKS is missing, its detail the one DETAILS names or `timed out after 6s`."""
        for name, command in TIMED_CHECKS.items():
            with self.subTest(check=name):
                row = rows[name]
                self.assertEqual(
                    (row["status"], row["detail"]),
                    ("missing", details.get(name, "timed out after 6s")),
                    f"{name} ({command} exited 124)",
                )

    def test_timeout_after_partial_output_reports_the_timeout(self) -> None:
        """`timeout` exits 124 when it stops a command; the detail says so, not the partial answer printed first."""
        rows = self.timed_out_rows(PARTIAL_OUTPUT)
        self.assert_timed_out(rows, {"kubectl": "context: production; timed out after 6s"})

    def test_timeout_with_no_output_reports_the_timeout(self) -> None:
        """A command stopped before it printed: no empty detail, no broker fallback, and kubectl's context timed out."""
        rows = self.timed_out_rows({command: "" for command in TIMED_CHECKS.values()})
        self.assert_timed_out(rows, {})

    def test_failure_detail_skips_an_empty_first_line(self) -> None:
        """A failure's detail is its first non-empty line: a lone CR line and a line of only blanks are empty."""
        expired = (
            "An error occurred (ExpiredToken) when calling the GetCallerIdentity operation:"
            " The security token included in the request is expired"
        )
        crashed = "ERROR: gcloud crashed (OperationalError): unable to open database file"
        write_stub(self.stub_dir, "aws", f"echo\necho {shlex.quote(expired)}\nexit 253")
        write_stub(self.stub_dir, "dispatch", "printf '\\r\\ndispatch: not logged in\\n'\nexit 1")
        write_stub(self.stub_dir, "gcloud", f"printf ' \\t \\n'\necho {shlex.quote(crashed)}\nexit 1")
        result = self.run_access("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row["name"]: row for row in json.loads(result.stdout)}
        details = {"aws": expired, "dispatch": "dispatch: not logged in", "gcloud": crashed}
        for name, detail in details.items():
            with self.subTest(check=name):
                self.assertEqual((rows[name]["status"], rows[name]["detail"]), ("missing", detail))

    def test_failure_with_no_output_names_its_exit_code(self) -> None:
        """A command that fails and prints nothing gives the detail `exit <code>, no output`, never an empty one."""
        for command in TIMED_CHECKS.values():
            write_stub(self.stub_dir, command, "exit 3")
        self.write_gws_registry("work")
        rows = self.access_rows()
        details = {name: "exit 3, no output" for name in TIMED_CHECKS}
        # The broker check's own text for an empty answer stays.
        details["agent_secrets_broker"] = "no broker identity; secret-run falls back to secretsd"
        for name, detail in details.items():
            with self.subTest(check=name):
                self.assertEqual((rows[name]["status"], rows[name]["detail"]), ("missing", detail))
        write_stub(
            self.stub_dir, "kubectl", '[[ "$1 $2" == "config current-context" ]] && { echo production; exit 0; }\nexit 3'
        )
        with self.subTest(check="kubectl auth whoami"):
            kubectl = self.access_rows()["kubectl"]
            self.assertEqual(
                (kubectl["status"], kubectl["detail"]), ("missing", "context: production; exit 3, no output")
            )

    def test_failure_that_printed_text_never_reports_no_output(self) -> None:
        """A failed tool's text reaches the detail from whichever stream it wrote, never `exit <code>, no output`.

        pup writes to stderr (on oryx it prints `Not authenticated` there beside its JSON), and a gws that writes its
        error to stdout leaves stderr empty; each check that reads one stream first falls back to the other.
        """
        write_stub(self.stub_dir, "pup", "printf '\\nError: request to api.datadoghq.com timed out\\n' >&2\nexit 1")
        write_stub(self.stub_dir, "gws", "printf '\\nerror: 403 insufficient scope\\n'\nexit 1")
        self.write_gws_registry("work")
        rows = self.access_rows()
        details = {
            "datadog_pup": "Error: request to api.datadoghq.com timed out",
            "google_workspace_work": "error: 403 insufficient scope",
        }
        for name, detail in details.items():
            with self.subTest(check=name):
                self.assertEqual((rows[name]["status"], rows[name]["detail"]), ("missing", detail))

    def test_datadog_answer_without_a_status_reports_its_first_line(self) -> None:
        """pup's answer with no `.status` gives its first line as the detail, not an empty one."""
        cases = {
            ('{"authenticated":false}', 0): '{"authenticated":false}',
            ('{"authenticated":false,"status":"token expired"}', 0): "token expired",
            ("Not authenticated", 0): "Not authenticated",
            ("", 1): "exit 1, no output",
        }
        for (stdout, code), detail in cases.items():
            with self.subTest(stdout=stdout, code=code):
                write_stub(self.stub_dir, "pup", f"printf %s {shlex.quote(stdout)}\nexit {code}")
                row = self.access_rows()["datadog_pup"]
                self.assertEqual((row["status"], row["detail"]), ("missing", detail))

    def test_gcloud_parses_only_stdout_and_reports_an_unparseable_answer(self) -> None:
        """A stderr warning does not hide the ACTIVE account; an answer that is not a JSON list is `unknown`.

        Only a parsed list with no ACTIVE account is `no ACTIVE account`, and a failure reports stderr's first line.
        """
        active = '[{"account":"sami@example.com","status":"ACTIVE"}]'
        cases = {
            "a warning on stderr before the answer": (
                f"echo 'WARNING: update available' >&2\necho {shlex.quote(active)}",
                ("live", "sami@example.com", ""),
            ),
            "text on stdout": (
                "echo 'You are not logged in'",
                ("unknown", "-", "unparseable answer: You are not logged in"),
            ),
            "empty stdout": ("exit 0", ("unknown", "-", "unparseable answer: ")),
            "a list with no ACTIVE account": ("echo '[]'", ("missing", "-", "no ACTIVE account")),
            "a failure with its error on stderr after stdout": (
                "echo '[]'\necho 'ERROR: (gcloud.auth.list) boom' >&2\nexit 1",
                ("missing", "-", "ERROR: (gcloud.auth.list) boom"),
            ),
        }
        for case, (body, expected) in cases.items():
            with self.subTest(case=case):
                write_stub(self.stub_dir, "gcloud", body)
                row = self.access_rows()["gcloud"]
                self.assertEqual((row["status"], row["identity"], row["detail"]), expected)

    def test_vercel_identity_is_stdout_and_a_failure_reports_stderr(self) -> None:
        """vercel's notice on stderr is not the identity; a failure reports stderr's last line, else stdout's."""
        cases = {
            "an update notice on stderr after the identity": (
                "echo sami-2219\necho 'Run npm i -g vercel@latest to update.' >&2",
                ("live", "sami-2219", ""),
            ),
            "a failure with its error on stderr before stdout": (
                "echo 'Error: No existing credentials found.' >&2\necho 'Vercel CLI 39.1.0'\nexit 1",
                ("missing", "-", "Error: No existing credentials found."),
            ),
            "a failure with its error on stdout only": (
                "echo 'Error: not logged in'\nexit 1",
                ("missing", "-", "Error: not logged in"),
            ),
        }
        for case, (body, expected) in cases.items():
            with self.subTest(case=case):
                write_stub(self.stub_dir, "vercel", body)
                row = self.access_rows()["vercel"]
                self.assertEqual((row["status"], row["identity"], row["detail"]), expected)

    def test_last_line_readers_skip_empty_lines_and_carriage_returns(self) -> None:
        """Where a check reads the last line on purpose, it reads the last non-empty one, CR removed."""
        write_stub(
            self.stub_dir, "gws", "printf 'Using keyring backend: keyring\\nerror: 401 Unauthorized\\n\\n' >&2\nexit 1"
        )
        self.write_gws_registry("work")
        write_stub(self.stub_dir, "secrets", "printf 'secrets: cannot reach secretsd\\n\\n' >&2\nexit 1")
        write_stub(self.stub_dir, "fleetctl", "printf 'denied\\r\\n\\r\\n'\nexit 1")
        write_stub(self.stub_dir, "vercel", "printf 'Error: no credentials\\r\\n \\r\\n' >&2\nexit 1")
        rows = self.access_rows()
        details = {
            "google_workspace_work": "error: 401 Unauthorized",
            "secretsd_grants": "secrets: cannot reach secretsd",
            "fleetctl": "denied",
            "vercel": "Error: no credentials",
        }
        for name, detail in details.items():
            with self.subTest(check=name):
                self.assertEqual((rows[name]["status"], rows[name]["detail"]), ("missing", detail))
        write_stub(self.stub_dir, "vercel", "printf 'sami-2219\\r\\n\\r\\n'")
        with self.subTest(check="vercel identity"):
            vercel = self.access_rows()["vercel"]
            self.assertEqual((vercel["status"], vercel["identity"]), ("live", "sami-2219"))

    def test_aws_parses_only_stdout_and_a_failure_reports_stderr(self) -> None:
        """A stderr warning does not hide a valid ARN; a failure reports stderr's first non-empty line."""
        arn = "arn:aws:sts::123:assumed-role/test/i-abc"
        expired = "An error occurred (ExpiredToken) when calling the GetCallerIdentity operation: token expired"
        cases = {
            "a warning on stderr before the answer": (
                f"echo 'WARNING: Python 3.8 is deprecated' >&2\necho {shlex.quote(json.dumps({'Arn': arn}))}",
                ("live", arn, ""),
            ),
            "a failure with its error on stderr after an empty line": (
                f"echo >&2\necho {shlex.quote(expired)} >&2\nexit 254",
                ("missing", "-", expired),
            ),
        }
        for case, (body, expected) in cases.items():
            with self.subTest(case=case):
                write_stub(self.stub_dir, "aws", body)
                row = self.access_rows()["aws"]
                self.assertEqual((row["status"], row["identity"], row["detail"]), expected)

    def test_github_app_unreadable_cache_is_unknown(self) -> None:
        """A covered-owner cache that is not a JSON object gives `unknown` and names the file, not `live`."""
        cache = self.home / ".cache" / "gh-app-token"
        cache.mkdir(parents=True)
        covered = cache / "covered-123.json"
        for content in ("{not json", '["stub-owner"]', ""):
            with self.subTest(content=content):
                covered.write_text(content, encoding="utf-8")
                row = self.access_rows()["github_app"]
                self.assertEqual(
                    (row["status"], row["identity"], row["detail"]),
                    ("unknown", "-", f"unreadable covered-owner cache: {covered}"),
                )

    def test_sentry_answer_without_a_user_line_says_so(self) -> None:
        """sentry-cli info that exits 0 with no `User:` line (an org auth token) is missing for that reason."""
        cases = {
            "Sentry Server: https://sentry.io\nDefault Organization: example\n": (
                0,
                "no User: line in sentry-cli info",
            ),
            "error: API request failed\n  caused by: 401\n": (1, "error: API request failed"),
        }
        for stdout, (code, detail) in cases.items():
            with self.subTest(code=code):
                write_stub(self.stub_dir, "sentry-cli", f"printf %s {shlex.quote(stdout)}\nexit {code}")
                row = self.access_rows()["sentry"]
                self.assertEqual((row["status"], row["detail"]), ("missing", detail))

    def test_live_answer_with_no_identity_line_reports_unknown(self) -> None:
        """vercel and dispatch exiting 0 with no non-empty line report the identity `unknown`, as other checks do."""
        cases = {
            "no output": "exit 0",
            "only blank lines": "printf '\\n \\r\\n'",
        }
        for case, body in cases.items():
            write_stub(self.stub_dir, "vercel", body)
            write_stub(self.stub_dir, "dispatch", body)
            rows = self.access_rows()
            for name in ("vercel", "dispatch"):
                with self.subTest(case=case, check=name):
                    self.assertEqual((rows[name]["status"], rows[name]["identity"]), ("live", "unknown"))

    def test_broker_failure_with_only_blank_output_gives_the_fallback_text(self) -> None:
        """`agent-secrets identity` failing with only blanks and CRs gives the fallback detail, not a blank one.

        A timeout still reports the timeout.
        """
        fallback = "no broker identity; secret-run falls back to secretsd"
        cases = {
            "spaces, a tab and CRLF": ("printf ' \\r\\n\\t\\n'", 1, fallback),
            "a lone CRLF": ("printf '\\r\\n'", 1, fallback),
            "blank output, then a timeout": ("printf ' \\r\\n'", 124, "timed out after 6s"),
        }
        for case, (body, code, detail) in cases.items():
            with self.subTest(case=case):
                write_stub(self.stub_dir, "agent-secrets", f"{body}\nexit {code}")
                row = self.access_rows()["agent_secrets_broker"]
                self.assertEqual((row["status"], row["detail"]), ("missing", detail))

    def test_kubectl_context_is_stdout_and_a_failure_reports_stderr(self) -> None:
        """A kubectl warning on stderr is not part of the context; a current-context failure reports stderr's line."""
        # `kubectl auth whoami` answers; any call but `config current-context` exits 2.
        stub = (
            'if [[ "$1 $2" == "auth whoami" ]]; then\n'
            '    echo "Username   arn:aws:sts::123:assumed-role/test/i-abc"; exit 0\n'
            "fi\n"
            '[[ "$1 $2" == "config current-context" ]] || exit 2'
        )
        cases = {
            "a warning on stderr before the context": (
                "echo 'W1122 warning: config is old' >&2\necho production",
                ("live", "arn:aws:sts::123:assumed-role/test/i-abc (context: production)", ""),
            ),
            "a failure with its error on stderr after stdout": (
                "echo 'Fetching context'\necho 'error: current-context is not set' >&2\nexit 1",
                ("missing", "-", "error: current-context is not set"),
            ),
        }
        for case, (context, expected) in cases.items():
            with self.subTest(case=case):
                write_stub(self.stub_dir, "kubectl", f"{stub}\n{context}")
                row = self.access_rows()["kubectl"]
                self.assertEqual((row["status"], row["identity"], row["detail"]), expected)

    def test_kubectl_empty_context_is_unknown(self) -> None:
        """current-context exiting 0 with no non-empty line gives `(context: unknown)`, not an empty context."""
        cases = {
            "no output": "exit 0",
            "only blank lines": "printf '\\n \\t\\r\\n'\nexit 0",
        }
        for case, context in cases.items():
            with self.subTest(case=case):
                write_stub(
                    self.stub_dir,
                    "kubectl",
                    f'if [[ "$1 $2" == "config current-context" ]]; then\n{context}\nfi\n'
                    'echo "Username   arn:aws:sts::123:assumed-role/test/i-abc"',
                )
                row = self.access_rows()["kubectl"]
                self.assertEqual(
                    (row["status"], row["identity"]),
                    ("live", "arn:aws:sts::123:assumed-role/test/i-abc (context: unknown)"),
                )

    def test_dispatch_identity_is_stdout_and_a_failure_reports_stderr(self) -> None:
        """A dispatch warning on stderr is not the identity; a failure reports stderr's first line, else stdout's."""
        cases = {
            "a warning on stderr before the identity": (
                "echo 'dispatch: warning: config is old' >&2\necho 'session 01a20508 (Legion implementer)'",
                ("live", "session 01a20508 (Legion implementer)", ""),
            ),
            "a failure with its error on stderr after stdout": (
                "echo 'contacting the server'\necho 'dispatch: not logged in' >&2\nexit 1",
                ("missing", "-", "dispatch: not logged in"),
            ),
        }
        for case, (body, expected) in cases.items():
            with self.subTest(case=case):
                write_stub(self.stub_dir, "dispatch", f'[[ "$1" == whoami ]] || exit 2\n{body}')
                row = self.access_rows()["dispatch"]
                self.assertEqual((row["status"], row["identity"], row["detail"]), expected)

    def secretsd_row(self, stdout: str, stderr: str = "", code: int = 0) -> dict[str, str]:
        """Return the secretsd_grants row when `secrets grants` writes STDERR, then STDOUT, and exits CODE.

        Any other `secrets` subcommand exits 2.
        """
        stdout_file = self.root / "secrets-grants.stdout"
        stderr_file = self.root / "secrets-grants.stderr"
        stdout_file.write_text(stdout, encoding="utf-8")
        stderr_file.write_text(stderr, encoding="utf-8")
        write_stub(
            self.stub_dir,
            "secrets",
            f"""
[[ "$1" == grants ]] || exit 2
cat {shlex.quote(str(stderr_file))} >&2
cat {shlex.quote(str(stdout_file))}
exit {code}
""",
        )
        result = self.run_access("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows: list[dict[str, str]] = json.loads(result.stdout)
        return next(row for row in rows if row["name"] == "secretsd_grants")

    def test_secretsd_v384_answer(self) -> None:
        row = self.secretsd_row(SECRETS_GRANTS_V384)
        self.assertEqual((row["status"], row["identity"]), ("live", "TEST_KEY"))

    def test_secretsd_no_active_grants(self) -> None:
        row = self.secretsd_row("no active grants\n")
        self.assertEqual((row["status"], row["identity"]), ("live", "none"))

    def test_secretsd_both_tables(self) -> None:
        """The secret-grant table, an empty line, then the KIND table: only the first holds keys."""
        row = self.secretsd_row(
            "SECRET GRANT\tSCOPE\tEXPIRES\n"
            "TEST_KEY\tsession\t3412s\n"
            "\n"
            "KIND\tNAME\tSCOPE\tSTATE\tEXPIRES\n"
            "request\tCAP_BROWSER\tomp 4242\tpending, id 7\t72s\n"
            "capability\tbrowser\t-\tactive\t41012s\n"
        )
        self.assertEqual((row["status"], row["identity"]), ("live", "TEST_KEY"))

    def test_secretsd_second_table_only(self) -> None:
        row = self.secretsd_row(
            "KIND\tNAME\tSCOPE\tSTATE\tEXPIRES\n"
            "request\tCAP_BROWSER\tomp 4242\tpending, id 7\t72s\n"
            "capability\tbrowser\t-\tactive\t41012s\n"
        )
        self.assertEqual((row["status"], row["identity"]), ("live", "none"))

    def test_secretsd_two_keys(self) -> None:
        row = self.secretsd_row("SECRET GRANT\tSCOPE\tEXPIRES\nA\tsession\t5s\nB\ttty /dev/pts/3\t9s\n")
        self.assertEqual((row["status"], row["identity"]), ("live", "A,B"))

    def test_secretsd_kind_line_with_no_empty_line(self) -> None:
        row = self.secretsd_row(
            "SECRET GRANT\tSCOPE\tEXPIRES\n"
            "TEST_KEY\tsession\t3412s\n"
            "KIND\tNAME\tSCOPE\tSTATE\tEXPIRES\n"
            "request\tCAP_BROWSER\tomp 4242\tpending, id 7\t72s\n"
        )
        self.assertEqual((row["status"], row["identity"]), ("live", "TEST_KEY"))

    def test_secretsd_stderr_before_the_table(self) -> None:
        """A shim's notice on stderr, written before the table, is not a key."""
        row = self.secretsd_row(SECRETS_GRANTS_V384, stderr=SECRETS_SHIM_NOTICE)
        self.assertEqual((row["status"], row["identity"]), ("live", "TEST_KEY"))

    def test_secretsd_failure(self) -> None:
        row = self.secretsd_row("", stderr="secrets: cannot reach secretsd\n", code=1)
        self.assertEqual((row["status"], row["identity"]), ("missing", "-"))
        self.assertEqual(row["detail"], "secrets: cannot reach secretsd")

    def test_secretsd_failure_after_a_shim_notice(self) -> None:
        """The client's own error, written after the shim's notice, is the detail."""
        row = self.secretsd_row("", stderr=SECRETS_SHIM_NOTICE + "secrets: cannot reach secretsd\n", code=1)
        self.assertEqual((row["status"], row["identity"]), ("missing", "-"))
        self.assertEqual(row["detail"], "secrets: cannot reach secretsd")

    def test_secretsd_failure_with_only_stdout(self) -> None:
        """With no stderr the detail is stdout's first line, its tabs made spaces so the row keeps its fields."""
        row = self.secretsd_row("KEY\tSCOPE\n", code=1)
        self.assertEqual((row["status"], row["identity"]), ("missing", "-"))
        self.assertEqual(row["detail"], "KEY SCOPE")

    def test_secretsd_unrecognised_answer(self) -> None:
        row = self.secretsd_row("NEW HEADER\tX\nA\tsession\t5s\n")
        self.assertEqual((row["status"], row["identity"]), ("unknown", "-"))
        self.assertEqual(row["detail"], "unrecognised answer: NEW HEADER X")

    def test_secretsd_empty_stdout(self) -> None:
        row = self.secretsd_row("")
        self.assertEqual((row["status"], row["identity"]), ("unknown", "-"))
        self.assertEqual(row["detail"], "unrecognised answer: ")

    def test_secretsd_row_text(self) -> None:
        row = self.secretsd_row(SECRETS_GRANTS_V384)
        self.assertEqual(row["expiry"], "see `secrets grants`")
        self.assertEqual(row["detail"], "keys of every session and scope; names only, never values")

    def test_row_makes_a_tab_in_a_detail_a_space(self) -> None:
        """check_fleetctl's detail is a raw output line; row() makes its tab a space so the row keeps its fields."""
        write_stub(self.stub_dir, "fleetctl", 'printf "denied\\tby policy\\n"\nexit 1')
        result = self.run_access("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row["name"]: row for row in json.loads(result.stdout)}
        self.assertEqual((rows["fleetctl"]["status"], rows["fleetctl"]["detail"]), ("missing", "denied by policy"))

    def test_row_makes_a_newline_in_a_kubectl_identity_and_a_broker_detail_a_space(self) -> None:
        """A warning line before the kubectl context and a two-line broker error stay inside their rows.

        A newline in a field splits the row file in two: `--json` reads only the first line, so the row loses its
        later fields, and the table prints the rest as a line of its own.
        """
        write_stub(
            self.stub_dir,
            "kubectl",
            r"""
if [[ "$1 $2" == "config current-context" ]]; then printf 'W1122 warning: config is old\nproduction\n'; exit 0; fi
if [[ "$1 $2" == "auth whoami" ]]; then
    echo "Username   arn:aws:sts::123:assumed-role/test/i-abc"
    exit 0
fi
exit 2
""",
        )
        write_stub(
            self.stub_dir,
            "agent-secrets",
            r"""
[[ "$1" == identity ]] || exit 2
printf 'agent-secrets: no broker session\nhint: run under secret-run\n'
exit 1
""",
        )
        result = self.run_access("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row["name"]: row for row in json.loads(result.stdout)}
        kubectl = rows["kubectl"]
        self.assertEqual(
            (kubectl["status"], kubectl["identity"], kubectl["expiry"], kubectl["command"]),
            (
                "live",
                "arn:aws:sts::123:assumed-role/test/i-abc (context: W1122 warning: config is old production)",
                "n/a (assumed-role session)",
                "kubectl auth whoami",
            ),
        )
        broker = rows["agent_secrets_broker"]
        self.assertEqual(
            (broker["status"], broker["detail"]),
            ("missing", "agent-secrets: no broker session hint: run under secret-run"),
        )

        table = self.run_access()
        self.assertEqual(table.returncode, 0, table.stderr)
        lines = table.stdout.splitlines()
        # The header, then one line per row: a split row adds a line that starts with neither name.
        self.assertEqual(len(lines), len(rows) + 1, table.stdout)
        for name in ("kubectl", "agent_secrets_broker"):
            with self.subTest(name=name):
                self.assertEqual(len([line for line in lines if line.startswith(name)]), 1, table.stdout)

    def test_row_makes_tabs_and_newlines_spaces_in_every_field(self) -> None:
        """row() makes each tab and newline in the identity, expiry, command and detail a space."""
        script = self.private_check_script(
            "check_demo() {\n"
            "    row demo_platform live $'id\\tone\\ntwo' $'exp\\tone\\ntwo' $'cmd\\tone\\ntwo' $'det\\tone\\ntwo'\n"
            "}\n"
            "extra_checks+=(check_demo)\n"
            "extra_rows+=(demo_platform)\n"
        )
        result = self.run_access("--json", script=script)
        self.assertEqual(result.returncode, 0, result.stderr)
        demo = next(row for row in json.loads(result.stdout) if row["name"] == "demo_platform")
        self.assertEqual(
            (demo["status"], demo["identity"], demo["expiry"], demo["command"], demo["detail"]),
            ("live", "id one two", "exp one two", "cmd one two", "det one two"),
        )

    def test_row_removes_carriage_returns_from_every_field(self) -> None:
        """A CRLF kubectl context, a CRLF broker error and a CR in each field of a private row reach no row."""
        write_stub(
            self.stub_dir,
            "kubectl",
            r"""
if [[ "$1 $2" == "config current-context" ]]; then printf 'production\r\n'; exit 0; fi
if [[ "$1 $2" == "auth whoami" ]]; then
    echo "Username   arn:aws:sts::123:assumed-role/test/i-abc"
    exit 0
fi
exit 2
""",
        )
        write_stub(
            self.stub_dir,
            "agent-secrets",
            "printf 'agent-secrets: no broker session\\r\\nhint: run under secret-run\\r\\n'\nexit 1",
        )
        script = self.private_check_script(
            "check_demo() { row demo_platform live $'id\\r\\none' $'exp\\r\\none' $'cmd\\r\\none' $'det\\r\\none'; }\n"
            "extra_checks+=(check_demo)\n"
            "extra_rows+=(demo_platform)\n"
        )
        rows = self.access_rows(script)
        checks = {
            "kubectl": ("live", "arn:aws:sts::123:assumed-role/test/i-abc (context: production)", ""),
            "agent_secrets_broker": ("missing", "-", "agent-secrets: no broker session hint: run under secret-run"),
        }
        for name, expected in checks.items():
            with self.subTest(check=name):
                self.assertEqual((rows[name]["status"], rows[name]["identity"], rows[name]["detail"]), expected)
        with self.subTest(check="demo_platform"):
            demo = rows["demo_platform"]
            self.assertEqual(
                (demo["status"], demo["identity"], demo["expiry"], demo["command"], demo["detail"]),
                ("live", "id one", "exp one", "cmd one", "det one"),
            )

    def test_json_keeps_every_field_in_its_place_after_an_empty_field(self) -> None:
        """`--json` keeps each of a row's six fields in place when the identity and expiry are empty.

        `IFS=$'\\t' read` makes a run of tabs one separator, so each field after an empty one moved one place left.
        """
        script = self.private_check_script(
            'check_demo() { row demo_platform live "" "" "demo login" "from the private part"; }\n'
            "extra_checks+=(check_demo)\n"
            "extra_rows+=(demo_platform)\n"
        )
        demo = self.access_rows(script)["demo_platform"]
        self.assertEqual(
            (demo["status"], demo["identity"], demo["expiry"], demo["command"], demo["detail"]),
            ("live", "", "", "demo login", "from the private part"),
        )

    def test_private_checks_beside_the_dotfiles_are_sourced_and_ordered(self) -> None:
        # A check whose name would name a customer lives in <dotfiles>/.secrets/dotfiles/
        # agent-access.d/*.sh (a private repository); the script finds that directory from its
        # own path, runs each check it adds and prints the rows it names after `anthropic_ant`.
        script = self.private_check_script(
            'check_demo() { row demo_platform live demo-identity - "demo login" "from the private part"; }\n'
            "extra_checks+=(check_demo)\n"
            "extra_rows+=(demo_platform)\n"
        )
        result = self.run_access("--json", script=script)
        self.assertEqual(result.returncode, 0, result.stderr)
        names = [row["name"] for row in json.loads(result.stdout)]
        self.assertIn("demo_platform", names)
        self.assertLess(names.index("anthropic_ant"), names.index("demo_platform"))
        demo = next(row for row in json.loads(result.stdout) if row["name"] == "demo_platform")
        self.assertEqual((demo["status"], demo["identity"]), ("live", "demo-identity"))

    def test_without_the_private_directory_only_the_public_checks_run(self) -> None:
        result = self.run_access("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        names = [row["name"] for row in json.loads(result.stdout)]
        self.assertIn("anthropic_ant", names)
        self.assertFalse([n for n in names if n.startswith("demo")], names)


if __name__ == "__main__":
    unittest.main()
