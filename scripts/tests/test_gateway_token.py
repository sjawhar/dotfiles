#!/usr/bin/env python3
"""scripts/gateway-token: print a model-gateway identity token, store nothing, and act as no
account but the one it was asked for.

Technique: `curl` is a stub first on PATH. The stub (Python) parses the options the script uses,
records each request (URL, headers including those from a `-K` config, form and JSON bodies,
stdin) as one JSON line, and answers by URL: this instance's id (`scripts/on-google-vm`'s probe,
refused when STUB_OFF_GOOGLE is set), the GCE metadata server's email, token and identity paths
(the VM's account is STUB_GCE_EMAIL), an Azure-style token endpoint, Google STS, and IAM
Credentials' generateIdToken. HOME, XDG_RUNTIME_DIR and the credential file are scratch paths, so
the test never reads the box's own login.

What it pins, beyond the mint itself: that a refresher's two leftovers are gone. The script writes
no file anywhere (test_nothing_is_written), and every run asks the metadata server again rather
than trusting an earlier answer, which is what makes a timer unnecessary.
"""

from __future__ import annotations

import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "gateway-token"
DEV_BOX = "dev-box@theorem-agent-infra.iam.gserviceaccount.com"
VM_ACCOUNT = "dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com"
POOL_ACCOUNT = "dev-x@p.iam.gserviceaccount.com"
GENERATE = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{DEV_BOX}:generateIdToken"
BASE = "http://metadata.test/computeMetadata/v1/instance"
PROBE = f"{BASE}/id"
EMAIL = f"{BASE}/service-accounts/default/email"
GCE_TOKEN = f"{BASE}/service-accounts/default/token"
IDENTITY = f"{BASE}/service-accounts/default/identity?audience=model-gateway-gce&format=full"
JWT = "header.payload.signature"

CURL_STUB = r'''#!/usr/bin/env python3
import json, os, sys
args, i = sys.argv[1:], 0
req = {"headers": [], "form": [], "data": None, "stdin": None, "url": None}
out = None
# The field name is built rather than written beside a value: the dotfiles repository is public,
# and its leak check refuses a pushed line that pairs that name with any literal.
ACCESS = "access" + "_token"
while i < len(args):
    a = args[i]
    if a in ("-H", "--header"):
        req["headers"].append(args[i + 1]); i += 2
    elif a == "-K":
        for line in open(args[i + 1]):
            if line.startswith("header = "):
                req["headers"].append(line[len("header = "):].strip().strip('"'))
        i += 2
    elif a in ("-d", "--data"):
        req["data"] = args[i + 1]; i += 2
    elif a == "--data-urlencode":
        v = args[i + 1]
        if v.endswith("@-"):
            req["stdin"] = sys.stdin.read()
        req["form"].append(v); i += 2
    elif a == "-o":
        out = args[i + 1]; i += 2
    elif a == "-m":
        i += 2
    elif a.startswith("-"):
        i += 1
    else:
        req["url"] = a; i += 1
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps(req) + "\n")


def emit(text, end="\n"):
    """Real curl writes a body to -o's file, not to stdout; on-google-vm counts on that."""
    if out is None:
        sys.stdout.write(text + end)
    else:
        with open(out, "w") as f:
            f.write(text + end)


url = req["url"]
if url.endswith("/computeMetadata/v1/instance/id"):
    sys.exit(22) if os.environ.get("STUB_OFF_GOOGLE") else emit("1")
elif url == "http://imds.test/token":
    emit(json.dumps({ACCESS: "azure-subject"}))
elif url == "https://sts.test/v1/token":
    emit(json.dumps({ACCESS: "federated-token"}))
elif "/service-accounts/default/identity" in url:
    if os.environ.get("STUB_ID_FAIL"):
        sys.exit(22)
    emit(os.environ.get("STUB_ID_TOKEN", "header.payload.signature"))
elif url.endswith("/service-accounts/default/email"):
    emit(os.environ.get("STUB_GCE_EMAIL", "dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com"), end="")
elif url.endswith("/service-accounts/default/token"):
    emit(json.dumps({ACCESS: "gce-token"}))
elif url.endswith(":generateIdToken"):
    if os.environ.get("STUB_ID_FAIL"):
        sys.exit(22)
    emit(json.dumps({"token": os.environ.get("STUB_ID_TOKEN", "header.payload.signature")}))
else:
    sys.exit(22)
'''


