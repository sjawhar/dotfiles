#!/usr/bin/env python3
"""scripts/hawk-token: keep a Hawk token cache warm without leaving a refresh behind.

Technique: every external command is a scratch stub first on PATH.  The stubs record one JSON
call record with their arguments, process identity and timing, and their controls are files whose
absolute paths are compiled into them because a refresh runs through ``env -i``.  The fixture
copies both the script and its user unit from ``HAWK_TOKEN_TEST_SOURCE`` (or this checkout), so
these tests also run unchanged against main as negative controls.  HOME, AGENTBOX_SRC, slots,
locks, caches, and the user-unit paths are all under one TemporaryDirectory; no test reads a Hawk
login, uses the network, or touches the machine's real token checkout.

What this pins: timer mode skips unreachable Hawk with one useful reason, sends errors but never
a token to its caller or journal, sweeps only old partial packs while holding its flock, waits six
hours after a failed refresh, preserves session calls, and keeps foreground work inside the user
unit that started it.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from secrets import token_bytes


CHECKOUT = Path(__file__).resolve().parents[2]
HOST = "hawk.test"
API_URL = f"https://{HOST}/api"


def make_token() -> str:
    """Build a fresh, realistically shaped signed token without committing one."""
    def b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    header = b64(json.dumps({"alg": "none", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64(json.dumps({"exp": int(time.time()) + 3600, "nonce": b64(token_bytes(24))},
                             separators=(",", ":")).encode())
    return f"{header}.{payload}.{b64(token_bytes(32))}"


def atomic_write(path: Path, text: str, mode: int = 0o755) -> None:
    """Write a fixture executable without a reader seeing a truncated stub."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(mode)
    os.replace(temporary, path)


def make_stub_source(log: Path, controls: Path, real_timeout: str, token: str) -> dict[str, str]:
    """Return the five fixture command stubs with absolute private control paths."""
    common = f'''import json, os, sys, time
from pathlib import Path
LOG = {str(log)!r}
CONTROLS = Path({str(controls)!r})

def control(name, default=None):
    path = CONTROLS / (name + ".json")
    if not path.exists():
        return {{}} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))

def record(name, argv, started, ended, **details):
    entry = {{"name": name, "argv": argv, "pid": os.getpid(), "pgid": os.getpgid(0),
             "start": started, "end": ended}}
    entry.update(details)
    with open(LOG, "a", encoding="utf-8") as file:
        file.write(json.dumps(entry, sort_keys=True) + "\\n")
'''
    mint = f'''#!/usr/bin/env python3
{common}
slot = Path(__file__).resolve().parents[2]
started = time.time()
kind = "validate" if os.path.realpath("/proc/self/fd/1") == "/dev/null" else "mint"
settings_path = slot / ".mint-control.json"
settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {{}}
lookup = control("lookup", {{"ok": True}}).get("ok", True)
status = 0
try:
    if not lookup:
        sys.stderr.write("Could not fetch config from Hawk: No address associated with hostname\\n")
        status = 1
    elif kind == "validate" and settings.get("validation_fail"):
        message = "error: " + {token!r} + " rejected\\n" if settings.get("validation_token") else "validation rejected\\n"
        sys.stderr.write(message)
        status = 1
    elif kind == "mint" and settings.get("mint_fail"):
        message = settings.get("mint_error")
        if message is None:
            message = "error: " + {token!r} + " rejected\\n" if settings.get("mint_token") else "mint rejected\\n"
        sys.stderr.write(message)
        stdout = settings.get("mint_stdout", "")
        if stdout:
            sys.stdout.write(stdout)
        status = int(settings.get("mint_status", 1))
    else:
        sys.stdout.write({token!r} + "\\n")
finally:
    record(kind, sys.argv[1:], started, time.time())
sys.exit(status)
'''
    git = f'''#!/usr/bin/env python3
{common}
MINT_SOURCE = {mint!r}
started = time.time()
argv = sys.argv[1:]
settings = control("git", {{}})
action = "fetch" if "fetch" in argv else "clone" if "clone" in argv else "reset" if "reset" in argv else "git"
active = CONTROLS / "active-git.json"
status = 0
paths_at_fetch = {{}}
try:
    if action == "fetch":
        paths_at_fetch = {{path: Path(path).exists() for path in settings.get("record_exists", [])}}
        pack = Path.cwd() / ".git" / "objects" / "pack"
        if settings.get("write_tmp"):
            pack.mkdir(parents=True, exist_ok=True)
            partial = pack / "tmp_pack_from_fetch"
            partial.write_text("partial", encoding="utf-8")
        active_temporary = active.with_name(f".{{active.name}}.{{os.getpid()}}.tmp")
        active_temporary.write_text(json.dumps({{"pid": os.getpid(), "pgid": os.getpgid(0), "start": started}}),
                                    encoding="utf-8")
        # Publish the record last because a test signals the stub once it sees it.
        os.replace(active_temporary, active)
        time.sleep(float(settings.get("fetch_sleep", 0)))
        if settings.get("rename_tmp"):
            partial = pack / "tmp_pack_from_fetch"
            if partial.exists():
                partial.rename(pack / "pack-from-fetch.pack")
        status = 1 if settings.get("fetch_fail") else 0
    elif action == "clone":
        destination = Path(argv[-1])
        if settings.get("clone_fail"):
            status = 1
        else:
            (destination / ".git" / "objects" / "pack").mkdir(parents=True, exist_ok=True)
            mint_path = destination / ".venv" / "bin" / "hawk-token"
            mint_path.parent.mkdir(parents=True, exist_ok=True)
            mint_path.write_text(MINT_SOURCE, encoding="utf-8")
            mint_path.chmod(0o755)
finally:
    if active.exists():
        active.unlink()
    record("git", argv, started, time.time(), paths_at_fetch=paths_at_fetch)
sys.exit(status)
'''
    uv = f'''#!/usr/bin/env python3
{common}
started = time.time()
status = 1 if control("uv", {{}}).get("fail") else 0
record("uv", sys.argv[1:], started, time.time())
sys.exit(status)
'''
    getent = f'''#!/usr/bin/env python3
{common}
started = time.time()
status = 0 if control("lookup", {{"ok": True}}).get("ok", True) else 2
record("getent", sys.argv[1:], started, time.time())
sys.exit(status)
'''
    curl = f'''#!/usr/bin/env python3
{common}
started = time.time()
settings = control("curl", {{}})
if "time" in settings:
    sys.stdout.write(str(settings["time"]))
record("curl", sys.argv[1:], started, time.time())
sys.exit(int(settings.get("rc", 0)))
'''
    timeout = f'''#!/usr/bin/env python3
{common}
started = time.time()
# timeout must replace this process so that the real timeout governs the mint and probe.
record("timeout", sys.argv[1:], started, time.time())
os.execv({real_timeout!r}, [{real_timeout!r}, *sys.argv[1:]])
'''
    return {"git": git, "uv": uv, "getent": getent, "curl": curl, "timeout": timeout, "mint": mint}


