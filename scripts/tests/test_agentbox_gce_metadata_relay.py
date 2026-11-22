#!/usr/bin/env python3
"""scripts/agentbox-gce-metadata-relay serves the account ~/.config/agent-identity/account names.

While that file names an account other than the VM's own, the relay answers the service-account paths
itself, as that account, with tokens it mints through IAM Credentials on the VM's own token; every
other path, and every path when the file is absent or names the VM's own account, goes to the real
metadata server unchanged.

Technique: the real relay, run as a process on loopback, between gce_metadata_stub.py (the real
metadata server, attached account REAL) and iam_credentials_stub.py (IAM Credentials), with a
throwaway HOME for the account file. The two upstreams are named by the relay's own overrides
(AGENTBOX_GCE_METADATA_UPSTREAM, AGENTBOX_IAM_CREDENTIALS_URL), never GCE_METADATA_HOST, which on the
host names the relay itself.
"""

from __future__ import annotations

import base64
import datetime
import http.client
import http.server
import concurrent.futures
import threading
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path

from gce_metadata_stub import PROJECT, SCOPES, STUB_BEARER, MetadataStub
from gce_metadata_stub import id_token as metadata_id_token
from iam_credentials_stub import IamCredentialsStub

DOTFILES = Path(__file__).resolve().parents[2]
RELAY = DOTFILES / "scripts" / "agentbox-gce-metadata-relay"
REAL = "dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com"
TARGET = "dev-box@theorem-agent-infra.iam.gserviceaccount.com"
OTHER = "ossv-box@theorem-agent-infra.iam.gserviceaccount.com"
SQL_SCOPE = "https://www.googleapis.com/auth/sqlservice.login"
ACCOUNTS = "/computeMetadata/v1/instance/service-accounts/"
FLAVOR = {"Metadata-Flavor": "Google"}
# A box network the test's loopback client is not in, so every request here is admitted as loopback.
SUBNET = "172.31.0.0/24"
# The token a cut-short IAM or metadata answer starts with: a stand-in, never a credential.
PARTIAL_TOKEN = "stub-private-partial-token"


def free_port(bind: str = "127.0.0.1") -> int:
    with socket.socket() as s:
        s.bind((bind, 0))
        return s.getsockname()[1]


