#!/usr/bin/env python3
"""scripts/gcloud-reader: one Google read as an agent reader account, through the broker.

gcloud-reader asks the agent-secrets client for METADATA_READ (a token of
metadata-reader@theorem-agent-infra) or, under --as legion, LEGION_READ (a token of
legion-reader@theorem-agent-infra), always with --ttl 1h: the broker refuses a request with no lifetime on a secret whose
max-grant is an hour. The broker releases a google-service-account secret as a bare access token
(cybermonklabs/legion packages/envoy/internal/broker/secrets/googleserviceaccount.go, Minted{Value:
token.AccessToken}), which the fake client below hands over the same way. The token goes to
0600 files of a directory removed on exit, never into an argument list, and the command runs with
CLOUDSDK_AUTH_ACCESS_TOKEN_FILE set to it, BIGQUERYRC naming a bigqueryrc that gives bq the token,
core/check_gce_metadata off, a fresh CLOUDSDK_CONFIG and gcloud's file logging off. The real-bq
cases run the SDK's bq against stand-ins for the metadata server and the BigQuery API.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from gce_metadata_stub import STUB_BEARER, MetadataStub

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "scripts" / "gcloud-reader"
TOKEN = "fake-reader-access-token"
SDK_BIN = Path(os.environ.get("HOME", "/nonexistent")) / ".gcloud" / "google-cloud-sdk" / "bin"
# Run by the command gcloud-reader gives it: prints the token as gcloud would, then lists the log
# files gcloud wrote under the configuration directory gcloud-reader made, and those holding the
# token, before the reader removes that directory on exit. A failed print leaves no token in
# "printed", which the test reads.
PRINT_TOKEN_AND_LIST_LOGS = (
    'gcloud auth print-access-token > "$LOG/printed"; '
    'find "$CLOUDSDK_CONFIG" -path "*/logs/*" -type f > "$LOG/log-files"; '
    'find "$CLOUDSDK_CONFIG" -path "*/logs/*" -type f -exec grep -lF "$FAKE_TOKEN" {} + > "$LOG/token-files"; '
    'exit 0'
)

# The fake agent-secrets client logs each argument on its own line and the broker URL it was given,
# then, unless AGENT_SECRETS_EXIT is set, exports the requested name (the first argument: the client
# takes the names before its flags) as the bare token and runs the command after `--`, as the
# client does.
AGENT_SECRETS = r"""#!/bin/bash
printf '%s\n' "$@" >> "$LOG/agent-secrets.argv"
printf '%s\n' "${AGENT_SECRETS_URL:-}" > "$LOG/agent-secrets.url"
[[ -z "${AGENT_SECRETS_EXIT:-}" ]] || exit "$AGENT_SECRETS_EXIT"
key=$1
while (( $# )) && [[ "$1" != -- ]]; do shift; done
shift
export "$key=$FAKE_TOKEN"
exec "$@"
"""

# A fake tool logs its argv, the environment gcloud-reader gave it, the token file's content, the
# bigqueryrc's path and mode, and the boto file gsutil would read.
TOOL = r"""#!/bin/bash
{
  printf 'argv %s\n' "$0 $*"
  printf 'token_file %s\n' "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-}"
  printf 'token_file_mode %s\n' "$(stat -c %a "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-/nonexistent}" 2>/dev/null)"
  printf 'token %s\n' "$(cat "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-/nonexistent}" 2>/dev/null)"
  printf 'config %s\n' "${CLOUDSDK_CONFIG:-}"
  printf 'prompts %s\n' "${CLOUDSDK_CORE_DISABLE_PROMPTS:-}"
  printf 'impersonate %s\n' "${CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT:-}"
  printf 'env_metadata %s\n' "${METADATA_READ:-}"
  printf 'env_legion %s\n' "${LEGION_READ:-}"
  printf 'file_logging %s\n' "${CLOUDSDK_CORE_DISABLE_FILE_LOGGING:-}"
  printf 'bigqueryrc %s\n' "${BIGQUERYRC:-}"
  printf 'bigqueryrc_mode %s\n' "$(stat -c %a "${BIGQUERYRC:-/nonexistent}" 2>/dev/null)"
  printf 'boto %s\n' "${BOTO_CONFIG:-}"
  printf 'boto_text %s\n' "$(tr '\n' '|' <"${BOTO_CONFIG:-/nonexistent}" 2>/dev/null)"
} >> "$LOG/tool.log"
"""


class BigQueryStub(http.server.ThreadingHTTPServer):
    """A stand-in for the BigQuery API that bq's --api names: it serves the SDK's own discovery
    document and an empty dataset list, and records the Authorization header of each request."""

    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _BigQueryHandler)
        self.authorizations: list[str] = []

    @property
    def host(self) -> str:
        return f"{self.server_address[0]}:{self.server_address[1]}"

    @classmethod
    def start(cls) -> "BigQueryStub":
        server = cls()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


class _BigQueryHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        assert isinstance(self.server, BigQueryStub)
        self.server.authorizations.append(self.headers.get("Authorization", ""))
        path = self.path.split("?", 1)[0]
        if path == "/$discovery/rest":
            status, body = 200, (SDK_BIN.parent / "platform" / "bq" / "discovery_next" / "bigquery.json").read_bytes()
        elif re.fullmatch(r"/bigquery/v2/projects/[^/]+/datasets", path):
            status, body = 200, json.dumps({"kind": "bigquery#datasetList", "etag": "stub"}).encode()
        else:
            status, body = 404, b'{"error": {"code": 404, "message": "not found"}}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server's name
        pass


def ttl_problems(script: Path, env_extra: dict[str, str] | None = None) -> list[str]:
    """Runs script as gcloud-reader for each --as, and returns what the client's argv lacks."""
    problems = []
    for as_args, key in (((), "METADATA_READ"), (("--as", "metadata"), "METADATA_READ"), (("--as", "legion"), "LEGION_READ")):
        with Fixture(script) as f:
            result = f.run(*as_args, "storage", "buckets", "list", env_extra=env_extra)
            argv = f.client_argv()
            if result.returncode != 0:
                problems.append(f"{as_args}: exit {result.returncode}: {result.stderr}")
            if "--ttl\n1h\n" not in "".join(a + "\n" for a in argv):
                problems.append(f"{as_args}: agent-secrets got no --ttl 1h: {argv}")
            names = argv[: next((i for i, a in enumerate(argv) if a.startswith("-")), len(argv))]
            if names != [key]:
                problems.append(f"{as_args}: agent-secrets did not ask for {key} alone: {argv}")
    return problems


