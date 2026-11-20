"""Tests for the MDR v1 control client and the SDP channel lookup.

Payload fixtures were captured from a WH-1000XM4 on firmware 2.5.0 (2026-10-26).
"""

from __future__ import annotations

import socket
import uuid

import pytest

from xm4_optimizer.transport.protocol_mdr import MdrPacket
from xm4_optimizer.transport.sdp import find_rfcomm_channels, parse_data_element
from xm4_optimizer.transport.sony_control import (
    AmbientSoundState,
    AncMode,
    AudioSetting,
    GeneralSetting,
    MessageType,
    SonyControlClient,
    SonyControlError,
    SystemSetting,
    decode_ambient,
    decode_battery,
    decode_firmware_version,
    encode_ambient_set,
)


class TestPayloadCodecs:
    def test_decode_captured_replies(self) -> None:
        assert decode_firmware_version(bytes.fromhex("050205322e352e30")) == "2.5.0"
        battery = decode_battery(bytes.fromhex("11005000"))
        assert battery.level == 80 and not battery.charging
        nc = decode_ambient(bytes.fromhex("6702010202010000"))
        assert nc == AmbientSoundState(AncMode.NOISE_CANCELLING, False, 0)
        ambient = decode_ambient(bytes.fromhex("670201020001000a"))
        assert ambient == AmbientSoundState(AncMode.AMBIENT, False, 10)

    @pytest.mark.parametrize(
        "state, wire",
        [
            (AmbientSoundState(AncMode.AMBIENT, False, 10), "680211020001000a"),
            (AmbientSoundState(AncMode.NOISE_CANCELLING, False, 0), "6802110202010000"),
            (AmbientSoundState(AncMode.WIND, False, 0), "6802110201010000"),
            (AmbientSoundState(AncMode.OFF, False, 0), "6802000200010000"),
        ],
    )
    def test_encode_ambient_set(self, state: AmbientSoundState, wire: str) -> None:
        assert encode_ambient_set(state).hex() == wire

    def test_encode_rejects_bad_level(self) -> None:
        with pytest.raises(ValueError):
            encode_ambient_set(AmbientSoundState(AncMode.AMBIENT, False, 21))

    def test_decode_rejects_wrong_payload(self) -> None:
        with pytest.raises(SonyControlError):
            decode_ambient(bytes.fromhex("11005000"))


class FakeHeadset:
    """Socket double that answers MDR v1 requests like the captured WH-1000XM4."""

    def __init__(self) -> None:
        self.state = bytes.fromhex("6702010202010000")
        self.sent_payloads: list[bytes] = []
        self._outbox = b""

    def settimeout(self, _t: float) -> None:
        pass

    def close(self) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        pkt, _ = MdrPacket.from_wire_bytes(data)
        if pkt is None or pkt.packet_type == MessageType.ACK:
            return
        self.sent_payloads.append(pkt.payload)
        out = [MdrPacket(MessageType.ACK, 1 - pkt.seq_num, b"")]
        code = pkt.payload[0]
        if code == 0x04:
            out.append(MdrPacket(MessageType.COMMAND_1, 1, bytes.fromhex("050205322e352e30")))
        elif code == 0x10:
            out.append(MdrPacket(MessageType.COMMAND_1, 1, bytes.fromhex("11005000")))
        elif code == 0x66:
            out.append(MdrPacket(MessageType.COMMAND_1, 1, self.state))
        elif code == 0x68:
            p = pkt.payload
            self.state = bytes([0x67, 0x02, 0x00 if p[2] == 0 else 0x01]) + p[3:]
            out.append(MdrPacket(MessageType.COMMAND_1, 1, bytes([0x69]) + self.state[1:]))
        self._outbox += b"".join(m.to_wire_bytes() for m in out)

    def recv(self, _n: int) -> bytes:
        if not self._outbox:
            raise socket.timeout()
        data, self._outbox = self._outbox, b""
        return data


class TestSonyControlClient:
    def test_get_and_set_with_readback(self) -> None:
        dev = FakeHeadset()
        client = SonyControlClient(dev, timeout_s=0.5)
        assert client.get_firmware_version() == "2.5.0"
        assert client.get_battery().level == 80
        assert client.get_ambient_sound().mode is AncMode.NOISE_CANCELLING
        result = client.set_ambient_sound(AmbientSoundState(AncMode.AMBIENT, True, 15))
        assert result == AmbientSoundState(AncMode.AMBIENT, True, 15)
        assert dev.sent_payloads[-2].hex() == "680211020001010f"

    def test_no_reply_raises(self) -> None:
        class Silent(FakeHeadset):
            def sendall(self, data: bytes) -> None:
                pass

        with pytest.raises(SonyControlError):
            SonyControlClient(Silent(), timeout_s=0.3).get_battery()