class HawkTokenFixture(unittest.TestCase):
    """A private copied script, unit, controls, slots, and observable command boundary."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.fixture = Path(self.temporary_directory.name)
        self.source = Path(os.environ.get("HAWK_TOKEN_TEST_SOURCE", str(CHECKOUT))).resolve()
        self.assertTrue((self.source / "scripts" / "hawk-token").is_file(), self.source)
        self.assertTrue((self.source / "omp" / "hawk-token-refresh.service").is_file(), self.source)

        self.dotfiles = self.fixture / "dotfiles"
        self.scripts = self.dotfiles / "scripts"
        self.omp = self.dotfiles / "omp"
        self.shims = self.dotfiles / "shims"
        self.home = self.fixture / "home"
        self.agentbox_src = self.fixture / "src"
        self.root = self.agentbox_src / ".hawk-token"
        self.controls = self.fixture / "controls"
        self.log = self.fixture / "calls.jsonl"
        for directory in (self.scripts, self.omp, self.shims, self.home, self.root, self.controls):
            directory.mkdir(parents=True, exist_ok=True)

        self.script = self.scripts / "hawk-token"
        self.unit = self.omp / "hawk-token-refresh.service"
        shutil.copy2(self.source / "scripts" / "hawk-token", self.script)
        shutil.copy2(self.source / "omp" / "hawk-token-refresh.service", self.unit)
        self.script.chmod(self.script.stat().st_mode | stat.S_IEXEC)

        self.token = make_token()
        self.real_timeout = shutil.which("timeout")
        self.assertIsNotNone(self.real_timeout, "timeout is required for this test fixture")
        for name, source in make_stub_source(self.log, self.controls, self.real_timeout, self.token).items():
            if name != "mint":
                atomic_write(self.shims / name, source)
        self.mint_source = make_stub_source(self.log, self.controls, self.real_timeout, self.token)["mint"]

        self.set_control("lookup", {"ok": True})
        self.set_control("curl", {"rc": 0, "time": "0.010000"})
        self.set_control("git", {})
        self.set_control("uv", {})
        self.environment = {
            "PATH": f"{self.shims}:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self.home),
            "AGENTBOX_SRC": str(self.agentbox_src),
            "HAWK_API_URL": API_URL,
            "USER": "hawk-test",
            "HOSTNAME": "hawk-test-host",
        }
        self.owned_processes: list[subprocess.Popen[str]] = []
        self.owned_process_groups: set[int] = set()

    def tearDown(self) -> None:
        for process_group in self.owned_process_groups:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for process in self.owned_processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        self.stop_fixture_processes()
        self.temporary_directory.cleanup()

    def set_control(self, name: str, value: dict) -> None:
        atomic_write(self.controls / f"{name}.json", json.dumps(value), 0o600)

    def slot(self, name: str) -> Path:
        return self.root / name

    def make_slot(self, name: str) -> Path:
        slot = self.slot(name)
        (slot / ".git" / "objects" / "pack").mkdir(parents=True, exist_ok=True)
        mint = slot / ".venv" / "bin" / "hawk-token"
        mint.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(mint, self.mint_source)
        self.set_mint_control(name, {})
        return slot

    def set_mint_control(self, name: str, value: dict) -> None:
        atomic_write(self.slot(name) / ".mint-control.json", json.dumps(value), 0o600)

    def make_live(self, *, fresh: bool = False) -> None:
        self.make_slot("agent-c")
        self.make_slot("agent-c.alt")
        current = self.root / "current"
        current.unlink(missing_ok=True)
        current.symlink_to("agent-c")
        if fresh:
            (self.root / ".refreshed").touch()

    def age(self, path: Path, seconds: int) -> None:
        then = time.time() - seconds
        os.utime(path, (then, then))

    def unit_settings(self) -> list[tuple[str, str]]:
        """Return this fixture's unit settings with specifiers substituted."""
        settings: list[tuple[str, str]] = []
        for line in self.unit.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.replace("%h/.dotfiles", str(self.dotfiles)).replace("%h", str(self.home))
            settings.append((key, value))
        return settings

    def timer_command(self) -> tuple[list[str], dict[str, str]]:
        values: dict[str, str] = {}
        execution = ""
        for key, value in self.unit_settings():
            if key == "ExecStart":
                execution = value
            elif key == "Environment":
                for assignment in shlex.split(value):
                    name, assigned = assignment.split("=", 1)
                    values[name] = assigned
        self.assertNotEqual(execution, "", "unit has no ExecStart")
        return shlex.split(execution), {**self.environment, **values}

    def timer_run(self, *, timeout: float = 45) -> subprocess.CompletedProcess[str]:
        command, environment = self.timer_command()
        return subprocess.run(command, cwd=self.fixture, env=environment, text=True,
                              capture_output=True, check=False, timeout=timeout)

    def session_call(self, *arguments: str, timeout: float = 45) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self.script), *arguments], cwd=self.fixture, env=self.environment,
                              text=True, capture_output=True, check=False, timeout=timeout)

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        # A stub may be appending while a wait_for polls: parse only lines its newline ended.
        complete = self.log.read_text(encoding="utf-8").split("\n")[:-1]
        return [json.loads(line) for line in complete]

    def named_calls(self, name: str) -> list[dict]:
        return [call for call in self.calls() if call["name"] == name]

    def git_calls(self, action: str) -> list[dict]:
        return [call for call in self.named_calls("git") if action in call["argv"]]

    def assert_call_order(self, *names: str) -> None:
        actual = [call["name"] for call in self.calls()]
        position = 0
        for name in names:
            try:
                position = actual.index(name, position) + 1
            except ValueError:
                self.fail(f"{name!r} missing from call order {actual!r}")

    def wait_for(self, predicate, description: str, timeout: float = 15) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        self.fail(f"timed out waiting for {description}")

    def active_fetch(self) -> dict:
        active = self.controls / "active-git.json"
        self.wait_for(active.exists, "the fetch stub to log its process")
        data = json.loads(active.read_text(encoding="utf-8"))
        command_line = Path(f"/proc/{data['pid']}/cmdline")
        self.assertTrue(command_line.exists(), f"fetch pid vanished before signal: {data}")
        self.assertIn(str(self.fixture), command_line.read_bytes().replace(b"\0", b" ").decode(errors="replace"))
        self.owned_process_groups.add(data["pgid"])
        return data

    def stop_fixture_processes(self) -> None:
        """Stop only exact PIDs whose command line proves this test fixture started them."""
        for signal_number in (signal.SIGTERM, signal.SIGKILL):
            for pid, _ in self.fixture_processes():
                try:
                    os.kill(pid, signal_number)
                except ProcessLookupError:
                    pass
            if not self.fixture_processes():
                return
            time.sleep(0.05)

    def fixture_processes(self) -> list[tuple[int, str]]:
        """Return only processes whose command line names this test's private fixture."""
        leftovers: list[tuple[int, str]] = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            command_line = entry / "cmdline"
            try:
                command = command_line.read_bytes().replace(b"\0", b" ").decode(errors="replace")
            except FileNotFoundError:
                continue
            if str(self.fixture) in command:
                leftovers.append((int(entry.name), command))
        return leftovers

    def assert_no_fixture_processes(self) -> None:
        leftovers = [f"{pid}:{command}" for pid, command in self.fixture_processes()]
        self.assertEqual(leftovers, [], "fixture process left behind")

    def pack(self, slot: str) -> Path:
        return self.slot(slot) / ".git" / "objects" / "pack"