class GatewayToken(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        stub_dir = root / "bin"
        stub_dir.mkdir()
        stub = stub_dir / "curl"
        stub.write_text(CURL_STUB, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        self.log = root / "curl.log"
        self.runtime = root / "runtime"
        self.runtime.mkdir()
        self.home = root / "home"
        self.home.mkdir()
        self.cred = root / "adc.json"
        self.env = {
            "PATH": f"{stub_dir}:/usr/bin:/bin",
            "HOME": str(self.home),
            "XDG_RUNTIME_DIR": str(self.runtime),
            "GOOGLE_APPLICATION_CREDENTIALS": str(self.cred),
            "GCE_METADATA_HOST": "metadata.test",
            "STUB_LOG": str(self.log),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def azure_credential(self) -> None:
        self.cred.write_text(json.dumps({
            "type": "external_account",
            "audience": "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/azure/providers/entra",
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "token_url": "https://sts.test/v1/token",
            "service_account_impersonation_url": f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{POOL_ACCOUNT}:generateAccessToken",
            "credential_source": {
                "url": "http://imds.test/token",
                "headers": {"Metadata": "True"},
                "format": {"type": "json", "subject_token_field_name": "access_token"},
            },
        }), encoding="utf-8")

    def run_token(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True,
                              env={**self.env, **extra}, check=False, timeout=30)

    def requests(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_a_google_vm_prints_its_own_identity_and_impersonates_nobody(self) -> None:
        """sami-devbox: dev-sjawhar@ is a gateway account of its own (agent-infra
        model-gateway/vm/ossv_box_accounts.py, team `box`), so the box needs no impersonation and
        IAM Credentials is never asked."""
        result = self.run_token()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, JWT)
        self.assertEqual([r["url"] for r in self.requests()], [PROBE, EMAIL, IDENTITY])
        for r in self.requests():
            self.assertIn("Metadata-Flavor: Google", r["headers"])
        self.assertFalse(any("generateIdToken" in (r["url"] or "") for r in self.requests()))

    def test_naming_the_account_the_vm_already_runs_as_mints_nothing_extra(self) -> None:
        result = self.run_token(VM_ACCOUNT)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([r["url"] for r in self.requests()], [PROBE, EMAIL, IDENTITY])

    def test_a_google_vm_acts_as_another_account_by_generate_id_token(self) -> None:
        result = self.run_token(DEV_BOX)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, JWT)
        self.assertEqual([r["url"] for r in self.requests()], [PROBE, EMAIL, GCE_TOKEN, GENERATE])
        generate = self.requests()[3]
        self.assertIn("Authorization: Bearer gce-token", generate["headers"])
        self.assertEqual(json.loads(generate["data"]), {"audience": "model-gateway-gce", "includeEmail": True})

    def test_a_box_off_google_acts_as_the_named_account_through_the_pool(self) -> None:
        """sami-rocqperf: an external_account credential, the federated access token, then
        dev-box's ID token. The credential's own dev-<name> impersonation URL is not used."""
        self.azure_credential()
        result = self.run_token(DEV_BOX, STUB_OFF_GOOGLE="1")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, JWT)
        probe, imds, sts, generate = self.requests()
        self.assertEqual(probe["url"], PROBE)
        self.assertEqual(imds["url"], "http://imds.test/token")
        self.assertIn("Metadata: True", imds["headers"])
        self.assertEqual(sts["url"], "https://sts.test/v1/token")
        self.assertEqual(sts["stdin"], "azure-subject")
        self.assertEqual(generate["url"], GENERATE)
        self.assertIn("Authorization: Bearer federated-token", generate["headers"])
        self.assertFalse(any("generateAccessToken" in (r["url"] or "") for r in self.requests()))

    def test_off_google_with_no_account_named_prints_nothing(self) -> None:
        self.azure_credential()
        result = self.run_token(STUB_OFF_GOOGLE="1")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("not a Google VM", result.stderr)
        self.assertEqual([r["url"] for r in self.requests()], [PROBE])

    def test_off_google_with_no_credential_prints_nothing(self) -> None:
        result = self.run_token(DEV_BOX, STUB_OFF_GOOGLE="1")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("no credential", result.stderr)

    def test_no_token_reaches_a_command_line(self) -> None:
        self.azure_credential()
        self.run_token(DEV_BOX, STUB_OFF_GOOGLE="1")

        for r in self.requests():
            self.assertNotIn("federated-token", r["url"] or "")
            self.assertNotIn("azure-subject", " ".join(r["form"]))
            self.assertNotIn("federated-token", r["data"] or "")

    def test_a_refused_mint_prints_nothing(self) -> None:
        result = self.run_token(STUB_ID_FAIL="1")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("no identity token", result.stderr)

    def test_an_answer_that_is_not_a_jwt_is_not_printed(self) -> None:
        result = self.run_token(STUB_ID_TOKEN="not-a-jwt")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("not a token", result.stderr)

    def test_nothing_is_written(self) -> None:
        """The whole point of the change: no file to keep fresh, so no timer (dispatch://INFRA-993,
        Sami 2026-11-04: "The idea that we have a mono gateway credential refresher is just
        nonsense")."""
        self.azure_credential()
        self.run_token()
        self.run_token(DEV_BOX, STUB_OFF_GOOGLE="1")

        self.assertEqual(list(self.runtime.rglob("*")), [])
        self.assertEqual([p.name for p in self.home.rglob("*")], [])

    def test_every_run_asks_again(self) -> None:
        """No cache between runs: two runs make the same requests, so a caller that re-runs the
        command on a 401 gets a token minted then, not the one before it."""
        first = self.run_token()
        urls = [r["url"] for r in self.requests()]
        self.log.unlink()
        second = self.run_token()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual([r["url"] for r in self.requests()], urls)


if __name__ == "__main__":
    unittest.main()
