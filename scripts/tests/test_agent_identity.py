#!/usr/bin/env python3
"""installers/agent-identity.sh switches the host's clients to the account the metadata relay serves.

The switch is off until ~/.config/agent-identity/account exists: with no file and no argument the
installer changes nothing, so devbox/install.sh runs it on every install. Given an account, it writes
that file, keeps the host's relay unit (devbox/agent-identity-relay.service) running, restarts a
running box relay (agentbox-imds) so it serves the account at once, and points the host's clients at
the host relay: environment.d and the user manager for the systemd user units, .bashrc (every login
shell reads it through ~/.profile, and bash reads it for a command sshd runs) through the same
environment.d file, and tmux's global environment. Then it re-runs installers/gcloud-box.sh against
the relay, so the configuration `box` names the account the relay serves.

Technique: the real installer and the real Google Cloud SDK in a throwaway HOME, with
gce_metadata_stub.py standing in for both the metadata server and the host relay (it serves the
target account, as the relay does after the switch). systemctl, tmux and sleep are stubs that log
each call. The login shell is a real `bash -l` whose ~/.profile and ~/.bashrc are Ubuntu's and
installers/shell.sh's, reading this checkout's .bashrc with a DOTFILES_DIR that holds only
agent-secrets/, as test_agent_secrets_launchers.py does. Skipped where the SDK is not installed, as
test_gcloud_box.py is.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Callable

from gce_metadata_stub import STUB_BEARER, MetadataStub

DOTFILES = Path(__file__).resolve().parents[2]
INSTALLER = DOTFILES / "installers" / "agent-identity.sh"
UNIT = DOTFILES / "devbox" / "agent-identity-relay.service"
SDK_BIN = Path(os.environ.get("HOME", "/nonexistent")) / ".gcloud" / "google-cloud-sdk" / "bin"
REAL = "dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com"
TARGET = "dev-box@theorem-agent-infra.iam.gserviceaccount.com"
GCE_VARS = ("GCE_METADATA_HOST", "GCE_METADATA_ROOT", "GCE_METADATA_IP")
# Ubuntu's ~/.profile reads ~/.bashrc, and installers/shell.sh makes ~/.bashrc read the dotfiles' one.
PROFILE = 'export KEEP=1\nif [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n'

SYSTEMCTL = r'''#!/bin/bash
printf 'systemctl %s\n' "$*" >>"$STUB_LOG"
case "$*" in
    "--user enable --now agent-identity-relay.service") [[ "${STUB_FAIL_START:-}" != yes ]] || exit 1 ;;
    # The host relay's state before the run: active and enabled unless a case says otherwise.
    "--user is-active --quiet agent-identity-relay.service") [[ "${STUB_RELAY_ACTIVE:-yes}" == yes ]] || exit 3 ;;
    "--user is-enabled --quiet agent-identity-relay.service") [[ "${STUB_RELAY_ENABLED:-yes}" == yes ]] || exit 1 ;;
    "--user is-active --quiet agentbox-imds") exit 0 ;;
    "--user is-active agent-identity-relay.service") echo active ;;
    "--user show-environment") cat "$STUB_ENV" 2>/dev/null ;;
    "--user set-environment "*) shift 2; printf '%s\n' "$@" >>"$STUB_ENV" ;;
esac
exit 0
'''
TMUX = r'''#!/bin/bash
printf 'tmux %s\n' "$*" >>"$STUB_LOG"
case "$1" in
    has-session) exit 0 ;;
    show-environment) grep -x "$3=.*" "$STUB_TMUX_ENV" 2>/dev/null || exit 1 ;;
    set-environment) printf '%s=%s\n' "$3" "$4" >>"$STUB_TMUX_ENV" ;;
esac
exit 0
'''


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@unittest.skipUnless((SDK_BIN / "gcloud").is_file(), "no Google Cloud SDK at ~/.gcloud")
@unittest.skipIf(Path("/.dockerenv").exists(), "the installer changes the host's units: run it on the host, not in a box")
class AgentIdentity(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agent-identity-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.home = self.root / "home"
        self.home.mkdir()
        # arm_user_service links units from $HOME/.dotfiles, the installed checkout.
        (self.home / ".dotfiles").symlink_to(DOTFILES)
        (self.home / ".profile").write_text(PROFILE, encoding="utf-8")
        minimal = self.root / "dotfiles"
        minimal.mkdir()
        (minimal / "agent-secrets").symlink_to(DOTFILES / "agent-secrets")
        (self.home / ".bashrc").write_text(f'DOTFILES_DIR={minimal}\n. {DOTFILES / ".bashrc"}\n', encoding="utf-8")
        self.config = self.home / ".config" / "gcloud"
        self.relay = MetadataStub.start(TARGET)
        self.addCleanup(self.relay.stop)
        stubs = self.root / "bin"
        write_stub(stubs, "systemctl", SYSTEMCTL)
        write_stub(stubs, "tmux", TMUX)
        write_stub(stubs, "sleep", "#!/bin/bash\nexit 0\n")
        # installers/gcloud-boxid.sh copies the dispatch context out of ~/.kube/config; STUB_KUBECTL_FAIL fails it.
        write_stub(stubs, "kubectl", "#!/bin/bash\n[[ -z \"${STUB_KUBECTL_FAIL:-}\" ]] || exit 1\necho 'apiVersion: v1'\n")
        (self.root / "nvme").mkdir()
        self.boxid = self.root / "nvme" / "boxid"
        self.log = self.root / "calls.log"
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{stubs}:{SDK_BIN}:/usr/bin:/bin",
            "DOTFILES_DIR": str(DOTFILES),
            "CLOUDSDK_CONFIG": str(self.config),
            "CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "1",
            # The real metadata server (the Google-VM test) and the host relay.
            "AGENTBOX_GCE_METADATA_UPSTREAM": self.relay.host,
            "AGENT_IDENTITY_RELAY": self.relay.host,
            "GCLOUD_BOX_IMDS_URL": "http://127.0.0.1:9",
            "GCLOUD_BOXID_DIR": str(self.boxid),
            "XDG_RUNTIME_DIR": str(self.root / "run"),
            "STUB_LOG": str(self.log),
            "STUB_ENV": str(self.root / "manager.env"),
            "STUB_TMUX_ENV": str(self.root / "tmux.env"),
        }

    def run_installer(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        self.log.write_text("", encoding="utf-8")
        return subprocess.run(["bash", str(INSTALLER), *args], env={**self.env, **extra}, capture_output=True,
                              text=True, timeout=600, check=False)

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines()

    def account_file(self) -> Path:
        return self.home / ".config" / "agent-identity" / "account"

    def env_d(self) -> Path:
        return self.home / ".config" / "environment.d" / "50-gce-metadata.conf"

    def login_shell(self, **caller: str) -> dict[str, str]:
        """The GCE_METADATA_* values a fresh login shell has, for a caller that set `caller`."""
        shell = subprocess.run(["bash", "-lc", "env"], env={"HOME": str(self.home), "PATH": "/usr/bin:/bin", **caller},
                               capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(shell.returncode, 0, shell.stderr)
        return dict(line.split("=", 1) for line in shell.stdout.splitlines() if line.startswith("GCE_METADATA_"))

    def box_account(self, config: Path | None = None) -> str:
        box = ((config or self.config) / "configurations" / "config_box").read_text(encoding="utf-8")
        return next(line.split("=", 1)[1].strip() for line in box.splitlines() if line.startswith("account"))

    def test_without_the_account_file_nothing_changes(self) -> None:
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        # devbox/install.sh runs this on every install: the usual state, no switch, prints no error.
        self.assertEqual(result.stderr, "")
        self.assertIn("nothing changed", result.stdout)
        self.assertFalse(self.account_file().exists())
        self.assertFalse(self.env_d().exists())
        self.assertEqual((self.home / ".profile").read_text(encoding="utf-8"), PROFILE)
        self.assertEqual(self.login_shell(), {})
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.config.exists())

    def token_caches(self) -> list[Path]:
        """The tokens cached as the old account outside gcloud's store: scripts/gcloud-token-fast's
        and gke-gcloud-auth-plugin's, each kept for up to an hour."""
        return [self.root / "run" / "gcloud-token" / "access-token", self.home / ".kube" / "gke_gcloud_auth_plugin_cache"]

    def cache_tokens(self) -> None:
        for cache in self.token_caches():
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text("old-account-token\n", encoding="utf-8")

    def test_the_switch_points_every_host_client_at_the_relay(self) -> None:
        self.cache_tokens()
        result = self.run_installer(TARGET)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.account_file().read_text(encoding="utf-8").strip(), TARGET)
        wanted = {name: self.relay.host for name in GCE_VARS}
        # 1. environment.d, for the systemd user units, and the running manager.
        self.assertEqual(dict(line.split("=", 1) for line in self.env_d().read_text(encoding="utf-8").splitlines()), wanted)
        self.assertIn("systemctl --user set-environment " + " ".join(f"{k}={v}" for k, v in wanted.items()), self.calls())
        # 2. A fresh login shell, through .bashrc: the relay; an agent box's own relay is kept.
        self.assertEqual(self.login_shell(), wanted)
        box = dict.fromkeys(GCE_VARS, "172.25.0.1:8169")
        self.assertEqual(self.login_shell(**box), box)
        self.assertEqual((self.home / ".profile").read_text(encoding="utf-8"), PROFILE)
        # 3. tmux's global environment.
        for name, value in wanted.items():
            self.assertIn(f"tmux set-environment -g {name} {value}", self.calls())
        # The relays: the host's is armed, a running box relay restarts to serve the new account.
        self.assertIn("systemctl --user enable --now agent-identity-relay.service", self.calls())
        self.assertIn("systemctl --user restart agentbox-imds", self.calls())
        # gcloud's configuration box names the account the relay serves, in ~/.config/gcloud and in
        # the boxid config dir that scripts from sami-rocqperf name.
        self.assertEqual(self.box_account(), TARGET)
        self.assertEqual(self.box_account(self.boxid / "gcloud-box"), TARGET)
        # No token cached as the old account outlives the switch.
        self.assertEqual([cache for cache in self.token_caches() if cache.exists()], [])

    def sdk_token(self, config: Path) -> str:
        """The token the real SDK prints through `config`, for a client with a fresh login shell's GCE_METADATA_*."""
        env = {"HOME": str(self.home), "PATH": f"{SDK_BIN}:/usr/bin:/bin", "CLOUDSDK_CONFIG": str(config),
               "CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "1", **self.login_shell()}
        result = subprocess.run(["gcloud", "auth", "print-access-token"], env=env, capture_output=True, text=True,
                                timeout=300, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_both_config_dirs_get_the_relays_tokens_after_the_switch_and_after_the_revert(self) -> None:
        """The VM's own server (REAL) and the host relay are separate stand-ins, and the relay's tokens name the
        account it serves, so a client that still reads the VM's server, or a config naming the other account, is
        seen. Both gcloud config dirs start with the configuration the VM's server gave before the switch."""
        vm = MetadataStub.start(REAL)
        self.addCleanup(vm.stop)

        def tokens(stub: MetadataStub, token: Callable[[], str], expires_in: int) -> None:
            served = stub.answer

            def answer(path: str, query: dict[str, list[str]]) -> tuple[int, str, str]:
                status, kind, body = served(path, query)
                if status == 200 and path.endswith("/token"):
                    body = json.dumps({"access_token": token(), "expires_in": expires_in, "token_type": "Bearer"})
                return status, kind, body

            stub.answer = answer

        # gcloud keeps each account's token in its config dir (access_tokens.db) and reuses it until near its
        # expiry. The VM's are near it, so the REAL token stored before the switch is asked for again after the
        # revert, from whichever server the client names; in use the relay passes that same VM token through.
        tokens(vm, lambda: STUB_BEARER, 60)
        tokens(self.relay, lambda: f"relay-token-of-{self.relay.account}", 3599)
        before = {**self.env, "AGENTBOX_GCE_METADATA_UPSTREAM": vm.host, **dict.fromkeys(GCE_VARS, vm.host)}
        for installer in ("gcloud-box.sh", "gcloud-boxid.sh"):
            seed = subprocess.run(["bash", str(DOTFILES / "installers" / installer)], env=before, capture_output=True,
                                  text=True, timeout=600, check=False)
            self.assertEqual(seed.returncode, 0, seed.stderr + seed.stdout)
        configs = (self.config, self.boxid / "gcloud-box")
        self.assertEqual([self.box_account(config) for config in configs], [REAL, REAL])
        # The switch, then the revert, after which the relay passes the VM's own account through.
        for account in (TARGET, REAL):
            with self.subTest(account=account):
                self.relay.account = account
                result = self.run_installer(account, AGENTBOX_GCE_METADATA_UPSTREAM=vm.host)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                for config in configs:
                    self.assertEqual(self.box_account(config), account)
                    self.assertEqual(self.sdk_token(config), f"relay-token-of-{account}")

    def test_a_second_run_changes_nothing(self) -> None:
        first = self.run_installer(TARGET)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        env_d = self.env_d().read_text(encoding="utf-8")
        self.cache_tokens()
        again = self.run_installer()
        self.assertEqual(again.returncode, 0, again.stderr + again.stdout)
        self.assertNotIn("->", again.stdout)
        self.assertEqual(self.env_d().read_text(encoding="utf-8"), env_d)
        self.assertFalse([c for c in self.calls() if "restart" in c or "set-environment" in c], self.calls())
        self.assertTrue(all(cache.exists() for cache in self.token_caches()))

    def test_the_old_account_is_the_revert(self) -> None:
        self.run_installer(TARGET)
        # With the VM's own account in the file, the relay passes the real server's answer through.
        self.relay.account = REAL
        result = self.run_installer(REAL)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.account_file().read_text(encoding="utf-8").strip(), REAL)
        self.assertIn(f"{TARGET} -> {REAL}", result.stdout)
        self.assertIn("systemctl --user restart agentbox-imds", self.calls())

    def test_a_relay_that_cannot_start_does_not_leave_the_switch_on(self) -> None:
        result = self.run_installer(TARGET, STUB_FAIL_START="yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.account_file().exists())
        self.assertFalse(self.env_d().exists())
        self.assertFalse(self.config.exists())

    def test_a_failed_account_change_restores_the_previous_switch(self) -> None:
        self.account_file().parent.mkdir(parents=True)
        self.account_file().write_text(REAL + "\n", encoding="utf-8")
        result = self.run_installer(TARGET, STUB_FAIL_START="yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.account_file().read_text(encoding="utf-8"), REAL + "\n")

    def test_a_disabled_and_inactive_host_relay_stays_stopped_and_the_switch_stays_off(self) -> None:
        """Someone stopped the relay on purpose (scripts/AGENTS.md, "Acting on the system"): the run leaves it
        stopped, says so, and fails, since no host client can read the account without it."""
        unit = self.home / ".config" / "systemd" / "user" / "agent-identity-relay.service"
        unit.parent.mkdir(parents=True)
        unit.symlink_to(UNIT)
        result = self.run_installer(TARGET, STUB_RELAY_ACTIVE="no", STUB_RELAY_ENABLED="no")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("disabled and inactive", result.stderr)
        started = [c for c in self.calls() if c.split()[2:3] in (["enable"], ["start"], ["restart"])]
        self.assertEqual(started, [], self.calls())
        self.assertFalse(self.account_file().exists())
        self.assertFalse(self.env_d().exists())
        self.assertFalse(self.config.exists())

    def test_a_relay_unit_not_yet_linked_is_armed_though_systemd_calls_it_inactive(self) -> None:
        """The first run: systemd knows no such unit, so it is neither enabled nor active, and the run arms it."""
        result = self.run_installer(TARGET, STUB_RELAY_ACTIVE="no", STUB_RELAY_ENABLED="no")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("systemctl --user enable --now agent-identity-relay.service", self.calls())

    def follow_the_switch(self) -> None:
        """The host relay stand-in serves what the real relay serves: the account the switch file
        names, or the VM's own account with no file."""
        served = self.relay.answer

        def answer(path: str, query: dict[str, list[str]]) -> tuple[int, str, str]:
            named = self.account_file().read_text(encoding="utf-8").strip() if self.account_file().exists() else ""
            self.relay.account = named or REAL
            return served(path, query)

        self.relay.answer = answer

    def test_an_account_whose_tokens_the_relay_cannot_mint_is_not_switched(self) -> None:
        """Before its Token Creator grant applies, the relay answers dev-box@'s token with a 503 that names
        the refusal. A switch then would break every client on the host, so the run stops before it points
        any client at the relay, says why, and leaves the switch off."""
        served = self.relay.answer
        refusal = (f"agentbox-gce-metadata-relay: cannot serve {TARGET}: IAM Credentials refused "
                   f"generateAccessToken for {TARGET}: HTTP 403 PERMISSION_DENIED: Permission denied")
        self.relay.answer = lambda path, query: (
            (503, "application/text", refusal) if path.endswith("/token") else served(path, query))
        self.cache_tokens()
        result = self.run_installer(TARGET)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("PERMISSION_DENIED", result.stderr)
        self.assertFalse(self.account_file().exists())
        self.assertFalse(self.env_d().exists())
        self.assertFalse([c for c in self.calls() if "set-environment" in c], self.calls())
        self.assertFalse(self.config.exists())
        # The agent boxes' relay keeps running, and no client loses a cached token, for a switch that does not happen.
        self.assertNotIn("systemctl --user restart agentbox-imds", self.calls())
        self.assertTrue(all(cache.exists() for cache in self.token_caches()))

    def test_a_failure_after_the_relay_check_restores_the_switch_and_gcloud(self) -> None:
        """A run that fails after it wrote the switch, here in installers/gcloud-boxid.sh, puts the
        switch back, and then configuration box names what the relay serves again."""
        self.follow_the_switch()
        result = self.run_installer(TARGET, STUB_KUBECTL_FAIL="yes")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.account_file().exists())
        self.assertEqual(self.box_account(), REAL)
        self.assertFalse((self.home / ".config" / "agent-identity" / "applied").exists())
        self.assertIn("restored", result.stderr)

    def test_a_failed_record_of_the_applied_account_restores_the_switch(self) -> None:
        """The last step records the account the run applied. A run that cannot record it fails, and puts the
        switch back like any other failure."""
        applied = self.account_file().with_name("applied")
        applied.mkdir(parents=True)
        applied.chmod(0o500)
        self.addCleanup(applied.chmod, 0o700)
        self.follow_the_switch()
        result = self.run_installer(TARGET)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.account_file().exists())
        self.assertEqual(self.box_account(), REAL)

    def test_a_prewritten_switch_restarts_relays_and_discards_the_old_tokens(self) -> None:
        self.account_file().parent.mkdir(parents=True)
        self.account_file().write_text(TARGET + "\n", encoding="utf-8")
        self.cache_tokens()
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("systemctl --user restart agentbox-imds", self.calls())
        self.assertEqual([cache for cache in self.token_caches() if cache.exists()], [])
        self.assertEqual(self.account_file().with_name("applied").read_text(encoding="utf-8").strip(), TARGET)

    def test_a_prewritten_revert_discards_the_targets_cached_tokens(self) -> None:
        first = self.run_installer(TARGET)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.account_file().write_text(REAL + "\n", encoding="utf-8")
        self.relay.account = REAL
        self.cache_tokens()
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("systemctl --user restart agentbox-imds", self.calls())
        self.assertEqual([cache for cache in self.token_caches() if cache.exists()], [])
        self.assertEqual(self.account_file().with_name("applied").read_text(encoding="utf-8").strip(), REAL)

    def test_a_relay_serving_the_wrong_account_does_not_leave_the_switch_on(self) -> None:
        self.relay.account = REAL
        result = self.run_installer(TARGET)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.account_file().exists())
        self.assertFalse(self.env_d().exists())

    def test_readiness_waits_until_the_relay_serves_the_new_account(self) -> None:
        answer = self.relay.answer
        emails = 0

        def warming(path: str, query: dict[str, list[str]]) -> tuple[int, str, str]:
            nonlocal emails
            if path.endswith("/default/email"):
                emails += 1
                if emails == 1:
                    return 200, "application/text", REAL
            return answer(path, query)

        self.relay.answer = warming
        result = self.run_installer(TARGET)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertGreaterEqual(emails, 2)

    def test_an_account_that_is_no_service_account_is_refused(self) -> None:
        result = self.run_installer("someone@example.com")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.account_file().exists())
        self.assertEqual(self.calls(), [])

    def test_a_machine_that_is_no_google_vm_changes_nothing(self) -> None:
        result = self.run_installer(TARGET, AGENTBOX_GCE_METADATA_UPSTREAM="127.0.0.1:9")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing changed", result.stdout)
        self.assertFalse(self.account_file().exists())
        self.assertEqual(self.calls(), [])