class SkipWhenUnreachable(HawkTokenFixture):
    """Timer reachability skips have one reason, no build, and no token mint."""

    def test_name_that_does_not_resolve_skips_without_work(self) -> None:
        self.make_live()
        self.set_control("lookup", {"ok": False})

        result = self.timer_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, f"hawk-token: skipped: {HOST} does not resolve (no route to Hawk)\n")
        self.assertEqual([call["name"] for call in self.calls()], ["timeout", "getent"])

    def test_connection_refused_skips_without_work(self) -> None:
        self.make_live()
        self.set_control("curl", {"rc": 7, "time": "0.000000"})

        result = self.timer_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, f"hawk-token: skipped: no connection to {HOST} opens (no route to Hawk)\n")
        self.assertEqual([call["name"] for call in self.calls()], ["timeout", "getent", "curl"])

    def test_connect_timeout_before_connection_skips_without_work(self) -> None:
        self.make_live()
        self.set_control("curl", {"rc": 28, "time": "0.000000"})

        result = self.timer_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr,
                         f"hawk-token: skipped: no connection to {HOST} opens within 5 s (no route to Hawk)\n")
        self.assertEqual([call["name"] for call in self.calls()], ["timeout", "getent", "curl"])

    def test_timeout_after_connection_and_tls_failure_mint_once(self) -> None:
        for control in ({"rc": 28, "time": "0.100000"}, {"rc": 35, "time": ""}):
            with self.subTest(control=control):
                self.make_live(fresh=True)
                self.set_control("curl", control)
                before = len(self.named_calls("mint"))

                result = self.timer_run()

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(len(self.named_calls("mint")), before + 1)
                self.assert_call_order("timeout", "getent", "curl", "timeout", "mint")
                self.root.joinpath("current").unlink()
                self.root.joinpath(".refreshed").unlink(missing_ok=True)
                shutil.rmtree(self.slot("agent-c"))
                shutil.rmtree(self.slot("agent-c.alt"))
                self.log.unlink(missing_ok=True)

    def test_next_reachable_run_mints_and_rewrites_the_cache(self) -> None:
        self.make_live(fresh=True)
        self.set_control("lookup", {"ok": False})
        skipped = self.timer_run()
        self.assertEqual(skipped.returncode, 0)
        self.set_control("lookup", {"ok": True})

        result = self.timer_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(self.named_calls("mint")), 1)
        self.assertEqual((self.root / "last-good-token").read_text(encoding="utf-8").strip(), self.token)
        self.assert_call_order("getent", "getent", "curl", "timeout", "mint")

    def test_empty_root_with_no_route_does_not_clone_or_mint(self) -> None:
        self.set_control("lookup", {"ok": False})

        result = self.timer_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, f"hawk-token: skipped: {HOST} does not resolve (no route to Hawk)\n")
        self.assertEqual(self.git_calls("clone"), [])
        self.assertEqual(self.named_calls("mint"), [])
        self.assertEqual([call["name"] for call in self.calls()], ["timeout", "getent"])


