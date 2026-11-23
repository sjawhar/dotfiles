#!/usr/bin/env python3
"""Tests for devbox/sami-devbox-check's legacy_is_keyless: which gcloud legacy_credentials
directories are a note rather than a FAIL in A2's login check.

A directory passes only when every credential in it is a keyless external_account: an adc.json of
type external_account with no private_key and no refresh_token, and a .boto that only names such a
file. A person's login (authorized_user, a .boto with a refresh token), a service account key
(service_account, with a private_key) and any other file fail (dispatch://INFRA-993). The function
is run from the real script, with a `sudo` on PATH that runs its command as the caller.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "devbox" / "sami-devbox-check"
FAKE_SUDO = """#!/bin/sh
[ "$1" = -n ] && shift
exec "$@"
"""
EXTERNAL = {
    "type": "external_account",
    "audience": "//iam.googleapis.com/projects/805240912354/locations/global/workloadIdentityPools/azure/providers/entra",
    "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
    "token_url": "https://sts.googleapis.com/v1/token",
    "credential_source": {"file": "/run/token", "format": {"type": "json", "subject_token_field_name": "access_token"}},
    "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/x:generateAccessToken",
}
EXTERNAL_BOTO = "[Credentials]\ngs_external_account_file = /home/sami/x/adc.json\n"


class LegacyIsKeylessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        (bin_dir / "sudo").write_text(FAKE_SUDO)
        (bin_dir / "sudo").chmod(0o755)
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")

    def legacy(self, files: dict[str, str]) -> Path:
        d = self.root / f"case{len(list(self.root.iterdir()))}" / "legacy_credentials"
        acct = d / "dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com"
        acct.mkdir(parents=True)
        for name, text in files.items():
            (acct / name).write_text(text)
        return d

    def keyless(self, d: Path) -> bool:
        # From the temp directory, not the checkout: jq is a mise shim on a devbox, and mise refuses
        # to run in a dotfiles clone whose mise.toml is not trusted (any clone but ~/.dotfiles), so
        # jq failed there, the type read as empty and every case read as "not keyless" (sami-devbox,
        # 2026-11-03). The failing cases then passed for the wrong reason.
        body = f'source <(sed \'/^case "${{1:-}}" in/,$d\' {SCRIPT}); legacy_is_keyless "$1"'
        return subprocess.run(["bash", "-c", body, "test", str(d)], env=self.env, cwd=self.root,
                              timeout=30).returncode == 0

    def test_jq_runs_where_the_check_runs(self) -> None:
        # The failing cases below prove nothing unless jq answers here.
        out = subprocess.run(["jq", "-r", ".type"], input='{"type": "t"}', env=self.env, cwd=self.root,
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.strip(), "t", out.stderr)

    def test_keyless_external_account_passes(self) -> None:
        self.assertTrue(self.keyless(self.legacy({"adc.json": json.dumps(EXTERNAL)})))
        self.assertTrue(self.keyless(self.legacy({"adc.json": json.dumps(EXTERNAL), ".boto": EXTERNAL_BOTO})))

    def test_service_account_key_fails(self) -> None:
        key = {"type": "service_account", "client_email": "x@p.iam.gserviceaccount.com", "private_key": "-----BEGIN PRIVATE KEY-----\n"}
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(key)})))
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps({"type": "service_account"})})))
        boto = "[Credentials]\ngs_service_key_file = /x/key.json\n"
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(EXTERNAL), ".boto": boto})))

    def test_person_login_fails(self) -> None:
        user = {"type": "authorized_user", "client_id": "c", "client_secret": "s", "refresh_token": "1//x"}
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(user)})))
        boto = "[Credentials]\ngs_oauth2_refresh_token = 1//x\n"
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(EXTERNAL), ".boto": boto})))

    def test_external_account_with_a_key_or_token_fails(self) -> None:
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(dict(EXTERNAL, private_key="k"))})))
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(dict(EXTERNAL, refresh_token="t"))})))

    def test_any_other_file_fails(self) -> None:
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(EXTERNAL), "key.p12": "x"})))
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(EXTERNAL), ".boto": EXTERNAL_BOTO + "gs_json_host = x\n"})))


class HostLoginsTest(unittest.TestCase):
    """host_logins judges each login-named file by what it holds (LOGIN_JUDGE_PY): a refresh token, a key, a GitHub
    token or a gcloud authorized_user row fails; a keyless external_account and a file with no credential (what Go
    tests leave behind) are notes; a file that cannot be read fails. The walk is the real login_walks over one seeded
    directory, with `sudo` running its command as the caller."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        (bin_dir / "sudo").write_text(FAKE_SUDO)
        (bin_dir / "sudo").chmod(0o755)
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
        self.tree = self.root / "disk"
        self.tree.mkdir()

    def put(self, rel: str, text: str) -> Path:
        p = self.tree / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def db(self, rel: str, rows: list[tuple[str, dict]] | None) -> None:
        import sqlite3
        p = self.tree / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(p)
        if rows is not None:
            con.execute('CREATE TABLE "credentials" (account_id TEXT PRIMARY KEY, value BLOB)')
            con.executemany("INSERT INTO credentials VALUES (?, ?)", [(a, json.dumps(v)) for a, v in rows])
        con.commit()
        con.close()

    def lines(self) -> list[str]:
        body = (f'source <(sed \'/^case "${{1:-}}" in/,$d\' {SCRIPT}); LOGIN_MOUNTS=("$1"); '
                'host_omp_logins() { :; }; host_logins')
        out = subprocess.run(["bash", "-c", body, "test", str(self.tree)], env=self.env, cwd=self.root,
                             capture_output=True, text=True, timeout=60)
        return [line for line in out.stdout.splitlines() if " logins: " in line]

    def verdict(self) -> list[str]:
        return [line.split()[0] for line in self.lines() if line.startswith(("ok", "FAIL"))]

    def seed_harmless(self) -> None:
        # What a box holds and must pass: a Go test's leftover, a moved session's keyless Azure identity, an empty
        # gcloud store, a gh test fixture, and a person's login inside a pruned tree.
        self.put("gotmp/TestCreateSignalHelper1/001/.config/gcloud/application_default_credentials.json",
                 '{"type": "authorized_user"}')
        self.put("tmp/moved/gcloud/box_credential_config.json", json.dumps(EXTERNAL))
        self.db("tmp/moved/gcloud/credentials.db", None)
        self.db("tmp/other/gcloud/credentials.db", [("dev-box@x", EXTERNAL)])
        self.put("probe/home/.config/gh/hosts.yml", "github.com:\n    oauth_token: PROBE-not-a-token\n")
        self.put("src/node_modules/pkg/application_default_credentials.json", '{"refresh_token": "1//x"}')

    def test_files_with_no_login_pass_and_are_notes(self) -> None:
        self.seed_harmless()
        lines = self.lines()
        self.assertEqual(self.verdict(), ["ok", "ok"], lines)
        self.assertTrue(any(line.startswith("note logins: walks") and str(self.tree) in line for line in lines), lines)
        self.assertTrue(any(line.startswith("note logins: keyless external_account credentials (no person's login, no "
                                            "key): 1,") for line in lines), lines)
        self.assertTrue(any(line.startswith("note logins: login-named files holding no credential: 4,") for line in lines),
                        lines)

    def test_each_person_login_fails_the_line(self) -> None:
        for name, seed in (
                ("refresh token", lambda: self.put("tmp/a/gcloud/application_default_credentials.json",
                                                   '{"type": "authorized_user", "refresh_token": "1//0abc"}')),
                ("key", lambda: self.put("tmp/a/box_credential_config.json", '{"type": "service_account", "private_key": "k"}')),
                ("gh token", lambda: self.put("tmp/a/.config/gh/hosts.yml", "github.com:\n    oauth_token: gho_abc\n")),
                ("gcloud user row", lambda: self.db("tmp/a/gcloud/credentials.db",
                                                    [("sami@theorem.dev", {"type": "authorized_user", "refresh_token": "1//0"})])),
                ("credentialed_accounts.db", lambda: self.put("tmp/a/credentialed_accounts.db", "")),
                ("unreadable", lambda: (self.tree / "tmp/a/application_default_credentials.json").mkdir(parents=True))):
            with self.subTest(name=name):
                for child in self.tree.iterdir():
                    subprocess.run(["rm", "-rf", str(child)], check=True)
                self.seed_harmless()
                seed()
                lines = self.lines()
                self.assertIn("FAIL", self.verdict(), lines)
                self.assertNotIn("ok   logins: no application_default", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
