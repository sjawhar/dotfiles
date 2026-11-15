#!/usr/bin/env python3
"""Cover per-session Go build caches and their one-pass removals (HOME-234)."""

from __future__ import annotations

import os
import select
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SWEEP = DOTFILES / "scripts" / "go-build-session-sweep"
AGENT_SESSION = DOTFILES / "scripts" / "agent-session"
OMP_SHIM = DOTFILES / "shims" / "omp"
BOOT = "01234567-89ab-cdef-0123-456789abcdef"
OTHER_BOOT = "fedcba98-7654-3210-fedc-ba9876543210"


def write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def session_name(pid: int, start: str, boot: str = BOOT) -> str:
    return f"{pid}-{start}-{boot}"


def stat_record(start: str, command: str = "agent") -> str:
    """Return /proc/<pid>/stat text whose field 22 is start."""
    return f"123 ({command}) S {' '.join(['0'] * 18)} {start}\n"


def process_start(pid: int) -> str:
    stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return stat_text.rpartition(") ")[2].split()[19]


class ScratchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="go-build-session-test-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = self.root / "home"
        self.cache_home = self.root / "cache"
        self.config_home = self.root / "config"
        for path in (self.home, self.cache_home, self.config_home):
            path.mkdir()
        self.base_env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "XDG_CACHE_HOME": str(self.cache_home),
            "XDG_CONFIG_HOME": str(self.config_home),
        }

    def assert_exit(self, result: subprocess.CompletedProcess[str], want: int) -> None:
        self.assertEqual(result.returncode, want, result.stderr)

    def run_process(self, argv: list[str], env: dict[str, str] | None = None,
                    timeout: int = 20) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            cwd=self.root,
            env={**self.base_env, **(env or {})},
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )


class GoBuildSessionSweepTest(ScratchTest):
    def make_proc(self) -> Path:
        proc = self.root / "proc"
        (proc / "sys" / "kernel" / "random").mkdir(parents=True)
        (proc / "sys" / "kernel" / "random" / "boot_id").write_text(f"{BOOT}\n", encoding="utf-8")
        (proc / "self").mkdir()
        (proc / "self" / "stat").write_text(stat_record("1"), encoding="utf-8")
        return proc

    def write_process_stat(self, proc: Path, pid: int, start: str, command: str = "agent") -> Path:
        directory = proc / str(pid)
        directory.mkdir(exist_ok=True)
        path = directory / "stat"
        path.write_text(stat_record(start, command), encoding="utf-8")
        return path

    def run_sweep(self, root: Path, proc: Path) -> subprocess.CompletedProcess[str]:
        return self.run_process([str(SWEEP), str(root)], {"GO_BUILD_SESSION_SWEEP_PROC": str(proc)})

    def test_sweep_removes_dead_entries_and_keeps_unproven_or_unrelated_entries(self) -> None:
        cache_root = self.root / "go-build-session"
        cache_root.mkdir()
        proc = self.make_proc()

        another_boot = cache_root / session_name(11, "22", OTHER_BOOT)
        missing_process = cache_root / session_name(12, "23")
        changed_process = cache_root / session_name(13, "24")
        live = cache_root / session_name(14, "25")
        unreadable = cache_root / session_name(15, "26")
        short_stat = cache_root / session_name(16, "27")
        for path in (another_boot, missing_process, changed_process, live, unreadable, short_stat):
            path.mkdir()
        self.write_process_stat(proc, 11, "22")
        self.write_process_stat(proc, 13, "99", "a) b")
        self.write_process_stat(proc, 14, "25")
        # A directory where the stat file belongs: reading it fails for root too, unlike mode 000.
        (proc / "15" / "stat").mkdir(parents=True)
        short_directory = proc / "16"
        short_directory.mkdir()
        (short_directory / "stat").write_text("short\n", encoding="utf-8")

        keep_name = cache_root / "keep-me"
        missing_boot = cache_root / "17-28"
        leading_pid = cache_root / f"018-29-{BOOT}"
        leading_start = cache_root / f"19-030-{BOOT}"
        upper_boot = cache_root / session_name(20, "31", BOOT.upper())
        for path in (keep_name, missing_boot, leading_pid, leading_start, upper_boot):
            path.mkdir()
        regular_file = cache_root / session_name(21, "32")
        regular_file.write_text("keep\n", encoding="utf-8")
        outside = self.root / "outside"
        outside.mkdir()
        outside_file = outside / "marker"
        outside_file.write_text("keep\n", encoding="utf-8")
        symlink = cache_root / session_name(22, "33")
        symlink.symlink_to(outside, target_is_directory=True)

        result = self.run_sweep(cache_root, proc)
        self.assert_exit(result, 0)
        for path in (another_boot, missing_process, changed_process):
            self.assertFalse(path.exists(), path)
        for path in (live, unreadable, short_stat, keep_name, missing_boot, leading_pid, leading_start, upper_boot):
            self.assertTrue(path.is_dir(), path)
        self.assertTrue(regular_file.is_file())
        self.assertTrue(symlink.is_symlink())
        self.assertTrue(outside_file.is_file())

    def test_sweep_keeps_everything_when_global_evidence_is_unreadable(self) -> None:
        cache_root = self.root / "go-build-session"
        cache_root.mkdir()
        proc = self.make_proc()
        dead = cache_root / session_name(29, "40")
        dead.mkdir()

        boot_id = proc / "sys" / "kernel" / "random" / "boot_id"
        boot_id.unlink()
        result = self.run_sweep(cache_root, proc)
        self.assert_exit(result, 0)
        self.assertTrue(dead.is_dir())

        boot_id.write_text(f"{BOOT}\n", encoding="utf-8")
        self_stat = proc / "self" / "stat"
        self_stat.unlink()
        result = self.run_sweep(cache_root, proc)
        self.assert_exit(result, 0)
        self.assertTrue(dead.is_dir())

        self_stat.write_text(stat_record("1"), encoding="utf-8")
        result = self.run_sweep(cache_root, proc)
        self.assert_exit(result, 0)
        self.assertFalse(dead.exists())

    def test_sweep_reports_usage_and_ignores_a_missing_root(self) -> None:
        proc = self.make_proc()
        missing = self.run_sweep(self.root / "missing", proc)
        self.assert_exit(missing, 0)
        usage = self.run_process([str(SWEEP)], {"GO_BUILD_SESSION_SWEEP_PROC": str(proc)})
        self.assert_exit(usage, 2)
        self.assertIn("usage: go-build-session-sweep <root>", usage.stderr)

    def test_sweep_uses_real_proc_for_a_live_process_and_a_reaped_one(self) -> None:
        cache_root = self.root / "go-build-session"
        cache_root.mkdir()
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        live = cache_root / session_name(os.getpid(), process_start(os.getpid()), boot)
        live.mkdir()
        child = subprocess.Popen(["/usr/bin/true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        _, child_stderr = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 0, child_stderr)
        self.assertFalse(Path(f"/proc/{child.pid}").exists())
        dead = cache_root / session_name(child.pid, "1", boot)
        dead.mkdir()

        result = self.run_process([str(SWEEP), str(cache_root)])
        self.assert_exit(result, 0)
        self.assertTrue(live.is_dir())
        self.assertFalse(dead.exists())


class AgentSessionGoBuildCacheTest(ScratchTest):
    user = "go-build-session-test-user"

    def setUp(self) -> None:
        super().setUp()
        self.dotfiles = self.root / "dotfiles"
        scripts = self.dotfiles / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(SWEEP, scripts / "go-build-session-sweep")
        shutil.copy2(DOTFILES / "scripts" / "agent-spend-env.sh", scripts / "agent-spend-env.sh")
        write_executable(scripts / "agent-secrets-session", 'exec "$@"')
        (self.dotfiles / "agent-spend").mkdir()
        self.no_nvme = self.root / "no-nvme"
        self.agent = self.root / "agent"
        self.write_standard_agent(self.agent)
        self.base_env.update({
            "USER": self.user,
            "DOTFILES_DIR": str(self.dotfiles),
            "AGENT_SESSION_NVME_HOME": str(self.no_nvme),
        })

    def write_standard_agent(self, path: Path) -> None:
        write_executable(
            path,
            """stat_text=""
read -r stat_text < /proc/$$/stat
stat_text="${stat_text##*) }"
read -ra fields <<< "$stat_text"
read -r boot < /proc/sys/kernel/random/boot_id
printf 'SESSION=%s-%s-%s\\n' "$$" "${fields[19]}" "$boot"
printf 'GOCACHE=%s\\n' "${GOCACHE-}"
""",
        )

    def start_agent(self, extra: dict[str, str] | None = None,
                    agent: Path | None = None) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        result = self.run_process([str(AGENT_SESSION), str(agent or self.agent)], extra)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        return result, values

    def live_name(self) -> str:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        return session_name(os.getpid(), process_start(os.getpid()), boot)

    def test_unset_cache_gets_the_agents_own_private_directory(self) -> None:
        result, values = self.start_agent()
        self.assert_exit(result, 0)
        cache = self.cache_home / "go-build-session" / values["SESSION"]
        self.assertEqual(values["GOCACHE"], str(cache))
        self.assertTrue(cache.is_dir())
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o700)

    def test_caller_values_outside_the_session_root_stay_unchanged(self) -> None:
        root = self.cache_home / "go-build-session"
        values_to_keep = (
            self.root / "callers-cache",
            root,
            self.cache_home / "go-build-session-other" / "x",
        )
        for value in values_to_keep:
            value.mkdir(parents=True, exist_ok=True)
            result, values = self.start_agent({"GOCACHE": str(value)})
            self.assert_exit(result, 0)
            self.assertEqual(values["GOCACHE"], str(value))

    def test_session_root_values_get_a_new_cache_and_leave_the_parent_in_place(self) -> None:
        root = self.cache_home / "go-build-session"
        parent_name = self.live_name()
        parent = root / parent_name
        parent.mkdir(parents=True)
        other_root = self.root / "other-cache" / "go-build-session"
        other_parent = other_root / parent_name
        other_parent.mkdir(parents=True)
        callers = (
            (str(parent), parent),
            (f"{parent}/", parent),
            (str(root / "custom" / "subcache"), root / "custom" / "subcache"),
            (str(other_parent), other_parent),
        )
        (root / "custom" / "subcache").mkdir(parents=True)
        for caller, parent_directory in callers:
            result, values = self.start_agent({"GOCACHE": caller})
            self.assert_exit(result, 0)
            cache = self.cache_home / "go-build-session" / values["SESSION"]
            self.assertEqual(values["GOCACHE"], str(cache))
            self.assertNotEqual(cache, Path(caller.rstrip("/")))
            self.assertTrue(cache.is_dir())
            self.assertTrue(parent_directory.is_dir())

    def test_unmakeable_root_unsets_an_inherited_or_unset_cache(self) -> None:
        bad_cache_home = self.root / "bad-cache-home"
        bad_cache_home.mkdir()
        root_file = bad_cache_home / "go-build-session"
        root_file.write_text("not a directory\n", encoding="utf-8")
        for caller in (None, str(root_file / self.live_name())):
            extra = {"XDG_CACHE_HOME": str(bad_cache_home)}
            if caller is not None:
                extra["GOCACHE"] = caller
            result, values = self.start_agent(extra)
            self.assert_exit(result, 0)
            self.assertEqual(values["GOCACHE"], "")
            self.assertIn("agent-session: cannot make", result.stderr)
            self.assertIn("GOCACHE is unset", result.stderr)

    def test_machine_local_cache_home_is_the_session_root(self) -> None:
        other_cache = self.root / "machine-cache"
        env_file = self.config_home / "agent-session" / "env"
        env_file.parent.mkdir()
        env_file.write_text(f"export XDG_CACHE_HOME={shlex.quote(str(other_cache))}\n", encoding="utf-8")
        env_file.chmod(0o600)

        result, values = self.start_agent()
        self.assert_exit(result, 0)
        cache = other_cache / "go-build-session" / values["SESSION"]
        self.assertEqual(values["GOCACHE"], str(cache))
        self.assertTrue(cache.is_dir())

    def test_detached_sweep_is_not_the_agents_child(self) -> None:
        started = self.root / "sweep-started"
        release = self.root / "sweep-release"
        finished = self.root / "sweep-finished"
        for fifo in (started, release, finished):
            os.mkfifo(fifo)
        sweep = self.dotfiles / "scripts" / "go-build-session-sweep"
        write_executable(
            sweep,
            f"""printf 'started\\n' > {shlex.quote(str(started))}
read -r -n 1 _ < {shlex.quote(str(release))}
printf 'finished\\n' > {shlex.quote(str(finished))}
""",
        )
        child_agent = self.root / "children-agent"
        write_executable(
            child_agent,
            """stat_text=""
read -r stat_text < /proc/$$/stat
stat_text="${stat_text##*) }"
read -ra fields <<< "$stat_text"
read -r boot < /proc/sys/kernel/random/boot_id
children=""
read -r children < /proc/$$/task/$$/children || true
printf 'SESSION=%s-%s-%s\\n' "$$" "${fields[19]}" "$boot"
printf 'GOCACHE=%s\\n' "${GOCACHE-}"
printf 'CHILDREN_BEFORE=%s\\n' "$children"
tail -f /dev/null &
tail_pid=$!
children=""
read -r children < /proc/$$/task/$$/children || true
printf 'CHILDREN_AFTER=%s\\n' "$children"
printf 'TAIL_PID=%s\\n' "$tail_pid"
kill "$tail_pid"
wait "$tail_pid" || true
""",
        )
        # The test holds a read-write end of each FIFO, so a write never blocks and the stub's open
        # of the release FIFO finds a writer whenever it comes, before or after the release byte.
        started_fd = os.open(started, os.O_RDWR | os.O_NONBLOCK)
        release_fd = os.open(release, os.O_RDWR | os.O_NONBLOCK)
        finished_fd = os.open(finished, os.O_RDWR | os.O_NONBLOCK)
        released = False
        try:
            result, values = self.start_agent(agent=child_agent)
            self.assert_exit(result, 0)
            self.assertEqual(values["CHILDREN_BEFORE"], "")
            self.assertEqual(values["CHILDREN_AFTER"], values["TAIL_PID"])
            # The launch has returned, and the stub has started and still waits for the release.
            self.assertEqual(self.read_fifo_line(started_fd), "started")
            os.write(release_fd, b"x")
            released = True
            self.assertEqual(self.read_fifo_line(finished_fd), "finished")
        finally:
            if not released:
                os.write(release_fd, b"x")
            os.close(release_fd)
            os.close(started_fd)
            os.close(finished_fd)

    def read_fifo_line(self, descriptor: int) -> str:
        readable, _, _ = select.select([descriptor], [], [], 10)
        self.assertTrue(readable, "timed out waiting for FIFO data")
        data = os.read(descriptor, 4096).decode("utf-8")
        self.assertTrue(data, "FIFO closed without data")
        return data.splitlines()[0]


class OmpGoBuildSessionSweepTest(ScratchTest):
    def make_case(self, name: str, *, machine_cache: Path | None = None,
                  env_mode: int = 0o600) -> dict[str, Path]:
        case = self.root / name
        home = case / "home"
        cache = case / "cache"
        config = case / "config"
        dotfiles = case / "dotfiles"
        bin_dir = case / "bin"
        for path in (home, cache, config, dotfiles / "scripts", bin_dir):
            path.mkdir(parents=True, exist_ok=True)
        shutil.copy2(AGENT_SESSION, dotfiles / "scripts" / "agent-session")
        shutil.copy2(DOTFILES / "scripts" / "agent-spend-env.sh", dotfiles / "scripts" / "agent-spend-env.sh")
        write_executable(dotfiles / "scripts" / "agent-secrets-session", 'exec "$@"')
        (dotfiles / "agent-spend").mkdir()

        omp_file = case / "omp.pid"
        done = case / "sweep-done"
        held = case / "sweep-held"
        release = case / "sweep-release"
        log = case / "sweep.log"
        wrapper_pid = case / "wrapper.pid"
        for fifo in (done, held, release):
            os.mkfifo(fifo)
        write_executable(bin_dir / "mise", "exit 0")
        write_executable(
            bin_dir / "systemd-run",
            """for word in "$@"; do
    if [[ "$word" == -- ]]; then
        shift
        exec "$@"
    fi
    shift
done
exit 2
""",
        )
        write_executable(
            bin_dir / "omp",
            f"""printf '%s %s\\n' "$$" "${{GOCACHE-}}" > {shlex.quote(str(omp_file))}
exit "${{STUB_EXIT}}"
""",
        )
        # The launch's detached sweep can run before or after the stub omp writes its pid, so only
        # the wrapper's line, written after the session ended, says whether that process is gone.
        write_executable(
            dotfiles / "scripts" / "go-build-session-sweep",
            f"""present=unknown
if [[ -s {shlex.quote(str(omp_file))} ]]; then
    read -r omp_pid _ < {shlex.quote(str(omp_file))}
    if [[ -e "/proc/$omp_pid" ]]; then present=yes; else present=no; fi
fi
printf '%s %s %s\\n' "$PPID" "$1" "$present" >> {shlex.quote(str(log))}
read -r wrapper < {shlex.quote(str(wrapper_pid))}
if [[ "$PPID" != "$wrapper" ]]; then
    printf x > {shlex.quote(str(done))}
elif [[ -n "${{HOLD_SWEEP:-}}" ]]; then
    printf x > {shlex.quote(str(held))}
    read -r -n 1 _ < {shlex.quote(str(release))}
fi
exit "${{SWEEP_EXIT}}"
""",
        )
        runner = case / "run-omp"
        write_executable(
            runner,
            f"""printf '%s\\n' "$$" > {shlex.quote(str(wrapper_pid))}
exec {shlex.quote(str(OMP_SHIM))} "$@"
""",
        )
        if machine_cache is not None:
            env_file = config / "agent-session" / "env"
            env_file.parent.mkdir()
            env_file.write_text(f"export XDG_CACHE_HOME={shlex.quote(str(machine_cache))}\n", encoding="utf-8")
            env_file.chmod(env_mode)
        return {
            "case": case,
            "home": home,
            "cache": cache,
            "config": config,
            "dotfiles": dotfiles,
            "bin": bin_dir,
            "omp_file": omp_file,
            "done": done,
            "held": held,
            "release": release,
            "log": log,
            "runner": runner,
            "wrapper_pid": wrapper_pid,
        }

    def run_case(self, name: str, status: int, sweep_status: int = 0,
                 machine_cache: Path | None = None, env_mode: int = 0o600,
                 interrupt: bool = False) -> tuple[dict[str, Path], str, str, int]:
        case = self.make_case(name, machine_cache=machine_cache, env_mode=env_mode)
        # Read-write ends, so neither the test's writes nor the stub's opens ever block.
        done_fd = os.open(case["done"], os.O_RDWR | os.O_NONBLOCK)
        held_fd = os.open(case["held"], os.O_RDWR | os.O_NONBLOCK)
        release_fd = os.open(case["release"], os.O_RDWR | os.O_NONBLOCK)
        environment = {
            "PATH": f"{case['bin']}:/usr/bin:/bin",
            "HOME": str(case["home"]),
            "XDG_CACHE_HOME": str(case["cache"]),
            "XDG_CONFIG_HOME": str(case["config"]),
            "DOTFILES_DIR": str(case["dotfiles"]),
            "AGENT_SESSION_NVME_HOME": str(case["case"] / "no-nvme"),
            "AGENTBOX_IDENTITY_FILE": str(case["case"] / "no-agentbox"),
            "OMP_MEMORY_MAX": "1G",
            "STUB_EXIT": str(status),
            "SWEEP_EXIT": str(sweep_status),
        }
        if interrupt:
            environment["HOLD_SWEEP"] = "1"
        process = subprocess.Popen(
            [str(case["runner"])],
            cwd=case["case"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            if interrupt:
                # The wrapper's own sweep is running: the session has ended and been reaped.
                readable, _, _ = select.select([held_fd], [], [], 20)
                self.assertTrue(readable, "the wrapper's sweep did not start")
                os.kill(process.pid, signal.SIGINT)
                os.write(release_fd, b"x")
            stdout, stderr = process.communicate(timeout=20)
            result = subprocess.CompletedProcess([str(case["runner"])], process.returncode, stdout, stderr)
            self.assert_exit(result, status)
            readable, _, _ = select.select([done_fd], [], [], 10)
            self.assertTrue(readable, "the detached sweep did not report completion")
            self.assertTrue(os.read(done_fd, 4096), "the detached sweep closed its FIFO")
        finally:
            if process.poll() is None:
                os.write(release_fd, b"x")
                process.kill()
                process.communicate()
            for descriptor in (done_fd, held_fd, release_fd):
                os.close(descriptor)
        omp_pid, go_cache = case["omp_file"].read_text(encoding="utf-8").strip().split(" ", 1)
        return case, omp_pid, go_cache, int(case["wrapper_pid"].read_text(encoding="utf-8").strip())

    def assert_sweep_lines(self, case: dict[str, Path], wrapper_pid: int, root: Path) -> None:
        lines = [line.split(" ", 2) for line in case["log"].read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(lines), 2, lines)
        wrapper_lines = [line for line in lines if int(line[0]) == wrapper_pid]
        self.assertEqual(len(wrapper_lines), 1, lines)
        self.assertEqual(wrapper_lines[0][1], str(root))
        self.assertEqual(wrapper_lines[0][2], "no")
        detached_lines = [line for line in lines if int(line[0]) != wrapper_pid]
        self.assertEqual(len(detached_lines), 1, lines)
        self.assertEqual(detached_lines[0][1], str(root))

    def test_wrapper_sweeps_for_zero_and_nonzero_session_statuses(self) -> None:
        for status in (0, 3):
            case, _, go_cache, wrapper_pid = self.run_case(f"status-{status}", status)
            root = case["cache"] / "go-build-session"
            self.assertEqual(Path(go_cache).parent, root)
            self.assert_sweep_lines(case, wrapper_pid, root)

    def test_wrapper_keeps_the_session_status_when_the_sweep_fails(self) -> None:
        case, _, _, wrapper_pid = self.run_case("sweep-fails", 3, sweep_status=1)
        self.assert_sweep_lines(case, wrapper_pid, case["cache"] / "go-build-session")

    def test_a_signal_during_the_wrappers_sweep_keeps_the_session_status(self) -> None:
        case, _, _, wrapper_pid = self.run_case("interrupted", 3, interrupt=True)
        self.assert_sweep_lines(case, wrapper_pid, case["cache"] / "go-build-session")

    def test_wrapper_and_session_use_the_same_safe_machine_cache_home(self) -> None:
        safe_cache = self.root / "safe-machine-cache"
        safe_case, _, safe_go_cache, safe_wrapper = self.run_case(
            "safe-machine-env", 0, machine_cache=safe_cache, env_mode=0o600
        )
        safe_root = safe_cache / "go-build-session"
        self.assertEqual(Path(safe_go_cache).parent, safe_root)
        self.assert_sweep_lines(safe_case, safe_wrapper, safe_root)

        unsafe_cache = self.root / "unsafe-machine-cache"
        unsafe_case, _, unsafe_go_cache, unsafe_wrapper = self.run_case(
            "unsafe-machine-env", 0, machine_cache=unsafe_cache, env_mode=0o620
        )
        unsafe_root = unsafe_case["cache"] / "go-build-session"
        self.assertEqual(Path(unsafe_go_cache).parent, unsafe_root)
        self.assert_sweep_lines(unsafe_case, unsafe_wrapper, unsafe_root)


if __name__ == "__main__":
    unittest.main()