class BashrcMetadata(unittest.TestCase):
    def test_each_callers_value_is_kept_in_bash_and_zsh(self) -> None:
        with tempfile.TemporaryDirectory(prefix="metadata-shell-") as directory:
            root = Path(directory)
            home = root / "home with spaces"
            env_file = home / ".config/environment.d/50-gce-metadata.conf"
            env_file.parent.mkdir(parents=True)
            env_file.write_text("".join(f"{name}=127.0.0.1:8170\n" for name in GCE_VARS), encoding="utf-8")
            minimal = root / "dotfiles"
            minimal.mkdir()
            (minimal / "agent-secrets").symlink_to(DOTFILES / "agent-secrets")
            for shell in ("bash", "zsh"):
                for names in ((GCE_VARS[0],), (GCE_VARS[1],), (GCE_VARS[2],), GCE_VARS[:2], GCE_VARS[1:],
                              (GCE_VARS[0], GCE_VARS[2]), GCE_VARS):
                    with self.subTest(shell=shell, caller=names):
                        caller = dict.fromkeys(names, "caller.invalid:8169")
                        env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "DOTFILES_DIR": str(minimal),
                               "DOTFILES_RC": str(DOTFILES / ".bashrc"), **caller}
                        result = subprocess.run([shell, "-c", '. "$DOTFILES_RC"; env'], env=env,
                                                capture_output=True, text=True, check=False, timeout=30)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        got = dict(line.split("=", 1) for line in result.stdout.splitlines() if line.startswith("GCE_METADATA_"))
                        self.assertEqual(got, {**dict.fromkeys(GCE_VARS, "127.0.0.1:8170"), **caller})