class SettingsHeadset:
    """Socket double for the MDR setting families (Table 1 and Table 2)."""

    def __init__(self) -> None:
        self.sent_payloads: list[bytes] = []
        self.sent_tables: list[int] = []
        self.state: dict[tuple[int, int], int] = {
            (0xD6, 0xD1): 1, (0xD6, 0xD2): 0,
            (0xE6, 0x01): 1, (0xE6, 0x02): 1,
            (0xF6, 0x03): 1, (0xF6, 0x04): 0, (0xF6, 0x05): 0,
            (0x48, 0x01): 0,
        }
        self._outbox = b""

    def settimeout(self, _t: float) -> None:
        pass

    def close(self) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        pkt, _ = MdrPacket.from_wire_bytes(data)
        if pkt is None or pkt.packet_type == MessageType.ACK:
            return
        self.sent_payloads.append(pkt.payload)
        self.sent_tables.append(pkt.packet_type)
        p = pkt.payload
        out = [MdrPacket(MessageType.ACK, 1 - pkt.seq_num, b"")]
        table = pkt.packet_type
        if p[0] in (0xD6, 0xE6, 0xF6):
            key = (p[0], p[1])
            value = self.state.get(key, 0)
            if p[0] == 0xF6 and p[1] == 0x04:
                ret = bytes([0xF7, 0x04, 0x01, 0x11 if value else 0x10, 0x00])
            else:
                ret = bytes([p[0] + 1, p[1], 0x01 if p[0] != 0xE6 else 0x00, value])
            out.append(MdrPacket(table, 1, ret))
        elif p[0] in (0xD8, 0xE8, 0xF8):
            self.state[(p[0] - 2, p[1])] = p[3]
        elif p[0] == 0x48:
            value = p[3]
            self.state[(0x48, 0x01)] = value
            out.append(MdrPacket(MessageType.COMMAND_2, 1, bytes([0x49, 0x01, 0x01, value])))
        elif p[0] == 0x56:
            out.append(MdrPacket(table, 1, bytes([0x57, 0x01, 0x00, 0x06] + [10] * 6)))
        self._outbox += b"".join(m.to_wire_bytes() for m in out)

    def recv(self, _n: int) -> bytes:
        if not self._outbox:
            raise socket.timeout()
        data, self._outbox = self._outbox, b""
        return data


class TestMdrSettings:
    def test_read_settings(self) -> None:
        client = SonyControlClient(SettingsHeadset(), timeout_s=0.2)
        assert client.get_general_setting(GeneralSetting.TOUCH_PANEL).hex() == "d7d10101"
        assert client.get_audio_setting(AudioSetting.DSEE).hex() == "e7020001"
        assert client.get_system_setting(SystemSetting.SPEAK_TO_CHAT).hex() == "f7050100"

    def test_write_settings_with_readback(self) -> None:
        dev = SettingsHeadset()
        client = SonyControlClient(dev, timeout_s=0.2)
        assert client.set_general_setting(GeneralSetting.TOUCH_PANEL, 0).hex() == "d7d10100"
        assert client.set_audio_setting(AudioSetting.DSEE, 0).hex() == "e7020000"
        assert client.set_system_setting(SystemSetting.SPEAK_TO_CHAT, 1).hex() == "f7050101"
        assert dev.sent_payloads[0].hex() == "d8d10100"
        assert dev.sent_payloads[2].hex() == "e8020000"
        assert dev.sent_payloads[4].hex() == "f8050101"

    def test_voice_guidance_uses_table2(self) -> None:
        dev = SettingsHeadset()
        client = SonyControlClient(dev, timeout_s=0.2)
        assert client.set_voice_guidance(False).hex() == "49010101"
        assert dev.sent_payloads[0].hex() == "48010101"
        assert dev.sent_tables[0] == MessageType.COMMAND_2

    def test_auto_power_off_encoding(self) -> None:
        dev = SettingsHeadset()
        client = SonyControlClient(dev, timeout_s=0.2)
        assert client.set_auto_power_off(0x1100).hex() == "f704011100"
        assert dev.sent_payloads[0].hex() == "f804011100"
        with pytest.raises(ValueError):
            client.set_auto_power_off(0x0F00)

    def test_eq_encoding(self) -> None:
        dev = SettingsHeadset()
        client = SonyControlClient(dev, timeout_s=0.2)
        client.set_eq_bands([0, 0, 0, 2, 6, 10])
        assert dev.sent_payloads[0].hex() == "5801ff060a0a0a0c1014"
        client.set_eq_preset(0xA1)
        assert dev.sent_payloads[2].hex() == "5801a100"


class TestSdp:
    def test_rfcomm_channel_from_attribute_list(self) -> None:
        # One record: attribute 0x0004 = ((L2CAP), (RFCOMM, channel 9))
        proto = b"\x35\x0c" + b"\x35\x03\x19\x01\x00" + b"\x35\x05\x19\x00\x03\x08\x09"
        record = b"\x35" + bytes([3 + 2 + len(proto) - 2]) + b"\x09\x00\x04" + b"\x35" + bytes([len(proto) - 2]) + proto[2:]
        lists = b"\x35" + bytes([len(record)]) + record
        decoded, end = parse_data_element(lists)
        assert end == len(lists)
        assert find_rfcomm_channels(decoded) == [9]

    def test_uuid128_decodes(self) -> None:
        u = uuid.UUID("96cc203e-5068-46ad-b32d-e316f5e069ba")
        value, _ = parse_data_element(b"\x1c" + u.bytes)
        assert value == u
