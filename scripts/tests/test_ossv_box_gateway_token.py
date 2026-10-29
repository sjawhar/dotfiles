#!/usr/bin/env python3
"""scripts/ossv-box-gateway-token: mint dev-box's ID token with generateIdToken, and nothing else.

Technique: `curl` and `logger` are stubs first on PATH. The curl stub (Python) parses the options
the script uses, records each request (URL, headers including those from a `-K` config, form and
JSON bodies, stdin) as one JSON line, and answers by URL: an Azure-style token endpoint, Google
STS, the GCE metadata server, and IAM Credentials' generateIdToken. HOME, XDG_RUNTIME_DIR and the
credential file are scratch paths, so the test never reads the box's own login or writes its token.
"""

from __future__ import annotations

import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ossv-box-gateway-token"
TARGET = "dev-box@theorem-agent-infra.iam.gserviceaccount.com"
GENERATE = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{TARGET}:generateIdToken"

CURL_STUB = r'''#!/usr/bin/env python3
import json, os, sys
args, i = sys.argv[1:], 0
req = {"headers": [], "form": [], "data": None, "stdin": None, "url": None}
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
    elif a == "-m":
        i += 2
    elif a.startswith("-"):
        i += 1
    else:
        req["url"] = a; i += 1
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps(req) + "\n")
url = req["url"]
if url == "http://imds.test/token":
    print(json.dumps({"access_token": "azure-subject"}))
elif url == "https://sts.test/v1/token":
    print(json.dumps({"access_token": "federated-token"}))
elif "/computeMetadata/v1/instance/service-accounts/default/token" in url:
    print(json.dumps({"access_token": "gce-token"}))
elif url.endswith(":generateIdToken"):
    if os.environ.get("STUB_ID_FAIL"):
        sys.exit(22)
    print(json.dumps({"token": os.environ.get("STUB_ID_TOKEN", "header.payload.signature")}))
else:
    sys.exit(22)
'''


class GatewayTokenWriter(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        stub_dir = root / "bin"
        stub_dir.mkdir()
        for name, body in (("curl", CURL_STUB), ("logger", "#!/bin/sh\nexit 0\n")):
            path = stub_dir / name
            path.write_text(body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        self.log = root / "curl.log"
        self.runtime = root / "runtime"
        self.token = self.runtime / "ossv-box" / "gateway-token"
        self.cred = root / "adc.json"
        self.env = {
            "PATH": f"{stub_dir}:/usr/bin:/bin",
            "HOME": str(root / "home"),
            "XDG_RUNTIME_DIR": str(self.runtime),
            "GOOGLE_APPLICATION_CREDENTIALS": str(self.cred),
            "OSSV_GCE_METADATA_HOST": "metadata.test",
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
            "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/dev-x@p.iam.gserviceaccount.com:generateAccessToken",
            "credential_source": {
                "url": "http://imds.test/token",
                "headers": {"Metadata": "True"},
                "format": {"type": "json", "subject_token_field_name": "access_token"},
            },
        }), encoding="utf-8")

    def run_writer(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(SCRIPT)], capture_output=True, text=True,
                              env={**self.env, **extra}, check=False, timeout=30)

    def requests(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_azure_box_mints_with_its_federated_token_and_never_impersonates(self) -> None:
        self.azure_credential()
        result = self.run_writer()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.token.read_text(encoding="utf-8"), "header.payload.signature")
        self.assertEqual(stat.S_IMODE(self.token.stat().st_mode), 0o600)
        imds, sts, generate = self.requests()
        self.assertEqual(imds["url"], "http://imds.test/token")
        self.assertIn("Metadata: True", imds["headers"])
        self.assertEqual(sts["url"], "https://sts.test/v1/token")
        self.assertEqual(sts["stdin"], "azure-subject")
        self.assertIn("audience=//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/azure/providers/entra", sts["form"])
        self.assertEqual(generate["url"], GENERATE)
        self.assertIn("Authorization: Bearer federated-token", generate["headers"])
        self.assertEqual(json.loads(generate["data"]), {"audience": "model-gateway-gce", "includeEmail": True})
        self.assertFalse(any("generateAccessToken" in (r["url"] or "") for r in self.requests()))

    def test_google_box_mints_with_its_metadata_token(self) -> None:
        result = self.run_writer()  # no credential file: a box in Google Cloud

        self.assertEqual(result.returncode, 0, result.stderr)
        metadata, generate = self.requests()
        self.assertEqual(metadata["url"], "http://metadata.test/computeMetadata/v1/instance/service-accounts/default/token")
        self.assertIn("Metadata-Flavor: Google", metadata["headers"])
        self.assertEqual(generate["url"], GENERATE)
        self.assertIn("Authorization: Bearer gce-token", generate["headers"])
        self.assertEqual(self.token.read_text(encoding="utf-8"), "header.payload.signature")

    def test_no_token_reaches_a_command_line(self) -> None:
        self.azure_credential()
        self.run_writer()
        for r in self.requests():
            self.assertNotIn("federated-token", r["url"] or "")
            self.assertNotIn("azure-subject", " ".join(r["form"]))

    def test_a_refused_mint_writes_no_token(self) -> None:
        result = self.run_writer(STUB_ID_FAIL="1")

        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.token.exists())
        self.assertIn("no identity token from generateIdToken", result.stderr)

    def test_output_that_is_not_a_jwt_writes_no_token(self) -> None:
        result = self.run_writer(STUB_ID_TOKEN="not-a-jwt")

        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.token.exists())


if __name__ == "__main__":
    unittest.main()