class TheOldTokenCaches(unittest.TestCase):
    def test_the_installer_removes_the_cache_where_its_producer_writes_it(self) -> None:
        """scripts/gcloud-token-fast keeps its token under `${XDG_RUNTIME_DIR:-/tmp}/gcloud-token`. The installer
        removes the file at that same expression, its fallback included. A run with XDG_RUNTIME_DIR set is the
        switch test above; with it unset the cache is in the shared /tmp, which no test here may touch."""
        producer = (DOTFILES / "scripts" / "gcloud-token-fast").read_text(encoding="utf-8")
        cache_dir = re.search(r'^cache_dir="([^"]+)"$', producer, re.M)
        self.assertIsNotNone(cache_dir, "scripts/gcloud-token-fast has no cache_dir= line")
        assert cache_dir is not None
        self.assertIn(f'"{cache_dir.group(1)}/access-token"', INSTALLER.read_text(encoding="utf-8"))


class TheHostRelayUnit(unittest.TestCase):
    def test_the_unit_runs_the_relay_on_the_address_the_installer_names_from_boot(self) -> None:
        unit = configparser.ConfigParser(interpolation=None)
        unit.read_string(UNIT.read_text(encoding="utf-8"))
        default = next(line for line in INSTALLER.read_text(encoding="utf-8").splitlines()
                       if line.startswith("RELAY="))
        host, port = default.split(":-", 1)[1].rstrip("}\"").split(":")
        # %h is the user's home: arm_user_service links the unit from ~/.dotfiles.
        argv = shlex.split(unit["Service"]["ExecStart"])
        self.assertEqual(argv, ["/usr/bin/python3", "%h/.dotfiles/scripts/agentbox-gce-metadata-relay", host, port, f"{host}/32"])
        self.assertTrue(os.access(argv[0], os.X_OK), argv[0])
        self.assertTrue((DOTFILES / argv[1].removeprefix("%h/.dotfiles/")).is_file(), argv[1])
        self.assertEqual(unit["Install"]["WantedBy"].split(), ["default.target"])


if __name__ == "__main__":
    unittest.main()
