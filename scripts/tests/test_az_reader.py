#!/usr/bin/env python3
"""scripts/az-reader: one Azure read as the Azure identity legion-reader, through the broker.

az-reader asks secret-run for METADATA_READ (an access token of metadata-reader@theorem-agent-infra)
with --register and --ttl 1h, mints that account's identity token for api://AzureADTokenExchange
with iamcredentials' generateIdToken (the header on curl's stdin), signs in with `az login
--service-principal --federated-token @FILE` in a throwaway AZURE_CONFIG_DIR, and runs the one az
command there. Neither token reaches an argument list, and the directory is gone after exit.
"""

from __future__ import annotations

import json
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "scripts" / "az-reader"
ACCESS = "fake-metadata-reader-access-token"
IDTOKEN = "fake-metadata-reader-identity-token"
READER = "metadata-reader@theorem-agent-infra.iam.gserviceaccount.com"
CLIENT = "472b284a-6160-4195-8951-8362117f6c6b"
TENANT = "aaa6ad34-51af-41e7-891f-f16f5c53c396"

SECRET_RUN = r"""#!/bin/bash
printf '%s\n' "$@" >> "$LOG/secret-run.argv"
[[ -z "${SECRET_RUN_EXIT:-}" ]] || exit "$SECRET_RUN_EXIT"
key=""
while (( $# )) && [[ "$1" != -- ]]; do key="$1"; shift; done
shift
export "$key=$FAKE_ACCESS"
exec "$@"
"""

# The fake gh logs its argv and answers the contents read of agent-infra's .github/azure-client-ids
# with a legion-reader= line, or with nothing when GH_EMPTY is set.
GH = r"""#!/bin/bash
printf '%s\n' "$*" >> "$LOG/gh.argv"
[[ -z "${GH_EMPTY:-}" ]] || exit 0
printf 'implement=00000000-0000-0000-0000-000000000000\nlegion-reader=%s\n' "$FAKE_CLIENT"
"""

# The fake curl logs its argv and its stdin (the headers), and answers generateIdToken with
# {"token": ...} only when the bearer header carries the released access token.
CURL = r"""#!/bin/bash
printf 'argv %s\n' "$*" >> "$LOG/curl.log"
stdin=$(cat)
printf 'stdin %s\n' "${stdin//$'\n'/|}" >> "$LOG/curl.log"
[[ -z "${CURL_EXIT:-}" ]] || exit "$CURL_EXIT"
out=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do [[ ${args[i]} == -o ]] && out=${args[i+1]}; done
[[ $stdin == *"Authorization: Bearer $FAKE_ACCESS"* ]] || { echo "fake curl: 401" >&2; exit 22; }
body="{\"token\":\"$FAKE_IDTOKEN\"}"
if [[ -n $out ]]; then printf '%s' "$body" > "$out"; else printf '%s' "$body"; fi
"""

# The fake az logs its argv, AZURE_CONFIG_DIR, and the content of a --federated-token @FILE.
AZ = r"""#!/bin/bash
printf 'argv %s\n' "$*" >> "$LOG/az.log"
printf 'config %s\n' "${AZURE_CONFIG_DIR:-}" >> "$LOG/az.log"
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  if [[ ${args[i]} == --federated-token ]]; then
    v=${args[i+1]}
    printf 'federated %s\n' "$v" >> "$LOG/az.log"
    [[ $v == @* ]] && printf 'federated_file %s\n' "$(cat "${v#@}")" >> "$LOG/az.log"
  fi
done
[[ -z "${AZ_EXIT:-}" || $1 == login ]] || exit "$AZ_EXIT"
"""


class Fixture:
    def __init__(self, script: Path) -> None:
        self.script = script

    def __enter__(self) -> "Fixture":
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bin, self.log, self.tmpdir = root / "bin", root / "log", root / "tmp"
        for d in (self.bin, self.log, self.tmpdir):
            d.mkdir()
        for name, body in (("secret-run", SECRET_RUN), ("gh", GH), ("curl", CURL), ("az", AZ)):
            path = self.bin / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return self

    def __exit__(self, *exc: object) -> None:
        self.tmp.cleanup()

    def run(self, *args: str, env_extra: dict[str, str] | None = None, reason: str | None = "INFRA-4748: the VMs of resource group SAMI") -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmpdir),
            "TMPDIR": str(self.tmpdir),
            "LOG": str(self.log),
            "FAKE_ACCESS": ACCESS,
            "FAKE_IDTOKEN": IDTOKEN,
            "FAKE_CLIENT": CLIENT,
        }
        if reason is not None:
            env["AZ_READER_REASON"] = reason
        env.update(env_extra or {})
        return subprocess.run([str(self.script), *args], env=env, capture_output=True, text=True, timeout=30)

    def lines(self, name: str) -> list[str]:
        path = self.log / name
        return path.read_text().splitlines() if path.exists() else []

    def values(self, name: str, key: str) -> list[str]:
        return [line.split(" ", 1)[1] if " " in line else "" for line in self.lines(name) if line.split(" ", 1)[0] == key]

    def leftovers(self) -> list[str]:
        return [p.name for p in self.tmpdir.iterdir() if p.name.startswith("az-reader.")]


