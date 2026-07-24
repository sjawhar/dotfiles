from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from types import TracebackType
from typing import final, override


SHIM = Path(__file__).parents[1] / "secrets"


@final
class FakeBroker:
    _socket_path: Path
    _response: bytes
    request: bytes
    _ready: threading.Event
    _thread: threading.Thread

    def __init__(self, socket_path: Path, response: bytes) -> None:
        self._socket_path = socket_path
        self._response = response
        self.request = b""
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve)

    def __enter__(self) -> FakeBroker:
        self._thread.start()
        if not self._ready.wait(timeout=2):
            raise AssertionError("fake broker did not bind its socket")
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self._thread.join(timeout=2)
        self._socket_path.unlink(missing_ok=True)
        if self._thread.is_alive():
            raise AssertionError("fake broker did not finish")

    def _serve(self) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(self._socket_path))
            listener.listen(1)
            listener.settimeout(2)
            self._ready.set()
            try:
                connection = listener.accept()[0]
            except TimeoutError:
                return
            with connection:
                while not self.request.endswith(b"\n"):
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    self.request += chunk
                connection.sendall(self._response)


@final
class SecretsShimTests(unittest.TestCase):
    _temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    root = Path()
    fixture = Path()
    human_dir = Path()
    runtime = Path()
    bin_dir = Path()
    sops_log = Path()
    token_file = Path()

    @override
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self._temporary_directory = temporary_directory
        self.root = Path(temporary_directory.name)
        self.fixture = self.root / "fixture"
        self.human_dir = self.fixture / "secrets.human.d"
        self.runtime = self.root / "runtime"
        self.bin_dir = self.root / "bin"
        self.sops_log = self.root / "sops.log"
        self.token_file = self.runtime / "session.token"
        self.fixture.mkdir()
        self.runtime.mkdir()
        self.bin_dir.mkdir()
        _ = (self.fixture / "secrets.env").write_text("agent ciphertext\n")
        _ = self.token_file.write_text("token-from-file\n")
        fake_sops = self.bin_dir / "sops"
        _ = fake_sops.write_text(
            "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >>\"$SOPS_LOG\"\n"
            + "printf '%s\\n' 'AGENT_ONLY=agent-value' 'DUP=agent-copy'\n"
        )
        fake_sops.chmod(0o755)

    @override
    def tearDown(self) -> None:
        temporary_directory = self._temporary_directory
        assert temporary_directory is not None
        temporary_directory.cleanup()
        self._temporary_directory = None

    def _environment(self, token_path: Path | None = None) -> dict[str, str]:
        environment = os.environ | {
            "DOTFILES_DIR": str(self.fixture),
            "PATH": f"{self.bin_dir}:{os.environ['PATH']}",
            "SOPS_LOG": str(self.sops_log),
            "XDG_RUNTIME_DIR": str(self.runtime),
        }
        _ = environment.pop("SECRETSD_SOCK", None)
        if token_path is not None:
            environment["SECRETSD_SESSION_TOKEN_FILE"] = str(token_path)
        else:
            _ = environment.pop("SECRETSD_SESSION_TOKEN_FILE", None)
        return environment

    def _run(
        self,
        *args: str,
        token_path: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SHIM), *args],
            capture_output=True,
            env=self._environment(token_path) if environment is None else environment,
            text=True,
            timeout=5,
        )

    def _call_helper(
        self, helper: str, *args: str, environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        command = "source \"$1\"; shift; \"$@\""
        return subprocess.run(
            ["bash", "-c", command, "bash", str(SHIM), helper, *args],
            capture_output=True,
            env=self._environment() if environment is None else environment,
            text=True,
            timeout=5,
        )

    def test_helpers_when_sourced_then_validate_and_discover_filename_keys(self) -> None:
        self.human_dir.mkdir()
        (self.human_dir / "HUMAN.env").touch()

        valid = self._call_helper("_secrets_valid_key", "HUMAN")
        invalid = self._call_helper("_secrets_valid_key", "not-valid")
        human_keys = self._call_helper("_secrets_human_keys")

        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertEqual(human_keys.stdout, "HUMAN\n")

    def test_agent_key_when_requested_then_decrypts_directly_without_broker(self) -> None:
        result = self._run("get", "AGENT_ONLY")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "agent-value\n")
        self.assertIn("-d --output-type dotenv", self.sops_log.read_text())

    def test_agent_key_when_runtime_directory_unset_then_decrypts_without_broker(self) -> None:
        environment = self._environment()
        del environment["XDG_RUNTIME_DIR"]

        result = self._run("get", "AGENT_ONLY", environment=environment)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "agent-value\n")

    def test_list_when_runtime_directory_unset_then_marks_human_tier_keys(self) -> None:
        self.human_dir.mkdir()
        (self.human_dir / "HUMAN.env").touch()
        environment = self._environment()
        del environment["XDG_RUNTIME_DIR"]

        result = self._run("list", environment=environment)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "AGENT_ONLY\nDUP\nHUMAN  (human tier)\n")

    def test_socket_when_runtime_directory_unset_then_prefers_override_then_fallback(self) -> None:
        override = self.root / "override.sock"
        cases: tuple[tuple[dict[str, str], str], ...] = (
            ({"SECRETSD_SOCK": str(override)}, str(override)),
            ({"XDG_RUNTIME_DIR": str(self.runtime)}, f"{self.runtime}/secretsd.sock"),
            ({}, f"/run/user/{os.getuid()}/secretsd.sock"),
        )

        for additions, expected in cases:
            with self.subTest(additions=additions):
                environment = self._environment()
                _ = environment.pop("XDG_RUNTIME_DIR")
                environment.update(additions)

                result = self._call_helper("_secrets_sock", environment=environment)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"{expected}\n")

    def test_human_key_when_requested_then_uses_token_file_and_broker(self) -> None:
        self.human_dir.mkdir()
        (self.human_dir / "HUMAN.env").touch()
        response = b"OK\tlen=5\nhuman"

        with FakeBroker(self.runtime / "secretsd.sock", response) as broker:
            result = self._run("get", "HUMAN", token_path=self.token_file)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "human\n")
        self.assertEqual(broker.request, b"GET\tkey=HUMAN\ttoken=token-from-file\n")

    def test_human_key_when_runtime_directory_unset_then_reports_broker_failure(self) -> None:
        self.human_dir.mkdir()
        (self.human_dir / "HUMAN.env").touch()
        environment = self._environment(token_path=self.token_file)
        del environment["XDG_RUNTIME_DIR"]
        environment["SECRETSD_SOCK"] = str(self.root / "missing-broker.sock")

        result = self._run("get", "HUMAN", environment=environment)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("broker unavailable or request timed out", result.stderr)
        self.assertNotIn("XDG_RUNTIME_DIR is required", result.stderr)

    def test_duplicate_key_when_get_or_list_then_fails_before_broker(self) -> None:
        self.human_dir.mkdir()
        (self.human_dir / "DUP.env").touch()

        get_result = self._run("get", "DUP", token_path=self.token_file)
        list_result = self._run("list")

        self.assertNotEqual(get_result.returncode, 0)
        self.assertIn("exists in both agent and human tiers", get_result.stderr)
        self.assertNotEqual(list_result.returncode, 0)
        self.assertIn("exists in both agent and human tiers", list_result.stderr)

    def test_framed_human_payload_when_not_exact_or_nul_then_fails_closed(self) -> None:
        self.human_dir.mkdir()
        (self.human_dir / "HUMAN.env").touch()
        responses = (
            b"OK\tlen=4\nabc",
            b"OK\tlen=3\nabcEXTRA",
            b"OK\tlen=3\nA\0B",
        )

        for response in responses:
            with self.subTest(response=response):
                with FakeBroker(self.runtime / "secretsd.sock", response):
                    result = self._run("get", "HUMAN", token_path=self.token_file)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("payload length mismatch", result.stderr)

    def test_control_operations_when_called_then_send_scope_free_frames(self) -> None:
        requests = (("grants", (), b"GRANTS\n", b"OK\tlen=0\n"), ("deny", ("7",), b"DENY\tid=7\n", b"OK\n"), ("lock", (), b"LOCK\n", b"OK\n"))

        for operation, arguments, expected, response in requests:
            with self.subTest(operation=operation):
                with FakeBroker(self.runtime / "secretsd.sock", response) as broker:
                    result = self._run(operation, *arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(broker.request, expected)


if __name__ == "__main__":
    _ = unittest.main()
