#!/usr/bin/env python3
"""The laptop command is safe to bootstrap from an old git or colocated jj checkout."""

from __future__ import annotations

import os
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import time
import unittest
from pathlib import Path


DOTFILES = Path(__file__).resolve().parents[2]
README = DOTFILES / "README.md"
SETUP = DOTFILES / "laptop" / "oryx-tunnel-setup"



def real_binary(name: str) -> str:
    path = os.pathsep.join(part for part in os.environ.get("PATH", "").split(os.pathsep) if "/shims" not in part)
    found = shutil.which(name, path=path)
    if not found:
        raise RuntimeError(f"test requires {name}")
    return found


def write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def run(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=45)


FAKE = r'''
name="${0##*/}"
record() {
    {
        printf '%s:' "$name"
        printf ' %q' "$@"
        printf ' env TUNNEL_SERVICE_TOKEN_ID=%s TUNNEL_SERVICE_TOKEN_SECRET=%s\n' \
            "${TUNNEL_SERVICE_TOKEN_ID:+set}" "${TUNNEL_SERVICE_TOKEN_SECRET:+set}"
    } >> "$FAKE_LOG"
}
record "$@"

case "$name" in
    ssh)
        if [[ "${1:-}" == -G ]]; then
            # SSH_G_FAIL_FROM: the Nth plain ssh -G of the run, and every later one, fails.
            if [[ -n "${SSH_G_FAIL_FROM:-}" ]]; then
                calls=0
                [[ -f "$SSH_G_CALLS" ]] && calls="$(<"$SSH_G_CALLS")"
                calls=$((calls + 1))
                printf '%s\n' "$calls" > "$SSH_G_CALLS"
                if ((calls >= SSH_G_FAIL_FROM)); then
                    printf 'ssh: could not read configuration\n' >&2
                    exit 255
                fi
            fi
            # A plain ssh reads ~/.ssh/config, then /etc/ssh/ssh_config; -F alone skips the second.
            combined="$(/usr/bin/mktemp)"
            { /bin/cat "$HOME/.ssh/config"; [[ -f /etc/ssh/ssh_config ]] && printf '\nMatch all\nInclude /etc/ssh/ssh_config\n'; } > "$combined"
            status=0
            /usr/bin/ssh -F "$combined" -G "$2" || status=$?
            /bin/rm -f "$combined"
            exit "$status"
        fi
        if [[ "${1:-}" == -F && "${3:-}" == -G ]]; then
            exec /usr/bin/ssh -F "$2" -G "$4"
        fi
        for ((index = 1; index <= $#; index += 1)); do
            [[ "${!index}" == -F ]] || continue
            next=$((index + 1))
            {
                printf 'ssh probe file: %s\n' "${!next}"
                /bin/cat "${!next}"
            } >> "$FAKE_LOG"
        done
        joined=" $* "
        # SSH_FAILS_ON: '|'-separated parts of a command line; ssh itself fails on each match, as
        # when Cloudflare or oryx does not answer.
        if [[ -n "${SSH_FAILS_ON:-}" ]]; then
            IFS='|' read -r -a fail_parts <<< "$SSH_FAILS_ON"
            for part in "${fail_parts[@]}"; do
                if [[ "$joined" == *"$part"* ]]; then
                    printf 'ssh: connect to host oryx.thecybermonk.com port 22: Connection timed out\n' >&2
                    exit 255
                fi
            done
        fi
        if [[ "$joined" == *"forward port"* ]]; then
            if [[ "$2" =~ (oryx-port-[0-9]+-[0-9]+) ]]; then
                printf '%s\n' "${BASH_REMATCH[1]}" > "$CURRENT_NONCE"
            fi
            if [[ "$2" =~ python3[[:space:]]-[[:space:]]([0-9]+) ]]; then
                printf '%s\n' "${BASH_REMATCH[1]}" > "$FORWARD_PORT"
            fi
            command="$2"
            if [[ "$command" == *"python3 -m http.server"* ]]; then
                command="${command//timeout 90 /timeout 1 }"
            fi
            exec /bin/bash -c "$command"
        fi
        if [[ "$joined" == *"oryx-laptop && cat >"* ]]; then
            [[ "${FAIL_STEP:-}" == report ]] && exit 1
            mkdir -p "$REMOTE_ROOT"
            /bin/cat > "$REMOTE_ROOT/report.txt"
            exit 0
        fi
        if [[ "$joined" == *"CF_ACCESS_ORYX_TUNNEL_ID"* ]]; then
            [[ "${FAIL_STEP:-}" == token ]] && exit 1
            if [[ "${TOKEN_MALFORMED:-}" == 1 ]]; then
                printf 'not-a-token\n'
            else
                printf 'TUNNEL_SERVICE_TOKEN_ID=%s\nTUNNEL_SERVICE_TOKEN_SECRET=%s\n' "$FAKE_TOKEN_ID" "$FAKE_TOKEN_SECRET"
            fi
            exit 0
        fi
        if [[ "$joined" == *"BatchMode=yes"* ]]; then
            [[ "${FAIL_STEP:-}" == probe ]] && exit 1
            exit 0
        fi
        if [[ "$joined" == *"test -x ~/.local/bin/oryx-edge-cutover"* ]]; then
            [[ "${CUTOVER_MISSING:-}" == 1 ]] && exit 1
            exit 0
        fi
        if [[ "$joined" == *"~/.local/bin/oryx-edge-cutover"* ]]; then
            printf 'cutover complete\r\n'
            [[ "${FAIL_STEP:-}" == cutover ]] && exit 1
            exit 0
        fi
        if [[ "$joined" == *".config-XXXXXX.toml"* ]]; then
            mkdir -p "$REMOTE_ROOT"
            /bin/cat > "$REMOTE_ROOT/config.toml"
            printf 'move recorded\n' >> "$FAKE_LOG"
            [[ "${FAIL_STEP:-}" == move ]] && exit 1
            exit 0
        fi
        if [[ "${1:-}" == oryx && "${2:-}" == "rm -rf -- '/tmp/oryx-tunnel-setup."* ]]; then
            printf 'remote delivery cleanup\n' >> "$FAKE_LOG"
            exit 0
        fi
        if [[ "$joined" == *"secrets get CLOUDFLARE_API_KEY"* ]]; then
            # secretsd waits for the touch here; the test reads where the prompt stands against it.
            printf 'fake secretsd: waiting for the human-tier touch\n' >&2
            printf 'CLOUDFLARE_API_KEY: tier human, grant live\n'
            # ssh's own failure (255) when HUMAN_TIER_SSH_EXIT is set: no secretsd answered.
            exit "${HUMAN_TIER_SSH_EXIT:-0}"
        fi
        if [[ "$joined" == *"forward browser grant"* ]]; then
            printf 'https://browser.example\n'
            exit 0
        fi
        if [[ "$joined" == *"/json/version"* ]]; then
            printf '{"Browser":"fake"}\n'
            exit 0
        fi
        if [[ "$joined" == *"forward url"* ]]; then
            if [[ "$joined" =~ (oryx-(preview|page)-[0-9]+-[0-9]+) ]]; then
                printf 'ORYX_TUNNEL_TEMP_DIR=/tmp/oryx-tunnel-setup.fake-%s\n' "$RANDOM"
                printf '%s\n' "${BASH_REMATCH[1]}" > "$CURRENT_NONCE"
                printf 'http://127.0.0.1:12802/%s\n' "${BASH_REMATCH[1]}"
            else
                exit 2
            fi
            exit 0
        fi
        if [[ "$joined" == *"forward port"* ]]; then
            if [[ "$joined" =~ (oryx-port-[0-9]+-[0-9]+) ]]; then
                printf '%s\n' "${BASH_REMATCH[1]}" > "$CURRENT_NONCE"
            fi
            printf 'READY\n'
            exit 0
        fi
        if [[ "$joined" =~ -L[[:space:]]127\.0\.0\.1:([0-9]+):/run ]]; then
            port="${BASH_REMATCH[1]}"
            if [[ "${LOCAL_SOCKET_LOGIN_FAILS:-}" == 1 ]]; then
                # The key did not log in, so the -L never listens.
                printf 'agent@oryx-pcsc.thecybermonk.com: Permission denied (publickey).\n' >&2
                exit 255
            fi
            # LOCAL_SOCKET_FORWARDED_SILENT: sshd forwarded the socket, and the server on it, like
            # forward's pulse.sock, sends nothing until the client speaks.
            [[ "${LOCAL_SOCKET_FORWARDED_SILENT:-}" == 1 ]] || printf 'open failed\n' >&2
            # LOCAL_SOCKET_LISTEN_DELAY: seconds before the -L listens, as when the tunnel's
            # ProxyCommand and login take that long (real time; the fake sleep does not apply).
            /usr/bin/python3 - "$port" "${LOCAL_SOCKET_FORWARDED_SILENT:-}" "${LOCAL_SOCKET_LISTEN_DELAY:-0}" <<'PY' &
import socket
import sys
import time
time.sleep(float(sys.argv[3]))
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", int(sys.argv[1])))
sock.listen(5)
# A real ssh -L accepts every connection until it exits, so a client may connect more than once.
while True:
    conn, _ = sock.accept()
    if sys.argv[2] == "1":
        time.sleep(8)
    conn.close()
PY
            child=$!
            trap 'kill "$child" 2>/dev/null || true; exit 0' TERM INT
            wait "$child"
            exit 0
        fi
        if [[ "$joined" == *"oryx-probe"* ]]; then
            # The lines OpenSSH 9.6 prints when the key's options refuse these (scratch sshd, 2026-11-13).
            if [[ "$joined" == *"127.0.0.1:12999"* ]]; then
                printf 'Error: remote port forwarding failed for listen port 12999\n' >&2
                exit 255
            fi
            if [[ "$joined" == *" -W 127.0.0.1:22 "* ]]; then
                printf 'channel 0: open failed: administratively prohibited: \nstdio forwarding failed\n' >&2
                exit 255
            fi
            if [[ "$joined" == *" -R /"* ]]; then
                printf 'Error: remote port forwarding failed for listen path /tmp/oryx-probe.sock\n' >&2
                exit 255
            fi
            if [[ "$joined" == *" id "* ]]; then
                printf 'oryx-tunnel: forwarding-only key\n'
            fi
            exit 0
        fi
        if [[ "$joined" == *"sport = :5038"* ]]; then
            # TUNNEL_LOOK_HANGS: the look through ssh oryx hangs, as a slow Cloudflare connect can.
            [[ "${TUNNEL_LOOK_HANGS:-}" == 1 ]] && exec /bin/sleep 30
            [[ "${TUNNEL_DOWN:-}" == 1 ]] || printf 'LISTEN 0 128 127.0.0.1:5038 0.0.0.0:*\n'
            exit 0
        fi
        if [[ "$joined" == *"000chost:version"* ]]; then
            if [[ "${ADB_FORWARD_DOWN:-}" == 1 ]]; then
                printf 'ConnectionRefusedError: [Errno 111] Connection refused\n' >&2
                exit 1
            fi
            if [[ "${ADB_REPLIES_FAIL:-}" == 1 ]]; then
                printf 'FAIL0005nope\n'
                exit 0
            fi
            printf 'OKAY00040029\n'
            exit 0
        fi
        if [[ "$joined" == " oryx id -u " ]]; then
            printf '4242\n'
            exit 0
        fi
        if [[ "$joined" == *"oryx-login-probe"* ]]; then
            [[ "$joined" == *" id "* ]] && printf 'uid=1000(agent) gid=1000(agent)\n'
            exit 0
        fi
        exit 0
        ;;
    cloudflared)
        if [[ "${1:-}" == --version ]]; then
            if [[ -f "$CF_VERSION_FILE" ]]; then
                printf 'cloudflared version %s\n' "$(<"$CF_VERSION_FILE")"
            else
                printf 'cloudflared version 2025.1.1\n'
            fi
        fi
        ;;
    curl)
        for ((index = 1; index <= $#; index += 1)); do
            [[ "${!index}" == -o ]] || continue
            next=$((index + 1))
            printf key > "${!next}"
            exit 0
        done
        url="${!#}"
        if [[ -f "$FORWARD_PORT" && "$url" == http://127.0.0.1:"$(<"$FORWARD_PORT")"/* ]]; then
            if [[ "${FORWARD_CURL_FAILS:-}" == 1 ]]; then
                printf 'curl: (7) Failed to connect to 127.0.0.1\n' >&2
                exit 7
            fi
            if [[ "${FORWARD_CURL_FAIL_FIRST:-}" == 1 ]]; then
                attempts=0
                [[ -f "$FORWARD_CURL_CALLS" ]] && attempts="$(<"$FORWARD_CURL_CALLS")"
                ((attempts += 1))
                printf '%s\n' "$attempts" > "$FORWARD_CURL_CALLS"
                ((attempts > 1)) || exit 1
            fi
            exec /usr/bin/curl --noproxy '*' -fsS "$url"
        fi
        if [[ "${PREVIEW_CURL_FAILS:-}" == 1 ]]; then
            printf 'curl: (7) Failed to connect to 127.0.0.1 port 12802\n' >&2
            exit 7
        fi
        [[ -f "$CURRENT_NONCE" ]] && /bin/cat "$CURRENT_NONCE"
        ;;
    forward)
        [[ "${1:-}" == port ]] || exit 2
        trap 'exit 0' TERM INT
        while :; do /bin/sleep 1; done
        ;;
    gpg)
        printf 'fpr:::::::::%s:\n' "$GPG_FINGERPRINT"
        ;;
    sudo)
        exec "$@"
        ;;
    apt-get)
        if [[ "${FAIL_ADB_INSTALL:-}" == 1 && "${1:-}" == install && "${2:-}" == -y && "${3:-}" == adb ]]; then
            exit 1
        fi
        if [[ "${1:-}" == install && " $* " == *" cloudflared="* ]]; then
            printf '%s\n' "$CF_VERSION" > "$CF_VERSION_FILE"
        fi
        ;;
    systemctl)
        if [[ " $* " == *" is-active "* ]]; then
            unit="${!#}"
            # IS_ACTIVE_UNREACHABLE: is-active cannot reach the user manager, so it tells no state.
            if [[ "${IS_ACTIVE_UNREACHABLE:-}" == "$unit" && " $* " != *" --quiet "* ]]; then
                printf 'Failed to connect to bus: No medium found\n'
                exit 1
            fi
            if [[ "${INACTIVE_UNIT:-}" == "$unit" ]]; then
                [[ " $* " == *" --quiet "* ]] || printf 'inactive\n'
                exit 3
            fi
            [[ " $* " == *" --quiet "* ]] || printf 'active\n'
        elif [[ " $* " == *" show -p MainPID "* ]]; then
            # SYSTEMCTL_SHOW_FAILS: the user manager does not answer, so no MainPID is read.
            [[ "${SYSTEMCTL_SHOW_FAILS:-}" == 1 ]] && { printf 'Failed to connect to bus: No medium found\n' >&2; exit 1; }
            printf '0\n'
        fi
        ;;
    journalctl)
        printf 'fake journal\n'
        ;;
    ss)
        [[ "${FAIL_SS:-}" == 1 ]] && exit 1
        if [[ -n "${SS_FAILS_ON:-}" && " $* " == *"$SS_FAILS_ON"* ]]; then
            printf 'Cannot open netlink socket: Permission denied\n' >&2
            exit 1
        fi
        if [[ " $* " == *"sport = :12802"* ]]; then
            # The tunnel's LocalForward of the preview port: down, or up TUNNEL_UP_DELAY s after the
            # first look, as after a slow Cloudflare connect or a unit restart.
            [[ "${TUNNEL_DOWN:-}" == 1 ]] && exit 0
            [[ -f "$TUNNEL_FIRST_LOOK" ]] || /bin/date +%s > "$TUNNEL_FIRST_LOOK"
            (( $(/bin/date +%s) - $(<"$TUNNEL_FIRST_LOOK") >= ${TUNNEL_UP_DELAY:-0} )) || exit 0
            printf 'LISTEN 0 128 127.0.0.1:12802 0.0.0.0:*\n'
            exit 0
        fi
        if [[ "${FOREIGN_LISTENER:-}" == 1 && " $* " == *" -Hltnp "* ]]; then
            printf 'LISTEN 0 1 127.0.0.1:12802 0.0.0.0:* users:(("foreign",pid=%s,fd=3))\n' "$$"
        fi
        ;;
    hostname)
        printf 'sami\n'
        ;;
    tailscale)
        case "${3:-self}" in
            sami-devbox) printf '100.80.0.2\n' ;;
            sami-rocqperf) printf '100.80.0.3\n' ;;
            *) printf '100.80.0.1\n' ;;
        esac
        ;;
    mise)
        if [[ " $* " == *" forward doctor "* ]]; then
            if [[ "${FORWARD_DOCTOR_FAILS:-}" == 1 ]]; then
                printf 'forward doctor: daemon socket missing\n'
                exit 1
            fi
            printf 'forward doctor: healthy\n'
        fi
        ;;
    adb|paplay|omp)
        ;;
    sleep)
        exec /bin/sleep 0.02
        ;;
esac
'''


