#!/usr/bin/env python3
"""The leak-term scanner against an encrypted, per-process test list."""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import resource
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from functools import lru_cache
from pathlib import Path
from unittest import mock

from leak_terms_fixture import TEST_TERMS, encrypt_yaml, fixture_environment, unrelated_key_file
from test_jj_push_leak_gate import SERVER_SIDE, SINK_HELPER


ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts" / "leak-terms-check"
SESSION = "01a0fa64-b3a5-7415-b143-c2a338b5da8b"
# Every variable that makes a shim read its caller as an agent. Each test environment starts without them (and
# without PI_*, LEGION_* and GIT_CONFIG_* variables), so the test alone decides who runs a case.
AGENT_MARKERS = (
    "OMP_SESSION_ID", "PI_SESSION_FILE", "PI_TOOL_BRIDGE_URL", "AGENTBOX_SESSION", "CLAUDECODE",
    "ENVOY_SESSION_ID", "AGENT_SHIM_AGENT", "LEGION_ROLE", "LEGION_TREE", "LEGION_CONTROLLER",
    "LEGION_DAEMON_URL", "SSH_CONNECTION", "SSH_TTY", "TMUX", "AGENT_SHIM_PERSON",
)
# A launch may start this many tasks beyond the ones this uid has when it starts. A shim or editor that
# recurses (an earlier draft of this file fork-bombed its machine) then fails to fork instead of exhausting the
# shared uid's tasks. The cap is read at each launch, not once per test: other sessions of the uid start and end
# threads all the time, and a cap read at the test's start refused git's own forks after they grew by the margin.
TASK_MARGIN = 1000
LAUNCH_TIMEOUT = 120
AGE_PUBLIC_KEY = re.compile(r"\bage1[02-9ac-hj-np-z]{58}\b")


def base_environment(*dropped: str) -> dict[str, str]:
    """os.environ without the agent markers, the PI_/LEGION_/GIT_CONFIG_ variables, and DROPPED."""
    return {
        name: value
        for name, value in os.environ.items()
        if name not in AGENT_MARKERS and name not in dropped and not name.startswith(("PI_", "LEGION_", "GIT_CONFIG_"))
    }


def live_tasks() -> int:
    """This uid's live tasks, threads included, read from /proc: what RLIMIT_NPROC counts."""
    uid = os.getuid()
    count = 0
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            with open(f"/proc/{entry.name}/status", encoding="ascii", errors="replace") as status:
                fields = dict(line.split(":", 1) for line in status if ":" in line)
        except OSError:
            continue  # the process exited while /proc was read
        if int(fields["Uid"].split()[0]) == uid:
            count += int(fields["Threads"])
    if count == 0:
        # This process is one of them, so a count of 0 means /proc showed nothing of this uid.
        raise AssertionError("no task of this uid is readable in /proc (UNKNOWN)")
    return count


# Each test term as the scanner matches it: case folded, each run of whitespace in the term any run of spaces and
# tabs, inside one line.
TERM_PATTERNS = tuple(
    re.compile(r"[ \t]+".join(re.escape(part) for part in term.casefold().split())) for term in TEST_TERMS
)


def leaked_terms(text: str | bytes) -> list[str]:
    """The test terms TEXT holds, by the scanner's matching rule."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="surrogateescape")
    folded = text.casefold()
    return [term for term, pattern in zip(TEST_TERMS, TERM_PATTERNS) if pattern.search(folded)]


def write_executable(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def unreadable_lists(root: Path, path: str) -> tuple[tuple[str, dict[str, str], str], ...]:
    """The five causes of an unreadable term list: each cause's environment changes, and the correction its one
    diagnostic line must name."""
    invalid = root / "invalid.sops.yaml"
    invalid.write_text("terms: clear text\n", encoding="utf-8")
    empty_sops_home = root / "empty-sops-home"
    empty_sops_home.mkdir()
    no_sops = os.pathsep.join(entry for entry in path.split(os.pathsep) if not (Path(entry) / "sops").exists())
    if shutil.which("sops", path=no_sops) is not None:
        raise AssertionError("the PATH without sops still finds sops")
    return (
        ("sops", {"PATH": no_sops}, "install sops with mise"),
        # A fresh age key that no rule of the list names, and a sops home that holds no other identity.
        (
            "key",
            {"SOPS_AGE_KEY_FILE": str(unrelated_key_file()), "DOTFILES_LEAK_TERMS_SOPS_HOME": str(empty_sops_home)},
            "add this machine's age key to .sops.yaml above the YubiKey line and run sops updatekeys leak-terms.sops.yaml"
            " on a machine that can decrypt the list",
        ),
        ("missing", {"DOTFILES_LEAK_TERMS_FILE": str(root / "missing.sops.yaml")}, "run scripts/dotfiles-sync local"),
        ("invalid", {"DOTFILES_LEAK_TERMS_FILE": str(invalid)}, "fix it with sops edit leak-terms.sops.yaml"),
        (
            "blank",
            {"DOTFILES_LEAK_TERMS_FILE": str(encrypt_yaml("terms: |\n  # blank\n\n"))},
            "add a term with sops edit leak-terms.sops.yaml",
        ),
    )


class LeakTestCase(unittest.TestCase):
    """A private scratch root per test, and the one bounded way this file starts a command."""

    def setUp(self) -> None:
        super().setUp()
        self.task_margin = TASK_MARGIN
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def private_home(self) -> None:
        """self.home, self.xdg and self.tmpdir: a HOME, XDG_CONFIG_HOME and TMPDIR of the test's own."""
        self.home, self.xdg, self.tmpdir = (self.root / name for name in ("home", "xdg", "tmp"))
        for directory in (self.home, self.xdg, self.tmpdir):
            directory.mkdir()

    def environment(self, agent: bool, **extra: str) -> dict[str, str]:
        """The test's agent or person environment (self.agent, self.person) with EXTRA."""
        return {**(self.agent if agent else self.person), **extra}

    def launch(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        input: str | bytes | None = None,
        text: bool = True,
        timeout: float = LAUNCH_TIMEOUT,
        pass_fds: tuple[int, ...] = (),
        stdin: int | None = None,
    ) -> subprocess.CompletedProcess:
        """ARGV in a session and process group of its own, its tasks capped at this uid's live tasks plus
        self.task_margin (RLIMIT_NPROC), stdin the descriptor STDIN, else /dev/null unless INPUT is given. A command
        that outlives TIMEOUT fails the test after its whole process group is killed, descendants holding its pipes
        included."""
        _, hard = resource.getrlimit(resource.RLIMIT_NPROC)
        task_limit = live_tasks() + self.task_margin
        limit = task_limit if hard == resource.RLIM_INFINITY else min(task_limit, hard)

        def cap_tasks() -> None:
            resource.setrlimit(resource.RLIMIT_NPROC, (limit, limit))

        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=stdin if stdin is not None else subprocess.DEVNULL if input is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            start_new_session=True,
            preexec_fn=cap_tasks,
            pass_fds=pass_fds,
        )
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            # The leader is not reaped yet, so its pid still names this group.
            os.killpg(process.pid, signal.SIGKILL)
            _, stderr = process.communicate(timeout=30)
            self.fail(f"{argv[0]} ran past {timeout} s; its process group was killed: {stderr!r}")
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)

    def assert_hidden(self, *outputs: object) -> None:
        """No test term in OUTPUTS (strings, bytes, or completed processes), by the scanner's matching rule."""
        for output in outputs:
            texts = (output.stdout, output.stderr) if isinstance(output, subprocess.CompletedProcess) else (output,)
            for text in texts:
                if text:
                    self.assertEqual(leaked_terms(text), [], "a test term reached the output")

    def assert_no_key_material(self, result: subprocess.CompletedProcess) -> None:
        output = (result.stdout or "") + (result.stderr or "")
        self.assertNotIn("AGE-SECRET-KEY", output.upper())
        self.assertIsNone(AGE_PUBLIC_KEY.search(output), "an age recipient reached the output")

    def assert_one_correction_line(self, result: subprocess.CompletedProcess, correction: str) -> None:
        """Exactly one line of the output names the term list, and it holds CORRECTION, no key and no term."""
        lines = [line for line in (result.stdout + result.stderr).splitlines() if "leak terms:" in line]
        self.assertEqual(len(lines), 1, result.stderr)
        self.assertIn(correction, lines[0])
        self.assert_no_key_material(result)
        self.assert_hidden(result)


def parse_strace(capture: bytes) -> tuple[list[tuple[bytes, ...]], dict[str, bytes]]:
    """Read the strace forms used by the secrecy test, failing closed on uncertainty."""
    if not capture:
        raise AssertionError("strace capture is empty (UNKNOWN)")
    text = capture.decode("utf-8", errors="surrogateescape")
    if re.search(r'"(?:\\.|[^"\\])*"\.\.\.', text):
        raise AssertionError("strace capture has a cut string")

    def quoted(source: str, start: int) -> tuple[bytes, int]:
        if start >= len(source) or source[start] != '"':
            raise AssertionError("strace string is malformed")
        value = bytearray()
        index = start + 1
        simple = {"a": b"\a", "b": b"\b", "f": b"\f", "n": b"\n", "r": b"\r", "t": b"\t", "v": b"\v"}
        while index < len(source):
            character = source[index]
            if character == '"':
                return bytes(value), index + 1
            if character != "\\":
                value.extend(character.encode("utf-8", errors="surrogateescape"))
                index += 1
                continue
            index += 1
            if index >= len(source):
                break
            escaped = source[index]
            if escaped == "x" and index + 2 < len(source) and all(
                digit in "0123456789abcdefABCDEF" for digit in source[index + 1:index + 3]
            ):
                value.append(int(source[index + 1:index + 3], 16))
                index += 3
            elif escaped in "01234567":
                digits = escaped
                index += 1
                while len(digits) < 3 and index < len(source) and source[index] in "01234567":
                    digits += source[index]
                    index += 1
                value.append(int(digits, 8))
            elif escaped in simple:
                value.extend(simple[escaped])
                index += 1
            else:
                value.extend(escaped.encode("utf-8", errors="surrogateescape"))
                index += 1
        raise AssertionError("strace string is unterminated")

    def closing(source: str, start: int, opener: str, closer: str) -> int:
        depth = 0
        index = start
        while index < len(source):
            if source[index] == '"':
                _, index = quoted(source, index)
                continue
            if source[index] == opener:
                depth += 1
            elif source[index] == closer:
                depth -= 1
                if depth == 0:
                    return index
            index += 1
        raise AssertionError("strace syscall is unterminated")

    def arguments(source: str) -> list[str]:
        result: list[str] = []
        start = 0
        depth = 0
        index = 0
        while index < len(source):
            if source[index] == '"':
                _, index = quoted(source, index)
                continue
            if source[index] in "([{":
                depth += 1
            elif source[index] in ")]}":
                depth -= 1
            elif source[index] == "," and depth == 0:
                result.append(source[start:index].strip())
                start = index + 1
            index += 1
        result.append(source[start:].strip())
        return result

    # A syscall left `<unfinished ...>` whose process then exits or is killed never resumes;
    # its arguments are in the capture, so it is audited as written in full. The capture is
    # cut, and the parse fails, only when the traced command has no exit record.
    exit_record = re.compile(r"\+\+\+ (?:exited with -?\d+|killed by SIG[A-Z0-9]+(?: \(core dumped\))?) \+\+\+")
    pending: dict[str, str] = {}
    calls: list[tuple[str, bool]] = []
    exited: set[str] = set()
    root: str | None = None
    for raw_line in text.splitlines():
        match = re.match(r"(?:\[pid\s+(\d+)\]\s+|(\d+)\s+)?(.*)", raw_line)
        assert match is not None
        pid = match.group(1) or match.group(2) or "main"
        line = match.group(3)
        if root is None:
            root = pid
        if exit_record.fullmatch(line):
            exited.add(pid)
            if pid in pending:
                calls.append((pending.pop(pid) + ")", True))
            continue
        if "<unfinished ...>" in line:
            if pid in pending or not line.endswith("<unfinished ...>"):
                raise AssertionError("strace unfinished syscall is malformed")
            pending[pid] = line.removesuffix("<unfinished ...>")
            continue
        resumed = re.fullmatch(r"<\.\.\. ([a-z0-9_]+) resumed>(.*)", line)
        if resumed:
            original = pending.pop(pid, None)
            if original is None:
                matches = [key for key, value in pending.items() if value.startswith(f"{resumed.group(1)}(")]
                if len(matches) == 1:
                    original = pending.pop(matches[0])
            if original is None or not original.startswith(f"{resumed.group(1)}("):
                raise AssertionError("strace resumed syscall has no matching start")
            line = original + resumed.group(2)
        elif "<... " in line:
            raise AssertionError("strace resumed syscall is malformed")
        calls.append((line, False))
    calls.extend((line + ")", True) for line in pending.values())

    argv: list[tuple[bytes, ...]] = []
    writes: dict[str, bytearray] = {}
    allowed_targets = ("pipe:[", "socket:[", "anon_inode:")
    for line, unfinished in calls:
        name = next((candidate for candidate in ("execve", "write", "pwrite64", "writev") if line.startswith(f"{candidate}(")), None)
        if name is None:
            continue
        end = closing(line, len(name), "(", ")")
        fields = arguments(line[len(name) + 1:end])
        if name == "execve":
            if len(fields) < 2 or not fields[1].startswith("["):
                raise AssertionError("strace execve argv is malformed")
            argv_end = closing(fields[1], 0, "[", "]")
            values: list[bytes] = []
            index = 1
            while index < argv_end:
                if fields[1][index] == '"':
                    value, index = quoted(fields[1], index)
                    values.append(value)
                else:
                    index += 1
            argv.append(tuple(values))
            continue
        if len(fields) < 2:
            raise AssertionError("strace write has no payload")
        target = re.fullmatch(r"\d+<(.*)>", fields[0])
        if target is None:
            raise AssertionError("strace write has no -yy file target")
        return_value = re.match(r"\s*=\s*(-?\d+|\?)", line[end + 1:])
        # `= ?` is a write its dying process never reported a result for: audit it in full.
        unfinished = unfinished or (return_value is not None and return_value.group(1) == "?")
        if not unfinished and (return_value is None or int(return_value.group(1)) <= 0):
            continue
        payload: list[bytes] = []
        if name == "writev":
            index = 0
            while index < len(fields[1]):
                iovec = fields[1].find("iov_base=", index)
                if iovec < 0:
                    break
                value_start = iovec + len("iov_base=")
                value, index = quoted(fields[1], value_start)
                payload.append(value)
            if not payload:
                raise AssertionError("strace writev has no readable iovec")
        else:
            payload.append(quoted(fields[1], 0)[0])
        path = quoted(f'"{target.group(1)}"', 0)[0].decode("utf-8", errors="surrogateescape")
        if path.startswith(allowed_targets) or path == "/dev/null" or path == "/dev/tty" or path.startswith("/dev/pts/"):
            continue
        if not path.startswith("/"):
            raise AssertionError(f"strace write target is not a regular-file path: {path}")
        written = b"".join(payload)
        writes.setdefault(path, bytearray()).extend(written if unfinished else written[:int(return_value.group(1))])
    if not argv:
        raise AssertionError("strace capture has no execve (UNKNOWN)")
    if root not in exited:
        raise AssertionError("strace capture has no exit record for the traced command (cut)")
    return argv, {path: bytes(data) for path, data in writes.items()}