class Fixture:
    def __init__(self, script: Path) -> None:
        self.script = script

    def __enter__(self) -> "Fixture":
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bin = root / "bin"
        self.log = root / "log"
        self.tmpdir = root / "tmp"
        for d in (self.bin, self.log, self.tmpdir):
            d.mkdir()
        for name, body in (("agent-secrets", AGENT_SECRETS), ("gcloud", TOOL), ("bq", TOOL), ("gsutil", TOOL)):
            path = self.bin / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return self

    def __exit__(self, *exc: object) -> None:
        self.tmp.cleanup()

    def run(self, *args: str, env_extra: dict[str, str] | None = None, reason: str | None = "INFRA-4748: the IAM policy of gs://theorem-build-io") -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmpdir),
            "TMPDIR": str(self.tmpdir),
            "LOG": str(self.log),
            "FAKE_TOKEN": TOKEN,
            "DOTFILES_DIR": str(DOTFILES),
        }
        if reason is not None:
            env["GCLOUD_READER_REASON"] = reason
        env.update(env_extra or {})
        return subprocess.run([str(self.script), *args], env=env, capture_output=True, text=True, timeout=30)

    def client_argv(self) -> list[str]:
        path = self.log / "agent-secrets.argv"
        return path.read_text().splitlines() if path.exists() else []

    def tool_log(self) -> dict[str, str]:
        path = self.log / "tool.log"
        if not path.exists():
            return {}
        return dict(line.split(" ", 1) if " " in line else (line, "") for line in path.read_text().splitlines())

    def leftovers(self) -> list[str]:
        return [p.name for p in self.tmpdir.iterdir() if p.name.startswith("gcloud-reader.")]