class NoTokenInOutput(HawkTokenFixture):
    """Timer output retains useful mint errors but never the minted value."""

    def test_success_has_no_stdout_writes_a_private_cache_and_leaks_no_token(self) -> None:
        self.make_live(fresh=True)

        result = self.timer_run()

        cache = self.root / "last-good-token"
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNotIn(self.token, result.stderr)
        self.assertEqual(cache.read_text(encoding="utf-8").strip(), self.token)
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o600)
        self.assertEqual([call["name"] for call in self.calls()], ["timeout", "getent", "curl", "timeout", "mint"])

    def test_failed_mint_redacts_the_token_but_keeps_its_error_words(self) -> None:
        self.make_live(fresh=True)
        self.set_mint_control("agent-c", {"mint_fail": True, "mint_token": True})

        result = self.timer_run()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("mint failed; its stderr follows, tokens redacted", result.stderr)
        self.assertIn("error:", result.stderr)
        self.assertIn("rejected", result.stderr)
        self.assertNotIn(self.token, result.stderr)
        self.assertEqual(len(self.named_calls("mint")), 1)

    def test_failed_mint_never_serves_an_existing_cache(self) -> None:
        self.make_live(fresh=True)
        cache = self.root / "last-good-token"
        cache.write_text(self.token + "\n", encoding="utf-8")
        cache.chmod(0o600)
        self.set_mint_control("agent-c", {"mint_fail": True, "mint_token": True})

        result = self.timer_run()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(cache.read_text(encoding="utf-8").strip(), self.token)
        self.assertNotIn(self.token, result.stderr)
        self.assertEqual(len(self.named_calls("mint")), 1)

    def test_nonzero_mint_with_stdout_keeps_existing_cache_and_fails(self) -> None:
        self.make_live(fresh=True)
        cache = self.root / "last-good-token"
        before = (self.token + "\n").encode("utf-8")
        cache.write_bytes(before)
        cache.chmod(0o600)
        self.set_mint_control("agent-c", {
            "mint_fail": True,
            "mint_stdout": "partial-not-a-token\n",
            "mint_error": "mint transport error\n",
            "mint_status": 3,
        })

        result = self.timer_run()

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("mint transport error", result.stderr)
        self.assertNotIn("minted", result.stderr)
        self.assertEqual(cache.read_bytes(), before)

    def test_failed_validation_is_redacted_and_the_live_slot_still_mints(self) -> None:
        self.make_live()
        self.set_mint_control("agent-c.alt", {"validation_fail": True, "validation_token": True})

        result = self.timer_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("validation mint failed:", result.stderr)
        self.assertNotIn(self.token, result.stderr)
        self.assertEqual(len(self.named_calls("validate")), 1)
        self.assertEqual(len(self.named_calls("mint")), 1)
        self.assertEqual(os.readlink(self.root / "current"), "agent-c")
        self.assert_call_order("git", "uv", "validate", "timeout", "mint")