@lru_cache(maxsize=1)
def real_jj() -> str:
    """The jj binary: the one `mise which jj` names, else the first jj on PATH that is neither a copy of shims/jj (its
    marker in the first 512 bytes) nor a mise shim (a link to mise), as test_jj_lock_shim.real_jj judges them. These
    tests must run, so finding none raises, never skips."""
    try:
        located = subprocess.run(["mise", "which", "jj"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        located = None
    if located is not None and located.returncode == 0 and located.stdout.strip():
        return located.stdout.strip()
    for directory in os.environ["PATH"].split(os.pathsep):
        candidate = Path(directory) / "jj"
        if not (candidate.is_file() and os.access(candidate, os.X_OK)) or candidate.resolve().name == "mise":
            continue
        with candidate.open("rb") as handle:
            if b"jj-lock-shim" in handle.read(512):
                continue
        return str(candidate)
    raise RuntimeError("jj is required for scanner tests")


@lru_cache(maxsize=1)
def scanner_module():
    """scripts/leak-terms-check loaded once as a module, for the tests that call its functions."""
    loader = importlib.machinery.SourceFileLoader("leak_terms_check", str(CHECK))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


class ScanModes(LeakTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.env = {**os.environ, **fixture_environment()}

    def check(self, mode: str, text: str, *arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return self.launch([str(CHECK), mode, *arguments], input=text, env=self.env if env is None else env, cwd=ROOT)

    def ok(self, argv: list[str], cwd: Path) -> str:
        result = self.launch(argv, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def assert_found(self, result: subprocess.CompletedProcess[str], place: str) -> None:
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn(f"leak terms: {place}: listed leak term", result.stderr)
        self.assertIn("sops edit leak-terms.sops.yaml", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assert_hidden(result)

    def test_hidden_term_detector_matches_as_the_scanner_does(self) -> None:
        # B7: an echo of the matched text, in another case or with another run of spaces, is a leak too. The
        # detector and the scanner must give the same verdict on each sample.
        for echoed in ("x quillfeather   LANE 9 y", "QUILLFEATHER\tlane  9", "Zorbleplex", "VINTRAPOLIS-CANARY-7"):
            with self.subTest(echoed=echoed):
                self.assertEqual(self.check("message", f"{echoed}\n").returncode, 3)
                self.assertNotEqual(leaked_terms(echoed), [])
                self.assertNotEqual(leaked_terms(echoed.encode()), [])
                with self.assertRaises(AssertionError):
                    self.assert_hidden(subprocess.CompletedProcess([], 1, "", f"error: {echoed}\n"))
        for clean in ("Quillfeather Lane", "Quillfeather Lane 8", "Quillfeather\nLane 9", "zorble plex"):
            with self.subTest(clean=clean):
                self.assertEqual(self.check("message", f"{clean}\n").returncode, 0)
                self.assertEqual(leaked_terms(clean), [])
                self.assert_hidden(subprocess.CompletedProcess([], 1, "", clean))

    def test_scanner_masks_overlapping_path_terms(self) -> None:
        terms = ("alpha beta", "beta gamma")
        matcher = scanner_module().TermMatcher(terms)
        self.assertEqual(matcher.spans("notes/alpha beta gamma.txt"), [
            scanner_module().Span(6, 16), scanner_module().Span(12, 22)
        ])
        self.assertEqual(
            scanner_module().mask_path("notes/alpha beta gamma.txt", matcher.spans("notes/alpha beta gamma.txt")),
            "notes/<listed term>.txt",
        )
        listed = encrypt_yaml("terms: |\n  alpha beta\n  beta gamma\n")
        diff = (
            "diff --git a/notes/alpha beta gamma.txt b/notes/alpha beta gamma.txt\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/notes/alpha beta gamma.txt\n"
            "@@ -0,0 +1 @@\n"
            "+clean\n"
        )
        result = self.check("diff", diff, env={**self.env, "DOTFILES_LEAK_TERMS_FILE": str(listed)})
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("notes/<listed term>.txt", result.stderr)
        for term in terms:
            self.assertNotIn(term, result.stderr)

    def test_scanner_refuses_an_unknown_message_encoding(self) -> None:
        result = self.check("message", "clean\n", "--encoding", "unknown-test-encoding")
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("cannot decode standard input", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_scanner_staged_uses_the_outgoing_attribute_policy(self) -> None:
        repository = self.root / "staged"
        repository.mkdir()
        self.ok(["/usr/bin/git", "init", "-q"], repository)
        (repository / ".gitattributes").write_text("*.txt -diff\n", encoding="utf-8")
        (repository / "hidden.txt").write_text("Quillfeather Lane 9\n", encoding="utf-8")
        self.ok(["/usr/bin/git", "add", "."], repository)
        result = self.launch([str(CHECK), "staged"], cwd=repository, env=self.env)
        self.assert_found(result, "hidden.txt:1")

    def test_scanner_casefolds_and_accepts_space_or_tab_runs(self) -> None:
        spaced = self.check("message", "quillfeather   LANE 9\n")
        tabbed = self.check("message", "quillfeather\tLANE 9\n")
        self.assert_found(spaced, "commit message line 1")
        self.assert_found(tabbed, "commit message line 1")
        for text in ("Quillfeather Lane\n", "Quillfeather Lane 8\n"):
            result = self.check("message", text)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_hidden(result)

    def test_scanner_reads_git_hunks_and_added_plus_content(self) -> None:
        diff = """diff --git a/note.txt b/note.txt
index 0000000..1111111 100644
--- a/note.txt
+++ b/note.txt
@@ -3,0 +4,2 @@
+before
++ Quillfeather Lane 9
"""
        result = self.check("diff", diff)
        self.assert_found(result, "note.txt:5")

    def test_scanner_treats_only_newlines_as_diff_line_breaks(self) -> None:
        clean = (
            "diff --git a/plain.txt b/plain.txt\n"
            "--- a/plain.txt\n"
            "+++ b/plain.txt\n"
            "@@ -0,0 +1,2 @@\n"
            "+a" + chr(12) + "b\n"
            "+a" + chr(13) + "b\n"
        )
        split_term = (
            "diff --git a/plain.txt b/plain.txt\n"
            "--- a/plain.txt\n"
            "+++ b/plain.txt\n"
            "@@ -0,0 +7 @@\n"
            "+x" + chr(12) + "Quillfeather Lane 9\n"
        )
        result = self.check("diff", clean)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_hidden(result)
        self.assert_found(self.check("diff", split_term), "plain.txt:7")

    def test_scanner_reads_a_real_git_c_quoted_path(self) -> None:
        repository = self.root / "git"
        repository.mkdir()
        self.ok(["/usr/bin/git", "init", "-q"], repository)
        path = repository / "notes"
        path.mkdir()
        (path / 'zorbleplex"file.txt').write_text("clean\n", encoding="utf-8")
        self.ok(["/usr/bin/git", "add", "."], repository)
        diff = self.ok(
            ["/usr/bin/git", "-c", "core.quotePath=false", "diff", "--cached", "-U0", "--no-ext-diff", "--no-textconv", "--no-color"],
            repository,
        )
        self.assertIn('"a/notes/', diff)
        result = self.check("diff", diff)
        self.assert_found(result, 'notes/<listed term>"file.txt')

    def test_scanner_reads_real_paths_that_contain_the_header_separator(self) -> None:
        repository = self.root / "ambiguous-path"
        repository.mkdir()
        self.ok(["/usr/bin/git", "init", "-q"], repository)
        self.ok([real_jj(), "git", "init", "--colocate"], repository)
        changed = repository / "dir b" / "notes.txt"
        listed = repository / "x b" / "zorbleplex.txt"
        changed.parent.mkdir()
        listed.parent.mkdir()
        changed.write_text("Quillfeather Lane 9\n", encoding="utf-8")
        listed.write_text("clean\n", encoding="utf-8")
        self.ok(["/usr/bin/git", "add", "."], repository)
        git_diff = self.ok(
            ["/usr/bin/git", "-c", "core.quotePath=false", "diff", "--cached", "-U0", "--no-ext-diff", "--no-textconv", "--no-color"],
            repository,
        )
        jj_diff = self.ok([real_jj(), "--no-pager", "--color=never", "diff", "--git", "-r", "@"], repository)
        for diff in (git_diff, jj_diff):
            self.assertIn("diff --git a/dir b/notes.txt b/dir b/notes.txt", diff)
            result = self.check("diff", diff)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("leak terms: dir b/notes.txt:1: listed leak term", result.stderr)
            self.assertIn("leak terms: x b/<listed term>.txt: listed leak term", result.stderr)
            self.assert_hidden(result)

    def test_scanner_reads_result_paths_but_not_deleted_or_renamed_away_paths(self) -> None:
        deleted = """diff --git a/zorbleplex.txt b/zorbleplex.txt
deleted file mode 100644
index 1111111..0000000
--- a/zorbleplex.txt
+++ /dev/null
@@ -1 +0,0 @@
-clean
"""
        renamed_away = """diff --git a/zorbleplex.txt b/clean.txt
similarity index 100%
rename from zorbleplex.txt
rename to clean.txt
"""
        renamed_to = """diff --git a/clean.txt b/zorbleplex.txt
similarity index 100%
rename from clean.txt
rename to zorbleplex.txt
"""
        for diff in (deleted, renamed_away):
            result = self.check("diff", diff)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_hidden(result)
        self.assert_found(self.check("diff", renamed_to), "<listed term>.txt")

    def test_scanner_reads_every_extended_header_as_metadata(self) -> None:
        # git's extended header lines between `diff --git` and the hunk: a mode change (git and jj print `old mode` and
        # `new mode` before `index`) and a break rewrite's `dissimilarity index` are metadata, never part of a path.
        def diff(path: str, header: str, hunk: str = "") -> str:
            return f"diff --git a/{path} b/{path}\n{header}{hunk}"

        mode = "old mode 100644\nnew mode 100755\n"
        added = "index 1111111..2222222\n--- a/run.sh\n+++ b/run.sh\n@@ -1,0 +2 @@\n+{}\n"
        rewrite = "dissimilarity index 100%\nindex 1111111..2222222 100644\n--- a/run.sh\n+++ b/run.sh\n@@ -1 +1 @@\n-old\n+{}\n"
        for label, text in (
            ("mode-only", diff("run.sh", mode)),
            ("mode-and-content", diff("run.sh", mode, added.format("echo clean"))),
            ("rewrite", diff("run.sh", "", rewrite.format("echo clean"))),
        ):
            with self.subTest(case=label, refused=False):
                result = self.check("diff", text)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_hidden(result)
        for label, text, place in (
            ("mode-only", diff("zorbleplex.sh", mode), "<listed term>.sh"),
            ("mode-and-content", diff("run.sh", mode, added.format("Quillfeather Lane 9")), "run.sh:2"),
            ("rewrite", diff("run.sh", "", rewrite.format("Quillfeather Lane 9")), "run.sh:1"),
        ):
            with self.subTest(case=label, refused=True):
                self.assert_found(self.check("diff", text), place)

    def test_scanner_reads_binary_paths_not_binary_payloads(self) -> None:
        binary_path = """diff --git a/zorbleplex.bin b/zorbleplex.bin
Binary files a/zorbleplex.bin and b/zorbleplex.bin differ
"""
        binary_payload = """diff --git a/plain.bin b/plain.bin
GIT binary patch
literal 3
Quillfeather Lane 9
"""
        self.assert_found(self.check("diff", binary_path), "<listed term>.bin")
        result = self.check("diff", binary_payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_hidden(result)

    def test_scanner_reads_a_real_jj_raw_path_with_controls(self) -> None:
        repository = self.root / "jj"
        repository.mkdir()
        self.ok(["/usr/bin/git", "init", "-q"], repository)
        self.ok([real_jj(), "git", "init", "--colocate"], repository)
        filename = 'raw-"\t\\\nzorbleplex.txt'
        (repository / filename).write_text("clean\n", encoding="utf-8")
        generated = self.ok([real_jj(), "--no-pager", "--color=never", "diff", "--git", "-r", "@"], repository)
        self.assertIn("diff --git", generated)
        result = self.check("diff", generated)
        self.assert_found(result, 'raw-"\\t\\\\\\n<listed term>.txt')

    def test_scanner_reads_combined_diffs_and_ignores_lines_from_one_parent(self) -> None:
        added = """diff --cc merge.txt
index 0000000,0000000..1111111
--- a/merge.txt
+++ b/merge.txt
@@@ -1,0 -1,0 +1,1 @@@
++Quillfeather Lane 9
"""
        from_one_parent = """diff --cc merge.txt
index 0000000,0000000..1111111
--- a/merge.txt
+++ b/merge.txt
@@@ -1,1 -1,0 +1,1 @@@
 +Quillfeather Lane 9
"""
        octopus = """diff --cc octopus.txt
index 0000000,0000000,0000000..1111111
--- a/octopus.txt
+++ b/octopus.txt
@@@@ -1,0 -1,0 -1,0 +1,1 @@@@
+++Quillfeather Lane 9
"""
        self.assert_found(self.check("diff", added), "merge.txt:1")
        clean = self.check("diff", from_one_parent)
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assert_hidden(clean)
        self.assert_found(self.check("diff", octopus), "octopus.txt:1")

    def test_scanner_refuses_a_malformed_hunk_count(self) -> None:
        malformed = """diff --git a/plain.txt b/plain.txt
--- a/plain.txt
+++ b/plain.txt
@@ -1,2 +1,1 @@
+clean
"""
        result = self.check("diff", malformed)
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("malformed unified hunk count", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assert_hidden(result)

    def test_scanner_applies_message_cleanups_and_final_line_numbers(self) -> None:
        strip_known = self.check("message", "# Quillfeather Lane 9\n", "--cleanup", "strip", "--comment-char", "#")
        strip_unknown = self.check("message", "# Quillfeather Lane 9\n", "--cleanup", "strip")
        whitespace = self.check("message", "# Quillfeather Lane 9\n", "--cleanup", "whitespace")
        verbatim = self.check("message", "# Quillfeather Lane 9\n", "--cleanup", "verbatim")
        scissors = self.check(
            "message",
            "keep\n# ------------------------ >8 ------------------------\nQuillfeather Lane 9\n",
            "--cleanup",
            "scissors",
            "--comment-char",
            "#",
        )
        cut = self.check(
            "message",
            "keep\n# ------------------------ >8 ------------------------\nQuillfeather Lane 9\n",
            "--cut-at-scissors",
            "--comment-char",
            "#",
        )
        unknown_scissors = self.check(
            "message",
            "keep\n# ------------------------ >8 ------------------------\nQuillfeather Lane 9\n",
            "--cleanup",
            "scissors",
        )
        unknown_cut = self.check(
            "message",
            "keep\n# ------------------------ >8 ------------------------\nQuillfeather Lane 9\n",
            "--cut-at-scissors",
        )
        git_whitespace = self.check(
            "message",
            "keep" + chr(11) + chr(12) + chr(13) + "\nQuillfeather Lane 9\n",
            "--cleanup",
            "whitespace",
        )
        lines = self.check(
            "message",
            "\n# removed\n\nkeep\n\nQuillfeather Lane 9  \n\n",
            "--cleanup",
            "strip",
            "--comment-char",
            "#",
        )
        for result in (strip_known, scissors, cut):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_hidden(result)
        for result in (strip_unknown, whitespace, verbatim):
            self.assert_found(result, "commit message line 1")
        for result in (unknown_scissors, unknown_cut):
            self.assert_found(result, "commit message line 3")
        self.assert_found(git_whitespace, "commit message line 2")
        self.assert_found(lines, "commit message line 3")

    def test_scanner_refuses_each_unreadable_list_cause_without_disclosing_terms(self) -> None:
        python_bin = self.root / "no-sops-bin"
        python_bin.mkdir()
        (python_bin / "python3").symlink_to(sys.executable)
        missing_sops = {**self.env, "PATH": str(python_bin)}
        unrelated = {**self.env, "SOPS_AGE_KEY_FILE": str(unrelated_key_file())}
        missing = {**self.env, "DOTFILES_LEAK_TERMS_FILE": str(self.root / "missing.sops.yaml")}
        invalid_file = self.root / "not-sops.yaml"
        invalid_file.write_text("terms: not encrypted\n", encoding="utf-8")
        invalid = {**self.env, "DOTFILES_LEAK_TERMS_FILE": str(invalid_file)}
        empty = {**self.env, "DOTFILES_LEAK_TERMS_FILE": str(encrypt_yaml("terms: |\n  # only comments\n\n"))}
        cases = (
            (missing_sops, "install sops with mise"),
            (
                unrelated,
                "add this machine's age key to .sops.yaml above the YubiKey line and run sops updatekeys "
                "leak-terms.sops.yaml on a machine that can decrypt the list",
            ),
            (missing, "run scripts/dotfiles-sync local"),
            (invalid, "fix it with sops edit leak-terms.sops.yaml"),
            (empty, "add a term with sops edit leak-terms.sops.yaml"),
        )
        for environment, correction in cases:
            result = self.check("message", "clean\n", env=environment)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(result.stdout, "")
            output_lines = result.stderr.strip().splitlines()
            self.assertEqual(len(output_lines), 1, result.stderr)
            self.assertIn(correction, output_lines[0])
            self.assert_hidden(result)
            self.assert_no_key_material(result)

    def test_scanner_uses_passwd_key_fallback_when_the_callers_home_moved(self) -> None:
        module = scanner_module()

        passwd_home = self.root / "passwd-home"
        key_dir = passwd_home / ".config" / "sops" / "age"
        key_dir.mkdir(parents=True)
        shutil.copyfile(self.env["SOPS_AGE_KEY_FILE"], key_dir / "keys.txt")
        moved_home = self.root / "moved-home"
        moved_home.mkdir()
        environment = {**self.env, "HOME": str(moved_home), "XDG_CONFIG_HOME": str(moved_home / "config")}
        for name in ("SOPS_AGE_KEY", "SOPS_AGE_KEY_FILE", "SOPS_AGE_KEY_CMD", "DOTFILES_LEAK_TERMS_SOPS_HOME"):
            environment.pop(name, None)
        with mock.patch.object(module, "passwd_home", return_value=passwd_home):
            matcher = module.load_terms(environment)
        self.assertTrue(matcher.spans("Quillfeather Lane 9"))

    def test_scanner_selects_gits_auto_comment_character(self) -> None:
        selected = self.check(
            "git-comment-char",
            # As git writes it: the message, a blank line, git's block, a blank line, the trailer.
            "# keep\n\n; status\n;\n; details\n\nOmp-Session: 01a0fa64-b3a5-7415-b143-c2a338b5da8b\n",
            "--editor-ran",
            "1",
            "--status-shown",
            "1",
            "--cut-line-written",
            "0",
        )
        scissors = self.check(
            "git-comment-char",
            "@ ------------------------ >8 ------------------------\n",
            "--editor-ran",
            "0",
            "--status-shown",
            "0",
            "--cut-line-written",
            "0",
        )
        self.assertEqual((selected.returncode, selected.stdout, selected.stderr), (0, ";\n", ""))
        self.assertEqual((scissors.returncode, scissors.stdout, scissors.stderr), (0, "#\n", ""))

    def test_scanner_jj_editor_cleans_each_description_section(self) -> None:
        edited = self.root / "editor.jjdescription"
        edited.write_text(
            "JJ: describe 111111111111 -------\n"
            "JJ: ignore-rest\n"
            "Quillfeather Lane 9\n"
            "JJ: describe 222222222222 -------\n"
            "Quillfeather Lane 9\n",
            encoding="utf-8",
        )
        result = self.launch(
            [str(CHECK), "jj-editor", str(edited)],
            env={
                **self.env,
                "DOTFILES_JJ_EDITOR": json.dumps(["/bin/true"]),
                "DOTFILES_JJ_EDITOR_MODE": "bulk",
                "DOTFILES_JJ_EDITOR_IDS": "111111111111,222222222222",
            },
            timeout=10,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("leak terms: description of 222222222222 line 1: listed leak term", result.stderr)
        self.assertIn("sops edit leak-terms.sops.yaml", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assert_hidden(result)

    def test_jj_header_marker_is_first_in_the_first_512_bytes(self) -> None:
        header = (ROOT / "shims" / "jj").read_bytes()[:512].decode("utf-8")
        self.assertTrue(header.startswith("#!/bin/bash\n# jj-lock-shim:"))

    def test_scanner_jj_editor_reads_the_list_before_running_the_editor(self) -> None:
        edited = self.root / "editor.jjdescription"
        edited.write_text("clean\n", encoding="utf-8")
        ran = self.root / "editor-ran"
        write_executable(self.root / "editor.sh", "#!/bin/sh\n" f": > {shlex.quote(str(ran))}\n")
        result = self.launch(
            [str(CHECK), "jj-editor", str(edited)],
            env={
                **self.env,
                "DOTFILES_JJ_EDITOR": json.dumps([str(self.root / "editor.sh")]),
                "DOTFILES_LEAK_TERMS_FILE": str(self.root / "missing.sops.yaml"),
            },
            timeout=10,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "leak terms: the term list is missing; run scripts/dotfiles-sync local\n")
        self.assertFalse(ran.exists())

    def test_scanner_refuses_non_string_term_lists(self) -> None:
        for label, shape in (
            ("sequence", "terms: []\n"),
            ("itemized", "terms: [Quillfeather Lane 9]\n"),
            ("mapping", "terms: {term: Quillfeather Lane 9}\n"),
            ("number", "terms: 9\n"),
        ):
            with self.subTest(shape=label):
                result = self.check(
                    "message",
                    "clean\n",
                    env={**self.env, "DOTFILES_LEAK_TERMS_FILE": str(encrypt_yaml(shape))},
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn(
                    "the term list is not a block of lines; fix it with sops edit leak-terms.sops.yaml",
                    result.stderr,
                )
                self.assert_hidden(result)

    def test_scanner_refuses_unresolved_raw_jj_paths(self) -> None:
        repository = self.root / "unresolved-jj-path"
        repository.mkdir()
        self.ok(["/usr/bin/git", "init", "-q"], repository)
        self.ok([real_jj(), "git", "init", "--colocate"], repository)
        for name in ("raw-\nnew file mode 100644\nzorbleplex.txt", "p\n+++ /dev/null\nq"):
            with self.subTest(name=name):
                (repository / name).parent.mkdir(parents=True, exist_ok=True)
                (repository / name).write_text("clean\n", encoding="utf-8")
                generated = self.ok([real_jj(), "--no-pager", "--color=never", "diff", "--git", "-r", "@"], repository)
                result = self.check("diff", generated)
                self.assertEqual(result.returncode, 4, result.stderr)
                self.assertIn("cannot read text to check", result.stderr)
                self.assert_hidden(result)
                (repository / name).unlink()

    def test_scanner_uses_git_auto_comment_character_rules(self) -> None:
        block = "; Please enter the commit message for your changes. Lines starting\n;\n; On branch main\n"
        cut = "------------------------ >8 ------------------------"
        cases = (
            # DeepReview 6: the caller's `#` cut line is message text; git chose `;`.
            ("caller-cut-line", f"# {cut}\n\n{block}", "1", "1", "0", ";\n"),
            # Many `@` lines in the message outnumber git's `;` block lines.
            ("many-at-lines", "# keep\n" + "@ line\n" * 40 + "\n" + block, "1", "1", "0", ";\n"),
            ("git-cut-line", f"# keep\n\n; {cut}\n; Do not modify or remove the line above.\n;\n", "1", "1", "1", ";\n"),
            # git's two cut lines (--cleanup=scissors with -v) name one character.
            ("two-git-cut-lines", f"x\n\n# {cut}\n# Do not modify or remove the line above.\n#\n# {cut}\n#\n", "1", "1", "1", "#\n"),
            # A caller's cut line in another character beside git's makes it unknown.
            ("two-cut-lines", f"# {cut}\n\n; {cut}\n;\n", "1", "1", "1", ""),
            ("caller-cut-line-and-two-git-cut-lines", f"# {cut}\n\n; {cut}\n;\n; {cut}\n", "1", "1", "1", ""),
            ("no-git-block", f"# {cut}\n", "1", "1", "0", ""),
            ("no-editor-caller-cut-line", f"@ {cut}\n# x\n", "0", "1", "0", ";\n"),
            ("no-hash", "plain text\n", "0", "0", "0", "#\n"),
            ("carriage-return-line-start", "a\r# b\r; c\n", "0", "0", "0", "@\n"),
        )
        for label, text, editor, status, written, expected in cases:
            with self.subTest(case=label):
                result = self.check(
                    "git-comment-char",
                    text,
                    "--editor-ran",
                    editor,
                    "--status-shown",
                    status,
                    "--cut-line-written",
                    written,
                )
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, expected, ""))

    def test_scanner_stream_parser_reads_a_16_mib_record_split_over_chunks(self) -> None:
        module = scanner_module()
        payload = b"x" * (16 * 1024 * 1024)
        chunks = [payload[index:index + 65536] for index in range(0, len(payload), 65536)]
        self.assertEqual(list(module.nul_records([*chunks, b"\0"])), [payload])

    def test_scanner_diff_stream_counts_hunks_before_accepting_a_commit_boundary(self) -> None:
        module = scanner_module()
        first = b"a" * 40
        second = b"b" * 40
        valid = (
            b"commit " + first + b"\n"
            b"diff --git a/file b/file\n"
            b"@@ -0,0 +1,2 @@\n"
            b"+clean\0bytes\n"
            b"+commit " + second + b"\n"
            b"commit " + second + b"\n"
        )
        records = list(module.outgoing_diff_records([valid[index:index + 7] for index in range(0, len(valid), 7)]))
        self.assertEqual([commit for commit, _ in records], [first.decode(), second.decode()])
        self.assertIn(b"+commit " + second + b"\n", records[0][1])
        malformed = b"commit " + first + b"\n@@ -0,0 +1,1 @@\ncommit " + second + b"\n"
        with self.assertRaises(module.TextError):
            list(module.outgoing_diff_records([malformed]))

    def test_scanner_reproduces_jj_command_comments_and_escapes(self) -> None:
        module = scanner_module()
        command, _ = module.jj_editor_command(json.dumps('"/editor path" # ignored comment'), "/message", 1)
        self.assertEqual(command, ["/editor path", "/message"])

    def test_scanner_jj_editor_preflight_reads_the_listing_and_names_each_cause(self) -> None:
        # The argument is `jj config list --include-defaults ui.editor -T 'name ++ "\t" ++ json(value)'`: one
        # `ui.editor` line, or a table's `ui.editor.command` and `ui.editor.env.<NAME>` lines.
        wrapper = "ui.editor=" + json.dumps([str(CHECK.resolve()), "jj-editor"])
        cases = (
            ("string", 'ui.editor\t"vim -f"', 0, f'{wrapper}\n"vim -f"\n'),
            (
                "table",
                'ui.editor.command\t["vim","-f"]\nui.editor.env.EDITOR_MODE\t"plain"\n',
                0,
                f'{wrapper}\n{{"env":{{"EDITOR_MODE":"plain"}},"command":["vim","-f"]}}\n',
            ),
            ("empty-array", "ui.editor\t[]", 5, ""),
            ("env-without-command", 'ui.editor.env.EDITOR_MODE\t"plain"', 5, ""),
            ("not-json", "ui.editor\tvim", 5, ""),
            ("wrapper", "ui.editor\t" + json.dumps([str(CHECK), "jj-editor"]), 6, ""),
        )
        for label, listing, status, output in cases:
            with self.subTest(case=label):
                result = self.launch([str(CHECK), "jj-editor-preflight", listing], env=self.env, cwd=ROOT)
                self.assertEqual((result.returncode, result.stdout), (status, output), result.stderr)

    def test_scanner_jj_editor_single_and_bulk_modes_fail_closed(self) -> None:
        single = self.root / "single.jjdescription"
        single.write_text(
            "Quillfeather Lane 9\nJJ: describe fake -------\nclean\n",
            encoding="utf-8",
        )
        single_result = self.launch(
            [str(CHECK), "jj-editor", str(single)],
            env={**self.env, "DOTFILES_JJ_EDITOR": json.dumps(["/bin/true"]), "DOTFILES_JJ_EDITOR_MODE": "single"},
            timeout=10,
        )
        self.assertEqual(single_result.returncode, 1, single_result.stderr)
        self.assertIn("commit message line 1: listed leak term", single_result.stderr)
        self.assert_hidden(single_result)

        bulk = self.root / "bulk.jjdescription"
        bulk.write_text("JJ: describe Quillfeather Lane 9 -------\nclean\n", encoding="utf-8")
        bulk_result = self.launch(
            [str(CHECK), "jj-editor", str(bulk)],
            env={
                **self.env,
                "DOTFILES_JJ_EDITOR": json.dumps(["/bin/true"]),
                "DOTFILES_JJ_EDITOR_MODE": "bulk",
                "DOTFILES_JJ_EDITOR_IDS": "111111111111",
            },
            timeout=10,
        )
        self.assertEqual(bulk_result.returncode, 1, bulk_result.stderr)
        self.assertIn("cannot read jj editor descriptions", bulk_result.stderr)
        self.assert_hidden(bulk_result)

    def test_harness_launch_kills_its_group_and_caps_tasks(self) -> None:
        # The bound itself, made to fire: a child that outlives the timeout while it holds the output pipes, and a
        # command that starts more tasks than its budget allows.
        pid_file = self.root / "sleeper.pid"
        with self.assertRaisesRegex(AssertionError, "process group was killed"):
            self.launch(["/bin/sh", "-c", f"sleep 300 & echo $! > {shlex.quote(str(pid_file))}; wait"], timeout=2)
        sleeper = Path(f"/proc/{int(pid_file.read_text(encoding='ascii'))}/stat")
        for _ in range(100):
            if not sleeper.exists() or sleeper.read_text(encoding="ascii").rsplit(")", 1)[1].split()[0] == "Z":
                break
            time.sleep(0.05)
        else:
            self.fail("the timed-out command's background child survived the group kill")

        self.task_margin = 50
        spawner = (
            "import subprocess\n"
            "started = []\n"
            "try:\n"
            "    for _ in range(400):\n"
            "        started.append(subprocess.Popen(['sleep', '30'], stdin=subprocess.DEVNULL,\n"
            "                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))\n"
            "except BlockingIOError:\n"
            "    pass\n"
            "finally:\n"
            "    for process in started:\n"
            "        process.kill()\n"
            "        process.wait()\n"
            "print(len(started))\n"
        )
        result = self.launch([sys.executable, "-c", spawner], timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(int(result.stdout), 400, "the task budget did not stop the forks")

    def test_harness_real_jj_skips_shim_copies_and_mise_shims(self) -> None:
        # With `mise which jj` failing or mise missing, the PATH lookup passes over another copy of shims/jj (its
        # marker in the first 512 bytes) and a mise shim (a link to mise), and raises, never skips, when nothing is
        # left.
        shim_copy = self.root / "shim-copy" / "jj"
        shim_copy.parent.mkdir()
        shutil.copyfile(ROOT / "shims" / "jj", shim_copy)
        shim_copy.chmod(0o755)
        mise_shim = self.root / "mise-shims" / "jj"
        mise_shim.parent.mkdir()
        mise_shim.symlink_to(write_executable(self.root / "mise-bin" / "mise", "#!/bin/sh\nexit 1\n"))
        binary = write_executable(self.root / "binary" / "jj", "#!/bin/sh\nexit 0\n")
        without_binary = os.pathsep.join(str(path.parent) for path in (shim_copy, mise_shim))
        failed = subprocess.CompletedProcess(["mise", "which", "jj"], 1, "", "")
        for label, mise in (("mise-fails", {"return_value": failed}), ("no-mise", {"side_effect": FileNotFoundError("mise")})):
            with self.subTest(case=label):
                with mock.patch.object(subprocess, "run", **mise):
                    with mock.patch.dict(os.environ, {"PATH": f"{without_binary}{os.pathsep}{binary.parent}"}):
                        self.assertEqual(real_jj.__wrapped__(), str(binary))
                    with mock.patch.dict(os.environ, {"PATH": without_binary}):
                        with self.assertRaises(RuntimeError):
                            real_jj.__wrapped__()


class GitLeaks(LeakTestCase):
    session = SESSION

    def setUp(self) -> None:
        super().setUp()
        self.private_home()
        self.config = self.root / "gitconfig"
        self.config.write_text(
            "[user]\n\tname = Leak Test\n\temail = leak-test@example.invalid\n"
            "[init]\n\tdefaultBranch = main\n",
            encoding="utf-8",
        )
        self.person = {
            **base_environment(),
            **fixture_environment(),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.xdg),
            "TMPDIR": str(self.tmpdir),
            "PATH": f"{ROOT / 'shims'}{os.pathsep}{os.environ['PATH']}",
            "GIT_CONFIG_GLOBAL": str(self.config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        self.agent = {**self.person, "OMP_SESSION_ID": self.session}
        self.repositories = 0
        self.editors = 0

    def git_run(self, repository: Path, *arguments: str, agent: bool, real: bool = False,
            extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        command_arguments = list(arguments)
        subcommand = 0
        while subcommand < len(command_arguments):
            option = command_arguments[subcommand]
            if option in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--exec-path"}:
                subcommand += 2
            elif option.startswith("-"):
                subcommand += 1
            else:
                break
        if subcommand < len(command_arguments) and command_arguments[subcommand] == "commit" and "--allow-empty" not in command_arguments:
            command_arguments.insert(subcommand + 1, "--allow-empty")
        command = ["/usr/bin/git" if real else "git", *command_arguments]
        return self.launch(command, cwd=repository, env=self.environment(agent, **(extra or {})))

    def hook_run(self, repository: Path, name: str, *arguments: str, agent: bool) -> subprocess.CompletedProcess[str]:
        return self.launch(
            [str(ROOT / "shims" / "git-hooks" / name), *arguments],
            cwd=repository,
            env=self.environment(agent, DOTFILES_GIT_COMMIT_SCAN="cleanup=default verbose=config status=config"),
        )

    def repository(self, agent: bool, remote: bool = False) -> Path:
        self.repositories += 1
        repository = self.root / f"repo-{self.repositories}"
        repository.mkdir()
        initialized = self.git_run(repository, "init", "-q", agent=agent)
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        if remote:
            added = self.git_run(repository, "remote", "add", "origin", "https://example.invalid/leak-test.git", agent=agent)
            self.assertEqual(added.returncode, 0, added.stderr)
        return repository

    def stage(self, repository: Path, name: str, text: str, agent: bool) -> None:
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        added = self.git_run(repository, "add", name, agent=agent)
        self.assertEqual(added.returncode, 0, added.stderr)

    def head(self, repository: Path, agent: bool) -> tuple[int, str]:
        result = self.git_run(repository, "rev-parse", "HEAD", agent=agent, real=True)
        return result.returncode, result.stdout.strip()

    def assert_refused(self, repository: Path, agent: bool, place: str, *arguments: str,
                       extra: dict[str, str] | None = None) -> None:
        before = self.head(repository, agent)
        result = self.git_run(repository, *arguments, agent=agent, extra=extra)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"leak terms: {place}: listed leak term", result.stderr)
        self.assertIn("sops edit leak-terms.sops.yaml", result.stderr)
        self.assertEqual(self.head(repository, agent), before)
        self.assert_hidden(result)

    def assert_clean(self, repository: Path, agent: bool, *arguments: str,
                     extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        result = self.git_run(repository, *arguments, agent=agent, extra=extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_hidden(result)
        return result

    def editor(self, text: str) -> str:
        self.editors += 1
        return str(write_executable(self.root / f"editor-{self.editors}.sh", f"#!/bin/sh\nprintf %s {shlex.quote(text)} > \"$1\"\n"))

    def test_git_term_refuses_staged_content_paths_amends_and_controls(self) -> None:
        for agent in (False, True):
            with self.subTest(agent=agent, case="content"):
                repository = self.repository(agent)
                self.stage(repository, "note.txt", "quillfeather   LANE 9\n", agent)
                self.assert_refused(repository, agent, "note.txt:1", "commit", "-m", "blocked")
                self.stage(repository, "note.txt", "clean\n", agent)
                self.assert_clean(repository, agent, "commit", "-m", "clean")

            with self.subTest(agent=agent, case="path"):
                repository = self.repository(agent)
                self.stage(repository, "notes/zorbleplex.txt", "clean\n", agent)
                self.assert_refused(repository, agent, "notes/<listed term>.txt", "commit", "-m", "blocked")
                self.stage(repository, "notes/clean.txt", "clean\n", agent)
                removed = self.git_run(repository, "rm", "--cached", "-q", "notes/zorbleplex.txt", agent=agent)
                self.assertEqual(removed.returncode, 0, removed.stderr)
                self.assert_clean(repository, agent, "commit", "-m", "clean")

            with self.subTest(agent=agent, case="pre-commit"):
                for content, refused in (("Quillfeather Lane 9", True), ("clean injected line", False)):
                    repository = self.repository(agent)
                    write_executable(
                        repository / ".git" / "hooks" / "pre-commit",
                        f"#!/bin/sh\nprintf '%s\\n' '{content}' > injected.txt\ngit add injected.txt\n",
                    )
                    self.stage(repository, "clean.txt", "clean\n", agent)
                    if refused:
                        self.assert_refused(repository, agent, "injected.txt:1", "commit", "-m", "blocked")
                        continue
                    self.assert_clean(repository, agent, "commit", "-m", "clean")
                    files = self.git_run(repository, "show", "--format=", "--name-only", "HEAD", agent=agent, real=True)
                    self.assertEqual(files.stdout.split(), ["clean.txt", "injected.txt"])

            with self.subTest(agent=agent, case="amend"):
                repository = self.repository(agent)
                self.stage(repository, "base.txt", "clean\n", agent)
                self.assert_clean(repository, agent, "commit", "-m", "base")
                self.stage(repository, "amend.txt", "Quillfeather Lane 9\n", agent)
                self.assert_refused(repository, agent, "amend.txt:1", "commit", "--amend", "--no-edit")
                self.stage(repository, "amend.txt", "clean\n", agent)
                self.assert_clean(repository, agent, "commit", "--amend", "--no-edit")

            with self.subTest(agent=agent, case="negative"):
                repository = self.repository(agent)
                self.stage(repository, "staged.txt", "Quillfeather Lane 8\n", agent)
                (repository / "unstaged.txt").write_text("Quillfeather Lane 9\n", encoding="utf-8")
                self.assert_clean(repository, agent, "commit", "-m", "clean")

            with self.subTest(agent=agent, case="partial"):
                repository = self.repository(agent)
                self.stage(repository, "a.txt", "old\n", agent)
                self.assert_clean(repository, agent, "commit", "-m", "base")
                self.stage(repository, "b.txt", "Quillfeather Lane 9\n", agent)
                (repository / "a.txt").write_text("clean\n", encoding="utf-8")
                self.assert_clean(repository, agent, "commit", "a.txt", "-m", "partial")

            with self.subTest(agent=agent, case="all"):
                for content, refused in (("Quillfeather Lane 9", True), ("changed clean", False)):
                    repository = self.repository(agent)
                    self.stage(repository, "tracked.txt", "clean\n", agent)
                    self.assert_clean(repository, agent, "commit", "-m", "base")
                    (repository / "tracked.txt").write_text(f"{content}\n", encoding="utf-8")
                    if refused:
                        self.assert_refused(repository, agent, "tracked.txt:1", "commit", "-a", "-m", "blocked")
                    else:
                        self.assert_clean(repository, agent, "commit", "-a", "-m", "clean")

    def test_git_term_refuses_through_global_options_that_take_a_separate_value(self) -> None:
        # Git 2.43 reads `--attr-source <tree-ish>` and `--shallow-file <path>` with the value in the next argument, so
        # the subcommand comes after that value. A shim that reads the value as the subcommand runs the commit unchecked.
        for agent in (False, True):
            for label, options in (("attr-source", ("--attr-source", "HEAD")), ("shallow-file", ("--shallow-file", ""))):
                with self.subTest(agent=agent, option=label):
                    repository = self.repository(agent)
                    self.stage(repository, "base.txt", "clean\n", agent)
                    self.assert_clean(repository, agent, "commit", "-m", "base")
                    self.stage(repository, "global.txt", "Quillfeather Lane 9\n", agent)
                    self.assert_refused(repository, agent, "global.txt:1", *options, "commit", "-m", "blocked")
                    self.stage(repository, "global.txt", "clean\n", agent)
                    self.assert_clean(repository, agent, *options, "commit", "-m", "clean")

    def test_git_term_refuses_a_commit_run_through_a_git_alias(self) -> None:
        # `git ci` with `alias.ci=commit` runs git's own commit: the alias is the same command, as for jj's aliases.
        for agent in (False, True):
            for label, configure, options in (
                ("repository-config", True, ()),
                ("command-line-config", False, ("-c", "alias.ci=commit")),
            ):
                with self.subTest(agent=agent, alias=label):
                    repository = self.repository(agent)
                    if configure:
                        configured = self.git_run(repository, "config", "alias.ci", "commit", agent=agent, real=True)
                        self.assertEqual(configured.returncode, 0, configured.stderr)
                    self.stage(repository, "alias.txt", "Quillfeather Lane 9\n", agent)
                    self.assert_refused(repository, agent, "alias.txt:1", *options, "ci", "-m", "blocked")
                    self.stage(repository, "alias.txt", "clean\n", agent)
                    self.assert_clean(repository, agent, *options, "ci", "-m", "clean")

    def test_git_term_refuses_an_alias_when_the_decoder_fails(self) -> None:
        failing_python = self.root / "failing-python"
        write_executable(
            failing_python / "python3",
            "#!/bin/sh\nif [ \"$1\" = - ]; then exit 71; fi\nexec /usr/bin/python3 \"$@\"\n",
        )
        for agent in (False, True):
            with self.subTest(agent=agent):
                repository = self.repository(agent)
                configured = self.git_run(repository, "config", "alias.ci", "commit", agent=agent, real=True)
                self.assertEqual(configured.returncode, 0, configured.stderr)
                environment = {"PATH": f"{failing_python}{os.pathsep}{self.environment(agent)['PATH']}"}
                status = self.git_run(repository, "status", "--short", agent=agent, extra=environment)
                self.assertEqual(status.returncode, 0, status.stderr)
                self.stage(repository, "alias.txt", "Quillfeather Lane 9\n", agent)
                before = self.head(repository, agent)
                result = self.git_run(repository, "ci", "-m", "blocked", agent=agent, extra=environment)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(
                    result.stderr,
                    "git shim: cannot read alias ci, so the leak-term check cannot run. Nothing was changed.\n",
                )
                self.assertEqual(self.head(repository, agent), before)
                self.assert_hidden(result)

    def test_git_term_reads_only_config_keys_for_the_hooks_path_exemption(self) -> None:
        for agent in (False, True):
            with self.subTest(agent=agent, case="unrelated-value"):
                repository = self.repository(agent)
                self.stage(repository, "leak.txt", "Quillfeather Lane 9\n", agent)
                self.assert_refused(
                    repository,
                    agent,
                    "leak.txt:1",
                    "-c",
                    "audit.note=core.hooksPath=unused",
                    "commit",
                    "-m",
                    "blocked",
                )
            with self.subTest(agent=agent, case="hooks-path"):
                repository = self.repository(agent)
                self.stage(repository, "leak.txt", "Quillfeather Lane 9\n", agent)
                self.assert_clean(repository, agent, "-c", "core.hookspath=/dev/null", "commit", "-m", "unchecked")
            with self.subTest(agent=agent, case="hooks-path-after-a-value-that-reads-as-an-option"):
                # `-C -c` changes into a directory named `-c`; the `-c core.hooksPath` after it is the caller's own.
                repository = self.repository(agent)
                (repository / "-c").mkdir()
                self.stage(repository, "leak.txt", "Quillfeather Lane 9\n", agent)
                self.assert_clean(
                    repository, agent, "-C", "-c", "-c", "core.hookspath=/dev/null", "commit", "-m", "unchecked"
                )

    def test_git_term_alias_expansion_leaves_a_dashed_external_command_to_git(self) -> None:
        # Git 2.43's run_argv runs an external `git-<name>` from the exec path or PATH before it reads `alias.<name>`,
        # so a command whose name is both an alias and an external command runs the external one. The shim's alias
        # expansion must keep that order: expanding the alias first runs a different program than git would.
        external = self.root / "external-commands"
        marker = "external git-zz ran"
        write_executable(external / "git-zz", f"#!/bin/sh\necho '{marker}'\n")
        for agent in (False, True):
            path = f"{external}{os.pathsep}{self.environment(agent)['PATH']}"
            with self.subTest(agent=agent, case="external-and-alias"):
                repository = self.repository(agent)
                configured = self.git_run(repository, "config", "alias.zz", "rev-parse --git-dir", agent=agent, real=True)
                self.assertEqual(configured.returncode, 0, configured.stderr)
                expected = self.git_run(repository, "zz", agent=agent, real=True, extra={"PATH": path})
                self.assertEqual((expected.returncode, expected.stdout), (0, f"{marker}\n"), expected.stderr)
                result = self.git_run(repository, "zz", agent=agent, extra={"PATH": path})
                self.assertEqual((result.returncode, result.stdout), (0, f"{marker}\n"), result.stderr)
            with self.subTest(agent=agent, case="alias-alone"):
                # The control: with no external command the alias runs, through the shim as through git.
                result = self.git_run(repository, "zz", agent=agent)
                self.assertEqual((result.returncode, result.stdout), (0, ".git\n"), result.stderr)

    def test_git_term_reads_text_files_the_repository_marks_binary(self) -> None:
        # A text file is still text when the repository's attributes tell `git diff` to show it as binary; the spec
        # leaves out binary files, not text files that an attribute hides.
        for agent in (False, True):
            for label, attributes, config in (
                ("minus-diff", "*.txt -diff\n", ()),
                ("binary-macro", "*.txt binary\n", ()),
                ("binary-driver", "*.txt diff=opaque\n", (("diff.opaque.binary", "true"),)),
            ):
                with self.subTest(agent=agent, attributes=label):
                    repository = self.repository(agent)
                    for name, value in config:
                        configured = self.git_run(repository, "config", name, value, agent=agent, real=True)
                        self.assertEqual(configured.returncode, 0, configured.stderr)
                    self.stage(repository, ".gitattributes", attributes, agent)
                    self.assert_clean(repository, agent, "commit", "-m", "attributes")
                    self.stage(repository, "hidden.txt", "clean\nQuillfeather Lane 9\n", agent)
                    self.assert_refused(repository, agent, "hidden.txt:2", "commit", "-m", "blocked")
                    self.stage(repository, "hidden.txt", "clean\n", agent)
                    self.assert_clean(repository, agent, "commit", "-m", "clean")

    def test_git_term_reads_text_files_external_attributes_mark_binary(self) -> None:
        # An empty attribute-source tree does not suppress higher-precedence info/attributes
        # or the user's configured attributes file. Those attributes cannot hide text.
        for agent in (False, True):
            for label in ("info", "global"):
                with self.subTest(agent=agent, attributes=label):
                    repository = self.repository(agent)
                    if label == "info":
                        attributes = repository / ".git" / "info" / "attributes"
                    else:
                        attributes = self.xdg / "git" / "attributes"
                        attributes.parent.mkdir(parents=True, exist_ok=True)
                    attributes.write_text("*.txt -diff\n" if label == "info" else "*.txt binary\n", encoding="utf-8")
                    self.stage(repository, "hidden.txt", "Quillfeather Lane 9\n", agent)
                    self.assert_refused(repository, agent, "hidden.txt:1", "commit", "-m", "blocked")

            with self.subTest(agent=agent, attributes="info-binary"):
                repository = self.repository(agent)
                attributes = repository / ".git" / "info" / "attributes"
                attributes.write_text("*.bin -diff\n", encoding="utf-8")
                (repository / "binary.bin").write_bytes(b"\0clean\n")
                staged = self.git_run(repository, "add", "binary.bin", agent=agent)
                self.assertEqual(staged.returncode, 0, staged.stderr)
                self.assert_clean(repository, agent, "commit", "-m", "binary")

    def test_git_term_handles_invalid_and_binary_file_bytes(self) -> None:
        # An invalid byte is still text and cannot break surrogateescape decoding. A NUL makes
        # the same term binary, so it remains outside the text-only scope.
        for agent in (False, True):
            with self.subTest(agent=agent, case="invalid-text-byte"):
                repository = self.repository(agent)
                (repository / "invalid.txt").write_bytes(b"\xffQuillfeather Lane 9\n")
                staged = self.git_run(repository, "add", "invalid.txt", agent=agent)
                self.assertEqual(staged.returncode, 0, staged.stderr)
                self.assert_refused(repository, agent, "invalid.txt:1", "commit", "-m", "blocked")
            with self.subTest(agent=agent, case="real-binary"):
                repository = self.repository(agent)
                (repository / "binary.bin").write_bytes(b"\0Quillfeather Lane 9\n")
                staged = self.git_run(repository, "add", "binary.bin", agent=agent)
                self.assertEqual(staged.returncode, 0, staged.stderr)
                self.assert_clean(repository, agent, "commit", "-m", "binary")

    def test_git_term_refuses_a_gitlink_when_submodules_are_ignored_in_config(self) -> None:
        for agent in (False, True):
            for path, refused in (("zorbleplex", True), ("clean-module", False)):
                with self.subTest(agent=agent, path=path):
                    repository = self.repository(agent)
                    object_id = self.launch(
                        ["/usr/bin/git", "hash-object", "-w", "--stdin"], cwd=repository, input="gitlink"
                    ).stdout.strip()
                    indexed = self.git_run(
                        repository,
                        "update-index",
                        "--add",
                        "--cacheinfo",
                        f"160000,{object_id},{path}",
                        agent=agent,
                    )
                    self.assertEqual(indexed.returncode, 0, indexed.stderr)
                    configured = self.git_run(repository, "config", "diff.ignoreSubmodules", "all", agent=agent)
                    self.assertEqual(configured.returncode, 0, configured.stderr)
                    if refused:
                        self.assert_refused(repository, agent, "<listed term>", "commit", "-m", "clean")
                    else:
                        self.assert_clean(repository, agent, "commit", "-m", "clean")

    def test_git_term_session_trailer_matches_existing_conditions(self) -> None:
        remote = self.repository(True, remote=True)
        self.stage(remote, "clean.txt", "clean\n", True)
        self.assert_clean(remote, True, "commit", "-m", "clean")
        body = self.git_run(remote, "log", "-1", "--format=%B", agent=False, real=True)
        self.assertIn(f"Omp-Session: {self.session}", body.stdout)

        throwaway = self.repository(True)
        self.stage(throwaway, "clean.txt", "clean\n", True)
        self.assert_clean(throwaway, True, "commit", "-m", "clean")
        body = self.git_run(throwaway, "log", "-1", "--format=%B", agent=False, real=True)
        self.assertNotIn("Omp-Session:", body.stdout)

    def test_git_term_reads_a_mode_change_as_metadata(self) -> None:
        # git prints `old mode` and `new mode` between the header and `index`: a mode-only change commits, and a term
        # the same change adds is refused at its line.
        for agent in (False, True):
            for content, place in ((None, None), ("echo clean\necho more\n", None), ("echo clean\nQuillfeather Lane 9\n", "run.sh:2")):
                with self.subTest(agent=agent, content=content, refused=place is not None):
                    repository = self.repository(agent)
                    self.stage(repository, "run.sh", "echo clean\n", agent)
                    made = self.git_run(repository, "commit", "-q", "-m", "base", agent=agent, real=True)
                    self.assertEqual(made.returncode, 0, made.stderr)
                    if content is not None:
                        (repository / "run.sh").write_text(content, encoding="utf-8")
                    (repository / "run.sh").chmod(0o755)
                    added = self.git_run(repository, "add", "run.sh", agent=agent)
                    self.assertEqual(added.returncode, 0, added.stderr)
                    if place is not None:
                        self.assert_refused(repository, agent, place, "commit", "-m", "mode")
                        continue
                    self.assert_clean(repository, agent, "commit", "-m", "mode")
                    tree = self.git_run(repository, "ls-tree", "HEAD", "run.sh", agent=agent, real=True)
                    self.assertTrue(tree.stdout.startswith("100755 "), tree.stdout)

    def message_case(self, agent: bool, config: tuple[tuple[str, str], ...], arguments: tuple[str, ...],
                     editor_text: str | None, hook_lines: tuple[str, ...] | None, place: str | None, *,
                     remote: bool = False, environment: dict[str, str] | None = None) -> None:
        """One commit in a new repository: refused at PLACE, or (PLACE None) one that must go on."""
        repository = self.repository(agent, remote=remote)
        for key, value in config:
            configured = self.git_run(repository, "config", key, value, agent=agent)
            self.assertEqual(configured.returncode, 0, configured.stderr)
        if hook_lines is not None:
            write_executable(
                repository / ".git" / "hooks" / "commit-msg",
                "#!/bin/sh\nprintf '%s\\n' " + " ".join(shlex.quote(line) for line in hook_lines) + " >> \"$1\"\n",
            )
        extra = dict(environment or {})
        if editor_text is not None:
            extra["GIT_EDITOR"] = self.editor(editor_text)
        if place is None:
            self.assert_clean(repository, agent, "commit", *arguments, extra=extra)
        else:
            self.assert_refused(repository, agent, place, "commit", *arguments, extra=extra)

    def test_git_message_refuses_cleanup_modes_and_forms(self) -> None:
        cut = "# ------------------------ >8 ------------------------"
        term, partial = "Quillfeather Lane 9", "Quillfeather Lane"
        with_term = self.root / "message-with-term"
        with_term.write_text(f"subject\n{term}\n", encoding="utf-8")
        with_partial = self.root / "message-with-partial"
        with_partial.write_text(f"subject\n{partial}\n", encoding="utf-8")
        strip, verbatim, semicolon, verbose = (
            (("commit.cleanup", "strip"),),
            (("commit.cleanup", "verbatim"),),
            (("core.commentChar", ";"),),
            (("commit.verbose", "true"),),
        )
        # (label, config, arguments, editor text, lines a commit-msg hook appends, the refusal's place or None for a
        # commit that goes on). Each setting has a refused commit and one that goes on; 'Quillfeather Lane' is the
        # partial-term control of each message form.
        cases = (
            ("m", (), ("-m", f"subject\n{term}"), None, None, "commit message line 2"),
            ("m", (), ("-m", f"subject\n{partial}"), None, None, None),
            ("message-equals", (), (f"--message={term}",), None, None, "commit message line 1"),
            ("message-equals", (), (f"--message={partial}",), None, None, None),
            ("m-attached", (), (f"-m{term}",), None, None, "commit message line 1"),
            ("m-attached", (), (f"-m{partial}",), None, None, None),
            ("file", (), ("-F", str(with_term)), None, None, "commit message line 2"),
            ("file", (), ("-F", str(with_partial)), None, None, None),
            ("editor", (), (), f"{term}\n", None, "commit message line 1"),
            ("editor", (), (), f"{partial}\n", None, None),
            ("m-comment", (), ("-m", f"# {term}"), None, None, "commit message line 1"),
            ("m-comment", (), ("-m", f"# {partial}"), None, None, None),
            ("config-strip", strip, (), f"# comment\n{term}\n", None, "commit message line 1"),
            ("config-strip", strip, (), f"# {term}\nclean\n", None, None),
            ("config-verbatim", verbatim, (), f"# {term}\n", None, "commit message line 1"),
            ("config-verbatim", verbatim, (), "# clean comment\nclean\n", None, None),
            ("cleanup-verbatim", (), ("--cleanup=verbatim",), f"# {term}\n", None, "commit message line 1"),
            ("cleanup-verbatim", (), ("--cleanup=verbatim",), "# clean comment\nclean\n", None, None),
            ("comment-char", semicolon, (), f"# {term}\n", None, "commit message line 1"),
            ("comment-char", semicolon, (), f"; {term}\nclean\n", None, None),
            ("commit-msg", (), ("-m", "clean"), None, (term,), "commit message line 2"),
            ("commit-msg", (), ("-m", "clean"), None, ("clean appended line",), None),
            ("scissors-editor", (), ("--cleanup=scissors",), f"# {term}\n{cut}\nclean\n", None, "commit message line 1"),
            ("scissors-editor", (), ("--cleanup=scissors",), f"clean\n{cut}\n{term}\n", None, None),
            ("scissors-message", (), ("--cleanup=scissors", "-m", f"clean\n{cut}\n{term}"), None, None, "commit message line 3"),
            ("scissors-message", (), ("--cleanup=scissors", "-m", f"clean\n{cut}\nclean"), None, None, None),
            ("config-verbose", verbose, ("-m", "clean"), None, (term, cut), "commit message line 2"),
            ("config-verbose", verbose, ("-m", "clean"), None, (cut, term), None),
        )
        for agent in (False, True):
            for label, config, arguments, editor_text, hook_lines, place in cases:
                with self.subTest(agent=agent, case=label, refused=place is not None):
                    self.message_case(agent, config, arguments, editor_text, hook_lines, place)

            with self.subTest(agent=agent, case="verbose-diff"):
                # `git commit -v` shows the editor the staged diff below its cut line; here that diff removes a
                # line holding the term. Above the line the editor writes the subject.
                for subject, place in (("clean subject", None), (term, "commit message line 1")):
                    repository = self.repository(agent)
                    (repository / "old.txt").write_text(f"{term}\nkeep\n", encoding="utf-8")
                    for arguments in (("add", "old.txt"), ("commit", "-q", "-m", "base")):
                        made = self.git_run(repository, *arguments, agent=agent, real=True)
                        self.assertEqual(made.returncode, 0, made.stderr)
                    self.stage(repository, "old.txt", "keep\n", agent)
                    shown = self.root / f"verbose-shown-{self.repositories}"
                    editor = write_executable(
                        self.root / f"verbose-editor-{self.repositories}.sh",
                        f"#!/bin/sh\ncp \"$1\" {shlex.quote(str(shown))}\n"
                        f"{{ printf '%s\\n' {shlex.quote(subject)}; cat {shlex.quote(str(shown))}; }} > \"$1\"\n",
                    )
                    if place is None:
                        self.assert_clean(repository, agent, "commit", "-v", extra={"GIT_EDITOR": str(editor)})
                    else:
                        self.assert_refused(repository, agent, place, "commit", "-v", extra={"GIT_EDITOR": str(editor)})
                    above, cut_line, below = shown.read_text(encoding="utf-8").partition(f"{cut}\n")
                    self.assertTrue(cut_line, "git commit -v showed no cut line")
                    self.assertEqual(leaked_terms(above), [])
                    self.assertIn(f"\n-{term}\n", below)

            with self.subTest(agent=agent, case="stored"):
                repository = self.repository(agent)
                message = "subject\n\nbody"
                self.assert_clean(repository, agent, "commit", "-m", message)
                stored = self.git_run(repository, "cat-file", "commit", "HEAD", agent=False, real=True)
                self.assertEqual(stored.returncode, 0, stored.stderr)
                _, _, body = stored.stdout.partition("\n\n")
                self.assertEqual(body, f"{message}\n")

    def test_git_message_uses_the_effective_commit_encoding(self) -> None:
        term = "Café Zorble"
        listed = encrypt_yaml(f"terms: |\n  {term}\n")
        message = self.root / "latin-1-message"
        message.write_bytes(f"{term}\n".encode("iso-8859-1"))
        for agent in (False, True):
            with self.subTest(agent=agent):
                repository = self.repository(agent)
                configured = self.git_run(
                    repository, "config", "i18n.commitEncoding", "ISO-8859-1", agent=agent, real=True
                )
                self.assertEqual(configured.returncode, 0, configured.stderr)
                self.stage(repository, "clean.txt", "clean\n", agent)
                before = self.head(repository, agent)
                result = self.launch(
                    ["git", "commit", "--allow-empty", "-F", str(message)],
                    cwd=repository,
                    env=self.environment(agent, DOTFILES_LEAK_TERMS_FILE=str(listed)),
                    text=False,
                )
                stderr = result.stderr.decode("utf-8", errors="surrogateescape")
                self.assertNotEqual(result.returncode, 0, stderr)
                self.assertIn("leak terms: commit message line 1: listed leak term", stderr)
                self.assertEqual(self.head(repository, agent), before)
                self.assertNotIn(term, stderr)

    def test_git_message_explicit_default_cleanup_beats_configuration(self) -> None:
        term = "Quillfeather Lane 9"
        verbatim, strip = (("commit.cleanup", "verbatim"),), (("commit.cleanup", "strip"),)
        cases = (
            ("editor", verbatim, ("--cleanup=default",), f"# {term}\nclean\n", None),
            ("editor", verbatim, ("--cleanup=default",), f"clean\n{term}\n", "commit message line 2"),
            ("no-editor", strip, ("--cleanup=default", "-m", f"# {term}"), None, "commit message line 1"),
            ("no-editor", strip, ("--cleanup=default", "-m", "# clean comment"), None, None),
        )
        for agent in (False, True):
            for label, config, arguments, editor_text, place in cases:
                with self.subTest(agent=agent, case=label, refused=place is not None):
                    self.message_case(
                        agent, config, arguments, editor_text, None, place,
                        environment=None if editor_text else {"GIT_EDITOR": ":"},
                    )

    def test_git_message_auto_comment_char_ignores_caller_cut_lines_and_line_counts(self) -> None:
        cut = "# ------------------------ >8 ------------------------"
        many_at = "# keep\n" + "\n".join(f"@ line {index}" for index in range(40))
        auto = (("core.commentChar", "auto"),)
        cases = (
            # DeepReview 6: git picks `;`, so it keeps the editor's `#` line.
            ("caller-cut-line", cut, "# Quillfeather Lane 9\n", "commit message line 1"),
            ("caller-cut-line", cut, "; Quillfeather Lane 9\nclean\n", None),
            # More `@` lines than git's `;` block: git still keeps the `@` line.
            ("many-at-lines", many_at, "clean\n@ Quillfeather Lane 9\n", "commit message line 2"),
            ("many-at-lines", many_at, "clean\n; Quillfeather Lane 9\n", None),
        )
        for agent in (False, True):
            for label, message, edited, place in cases:
                with self.subTest(agent=agent, case=label, refused=place is not None):
                    self.message_case(agent, auto, ("-e", "-m", message), edited, None, place)

    def test_git_message_auto_comment_char_handles_status_trailers_and_scissors(self) -> None:
        auto = (("core.commentChar", "auto"),)
        keep = ("-e", "-m", "# keep")
        semicolon_comment, hash_line = "clean\n; Quillfeather Lane 9\n", "clean\n# Quillfeather Lane 9\n"
        # With `# keep` in the message git picks `;`; with no message it picks `#`.
        cases = (
            ("initial-comment", keep, semicolon_comment, None),
            ("initial-comment", keep, hash_line, "commit message line 2"),
            ("no-initial-comment", (), hash_line, None),
            ("no-initial-comment", (), semicolon_comment, "commit message line 2"),
            ("no-status", (*keep, "--no-status"), semicolon_comment, None),
            ("no-status", (*keep, "--no-status"), hash_line, "commit message line 2"),
            ("caller-trailer", (*keep, "--trailer", "X-Test: y"), semicolon_comment, None),
            ("caller-trailer", (*keep, "--trailer", "X-Test: y"), hash_line, "commit message line 2"),
            ("signoff", (*keep, "-s"), semicolon_comment, None),
            ("signoff", (*keep, "-s"), hash_line, "commit message line 2"),
        )
        for agent in (False, True):
            for label, arguments, edited, place in cases:
                with self.subTest(agent=agent, case=label, refused=place is not None):
                    self.message_case(agent, auto, arguments, edited, None, place)
        # An agent's commit in a repository with a remote gets the Omp-Session trailer after git's block.
        for edited, place in ((semicolon_comment, None), (hash_line, "commit message line 2")):
            with self.subTest(case="session-trailer", refused=place is not None):
                self.message_case(True, auto, keep, edited, None, place, remote=True)

    def comment_char_files(self, repository: Path) -> list[str]:
        """The comment-character files prepare-commit-msg left in REPOSITORY's git directory."""
        return sorted(path.name for path in (repository / ".git").glob("dotfiles-leak-terms-comment-char.*"))

    def test_git_message_auto_comment_char_leaves_no_file_and_reads_no_stale_one(self) -> None:
        cut = "------------------------ >8 ------------------------"
        for agent in (False, True):
            with self.subTest(agent=agent, case="no-verify"):
                # commit-msg, which reads and removes the file, does not run under --no-verify.
                repository = self.repository(agent)
                configured = self.git_run(repository, "config", "core.commentChar", "auto", agent=agent)
                self.assertEqual(configured.returncode, 0, configured.stderr)
                self.assert_clean(repository, agent, "commit", "--no-verify", "-m", "clean")
                self.assertEqual(self.comment_char_files(repository), [])

            with self.subTest(agent=agent, case="stale-file"):
                # git picks `#`, which the message does not hold, and under -v writes its cut line in it; the message's
                # own `;` cut line makes two, so git's choice is not certain and every line is read, as git keeps the
                # term line. A file left for an earlier git process of the same pid names `;`: read, it would cut the
                # message at the `;` line and drop the term. The exec keeps the pid that names the file.
                repository = self.repository(agent)
                configured = self.git_run(repository, "config", "core.commentChar", "auto", agent=agent)
                self.assertEqual(configured.returncode, 0, configured.stderr)
                before = self.head(repository, agent)
                result = self.launch(
                    [
                        "bash", "-c",
                        'printf ";" > ".git/dotfiles-leak-terms-comment-char.$$" && exec git commit --allow-empty -v -e -m "$1"',
                        "commit", f"subject\n\n; {cut}\n; Quillfeather Lane 9",
                    ],
                    cwd=repository,
                    env=self.environment(agent, GIT_EDITOR="true"),
                )
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("leak terms: commit message line 4: listed leak term", result.stderr)
                self.assertEqual(self.head(repository, agent), before)
                self.assertEqual(self.comment_char_files(repository), [])
                self.assert_hidden(result)

    def test_git_message_auto_comment_char_reads_every_cut_line_git_wrote(self) -> None:
        # With --cleanup=scissors and -v git 2.43 writes two cut lines, both in its character; a caller's cut line in
        # another character makes the character not certain, so every line is read.
        cut = "------------------------ >8 ------------------------"
        auto = (("core.commentChar", "auto"),)
        scissors = ("--cleanup=scissors", "-v")
        cases = (
            # git picks `#`: a term only below its cut line goes on, one on a kept line above it is refused.
            ("below-the-cut", scissors, f"clean\n# {cut}\nQuillfeather Lane 9\n", None),
            ("above-the-cut", scissors, f"clean\nQuillfeather Lane 9\n# {cut}\nclean\n", "commit message line 2"),
            # The caller's `#` cut line makes git pick `;` and write its two cut lines in it: two characters, so every
            # line is read, the term below git's cut line included.
            ("caller-cut-line", ("-e", "-m", f"# {cut}", *scissors), f"# {cut}\nclean\n; {cut}\nQuillfeather Lane 9\n",
             "commit message line 4"),
        )
        for agent in (False, True):
            for label, arguments, edited, place in cases:
                with self.subTest(agent=agent, case=label, refused=place is not None):
                    self.message_case(agent, auto, arguments, edited, None, place)

    def recording_hooks(self, directory: Path, label: str) -> None:
        """pre-commit and commit-msg in DIRECTORY, each writing its argv and its stdin bytes to files named after
        LABEL and itself in $HOOK_MARKERS."""
        for name in ("pre-commit", "commit-msg"):
            record = f"\"$HOOK_MARKERS\"/{label}-{name}"
            write_executable(directory / name, f"#!/bin/sh\nprintf '%s\\0' \"$@\" > {record}.argv\ncat > {record}.stdin\n")

    def hook_records(self, markers: Path) -> dict[str, bytes]:
        return {path.name: path.read_bytes() for path in sorted(markers.iterdir())}

    def test_repo_hooks_run_and_the_opt_outs_stay_unscanned(self) -> None:
        # With the real git first on PATH and no shims directory at all (one holding git-hooks/hook).
        no_shim_path = os.pathsep.join(
            ["/usr/bin", *(entry for entry in os.environ["PATH"].split(os.pathsep) if not (Path(entry) / "git-hooks" / "hook").exists())]
        )
        self.assertEqual(shutil.which("git", path=no_shim_path), "/usr/bin/git")
        for agent in (False, True):
            for layout in ("default", "hooks-path"):
                with self.subTest(agent=agent, case=layout):
                    repository = self.repository(agent)
                    hooks = repository / ".git" / "hooks"
                    if layout == "hooks-path":
                        hooks = self.root / f"own-hooks-{self.repositories}"
                        configured = self.git_run(repository, "config", "core.hooksPath", str(hooks), agent=agent)
                        self.assertEqual(configured.returncode, 0, configured.stderr)
                    self.recording_hooks(hooks, layout)
                    through_shim, without_shim = self.root / f"shim-{self.repositories}", self.root / f"real-{self.repositories}"
                    through_shim.mkdir()
                    without_shim.mkdir()
                    self.stage(repository, "clean.txt", "clean\n", agent)
                    self.assert_clean(repository, agent, "commit", "-m", "clean", extra={"HOOK_MARKERS": str(through_shim)})
                    # The no-shim control in the same repository: a file with the term commits, and git gives the
                    # same hooks the same arguments and input.
                    (repository / "leak.txt").write_text("Quillfeather Lane 9\n", encoding="utf-8")
                    added = self.git_run(repository, "add", "leak.txt", agent=agent, real=True)
                    self.assertEqual(added.returncode, 0, added.stderr)
                    unchecked = self.git_run(
                        repository, "commit", "-m", "unchecked", agent=agent,
                        extra={"HOOK_MARKERS": str(without_shim), "PATH": no_shim_path},
                    )
                    self.assertEqual(unchecked.returncode, 0, unchecked.stderr)
                    names = [f"{layout}-{name}.{kind}" for name in ("commit-msg", "pre-commit") for kind in ("argv", "stdin")]
                    self.assertEqual(list(self.hook_records(through_shim)), names)
                    self.assertEqual(self.hook_records(through_shim), self.hook_records(without_shim))
                    self.assertEqual(self.hook_records(through_shim)[f"{layout}-commit-msg.argv"], b".git/COMMIT_EDITMSG\0")

            with self.subTest(agent=agent, case="failing-pre-commit"):
                repository = self.repository(agent)
                write_executable(repository / ".git" / "hooks" / "pre-commit", "#!/bin/sh\nexit 19\n")
                before = self.head(repository, agent)
                result = self.git_run(repository, "commit", "-m", "clean", agent=agent)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.head(repository, agent), before)
                direct = self.hook_run(repository, "pre-commit", agent=agent)
                self.assertEqual(direct.returncode, 19, direct.stderr)

            with self.subTest(agent=agent, case="failing-commit-msg"):
                repository = self.repository(agent)
                write_executable(repository / ".git" / "hooks" / "commit-msg", "#!/bin/sh\nexit 23\n")
                before = self.head(repository, agent)
                result = self.git_run(repository, "commit", "-m", "clean", agent=agent)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.head(repository, agent), before)
                message = repository / "message"
                message.write_text("clean\n", encoding="utf-8")
                direct = self.hook_run(repository, "commit-msg", str(message), agent=agent)
                self.assertEqual(direct.returncode, 23, direct.stderr)

            with self.subTest(agent=agent, case="caller-hooks-path"):
                repository = self.repository(agent)
                hooks = self.root / f"caller-hooks-{agent}"
                hooks.mkdir(exist_ok=True)
                self.stage(repository, "leak.txt", "Quillfeather Lane 9\n", agent)
                self.assert_clean(repository, agent, "-c", f"core.hooksPath={hooks}", "commit", "-m", "unchecked")
            with self.subTest(agent=agent, case="no-verify"):
                repository = self.repository(agent)
                self.stage(repository, "leak.txt", "Quillfeather Lane 9\n", agent)
                self.assert_clean(repository, agent, "commit", "--no-verify", "-m", "unchecked")

    def test_unreadable_lists_refuse_git_commits(self) -> None:
        cases = unreadable_lists(self.root, self.person["PATH"])
        for agent in (False, True):
            with self.subTest(agent=agent, case="healthy-list-control"):
                repository = self.repository(agent)
                self.stage(repository, "clean.txt", "clean\n", agent)
                before = self.head(repository, agent)
                self.assert_clean(repository, agent, "commit", "-m", "clean")
                self.assertNotEqual(self.head(repository, agent), before)
            for label, extra, correction in cases:
                with self.subTest(agent=agent, case=label):
                    repository = self.repository(agent)
                    self.stage(repository, "clean.txt", "clean\n", agent)
                    before = self.head(repository, agent)
                    result = self.git_run(repository, "commit", "-m", "clean", agent=agent, extra=extra)
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assert_one_correction_line(result, correction)
                    self.assertEqual(self.head(repository, agent), before)


class JjLeaks(LeakTestCase):
    session = SESSION

    def setUp(self) -> None:
        super().setUp()
        self.private_home()
        self.config = self.root / "jjconfig.toml"
        self.config.write_text("[user]\nname = \"Leak Test\"\nemail = \"leak-test@example.invalid\"\n", encoding="utf-8")
        self.person = {
            **base_environment("JJ_EDITOR", "VISUAL", "EDITOR"),
            **fixture_environment(),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.xdg),
            "TMPDIR": str(self.tmpdir),
            "PATH": f"{ROOT / 'shims'}{os.pathsep}{os.environ['PATH']}",
            "JJ_CONFIG": str(self.config),
            # One commit timestamp, so a snapshot of the same working copy gives the same commit ids anywhere.
            "JJ_TIMESTAMP": "2026-01-01T00:00:00+00:00",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        self.agent = {**self.person, "OMP_SESSION_ID": self.session}
        self.jj_binary = real_jj()
        self.repositories = 0
        self.editors = 0
        self.copies = 0

    def jj(self, repository: Path, *arguments: str, agent: bool, real: bool = False, input: str | bytes | None = None,
           extra: dict[str, str] | None = None, text: bool = True) -> subprocess.CompletedProcess[str]:
        return self.launch(
            [self.jj_binary if real else "jj", *arguments],
            cwd=repository,
            env=self.environment(agent, **(extra or {})),
            input=input,
            text=text,
        )

    def repository(self, agent: bool) -> Path:
        self.repositories += 1
        repository = self.root / f"repo-{self.repositories}"
        repository.mkdir()
        initialized = self.jj(repository, "git", "init", agent=agent)
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        return repository

    def write(self, repository: Path, name: str, text: str) -> None:
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def state(self, repository: Path, agent: bool) -> tuple[str, str]:
        """Each visible commit's change and commit id, its parents' change and commit ids and its description, and
        @'s change id; read without a snapshot."""
        listed = self.jj(
            repository,
            "--ignore-working-copy",
            "log",
            "-r",
            "all()",
            "--no-graph",
            "-T",
            'change_id ++ " " ++ commit_id ++ "\\n" ++ parents.map(|p| p.change_id() ++ ":" ++ p.commit_id()).join(",")'
            ' ++ "\\n" ++ json(description) ++ "\\n"',
            agent=agent,
            real=True,
        )
        current = self.jj(repository, "--ignore-working-copy", "log", "-r", "@", "--no-graph", "-T", "change_id", agent=agent, real=True)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertTrue(listed.stdout and current.stdout, "jj listed no commit (UNKNOWN)")
        return listed.stdout, current.stdout

    def snapshot_copy(self, repository: Path, agent: bool) -> Path:
        """An equivalent copy of REPOSITORY after a plain snapshot (`jj status`); REPOSITORY itself stays as it was,
        its working copy not yet snapshotted."""
        target = repository / ".jj" / "repo" / "store" / "git_target"
        if target.exists():
            self.assertFalse(Path(target.read_text(encoding="utf-8").strip()).is_absolute(), "the copy would share git")
        self.copies += 1
        copy = self.root / f"snapshot-copy-{self.copies}"
        copied = self.launch(["cp", "-a", "--", str(repository), str(copy)])
        self.assertEqual(copied.returncode, 0, copied.stderr)
        status = self.jj(copy, "status", agent=agent, real=True)
        self.assertEqual(status.returncode, 0, status.stderr)
        return copy

    def expected_state(self, repository: Path, agent: bool) -> tuple[str, str]:
        return self.state(self.snapshot_copy(repository, agent), agent)

    def assert_refused(self, repository: Path, agent: bool, place: str, *arguments: str,
                       input: str | bytes | None = None, extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        expected = self.expected_state(repository, agent)
        result = self.jj(repository, *arguments, agent=agent, input=input, extra=extra)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"leak terms: {place}: listed leak term", result.stderr)
        self.assertIn("sops edit leak-terms.sops.yaml", result.stderr)
        self.assertEqual(self.state(repository, agent), expected)
        self.assert_hidden(result)
        return result

    def assert_clean(self, repository: Path, agent: bool, *arguments: str, input: str | None = None,
                     extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        result = self.jj(repository, *arguments, agent=agent, input=input, extra=extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_hidden(result)
        return result

    def description(self, repository: Path, agent: bool, revision: str = "@") -> str:
        result = self.jj(repository, "--ignore-working-copy", "log", "-r", revision, "--no-graph", "-T", "description", agent=agent, real=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def commit_id(self, repository: Path, agent: bool, revision: str) -> str:
        result = self.jj(repository, "--ignore-working-copy", "log", "-r", revision, "--no-graph", "-T", "commit_id", agent=agent, real=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def changed_paths(self, repository: Path, agent: bool, revision: str) -> list[str]:
        result = self.jj(repository, "--ignore-working-copy", "diff", "--name-only", "-r", revision, agent=agent, real=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.split()

    def editor(self, text: str, status: int = 0) -> tuple[str, Path]:
        script, arguments, _ = self.editor_with_environment(text, status)
        return script, arguments

    def editor_with_environment(self, text: str, status: int = 0) -> tuple[str, Path, Path]:
        self.editors += 1
        script = self.root / f"editor-{self.editors}.sh"
        arguments = self.root / f"editor-{self.editors}.args"
        environment = self.root / f"editor-{self.editors}.environment"
        write_executable(
            script,
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$@\" > {shlex.quote(str(arguments))}\n"
            f"printf '%s' \"${{JJ_LEAK_TEST_EDITOR-}}\" > {shlex.quote(str(environment))}\n"
            "for argument in \"$@\"; do\n"
            "    if [ -f \"$argument\" ]; then file=$argument; fi\n"
            "done\n"
            f"printf '%s' {shlex.quote(text)} > \"$file\"\n"
            f"exit {status}\n",
        )
        return str(script), arguments, environment

    def set_user_editor(self, value: str) -> None:
        self.config.write_text(
            "[user]\nname = \"Leak Test\"\nemail = \"leak-test@example.invalid\"\n[ui]\neditor = " + value + "\n",
            encoding="utf-8",
        )

    def test_jj_term_refuses_target_diffs_and_controls(self) -> None:
        tool = write_executable(self.root / "test-diff-tool", "#!/bin/sh\nexit 0\n")
        # The test diff tool keeps every change jj shows it: as ui.diff-editor for -i, and by name for --tool.
        tool_config = ("--config", f"merge-tools.fixture.program={json.dumps(str(tool))}", "--config", "ui.diff-editor=fixture")
        # (label, the file just written and not yet snapshotted, arguments)
        cases = (
            ("commit", "note.txt", ("commit", "-m", "ok")),
            ("ci", "note.txt", ("ci", "-m", "ok")),
            ("commit-fileset", "dirty.txt", ("commit", "dirty.txt", "-m", "ok")),
            ("commit-interactive", "note.txt", (*tool_config, "commit", "-i", "-m", "ok")),
            ("commit-tool", "note.txt", (*tool_config, "commit", "--tool", "fixture", "-m", "ok")),
            ("describe", "note.txt", ("describe", "-m", "ok")),
            ("desc", "note.txt", ("desc", "-m", "ok")),
            ("new", "note.txt", ("new",)),
            ("new-root", "note.txt", ("new", "root()")),
        )
        for agent in (False, True):
            for label, path, arguments in cases:
                with self.subTest(agent=agent, case=label):
                    repository = self.repository(agent)
                    self.write(repository, path, "Quillfeather Lane 9\n")
                    place = f"{path}:1"
                    if label in {"describe", "desc"}:
                        # The id the snapshot gives @, read from an equivalent copy, so the subject stays unsnapshotted.
                        place = f"commit {self.commit_id(self.snapshot_copy(repository, agent), agent, '@')[:12]} {place}"
                    self.assert_refused(repository, agent, place, *arguments)

                    clean = self.repository(agent)
                    self.write(clean, path, "clean\n")
                    self.assert_clean(clean, agent, *arguments)
                    if label.startswith(("commit", "ci")):
                        self.assertEqual((self.description(clean, agent, "@-"), self.changed_paths(clean, agent, "@-")), ("ok\n", [path]))
                    elif label in {"describe", "desc"}:
                        self.assertEqual((self.description(clean, agent), self.changed_paths(clean, agent, "@")), ("ok\n", [path]))
                    elif label == "new":
                        self.assertEqual((self.changed_paths(clean, agent, "@-"), self.changed_paths(clean, agent, "@")), ([path], []))
                    else:
                        self.assertEqual(self.commit_id(clean, agent, "@-"), "0" * 40)

            for option in ("-i", "--tool=fixture"):
                with self.subTest(agent=agent, case=f"{option}-reads-all-of-@"):
                    # A diff tool may select any change, so a fileset does not narrow what is read.
                    repository = self.repository(agent)
                    self.write(repository, "clean.txt", "clean\n")
                    self.write(repository, "dirty.txt", "Quillfeather Lane 9\n")
                    self.assert_refused(repository, agent, "dirty.txt:1", *tool_config, "commit", option, "clean.txt", "-m", "ok")
                    clean = self.repository(agent)
                    self.write(clean, "clean.txt", "clean\n")
                    self.write(clean, "dirty.txt", "also clean\n")
                    self.assert_clean(clean, agent, *tool_config, "commit", option, "clean.txt", "-m", "ok")
                    self.assertEqual(self.changed_paths(clean, agent, "@-"), ["clean.txt"])

            with self.subTest(agent=agent, case="path"):
                repository = self.repository(agent)
                self.write(repository, "zorbleplex.txt", "clean\n")
                self.assert_refused(repository, agent, "<listed term>.txt", "commit", "-m", "ok")
                clean = self.repository(agent)
                self.write(clean, "clean-name.txt", "clean\n")
                self.assert_clean(clean, agent, "commit", "-m", "ok")

            with self.subTest(agent=agent, case="limited-fileset"):
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.write(repository, "dirty.txt", "Quillfeather Lane 9\n")
                self.assert_clean(repository, agent, "commit", "clean.txt", "-m", "ok")
                self.assertEqual(self.changed_paths(repository, agent, "@-"), ["clean.txt"])

    def test_jj_term_reads_text_files_attributes_mark_binary(self) -> None:
        # jj's --git diff must retain text that Git attributes mark binary, while an actual
        # NUL-containing file remains outside the text-only scan.
        for agent in (False, True):
            with self.subTest(agent=agent, case="attribute-hidden-text"):
                repository = self.repository(agent)
                self.write(repository, ".gitattributes", "*.txt -diff\n")
                self.write(repository, "hidden.txt", "Quillfeather Lane 9\n")
                self.assert_refused(repository, agent, "hidden.txt:1", "commit", "-m", "blocked")
            with self.subTest(agent=agent, case="real-binary"):
                repository = self.repository(agent)
                (repository / "binary.bin").write_bytes(b"\0Quillfeather Lane 9\n")
                self.assert_clean(repository, agent, "commit", "-m", "binary")

    def test_jj_message_editor_config_precedes_separator(self) -> None:
        for agent in (False, True):
            with self.subTest(agent=agent, case="refusal"):
                editor, _ = self.editor("Quillfeather Lane 9\n")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.write(repository, "other.txt", "other\n")
                self.assert_refused(
                    repository,
                    agent,
                    "commit message line 1",
                    "commit",
                    "--",
                    "clean.txt",
                    extra={"JJ_EDITOR": editor},
                )

            with self.subTest(agent=agent, case="clean"):
                editor, _ = self.editor("clean editor message\n")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.write(repository, "other.txt", "other\n")
                self.assert_clean(repository, agent, "commit", "--", "clean.txt", extra={"JJ_EDITOR": editor})
                committed = self.jj(
                    repository,
                    "--ignore-working-copy",
                    "diff",
                    "--git",
                    "-r",
                    "@-",
                    agent=agent,
                    real=True,
                )
                self.assertEqual(committed.returncode, 0, committed.stderr)
                self.assertIn("b/clean.txt", committed.stdout)
                self.assertNotIn("b/other.txt", committed.stdout)

    def test_jj_term_checks_each_described_revision_and_alias(self) -> None:
        for agent in (False, True):
            for added, refused in (("Quillfeather Lane 9", True), ("clean first", False)):
                with self.subTest(agent=agent, case="adds-then-removes", refused=refused):
                    # @-- adds the line and @- removes it, so only @--'s own diff holds it.
                    repository = self.repository(agent)
                    for text, message in ((added, "adds"), ("clean\n", "removes")):
                        self.write(repository, "note.txt", f"{text}\n")
                        made = self.jj(repository, "commit", "-m", message, agent=agent, real=True)
                        self.assertEqual(made.returncode, 0, made.stderr)
                    arguments = ("describe", "-r", "@-", "-r", "@--", "-m", "ok")
                    if refused:
                        place = f"commit {self.commit_id(repository, agent, '@--')[:12]} note.txt:1"
                        self.assert_refused(repository, agent, place, *arguments)
                    else:
                        self.assert_clean(repository, agent, *arguments)
                        self.assertEqual((self.description(repository, agent, "@-"), self.description(repository, agent, "@--")), ("ok\n", "ok\n"))

            with self.subTest(agent=agent, case="alias"):
                self.config.write_text(
                    "[user]\nname = \"Leak Test\"\nemail = \"leak-test@example.invalid\"\n[aliases]\nc = [\"commit\"]\n",
                    encoding="utf-8",
                )
                repository = self.repository(agent)
                self.write(repository, "note.txt", "Quillfeather Lane 9\n")
                self.assert_refused(repository, agent, "note.txt:1", "c", "-m", "ok")
                clean = self.repository(agent)
                self.write(clean, "note.txt", "clean\n")
                self.assert_clean(clean, agent, "c", "-m", "ok")
                self.assertEqual(self.description(clean, agent, "@-"), "ok\n")

    def test_jj_term_reads_a_mode_change_as_metadata(self) -> None:
        # jj prints `old mode` and `new mode` after the header too: a mode-only change commits, and a term the same
        # change adds is refused at its line.
        for agent in (False, True):
            for content, place in ((None, None), ("echo clean\necho more\n", None), ("echo clean\nQuillfeather Lane 9\n", "run.sh:2")):
                with self.subTest(agent=agent, content=content, refused=place is not None):
                    repository = self.repository(agent)
                    self.write(repository, "run.sh", "echo clean\n")
                    made = self.jj(repository, "commit", "-m", "base", agent=agent, real=True)
                    self.assertEqual(made.returncode, 0, made.stderr)
                    if content is not None:
                        self.write(repository, "run.sh", content)
                    (repository / "run.sh").chmod(0o755)
                    if place is not None:
                        self.assert_refused(repository, agent, place, "commit", "-m", "ok")
                        continue
                    self.assert_clean(repository, agent, "commit", "-m", "ok")
                    self.assertEqual((self.changed_paths(repository, agent, "@-"), self.description(repository, agent, "@-")), (["run.sh"], "ok\n"))

    def test_jj_message_refuses_forms_and_keeps_stored_messages(self) -> None:
        commands = (("describe",), ("commit",), ("new",))
        for agent in (False, True):
            for command in commands:
                revision = "@-" if command == ("commit",) else "@"
                with self.subTest(agent=agent, command=command):
                    repository = self.repository(agent)
                    self.write(repository, "clean.txt", "clean\n")
                    self.assert_refused(repository, agent, "commit message line 2", *command, "-m", "subject\nQuillfeather Lane 9")

                    clean = self.repository(agent)
                    self.write(clean, "clean.txt", "clean\n")
                    self.assert_clean(clean, agent, *command, "--message=first", "-msecond")
                    self.assertEqual(self.description(clean, agent, revision), "first\n\nsecond\n")

                with self.subTest(agent=agent, command=command, case="jj-line"):
                    # Outside the editor jj keeps a line of -m that starts with `JJ:`.
                    repository = self.repository(agent)
                    self.write(repository, "clean.txt", "clean\n")
                    self.assert_refused(repository, agent, "commit message line 1", *command, "-m", "JJ: Quillfeather Lane 9")
                    clean = self.repository(agent)
                    self.write(clean, "clean.txt", "clean\n")
                    self.assert_clean(clean, agent, *command, "-m", "JJ: Quillfeather Lane")
                    self.assertEqual(self.description(clean, agent, revision), "JJ: Quillfeather Lane\n")

            for text, place, stored in (
                ("Quillfeather Lane 9", "commit message line 1", None),
                ("clean\nJJ: Quillfeather Lane 9\n", "commit message line 2", None),
                ("Quillfeather Lane", None, "Quillfeather Lane\n"),
                ("clean\nJJ: Quillfeather Lane\n", None, "clean\nJJ: Quillfeather Lane\n"),
            ):
                with self.subTest(agent=agent, case="stdin", refused=place is not None, jj_line="JJ:" in text):
                    repository = self.repository(agent)
                    self.write(repository, "clean.txt", "clean\n")
                    if place is not None:
                        self.assert_refused(repository, agent, place, "describe", "--stdin", input=text)
                    else:
                        self.assert_clean(repository, agent, "describe", "--stdin", input=text)
                        self.assertEqual(self.description(repository, agent), stored)

            with self.subTest(agent=agent, case="stdin-preserves-bytes"):
                for text in ("", "no final newline", "three\n\n\n"):
                    subject = self.repository(agent)
                    control = self.repository(agent)
                    self.write(subject, "clean.txt", "clean\n")
                    self.write(control, "clean.txt", "clean\n")
                    actual = self.assert_clean(subject, agent, "describe", "--stdin", input=text)
                    expected = self.jj(control, "describe", "--stdin", agent=agent, real=True, input=text)
                    self.assertEqual(expected.returncode, 0, expected.stderr)
                    self.assertEqual(self.description(subject, agent), self.description(control, agent), actual.stderr)

            with self.subTest(agent=agent, case="stdin-nul"):
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                before = self.expected_state(repository, agent)
                result = self.jj(
                    repository,
                    "describe",
                    "--stdin",
                    agent=agent,
                    input=b"clean\x00message",
                    text=False,
                )
                self.assertNotEqual(result.returncode, 0, result.stderr)
                output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
                self.assertIn("cannot read a NUL byte", output)
                self.assertEqual(self.state(repository, agent), before)
                self.assert_hidden(output)

    def test_jj_message_refuses_an_unreadable_standard_input(self) -> None:
        directory = self.root / "description-directory"
        directory.mkdir()

        def read_directory(repository: Path, agent: bool, binary: str) -> subprocess.CompletedProcess[str]:
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                return self.launch(
                    [binary, "describe", "--stdin"],
                    cwd=repository,
                    env=self.environment(agent),
                    stdin=descriptor,
                )
            finally:
                os.close(descriptor)

        for agent in (False, True):
            with self.subTest(agent=agent):
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                direct = read_directory(repository, agent, self.jj_binary)
                self.assertEqual(direct.returncode, 1, direct.stderr)
                expected = self.expected_state(repository, agent)
                result = read_directory(repository, agent, "jj")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(
                    "jj shim: cannot read the description from standard input; nothing changed",
                    result.stderr,
                )
                self.assertEqual(self.state(repository, agent), expected)
                self.assert_hidden(result)

    def test_jj_message_editor_options_and_unknown_option_refuse(self) -> None:
        for agent in (False, True):
            with self.subTest(agent=agent, case="jj-editor"):
                editor, _ = self.editor("Quillfeather Lane 9\n")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_refused(repository, agent, "commit message line 1", "describe", extra={"JJ_EDITOR": editor})
                editor, arguments = self.editor("ok\n")
                clean = self.repository(agent)
                self.write(clean, "clean.txt", "clean\n")
                self.assert_clean(clean, agent, "describe", extra={"JJ_EDITOR": editor})
                self.assertEqual((self.editor_argv(arguments), self.description(clean, agent)), (["<path>"], "ok\n"))

            with self.subTest(agent=agent, case="editor-over-message"):
                editor, _ = self.editor("Quillfeather Lane 9\n")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_refused(repository, agent, "commit message line 1", "describe", "--editor", "-m", "clean", extra={"JJ_EDITOR": editor})
                editor, _ = self.editor("clean\n")
                clean = self.repository(agent)
                self.write(clean, "clean.txt", "clean\n")
                self.assert_clean(clean, agent, "describe", "--editor", "-m", "Quillfeather Lane 9", extra={"JJ_EDITOR": editor})
                self.assertEqual(self.description(clean, agent), "clean\n")

            with self.subTest(agent=agent, case="user-string"):
                editor, arguments = self.editor("clean from editor\n")
                self.set_user_editor(json.dumps(f"{editor} --wait"))
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_clean(repository, agent, "describe")
                self.assertEqual(self.editor_argv(arguments), ["--wait", "<path>"])

            with self.subTest(agent=agent, case="user-array-path"):
                editor, arguments = self.editor("clean array editor\n")
                self.set_user_editor(json.dumps([editor, "$path", "--wait"]))
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_clean(repository, agent, "describe")
                self.assertEqual(self.editor_argv(arguments), ["<path>", "--wait"])

            with self.subTest(agent=agent, case="user-structured"):
                editor, arguments, configured_environment = self.editor_with_environment("clean structured editor\n")
                self.set_user_editor(
                    "{ env = { JJ_LEAK_TEST_EDITOR = \"configured\" }, command = "
                    + json.dumps([editor, "$path"])
                    + " }"
                )
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_clean(repository, agent, "describe")
                self.assertEqual(self.editor_argv(arguments), ["<path>"])
                self.assertEqual(configured_environment.read_text(encoding="utf-8"), "configured")

            with self.subTest(agent=agent, case="visual-and-editor"):
                for variable in ("VISUAL", "EDITOR"):
                    editor, arguments = self.editor("clean environment editor\n")
                    self.config.write_text("[user]\nname = \"Leak Test\"\nemail = \"leak-test@example.invalid\"\n", encoding="utf-8")
                    repository = self.repository(agent)
                    self.write(repository, "clean.txt", "clean\n")
                    self.assert_clean(repository, agent, "describe", extra={variable: editor})
                    self.assertEqual(self.editor_argv(arguments), ["<path>"])
                    self.assertEqual(self.description(repository, agent), "clean environment editor\n")

            with self.subTest(agent=agent, case="command-config-editor"):
                editor, arguments = self.editor("clean command config editor\n")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_clean(repository, agent, "describe", "--config", f"ui.editor={json.dumps(editor + ' --wait')}")
                self.assertEqual(self.editor_argv(arguments), ["--wait", "<path>"])

            with self.subTest(agent=agent, case="line-variable"):
                # jj puts the first line after the leading `JJ:` lines where an argument says $line; the wrapper
                # must give the editor that same argv.
                recorded = []
                for real in (True, False):
                    editor, arguments = self.editor("clean line editor\n")
                    repository = self.repository(agent)
                    self.write(repository, "clean.txt", "clean\n")
                    configured = f"ui.editor={json.dumps([editor, '+$line', '$path'])}"
                    ran = self.jj(repository, "--config", configured, "describe", agent=agent, real=real)
                    self.assertEqual(ran.returncode, 0, ran.stderr)
                    recorded.append(self.editor_argv(arguments))
                self.assertEqual(recorded[0], ["+1", "<path>"])
                self.assertEqual(recorded[1], recorded[0])

            with self.subTest(agent=agent, case="editor-failure"):
                editor, _ = self.editor("clean\n", status=19)
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                expected = self.expected_state(repository, agent)
                result = self.jj(repository, "describe", agent=agent, extra={"JJ_EDITOR": editor})
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("Failed to edit description", result.stderr)
                self.assertIn("exited with exit status: 19", result.stderr)
                self.assertEqual(self.state(repository, agent), expected)

            with self.subTest(agent=agent, case="jj-lines-clean"):
                editor, _ = self.editor("JJ: Quillfeather Lane 9\nclean\nJJ: ignore-rest\nQuillfeather Lane 9\n")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_clean(repository, agent, "describe", extra={"JJ_EDITOR": editor})
                self.assertEqual(self.description(repository, agent), "clean\n")

            for command, option in (
                ("describe", "--unknown-option"),
                ("new", "--edit"),
                ("new", "-d"),
            ):
                with self.subTest(agent=agent, case=f"unknown-{command}-{option}"):
                    # Refused before any jj command runs, so nothing changes: not even a snapshot.
                    repository = self.repository(agent)
                    self.write(repository, "clean.txt", "clean\n")
                    before = self.state(repository, agent)
                    result = self.jj(repository, command, option, "-m", "clean", agent=agent)
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertIn(
                        f"jj shim: cannot read option {option}, so the leak-term check cannot run; nothing changed",
                        result.stderr,
                    )
                    self.assertEqual(self.state(repository, agent), before)
                    self.assert_hidden(result)

            for label, before_command in (("before-command", True), ("after-command", False)):
                with self.subTest(agent=agent, case=f"repository-option-{label}"):
                    repository = self.repository(agent)
                    self.write(repository, "clean.txt", "clean\n")
                    expected = self.expected_state(repository, agent)
                    location = ("-R", str(repository))
                    arguments = (*location, "describe") if before_command else ("describe", *location)
                    result = self.jj(self.root, *arguments, "-m", "Quillfeather Lane 9", agent=agent)
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertIn("leak terms: commit message line 1: listed leak term", result.stderr)
                    self.assertEqual(self.state(repository, agent), expected)
                    self.assert_hidden(result)
                    clean = self.repository(agent)
                    self.write(clean, "clean.txt", "clean\n")
                    location = ("-R", str(clean))
                    arguments = (*location, "describe") if before_command else ("describe", *location)
                    self.assert_clean(self.root, agent, *arguments, "-m", "Quillfeather Lane")
                    self.assertEqual(self.description(clean, agent), "Quillfeather Lane\n")

    def editor_argv(self, arguments: Path) -> list[str]:
        """The arguments a test editor recorded, with the description file it got shown as <path>."""
        return ["<path>" if value.endswith(".jjdescription") else value for value in arguments.read_text(encoding="utf-8").splitlines()]


    def test_jj_message_editor_strings_split_as_real_jj(self) -> None:
        # jj 0.46 splits a ui.editor string with the shlex 2.0.1 crate when it holds a quote,
        # else at each space; the wrapper must hand the editor the argv jj itself would.
        plain = self.root / "recorder.sh"
        spaced = self.root / "editor dir" / "recorder with space.sh"
        spaced.parent.mkdir()
        log = self.root / "recorder.argv"
        for script in (plain, spaced):
            script.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\0' \"$@\" > {shlex.quote(str(log))}\n"
                "for argument in \"$@\"; do\n"
                "    if [ -f \"$argument\" ]; then file=$argument; fi\n"
                "done\n"
                "printf 'clean\\n' > \"$file\"\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
        # Each form's argv, as real jj gives it, is pinned too, so a naive split cannot pass.
        forms = (
            ("comment", f'"{plain}" # ignored comment', ["<path>"]),
            ("flag", f"{plain} --flag", ["--flag", "<path>"]),
            ("spaced", f"'{spaced}' --x", ["--x", "<path>"]),
            ("escapes", f'"{plain}" a\\ b "c\\"d" \'e\\f\' g\\\\h', ["a b", 'c"d', "e\\f", "g\\h", "<path>"]),
        )

        def recorded(repository: Path, value: str, real: bool) -> list[str]:
            log.unlink(missing_ok=True)
            result = self.jj(repository, "--config", f"ui.editor={json.dumps(value)}", "describe", agent=False, real=real)
            self.assertEqual(result.returncode, 0, result.stderr)
            values = log.read_bytes().decode("utf-8").split("\0")[:-1]
            return ["<path>" if item.endswith(".jjdescription") else item for item in values]

        for label, value, expected in forms:
            with self.subTest(form=label):
                direct = recorded(self.repository(False), value, real=True)
                wrapped = recorded(self.repository(False), value, real=False)
                self.assertEqual(direct, expected)
                self.assertEqual(wrapped, direct)

    def test_jj_message_commit_editor_scans_only_the_edited_description(self) -> None:
        for agent in (False, True):
            with self.subTest(agent=agent, case="editor-replaces-leaking-message"):
                editor, _ = self.editor("clean\n")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_clean(
                    repository,
                    agent,
                    "commit",
                    "--editor",
                    "-m",
                    "Quillfeather Lane 9",
                    extra={"JJ_EDITOR": editor},
                )
                self.assertEqual(self.description(repository, agent, "@-"), "clean\n")
            with self.subTest(agent=agent, case="editor-introduces-leaking-message"):
                editor, _ = self.editor("Quillfeather Lane 9\n")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_refused(
                    repository,
                    agent,
                    "commit message line 1",
                    "commit",
                    "--editor",
                    "-m",
                    "clean",
                    extra={"JJ_EDITOR": editor},
                )

    def test_jj_message_single_editor_reads_describe_headers_as_jj_lines(self) -> None:
        # DeepReview 5: one description is cleaned whole; a `JJ: describe` line is only a JJ: line.
        for agent in (False, True):
            for label, text in (
                ("term-before-header", "Quillfeather Lane 9\nJJ: describe fake -------\nclean\n"),
                ("term-in-header", "JJ: describe Quillfeather Lane 9 -------\nzorbleplex\n"),
            ):
                with self.subTest(agent=agent, case=label):
                    editor, _ = self.editor(text)
                    repository = self.repository(agent)
                    self.write(repository, "clean.txt", "clean\n")
                    self.assert_refused(repository, agent, "commit message line 1", "describe", extra={"JJ_EDITOR": editor})
            with self.subTest(agent=agent, case="clean-header-line"):
                editor, _ = self.editor("clean\nJJ: describe Quillfeather Lane 9 -------\nmore\n")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_clean(repository, agent, "describe", extra={"JJ_EDITOR": editor})
                self.assertEqual(self.description(repository, agent), "clean\nmore\n")

    def bulk_editor(self, awk_program: str) -> str:
        """An editor that rewrites the multi-revision description file with AWK_PROGRAM."""
        self.editors += 1
        return str(write_executable(
            self.root / f"bulk-editor-{self.editors}.sh",
            f"#!/bin/sh\nawk {shlex.quote(awk_program)} \"$1\" > \"$1.new\"\nmv \"$1.new\" \"$1\"\n",
        ))

    def test_jj_message_bulk_editor_checks_only_validated_sections(self) -> None:
        # jj writes the two sections parents first: @-- (first), then @- (second). Each editor adds `JJ: ignore-rest`
        # below the first description's own line.
        ignore_first = (
            '/^JJ: describe / { count++; print; if (count == 1) below = 1; next } '
            'below { print; print "JJ: ignore-rest"; below = 0; next } { print }'
        )
        for agent in (False, True):
            for label, program in (
                # The term on a kept line of the second description, after the file's last `JJ:` line.
                ("kept-second-section", ignore_first + ' END { print "Quillfeather Lane 9" }'),
                # The term only after `JJ: ignore-rest`, in the first description.
                ("ignored-first-section", ignore_first.replace('print "JJ: ignore-rest";', 'print "JJ: ignore-rest"; print "Quillfeather Lane 9";')),
            ):
                with self.subTest(agent=agent, case=label):
                    repository = self.repository(agent)
                    for name in ("first", "second"):
                        self.write(repository, f"{name}.txt", f"{name}\n")
                        self.assert_clean(repository, agent, "commit", "-m", name)
                    arguments = ("describe", "-r", "@-", "-r", "@--")
                    editor = {"JJ_EDITOR": self.bulk_editor(program)}
                    if label == "kept-second-section":
                        place = f"description of {self.commit_id(repository, agent, '@-')[:12]} line 3"
                        self.assert_refused(repository, agent, place, *arguments, extra=editor)
                    else:
                        self.assert_clean(repository, agent, *arguments, extra=editor)
                    # Both descriptions stay as they were.
                    self.assertEqual((self.description(repository, agent, "@--"), self.description(repository, agent, "@-")), ("first\n", "second\n"))

    def test_jj_message_editor_wrapper_loop_refuses_before_a_child_runs(self) -> None:
        for agent in (False, True):
            with self.subTest(agent=agent, case="shim"):
                self.set_user_editor(json.dumps(str(CHECK)))
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                expected = self.expected_state(repository, agent)
                result = self.jj(repository, "describe", agent=agent)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("configured jj editor resolves to leak-terms-check", result.stderr)
                self.assertEqual(self.state(repository, agent), expected)
                self.assert_hidden(result)

            with self.subTest(agent=agent, case="wrapper"):
                description = self.root / f"description-{agent}"
                description.write_text("clean\n", encoding="utf-8")
                result = self.launch(
                    [str(CHECK), "jj-editor", str(description)],
                    env=self.environment(agent, DOTFILES_JJ_EDITOR=json.dumps([str(CHECK), "jj-editor"])),
                    timeout=10,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("recursive jj editor wrapper refused", result.stderr)
                self.assertEqual(description.read_text(encoding="utf-8"), "clean\n")
                self.assert_hidden(result)

            with self.subTest(agent=agent, case="already-active"):
                description = self.root / f"active-description-{agent}"
                description.write_text("clean\n", encoding="utf-8")
                ran = self.root / f"unexpected-editor-child-{agent}"
                editor = write_executable(self.root / f"unexpected-editor-{agent}.sh", f"#!/bin/sh\n: > {shlex.quote(str(ran))}\n")
                result = self.launch(
                    [str(CHECK), "jj-editor", str(description)],
                    env=self.environment(
                        agent,
                        DOTFILES_JJ_EDITOR=json.dumps([str(editor)]),
                        DOTFILES_JJ_EDITOR_WRAPPER_ACTIVE="1",
                    ),
                    timeout=10,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("recursive jj editor wrapper refused", result.stderr)
                self.assertFalse(ran.exists())
                self.assert_hidden(result)

    def test_jj_message_editor_preflight_names_an_unreadable_editor(self) -> None:
        # An editor jj's configuration cannot give (an empty array) is a configuration the check cannot read, not the
        # wrapper itself.
        for agent in (False, True):
            with self.subTest(agent=agent):
                self.set_user_editor("[]")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                expected = self.expected_state(repository, agent)
                result = self.jj(repository, "describe", agent=agent)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("jj shim: cannot read jj's configured editor for the leak-term check; nothing changed", result.stderr)
                self.assertNotIn("resolves to leak-terms-check", result.stderr)
                self.assertEqual(self.state(repository, agent), expected)
                self.assert_hidden(result)

    def test_jj_without_a_real_binary_refuses_without_recursing(self) -> None:
        result = self.launch(
            [str(ROOT / "shims" / "jj"), "status"],
            cwd=self.root,
            env=self.environment(
                False,
                PATH=str(ROOT / "shims"),
                DOTFILES_DIR=str(self.root / "missing-dotfiles"),
            ),
            timeout=10,
        )
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertIn("jj shim: no real jj found on PATH", result.stderr)
        self.assertNotIn("recurs", result.stderr.casefold())

    def test_jj_message_uses_an_attached_repository_for_trailer_decisions(self) -> None:
        overlay = self.root / f"omp-attribution-{self.session}.toml"
        overlay.write_text(
            f"[templates]\ncommit_trailers = '\"Omp-Session: {self.session}\"'\n",
            encoding="utf-8",
        )
        repository = self.repository(False)
        result = self.jj(
            self.root,
            f"-R={repository}",
            "describe",
            "-m",
            "clean",
            agent=False,
            extra={"JJ_CONFIG": f"{self.config}{os.pathsep}{overlay}"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.description(repository, False), "clean\n")

    def test_jj_message_reads_a_repository_after_the_command_for_trailer_decisions(self) -> None:
        # A writing command the shim does not check (`jj squash`) names its repository after the command: the trailer
        # decision is that repository's, not the current directory's.
        overlay = self.root / f"omp-attribution-{self.session}.toml"
        overlay.write_text(
            f"[templates]\ncommit_trailers = '\"Omp-Session: {self.session}\"'\n",
            encoding="utf-8",
        )
        repository = self.repository(False)
        made = self.jj(repository, "commit", "-m", "base", agent=False)
        self.assertEqual(made.returncode, 0, made.stderr)
        result = self.jj(
            self.root,
            "squash",
            "-R",
            str(repository),
            "-m",
            "clean",
            agent=False,
            extra={"JJ_CONFIG": f"{self.config}{os.pathsep}{overlay}"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.description(repository, False, "@-"), "clean\n")

    def test_jj_term_locks_workspace_add_after_global_options(self) -> None:
        repository = self.repository(False)
        workspace = self.root / "workspace"
        result = self.jj(repository, "workspace", "--quiet", "add", str(workspace), agent=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        listed = self.launch(["/usr/bin/git", "-C", str(workspace), "worktree", "list", "--porcelain"], env=self.person)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("locked jj shim: locked on add (AGENTC-431)", listed.stdout)

    def test_jj_term_workspace_add_with_an_attached_name_and_help(self) -> None:
        repository = self.repository(False)
        workspace = self.root / "named-workspace"
        result = self.jj(repository, "workspace", "add", "--quiet", "--name=w2", str(workspace), agent=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        listed = self.launch(["/usr/bin/git", "-C", str(workspace), "worktree", "list", "--porcelain"], env=self.person)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("locked jj shim: locked on add (AGENTC-431)", listed.stdout)
        helped = self.jj(repository, "workspace", "add", "--help", agent=False)
        self.assertEqual(helped.returncode, 0, helped.stderr)
        self.assertIn("Usage:", helped.stdout)

    def test_unreadable_lists_allow_empty_jj_new_but_refuse_a_clean_file(self) -> None:
        cases = [case for case in unreadable_lists(self.root, self.person["PATH"]) if case[0] in {"key", "missing"}]
        self.assertEqual({case[0] for case in cases}, {"key", "missing"})
        for agent in (False, True):
            for label, extra, correction in cases:
                with self.subTest(agent=agent, case=label, empty=True):
                    repository = self.repository(agent)
                    before = self.commit_id(repository, agent, "@")
                    result = self.jj(repository, "new", "--quiet", agent=agent, extra=extra)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotEqual(self.commit_id(repository, agent, "@"), before)
                    self.assert_hidden(result)
                with self.subTest(agent=agent, case=label, empty=False):
                    repository = self.repository(agent)
                    self.write(repository, "clean.txt", "clean\n")
                    expected = self.expected_state(repository, agent)
                    result = self.jj(repository, "new", "--quiet", agent=agent, extra=extra)
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assert_one_correction_line(result, correction)
                    self.assertEqual(self.state(repository, agent), expected)

    def test_unreadable_lists_refuse_jj_commits(self) -> None:
        cases = unreadable_lists(self.root, self.person["PATH"])
        for agent in (False, True):
            with self.subTest(agent=agent, case="healthy-list-control"):
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_clean(repository, agent, "commit", "-m", "ok")
                self.assertEqual(self.description(repository, agent, "@-"), "ok\n")
            for label, extra, correction in cases:
                with self.subTest(agent=agent, case=label):
                    repository = self.repository(agent)
                    self.write(repository, "clean.txt", "clean\n")
                    expected = self.expected_state(repository, agent)
                    result = self.jj(repository, "commit", "-m", "ok", agent=agent, extra=extra)
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assert_one_correction_line(result, correction)
                    self.assertEqual(self.state(repository, agent), expected)


class SinkRepositoryCase(LeakTestCase):
    """self.repo, whose origin is PUBLIC_URL, which git reaches through the test sink helper (remote.origin.vcs=sink), in
    a HOME of its own with a fresh visibility cache, a gh that answers nothing and a stub public leak check that logs
    each call to self.public_log; its commits and jj's are IDENTITY's."""

    public_url = "https://github.com/theorem-labs/rocq.git"
    identity = "Sink Test"

    def setUp(self) -> None:
        super().setUp()
        self.private_home()
        self.fake = self.root / "fake"
        self.sinks = self.root / "sinks"
        for directory in (self.fake, self.sinks):
            directory.mkdir()
        self.cache = self.home / ".cache" / "public-leak-check"
        self.cache.mkdir(parents=True)
        for name, visibility in (("rocq", "public"), ("private-fixture", "private"), ("other-public", "public")):
            (self.cache / f"theorem-labs_{name}").write_text(visibility, encoding="utf-8")
        self.public_log = self.root / "public-check.log"
        write_executable(self.fake / "gh", "#!/bin/bash\nexit 1\n")
        write_executable(self.fake / "git-remote-sink", SINK_HELPER.replace("@SINKS@", str(self.sinks)))
        self.check = write_executable(
            self.root / "public-leak-check",
            f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(self.public_log))}\nexit 0\n",
        )
        email = f"{self.identity.lower().replace(' ', '-')}@example.invalid"
        self.jj_config = self.root / "jj.toml"
        self.jj_config.write_text(
            f'[user]\nname = "{self.identity}"\nemail = "{email}"\n[signing]\nbehavior = "drop"\n', encoding="utf-8"
        )
        self.person = {
            **base_environment(),
            **fixture_environment(),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.xdg),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "TMPDIR": str(self.tmpdir),
            "PATH": os.pathsep.join([str(self.fake), str(ROOT / "shims"), str(Path(real_jj()).parent), os.environ["PATH"]]),
            "PUBLIC_LEAK_CHECK": str(self.check),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ALLOW_PROTOCOL": "sink",
            "JJ_CONFIG": str(self.jj_config),
        }
        self.agent = {**self.person, "OMP_SESSION_ID": SESSION}
        self.repo = self.root / "repo"
        self.repo.mkdir()
        for arguments in (
            ("init", "-q", "-b", "main"),
            ("config", "user.name", self.identity),
            ("config", "user.email", email),
            ("commit", "-q", "--allow-empty", "-m", "base"),
            ("remote", "add", "origin", self.public_url),
            ("config", "remote.origin.vcs", "sink"),
        ):
            made = self.launch(["/usr/bin/git", *arguments], cwd=self.repo, env=self.person)
            self.assertEqual(made.returncode, 0, made.stderr)


class OutgoingLeaks(SinkRepositoryCase):
    """Outgoing Git and JJ commits are scanned independently of the public-leak gate."""

    unknown_url = "https://github.com/theorem-labs/unreadable-fixture.git"
    private_url = "https://github.com/theorem-labs/private-fixture.git"
    term = TEST_TERMS[0]
    identity = "Push Test"

    def setUp(self) -> None:
        super().setUp()
        self.base = self.real("rev-parse", "HEAD")
        self.made = 0

    def callers(self) -> tuple[tuple[bool, dict[str, str]], ...]:
        return ((True, self.agent), (False, self.person))

    def combinations(self) -> list[tuple[bool, dict[str, str], str]]:
        """An agent and a person, each to a public remote and to one whose visibility cannot be read."""
        return [(agent, environment, url) for url in (self.public_url, self.unknown_url) for agent, environment in self.callers()]

    def unique(self, label: str) -> str:
        self.made += 1
        return f"{label}-{self.made}"

    def fresh_public_url(self) -> str:
        """A public repository whose sink nothing has reached yet."""
        name = self.unique("fresh-public")
        (self.cache / f"theorem-labs_{name}").write_text("public", encoding="utf-8")
        return f"https://github.com/theorem-labs/{name}.git"

    def command(self, command: list[str], environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return self.launch(command, cwd=self.repo, env=environment or self.agent)

    def real(self, *arguments: str, environment: dict[str, str] | None = None) -> str:
        result = self.command(["/usr/bin/git", *arguments], environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def git_push(self, remote: str, commit: str, ref: str, environment: dict[str, str], *options: str) -> subprocess.CompletedProcess[str]:
        self.refresh_visibility()
        return self.command(["git", "push", *options, remote, f"{commit}:refs/heads/{ref}"], environment)

    def commit(self, path: str, content: str, message: str, parent: str | None = None,
               shim: dict[str, str] | None = None) -> str:
        """A commit of PATH holding CONTENT, on PARENT (else on HEAD). /usr/bin/git makes it, or, with SHIM the caller's
        environment, `git commit --no-verify` through the shim: neither checks it."""
        if parent is not None:
            self.real("checkout", "-q", "--detach", parent)
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content + "\n", encoding="utf-8")
        self.real("add", path)
        if shim is None:
            self.real("commit", "--no-verify", "-m", message)
        else:
            made = self.command(["git", "commit", "--no-verify", "-m", message], shim)
            self.assertEqual(made.returncode, 0, made.stderr)
        return self.real("rev-parse", "HEAD")

    def sink(self, url: str) -> Path:
        """The bare repository the sink helper keeps for URL. git hands the helper the address of a `sink::<address>`
        URL, so both forms of one address reach one sink."""
        address = url.split("::", 1)[1] if "::" in url else url
        return self.sinks / f"{hashlib.sha1(address.encode()).hexdigest()[:16]}.git"

    def sink_git(self, url: str, *arguments: str, input: str | None = None) -> str:
        result = self.launch(["/usr/bin/git", "--git-dir", str(self.sink(url)), *arguments], env=self.person, input=input)
        self.assertEqual(result.returncode, 0, f"cannot read the sink of {url}: {result.stderr}")
        return result.stdout

    def received(self, commit: str, url: str) -> bool:
        """Whether the sink of URL holds COMMIT; a sink not made yet holds nothing, and one that cannot be read fails."""
        if not self.sink(url).exists():
            return False
        answer = self.sink_git(url, "cat-file", "--batch-check", input=f"{commit}\n")
        self.assertRegex(answer, rf"\A{commit} (missing|commit \d+)\n\Z")
        return not answer.endswith(" missing\n")

    def sink_state(self, url: str) -> tuple[str, str]:
        """Every ref and every object the sink of URL holds: none for a sink not made yet; a sink that exists and
        cannot be read fails the test."""
        if not self.sink(url).exists():
            return "", ""
        refs = self.sink_git(url, "for-each-ref", "--format=%(objectname) %(refname)")
        objects = self.sink_git(url, "cat-file", "--batch-all-objects", "--batch-check")
        return refs, objects

    def set_url(self, url: str) -> None:
        self.refresh_visibility()
        self.real("remote", "set-url", "origin", url)

    def refresh_visibility(self) -> None:
        """Keep every cached visibility fresh: the cache trusts a private entry for 60 s only, and a long test
        would otherwise read a private remote as unknown."""
        for entry in self.cache.iterdir():
            os.utime(entry)

    def public_checks(self) -> int:
        return len(self.public_log.read_text(encoding="utf-8").splitlines()) if self.public_log.exists() else 0

    def assert_term_refusal(self, result: subprocess.CompletedProcess[str], commit: str, place: str, url: str,
                            shim: str = "git") -> None:
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"error: leak terms: commit {commit[:12]} {place}: listed leak term", result.stderr)
        self.assertIn(f"error: {shim} shim: this push to {url} holds a listed leak term. Nothing was pushed.", result.stderr)
        self.assertFalse(self.received(commit, url), result.stderr)
        self.assert_hidden(result)

    def assert_push_refused(self, push, commit: str, place: str, url: str, shim: str = "git") -> subprocess.CompletedProcess[str]:
        """PUSH() is refused for the term at PLACE of COMMIT, and the sink of URL keeps exactly the refs and objects
        it had."""
        before = self.sink_state(url)
        result = push()
        self.assert_term_refusal(result, commit, place, url, shim)
        self.assertEqual(self.sink_state(url), before, "a refused push changed the sink")
        return result

    def assert_pushed(self, result: subprocess.CompletedProcess[str], url: str, *commits: str) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        for commit in commits:
            self.assertTrue(self.received(commit, url), result.stderr)
        self.assert_hidden(result)

    def init_jj(self) -> None:
        result = self.command(["jj", "git", "init", "--colocate"])
        self.assertEqual(result.returncode, 0, result.stderr)

    def jj_ok(self, environment: dict[str, str], *arguments: str) -> str:
        result = self.command(["jj", *arguments], environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def jj_push(self, commit: str, bookmark: str, environment: dict[str, str], executable: bool = False) -> subprocess.CompletedProcess[str]:
        self.jj_ok(environment, "git", "import")
        self.jj_ok(environment, "bookmark", "set", "--allow-backwards", bookmark, "-r", commit)
        config = ["--config", "git.executable-path=/usr/bin/git"] if executable else []
        self.refresh_visibility()
        return self.command(["jj", *config, "git", "push", "--bookmark", bookmark], environment)

    def squash_commit(self, path: str, content: str, environment: dict[str, str]) -> str:
        """A commit made by `jj squash`: PATH, holding CONTENT, moved from a new working-copy commit into its parent."""
        self.jj_ok(environment, "new", self.base, "-m", "squashed")
        self.jj_ok(environment, "new")
        (self.repo / path).write_text(content + "\n", encoding="utf-8")
        self.jj_ok(environment, "squash")
        made = self.jj_ok(environment, "log", "--no-graph", "-r", "@-", "-T", 'commit_id ++ "\\n" ++ description')
        commit, description = made.split("\n", 1)
        self.assertEqual(description, "squashed\n")
        self.assertEqual(self.jj_ok(environment, "diff", "--name-only", "-r", commit).split(), [path])
        return commit

    def without_public_check(self, environment: dict[str, str]) -> dict[str, str]:
        """ENVIRONMENT with no public leak check in any place the hook looks: PUBLIC_LEAK_CHECK, public-leak-check.sh
        on PATH, and the pointer file under XDG_CONFIG_HOME."""
        empty_config = self.root / "no-check-config"
        empty_config.mkdir(exist_ok=True)
        stripped = {name: value for name, value in environment.items() if name != "PUBLIC_LEAK_CHECK"}
        stripped["XDG_CONFIG_HOME"] = str(empty_config)
        stripped["PATH"] = os.pathsep.join(
            entry for entry in environment["PATH"].split(os.pathsep) if not (Path(entry) / "public-leak-check.sh").exists()
        )
        self.assert_no_public_check(stripped)
        return stripped

    def assert_no_public_check(self, environment: dict[str, str]) -> None:
        self.assertNotIn("PUBLIC_LEAK_CHECK", environment)
        self.assertIsNone(shutil.which("public-leak-check.sh", path=environment["PATH"]))
        pointer = Path(environment.get("XDG_CONFIG_HOME") or Path(environment["HOME"]) / ".config") / "public-leak-check" / "path"
        self.assertFalse(pointer.exists(), pointer)

    def test_push_term_gate_parses_verification_and_destination_forms(self) -> None:
        # Each case pushes its own leaky commit, so one that leaks cannot hide the next.
        destination = f"sink::{self.public_url}"
        self.set_url(self.private_url)
        self.real("config", f"url.{destination}.insteadOf", "alias")
        with self.subTest(option="insteadof-alias"):
            leaky = self.commit("alias.txt", self.term, "clean")
            self.assert_push_refused(
                lambda: self.command(["git", "push", "alias", f"{leaky}:refs/heads/alias"], self.person), leaky, "alias.txt:1", destination
            )

        for label, options in (
            ("no-veri", ("--no-veri",)),
            ("push-opt", ("--push-opt", "marker")),
            ("short-value", ("-o", "--no-verify")),
            ("attached-value", ("--push-option=--no-verify",)),
        ):
            with self.subTest(option=label):
                leaky = self.commit(f"{label}.txt", self.term, "clean")
                self.assert_push_refused(
                    lambda: self.command(["git", "push", *options, destination, f"{leaky}:refs/heads/{label}"], self.person),
                    leaky,
                    f"{label}.txt:1",
                    destination,
                )
        with self.subTest(option="ambiguous"):
            leaky = self.commit("ambiguous.txt", self.term, "clean")
            before = self.sink_state(destination)
            ambiguous = self.command(
                ["git", "push", "--no-v", destination, f"{leaky}:refs/heads/ambiguous"],
                self.person,
            )
            self.assertEqual(ambiguous.returncode, 1, ambiguous.stderr)
            self.assertIn("git shim: cannot read option --no-v, so the leak-term check cannot run. Nothing was pushed.", ambiguous.stderr)
            self.assertEqual(self.sink_state(destination), before)

        with self.subTest(option="bare-push-default"):
            leaky = self.commit("bare.txt", self.term, "clean")
            self.real("remote", "remove", "origin")
            self.real("config", "push.default", "current")
            self.real("config", "remote.pushDefault", destination)
            self.assert_push_refused(lambda: self.command(["git", "push"], self.person), leaky, "bare.txt:1", destination)

    def test_push_refuses_jj_git_push_with_nested_global_options(self) -> None:
        self.init_jj()
        cases = (
            ("config-separate", ("--config", "git.executable-path=/usr/bin/git")),
            ("config-attached", ("--config=git.executable-path=/usr/bin/git",)),
            ("repository", ("-R", str(self.repo), "--config", "git.executable-path=/usr/bin/git")),
            ("quiet", ("--quiet", "--config", "git.executable-path=/usr/bin/git")),
        )
        for agent, environment in self.callers():
            for label, options in cases:
                with self.subTest(agent=agent, option=label):
                    url = self.fresh_public_url()
                    self.set_url(url)
                    path = f"{self.unique(label)}.txt"
                    leaky = self.commit(path, self.term, "clean", parent=self.base)
                    bookmark = self.unique("bookmark")
                    self.jj_ok(environment, "git", "import")
                    self.jj_ok(environment, "bookmark", "set", "--allow-backwards", bookmark, "-r", leaky)
                    self.refresh_visibility()
                    self.assert_push_refused(
                        lambda: self.command(["jj", "git", *options, "push", "--bookmark", bookmark], environment),
                        leaky,
                        f"{path}:1",
                        url,
                        "jj",
                    )
        before = self.sink_state(self.public_url)
        deleted = self.command(["jj", "git", "--quiet", "push", "--deleted"], self.agent)
        self.assertEqual(deleted.returncode, 1, deleted.stderr)
        self.assertIn("jj shim: `jj git push --deleted` is refused in an agent session.", deleted.stderr)
        self.assertEqual(self.sink_state(self.public_url), before)

    def test_push_refuses_an_alias_when_the_decoder_fails(self) -> None:
        self.real("config", "alias.p", "push")
        failing_python = self.root / "failing-python"
        write_executable(
            failing_python / "python3",
            "#!/bin/sh\nif [ \"$1\" = - ]; then exit 71; fi\nexec /usr/bin/python3 \"$@\"\n",
        )
        leaky = self.commit("alias-decoder.txt", self.term, "clean", parent=self.base)
        before = self.sink_state(self.public_url)
        result = self.command(
            ["git", "p", "origin", f"{leaky}:refs/heads/{self.unique('alias-decoder')}"],
            {**self.person, "PATH": f"{failing_python}{os.pathsep}{self.person['PATH']}"},
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(
            result.stderr,
            "git shim: cannot read alias p, so the leak-term check cannot run. Nothing was changed.\n",
        )
        self.assertEqual(self.sink_state(self.public_url), before)
        self.assert_hidden(result)

    def test_unreadable_push_allows_an_advertised_tag_but_refuses_a_new_commit(self) -> None:
        cases = [case for case in unreadable_lists(self.root, self.person["PATH"]) if case[0] in {"key", "missing"}]
        self.assertEqual({case[0] for case in cases}, {"key", "missing"})
        self.real("push", "origin", f"{self.base}:refs/heads/base")
        for label, extra, correction in cases:
            with self.subTest(case=label, kind="tag"):
                tag = self.unique("advertised")
                self.real("tag", tag, self.base)
                self.refresh_visibility()
                tag_result = self.command(["git", "push", "origin", f"refs/tags/{tag}"], {**self.agent, **extra})
                self.assertEqual(tag_result.returncode, 0, tag_result.stderr)
                self.sink_git(self.public_url, "show-ref", "--verify", f"refs/tags/{tag}")
                self.assert_hidden(tag_result)
            with self.subTest(case=label, kind="commit"):
                path = f"{self.unique('new')}.txt"
                commit = self.commit(path, "clean", "clean", parent=self.base)
                before = self.sink_state(self.public_url)
                self.refresh_visibility()
                commit_result = self.command(
                    ["git", "push", "origin", f"{commit}:refs/heads/{self.unique('new')}"],
                    {**self.agent, **extra},
                )
                self.assertNotEqual(commit_result.returncode, 0, commit_result.stderr)
                self.assert_one_correction_line(commit_result, correction)
                self.assertEqual(self.sink_state(self.public_url), before)

    def test_push_term_gate_ignores_replacement_objects(self) -> None:
        leaky = self.commit("replace-leaky.txt", self.term, "clean")
        clean = self.commit("replace-clean.txt", "clean", "clean")
        self.real("replace", leaky, clean)
        destination = f"sink::{self.public_url}"
        self.assert_push_refused(
            lambda: self.command(["git", "push", destination, f"{leaky}:refs/heads/replaced"], self.person),
            leaky,
            "replace-leaky.txt:1",
            destination,
        )

    def test_push_refuses_through_global_options_aliases_and_binary_attributes(self) -> None:
        # Git 2.43 reads `--attr-source <tree-ish>` and `--shallow-file <path>` with the value in the next argument;
        # `git p` with `alias.p=push` runs git's own push; and a text file that `.gitattributes` marks `-diff` is still
        # text. Each of these pushes reaches the public sink unchecked when the shim misreads it.
        self.real("config", "alias.p", "push")
        cases = (
            ("attr-source", ("--attr-source", "HEAD", "push"), None),
            ("shallow-file", ("--shallow-file", "", "push"), None),
            ("alias", ("p",), None),
            ("minus-diff", ("push",), "*.txt -diff\n"),
        )

        def make(path: str, content: str, attributes: str | None) -> str:
            """A commit on the base holding PATH with CONTENT and, with ATTRIBUTES, the `.gitattributes` beside it."""
            if attributes is None:
                return self.commit(path, content, "clean", parent=self.base)
            self.real("checkout", "-q", "--detach", self.base)
            (self.repo / ".gitattributes").write_text(attributes, encoding="utf-8")
            self.real("add", ".gitattributes")
            return self.commit(path, content, "clean")

        for agent, environment in self.callers():
            for label, arguments, attributes in cases:
                with self.subTest(agent=agent, case=label):
                    path = f"{self.unique(label)}.txt"
                    leaky = make(path, self.term, attributes)
                    ref = self.unique("refused")
                    self.refresh_visibility()
                    self.assert_push_refused(
                        lambda: self.command(["git", *arguments, "origin", f"{leaky}:refs/heads/{ref}"], environment),
                        leaky,
                        f"{path}:1",
                        self.public_url,
                    )
                    # The negative control: the same push of a commit with no term goes on.
                    clean = make(f"{self.unique(label)}.txt", "clean", attributes)
                    self.refresh_visibility()
                    pushed = self.command(["git", *arguments, "origin", f"{clean}:refs/heads/{self.unique('clean')}"], environment)
                    self.assert_pushed(pushed, self.public_url, clean)

    def test_push_refuses_files_external_attributes_mark_binary(self) -> None:
        # The push scanner runs in this checkout, so attributes outside the commit tree must not
        # hide a text blob. Exercise both Git's hook and jj's direct scanner for every caller.
        info_attributes = self.repo / ".git" / "info" / "attributes"
        global_attributes = self.xdg / "git" / "attributes"
        self.init_jj()
        for label, attributes in (("info", info_attributes), ("global", global_attributes)):
            for agent, environment in self.callers():
                with self.subTest(attributes=label, agent=agent):
                    info_attributes.unlink(missing_ok=True)
                    global_attributes.unlink(missing_ok=True)
                    attributes.parent.mkdir(parents=True, exist_ok=True)
                    attributes.write_text("*.txt -diff\n" if label == "info" else "*.txt binary\n", encoding="utf-8")
                    path = f"{self.unique(label)}.txt"
                    leaky = self.commit(path, self.term, "clean", parent=self.base)
                    git_url = self.fresh_public_url()
                    self.set_url(git_url)
                    self.assert_push_refused(
                        lambda: self.git_push("origin", leaky, self.unique("git"), environment),
                        leaky,
                        f"{path}:1",
                        git_url,
                    )
                    jj_url = self.fresh_public_url()
                    self.set_url(jj_url)
                    self.assert_push_refused(
                        lambda: self.jj_push(leaky, self.unique("jj"), environment),
                        leaky,
                        f"{path}:1",
                        jj_url,
                        "jj",
                    )

    def test_push_allows_real_binary_with_info_attributes(self) -> None:
        # A non-empty info/attributes file makes the producer use --text. Binary NUL bytes must
        # not crash that framed producer, and a binary file with no term still pushes.
        info_attributes = self.repo / ".git" / "info" / "attributes"
        info_attributes.write_text("*.bin -diff\n", encoding="utf-8")
        commits: list[tuple[bool, dict[str, str], str]] = []
        for agent, environment in self.callers():
            self.real("checkout", "-q", "--detach", self.base)
            path = f"{self.unique('binary-info')}.bin"
            (self.repo / path).write_bytes(b"\0clean\n")
            self.real("add", path)
            self.real("commit", "--no-verify", "-m", "binary")
            commits.append((agent, environment, self.real("rev-parse", "HEAD")))
        self.init_jj()
        for agent, environment, commit in commits:
            with self.subTest(agent=agent, route="git"):
                url = self.fresh_public_url()
                self.set_url(url)
                self.assert_pushed(self.git_push("origin", commit, self.unique("binary-git"), environment), url, commit)
            with self.subTest(agent=agent, route="jj"):
                url = self.fresh_public_url()
                self.set_url(url)
                self.assert_pushed(self.jj_push(commit, self.unique("binary-jj"), environment), url, commit)

    def test_push_allows_real_binary_file_content(self) -> None:
        # With no info/attributes override, Git's binary detection omits this NUL-containing
        # blob, even though its bytes contain a listed term. Test direct Git and jj push routes.
        commits: list[tuple[bool, dict[str, str], str]] = []
        for agent, environment in self.callers():
            self.real("checkout", "-q", "--detach", self.base)
            path = f"{self.unique('binary')}.bin"
            (self.repo / path).write_bytes(b"\0Quillfeather Lane 9\n")
            self.real("add", path)
            self.real("commit", "--no-verify", "-m", "binary")
            commits.append((agent, environment, self.real("rev-parse", "HEAD")))
        self.init_jj()
        for agent, environment, commit in commits:
            with self.subTest(agent=agent, route="git"):
                url = self.fresh_public_url()
                self.set_url(url)
                self.assert_pushed(self.git_push("origin", commit, self.unique("binary-git"), environment), url, commit)
            with self.subTest(agent=agent, route="jj"):
                url = self.fresh_public_url()
                self.set_url(url)
                self.assert_pushed(self.jj_push(commit, self.unique("binary-jj"), environment), url, commit)

    def test_push_term_gate_parses_negations_end_of_options_and_short_values(self) -> None:
        # Each case pushes its own leaky commit, so one that leaks cannot hide the next.
        destination = f"sink::{self.public_url}"
        self.set_url(self.private_url)
        for label, options in (
            ("no-no-verify", ("--no-no-verify", "--no-verif")),
            ("no-force-with-lease", ("--no-force-with-lease", "--no-verify")),
            ("end-of-options", ("--no-verify", "--end-of-options")),
            ("short-attached", ("-ovalue", "--no-verify")),
            ("short-bundled-value", ("-oo", "--no-verify")),
            # git pushes to the positional destination, not to --repo's unguarded origin.
            ("repo-beside-positional", ("--repo=origin",)),
        ):
            with self.subTest(option=label):
                leaky = self.commit(f"{label}.txt", self.term, "clean")
                self.assert_push_refused(
                    lambda: self.command(["git", "push", *options, destination, f"{leaky}:refs/heads/{label}"], self.person),
                    leaky,
                    f"{label}.txt:1",
                    destination,
                )

    def test_push_term_uses_utf8_when_log_output_encoding_is_configured(self) -> None:
        # i18n.logOutputEncoding re-encodes messages, not file content, so the term is the message.
        term = "Café leak"
        term_file = encrypt_yaml(f"terms: |\n  {term}\n")
        commit = self.commit("encoding.txt", "clean", term)
        self.real("config", "i18n.logOutputEncoding", "ISO-8859-1")
        result = self.assert_push_refused(
            lambda: self.git_push("origin", commit, "encoding", self.person | {"DOTFILES_LEAK_TERMS_FILE": str(term_file)}),
            commit,
            "message line 1",
            self.public_url,
        )
        self.assertNotIn("Caf", result.stderr)

    def test_push_agent_guarded_checkout_keeps_its_public_check_for_private_url(self) -> None:
        clean = self.commit("private-direct-clean.txt", "clean", "clean")
        invoked = self.root / "private-direct-public-check"
        write_executable(self.check, f"#!/bin/sh\n: > {shlex.quote(str(invoked))}\nexit 9\n")
        result = self.command(
            ["git", "push", f"sink::{self.private_url}", f"{clean}:refs/heads/private-direct"],
            self.agent,
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertTrue(invoked.exists(), result.stderr)
        self.assertFalse(self.received(clean, self.private_url), result.stderr)

    def test_push_options_keep_their_values_after_verification_is_rebuilt(self) -> None:
        destination = f"sink::{self.public_url}"
        self.real("push", "--no-verify", destination, f"{self.base}:refs/heads/bootstrap")
        received = self.root / "received-push-option"
        write_executable(
            self.sink(self.public_url) / "hooks" / "pre-receive",
            f"#!/bin/sh\nprintf '%s' \"${{GIT_PUSH_OPTION_0-unset}}\" > {shlex.quote(str(received))}\n",
        )
        self.sink_git(self.public_url, "config", "receive.advertisePushOptions", "true")
        clean = self.commit("push-option-clean.txt", "clean", "clean")
        for label, options, expected in (
            ("no-veri", ("--no-veri",), "unset"),
            ("long", ("--push-opt", "marker"), "marker"),
            ("short", ("-o", "--no-verify"), "--no-verify"),
            ("attached", ("--push-option=--no-verify",), "--no-verify"),
            ("no-no-verify", ("--no-no-verify",), "unset"),
            ("no-force-with-lease", ("--no-force-with-lease",), "unset"),
            ("end-of-options", ("--end-of-options",), "unset"),
            ("short-attached", ("-ovalue",), "value"),
            ("short-bundled-value", ("-oo",), "o"),
            ("repo-beside-positional", ("--repo=origin",), "unset"),
        ):
            with self.subTest(option=label):
                result = self.command(
                    ["git", "push", *options, destination, f"{clean}:refs/heads/{label}"],
                    self.person,
                )
                self.assert_pushed(result, self.public_url, clean)
                self.assertEqual(received.read_text(encoding="utf-8"), expected)

    def test_push_refuses_unchecked_git_commits_and_pushes_that_skip_hooks(self) -> None:
        # (1) a commit made through the shim by `git commit --no-verify`, its diff holding the term; (2) a commit made
        # by /usr/bin/git, its message holding it; (3) `git push --no-verify` and (4) `git -c core.hooksPath=<dir>
        # push`, <dir> the caller's own hooks, of a commit like (1).
        own_hooks = self.root / "command-hooks"
        hook_log = self.root / "command-hooks.log"
        write_executable(own_hooks / "pre-push", f"#!/bin/sh\n{{ printf 'argv %s\\n' \"$*\"; cat; }} >> {shlex.quote(str(hook_log))}\n")
        command_hooks = ("-c", f"core.hooksPath={own_hooks}")
        cases = (
            ("1", "shim", self.term, "clean", ()),
            ("2", "absolute-git", "clean", self.term, ()),
            ("3", "shim", self.term, "clean", ("--no-verify",)),
            ("4", "shim", self.term, "clean", command_hooks),
        )

        def push(options: tuple[str, ...], commit: str, ref: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
            if options == command_hooks:
                return self.command(["git", *options, "push", "origin", f"{commit}:refs/heads/{ref}"], environment)
            return self.git_push("origin", commit, ref, environment, *options)

        for case, maker, content, message, options in cases:
            for agent, environment, url in self.combinations():
                with self.subTest(case=case, agent=agent, url=url):
                    hook_log.unlink(missing_ok=True)
                    self.set_url(url)
                    path = f"{self.unique(f'case-{case}')}.txt"
                    commit = self.commit(path, content, message, parent=self.base, shim=environment if maker == "shim" else None)
                    place = f"{path}:1" if content == self.term else "message line 1"
                    ref = self.unique("refused")
                    self.assert_push_refused(lambda: push(options, commit, ref, environment), commit, place, url)
                    self.assertFalse(hook_log.exists(), "the caller's pre-push ran for a refused push")
                    # The negative control: the same commit goes to a private remote.
                    self.set_url(self.private_url)
                    self.assert_pushed(push(options, commit, self.unique("private"), environment), self.private_url, commit)
                    hook_log.unlink(missing_ok=True)
            for agent, environment in self.callers():
                with self.subTest(case=case, agent=agent, control="clean"):
                    hook_log.unlink(missing_ok=True)
                    self.set_url(self.public_url)
                    clean = self.commit(
                        f"{self.unique(f'clean-{case}')}.txt", "clean", "clean", parent=self.base,
                        shim=environment if maker == "shim" else None,
                    )
                    ref = self.unique("clean")
                    if options == command_hooks:
                        # What git gives the caller's pre-push for this push: a dry run of the real git runs it.
                        dry = self.command(["/usr/bin/git", *options, "push", "--dry-run", "origin", f"{clean}:refs/heads/{ref}"], environment)
                        self.assertEqual(dry.returncode, 0, dry.stderr)
                        expected = hook_log.read_text(encoding="utf-8")
                        hook_log.unlink()
                    self.assert_pushed(push(options, clean, ref, environment), self.public_url, clean)
                    if options == command_hooks:
                        # After the clean scan the caller's pre-push ran once, with git's arguments and input.
                        self.assertEqual(hook_log.read_text(encoding="utf-8"), expected)
                        hook_log.unlink()

    def test_push_refuses_through_a_dispatcher_or_wrapper_and_runs_it_once_when_clean(self) -> None:
        # (5) the repository's core.hooksPath holds the public leak check's dispatcher entry: an executable
        # `dispatch` marked PUBLIC-LEAK-CHECK-DISPATCHER, which `pre-push` links to; (5b) its pre-push is the wrapper.
        entry_log = self.root / "entry.log"
        record = f"{{ printf '%s %s\\n' \"${{0##*/}}\" \"$*\"; cat; }} >> {shlex.quote(str(entry_log))}\n"
        for kind in ("dispatcher", "wrapper"):
            hooks = self.root / f"{kind}-hooks"
            if kind == "dispatcher":
                write_executable(hooks / "dispatch", f"#!/bin/sh\n# PUBLIC-LEAK-CHECK-DISPATCHER\n{record}")
                (hooks / "pre-push").symlink_to("dispatch")
            else:
                write_executable(hooks / "pre-push", f"#!/bin/sh\n# PUBLIC-LEAK-CHECK-WRAPPER\n{record}")
            self.real("config", "core.hooksPath", str(hooks))
            for agent, environment, url in self.combinations():
                with self.subTest(kind=kind, agent=agent, url=url):
                    entry_log.unlink(missing_ok=True)
                    self.set_url(url)
                    path = f"{self.unique(kind)}.txt"
                    commit = self.commit(path, self.term, "clean", parent=self.base, shim=environment)
                    ref = self.unique("refused")
                    self.assert_push_refused(lambda: self.git_push("origin", commit, ref, environment), commit, f"{path}:1", url)
                    self.assertFalse(entry_log.exists(), f"the {kind} ran for a refused push")
                    self.set_url(self.private_url)
                    self.assert_pushed(self.git_push("origin", commit, self.unique("private"), environment), self.private_url, commit)
                    entry_log.unlink(missing_ok=True)
            for agent, environment in self.callers():
                with self.subTest(kind=kind, agent=agent, control="clean"):
                    entry_log.unlink(missing_ok=True)
                    self.set_url(self.public_url)
                    clean = self.commit(f"{self.unique(kind)}.txt", "clean", "clean", parent=self.base, shim=environment)
                    ref = self.unique("clean")
                    checks = self.public_checks()
                    self.assert_pushed(self.git_push("origin", clean, ref, environment), self.public_url, clean)
                    # The entry ran once, as git's pre-push; it holds the public check, so the gate ran none itself.
                    lines = entry_log.read_text(encoding="utf-8").splitlines()
                    self.assertEqual(len(lines), 2, lines)
                    self.assertEqual(lines[0], f"pre-push origin {self.public_url}")
                    self.assertTrue(lines[1].endswith(f" {clean} refs/heads/{ref} {'0' * 40}"), lines)
                    self.assertEqual(self.public_checks(), checks)
                    entry_log.unlink()
            self.real("config", "--unset", "core.hooksPath")

    def test_push_refuses_jj_pushes_of_a_squash_made_commit(self) -> None:
        # (6) `jj git push` of a bookmark whose commit `jj squash` made; (7) the same with jj running /usr/bin/git;
        # (8) the same as a person with DOTFILES_JJ_NO_LEAK_CHECK=1, whose refusal names no such bypass.
        self.init_jj()
        variants = (
            ("6", True, {}, False),
            ("6", False, {}, False),
            ("7", True, {}, True),
            ("7", False, {}, True),
            ("8", False, {"DOTFILES_JJ_NO_LEAK_CHECK": "1"}, False),
        )
        for case, agent, extra, executable in variants:
            environment = (self.agent if agent else self.person) | extra
            for url in (self.public_url, self.unknown_url):
                with self.subTest(case=case, agent=agent, url=url):
                    self.set_url(url)
                    path = f"{self.unique('squash')}.txt"
                    commit = self.squash_commit(path, self.term, environment)
                    bookmark = self.unique("refused")
                    result = self.assert_push_refused(
                        lambda: self.jj_push(commit, bookmark, environment, executable), commit, f"{path}:1", url, "jj"
                    )
                    self.assertNotIn("DOTFILES_JJ_NO_LEAK_CHECK", result.stderr)
                    self.set_url(self.private_url)
                    self.assert_pushed(self.jj_push(commit, self.unique("private"), environment, executable), self.private_url, commit)
            with self.subTest(case=case, agent=agent, control="clean"):
                self.set_url(self.public_url)
                clean = self.squash_commit(f"{self.unique('squash-clean')}.txt", "clean", environment)
                self.assert_pushed(self.jj_push(clean, self.unique("clean"), environment, executable), self.public_url, clean)

    def test_push_refuses_a_jj_push_of_a_text_file_the_repository_marks_binary(self) -> None:
        # `jj git push` reads the outgoing commits with git, which shows a text file that `.gitattributes` marks `-diff`
        # as binary; the file is still text, and its term must be read.
        def make(path: str, content: str) -> str:
            """A commit on the base holding PATH with CONTENT and the `.gitattributes`, kept by a branch so jj imports it."""
            self.real("checkout", "-q", "--detach", self.base)
            (self.repo / ".gitattributes").write_text("*.txt -diff\n", encoding="utf-8")
            self.real("add", ".gitattributes")
            commit = self.commit(path, content, "clean")
            self.real("branch", "-f", path.removesuffix(".txt"), commit)
            return commit

        leaky = make("jj-attributes.txt", self.term)
        clean = make("jj-attributes-clean.txt", "clean")
        self.init_jj()
        for agent, environment in self.callers():
            with self.subTest(agent=agent):
                self.assert_push_refused(
                    lambda: self.jj_push(leaky, self.unique("refused"), environment),
                    leaky,
                    "jj-attributes.txt:1",
                    self.public_url,
                    "jj",
                )
            with self.subTest(agent=agent, control="clean"):
                self.assert_pushed(self.jj_push(clean, self.unique("clean"), environment), self.public_url, clean)

    def test_push_refuses_a_persons_terms_with_no_public_check_installed(self) -> None:
        # (9): cases (1) and (6) as a person on a machine with no public leak check.
        planted = write_executable(self.root / "planted-check" / "public-leak-check.sh", "#!/bin/sh\nexit 0\n")
        no_check = self.without_public_check(self.person)
        # The check-free assertion fires for a check the hook would find on PATH or through the pointer file.
        with self.assertRaises(AssertionError):
            self.assert_no_public_check(no_check | {"PATH": f"{planted.parent}{os.pathsep}{no_check['PATH']}"})
        pointer_config = self.root / "pointer-config"
        (pointer_config / "public-leak-check").mkdir(parents=True)
        (pointer_config / "public-leak-check" / "path").write_text(f"{planted}\n", encoding="utf-8")
        with self.assertRaises(AssertionError):
            self.assert_no_public_check(no_check | {"XDG_CONFIG_HOME": str(pointer_config)})
        self.init_jj()
        for url in (self.public_url, self.unknown_url):
            with self.subTest(case="1", url=url):
                self.set_url(url)
                path = f"{self.unique('no-check')}.txt"
                commit = self.commit(path, self.term, "clean", parent=self.base, shim=no_check)
                ref = self.unique("refused")
                self.assert_push_refused(lambda: self.git_push("origin", commit, ref, no_check), commit, f"{path}:1", url)
                self.set_url(self.private_url)
                self.assert_pushed(self.git_push("origin", commit, self.unique("private"), no_check), self.private_url, commit)
            with self.subTest(case="6", url=url):
                self.set_url(url)
                path = f"{self.unique('no-check-squash')}.txt"
                commit = self.squash_commit(path, self.term, no_check)
                bookmark = self.unique("refused")
                self.assert_push_refused(lambda: self.jj_push(commit, bookmark, no_check), commit, f"{path}:1", url, "jj")
                self.set_url(self.private_url)
                self.assert_pushed(self.jj_push(commit, self.unique("private"), no_check), self.private_url, commit)
        with self.subTest(control="clean"):
            self.set_url(self.public_url)
            clean = self.commit(f"{self.unique('no-check-clean')}.txt", "clean", "clean", parent=self.base, shim=no_check)
            self.assert_pushed(self.git_push("origin", clean, self.unique("clean"), no_check), self.public_url, clean)
            squashed = self.squash_commit(f"{self.unique('no-check-clean')}.txt", "clean", no_check)
            self.assert_pushed(self.jj_push(squashed, self.unique("clean"), no_check), self.public_url, squashed)

    def stack(self, label: str, middle_content: str, middle_message: str) -> tuple[str, str, str]:
        """Three commits on the base; only the middle one may hold the term."""
        first = self.commit(f"{label}-first.txt", "clean", "first", parent=self.base)
        middle = self.commit(f"{label}-middle.txt", middle_content, middle_message)
        last = self.commit(f"{label}-last.txt", "clean", "last")
        return first, middle, last

    def test_push_refuses_a_stack_whose_middle_commit_alone_holds_the_term(self) -> None:
        # (10) a git push of three commits whose middle one holds the term in its diff, and a jj push of three whose
        # middle one holds it in its message; the tip holds none.
        self.init_jj()
        jj_variants = [(agent, environment, url, False) for agent, environment, url in self.combinations()] + [
            (agent, environment, url, True)
            for url in (self.public_url, self.unknown_url)
            for agent, environment in ((True, self.agent), (False, self.person | {"DOTFILES_JJ_NO_LEAK_CHECK": "1"}))
        ]
        for agent, environment, url in self.combinations():
            with self.subTest(kind="git", agent=agent, url=url):
                self.set_url(url)
                label = self.unique("git-stack")
                commits = self.stack(label, self.term, "middle")
                ref = self.unique("refused")
                self.assert_push_refused(lambda: self.git_push("origin", commits[2], ref, environment), commits[1], f"{label}-middle.txt:1", url)
                self.set_url(self.private_url)
                self.assert_pushed(self.git_push("origin", commits[2], self.unique("private"), environment), self.private_url, *commits)
        for agent, environment, url, executable in jj_variants:
            with self.subTest(kind="jj", agent=agent, url=url, executable=executable):
                self.set_url(url)
                commits = self.stack(self.unique("jj-stack"), "clean", self.term)
                bookmark = self.unique("refused")
                result = self.assert_push_refused(
                    lambda: self.jj_push(commits[2], bookmark, environment, executable), commits[1], "message line 1", url, "jj"
                )
                self.assertNotIn("DOTFILES_JJ_NO_LEAK_CHECK", result.stderr)
                self.set_url(self.private_url)
                self.assert_pushed(self.jj_push(commits[2], self.unique("private"), environment, executable), self.private_url, *commits)
        for agent, environment in self.callers():
            for kind in ("git", "jj"):
                with self.subTest(kind=kind, agent=agent, control="clean-stack"):
                    url = self.fresh_public_url()
                    self.set_url(url)
                    commits = self.stack(self.unique(f"{kind}-clean-stack"), "clean", "middle")
                    pushed = (
                        self.git_push("origin", commits[2], self.unique("clean"), environment)
                        if kind == "git"
                        else self.jj_push(commits[2], self.unique("clean"), environment)
                    )
                    self.assert_pushed(pushed, url, *commits)

    def test_push_refuses_a_term_only_in_a_changed_path(self) -> None:
        # (11) the only test term is in the path of a changed file, shown as <listed term>.txt.
        self.init_jj()

        def push(kind: str, commit: str, ref: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
            return self.git_push("origin", commit, ref, environment) if kind == "git" else self.jj_push(commit, ref, environment)

        for agent, environment, url in self.combinations():
            for kind in ("git", "jj"):
                with self.subTest(kind=kind, agent=agent, url=url):
                    self.set_url(url)
                    directory = self.unique(f"{kind}-path")
                    commit = self.commit(f"{directory}/zorbleplex.txt", "clean", "clean", parent=self.base)
                    ref = self.unique("refused")
                    self.assert_push_refused(
                        lambda: push(kind, commit, ref, environment), commit, f"{directory}/<listed term>.txt", url, kind
                    )
                    self.set_url(self.private_url)
                    self.assert_pushed(push(kind, commit, self.unique("private"), environment), self.private_url, commit)
        for agent, environment in self.callers():
            for kind in ("git", "jj"):
                with self.subTest(kind=kind, agent=agent, control="clean"):
                    self.set_url(self.public_url)
                    commit = self.commit(f"{self.unique(f'{kind}-path')}/clean-name.txt", "clean", "clean", parent=self.base)
                    self.assert_pushed(push(kind, commit, self.unique("clean"), environment), self.public_url, commit)

    def test_push_reads_a_mode_change_as_metadata(self) -> None:
        # `git log --cc` prints `old mode` and `new mode` for a commit that changes a mode: a commit that only makes a
        # file executable goes out, and the same change with a term line is refused at that line.
        self.init_jj()

        def push(kind: str, commit: str, ref: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
            return self.git_push("origin", commit, ref, environment) if kind == "git" else self.jj_push(commit, ref, environment)

        def mode_commit(content: str) -> tuple[str, str]:
            """A clean base adding a script, then a commit that writes CONTENT to it and makes it executable."""
            name = f"{self.unique('mode')}.sh"
            self.commit(name, "echo clean", "base", parent=self.base)
            (self.repo / name).write_text(content + "\n", encoding="utf-8")
            (self.repo / name).chmod(0o755)
            self.real("add", name)
            self.real("commit", "--no-verify", "-m", "mode")
            return name, self.real("rev-parse", "HEAD")

        for agent, environment, url in self.combinations():
            for kind in ("git", "jj"):
                with self.subTest(kind=kind, agent=agent, url=url):
                    self.set_url(url)
                    name, leaky = mode_commit(f"echo clean\n{self.term}")
                    ref = self.unique("refused")
                    self.assert_push_refused(lambda: push(kind, leaky, ref, environment), leaky, f"{name}:2", url, kind)
                    _, clean = mode_commit("echo clean")
                    self.assert_pushed(push(kind, clean, self.unique("mode-only"), environment), url, clean)

    def merge(self, label: str, resolution: str) -> str:
        """A merge of two clean parents whose conflict resolution writes RESOLUTION."""
        self.real("checkout", "-q", "-B", f"{label}-side", self.base)
        self.commit(f"{label}.txt", "side", "side")
        self.real("checkout", "-q", "-B", f"{label}-main", self.base)
        self.commit(f"{label}.txt", "main", "main")
        merged = self.command(["/usr/bin/git", "merge", "--no-ff", f"{label}-side"], self.person)
        self.assertNotEqual(merged.returncode, 0, "the parents did not conflict")
        (self.repo / f"{label}.txt").write_text(resolution + "\n", encoding="utf-8")
        self.real("add", f"{label}.txt")
        self.real("commit", "--no-verify", "-m", "resolve")
        return self.real("rev-parse", "HEAD")

    def test_push_refuses_a_combined_diff(self) -> None:
        # (12) a merge of two clean parents whose conflict resolution adds a line with the term.
        leaky = self.merge("merge", self.term)
        clean = self.merge("clean-merge", "resolution")
        clean_parents = self.real("rev-parse", f"{clean}^1", f"{clean}^2").split()
        self.init_jj()
        for agent, environment, url in self.combinations():
            with self.subTest(kind="git", agent=agent, url=url):
                self.set_url(url)
                ref = self.unique("refused")
                self.assert_push_refused(lambda: self.git_push("origin", leaky, ref, environment), leaky, "merge.txt:1", url)
            for executable in (False, True):
                with self.subTest(kind="jj", agent=agent, executable=executable, url=url):
                    self.set_url(url)
                    bookmark = self.unique("refused")
                    self.assert_push_refused(
                        lambda: self.jj_push(leaky, bookmark, environment, executable), leaky, "merge.txt:1", url, "jj"
                    )
        for agent, environment in self.callers():
            for kind in ("git", "jj"):
                with self.subTest(kind=kind, agent=agent, control="private"):
                    self.set_url(self.private_url)
                    pushed = (
                        self.git_push("origin", leaky, self.unique("private"), environment)
                        if kind == "git"
                        else self.jj_push(leaky, self.unique("private"), environment)
                    )
                    self.assert_pushed(pushed, self.private_url, leaky)
                with self.subTest(kind=kind, agent=agent, control="clean-merge"):
                    # A fresh sink, so the clean merge and both its parents go out and are read.
                    url = self.fresh_public_url()
                    self.set_url(url)
                    checks = self.public_checks()
                    pushed = (
                        self.git_push("origin", clean, self.unique("clean"), environment)
                        if kind == "git"
                        else self.jj_push(clean, self.unique("clean"), environment)
                    )
                    self.assert_pushed(pushed, url, clean, *clean_parents)
                    if kind == "jj" and not agent:
                        # A person's clean jj push still runs the public check once.
                        self.assertEqual(self.public_checks(), checks + 1)

    def test_push_root_diff_ignores_log_configuration(self) -> None:
        (self.home / ".gitconfig").write_text(
            "[log]\n"
            "\tshowRoot = false\n"
            "\tshowSignature = true\n",
            encoding="utf-8",
        )
        initial_repository = self.repo
        try:
            for label, content in (("root-leak", self.term), ("root-clean", "clean")):
                with self.subTest(kind=label):
                    self.repo = self.root / label
                    self.repo.mkdir()
                    self.real("init", "-q", "-b", "main")
                    self.real("config", "user.name", "Push Test")
                    self.real("config", "user.email", "push-test@example.invalid")
                    self.real("remote", "add", "origin", self.public_url)
                    self.real("config", "remote.origin.vcs", "sink")
                    commit = self.commit("root.txt", content, label)
                    if label == "root-leak":
                        self.assert_push_refused(lambda: self.git_push("origin", commit, label, self.person), commit, "root.txt:1", self.public_url)
                    else:
                        self.assert_pushed(self.git_push("origin", commit, label, self.person), self.public_url, commit)
        finally:
            self.repo = initial_repository

    def test_push_skips_an_empty_private_dry_run_destination(self) -> None:
        self.init_jj()
        self.set_url(self.private_url)
        write_executable(
            self.fake / "jj",
            "#!/bin/bash\n"
            "if [[ \" $* \" == *' --dry-run '* ]]; then\n"
            "    printf 'Changes to push to origin:\\n'\n"
            "fi\n",
        )
        result = self.command(
            [str(ROOT / "shims" / "jj"), "git", "push"],
            self.person | {"DOTFILES_JJ_NO_LEAK_CHECK": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_push_refuses_force_and_direct_url_pushes_and_lets_deletions_go(self) -> None:
        for agent, environment, url in self.combinations():
            with self.subTest(case="force", agent=agent, url=url):
                self.set_url(url)
                path = f"{self.unique('force')}.txt"
                leaky = self.commit(path, self.term, "force", parent=self.base)
                ref = self.unique("force")
                self.assert_push_refused(lambda: self.git_push("origin", leaky, ref, environment, "--force"), leaky, f"{path}:1", url)
            with self.subTest(case="deletion", agent=agent, url=url):
                self.set_url(url)
                ref = self.unique("delete")
                self.real("push", "--no-verify", "origin", f"{self.base}:refs/heads/{ref}")
                self.assertIn(f"refs/heads/{ref}\n", self.sink_state(url)[0])
                deleted = self.command(["git", "push", "origin", f":refs/heads/{ref}"], environment)
                self.assertEqual(deleted.returncode, 0, deleted.stderr)
                self.assertNotIn(f"refs/heads/{ref}\n", self.sink_state(url)[0])
            with self.subTest(case="direct-url", agent=agent, url=url):
                # A URL that is not a configured remote, from a checkout whose one remote is private.
                self.set_url(self.private_url)
                destination = f"sink::{url}"
                path = f"{self.unique('direct')}.txt"
                leaky = self.commit(path, self.term, "direct", parent=self.base)
                ref = self.unique("direct")
                self.assert_push_refused(
                    lambda: self.command(["git", "push", destination, f"{leaky}:refs/heads/{ref}"], environment),
                    leaky,
                    f"{path}:1",
                    destination,
                )
                clean = self.commit(f"{self.unique('direct-clean')}.txt", "clean", "clean", parent=self.base)
                pushed = self.command(["git", "push", destination, f"{clean}:refs/heads/{self.unique('direct')}"], environment)
                self.assert_pushed(pushed, url, clean)

    def test_push_uses_the_destination_advertisement_not_other_refs(self) -> None:
        leaky = self.commit("advertisement.txt", self.term, "leak", parent=self.base)
        other_url = "https://github.com/theorem-labs/other-public.git"
        self.real("remote", "add", "other", other_url)
        self.real("config", "remote.other.vcs", "sink")
        self.real("push", "--no-verify", "other", f"{leaky}:refs/heads/held")
        self.real("update-ref", "refs/remotes/origin/stale", leaky)
        self.assert_push_refused(
            lambda: self.git_push("origin", leaky, "destination", self.agent), leaky, "advertisement.txt:1", self.public_url
        )
        # Once the destination advertises the commit, neither git nor jj refuses a push of it for its term.
        self.real("push", "--no-verify", "origin", f"{leaky}:refs/heads/already")
        direct_url = f"sink::{self.unknown_url}"
        self.real("push", "--no-verify", direct_url, f"{leaky}:refs/heads/direct-held")
        self.init_jj()
        for agent, environment in self.callers():
            with self.subTest(kind="git", agent=agent):
                self.assert_pushed(self.git_push("origin", leaky, self.unique("again"), environment), self.public_url, leaky)
                direct = self.command(["git", "push", direct_url, f"{leaky}:refs/heads/{self.unique('direct-again')}"], environment)
                self.assert_pushed(direct, self.unknown_url, leaky)
            with self.subTest(kind="jj", agent=agent):
                bookmark = self.unique("jj-again")
                self.assert_pushed(self.jj_push(leaky, bookmark, environment), self.public_url, leaky)
                self.assertIn(f"{leaky} refs/heads/{bookmark}\n", self.sink_state(self.public_url)[0])

    def test_push_refuses_a_failed_advertisement_read(self) -> None:
        # A transport whose upload-pack, the advertisement read, fails, while its receive-pack would take the push.
        write_executable(
            self.fake / "git-remote-fail",
            "#!/bin/bash\n"
            "key=$(printf '%s' \"$2\" | sha1sum | cut -c1-16)\n"
            f"sink={shlex.quote(str(self.sinks))}/$key.git\n"
            "[[ -d \"$sink\" ]] || /usr/bin/git init -q --bare \"$sink\"\n"
            "while IFS= read -r line; do\n"
            "    case \"$line\" in\n"
            "        capabilities) printf 'connect\\n\\n' ;;\n"
            f"        'connect git-receive-pack') printf '\\n'; exec {SERVER_SIDE} receive-pack \"$sink\" ;;\n"
            "        'connect git-upload-pack') exit 1 ;;\n"
            "        '') exit 0 ;;\n"
            "        *) exit 1 ;;\n"
            "    esac\n"
            "done\n",
        )
        self.real("config", "remote.origin.vcs", "fail")
        leaky = self.commit("failed.txt", self.term, "failed")
        before = self.sink_state(self.public_url)
        result = self.git_push("origin", leaky, "failed", self.person | {"GIT_ALLOW_PROTOCOL": "sink:fail"})
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("cannot read text to check: git could not read the destination's advertised refs", result.stderr)
        # No term was found, so the refusal must not say the push holds one.
        self.assertNotIn("holds a listed leak term", result.stderr)
        self.assertEqual(self.sink_state(self.public_url), before)
        self.assertFalse(self.received(leaky, self.public_url))

    def test_push_gate_refusal_names_a_plain_push_or_a_jj_push(self) -> None:
        # shims/git tells the gate's hook whether jj ran the push. Here the hook cannot read git's update lines (its
        # standard input is open only for writing), so it refuses, naming what it refused, before any check runs.
        hook = ROOT / "shims" / "jj-push-hooks" / "pre-push"
        for extra, what in (({}, "push"), ({"GIT_SHIM_JJ_PUSH": "1"}, "jj push")):
            with self.subTest(what=what):
                read_end, write_end = os.pipe()
                try:
                    result = self.launch(
                        [str(hook), "origin", self.public_url],
                        cwd=self.repo,
                        env=self.person | {"GIT_SHIM_PUBLIC_CHECK": "0"} | extra,
                        stdin=write_end,
                    )
                finally:
                    os.close(read_end)
                    os.close(write_end)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(
                    f"error: git shim: cannot read the updates git gave the pre-push hook, so this {what} to {self.public_url} is refused.",
                    result.stderr,
                )

    def test_push_sink_reader_fails_on_an_unreadable_sink(self) -> None:
        # B9's instrument, made to fail: a sink not made yet reads as empty, one that holds a push lists its ref and
        # objects, and one that exists but is no repository fails the test instead of reading as empty.
        self.assertEqual(self.sink_state(self.public_url), ("", ""))
        self.assertFalse(self.received(self.base, self.public_url))
        self.real("push", "--no-verify", "origin", f"{self.base}:refs/heads/held")
        refs, objects = self.sink_state(self.public_url)
        self.assertEqual(refs, f"{self.base} refs/heads/held\n")
        self.assertIn(f"{self.base} commit ", objects)
        self.assertTrue(self.received(self.base, self.public_url))
        broken = self.sink(self.unknown_url)
        broken.mkdir()
        (broken / "HEAD").write_text("not a reference\n", encoding="utf-8")
        with self.assertRaisesRegex(AssertionError, "cannot read the sink"):
            self.sink_state(self.unknown_url)
        with self.assertRaisesRegex(AssertionError, "cannot read the sink"):
            self.received(self.base, self.unknown_url)

    def test_unreadable_push_lists_refuse_git_and_jj(self) -> None:
        self.init_jj()
        cases = unreadable_lists(self.root, self.person["PATH"])
        for agent, environment in self.callers():
            with self.subTest(agent=agent, case="healthy-list-control"):
                commit = self.commit(f"{self.unique('healthy')}.txt", "clean", "clean", parent=self.base)
                self.assert_pushed(self.git_push("origin", commit, self.unique("git-healthy"), environment), self.public_url, commit)
                commit = self.commit(f"{self.unique('healthy')}.txt", "clean", "clean", parent=self.base)
                self.assert_pushed(self.jj_push(commit, self.unique("jj-healthy"), environment), self.public_url, commit)
            for label, extra, correction in cases:
                with self.subTest(agent=agent, case=label):
                    commit = self.commit(f"{self.unique(label)}.txt", "clean", label, parent=self.base)
                    for kind in ("git", "jj"):
                        before = self.sink_state(self.public_url)
                        ref = self.unique(f"{kind}-{label}")
                        result = (
                            self.git_push("origin", commit, ref, environment | extra)
                            if kind == "git"
                            else self.jj_push(commit, ref, environment | extra)
                        )
                        self.assertNotEqual(result.returncode, 0, result.stderr)
                        self.assert_one_correction_line(result, correction)
                        # The list was not read, so the refusal must not say the push holds a listed term.
                        self.assertNotIn("holds a listed leak term", result.stderr)
                        self.assertEqual(self.sink_state(self.public_url), before, "a refused push changed the sink")


class SecrecyTrace(SinkRepositoryCase):
    """Trace refusals so that a listed term never reaches a persistent surface."""

    identity = "Hidden Test"

    def command(self, command: list[str], environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return self.launch(command, cwd=self.repo, env=environment or self.person)

    def real_git(self, *arguments: str) -> str:
        result = self.command(["/usr/bin/git", *arguments])
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def traced(
        self, command: list[str], *, string_limit: int = 1_048_576, environment: dict[str, str] | None = None
    ) -> tuple[subprocess.CompletedProcess[bytes], bytes]:
        read_fd, write_fd = os.pipe()
        captured: list[bytes] = []

        def read_trace() -> None:
            with os.fdopen(read_fd, "rb") as stream:
                captured.append(stream.read())

        reader = threading.Thread(target=read_trace, daemon=True)
        reader.start()
        try:
            result = self.launch(
                [
                    "strace", "-f", "-yy", "-xx", "-s", str(string_limit),
                    "-e", "trace=execve,write,pwrite64,writev", "-o", f"/dev/fd/{write_fd}", "--", *command,
                ],
                cwd=self.repo,
                env=environment or self.person,
                text=False,
                pass_fds=(write_fd,),
            )
        finally:
            os.close(write_fd)
        reader.join(timeout=10)
        self.assertFalse(reader.is_alive(), "strace capture pipe did not close")
        self.assertEqual(len(captured), 1, "strace capture was not read")
        return result, captured[0]

    def assert_refusal(
        self, result: subprocess.CompletedProcess[bytes], capture: bytes, allowed_files: set[str] | None = None
    ) -> dict[str, bytes]:
        """The traced refusal shows and writes no test term, by the scanner's matching rule: not in its output, an
        argv, or any regular file but ALLOWED_FILES, which may hold the person's own message but not the canary."""
        allowed_files = allowed_files or set()
        canary = TEST_TERMS[-1]
        self.assertNotEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        self.assertIn(b"listed leak term", result.stderr.lower())
        self.assert_hidden(result.stdout, result.stderr)
        argv, writes = parse_strace(capture)
        for values in argv:
            self.assertEqual(leaked_terms(b"\0".join(values)), [], values)
        for path, data in writes.items():
            if path in allowed_files:
                self.assertNotIn(canary, leaked_terms(data), path)
            else:
                self.assertEqual(leaked_terms(data), [], path)
        return writes

    def canary_hits(self) -> list[str]:
        """Each place the canary is after a command: a regular file under the test HOME, TMPDIR or repository, or an
        object of the repository's git or jj backing store. An object store that lists no object fails (UNKNOWN)."""
        canary = TEST_TERMS[-1]
        hits: list[str] = []
        for root in (self.home, self.tmpdir, self.repo):
            for directory, names, files in os.walk(root):
                names[:] = [name for name in names if not (Path(directory) / name).is_symlink()]
                for name in files:
                    path = Path(directory) / name
                    if stat.S_ISREG(path.lstat().st_mode) and canary in leaked_terms(path.read_bytes()):
                        hits.append(f"file {path}")
        git_directories = {Path(self.real_git("rev-parse", "--absolute-git-dir")).resolve()}
        target_file = self.repo / ".jj" / "repo" / "store" / "git_target"
        if target_file.is_file():
            target = Path(target_file.read_text(encoding="utf-8").strip())
            git_directories.add((target if target.is_absolute() else target_file.parent / target).resolve())
        for git_directory in sorted(git_directories):
            objects = self.launch(
                ["/usr/bin/git", f"--git-dir={git_directory}", "cat-file", "--batch-all-objects", "--batch"],
                cwd=self.repo,
                env=self.person,
                text=False,
            )
            self.assertEqual(objects.returncode, 0, objects.stderr.decode("utf-8", errors="replace"))
            self.assertTrue(objects.stdout, f"{git_directory} listed no object (UNKNOWN)")
            if canary in leaked_terms(objects.stdout):
                hits.append(f"object {git_directory}")
        return hits

    def assert_no_canary(self) -> None:
        self.assertEqual(self.canary_hits(), [])

    def initialize_jj(self) -> None:
        result = self.command(["jj", "git", "init", "--colocate"])
        self.assertEqual(result.returncode, 0, result.stderr)

    def leaky_commit(self, name: str) -> str:
        (self.repo / name).write_text(TEST_TERMS[0] + "\n", encoding="utf-8")
        self.real_git("add", name)
        self.real_git("commit", "--no-verify", "-m", "clean")
        return self.real_git("rev-parse", "HEAD")

    def control_writer(self) -> tuple[Path, Path, Path]:
        canary_file = self.tmpdir / "hidden-canary"
        term_file = self.tmpdir / "hidden-term"
        script = self.root / "hidden-control.sh"
        script.write_text(
            "#!/bin/sh\n"
            "canary=$CANARY\n"
            "length=${#canary}\n"
            "half=$((length / 2))\n"
            "first=$(printf '%s' \"$canary\" | cut -c \"1-$half\")\n"
            "second=$(printf '%s' \"$canary\" | cut -c \"$((half + 1))-\")\n"
            f"printf '%s' \"$first\" > {shlex.quote(str(canary_file))}\n"
            f"printf '%s' \"$second\" >> {shlex.quote(str(canary_file))}\n"
            f"printf '%s' \"$TEST_TERM\" > {shlex.quote(str(term_file))}\n"
            f"rm -- {shlex.quote(str(canary_file))} {shlex.quote(str(term_file))}\n"
            "exec /usr/bin/true \"$canary\"\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        return script, canary_file, term_file

    def test_hidden_git_commit(self) -> None:
        (self.repo / "git-content.txt").write_text(TEST_TERMS[0] + "\n", encoding="utf-8")
        self.real_git("add", "git-content.txt")
        result, capture = self.traced(["git", "commit", "-m", "ok"])
        self.assert_refusal(result, capture)
        self.assert_no_canary()

    def test_hidden_jj_commit(self) -> None:
        self.initialize_jj()
        (self.repo / "jj-content.txt").write_text(TEST_TERMS[0] + "\n", encoding="utf-8")
        result, capture = self.traced(["jj", "commit", "-m", "ok"])
        self.assert_refusal(result, capture)
        self.assert_no_canary()

    def test_hidden_jj_describe_editor(self) -> None:
        self.initialize_jj()
        editor = self.root / "hidden-editor.sh"
        description = self.root / "hidden-description"
        editor.write_text(
            "#!/bin/sh\n"
            "for argument in \"$@\"; do\n"
            "    [ -f \"$argument\" ] && file=$argument\n"
            "done\n"
            f"printf '%s' \"$file\" > {shlex.quote(str(description))}\n"
            f"printf '%s' {shlex.quote(TEST_TERMS[0])} > \"$file\"\n",
            encoding="utf-8",
        )
        editor.chmod(0o755)
        result, capture = self.traced(["jj", "describe"], environment=self.person | {"JJ_EDITOR": str(editor)})
        description_file = description.read_text(encoding="utf-8")
        writes = self.assert_refusal(result, capture, {description_file})
        self.assertIn(description_file, writes)
        self.assertIn(TEST_TERMS[0].encode(), writes[description_file])
        self.assert_no_canary()

    def test_hidden_git_push(self) -> None:
        commit = self.leaky_commit("git-push-content.txt")
        result, capture = self.traced(["git", "push", "origin", f"{commit}:refs/heads/hidden-git"])
        self.assert_refusal(result, capture)
        self.assert_no_canary()

    def test_hidden_jj_push(self) -> None:
        commit = self.leaky_commit("jj-push-content.txt")
        self.initialize_jj()
        imported = self.command(["jj", "git", "import"])
        self.assertEqual(imported.returncode, 0, imported.stderr)
        marked = self.command(["jj", "bookmark", "set", "--allow-backwards", "hidden-jj", "-r", commit])
        self.assertEqual(marked.returncode, 0, marked.stderr)
        result, capture = self.traced(
            ["jj", "git", "push", "--bookmark", "hidden-jj"],
            environment=self.person | {"DOTFILES_JJ_NO_LEAK_CHECK": "1"},
        )
        self.assert_refusal(result, capture)
        self.assert_no_canary()

    def test_hidden_canary_search_finds_a_seeded_object_and_file(self) -> None:
        # B6: the post-command search must find the canary inside the backing store's compressed objects, which no
        # file holds as plain bytes, and in a plain file.
        self.initialize_jj()
        self.assert_no_canary()
        seeded = self.launch(
            ["/usr/bin/git", "hash-object", "-w", "--stdin"], cwd=self.repo, env=self.person, input=f"{TEST_TERMS[-1]}\n"
        )
        self.assertEqual(seeded.returncode, 0, seeded.stderr)
        git_directory = Path(self.real_git("rev-parse", "--absolute-git-dir")).resolve()
        self.assertEqual(self.canary_hits(), [f"object {git_directory}"])
        planted = self.tmpdir / "planted"
        planted.write_text(f"x {TEST_TERMS[-1].upper()}\n", encoding="utf-8")
        self.assertEqual(self.canary_hits(), [f"file {planted}", f"object {git_directory}"])

    def test_hidden_refusal_audit_catches_an_echo_in_another_case_and_spacing(self) -> None:
        # B7: the audit matches as the scanner does, so a refusal that echoes `quillfeather   LANE 9` to its output,
        # a file or an argv fails it; the same refusal without the echo passes.
        target = self.tmpdir / "echo-target"
        script = write_executable(
            self.root / "echoing-refusal.sh",
            "#!/bin/sh\n"
            "printf 'leak terms: listed leak term\\n' >&2\n"
            "case \"$MODE\" in\n"
            "    output) printf 'echo: %s\\n' \"$ECHOED\" >&2 ;;\n"
            f"    file) printf '%s\\n' \"$ECHOED\" > {shlex.quote(str(target))}; rm -f -- {shlex.quote(str(target))} ;;\n"
            "    argv) /usr/bin/true \"$ECHOED\" ;;\n"
            "esac\n"
            "exit 1\n",
        )
        for mode in ("output", "file", "argv"):
            with self.subTest(mode=mode):
                result, capture = self.traced(
                    [str(script)], environment=self.person | {"MODE": mode, "ECHOED": "quillfeather   LANE 9"}
                )
                with self.assertRaises(AssertionError):
                    self.assert_refusal(result, capture)
        result, capture = self.traced([str(script)], environment=self.person | {"MODE": "none", "ECHOED": ""})
        self.assert_refusal(result, capture)

    def test_hidden_negative_control_finds_file_writes_and_argv(self) -> None:
        script, canary_file, term_file = self.control_writer()
        canary = TEST_TERMS[-1].encode()
        term = TEST_TERMS[0].encode()
        result, capture = self.traced(
            [str(script)], environment=self.person | {"CANARY": canary.decode(), "TEST_TERM": term.decode()}
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        argv, writes = parse_strace(capture)
        self.assertTrue(any(canary in values for values in argv))
        self.assertEqual(writes[str(canary_file)], canary)
        self.assertIn(term, writes[str(term_file)])

    def test_hidden_negative_control_rejects_cut_capture(self) -> None:
        script, _, _ = self.control_writer()
        result, capture = self.traced(
            [str(script)],
            string_limit=8,
            environment=self.person | {"CANARY": TEST_TERMS[-1], "TEST_TERM": TEST_TERMS[0]},
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        with self.assertRaisesRegex(AssertionError, "cut string"):
            parse_strace(capture)

    def test_hidden_trace_parser_rejects_unknown_captures(self) -> None:
        with self.assertRaisesRegex(AssertionError, "empty.*UNKNOWN"):
            parse_strace(b"")
        with self.assertRaisesRegex(AssertionError, "no execve.*UNKNOWN"):
            parse_strace(b'write(1<pipe:[1]>, "\\x78", 1) = 1\n')
        payload = "".join(f"\\x{byte:02x}" for byte in TEST_TERMS[0].encode()).encode()
        capture = (
            b'1 execve("/usr/bin/true", ["/usr/bin/true"], 0x0) = 0\n'
            b'[pid 2] write(3</tmp/hidden-output>, "' + payload + b'" <unfinished ...>\n'
            b'[pid 2] <... write resumed> , ' + str(len(TEST_TERMS[0])).encode() + b') = ' + str(len(TEST_TERMS[0])).encode() + b'\n'
            b'1 +++ exited with 0 +++\n'
        )
        _, writes = parse_strace(capture)
        self.assertEqual(writes["/tmp/hidden-output"], TEST_TERMS[0].encode())

    def test_hidden_trace_parser_audits_unfinished_syscalls_of_exited_processes(self) -> None:
        payload = "".join(f"\\x{byte:02x}" for byte in TEST_TERMS[0].encode()).encode()
        killed = (
            b'100 execve("/usr/bin/true", ["/usr/bin/true"], 0x0 /* 0 vars */) = 0\n'
            b'101 execve("/usr/bin/cat", ["cat", "' + payload + b'"], 0x0 /* 0 vars */ <unfinished ...>\n'
            b'102 write(3</tmp/killed-output>, "' + payload + b'", 19 <unfinished ...>\n'
            b'103 write(3</tmp/unknown-output>, "' + payload + b'", 19) = ?\n'
            b'101 +++ killed by SIGKILL +++\n'
            b'102 +++ exited with 1 +++\n'
            b'103 +++ killed by SIGPIPE +++\n'
            b'100 +++ exited with 0 +++\n'
        )
        argv, writes = parse_strace(killed)
        self.assertIn((b"cat", TEST_TERMS[0].encode()), argv)
        self.assertEqual(writes["/tmp/killed-output"], TEST_TERMS[0].encode())
        self.assertEqual(writes["/tmp/unknown-output"], TEST_TERMS[0].encode())
        cut = killed.removesuffix(b'100 +++ exited with 0 +++\n')
        with self.assertRaisesRegex(AssertionError, "no exit record.*cut"):
            parse_strace(cut)


if __name__ == "__main__":
    unittest.main()