def ttl_problems(script: Path) -> list[str]:
    with Fixture(script) as f:
        result = f.run("vm", "list", "-g", "SAMI")
        argv = f.lines("secret-run.argv")
        problems = []
        if result.returncode != 0:
            problems.append(f"exit {result.returncode}: {result.stderr}")
        if "--ttl\n1h\n" not in "".join(a + "\n" for a in argv):
            problems.append(f"secret-run got no --ttl 1h: {argv}")
        if "--register" not in argv:
            problems.append(f"secret-run got no --register: {argv}")
        if "--" not in argv or argv[argv.index("--") - 1] != "METADATA_READ":
            problems.append(f"secret-run did not ask for METADATA_READ alone: {argv}")
        return problems


class AzReaderTest(unittest.TestCase):
    def test_asks_for_metadata_read_with_an_hour_and_register(self) -> None:
        self.assertEqual(ttl_problems(SCRIPT), [])

    def test_a_copy_without_the_ttl_fails_the_ttl_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "az-reader"
            text = SCRIPT.read_text()
            stripped = re.sub(r" --ttl 1h", "", text)
            self.assertNotEqual(stripped, text, "the script holds no ' --ttl 1h' to remove")
            copy.write_text(stripped)
            copy.chmod(0o755)
            self.assertTrue(any("no --ttl 1h" in p for p in ttl_problems(copy)))

    def test_mints_the_identity_token_of_metadata_reader_for_azure(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("vm", "list", "-g", "SAMI", "-o", "table")
            self.assertEqual(result.returncode, 0, result.stderr)
            argv = f.values("curl.log", "argv")
            self.assertEqual(len(argv), 1, argv)
            self.assertIn(f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{READER}:generateIdToken", argv[0])
            body = re.search(r"--data (\{.*\})", argv[0])
            self.assertIsNotNone(body, argv[0])
            assert body is not None
            self.assertEqual(json.loads(body.group(1)), {"audience": "api://AzureADTokenExchange", "includeEmail": True})
            self.assertIn(f"Authorization: Bearer {ACCESS}", f.values("curl.log", "stdin")[0])

    def test_signs_in_as_legion_reader_with_the_token_in_a_file_then_runs_the_command(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("--", "vm", "list", "-g", "SAMI", "-o", "table")
            self.assertEqual(result.returncode, 0, result.stderr)
            argv = f.values("az.log", "argv")
            self.assertEqual(len(argv), 2, argv)
            self.assertTrue(argv[0].startswith(f"login --service-principal --username {CLIENT} --tenant {TENANT} --federated-token @"), argv[0])
            self.assertEqual(argv[1], "vm list -g SAMI -o table")
            self.assertEqual(f.values("az.log", "federated_file"), [IDTOKEN])
            configs = f.values("az.log", "config")
            self.assertEqual(len(set(configs)), 1, configs)
            self.assertTrue(configs[0].startswith(str(f.tmpdir) + "/az-reader."), configs)

    def test_no_token_reaches_an_argument_list(self) -> None:
        with Fixture(SCRIPT) as f:
            f.run("vm", "list", "-g", "SAMI")
            argv = "\n".join(f.lines("secret-run.argv") + f.values("curl.log", "argv") + f.values("az.log", "argv"))
            self.assertNotIn(ACCESS, argv)
            self.assertNotIn(IDTOKEN, argv)

    def test_the_directory_is_gone_after_exit(self) -> None:
        with Fixture(SCRIPT) as f:
            f.run("vm", "list", "-g", "SAMI")
            self.assertEqual(f.leftovers(), [])
        with Fixture(SCRIPT) as f:
            result = f.run("vm", "start", "-g", "SAMI", "-n", "x", env_extra={"AZ_EXIT": "3"})
            self.assertEqual(result.returncode, 3)
            self.assertEqual(f.leftovers(), [])

    def test_a_refused_release_stops_before_any_call(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("vm", "list", env_extra={"SECRET_RUN_EXIT": "77"})
            self.assertEqual(result.returncode, 77)
            self.assertEqual(f.lines("curl.log"), [])
            self.assertEqual(f.lines("az.log"), [])

    def test_a_refused_mint_stops_before_az(self) -> None:
        with Fixture(SCRIPT) as f:
            result = f.run("vm", "list", env_extra={"CURL_EXIT": "22"})
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(f.lines("az.log"), [])
            self.assertEqual(f.leftovers(), [])

    def test_reads_the_client_id_from_agent_infra_before_the_broker(self) -> None:
        with Fixture(SCRIPT) as f:
            f.run("vm", "list", "-g", "SAMI")
            self.assertEqual(f.lines("gh.argv"), ["api repos/theorem-labs/agent-infra/contents/.github/azure-client-ids?ref=main -H Accept: application/vnd.github.raw"])
        with Fixture(SCRIPT) as f:
            result = f.run("vm", "list", env_extra={"GH_EMPTY": "1"})
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("legion-reader=", result.stderr)
            self.assertEqual(f.lines("secret-run.argv"), [])
            self.assertEqual(f.lines("az.log"), [])

    def test_usage_errors_exit_2_before_any_call(self) -> None:
        with Fixture(SCRIPT) as f:
            self.assertEqual(f.run().returncode, 2)
            self.assertEqual(f.run("vm", "list", reason=None).returncode, 2)
            self.assertEqual(f.run("--help").returncode, 0)
            self.assertEqual(f.lines("secret-run.argv"), [])
            self.assertEqual(f.lines("gh.argv"), [])


if __name__ == "__main__":
    unittest.main()
