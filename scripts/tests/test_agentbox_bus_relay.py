#!/usr/bin/env python3
"""scripts/agentbox-bus-relay: a box's calls that name pids to the host's systemd are refused.

A box pid is a number in the box's pid namespace, and the host's systemd reads it as a host pid:
Chromium in a box asked it to move "its own" pid into a scope, and the host moved one of its own
processes instead (the relay's docstring has the cases). The relay renames exactly those calls so
systemd answers UnknownMethod, and passes every other byte through unchanged.

Technique: run the real script between a test client and a fake bus (a Unix socket that records
what reaches it), send hand-marshalled D-Bus messages, and compare the recorded bytes with the
bytes sent, with the one rename the relay must make.
"""

from __future__ import annotations

import os
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

RELAY = Path(__file__).resolve().parents[1] / "agentbox-bus-relay"
SYSTEMD = "org.freedesktop.systemd1"
MANAGER = "org.freedesktop.systemd1.Manager"
AUTH = b"\0AUTH EXTERNAL 31303030\r\nNEGOTIATE_UNIX_FD\r\nBEGIN\r\n"


def message(member: str, *, destination: str = SYSTEMD, interface: str | None = MANAGER,
            unit: str | None = None, endian: str = "<", serial: int = 7) -> bytes:
    """A METHOD_CALL; with `unit`, its body starts with that string, as StartTransientUnit's does."""
    fields = bytearray()

    def pad(n: int) -> None:
        while (16 + len(fields)) % n:
            fields.append(0)

    def field(code: int, sig: str, value: str) -> None:
        pad(8)
        fields.extend(bytes([code, 1]) + sig.encode() + b"\0")
        raw = value.encode()
        if sig == "g":
            fields.extend(bytes([len(raw)]) + raw + b"\0")
        else:
            pad(4)
            fields.extend(struct.pack(endian + "I", len(raw)) + raw + b"\0")

    field(1, "o", "/org/freedesktop/systemd1")
    if interface is not None:
        field(2, "s", interface)
    field(3, "s", member)
    field(6, "s", destination)
    body = b""
    if unit is not None:
        field(8, "g", "ss")
        raw = unit.encode()
        body = struct.pack(endian + "I", len(raw)) + raw + b"\0"
        body += b"\0" * (-len(body) % 4) + struct.pack(endian + "I", 4) + b"fail\0"
    head = (b"l" if endian == "<" else b"B") + bytes([1, 0, 1])
    head += struct.pack(endian + "III", len(body), serial, len(fields))
    msg = head + bytes(fields)
    return msg + b"\0" * (-len(msg) % 8) + body


def renamed(msg: bytes, old: str, new: str) -> bytes:
    assert len(old) == len(new) and msg.count(old.encode()) == 1
    return msg.replace(old.encode(), new.encode())


class Relay(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.listen, self.bus = str(root / "relay"), str(root / "bus")
        self.received = bytearray()
        self.bus_sock = socket.socket(socket.AF_UNIX)
        self.bus_sock.bind(self.bus)
        self.bus_sock.listen()
        self.got_eof = threading.Event()
        threading.Thread(target=self.fake_bus, daemon=True).start()
        self.proc = subprocess.Popen([str(RELAY), self.listen, self.bus], stderr=subprocess.PIPE, text=True)
        for _ in range(100):
            if os.path.exists(self.listen):
                break
            time.sleep(0.05)
        self.assertTrue(os.path.exists(self.listen), "the relay did not open its socket")

    def tearDown(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=10)
        assert self.proc.stderr is not None
        self.proc.stderr.close()
        self.bus_sock.close()
        self.tmp.cleanup()

    def fake_bus(self) -> None:
        conn, _ = self.bus_sock.accept()
        with conn:
            while data := conn.recv(65536):
                self.received += data
        self.got_eof.set()

    def through(self, *chunks: bytes) -> bytes:
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(self.listen)
            for chunk in chunks:
                client.sendall(chunk)
        self.assertTrue(self.got_eof.wait(10), "the bus never saw the client go")
        return bytes(self.received)

    def test_a_scope_start_reaches_systemd_renamed_and_nothing_else_changes(self) -> None:
        msg = message("StartTransientUnit", unit="app-org.chromium.Chromium-723175.scope")
        self.assertEqual(self.through(AUTH + msg), AUTH + renamed(msg, "StartTransientUnit", "BoxPidsNotHostPids"))

    def test_attaching_pids_is_renamed(self) -> None:
        msg = message("AttachProcessesToUnit")
        self.assertEqual(self.through(AUTH + msg), AUTH + renamed(msg, "AttachProcessesToUnit", "BoxPidsAreNotHostPids"))

    def test_calls_that_name_no_pid_pass_unchanged(self) -> None:
        service = message("StartTransientUnit", unit="run-u5.service")
        other_bus_name = message("StartTransientUnit", destination="org.example.Other", unit="x.scope")
        other_method = message("StopUnit", unit="x.scope")
        sent = AUTH + service + other_bus_name + other_method
        self.assertEqual(self.through(sent), sent)

    def test_a_call_without_an_interface_field_is_still_refused(self) -> None:
        msg = message("StartTransientUnit", interface=None, unit="x.scope")
        self.assertEqual(self.through(AUTH + msg), AUTH + renamed(msg, "StartTransientUnit", "BoxPidsNotHostPids"))

    def test_big_endian_messages_are_read_too(self) -> None:
        msg = message("StartTransientUnit", unit="x.scope", endian=">")
        self.assertEqual(self.through(AUTH + msg), AUTH + renamed(msg, "StartTransientUnit", "BoxPidsNotHostPids"))

    def test_a_stream_split_into_single_bytes_gives_the_same_result(self) -> None:
        keep = message("StartTransientUnit", unit="run-u5.service", serial=1)
        refuse = message("StartTransientUnit", unit="y.scope", serial=2)
        sent = AUTH + keep + refuse
        got = self.through(*[sent[i:i + 1] for i in range(len(sent))])
        self.assertEqual(got, AUTH + keep + renamed(refuse, "StartTransientUnit", "BoxPidsNotHostPids"))

    def test_a_stream_it_cannot_parse_is_relayed_raw_not_dropped(self) -> None:
        garbage = b"Xnot a dbus message, but the bytes must still arrive" * 3
        tail = message("StartTransientUnit", unit="z.scope")
        sent = AUTH + garbage + tail
        self.assertEqual(self.through(sent), sent)


if __name__ == "__main__":
    unittest.main()