class SweepAndForeground(HawkTokenFixture):
    """The sweep has precise age boundaries and a refresh cannot outlive its caller."""

    def make_boundary_pack(self, slot: str) -> dict[str, Path]:
        pack = self.pack(slot)
        paths = {
            "minute": pack / "tmp_pack_one-minute",
            "eleven": pack / "tmp_idx_eleven-minutes",
            "590": pack / "tmp_rev_590-seconds",
            "610": pack / "tmp_pack_610-seconds",
            "other": pack / "other-old",
            "pack": pack / "pack-old.pack",
            "index": pack / "pack-old.idx",
        }
        for path in paths.values():
            path.write_text(path.name, encoding="utf-8")
        self.age(paths["minute"], 60)
        self.age(paths["eleven"], 660)
        self.age(paths["590"], 590)
        self.age(paths["610"], 610)
        self.age(paths["other"], 660)
        self.age(paths["pack"], 10 * 24 * 60 * 60)
        self.age(paths["index"], 10 * 24 * 60 * 60)
        return paths

    def assert_boundary_result(self, paths: dict[str, Path]) -> None:
        self.assertTrue(paths["minute"].exists())
        self.assertFalse(paths["eleven"].exists())
        self.assertTrue(paths["590"].exists())
        self.assertFalse(paths["610"].exists())
        self.assertTrue(paths["other"].exists())
        self.assertTrue(paths["pack"].exists())
        self.assertTrue(paths["index"].exists())

    def test_sweep_uses_the_600_second_boundary_in_both_slots_and_before_a_skip(self) -> None:
        self.make_live(fresh=True)
        first = self.make_boundary_pack("agent-c")
        second = self.make_boundary_pack("agent-c.alt")

        result = self.timer_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_boundary_result(first)
        self.assert_boundary_result(second)
        self.assertIn("removed 2 partial pack files older than 10 minutes from agent-c", result.stderr)
        self.assertIn("removed 2 partial pack files older than 10 minutes from agent-c.alt", result.stderr)
        self.assertEqual(self.git_calls("fetch"), [])

        skipped_old = self.pack("agent-c") / "tmp_pack_skip-old"
        skipped_old.write_text("partial", encoding="utf-8")
        self.age(skipped_old, 610)
        self.set_control("lookup", {"ok": False})
        skipped = self.timer_run()

        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertFalse(skipped_old.exists())
        self.assertIn("removed 1 partial pack files older than 10 minutes from agent-c", skipped.stderr)
        self.assertEqual(len(self.named_calls("mint")), 1)

    def test_a_held_lock_prevents_sweep_and_refresh_but_still_mints(self) -> None:
        self.make_live()
        old = self.pack("agent-c") / "tmp_pack_held-lock"
        old.write_text("partial", encoding="utf-8")
        self.age(old, 660)
        lock = self.root / ".refresh.lock"
        flock = shutil.which("flock")
        self.assertIsNotNone(flock, "flock is required on oryx")
        holder = subprocess.Popen([flock, "-n", str(lock), "/bin/sh", "-c", "sleep 30"],
                                  text=True, start_new_session=True)
        self.owned_processes.append(holder)
        self.wait_for(lambda: subprocess.run([flock, "-n", str(lock), "true"], check=False,
                                             capture_output=True).returncode == 1,
                      "the test flock to hold the refresh lock")

        result = self.timer_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(old.exists())
        self.assertEqual(self.git_calls("fetch"), [])
        self.assertEqual(len(self.named_calls("mint")), 1)
        self.assertIn("another refresh holds the lock; no sweep and no refresh this run", result.stderr)

    def test_five_second_fetch_finishes_before_the_timer_returns_and_leaves_no_process(self) -> None:
        self.make_live()
        self.set_control("git", {"fetch_sleep": 5, "write_tmp": True})

        result = self.timer_run(timeout=30)

        self.assertEqual(result.returncode, 0, result.stderr)
        fetch = self.git_calls("fetch")
        self.assertEqual(len(fetch), 1)
        self.assertGreaterEqual(time.time(), fetch[0]["end"])
        self.assertEqual(os.readlink(self.root / "current"), "agent-c.alt")
        self.assert_no_fixture_processes()
        self.assert_call_order("git", "uv", "validate", "timeout", "mint")

    def test_ten_second_fetch_does_not_spend_the_timer_mints_own_budget(self) -> None:
        self.make_live()
        self.set_control("git", {"fetch_sleep": 10})

        result = self.timer_run(timeout=35)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.git_calls("fetch")), 1)
        self.assertEqual(len(self.named_calls("mint")), 1)
        self.assertEqual((self.root / "last-good-token").read_text(encoding="utf-8").strip(), self.token)
        self.assert_no_fixture_processes()

    def test_session_started_refresh_sweeps_old_files_after_it_detaches(self) -> None:
        self.make_live()
        old = self.pack("agent-c") / "tmp_pack_session-old"
        old.write_text("partial", encoding="utf-8")
        self.age(old, 660)

        result = self.session_call()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.token + "\n")
        self.wait_for(lambda: len(self.git_calls("fetch")) == 1, "the detached refresh fetch")
        self.wait_for(lambda: not old.exists(), "the detached refresh sweep")
        self.wait_for(lambda: os.readlink(self.root / "current") == "agent-c.alt", "the detached refresh publish")
        self.wait_for(lambda: not self.fixture_processes(), "the detached refresh to exit")

    def test_first_run_sweeps_existing_slot_before_a_failed_fetch(self) -> None:
        pack = self.pack("agent-c")
        pack.mkdir(parents=True, exist_ok=True)
        old_pack = pack / "tmp_pack_first-run-old"
        old_index = pack / "tmp_idx_first-run-old"
        fresh = pack / "tmp_pack_first-run-fresh"
        for path in (old_pack, old_index, fresh):
            path.write_text("partial", encoding="utf-8")
        self.age(old_pack, 2 * 60 * 60)
        self.age(old_index, 2 * 60 * 60)
        self.age(fresh, 60)
        self.set_control("git", {
            "fetch_fail": True,
            "record_exists": [str(old_pack), str(old_index), str(fresh)],
        })

        result = self.session_call()

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(old_pack.exists())
        self.assertFalse(old_index.exists())
        self.assertTrue(fresh.exists())
        fetch = self.git_calls("fetch")
        self.assertEqual(len(fetch), 1)
        self.assertEqual(fetch[0]["paths_at_fetch"], {
            str(old_pack): False,
            str(old_index): False,
            str(fresh): True,
        })

    def test_next_sweep_removes_a_partial_pack_left_by_a_killed_fetch(self) -> None:
        self.make_live()
        self.set_control("git", {"fetch_sleep": 20, "write_tmp": True})
        command, environment = self.timer_command()
        process = subprocess.Popen(command, cwd=self.fixture, env=environment, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        self.owned_processes.append(process)
        active = self.active_fetch()
        self.assertEqual(active["pgid"], process.pid)
        os.killpg(active["pgid"], signal.SIGKILL)
        process.communicate(timeout=10)
        partial = self.pack("agent-c.alt") / "tmp_pack_from_fetch"
        self.wait_for(partial.exists, "the killed fetch partial pack")
        self.age(partial, 660)
        self.set_control("git", {})

        result = self.timer_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(partial.exists())
        self.assertEqual(self.git_calls("fetch"), [])


class WaitAfterFailure(HawkTokenFixture):
    """Refresh failures delay every entry point, including signals and first bootstraps."""

    def test_failed_refresh_waits_six_hours_then_success_uses_the_success_stamp(self) -> None:
        self.make_live()
        self.set_control("git", {"fetch_fail": True})

        first = self.timer_run()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(len(self.git_calls("fetch")), 1)
        self.assertTrue((self.root / ".refresh-failed").exists())
        for caller in (self.timer_run, self.session_call, self.timer_run):
            result = caller()
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.git_calls("fetch")), 1)

        self.set_control("git", {})
        self.age(self.root / ".refresh-started", 6 * 60 * 60 + 60)
        self.age(self.root / ".refresh-failed", 6 * 60 * 60 + 60)
        recovered = self.timer_run()
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(len(self.git_calls("fetch")), 2)

        current = self.timer_run()
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertEqual(len(self.git_calls("fetch")), 2)
        self.age(self.root / ".refreshed", 6 * 60 * 60 + 60)
        self.age(self.root / ".refresh-started", 6 * 60 * 60 + 120)
        stale = self.timer_run()
        self.assertEqual(stale.returncode, 0, stale.stderr)
        self.assertEqual(len(self.git_calls("fetch")), 3)

    def test_failure_stamp_is_written_after_the_slow_fetch_ends(self) -> None:
        self.make_live()
        self.set_control("git", {"fetch_sleep": 3, "fetch_fail": True})

        result = self.timer_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        fetch = self.git_calls("fetch")
        self.assertEqual(len(fetch), 1)
        self.assertGreaterEqual((self.root / ".refresh-failed").stat().st_mtime, fetch[0]["end"])

    def test_recent_failure_overrides_an_older_started_stamp(self) -> None:
        self.make_live()
        started = self.root / ".refresh-started"
        failed = self.root / ".refresh-failed"
        started.touch()
        failed.touch()
        self.age(started, 6 * 60 * 60 + 30 * 60)
        self.age(failed, 5 * 60 * 60 + 59 * 60)

        result = self.timer_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git_calls("fetch"), [])

    def test_sigterm_of_a_detached_refresh_records_failure_at_the_signal(self) -> None:
        self.make_live()
        self.set_control("git", {"fetch_sleep": 20})

        session = self.session_call()
        self.assertEqual(session.returncode, 0, session.stderr)
        active = self.active_fetch()
        signal_marker = self.root / ".term-sent"
        signal_marker.touch()
        os.killpg(active["pgid"], signal.SIGTERM)
        self.wait_for(lambda: (self.root / ".refresh-failed").exists(), "SIGTERM failure stamp")
        self.assertGreaterEqual((self.root / ".refresh-failed").stat().st_mtime_ns,
                                signal_marker.stat().st_mtime_ns)
        self.wait_for(lambda: not self.fixture_processes(), "the signalled detached refresh to exit")

    def test_sigkill_is_repaired_before_another_fetch_and_then_can_expire(self) -> None:
        self.make_live()
        self.set_control("git", {"fetch_sleep": 20})

        session = self.session_call()
        self.assertEqual(session.returncode, 0, session.stderr)
        active = self.active_fetch()
        os.killpg(active["pgid"], signal.SIGKILL)
        self.wait_for(lambda: not Path(f"/proc/{active['pid']}").exists(), "SIGKILLed fetch exit")
        next_start = time.time()
        repaired = self.timer_run()
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        failed = self.root / ".refresh-failed"
        self.assertTrue(failed.exists())
        self.assertGreaterEqual(failed.stat().st_mtime, next_start)
        self.assertEqual(self.git_calls("fetch"), [])

        self.set_control("git", {})
        self.age(self.root / ".refresh-started", 6 * 60 * 60 + 60)
        self.age(failed, 6 * 60 * 60 + 60)
        expired = self.timer_run()
        self.assertEqual(expired.returncode, 0, expired.stderr)
        self.assertEqual(len(self.git_calls("fetch")), 1)

    def test_failed_first_run_clone_waits_before_retrying(self) -> None:
        self.set_control("git", {"clone_fail": True})

        first = self.timer_run()
        self.assertNotEqual(first.returncode, 0)
        self.assertEqual(len(self.git_calls("clone")), 1)
        second = self.timer_run()
        third = self.timer_run()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertEqual(len(self.git_calls("clone")), 1)

        self.set_control("git", {})
        self.age(self.root / ".refresh-started", 6 * 60 * 60 + 60)
        self.age(self.root / ".refresh-failed", 6 * 60 * 60 + 60)
        retry = self.timer_run()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(len(self.git_calls("clone")), 2)


