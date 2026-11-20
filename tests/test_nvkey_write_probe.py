"""Tests for RACE NVKEY read/write framing."""

from __future__ import annotations

import socket
import struct

from tools.nvkey_write_probe import nvkey_read, nvkey_write
from xm4_optimizer.transport.protocol_race import RacePacket, RACE_HEADER_SIZE


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[RacePacket] = []
        self.value = b"\x01\x00\x00"

    def settimeout(self, _t: float) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        pkt, _ = RacePacket.from_bytes(data)
        self.sent.append(pkt)
        payload = struct.pack("<H", len(self.value)) + self.value
        resp = RacePacket(type=0x5B, cmd_id=pkt.cmd_id, payload=payload)
        self._reply = resp.to_bytes()

    def recv(self, n: int) -> bytes:
        if not getattr(self, "_reply", b""):
            raise socket.timeout()
        chunk, self._reply = self._reply[:n], self._reply[n:]
        return chunk


class TestNvkeyFraming:
    def test_read_uses_0a00_with_le_key_and_length(self) -> None:
        sock = FakeSocket()
        assert nvkey_read(sock, 0xFFFF, 16) == b"\x01\x00\x00"
        assert sock.sent[0].cmd_id == 0x0A00
        assert sock.sent[0].payload == struct.pack("<HH", 0xFFFF, 16)

    def test_write_uses_0a01_with_le_key(self) -> None:
        sock = FakeSocket()
        nvkey_write(sock, 0xFFFF, b"\x01")
        assert sock.sent[0].cmd_id == 0x0A01
        assert sock.sent[0].payload == struct.pack("<H", 0xFFFF) + b"\x01"