class GcloudReaderTest(unittest.TestCase):
    def test_asks_for_metadata_read_by_default_with_an_hour_and_the_reason(self) -> None:
        self.assertEqual(ttl_problems(SCRIPT), [])
        with Fixture(SCRIPT) as f:
            f.run("storage", "buckets", "list")
            argv = f.client_argv()
            self.assertEqual(argv[: argv.index("--")], ["METADATA_READ", "--ttl", "1h", "--reason", "INFRA-4748: the IAM policy of gs://theorem-build-io"])

    def test_as_legion_asks_for_legion_read(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("--as", "legion", "--", "gcloud", "organizations", "get-iam-policy", "123456789012")
            self.assertEqual(result.returncode, 0, result.stderr)
            argv = f.client_argv()
            self.assertEqual(argv[0], "LEGION_READ")
            self.assertNotIn("METADATA_READ", argv)

    def test_the_client_gets_broker_env_s_url_when_the_caller_has_none(self) -> None:
        """omp's eval kernel and a unit pass no AGENT_SECRETS_URL on, and the client refuses without one."""
        committed = (DOTFILES / "agent-secrets" / "broker.env").read_text().split("AGENT_SECRETS_URL=", 1)[1].split()[0]
        with Fixture(SCRIPT) as f:
            result = f.run("storage", "buckets", "list")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((f.log / "agent-secrets.url").read_text().strip(), committed)
        with Fixture(SCRIPT) as f:
            result = f.run("storage", "buckets", "list", env_extra={"AGENT_SECRETS_URL": "https://broker.example.test"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((f.log / "agent-secrets.url").read_text().strip(), "https://broker.example.test")

    def test_a_copy_without_the_ttl_fails_the_ttl_check(self) -> None:
        """The negative control of the --ttl case: the broker refuses a request with no lifetime."""
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "gcloud-reader"
            text = SCRIPT.read_text()
            stripped = re.sub(r" --ttl 1h", "", text)
            self.assertNotEqual(stripped, text, "the script holds no ' --ttl 1h' to remove")
            copy.write_text(stripped)
            copy.chmod(0o755)
            self.assertTrue(any("no --ttl 1h" in p for p in ttl_problems(copy)))

    def test_another_as_exits_2_before_any_call(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("--as", "other", "storage", "buckets", "list")
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("--as", result.stderr)
            self.assertEqual(f.client_argv(), [])
            self.assertEqual(f.tool_log(), {})

    def test_no_reason_exits_2_before_any_call(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("storage", "buckets", "list", reason=None)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("GCLOUD_READER_REASON", result.stderr)
            self.assertEqual(f.client_argv(), [])

    def test_help_and_no_command_ask_nothing(self) -> None:
        with Fixture(SCRIPT) as f:
            self.assertEqual(f.run("--help").returncode, 0)
            self.assertEqual(f.run().returncode, 2)
            self.assertEqual(f.run("--as", "legion").returncode, 2)
            self.assertEqual(f.client_argv(), [])

    def test_a_refused_release_stops_before_any_call(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("storage", "buckets", "list", env_extra={"AGENT_SECRETS_EXIT": "1"})
            self.assertEqual(result.returncode, 1)
            self.assertEqual(f.tool_log(), {})
            result = f.run("storage", "buckets", "list", env_extra={"AGENT_SECRETS_EXIT": "77"})
            self.assertEqual(result.returncode, 77)
            self.assertEqual(f.tool_log(), {})

    def test_the_token_reaches_the_command_in_a_file_and_in_no_argv(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("storage", "buckets", "get-iam-policy", "gs://theorem-build-io")
            self.assertEqual(result.returncode, 0, result.stderr)
            log = f.tool_log()
            self.assertEqual(log["token"], TOKEN, "the released token is written as it is, with no newline")
            self.assertEqual(log["token_file_mode"], "600")
            self.assertEqual(Path(log["bigqueryrc"]).parent, Path(log["token_file"]).parent, "the bigqueryrc is not in the removed directory")
            self.assertEqual(log["bigqueryrc_mode"], "600")
            self.assertTrue(log["config"].startswith(str(f.tmpdir) + "/gcloud-reader."), log["config"])
            self.assertEqual(log["prompts"], "1")
            self.assertEqual(log["impersonate"], "")
            self.assertEqual(log["env_metadata"], "", "the command still holds METADATA_READ")
            self.assertNotIn(TOKEN, log["argv"])
            self.assertNotIn(TOKEN, "\n".join(f.client_argv()))
            self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_a_first_word_that_is_no_command_runs_gcloud(self) -> None:
        with Fixture(SCRIPT) as f:
            f.run("storage", "buckets", "list")
            self.assertRegex(f.tool_log()["argv"], r"/gcloud storage buckets list$")
        with Fixture(SCRIPT) as f:
            f.run("--as", "legion", "--", "bq", "--project_id=theorem-agent-infra", "ls")
            self.assertRegex(f.tool_log()["argv"], r"/bq --project_id=theorem-agent-infra ls$")

    def test_gsutil_reads_no_boto_file_that_names_the_metadata_server(self) -> None:
        """gsutil cannot use the token file, and a Google VM's /etc/boto.cfg names the metadata
        server's account ([GoogleCompute] service_account = default), so `gcloud-reader gsutil ...`
        ran as the box (2026-11-20, a fake token still listed a bucket). The SDK's gsutil wrapper reads
        BOTO_CONFIG in place of /etc/boto.cfg and ~/.boto (bin/bootstrapping/gsutil.py); gsutil gets an
        empty one of gcloud-reader's directory, whatever the caller set, so it runs anonymous."""
        with Fixture(SCRIPT) as f:
            caller = f.tmpdir / "boto.cfg"
            caller.write_text("[GoogleCompute]\nservice_account = default\n")
            result = f.run("gsutil", "ls", "gs://theorem-build-io", env_extra={"BOTO_CONFIG": str(caller)})
            self.assertEqual(result.returncode, 0, result.stderr)
            log = f.tool_log()
            self.assertEqual(Path(log["boto"]).parent, Path(log["token_file"]).parent, log["boto"])
            self.assertEqual(log["boto_text"], "")

    def test_the_directory_is_gone_after_exit(self) -> None:
        with Fixture(SCRIPT) as f:
            f.run("storage", "buckets", "list")
            token_file = Path(f.tool_log()["token_file"])
            self.assertFalse(token_file.parent.exists(), token_file.parent)
            self.assertEqual(f.leftovers(), [])
        with Fixture(SCRIPT) as f:
            failing = f.bin / "gcloud"
            failing.write_text(TOOL + "exit 3\n")
            result = f.run("storage", "buckets", "list")
            self.assertEqual(result.returncode, 3)
            self.assertEqual(f.leftovers(), [])

    def test_the_command_runs_with_file_logging_off(self) -> None:
        """gcloud copies a command's output into a log file under CLOUDSDK_CONFIG/logs, so a printed
        token lands on disk (dispatch://INFRA-4824). The command runs with file logging off, whatever
        the caller set."""
        for caller in ({}, {"CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "false"}):
            with self.subTest(caller=caller), Fixture(SCRIPT) as f:
                result = f.run("auth", "print-access-token", env_extra=caller)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(f.tool_log()["file_logging"], "true")

    @unittest.skipUnless((SDK_BIN / "gcloud").is_file(), "no Google Cloud SDK at ~/.gcloud")
    def test_the_real_gcloud_writes_no_log_file(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("sh", "-c", PRINT_TOKEN_AND_LIST_LOGS,
                           env_extra={"PATH": f"{SDK_BIN}:{f.bin}:/usr/bin:/bin"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((f.log / "printed").read_text().strip(), TOKEN, "gcloud printed no token")
            self.assertEqual((f.log / "token-files").read_text(), "", "a log file holds the token")
            self.assertEqual((f.log / "log-files").read_text(), "", "gcloud wrote a log file")

    def run_real_bq(self, f: Fixture, *args: str, gcloud: str) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
        """Runs the SDK's real bq through gcloud-reader against a stand-in metadata server for the
        box and a stand-in BigQuery API (bq's --api), and returns the result, the Authorization
        header of each API request, and the metadata paths read. With gcloud="fails", the gcloud bq
        calls for its configuration exits 1 and prints nothing, as the gcloud shim's snap gcloud did
        when bq called it on a dev box (2026-11-20); with gcloud="sdk" it is the SDK's own."""
        metadata = MetadataStub.start()
        self.addCleanup(metadata.stop)
        api = BigQueryStub.start()
        self.addCleanup(api.stop)
        first = SDK_BIN
        if gcloud == "fails":
            first = f.bin.parent / "failing-gcloud"
            first.mkdir()
            (first / "gcloud").write_text("#!/bin/sh\nexit 1\n")
            (first / "gcloud").chmod(0o755)
        args = tuple(a.replace("API", f"http://{api.host}") for a in args)
        result = f.run(*args, env_extra={
            "PATH": f"{first}:{SDK_BIN}:{f.bin}:/usr/bin:/bin",
            "GCE_METADATA_HOST": metadata.host,
            "GCE_METADATA_ROOT": metadata.host,
            "GCE_METADATA_IP": metadata.host,
        })
        return result, list(api.authorizations), list(metadata.requests)

    @unittest.skipUnless((SDK_BIN / "bq").is_file(), "no Google Cloud SDK at ~/.gcloud")
    def test_the_real_bq_runs_as_the_token_and_not_as_the_metadata_server(self) -> None:
        """On a Google VM the SDK's bq wrapper takes the metadata server's account for the active
        one and hands bq --use_gce_service_account; bq used the token file only when its own call
        to gcloud for the configuration answered, so under the gcloud shim it ran as the box
        (2026-11-20: SESSION_USER() named the box's account under --as legion). Every request bq
        sends carries the released token, and bq asks the metadata server for no token: under either
        --as, from sh -c (which no first-word check sees), with the legacy auth library, and when
        bq's gcloud answers too."""
        bq = ("bq", "--api=API", "--project_id=example-project", "ls")
        cases = {
            "--as metadata": (bq, "fails"),
            "--as legion": (("--as", "legion", "--", *bq), "fails"),
            "sh -c": (("sh", "-c", 'exec "$@"', "sh", *bq), "fails"),
            "--nouse_google_auth": (("bq", "--nouse_google_auth", *bq[1:]), "fails"),
            "gcloud answers": (bq, "sdk"),
        }
        for name, (args, gcloud) in cases.items():
            with self.subTest(name), Fixture(SCRIPT) as f:
                result, authorizations, metadata_reads = self.run_real_bq(f, *args, gcloud=gcloud)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(authorizations, "bq sent the BigQuery API no request")
                self.assertNotIn(f"Bearer {STUB_BEARER}", authorizations, "bq ran as the metadata server's account")
                self.assertEqual(set(authorizations), {f"Bearer {TOKEN}"})
                self.assertEqual([p for p in metadata_reads if "/token" in p], [], "bq asked the metadata server for a token")

    @unittest.skipUnless((SDK_BIN / "bq").is_file(), "no Google Cloud SDK at ~/.gcloud")
    def test_the_real_bq_fails_rather_than_run_as_the_metadata_server(self) -> None:
        """A caller's own --bigqueryrc replaces the one that carries the token; with no gcloud to
        give bq a token either, bq fails, and asks neither the API nor the metadata server as the
        box."""
        with Fixture(SCRIPT) as f:
            empty_rc = f.tmpdir / "caller-bigqueryrc"
            empty_rc.write_text("")
            result, authorizations, metadata_reads = self.run_real_bq(
                f, "bq", f"--bigqueryrc={empty_rc}", "--api=API", "--project_id=example-project", "ls", gcloud="fails")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertNotIn(f"Bearer {STUB_BEARER}", authorizations, "bq ran as the metadata server's account")
            self.assertEqual([p for p in metadata_reads if "/token" in p], [], "bq asked the metadata server for a token")


if __name__ == "__main__":
    unittest.main()