class SessionCalls(HawkTokenFixture):
    """Existing session calls keep their stdout and argument/cache contract."""

    def test_plain_session_call_prints_the_token_and_writes_the_cache(self) -> None:
        self.make_live(fresh=True)

        result = self.session_call()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.token + "\n")
        self.assertEqual((self.root / "last-good-token").read_text(encoding="utf-8").strip(), self.token)
        self.assertEqual([call["name"] for call in self.calls()], ["timeout", "mint"])

    def test_session_argument_reaches_mint_without_overwriting_the_cache(self) -> None:
        self.make_live(fresh=True)
        cache = self.root / "last-good-token"
        cache.write_text("cache-before-argument\n", encoding="utf-8")
        cache.chmod(0o600)

        result = self.session_call("--audience", "other")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.token + "\n")
        self.assertEqual(cache.read_text(encoding="utf-8"), "cache-before-argument\n")
        mints = self.named_calls("mint")
        self.assertEqual(len(mints), 1)
        self.assertEqual(mints[0]["argv"][-2:], ["--audience", "other"])
        self.assertEqual([call["name"] for call in self.calls()], ["timeout", "mint"])


class UnderSystemd(HawkTokenFixture):
    """The copied user unit keeps the real timer behaviour and journal boundary."""

    unit_counter = 0

    def setUp(self) -> None:
        super().setUp()
        manager = subprocess.run(["systemctl", "--user", "show-environment"], text=True,
                                 capture_output=True, check=False)
        if manager.returncode != 0:
            self.fail("no systemd user manager: run UnderSystemd on oryx")
        self.transient_units: list[str] = []

    def tearDown(self) -> None:
        for unit in getattr(self, "transient_units", []):
            subprocess.run(["systemctl", "--user", "stop", unit], text=True, capture_output=True, check=False)
            subprocess.run(["systemctl", "--user", "reset-failed", unit], text=True, capture_output=True, check=False)
        super().tearDown()

    def systemd_timer(self, *, timeout: float = 50) -> tuple[subprocess.CompletedProcess[str], str]:
        command, environment = self.timer_command()
        service_properties: list[tuple[str, str]] = []
        wanted = {"Type", "StandardOutput", "StandardError", "ExecCondition", "TimeoutStartSec", "KillMode"}
        for key, value in self.unit_settings():
            if key in wanted:
                service_properties.append((key, value))
            elif key == "Environment":
                for assignment in shlex.split(value):
                    service_properties.append(("Environment", assignment))
        service_properties.extend((("Environment", f"HOME={self.home}"),
                                   ("Environment", f"AGENTBOX_SRC={self.agentbox_src}")))
        self.assertFalse(any("%" in value for _, value in service_properties))
        self.assertFalse(any("%" in item for item in command))
        type(self).unit_counter += 1
        unit = f"hawk-token-test-{os.getpid()}-{type(self).unit_counter}"
        self.transient_units.append(unit)
        systemd_command = ["systemd-run", "--user", "--wait", f"--unit={unit}"]
        for key, value in service_properties:
            systemd_command.extend(("-p", f"{key}={value}"))
        systemd_command.extend(("--", *command))
        parent_environment = os.environ.copy()
        parent_environment.update(environment)
        result = subprocess.run(systemd_command, text=True, capture_output=True, check=False,
                                env=parent_environment, timeout=timeout)
        return result, unit

    def journal(self, invocation: str, expected: str) -> str:
        last = ""
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = subprocess.run(["journalctl", "--user", f"_SYSTEMD_INVOCATION_ID={invocation}",
                                     "-o", "cat", "--no-pager"], text=True, capture_output=True, check=False)
            last = result.stdout
            if expected in last:
                return last
            time.sleep(0.1)
        self.fail(f"journal for {invocation} did not contain {expected!r}: {last!r}")

    def success_invocation(self, result: subprocess.CompletedProcess[str]) -> str:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("Finished with result: success", output)
        match = re.search(r"invocation ID: ([0-9a-f]+)", output)
        self.assertIsNotNone(match, output)
        return match.group(1)  # type: ignore[union-attr]

    def test_unreachable_timer_succeeds_and_journals_its_reason_not_a_token(self) -> None:
        self.make_live(fresh=True)
        self.set_control("lookup", {"ok": False})

        result, _ = self.systemd_timer()

        invocation = self.success_invocation(result)
        journal = self.journal(invocation, "skipped: api.hawk.internal.trajectorylabs.com does not resolve")
        self.assertNotIn(self.token, journal)
        self.assertEqual(self.named_calls("mint"), [])

    def test_foreground_refresh_outlives_its_fetch_and_renames_the_partial_pack(self) -> None:
        self.make_live()
        self.set_control("git", {"fetch_sleep": 5, "write_tmp": True, "rename_tmp": True})

        result, _ = self.systemd_timer(timeout=35)

        self.success_invocation(result)
        fetch = self.git_calls("fetch")
        self.assertEqual(len(fetch), 1)
        self.assertGreaterEqual(time.time(), fetch[0]["end"])
        pack = self.pack("agent-c.alt")
        self.assertTrue((pack / "pack-from-fetch.pack").exists())
        self.assertFalse((pack / "tmp_pack_from_fetch").exists())
        self.assert_no_fixture_processes()

    def test_failed_mint_keeps_the_unit_loaded_failed_and_redacts_its_journal(self) -> None:
        self.make_live(fresh=True)
        self.set_mint_control("agent-c", {"mint_fail": True, "mint_token": True})

        result, unit = self.systemd_timer()

        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr
        self.assertIn(f"Job for {unit}.service failed", output)
        shown = subprocess.run(["systemctl", "--user", "show", unit, "-p", "LoadState", "-p", "ActiveState",
                                "-p", "Result", "-p", "InvocationID"], text=True, capture_output=True, check=False)
        values = dict(line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line)
        self.assertEqual(values.get("LoadState"), "loaded", shown.stdout)
        self.assertEqual(values.get("ActiveState"), "failed", shown.stdout)
        self.assertEqual(values.get("Result"), "exit-code", shown.stdout)
        invocation = values.get("InvocationID")
        self.assertIsNotNone(invocation, shown.stdout)
        journal = self.journal(invocation or "", "error:")
        self.assertIn("rejected", journal)
        self.assertNotIn(self.token, journal)


if __name__ == "__main__":
    unittest.main()