class TunnelSetup(unittest.TestCase):
    def setUp(self) -> None:
        scratch = subprocess.run(["mktemp", "-d"], capture_output=True, text=True, check=True)
        self.root = Path(scratch.stdout.strip())
        self.git = real_binary("git")
        self.jj = real_binary("jj")
        self._make_origin()
        self.reapers: list[tuple[subprocess.Popen[str], int]] = []

    def tearDown(self) -> None:
        for reaper, pid in self.reapers:
            if self.process_is_alive(pid):
                os.kill(pid, 15)
            try:
                reaper.wait(timeout=2)
            except subprocess.TimeoutExpired:
                reaper.terminate()
                reaper.wait(timeout=2)
        shutil.rmtree(self.root)

    def command(self, *args: str, cwd: Path, env: dict[str, str]) -> str:
        result = run(list(args), cwd=cwd, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def _make_origin(self) -> None:
        self.source = self.root / "source"
        shutil.copytree(DOTFILES, self.source, symlinks=True, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        self._write_pass_through_shims(self.source)
        write_executable(self.source / "bin/mise", 'exec "$ORYX_SETUP_FAKEBIN/mise" "$@"')
        for path in (
            "installers/cloudflared.sh",
            "laptop/oryx-tunnel-setup",
            "laptop/adb-server",
            "laptop/adb-server.service",
            "README.md",
        ):
            (self.source / path).unlink()
        env = self.git_env(self.root / "origin-home")
        self.command(self.git, "init", "-q", "-b", "main", cwd=self.source, env=env)
        self.command(self.git, "add", "-A", cwd=self.source, env=env)
        self.command(self.git, "add", "-f", "bin/mise", cwd=self.source, env=env)
        self.command(self.git, "commit", "-q", "-m", "stale checkout", cwd=self.source, env=env)
        self.stale = self.command(self.git, "rev-parse", "HEAD", cwd=self.source, env=env).strip()
        for path in (
            "installers/cloudflared.sh",
            "laptop/oryx-tunnel-setup",
            "laptop/adb-server",
            "laptop/adb-server.service",
            "README.md",
        ):
            destination = self.source / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DOTFILES / path, destination)
        self.command(self.git, "add", "-A", cwd=self.source, env=env)
        self.command(self.git, "commit", "-q", "-m", "current checkout", cwd=self.source, env=env)
        self.origin = self.root / "origin.git"
        self.command(self.git, "clone", "-q", "--bare", str(self.source), str(self.origin), cwd=self.root, env=env)

    def _write_pass_through_shims(self, tree: Path) -> None:
        write_executable(tree / "shims/git", f'exec "{self.git}" "$@"')
        write_executable(tree / "shims/jj", f'exec env JJ_CONFIG="$ORYX_SETUP_JJ_CONFIG" "{self.jj}" "$@"')
        write_executable(tree / "shims/omp", 'exec /bin/true "$@"')

    def git_env(self, home: Path) -> dict[str, str]:
        home.mkdir(parents=True, exist_ok=True)
        return {
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }

    def checkout(self, kind: str, name: str | None = None) -> None:
        name = name or kind
        self.kind = kind
        self.home = self.root / f"home-{name}"
        self.home.mkdir()
        self.checkout_dir = self.home / ".dotfiles"
        self.fakebin = self.root / f"fakebin-{name}"
        self.fakebin.mkdir()
        self.log = self.root / f"{name}.log"
        self.remote = self.root / f"remote-{name}"
        self.current_nonce = self.root / f"nonce-{name}"
        self.forward_port = self.root / f"forward-port-{name}"
        self.forward_curl_calls = self.root / f"forward-curl-calls-{name}"
        self.version_file = self.root / f"version-{name}"
        self.jj_config = self.root / f"{name}.jjconfig.toml"
        self.jj_config.write_text('[user]\nname = "Test"\nemail = "test@example.com"\n[signing]\nbehavior = "drop"\n', encoding="utf-8")
        self.token_id = f"test-token-id-{secrets.token_hex(12)}"
        self.token_secret = f"test-token-secret-{secrets.token_hex(12)}"
        self.env = {
            **self.git_env(self.home),
            "PATH": f"{self.fakebin}:/usr/bin:/bin",
            "ORYX_SETUP_FAKEBIN": str(self.fakebin),
            "FAKE_LOG": str(self.log),
            "REMOTE_ROOT": str(self.remote),
            "CURRENT_NONCE": str(self.current_nonce),
            "FORWARD_PORT": str(self.forward_port),
            "TUNNEL_FIRST_LOOK": str(self.root / f"tunnel-first-look-{name}"),
            "FORWARD_CURL_CALLS": str(self.forward_curl_calls),
            "SSH_G_CALLS": str(self.root / f"ssh-g-calls-{name}"),
            "CF_VERSION_FILE": str(self.version_file),
            "CF_VERSION": "2026.10.7",
            "GPG_FINGERPRINT": "CC94B39C77AE7342A68B89628A682D308D4E5E73",
            "FAKE_TOKEN_ID": self.token_id,
            "FAKE_TOKEN_SECRET": self.token_secret,
            "INSTALL_ROOT": str(self.root / f"install-root-{name}"),
            "ORYX_SETUP_JJ_CONFIG": str(self.jj_config),
            "JJ_CONFIG": str(self.jj_config),
        }
        self._write_fakes()
        if kind == "git":
            self.command(self.git, "clone", "-q", str(self.origin), str(self.checkout_dir), cwd=self.root, env=self.env)
            self.command(self.git, "checkout", "-q", "--detach", self.stale, cwd=self.checkout_dir, env=self.env)
        else:
            self.command(self.jj, "git", "clone", "--quiet", "--colocate", str(self.origin), str(self.checkout_dir), cwd=self.root, env=self.env)
            self.command(self.jj, "new", "--quiet", self.stale, cwd=self.checkout_dir, env=self.env)
        ssh_dir = self.home / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "oryx-tunnel").write_text("test private key\n", encoding="utf-8")
        (ssh_dir / "oryx-tunnel").chmod(0o600)

    def advance_to_current(self) -> None:
        if self.kind == "git":
            self.command(self.git, "checkout", "-q", "main", cwd=self.checkout_dir, env=self.env)
        else:
            self.command(self.jj, "new", "--quiet", "main@origin", cwd=self.checkout_dir, env=self.env)

    def _write_fakes(self) -> None:
        fake = self.fakebin / "fake"
        write_executable(fake, FAKE)
        for name in (
            "ssh",
            "systemctl",
            "journalctl",
            "cloudflared",
            "sudo",
            "apt-get",
            "curl",
            "forward",
            "gpg",
            "adb",
            "ss",
            "paplay",
            "hostname",
            "tailscale",
            "mise",
            "omp",
            "sleep",
        ):
            (self.fakebin / name).symlink_to(fake)

    def readme_command(self) -> str:
        text = README.read_text(encoding="utf-8")
        match = re.search(r"^git -C ~/.dotfiles fetch -q origin main && s=\$\(git -C ~/.dotfiles show origin/main:laptop/oryx-tunnel-setup\) && bash -c \"\$s\" oryx-tunnel-setup$", text, re.MULTILINE)
        self.assertIsNotNone(match, "README must contain the bootstrap command verbatim")
        return match.group(0)

    def invoke(
        self, *, readme: bool = False, after_sync: bool = False, answers: str = "y\ny\n", **extra: str
    ) -> subprocess.CompletedProcess[str]:
        if readme:
            command = ["bash", "-c", self.readme_command()]
        else:
            command = ["bash", str(self.checkout_dir / "laptop/oryx-tunnel-setup")]
            if after_sync:
                command.append("--after-sync")
        return subprocess.run(
            command,
            cwd=self.home,
            env={**self.env, **extra},
            input=answers,
            capture_output=True,
            text=True,
            timeout=75,
            check=False,
        )

    def lines(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def report(self) -> str:
        return (self.remote / "report.txt").read_text(encoding="utf-8")

    def ssh_values(self, host: str) -> dict[str, list[str]]:
        result = run(["/usr/bin/ssh", "-F", str(self.home / ".ssh/config"), "-G", host], cwd=self.home, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        values: dict[str, list[str]] = {}
        for line in result.stdout.splitlines():
            key, value = line.split(maxsplit=1)
            values.setdefault(key, []).append(value)
        return values

    def assert_no_tokens(self, *texts: str) -> None:
        for token in (self.token_id, self.token_secret):
            for text in texts:
                self.assertNotIn(token, text)
            for path in self.checkout_dir.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    self.assertNotIn(token, path.read_text(encoding="utf-8", errors="ignore"), path)

    def state(self) -> dict[str, tuple[str, bytes | str]]:
        paths = (
            self.home / ".ssh/config",
            self.home / ".config/forward/tunnel-oryx.env",
            self.home / ".config/forward/config-oryx.toml",
            self.home / ".config/systemd/user/forward-daemon@.service",
            self.home / ".config/systemd/user/forward-tunnel@.service",
            self.home / ".config/systemd/user/forward-tunnel@oryx.service.d/token.conf",
            self.home / ".config/systemd/user/adb-server.service",
        )
        state: dict[str, tuple[str, bytes | str]] = {}
        for path in paths:
            self.assertTrue(path.exists() or path.is_symlink(), path)
            if path.is_symlink():
                state[str(path)] = ("link", os.readlink(path))
            else:
                state[str(path)] = ("file", path.read_bytes())
        return state

    def assert_stop_before_move(self, result: subprocess.CompletedProcess[str], step: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"oryx-tunnel-setup: {step}:", result.stderr)
        calls = "\n".join(self.lines())
        self.assertNotIn("move recorded", calls)
        self.assertNotIn("enable --now forward-daemon@oryx.service", calls)
        self.assertNotIn("enable --now forward-tunnel@oryx.service", calls)
        self.assertNotIn("enable --now adb-server.service", calls)

    def build_reaper(self) -> tuple[Path, Path]:
        waiter_source = self.root / "waiter.c"
        reaper_source = self.root / "reaper.c"
        waiter = self.root / "waiter"
        reaper = self.root / "reaper"
        waiter_source.write_text("#include <unistd.h>\nint main(void) { for (;;) pause(); }\n", encoding="utf-8")
        reaper_source.write_text(
            "#include <stdio.h>\n#include <sys/wait.h>\n#include <unistd.h>\n"
            "int main(int argc, char **argv) { if (argc < 3) return 2; pid_t p = fork(); "
            "if (p == 0) { execv(argv[1], argv + 2); return 127; } printf(\"%ld\\n\", (long)p); "
            "fflush(stdout); return waitpid(p, 0, 0) < 0; }\n",
            encoding="utf-8",
        )
        compiler = real_binary("cc")
        for source, output in ((waiter_source, waiter), (reaper_source, reaper)):
            result = subprocess.run([compiler, "-O2", "-o", str(output), str(source)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
        return waiter, reaper

    def spawn_fixtures(self) -> dict[str, int]:
        waiter, reaper = self.build_reaper()
        devbox = self.root / "devbox"
        write_executable(devbox, "while :; do /bin/sleep 60; done")

        def spawn(executable: Path, args: list[str]) -> int:
            process = subprocess.Popen([str(reaper), str(executable), *args], stdout=subprocess.PIPE, text=True)
            self.assertIsNotNone(process.stdout)
            pid = int(process.stdout.readline().strip())
            self.reapers.append((process, pid))
            return pid

        return {
            "supervisor_oryx": spawn(Path("/bin/bash"), ["/bin/bash", str(devbox), "--tunnel-supervisor", "oryx"]),
            "supervisor_cf": spawn(Path("/bin/bash"), ["/bin/bash", str(devbox), "--tunnel-supervisor", "oryx-cf"]),
            "old_tunnel": spawn(waiter, ["ssh", "-N", "-f", "oryx-tunnel"]),
            "old_cf": spawn(waiter, ["ssh", "-N", "-f", "oryx-cf-tunnel"]),
            "unit_tunnel": spawn(waiter, ["/usr/bin/ssh", "-o", "ConnectTimeout=30", "-N", "oryx-tunnel"]),
        }

    @staticmethod
    def process_is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def test_readme_bootstraps_stale_git_and_jj_checkouts_twice(self) -> None:
        for kind in ("git", "jj"):
            with self.subTest(kind=kind):
                self.checkout(kind)
                self.assertFalse((self.checkout_dir / "laptop/oryx-tunnel-setup").exists())
                fixtures = self.spawn_fixtures()
                started = time.monotonic()
                first = self.invoke(readme=True)
                self.assertLess(time.monotonic() - started, 20)
                self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
                self.assertTrue((self.checkout_dir / "laptop/oryx-tunnel-setup").exists())
                self.assertLess(SETUP.stat().st_size, 100 * 1024)
                report = self.report()
                for fact in (
                    "cutover: output: cutover complete",
                    "systemctl --user is-active forward-daemon@oryx.service: active",
                    "devboxes daemon configuration:",
                    "ssh -G oryx:",
                    "ssh oryx true",
                    "forward doctor oryx pair",
                    "preview delivery",
                    "forward port delivery",
                    "browser page delivery",
                    "audio delivery",
                    "browser grant delivery",
                    "human-tier read",
                    "oryx SSM login",
                    "token file mode and owner",
                    "token leak search",
                    "laptop port 22: none",
                    "adb through tunnel",
                    "tunnel key true",
                    "tunnel key forced command",
                    "tunnel key rejects remote port 12999",
                    "tunnel key rejects stdio forwarding",
                    "tunnel key rejects remote socket forwarding",
                    "tunnel key rejects local socket forwarding",
                    "login key shell",
                ):
                    self.assertIn(fact, report)
                calls = "\n".join(self.lines())
                self.assertLess(calls.index("oryx-edge-cutover"), calls.index("CF_ACCESS_ORYX_TUNNEL_ID"))
                self.assertLess(calls.index("CF_ACCESS_ORYX_TUNNEL_ID"), calls.index("move recorded"))
                self.assertIn("old-tunnel: ended", report)
                self.assertIn("devbox-forward: complete", report)
                self.assertIn("ssh probe file:", calls)
                self.assertIn("IdentitiesOnly no", calls)
                self.assertIn("IdentityAgent ~/.1password/agent.sock", calls)
                self.assertIn("IdentitiesOnly yes", calls)
                self.assertNotIn("RemoteForward", calls)
                # The socket probes name paths on oryx: oryx's uid and oryx's /tmp.
                self.assertIn(":/run/user/4242/forward/pulse.sock", calls)
                self.assertRegex(calls, r"-R /tmp/oryx-probe-[0-9]+\.sock:127\.0\.0\.1:9")
                remote_forwards = [forward.replace("[", "").replace("]", "") for forward in self.ssh_values("oryx-tunnel").get("remoteforward", [])]
                self.assertIn("127.0.0.1:2224 ghost-wispr:22", remote_forwards)
                self.assert_no_tokens(first.stdout, first.stderr, report, calls)
                token = self.home / ".config/forward/tunnel-oryx.env"
                self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o600)
                self.assertEqual(token.read_text(encoding="utf-8"), f"TUNNEL_SERVICE_TOKEN_ID={self.token_id}\nTUNNEL_SERVICE_TOKEN_SECRET={self.token_secret}\n")
                for name in ("supervisor_oryx", "supervisor_cf", "old_tunnel", "old_cf"):
                    self.assertFalse(self.process_is_alive(fixtures[name]), name)
                self.assertTrue(self.process_is_alive(fixtures["unit_tunnel"]))
                first_state = self.state()
                second = self.invoke(readme=True)
                self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
                self.assertEqual(self.state(), first_state)
                tunnel_restarts = [
                    line
                    for line in self.lines()
                    if line.startswith("systemctl: --user restart forward-tunnel@oryx.service ")
                ]
                self.assertEqual(len(tunnel_restarts), 2)

    def test_proxycommand_uses_token_environment_without_arguments(self) -> None:
        self.checkout("git")
        result = self.invoke(readme=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        values = run(["/usr/bin/ssh", "-F", str(self.home / ".ssh/config"), "-G", "oryx-tunnel"], cwd=self.home, env=self.env)
        self.assertEqual(values.returncode, 0, values.stderr)
        parsed = dict(line.split(maxsplit=1) for line in values.stdout.splitlines())
        command = parsed["proxycommand"].replace("%h", parsed["hostname"])
        proxy = subprocess.run(
            ["bash", "-c", command],
            cwd=self.home,
            env={**self.env, "TUNNEL_SERVICE_TOKEN_ID": self.token_id, "TUNNEL_SERVICE_TOKEN_SECRET": self.token_secret},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proxy.returncode, 0, proxy.stderr)
        calls = "\n".join(self.lines())
        self.assertIn("cloudflared: access ssh --hostname oryx-pcsc.thecybermonk.com env TUNNEL_SERVICE_TOKEN_ID=set TUNNEL_SERVICE_TOKEN_SECRET=set", calls)
        self.assert_no_tokens(calls, proxy.stdout, proxy.stderr, self.report())

    def test_cutover_and_token_failures_are_negative_controls(self) -> None:
        for failure, step in (("cutover", "cutover"), ("token", "token")):
            with self.subTest(failure=failure):
                self.checkout("git", f"negative-{failure}")
                self.advance_to_current()
                result = self.invoke(after_sync=True, FAIL_STEP=failure)
                self.assert_stop_before_move(result, step)

    def test_stops_before_the_move_and_reports_locally_until_probe(self) -> None:
        cases = (
            ("probe", {"FAIL_STEP": "probe"}),
            ("cutover", {"CUTOVER_MISSING": "1"}),
            ("cutover", {"FAIL_STEP": "cutover"}),
            ("token", {"TOKEN_MALFORMED": "1"}),
            ("ssh-config", {"CONFIG_LOCAL": "Host oryx\n  HostName old.example\n"}),
            ("preflight", {"FOREIGN_LISTENER": "1"}),
        )
        for index, (step, extra) in enumerate(cases):
            with self.subTest(step=step, extra=extra):
                self.checkout("git", f"stop-{index}")
                self.advance_to_current()
                if "CONFIG_LOCAL" in extra:
                    (self.home / ".ssh/config.local").write_text(extra.pop("CONFIG_LOCAL"), encoding="utf-8")
                result = self.invoke(after_sync=True, **extra)
                self.assert_stop_before_move(result, step)
                if step == "probe":
                    self.assertFalse(self.remote.exists())
                    self.assertIn(str(self.home / ".local/state/oryx-laptop"), result.stdout)

    def test_move_retry_and_inactive_unit_stop(self) -> None:
        self.checkout("git", "move-retry")
        self.advance_to_current()
        failed = self.invoke(after_sync=True, FAIL_STEP="move")
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertIn("oryx-tunnel-setup: move:", failed.stderr)
        self.assertTrue((self.remote / "config.toml").exists())
        self.assertNotIn("enable --now forward-daemon@oryx.service", "\n".join(self.lines()))
        complete = self.invoke(after_sync=True)
        self.assertEqual(complete.returncode, 0, complete.stdout + complete.stderr)
        self.assertIn('listen = "127.0.0.1"', (self.remote / "config.toml").read_text(encoding="utf-8"))

        self.checkout("git", "inactive-unit")
        self.advance_to_current()
        inactive = self.invoke(after_sync=True, INACTIVE_UNIT="adb-server.service")
        self.assertEqual(inactive.returncode, 1, inactive.stdout + inactive.stderr)
        self.assertIn("oryx-tunnel-setup: units:", inactive.stderr)
        self.assertIn("move recorded", "\n".join(self.lines()))

    def test_report_upload_failure_prints_the_local_path(self) -> None:
        self.checkout("git", "report-failure")
        self.advance_to_current()
        result = self.invoke(after_sync=True, FAIL_STEP="report")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("oryx-tunnel-setup: report:", result.stderr)
        self.assertIn(str(self.home / ".local/state/oryx-laptop"), result.stdout)

    def test_token_argument_control_trips_the_recorded_argument_search(self) -> None:
        self.checkout("git", "token-argument")
        result = self.invoke(readme=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = self.checkout_dir / "laptop/ssh-config"
        text = config.read_text(encoding="utf-8")
        config.write_text(text.replace("cloudflared access ssh --hostname %h", f"cloudflared access ssh --hostname %h --service-token-secret {self.token_secret}"), encoding="utf-8")
        values = run(["/usr/bin/ssh", "-F", str(self.home / ".ssh/config"), "-G", "oryx-tunnel"], cwd=self.home, env=self.env)
        command = dict(line.split(maxsplit=1) for line in values.stdout.splitlines())["proxycommand"].replace("%h", "oryx-pcsc.thecybermonk.com")
        subprocess.run(["bash", "-c", command], cwd=self.home, env=self.env, capture_output=True, text=True, check=False)
        recorded = "\n".join(self.lines())
        self.assertIn(self.token_secret, recorded)
        with self.assertRaises(AssertionError):
            self.assert_no_tokens(recorded)

    def test_adb_launcher_prefers_the_sdk_adb_and_runs_the_loopback_server(self) -> None:
        launcher = DOTFILES / "laptop/adb-server"
        home = self.root / "adb-home"
        bin_dir = self.root / "adb-bin"
        bin_dir.mkdir()
        log = self.root / "adb.log"
        for path, name in ((bin_dir / "adb", "path"), (home / "Android/sdk/platform-tools/adb", "sdk")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'#!/bin/bash\necho "{name} $*" >> "{log}"\n', encoding="utf-8")
            path.chmod(0o755)
        # PATH holds only the fake: a real adb elsewhere on PATH must not be found.
        env = {"HOME": str(home), "PATH": str(bin_dir)}
        sdk = subprocess.run(["/bin/bash", str(launcher)], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(sdk.returncode, 0, sdk.stderr)
        (home / "Android/sdk/platform-tools/adb").unlink()
        on_path = subprocess.run(["/bin/bash", str(launcher)], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(on_path.returncode, 0, on_path.stderr)
        self.assertEqual(
            log.read_text(encoding="utf-8").splitlines(),
            ["sdk -L tcp:localhost:5037 server nodaemon", "path -L tcp:localhost:5037 server nodaemon"],
        )
        (bin_dir / "adb").unlink()
        missing = subprocess.run(["/bin/bash", str(launcher)], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("adb is not installed", missing.stderr)

    def fake_ss(self, *lines: str) -> None:
        """Make the fake ss print these `ss -Hltnp` lines; PID becomes the fake's own pid. The
        self-check's look for the tunnel's preview LocalForward still finds it up."""
        (self.fakebin / "ss").unlink()
        text = "\n".join(lines)
        write_executable(
            self.fakebin / "ss",
            f'if [[ " $* " == *" -Hltnp "* ]]; then\n/bin/sed "s/PID/$$/" <<\'LINES\'\n{text}\nLINES\nfi\n'
            'if [[ " $* " == *"sport = :12802"* ]]; then\nprintf \'LISTEN 0 128 127.0.0.1:12802 0.0.0.0:*\\n\'\nfi',
        )

    def path_without_adb(self) -> Path:
        restricted = self.root / f"no-adb-bin-{self.kind}"
        restricted.mkdir()
        for name in (
            "ssh",
            "systemctl",
            "journalctl",
            "cloudflared",
            "sudo",
            "apt-get",
            "curl",
            "forward",
            "gpg",
            "ss",
            "paplay",
            "hostname",
            "tailscale",
            "mise",
            "omp",
            "sleep",
        ):
            (restricted / name).symlink_to(self.fakebin / name)
        for name in (
            "bash",
            "basename",
            "cat",
            "chmod",
            "cmp",
            "date",
            "dirname",
            "grep",
            "id",
            "ln",
            "mkdir",
            "mktemp",
            "mv",
            "python3",
            "rm",
            "sort",
            "stat",
            "tee",
            "tr",
        ):
            (restricted / name).symlink_to(real_binary(name))
        return restricted

    def test_preflight_refuses_a_wildcard_listener_on_a_tunnel_port(self) -> None:
        # ss prints these for a listener on 0.0.0.0 and on dual-stack ::. Either one makes the
        # tunnel's LocalForward bind of 127.0.0.1:12802 fail with EADDRINUSE.
        for index, line in enumerate((
            'LISTEN 0      1      0.0.0.0:12802 0.0.0.0:* users:(("python3",pid=PID,fd=3))',
            'LISTEN 0      1      *:12802 *:* users:(("python3",pid=PID,fd=4))',
        )):
            with self.subTest(line=line):
                self.checkout("git", f"wildcard-{index}")
                self.advance_to_current()
                self.fake_ss(line)
                result = self.invoke(after_sync=True)
                self.assert_stop_before_move(result, "preflight")

    def test_preflight_ignores_a_listener_whose_port_only_starts_with_5037(self) -> None:
        self.checkout("git", "port-prefix")
        self.advance_to_current()
        self.fake_ss('LISTEN 0      4096   127.0.0.1:50371 0.0.0.0:* users:(("python3",pid=PID,fd=3))')
        result = self.invoke(after_sync=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_preflight_stops_when_ss_cannot_list_listeners(self) -> None:
        # A listing that could not be read is not "nothing listens".
        self.checkout("git", "ss-fails")
        self.advance_to_current()
        result = self.invoke(after_sync=True, FAIL_SS="1")
        self.assert_stop_before_move(result, "preflight")

    def test_report_umask_does_not_reach_the_apt_files(self) -> None:
        self.checkout("git", "umask")
        result = self.invoke(readme=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        install_root = Path(self.env["INSTALL_ROOT"])
        for path in (
            install_root / "etc/apt/sources.list.d/cloudflared.list",
            install_root / "etc/apt/preferences.d/cloudflared.pref",
        ):
            with self.subTest(path=str(path)):
                # apt's non-root readers (apt list, apt-cache policy) need group and other read.
                self.assertEqual(oct(stat.S_IMODE(path.stat().st_mode) & 0o044), oct(0o044))

    def test_bootstrap_leaves_no_work_dir_behind(self) -> None:
        self.checkout("git", "workdir")
        tmpdir = self.root / "tmpdir"
        tmpdir.mkdir()
        result = self.invoke(readme=True, TMPDIR=str(tmpdir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sorted(path.name for path in tmpdir.glob("oryx-tunnel-setup.*")), [])

    def test_config_local_options_outside_host_lines_are_refused(self) -> None:
        # An option before any Host line, or in a Match block, of ~/.ssh/config.local applies
        # to Host oryx-tunnel too, since the config includes config.local first.
        for index, text in enumerate((
            "StrictHostKeyChecking no\n",
            "IdentityFile ~/.ssh/other\n",
            "Match originalhost oryx-tunnel\n  StrictHostKeyChecking no\n",
        )):
            with self.subTest(text=text):
                self.checkout("git", f"config-local-{index}")
                self.advance_to_current()
                (self.home / ".ssh/config.local").write_text(text, encoding="utf-8")
                result = self.invoke(after_sync=True)
                self.assert_stop_before_move(result, "ssh-config")

    def test_token_search_that_cannot_read_the_tree_does_not_report_none(self) -> None:
        self.checkout("git", "leak-blind")
        self.advance_to_current()
        leaked = self.checkout_dir / "leaked.env"
        leaked.write_text(f"TUNNEL_SERVICE_TOKEN_SECRET={self.token_secret}\n", encoding="utf-8")
        leaked.chmod(0)
        try:
            result = self.invoke(after_sync=True)
        finally:
            leaked.chmod(0o600)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("oryx-tunnel-setup: token:", result.stderr)
        self.assertNotIn("dotfiles search: none", self.report())

    def test_human_tier_read_asks_for_the_yubikey_touch(self) -> None:
        # README: "Touch the YubiKey when the command asks for it." The human-tier read needs a
        # touch (secrets get: "triggers the hardware touch when no grant is live"), and its own
        # output goes to /dev/null.
        self.checkout("git", "yubikey-ask")
        self.advance_to_current()
        result = self.invoke(after_sync=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("YubiKey", result.stderr)

    def test_human_tier_prompt_comes_before_its_secrets_read(self) -> None:
        # The cutover and the browser grant also name the YubiKey, so this reads the human-tier
        # read's own prompt and its place before the touch secretsd waits for.
        self.checkout("git", "human-tier-prompt")
        self.advance_to_current()
        result = self.invoke(after_sync=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        prompt = "Touch the YubiKey now for the human-tier secret read."
        waiting = "fake secretsd: waiting for the human-tier touch"
        self.assertIn(prompt, result.stderr)
        self.assertIn(waiting, result.stderr)
        self.assertLess(result.stderr.index(prompt), result.stderr.index(waiting))

    def test_report_carries_each_self_check_value(self) -> None:
        # The parent's checks read these values from the report, not only ok or failed.
        self.checkout("git", "report-values")
        self.advance_to_current()
        result = self.invoke(after_sync=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = self.report()
        user = pwd.getpwuid(os.getuid()).pw_name
        for line in (
            f"self-check: token file mode and owner: 600 {user}: ok",
            "self-check: token leak search: none: ok",
            "self-check: ssh oryx true: exit 0: ok",
            "self-check: oryx SSM login: exit 0: ok",
            "self-check: human-tier read: CLOUDFLARE_API_KEY granted (exit 0): ok",
            "self-check: adb through tunnel: reply OKAY00040029: ok",
            "self-check: tunnel key true: exit 0: ok",
            "self-check: tunnel key forced command: exit 0, no uid printed: ok",
            "self-check: tunnel key rejects remote port 12999: exit 255, Error: remote port forwarding failed for listen port 12999: ok",
            "self-check: tunnel key rejects stdio forwarding: exit 255, stdio forwarding failed: ok",
            "self-check: tunnel key rejects remote socket forwarding: exit 255, Error: remote port forwarding failed for listen path /tmp/oryx-probe.sock: ok",
            "self-check: tunnel key rejects local socket forwarding: client got EOF, open failed: ok",
            "self-check: login key shell: exit 0, uid present: ok",
        ):
            self.assertIn(line + "\n", report)
        self.assertNotIn("uid=", report)

    def test_local_socket_probe_names_a_connection_error_not_data(self) -> None:
        # A -L that never came up proves nothing about the key; the report must not say "got data".
        self.checkout("git", "local-socket-refused")
        self.advance_to_current()
        result = self.invoke(after_sync=True, LOCAL_SOCKET_LOGIN_FAILS="1")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("oryx-tunnel-setup: self-check:", result.stderr)
        report = self.report()
        self.assertIn(
            "self-check: tunnel key rejects local socket forwarding: client never connected; ssh exited"
            " (exit 255), ssh said: agent@oryx-pcsc.thecybermonk.com: Permission denied"
            " (publickey).: failed\n",
            report,
        )
        self.assertNotIn("client got data", report)

    def test_adb_probe_names_an_error_not_a_reply(self) -> None:
        self.checkout("git", "adb-forward-down")
        self.advance_to_current()
        result = self.invoke(after_sync=True, ADB_FORWARD_DOWN="1")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "self-check: adb through tunnel: no adb reply: ConnectionRefusedError: [Errno 111] Connection refused: failed\n",
            self.report(),
        )

    def test_human_tier_line_does_not_call_an_ssh_failure_a_refusal(self) -> None:
        # ssh exits 255 when it cannot reach oryx or log in; secretsd never saw the request, so
        # the report must not say the key was refused.
        self.checkout("git", "human-tier-ssh-down")
        self.advance_to_current()
        result = self.invoke(after_sync=True, HUMAN_TIER_SSH_EXIT="255")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        lines = [line for line in self.report().splitlines() if line.startswith("self-check: human-tier read:")]
        self.assertEqual(len(lines), 1, self.report())
        self.assertIn("exit 255", lines[0])
        self.assertTrue(lines[0].endswith(": failed"), lines[0])
        self.assertNotIn("refused", lines[0])

    def test_human_tier_line_does_not_call_every_secrets_error_a_refusal(self) -> None:
        # secrets exits 1 for errors that are not a refusal: `secrets get --no-request` of a key
        # that does not exist printed "secrets: secret '...' not found; looked in ..." and exit 1 on
        # oryx. A bare exit status cannot tell those from a refusal, so the line must not say one.
        self.checkout("git", "human-tier-secrets-error")
        self.advance_to_current()
        result = self.invoke(after_sync=True, HUMAN_TIER_SSH_EXIT="1")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        lines = [line for line in self.report().splitlines() if line.startswith("self-check: human-tier read:")]
        self.assertEqual(len(lines), 1, self.report())
        self.assertIn("exit 1", lines[0])
        self.assertTrue(lines[0].endswith(": failed"), lines[0])
        self.assertNotIn("refused", lines[0])

    def test_human_tier_status_reaches_the_terminal_not_the_report(self) -> None:
        # Sami reads secretsd's own status (or its "not found" / timeout) on the terminal.
        self.checkout("git", "human-tier-terminal")
        self.advance_to_current()
        result = self.invoke(after_sync=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CLOUDFLARE_API_KEY: tier human, grant live", result.stdout)
        self.assertNotIn("tier human, grant live", self.report())

    def test_local_socket_probe_reports_a_silent_socket_as_no_data_or_eof(self) -> None:
        # The dangerous case: sshd forwards the Unix socket and its server stays silent.
        self.checkout("git", "local-socket-forwarded")
        self.advance_to_current()
        result = self.invoke(after_sync=True, LOCAL_SOCKET_FORWARDED_SILENT="1")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertIn(
            "self-check: tunnel key rejects local socket forwarding: client connected; no data or EOF"
            ' within 5 s, no "open failed" line: failed\n',
            report,
        )
        self.assertNotIn("client connection failed", report)

    def test_local_socket_probe_waits_for_a_tunnel_that_takes_seconds_to_listen(self) -> None:
        # ssh binds a -L only after its ProxyCommand connects and it logs in. Against a real
        # sshd, a ProxyCommand that waits 2 s gave 'client connection failed:
        # ConnectionRefusedError' after the probe's fixed 1 s wait (2026-11-13), and cloudflared
        # reaches Cloudflare's edge before the login starts. The probe must wait for the -L.
        self.checkout("git", "local-socket-slow-tunnel")
        self.advance_to_current()
        result = self.invoke(after_sync=True, LOCAL_SOCKET_LISTEN_DELAY="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("self-check: tunnel key rejects local socket forwarding: client got EOF, open failed: ok\n", self.report())

    def test_human_tier_ssh_failure_says_the_outcome_is_unknown(self) -> None:
        # ssh also exits 255 when the connection drops after secrets get started.
        self.checkout("git", "human-tier-255-label")
        self.advance_to_current()
        result = self.invoke(after_sync=True, HUMAN_TIER_SSH_EXIT="255")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "self-check: human-tier read: CLOUDFLARE_API_KEY ssh failed (exit 255); request outcome unknown: failed\n",
            self.report(),
        )

    def test_delivery_probes_wait_for_the_tunnel_to_carry_its_forwards(self) -> None:
        # The unit is active before cloudflared connects: the deliveries wait until the forwards exist.
        self.checkout("git", "tunnel-late")
        self.advance_to_current()
        result = self.invoke(after_sync=True, TUNNEL_UP_DELAY="3")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = self.report()
        waited = re.search(r"self-check: tunnel up after (\d+) s\n", report)
        self.assertIsNotNone(waited, report)
        self.assertGreaterEqual(int(waited.group(1)), 3)
        self.assertLess(report.index("self-check: tunnel up after"), report.index("self-check: preview delivery:"))
        self.assertIn("self-check: preview delivery: ok\n", report)

    def test_delivery_probes_say_not_run_when_the_tunnel_never_comes_up(self) -> None:
        self.checkout("git", "tunnel-down")
        self.advance_to_current()
        result = self.invoke(after_sync=True, TUNNEL_DOWN="1", ORYX_TUNNEL_WAIT_SECONDS="2")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        waited = re.search(
            r"self-check: tunnel not up after (\d+) s \(laptop 127\.0\.0\.1:12802 listening: no;"
            r" oryx 127\.0\.0\.1:5038 listening: no\)\n",
            report,
        )
        self.assertIsNotNone(waited, report)
        self.assertGreaterEqual(int(waited.group(1)), 2)
        # The devboxes' doctor reads the laptop's own devbox daemon, so it runs without oryx's tunnel.
        self.assertIn("self-check: forward doctor devboxes: ok\n", report)
        for name in (
            "forward doctor oryx pair", "oryx forward doctor", "preview delivery",
            "forward port delivery", "browser page delivery", "audio delivery", "browser grant delivery",
            "human-tier read", "adb through tunnel",
        ):
            self.assertIn(f"self-check: {name}: not run: tunnel not up: failed\n", report)
        self.assertIn("self-check: tunnel key true: exit 0: ok\n", report)

    def test_tunnel_wait_bounds_its_look_through_ssh_oryx(self) -> None:
        # Each look through ssh oryx is cut to the time left, so a hanging connect cannot stretch the wait.
        self.checkout("git", "tunnel-look-hangs")
        self.advance_to_current()
        started = time.monotonic()
        result = self.invoke(after_sync=True, TUNNEL_LOOK_HANGS="1", ORYX_TUNNEL_WAIT_SECONDS="3")
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        waited = re.search(r"self-check: tunnel not up after (\d+) s ", self.report())
        self.assertIsNotNone(waited, self.report())
        self.assertLessEqual(int(waited.group(1)), 5)
        self.assertLess(elapsed, 25)

    def test_tunnel_wait_does_not_report_a_hung_look_as_not_listening(self) -> None:
        # When the look through ssh oryx never answers, nothing was seen of oryx's 127.0.0.1:5038.
        # The not-up line must not say that port is not listening; it was never looked at. Against a
        # real tunnel whose RemoteForward did listen, a hung look gave
        # 'oryx 127.0.0.1:5038 listening: no' (scratch sshd, 2026-11-13).
        self.checkout("git", "tunnel-look-hangs-label")
        self.advance_to_current()
        result = self.invoke(after_sync=True, TUNNEL_LOOK_HANGS="1", ORYX_TUNNEL_WAIT_SECONDS="3")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        lines = [line for line in self.report().splitlines() if line.startswith("self-check: tunnel not up after ")]
        self.assertEqual(len(lines), 1, self.report())
        self.assertNotIn("oryx 127.0.0.1:5038 listening: no", lines[0])

    def test_tunnel_wait_names_a_failed_look_on_each_side(self) -> None:
        # A look that failed saw no port, so the not-up line names the failed look, not "no".
        self.checkout("git", "tunnel-looks-fail")
        self.advance_to_current()
        result = self.invoke(
            after_sync=True, SS_FAILS_ON="sport = :12802", SSH_FAILS_ON="sport = :5038", ORYX_TUNNEL_WAIT_SECONDS="3"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "(laptop 127.0.0.1:12802 listening: look with ss failed (exit 1); "
            "oryx 127.0.0.1:5038 listening: look through ssh oryx failed (exit 255))\n",
            self.report(),
        )

    def test_laptop_self_checks_name_their_failed_looks(self) -> None:
        self.checkout("git", "laptop-looks-fail")
        self.advance_to_current()
        # The third plain ssh -G is the self-check's; the first two are the ssh-config step's.
        result = self.invoke(
            after_sync=True,
            SS_FAILS_ON="sport = :22 )",
            IS_ACTIVE_UNREACHABLE="adb-server.service",
            SSH_G_FAIL_FROM="3",
            FORWARD_DOCTOR_FAILS="1",
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        for line in (
            "self-check: laptop port 22: look with ss failed (exit 1): failed",
            "self-check: unit adb-server.service: systemctl --user is-active printed "
            "Failed to connect to bus: No medium found (exit 1): failed",
            "self-check: ssh configuration: ssh -G oryx failed (exit 255): failed",
            "self-check: forward doctor devboxes: exit 1: forward doctor: daemon socket missing: failed",
        ):
            self.assertIn(line + "\n", report)

    def test_delivery_probes_name_ssh_oryx_failing(self) -> None:
        self.checkout("git", "delivery-ssh-fails")
        self.advance_to_current()
        result = self.invoke(
            after_sync=True, SSH_FAILS_ON="forward url|forward port|paplay|forward browser grant|000chost:version"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        for line in (
            "self-check: preview delivery: making the file and its URL through ssh oryx failed (exit 255): failed",
            "self-check: forward port delivery: ssh oryx exited 255 before oryx printed READY: failed",
            "self-check: browser page delivery: making the page and its URL through ssh oryx failed (exit 255): failed",
            "self-check: audio delivery: paplay through ssh oryx failed (exit 255): failed",
            "self-check: browser grant delivery: forward browser grant through ssh -t oryx failed "
            "(exit 255, tee exit 0): failed",
            "self-check: adb through tunnel: ssh oryx failed (exit 255): "
            "ssh: connect to host oryx.thecybermonk.com port 22: Connection timed out: failed",
        ):
            self.assertIn(line + "\n", report)

    def test_delivery_probes_name_the_step_after_ssh_that_failed(self) -> None:
        self.checkout("git", "delivery-step-fails")
        self.advance_to_current()
        result = self.invoke(
            after_sync=True, PREVIEW_CURL_FAILS="1", FORWARD_CURL_FAILS="1", SSH_FAILS_ON="forward open|/json/version"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        for line in (
            "self-check: preview delivery: fetching the preview URL failed (curl exit 7): failed",
            "self-check: browser page delivery: forward open through ssh oryx failed (exit 255): failed",
            "self-check: browser grant delivery: fetching /json/version through ssh oryx failed (exit 255): failed",
        ):
            self.assertIn(line + "\n", report)
        self.assertRegex(
            report,
            r"self-check: forward port delivery: fetching localhost:\d+ failed for 10 s: "
            r"curl: \(7\) Failed to connect to 127\.0\.0\.1: failed\n",
        )

    def test_delivery_probes_name_a_failed_cleanup_on_oryx(self) -> None:
        # The file was delivered; removing its directory through ssh oryx failed, and the line says so.
        self.checkout("git", "delivery-cleanup-fails")
        self.advance_to_current()
        result = self.invoke(after_sync=True, SSH_FAILS_ON="rm -rf -- '/tmp/oryx-tunnel-setup.")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertIn("self-check: preview delivery: removing the preview's oryx directory failed (exit 255): failed\n", report)
        self.assertIn(
            "self-check: browser page delivery: answer: yes; removing the page's oryx directory failed (exit 255): failed\n", report
        )

    def test_preview_probe_names_both_a_failed_fetch_and_a_failed_cleanup(self) -> None:
        self.checkout("git", "preview-fetch-and-cleanup-fail")
        self.advance_to_current()
        result = self.invoke(after_sync=True, PREVIEW_CURL_FAILS="1", SSH_FAILS_ON="rm -rf -- '/tmp/oryx-tunnel-setup.")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "self-check: preview delivery: fetching the preview URL failed (curl exit 7); "
            "removing the preview's oryx directory failed (exit 255): failed\n",
            self.report(),
        )

    def test_delivery_probes_report_the_answer_they_read(self) -> None:
        for name, answers, value in (
            ("answers-no", "n\nn\n", "answer: no"),
            ("answers-none", "", "no answer (read exit 1)"),
        ):
            with self.subTest(name):
                self.checkout("git", name)
                self.advance_to_current()
                result = self.invoke(after_sync=True, answers=answers)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                report = self.report()
                self.assertIn(f"self-check: browser page delivery: {value}: failed\n", report)
                self.assertIn(f"self-check: audio delivery: {value}: failed\n", report)

    def test_a_typed_answer_reaches_no_report_and_no_file(self) -> None:
        # Something pasted by mistake at a y/N prompt must stay on the terminal: the report, which
        # is uploaded to oryx, carries only the answer's category.
        self.checkout("git", "typed-secret")
        self.advance_to_current()
        typed = f"synthetic-pasted-secret-{secrets.token_hex(8)}"
        result = self.invoke(after_sync=True, answers=f"{typed}\n" * 4)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertIn("self-check: browser page delivery: answer: not recognized twice: failed\n", report)
        self.assertIn("self-check: audio delivery: answer: not recognized twice: failed\n", report)
        searched = 0
        for root in (self.home, self.remote):
            for path in root.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    searched += 1
                    self.assertNotIn(typed, path.read_text(encoding="utf-8", errors="ignore"), path)
        self.assertGreater(searched, 0)

    def test_a_y_without_a_final_newline_counts_as_yes(self) -> None:
        self.checkout("git", "answer-without-newline")
        self.advance_to_current()
        result = self.invoke(after_sync=True, answers="y\ny")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("self-check: audio delivery: answer: yes: ok\n", self.report())

    def test_a_typed_yes_is_not_reported_as_no(self) -> None:
        # Sami may type "yes" at a [y/N] prompt. Whether the command takes it as yes or asks for
        # y, the report must not say he answered no.
        self.checkout("git", "answer-typed-yes")
        self.advance_to_current()
        self.invoke(after_sync=True, answers="yes\nYes\n")
        report = self.report()
        for probe in ("browser page delivery", "audio delivery"):
            with self.subTest(probe=probe):
                lines = [line for line in report.splitlines() if line.startswith(f"self-check: {probe}:")]
                self.assertEqual(len(lines), 1, report)
                self.assertNotIn("answer: no", lines[0])

    def test_yes_in_any_case_and_with_spaces_counts_as_yes(self) -> None:
        for name, answers in (("answer-yes-upper", "YES\nyes\n"), ("answer-y-spaced", "  y \n\tY\t\n")):
            with self.subTest(name):
                self.checkout("git", name)
                self.advance_to_current()
                result = self.invoke(after_sync=True, answers=answers)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                report = self.report()
                self.assertIn("self-check: browser page delivery: answer: yes: ok\n", report)
                self.assertIn("self-check: audio delivery: answer: yes: ok\n", report)

    def test_an_unrecognized_answer_is_asked_once_more_then_fails(self) -> None:
        # The page prompt reads two unrecognized answers and fails; the sound prompt then reads
        # the third line.
        self.checkout("git", "answer-unrecognized")
        self.advance_to_current()
        result = self.invoke(after_sync=True, answers="maybe\nperhaps\ny\n")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertIn("self-check: browser page delivery: answer: not recognized twice: failed\n", report)
        self.assertIn("self-check: audio delivery: answer: yes: ok\n", report)
        self.assertEqual(result.stderr.count("Did the forward test page open in the browser? [y/N] "), 2)
        self.assertEqual(result.stderr.count("Answer y or n.\n"), 1)

    def test_steps_stop_naming_a_failed_look_not_its_absence(self) -> None:
        for name, extra, message in (
            ("probe-ssh-down", {"SSH_FAILS_ON": "BatchMode=yes"},
             "oryx-tunnel-setup: probe: ssh -o BatchMode=yes oryx true failed (exit 255)"),
            ("cutover-look-fails", {"SSH_FAILS_ON": "test -x ~/.local/bin/oryx-edge-cutover"},
             "oryx-tunnel-setup: cutover: could not check for ~/.local/bin/oryx-edge-cutover through ssh oryx (exit 255)\n"),
            ("old-tunnel-look-fails", {"SSH_FAILS_ON": "sport = :12799"},
             "oryx-tunnel-setup: old-tunnel: could not check oryx port 12799 through ssh oryx (exit 255)\n"),
            ("ssh-g-fails", {"SSH_G_FAIL_FROM": "1"},
             "oryx-tunnel-setup: ssh-config: could not read effective Host oryx values: ssh -G oryx failed (exit 255)\n"),
            ("listeners-look-fails", {"SS_FAILS_ON": "-Hltnp"},
             "oryx-tunnel-setup: preflight: could not list the laptop's TCP listeners with ss -Hltnp (exit 1)\n"),
            ("cutover-run-fails", {"FAIL_STEP": "cutover"},
             "oryx-tunnel-setup: cutover: ssh -t oryx ~/.local/bin/oryx-edge-cutover exited 1 (255 is ssh's own failure); "
             "tr exited 0, tee exited 0\n"),
            ("cutover-missing", {"CUTOVER_MISSING": "1"},
             "oryx-tunnel-setup: cutover: ~/.local/bin/oryx-edge-cutover is missing or not executable; the parent installs it\n"),
            ("token-copy-fails", {"FAIL_STEP": "token"},
             "oryx-tunnel-setup: token: could not copy the Cloudflare Access tunnel token from oryx: "
             "ssh oryx secrets failed (exit 1)\n"),
            ("forward-installer-fails", {"XDG_CONFIG_HOME": str(self.root / "elsewhere")},
             "oryx-tunnel-setup: devbox-forward: forward.sh daemon failed (exit 1); its message is above\n"),
        ):
            with self.subTest(name):
                self.checkout("git", name)
                self.advance_to_current()
                result = self.invoke(after_sync=True, **extra)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(message, result.stderr)

    def test_sync_stop_names_the_failed_sync_and_its_status(self) -> None:
        self.checkout("git", "sync-fails")
        self.advance_to_current()
        self.command(self.git, "remote", "set-url", "origin", str(self.root / "no-such-origin.git"), cwd=self.checkout_dir, env=self.env)
        result = self.invoke()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertRegex(result.stderr, r"oryx-tunnel-setup: sync: dotfiles-sync --here failed \(exit [1-9][0-9]*\); ")

    def test_unit_wait_reports_what_is_active_last_said(self) -> None:
        self.checkout("git", "unit-wait-state")
        self.advance_to_current()
        result = self.invoke(after_sync=True, INACTIVE_UNIT="adb-server.service")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "units: adb-server.service: systemctl --user is-active printed inactive (exit 3) after 30 s\n", self.report()
        )

    def test_token_search_that_cannot_read_the_tree_names_grep_status(self) -> None:
        self.checkout("git", "leak-blind-status")
        self.advance_to_current()
        leaked = self.checkout_dir / "leaked.env"
        leaked.write_text(f"TUNNEL_SERVICE_TOKEN_SECRET={self.token_secret}\n", encoding="utf-8")
        leaked.chmod(0)
        try:
            result = self.invoke(after_sync=True)
        finally:
            leaked.chmod(0o600)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("token: copied; dotfiles search: UNKNOWN (grep exit 2)\n", self.report())

    def test_adb_probe_names_a_fail_reply_as_a_reply(self) -> None:
        self.checkout("git", "adb-replies-fail")
        self.advance_to_current()
        result = self.invoke(after_sync=True, ADB_REPLIES_FAIL="1")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("self-check: adb through tunnel: adb replied FAIL: FAIL0005nope: failed\n", self.report())

    def test_unit_link_failure_leaves_oryx_forward_unmoved(self) -> None:
        self.checkout("git", "before-move-unit")
        self.advance_to_current()
        unit = self.home / ".config/systemd/user/forward-tunnel@.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
        result = self.invoke(after_sync=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("move recorded", "\n".join(self.lines()))
        self.assertFalse((self.remote / "config.toml").exists())

    def test_adb_install_failure_leaves_oryx_forward_unmoved(self) -> None:
        self.checkout("git", "before-move-adb")
        self.advance_to_current()
        self.version_file.write_text("2026.10.7\n", encoding="utf-8")
        result = self.invoke(after_sync=True, PATH=str(self.path_without_adb()), FAIL_ADB_INSTALL="1")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("move recorded", "\n".join(self.lines()))
        self.assertFalse((self.remote / "config.toml").exists())

    def test_forward_port_retries_until_the_listener_is_ready(self) -> None:
        self.checkout("git", "forward-port-retry")
        self.advance_to_current()
        result = self.invoke(after_sync=True, FORWARD_CURL_FAIL_FIRST="1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.forward_curl_calls.read_text(encoding="utf-8").strip(), "2")

    def test_delivery_preview_and_page_remove_their_remote_directories(self) -> None:
        self.checkout("git", "delivery-cleanup")
        self.advance_to_current()
        result = self.invoke(after_sync=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.lines().count("remote delivery cleanup"), 2)

    def test_config_local_wildcard_changes_are_refused(self) -> None:
        self.checkout("git", "config-local-wildcard")
        self.advance_to_current()
        (self.home / ".ssh/config.local").write_text("Host *\n  Compression no\n", encoding="utf-8")
        result = self.invoke(after_sync=True)
        self.assert_stop_before_move(result, "ssh-config")

    def test_preflight_does_not_call_a_listener_foreign_when_systemctl_could_not_answer(self) -> None:
        # On a second run the unit's own ssh holds 127.0.0.1:12802. The preflight tells it from a
        # foreign listener by the units' MainPID; when systemctl --user show fails, nothing told
        # the listener apart, so the stop must say the look failed, not that it is not allowed.
        self.checkout("git", "preflight-systemctl-down")
        self.advance_to_current()
        holder = subprocess.Popen(["/bin/sleep", "60"])
        try:
            self.fake_ss(f'LISTEN 0      128    127.0.0.1:12802 0.0.0.0:* users:(("ssh",pid={holder.pid},fd=5))')
            result = self.invoke(after_sync=True, SYSTEMCTL_SHOW_FAILS="1")
        finally:
            holder.kill()
            holder.wait()
        self.assert_stop_before_move(result, "preflight")
        self.assertNotIn("is not an allowed forward process", result.stderr)

    def test_preflight_names_the_failed_look_for_a_foreign_listener_on_the_adb_port(self) -> None:
        # A process that is not adb holds 127.0.0.1:5037, and systemctl cannot say whether
        # adb-server.service started it: the stop names that failed look, not a foreign process.
        self.checkout("git", "preflight-adb-systemctl-down")
        self.advance_to_current()
        holder = subprocess.Popen(["/bin/sleep", "60"])
        try:
            self.fake_ss(f'LISTEN 0      128    127.0.0.1:5037 0.0.0.0:* users:(("sleep",pid={holder.pid},fd=5))')
            result = self.invoke(after_sync=True, SYSTEMCTL_SHOW_FAILS="1")
        finally:
            holder.kill()
            holder.wait()
        self.assert_stop_before_move(result, "preflight")
        self.assertIn(
            "oryx-tunnel-setup: preflight: could not tell whether the listener on 127.0.0.1:5037 is adb-server.service's: "
            "systemctl --user show -p MainPID adb-server.service failed (exit 1): ",
            result.stderr,
        )

    def test_units_step_does_not_stop_an_adb_server_systemctl_could_not_place(self) -> None:
        # An adb server holds 127.0.0.1:5037. When systemctl cannot say whether it is
        # adb-server.service's own, the command must not take it for a stray server and kill it.
        self.checkout("git", "units-adb-systemctl-down")
        self.advance_to_current()
        holder = subprocess.Popen(["/bin/bash", "-c", "exec -a adb /bin/sleep 60"])
        try:
            self.fake_ss(f'LISTEN 0      128    127.0.0.1:5037 0.0.0.0:* users:(("adb",pid={holder.pid},fd=5))')
            result = self.invoke(after_sync=True, SYSTEMCTL_SHOW_FAILS="1")
        finally:
            holder.kill()
            holder.wait()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            f"oryx-tunnel-setup: units: could not tell whether the adb server on 127.0.0.1:5037 (pid {holder.pid}) "
            "is adb-server.service's: systemctl --user show -p MainPID adb-server.service failed (exit 1)\n",
            result.stderr,
        )
        self.assertFalse([line for line in self.lines() if line.startswith("adb:") and "kill-server" in line])

    def test_preflight_accepts_the_devbox_daemons_on_other_addresses(self) -> None:
        # The laptop's devbox daemon binds forward's default ports on the laptop's tailnet address
        # (installers/forward.sh writes listen = <tailnet IP>), and a legacy pair binds 127.0.0.2.
        # Neither blocks the tunnel's binds on 127.0.0.1, so neither may stop the command.
        self.checkout("git", "devbox-daemon-listeners")
        self.advance_to_current()
        self.fake_ss(
            'LISTEN 0      4096   100.80.0.1:12800 0.0.0.0:* users:(("forward-daemon",pid=PID,fd=7))',
            'LISTEN 0      4096   100.80.0.1:12803 0.0.0.0:* users:(("forward-daemon",pid=PID,fd=8))',
            'LISTEN 0      4096   127.0.0.2:12802 0.0.0.0:* users:(("ssh",pid=PID,fd=5))',
        )
        result = self.invoke(after_sync=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ssh_config_check_passes_with_the_system_ssh_config(self) -> None:
        # On the laptop, `ssh -G <host>` reads ~/.ssh/config and then /etc/ssh/ssh_config, while
        # `ssh -F <file> -G <host>` skips the system file (ssh(1), -F). Stock Ubuntu's file sets
        # SendEnv, HashKnownHosts and GSSAPIAuthentication for Host *. This fake ssh reads the
        # system file for a plain -G, as the real one does.
        system = Path("/etc/ssh/ssh_config")
        self.checkout("git", "system-ssh-config")
        self.advance_to_current()
        bare = run(["/usr/bin/ssh", "-F", "/dev/null", "-G", "oryx"], cwd=self.home, env=self.env).stdout
        with_system = self.root / "system-only.conf"
        with_system.write_text(f"Match all\nInclude {system}\n", encoding="utf-8")
        if run(["/usr/bin/ssh", "-F", str(with_system), "-G", "oryx"], cwd=self.home, env=self.env).stdout == bare:
            self.skipTest(f"{system} on this host changes no ssh -G value")
        real = self.fakebin / "real"
        real.mkdir()
        (real / "ssh").symlink_to(self.fakebin / "fake")
        (self.fakebin / "ssh").unlink()
        write_executable(
            self.fakebin / "ssh",
            f'''if [[ "${{1:-}}" == -G ]]; then
    combined="$(/usr/bin/mktemp)"
    {{ /bin/cat "$HOME/.ssh/config"; printf '\\nMatch all\\nInclude %s\\n' "{system}"; }} > "$combined"
    status=0
    /usr/bin/ssh -F "$combined" -G "$2" || status=$?
    /bin/rm -f "$combined"
    exit "$status"
fi
exec "{real}/ssh" "$@"''',
        )
        result = self.invoke(after_sync=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
