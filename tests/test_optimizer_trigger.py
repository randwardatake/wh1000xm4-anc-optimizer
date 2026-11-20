"""Tests for the NC Optimizer trigger experiment tool."""

from __future__ import annotations

import pytest

from tools.optimizer_trigger import (
    build_start_frame,
    decode_optimizer_status,
    extract_coeff_words,
    format_coeff,
    monitor_optimizer,
    OPTIMIZER_START_PAYLOAD,
)
from xm4_optimizer.nvdm.cxd90050 import OptimizerCoefficient
from xm4_optimizer.nvdm.parser import parse_nvdm_image
from xm4_optimizer.transport.protocol_mdr import MdrPacket, MdrPacketType
from xm4_optimizer.transport.virtual_device import VirtualXM4Device


class TestStatusDecoding:
    def test_known_status_codes(self) -> None:
        assert decode_optimizer_status(bytes([0x85, 0x00])) == (0x00, "NOT_RUNNING")
        assert decode_optimizer_status(bytes([0x85, 0x03])) == (0x03, "ANALYZING")
        assert decode_optimizer_status(bytes([0x85, 0x04])) == (0x04, "FINISHED")

    def test_unknown_status_and_wrong_payload(self) -> None:
        assert decode_optimizer_status(bytes([0x85, 0x7F]))[1].startswith("UNKNOWN")
        with pytest.raises(ValueError):
            decode_optimizer_status(bytes([0x66, 0x04]))


class TestFraming:
    def test_start_frame_carries_verified_payload(self) -> None:
        wire = build_start_frame(seq=1)
        packet, remaining = MdrPacket.from_wire_bytes(wire)
        assert packet is not None
        assert remaining == b""
        assert packet.packet_type == MdrPacketType.COMMAND
        assert packet.seq_num == 1
        assert packet.payload == OPTIMIZER_START_PAYLOAD == bytes.fromhex("84010001")


class TestCoefficientExtraction:
    def test_extract_from_seeded_nvdm(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        partition = parse_nvdm_image(bytes(dev.flash))
        words = extract_coeff_words(bytes(dev.flash))
        item = partition.get_value("config", "optimizer_coeff")
        assert item is not None
        assert words == list(OptimizerCoefficient.unpack(item).words)
        assert len(words) == 10

    def test_format_coeff_contains_labels(self) -> None:
        text = format_coeff([0x10000000] + [0] * 9, "BEFORE")
        assert "BEFORE" in text
        assert "L:b0" in text
        assert "R:a2" in text
        assert "0x10000000" in text

    def test_malformed_image_raises(self) -> None:
        with pytest.raises(Exception):
            extract_coeff_words(b"not an nvdm image")


class _FakeSocket:
    """Minimal socket stub that replays one chunk and records ACK frames."""

    def __init__(self, chunk: bytes) -> None:
        self._chunk = chunk
        self.sent: list[bytes] = []

    def settimeout(self, _seconds: float) -> None:
        pass

    def recv(self, _size: int) -> bytes:
        chunk, self._chunk = self._chunk, b""
        return chunk

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)


class TestMonitor:
    def test_follows_status_until_finished_and_not_running(self) -> None:
        statuses = [0x01, 0x02, 0x03, 0x04, 0x00]
        frames = b"".join(
            MdrPacket(
                packet_type=0x03,  # notification
                seq_num=i % 2,
                payload=bytes([0x85, code, 0x00]),
            ).to_wire_bytes()
            for i, code in enumerate(statuses)
        )
        sock = _FakeSocket(frames)
        events = monitor_optimizer(sock, timeout_s=0.1)

        assert [code for code, _ in events] == statuses
        assert [name for _, name in events][-1] == "NOT_RUNNING"
        # one start frame + one ACK per notification
        assert len(sock.sent) == len(statuses) + 1

    def test_no_status_frames(self) -> None:
        sock = _FakeSocket(b"")
        assert monitor_optimizer(sock, timeout_s=0.05) == []


class TestCli:
    def test_dry_run_sends_nothing(self, capsys) -> None:
        from tools.optimizer_trigger import main

        rc = main(["--mac", "00:11:22:33:44:55"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Dry run" in out