def outward_address() -> str | None:
    """This machine's own address on its default route, or None where it has none."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))
        except OSError:
            return None
        address = s.getsockname()[0]
    return None if address.startswith("127.") else address


class Relay:
    def __init__(self, home: Path, upstream: str, iam_url: str, bind: str = "127.0.0.1", subnet: str = SUBNET) -> None:
        self.bind = bind
        env = {**os.environ, "HOME": str(home), "AGENTBOX_GCE_METADATA_UPSTREAM": upstream,
               "AGENTBOX_IAM_CREDENTIALS_URL": iam_url}
        # On the host, after the switch, every user unit gets GCE_METADATA_HOST naming the relay: the
        # relay must not read it. Set it to a dead address so a relay that does fails here.
        env.update(GCE_METADATA_HOST="127.0.0.1:9", GCE_METADATA_ROOT="127.0.0.1:9", GCE_METADATA_IP="127.0.0.1:9")
        # Another process on this shared machine can take the port between free_port and the relay's
        # bind: the relay then exits, and a bare connect would reach that other server. So the relay
        # is ready only once a request through it gets the metadata server's own header, and a relay
        # that exited is started again on a new port.
        exits: list[str] = []
        for _ in range(5):
            self.port = free_port(bind)
            self.proc = subprocess.Popen([sys.executable, str(RELAY), bind, str(self.port), subnet], env=env,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if self.ready():
                return
            exits.append(self.proc.communicate()[1])
        raise RuntimeError(f"relay exited each time: {exits}")

    def ready(self) -> bool:
        """True once the relay answers through to the metadata server; False if it exited first."""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return False
            try:
                if self.get("/")[1].get("metadata-flavor") == "Google":
                    return True
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.05)
        raise RuntimeError("relay did not listen")

    def stop(self) -> str:
        self.proc.terminate()
        try:
            _, err = self.proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            _, err = self.proc.communicate()
        return err

    def get(self, path: str, headers: dict[str, str] | None = None, host: str | None = None) -> tuple[int, dict[str, str], str]:
        conn = http.client.HTTPConnection(host or self.bind, self.port, timeout=10)
        try:
            conn.request("GET", path, headers=FLAVOR if headers is None else headers)
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read().decode()
        finally:
            conn.close()


class RelayCase(unittest.TestCase):
    vm_account = REAL
    account_file: str | None = TARGET

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="relay-home-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.home)], check=False))
        if self.account_file is not None:
            self.write_account(self.account_file)
        self.metadata = MetadataStub.start(self.vm_account)
        self.addCleanup(self.metadata.stop)
        self.iam = IamCredentialsStub.start()
        self.addCleanup(self.iam.stop)
        self.relay = self.start_relay()

    def start_relay(self, **kwargs: str) -> Relay:
        relay = Relay(self.home, self.metadata.host, self.iam.url, **kwargs)
        self.addCleanup(relay.stop)
        return relay

    def write_account(self, account: str) -> None:
        path = self.home / ".config" / "agent-identity" / "account"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(account + "\n", encoding="utf-8")

    def token_requests_upstream(self) -> list[str]:
        return [p for p in self.metadata.requests if "/token" in p]

    def access_token(self, query: str = "") -> str:
        status, _, body = self.relay.get(ACCOUNTS + "default/token" + query)
        self.assertEqual(status, 200, body)
        return str(json.loads(body)["access_token"])

    def id_claims(self, audience: str) -> dict[str, object]:
        status, _, body = self.relay.get(ACCOUNTS + "default/identity?" + urllib.parse.urlencode({"audience": audience}))
        self.assertEqual(status, 200, body)
        return payload(body)


class TheRelayServesTheTarget(RelayCase):
    def test_service_accounts_lists_default_and_the_target(self) -> None:
        status, headers, body = self.relay.get(ACCOUNTS)
        self.assertEqual((status, body), (200, f"default/\n{TARGET}/\n"))
        self.assertEqual(headers.get("metadata-flavor"), "Google")
        status, _, body = self.relay.get(ACCOUNTS + "?recursive=true")
        self.assertEqual(status, 200)
        self.assertEqual({who: info["email"] for who, info in json.loads(body).items()}, {"default": TARGET, TARGET: TARGET})

    def test_the_default_and_the_target_directories_list_the_five_leaves(self) -> None:
        for who in ("default", TARGET):
            with self.subTest(who=who):
                status, _, body = self.relay.get(f"{ACCOUNTS}{who}/")
                self.assertEqual((status, body.split()), (200, ["aliases", "email", "identity", "scopes", "token"]))
                status, _, body = self.relay.get(f"{ACCOUNTS}{who}/?recursive=true")
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), {"aliases": ["default"], "email": TARGET, "scopes": SCOPES})
                status, _, body = self.relay.get(f"{ACCOUNTS}{who}/aliases")
                self.assertEqual((status, body.strip()), (200, "default"))

    def test_email_is_the_target(self) -> None:
        for who in ("default", TARGET):
            with self.subTest(who=who):
                self.assertEqual(self.relay.get(f"{ACCOUNTS}{who}/email")[::2], (200, TARGET))

    def test_scopes_are_cloud_platform_and_userinfo_email(self) -> None:
        status, _, body = self.relay.get(ACCOUNTS + "default/scopes")
        self.assertEqual((status, body.split()), (200, ["https://www.googleapis.com/auth/cloud-platform",
                                                        "https://www.googleapis.com/auth/userinfo.email"]))

    def test_token_is_minted_for_the_target_on_the_vms_own_token(self) -> None:
        status, headers, body = self.relay.get(ACCOUNTS + "default/token")
        self.assertEqual(status, 200, body)
        self.assertEqual(headers.get("content-type"), "application/json")
        token = json.loads(body)
        self.assertEqual(token["access_token"], "stub-minted-access-0")
        self.assertEqual(token["token_type"], "Bearer")
        self.assertTrue(3500 <= token["expires_in"] <= 3600, token)
        [(method, account, auth, sent)] = self.iam.calls
        self.assertEqual((method, account, auth), ("generateAccessToken", TARGET, f"Bearer {STUB_BEARER}"))
        self.assertEqual(sent, {"scope": SCOPES, "lifetime": "3600s"})

    def test_token_honours_the_scopes_a_client_asks_for(self) -> None:
        scope = "https://www.googleapis.com/auth/sqlservice.login"
        status, _, body = self.relay.get(ACCOUNTS + f"{TARGET}/token?scopes={urllib.parse.quote(scope)}")
        self.assertEqual(status, 200, body)
        self.assertEqual(self.iam.calls[-1][3]["scope"], [scope])

    def test_identity_is_minted_with_the_email_for_the_audience_asked(self) -> None:
        audience = "model-gateway-gce"
        before = int(time.time())
        status, _, body = self.relay.get(ACCOUNTS + f"default/identity?audience={audience}&format=full&licenses=FALSE")
        after = int(time.time())
        self.assertEqual(status, 200, body)
        [(method, account, auth, sent)] = self.iam.calls
        self.assertEqual((method, account, auth), ("generateIdToken", TARGET, f"Bearer {STUB_BEARER}"))
        self.assertEqual(sent, {"audience": audience, "includeEmail": True})
        claims = payload(body)
        self.assertEqual({k: v for k, v in claims.items() if k != "exp"}, {"aud": audience, "email": TARGET})
        # The stub mints an ID token for an hour, as IAM Credentials does.
        self.assertTrue(before + 3600 <= expiry(claims) <= after + 3600, claims)
        status, _, _ = self.relay.get(ACCOUNTS + "default/identity")
        self.assertEqual(status, 400)

    def test_a_token_inside_its_lifetime_is_not_minted_again(self) -> None:
        first = json.loads(self.relay.get(ACCOUNTS + "default/token")[2])
        second = json.loads(self.relay.get(ACCOUNTS + f"{TARGET}/token")[2])
        self.assertEqual(first["access_token"], second["access_token"])
        self.assertLessEqual(second["expires_in"], first["expires_in"])
        for _ in range(2):
            self.relay.get(ACCOUNTS + "default/identity?audience=a")
        self.assertEqual((self.iam.count("generateAccessToken"), self.iam.count("generateIdToken")), (1, 1))

    def test_a_token_within_five_minutes_of_its_expiry_is_minted_again(self) -> None:
        self.iam.access_lifetime = 200
        first = json.loads(self.relay.get(ACCOUNTS + "default/token")[2])
        second = json.loads(self.relay.get(ACCOUNTS + "default/token")[2])
        self.assertNotEqual(first["access_token"], second["access_token"])
        self.assertEqual(self.iam.count("generateAccessToken"), 2)

    def test_an_id_token_within_five_minutes_of_its_exp_is_minted_again(self) -> None:
        self.iam.id_lifetime = 200
        self.id_claims("a")
        self.iam.id_lifetime = 3600
        before = int(time.time())
        claims = self.id_claims("a")
        after = int(time.time())
        self.assertTrue(before + 3600 <= expiry(claims) <= after + 3600, claims)
        self.assertEqual(self.iam.count("generateIdToken"), 2)

    def test_an_id_token_outside_that_margin_is_kept_until_its_own_exp(self) -> None:
        self.iam.id_lifetime = 400
        before = int(time.time())
        first = self.id_claims("a")
        after = int(time.time())
        self.iam.id_lifetime = 3600
        self.assertEqual(self.id_claims("a"), first)
        self.assertTrue(before + 400 <= expiry(first) <= after + 400, first)
        self.assertEqual(self.iam.count("generateIdToken"), 1)

    def test_tokens_are_kept_apart_by_scopes(self) -> None:
        sql = "?scopes=" + urllib.parse.quote(SQL_SCOPE)
        first = [self.access_token(), self.access_token(sql)]
        again = [self.access_token(), self.access_token(sql)]
        self.assertNotEqual(first[0], first[1])
        self.assertEqual(again, first)
        self.assertEqual([call[3]["scope"] for call in self.iam.calls], [SCOPES, [SQL_SCOPE]])

    def test_id_tokens_are_kept_apart_by_audience(self) -> None:
        self.assertEqual([self.id_claims(audience)["aud"] for audience in ("a", "b", "a", "b")], ["a", "b", "a", "b"])
        self.assertEqual([call[3]["audience"] for call in self.iam.calls], ["a", "b"])

    def test_tokens_are_kept_apart_by_target(self) -> None:
        mine = (self.access_token(), self.id_claims("a"))
        self.write_account(OTHER)
        self.assertEqual(self.relay.get(ACCOUNTS + "default/email")[::2], (200, OTHER))
        other = (self.access_token(), self.id_claims("a"))
        self.assertNotEqual(other[0], mine[0])
        self.assertEqual(other[1]["email"], OTHER)
        self.write_account(TARGET)
        self.assertEqual((self.access_token(), self.id_claims("a")), mine)
        self.assertEqual([call[:2] for call in self.iam.calls], [("generateAccessToken", TARGET), ("generateIdToken", TARGET),
                                                                 ("generateAccessToken", OTHER), ("generateIdToken", OTHER)])

    def test_removing_the_switch_serves_the_vms_own_tokens_and_mints_nothing(self) -> None:
        self.access_token()
        self.id_claims("a")
        (self.home / ".config" / "agent-identity" / "account").unlink()
        self.assertEqual(self.relay.get(ACCOUNTS + "default/email")[::2], (200, REAL))
        self.assertEqual(self.access_token(), STUB_BEARER)
        self.assertEqual(self.relay.get(ACCOUNTS + "default/identity?audience=a")[::2], (200, metadata_id_token("a")))
        self.assertEqual(len(self.iam.calls), 2)

    def test_expires_in_counts_from_when_the_mint_returned(self) -> None:
        """A slow mint does not stretch the lifetime a client is told: expires_in is the expiry less the time the
        relay answers, not less the time it started minting."""
        answer = self.iam.answer
        answered: list[tuple[float, str]] = []

        def slow(method: str, account: str, body: dict[str, object]) -> tuple[int, dict[str, object]]:
            status, reply = answer(method, account, body)
            time.sleep(2)
            answered.append((time.time(), str(reply["expireTime"])))
            return status, reply

        self.iam.answer = slow
        status, _, body = self.relay.get(ACCOUNTS + "default/token")
        self.assertEqual(status, 200, body)
        [(at, stamp)] = answered
        until = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
        self.assertLessEqual(json.loads(body)["expires_in"], until - at)

    def test_a_refused_mint_answers_503_with_the_refusal_and_no_token(self) -> None:
        self.iam.refuse = True
        for path in ("default/token", "default/identity?audience=a"):
            with self.subTest(path=path):
                status, _, body = self.relay.get(ACCOUNTS + path)
                self.assertEqual(status, 503)
                self.assertIn("PERMISSION_DENIED", body)
                self.assertIn(TARGET, body)
                self.assertNotIn(STUB_BEARER, body)
                self.assertNotIn("stub-minted", body)

    def test_a_token_with_no_readable_expiry_answers_503_and_no_token(self) -> None:
        self.iam.no_expiry = True
        for path in ("default/token", "default/identity?audience=a"):
            with self.subTest(path=path):
                status, _, body = self.relay.get(ACCOUNTS + path)
                self.assertEqual(status, 503, body)
                self.assertIn("no expiry", body)
                self.assertNotIn("stub-minted", body)

    def test_an_audience_is_not_written_to_the_journal(self) -> None:
        audience = "https://example.invalid/callback?access_token=stub-private-audience"
        status, _, _ = self.relay.get(ACCOUNTS + "default/identity?" + urllib.parse.urlencode({"audience": audience}))
        self.assertEqual(status, 200)
        journal = self.relay.stop()
        self.assertNotIn("stub-private-audience", journal)

    def test_a_refusal_does_not_echo_a_private_audience(self) -> None:
        audience = "https://example.invalid/callback?access_token=stub-private-audience"
        self.iam.answer = lambda method, account, body: (403, {"error": {"status": "PERMISSION_DENIED", "message": audience}})
        status, _, body = self.relay.get(ACCOUNTS + "default/identity?" + urllib.parse.urlencode({"audience": audience}))
        self.assertEqual(status, 503)
        self.assertIn("PERMISSION_DENIED", body)
        self.assertNotIn("stub-private-audience", body)
        self.assertNotIn("stub-private-audience", self.relay.stop())

    def test_a_slow_mint_does_not_block_an_already_cached_token(self) -> None:
        first = self.relay.get(ACCOUNTS + "default/token")
        started, release = threading.Event(), threading.Event()
        answer = self.iam.answer

        def slow(method: str, account: str, body: dict[str, object]) -> tuple[int, dict[str, object]]:
            started.set()
            release.wait(10)
            return answer(method, account, body)

        self.iam.answer = slow
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            mint = pool.submit(self.relay.get, ACCOUNTS + "default/identity?audience=slow")
            self.assertTrue(started.wait(5))
            cached = pool.submit(self.relay.get, ACCOUNTS + "default/token")
            try:
                status, _, body = cached.result(timeout=2)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["access_token"], json.loads(first[2])["access_token"])
            finally:
                release.set()
            self.assertEqual(mint.result()[0], 200)

    def test_simultaneous_requests_for_one_token_mint_it_once(self) -> None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _: self.relay.get(ACCOUNTS + "default/token"), range(8)))
        self.assertTrue(all(response[0] == 200 for response in responses))
        self.assertEqual(len({json.loads(response[2])["access_token"] for response in responses}), 1)
        self.assertEqual(self.iam.count("generateAccessToken"), 1)

    def test_the_vms_own_account_is_not_served_by_name(self) -> None:
        for leaf in ("token", "email", "identity?audience=a"):
            with self.subTest(leaf=leaf):
                self.assertEqual(self.relay.get(f"{ACCOUNTS}{REAL}/{leaf}")[0], 404)
        self.assertEqual(self.token_requests_upstream(), [])
        self.assertEqual(self.iam.calls, [])

    def test_an_invalid_switch_never_serves_the_vms_old_identity_or_logs_its_contents(self) -> None:
        self.write_account("stub-private-config-not-an-email")
        status, _, body = self.relay.get(ACCOUNTS + "default/token")
        self.assertEqual(status, 503)
        self.assertNotIn(STUB_BEARER, body)
        self.assertNotIn("stub-private-config", body)
        self.assertNotIn("stub-private-config", self.relay.stop())

    def test_a_non_utf8_switch_gives_a_controlled_failure(self) -> None:
        path = self.home / ".config/agent-identity/account"
        path.write_bytes(b"\xff")
        status, _, body = self.relay.get(ACCOUNTS + "default/token")
        self.assertEqual(status, 503)
        self.assertNotIn(STUB_BEARER, body)

    def test_a_request_without_the_metadata_flavor_header_is_refused(self) -> None:
        self.assertEqual(self.relay.get(ACCOUNTS + "default/token", headers={})[0], 403)
        self.assertEqual(self.iam.calls, [])

    def test_every_other_path_passes_through_unchanged(self) -> None:
        status, headers, body = self.relay.get("/computeMetadata/v1/project/project-id")
        self.assertEqual((status, body), (200, PROJECT))
        self.assertEqual(headers.get("metadata-flavor"), "Google")
        self.assertIn("/computeMetadata/v1/project/project-id", self.metadata.requests)

    def test_a_cut_short_upstream_answer_is_a_controlled_bad_gateway(self) -> None:
        class Truncated(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(b"stub-private-partial-body")

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.metadata.RequestHandlerClass = Truncated
        status, _, body = self.relay.get("/computeMetadata/v1/project/project-id")
        self.assertEqual(status, 502)
        self.assertNotIn("stub-private-partial-body", body)
        self.assertNotIn("Traceback", self.relay.stop())

    def test_cut_short_token_upstreams_refuse_without_credentials_and_recover(self) -> None:
        class Truncated(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(json.dumps({"accessToken": PARTIAL_TOKEN}).encode())

            do_POST = do_GET

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.assertEqual(self.relay.get(ACCOUNTS + "default/email")[0], 200)
        # A list, not a tuple: the element type stays the common base, so `original` fits either server.
        upstreams: list[http.server.ThreadingHTTPServer] = [self.iam, self.metadata]
        for upstream in upstreams:
            original = upstream.RequestHandlerClass
            upstream.RequestHandlerClass = Truncated
            try:
                status, _, body = self.relay.get(ACCOUNTS + "default/token")
                self.assertEqual(status, 503)
                self.assertNotIn(PARTIAL_TOKEN, body)
                self.assertNotIn(STUB_BEARER, body)
            finally:
                upstream.RequestHandlerClass = original
        self.assertEqual(self.relay.get(ACCOUNTS + "default/token")[0], 200)
        journal = self.relay.stop()
        self.assertNotIn("Traceback", journal)
        self.assertNotIn(PARTIAL_TOKEN, journal)

    def test_head_preserves_the_upstream_content_length_without_a_body(self) -> None:
        class Head(http.server.BaseHTTPRequestHandler):
            def do_HEAD(self) -> None:
                self.send_response(200)
                self.send_header("Content-Length", "37")
                self.send_header("X-Metadata-Test", "distinctive")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.metadata.RequestHandlerClass = Head
        conn = http.client.HTTPConnection(self.relay.bind, self.relay.port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("HEAD", "/computeMetadata/v1/project/project-id?probe=yes", headers=FLAVOR)
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Length"), "37")
        self.assertEqual(response.getheader("X-Metadata-Test"), "distinctive")
        self.assertEqual(response.read(), b"")

    def test_a_non_loopback_client_is_admitted_by_the_subnet_alone(self) -> None:
        """A box's client reaches the relay from its own address on the box network: inside SUBNET it is admitted,
        and the same client is refused (403) by a relay whose SUBNET leaves it out. Loopback is admitted by both."""
        address = outward_address()
        if address is None:
            self.skipTest("no non-loopback address on this machine")
        inside = self.start_relay(bind="0.0.0.0", subnet=f"{address}/32")
        outside = self.start_relay(bind="0.0.0.0")
        self.assertEqual(inside.get(ACCOUNTS + "default/email", host=address)[::2], (200, TARGET))
        self.assertEqual(outside.get(ACCOUNTS + "default/email", host=address)[0], 403)
        for relay in (inside, outside):
            self.assertEqual(relay.get(ACCOUNTS + "default/email", host="127.0.0.1")[::2], (200, TARGET))


class TheSwitchIsOffWithoutTheAccountFile(RelayCase):
    account_file = None

    def test_everything_passes_through(self) -> None:
        self.assertEqual(self.relay.get(ACCOUNTS + "default/email")[::2], (200, REAL))
        self.assertEqual(json.loads(self.relay.get(ACCOUNTS + "default/token")[2])["access_token"], STUB_BEARER)
        self.assertEqual(self.relay.get(f"{ACCOUNTS}{REAL}/email")[::2], (200, REAL))
        self.assertEqual(self.iam.calls, [])

    def test_writing_the_file_switches_a_running_relay_and_the_old_account_switches_it_back(self) -> None:
        self.write_account(TARGET)
        self.assertEqual(self.relay.get(ACCOUNTS + "default/email")[::2], (200, TARGET))
        self.write_account(REAL)
        self.assertEqual(self.relay.get(ACCOUNTS + "default/email")[::2], (200, REAL))
        self.assertEqual(json.loads(self.relay.get(ACCOUNTS + "default/token")[2])["access_token"], STUB_BEARER)
        self.assertEqual(self.iam.calls, [])


class TheVmAlreadyRunsAsTheTarget(RelayCase):
    vm_account = TARGET

    def test_everything_passes_through(self) -> None:
        self.assertEqual(self.relay.get(ACCOUNTS + "default/email")[::2], (200, TARGET))
        self.assertEqual(json.loads(self.relay.get(ACCOUNTS + "default/token")[2])["access_token"], STUB_BEARER)
        self.assertEqual(self.iam.calls, [])
        self.assertIn(ACCOUNTS + "default/token", self.metadata.requests)


def payload(jwt: str) -> dict[str, object]:
    part = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def expiry(claims: dict[str, object]) -> int:
    exp = claims["exp"]
    assert isinstance(exp, int), claims
    return exp


if __name__ == "__main__":
    unittest.main()
