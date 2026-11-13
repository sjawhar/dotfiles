#!/usr/bin/env python3
"""The laptop command is safe to bootstrap from an old git or colocated jj checkout."""

from __future__ import annotations

import os
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
            exec /usr/bin/ssh -F "$HOME/.ssh/config" -G "$2"
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
            printf 'open failed\n' >&2
            /usr/bin/python3 - "$port" <<'PY' &
import socket
import sys
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", int(sys.argv[1])))
sock.listen(1)
conn, _ = sock.accept()
conn.close()
sock.close()
PY
            child=$!
            trap 'kill "$child" 2>/dev/null || true; exit 0' TERM INT
            wait "$child"
            exit 0
        fi
        if [[ "$joined" == *"oryx-probe"* ]]; then
            [[ "$joined" == *"127.0.0.1:12999"* || "$joined" == *" -W 127.0.0.1:22 "* || "$joined" == *" -R /"* ]] && exit 255
            if [[ "$joined" == *" id "* ]]; then
                printf 'oryx-tunnel: forwarding-only key\n'
            fi
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
            for _ in {1..20}; do
                if /usr/bin/curl --noproxy '*' -fsS "$url" 2>/dev/null; then
                    exit 0
                fi
                /bin/sleep 0.01
            done
            exit 1
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
        if [[ "${1:-}" == install && " $* " == *" cloudflared="* ]]; then
            printf '%s\n' "$CF_VERSION" > "$CF_VERSION_FILE"
        fi
        ;;
    systemctl)
        if [[ " $* " == *" is-active "* ]]; then
            unit="${!#}"
            if [[ "${INACTIVE_UNIT:-}" == "$unit" ]]; then
                [[ " $* " == *" --quiet "* ]] || printf 'inactive\n'
                exit 3
            fi
            [[ " $* " == *" --quiet "* ]] || printf 'active\n'
        elif [[ " $* " == *" show -p MainPID "* ]]; then
            printf '0\n'
        fi
        ;;
    journalctl)
        printf 'fake journal\n'
        ;;
    ss)
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

    def invoke(self, *, readme: bool = False, after_sync: bool = False, **extra: str) -> subprocess.CompletedProcess[str]:
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
            input="y\ny\n",
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
        """Make the fake ss print these `ss -Hltnp` lines; PID becomes the fake's own pid."""
        (self.fakebin / "ss").unlink()
        text = "\n".join(lines)
        write_executable(
            self.fakebin / "ss",
            f'if [[ " $* " == *" -Hltnp "* ]]; then\n/bin/sed "s/PID/$$/" <<\'LINES\'\n{text}\nLINES\nfi',
        )

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


if __name__ == "__main__":
    unittest.main()
