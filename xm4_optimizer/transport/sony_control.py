"""Sony headphone control over the MDR v1 service (RFCOMM, UUID 96cc203e-...).

Message types, command codes and payload layouts follow Gadgetbridge's
SonyProtocolImplV1 (https://codeberg.org/Freeyourgadget/Gadgetbridge) and were
verified on a WH-1000XM4 running firmware 2.5.0 (see docs/STATUS.md).

Frames reuse the 0x3E ... 0x3C framing, 0x3D escaping and modulo-256 checksum
implemented by `MdrPacket`; only the message type values differ from the
emulator's `MdrPacketType`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
import socket
import time
from typing import Optional

from xm4_optimizer.transport.protocol_mdr import MdrPacket, MdrStreamDecoder
from xm4_optimizer.transport.rfcomm_socket import DEFAULT_MDR_CHANNEL, create_rfcomm_socket
from xm4_optimizer.transport.sdp import query_rfcomm_channel

SONY_CONTROL_UUID_V1: str = "96cc203e-5068-46ad-b32d-e316f5e069ba"


class MessageType(IntEnum):
    ACK = 0x01
    COMMAND_1 = 0x0C
    COMMAND_2 = 0x0E


class Code(IntEnum):
    INIT_REQUEST = 0x00
    FW_VERSION_REQUEST = 0x04
    FW_VERSION_REPLY = 0x05
    BATTERY_LEVEL_REQUEST = 0x10
    BATTERY_LEVEL_REPLY = 0x11
    AMBIENT_SOUND_CONTROL_GET = 0x66
    AMBIENT_SOUND_CONTROL_RET = 0x67
    AMBIENT_SOUND_CONTROL_SET = 0x68
    AMBIENT_SOUND_CONTROL_NOTIFY = 0x69


class AncMode(Enum):
    OFF = "off"
    AMBIENT = "ambient"
    WIND = "wind"
    NOISE_CANCELLING = "nc"


@dataclass(frozen=True)
class AmbientSoundState:
    mode: AncMode
    focus_on_voice: bool
    ambient_level: int  # 0..20


@dataclass(frozen=True)
class BatteryState:
    level: int  # percent
    charging: bool


class SonyControlError(Exception):
    """Raised when the headset does not answer or answers with an unexpected payload."""


def encode_ambient_set(state: AmbientSoundState) -> bytes:
    """Builds an AMBIENT_SOUND_CONTROL_SET payload for wind-capable V1 devices (XM4)."""
    if not 0 <= state.ambient_level <= 20:
        raise ValueError("ambient_level must be 0..20")
    sub_mode = {AncMode.NOISE_CANCELLING: 0x02, AncMode.WIND: 0x01}.get(state.mode, 0x00)
    return bytes([
        Code.AMBIENT_SOUND_CONTROL_SET, 0x02,
        0x00 if state.mode is AncMode.OFF else 0x11,
        0x02, sub_mode,
        0x01,
        0x01 if state.focus_on_voice else 0x00,
        state.ambient_level,
    ])


def decode_ambient(payload: bytes) -> AmbientSoundState:
    """Decodes an AMBIENT_SOUND_CONTROL_RET/NOTIFY payload (8 bytes)."""
    if len(payload) != 8 or payload[0] not in (Code.AMBIENT_SOUND_CONTROL_RET, Code.AMBIENT_SOUND_CONTROL_NOTIFY):
        raise SonyControlError(f"Not an ambient sound payload: {payload.hex()}")
    if payload[2] == 0x00:
        mode = AncMode.OFF
    elif payload[3] == 0x02:
        mode = {0x00: AncMode.AMBIENT, 0x01: AncMode.WIND, 0x02: AncMode.NOISE_CANCELLING}.get(payload[4])
    else:
        mode = {0x00: AncMode.AMBIENT, 0x01: AncMode.NOISE_CANCELLING}.get(payload[4])
    if mode is None:
        raise SonyControlError(f"Unknown ambient sound mode in {payload.hex()}")
    return AmbientSoundState(mode=mode, focus_on_voice=payload[6] == 0x01, ambient_level=payload[7])


def decode_firmware_version(payload: bytes) -> str:
    if len(payload) < 3 or payload[0] != Code.FW_VERSION_REPLY:
        raise SonyControlError(f"Not a firmware version payload: {payload.hex()}")
    return payload[3:3 + payload[2]].decode("ascii", errors="replace")


def decode_battery(payload: bytes) -> BatteryState:
    if len(payload) < 4 or payload[0] != Code.BATTERY_LEVEL_REPLY:
        raise SonyControlError(f"Not a battery payload: {payload.hex()}")
    return BatteryState(level=payload[2], charging=payload[3] == 0x01)


class SonyControlClient:
    """Request/response client for the MDR v1 control service."""

    def __init__(self, sock: socket.socket, timeout_s: float = 3.0) -> None:
        self._sock = sock
        self._timeout_s = timeout_s
        self._seq = 0
        self._decoder = MdrStreamDecoder()
        self._sock.settimeout(0.3)

    @classmethod
    def connect(cls, bd_addr: str, channel: Optional[int] = None) -> "SonyControlClient":
        if channel is None:
            channel = query_rfcomm_channel(bd_addr, SONY_CONTROL_UUID_V1)
            if channel is None and not hasattr(socket, "BTPROTO_L2CAP"):
                channel = DEFAULT_MDR_CHANNEL  # no SDP over L2CAP on this platform (Windows)
            if channel is None:
                raise SonyControlError("Device does not advertise the Sony MDR v1 control service")
        client = cls(create_rfcomm_socket(bd_addr, channel, timeout_s=5.0))
        client._send(bytes([Code.INIT_REQUEST, 0x00]))
        client._drain(0.5)
        return client

    def close(self) -> None:
        self._sock.close()

    def __enter__(self) -> "SonyControlClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _send(self, payload: bytes) -> None:
        self._sock.sendall(MdrPacket(packet_type=MessageType.COMMAND_1, seq_num=self._seq, payload=payload).to_wire_bytes())
        self._seq ^= 1

    def _receive(self, deadline: float):
        """Yields command payloads until `deadline`, acknowledging each one."""
        while time.monotonic() < deadline:
            try:
                data = self._sock.recv(4096)
            except (socket.timeout, TimeoutError):
                continue
            if not data:
                raise SonyControlError("Connection closed by headset")
            for pkt in self._decoder.feed(data):
                if pkt.packet_type == MessageType.ACK:
                    continue
                ack = MdrPacket(packet_type=MessageType.ACK, seq_num=1 - pkt.seq_num, payload=b"")
                self._sock.sendall(ack.to_wire_bytes())
                if pkt.payload:
                    yield pkt.payload

    def _drain(self, seconds: float) -> None:
        for _ in self._receive(time.monotonic() + seconds):
            pass

    def request(self, payload: bytes, reply_codes: tuple) -> bytes:
        self._send(payload)
        for reply in self._receive(time.monotonic() + self._timeout_s):
            if reply[0] in reply_codes:
                return reply
        raise SonyControlError(f"No reply to request {payload.hex()}")

    def get_firmware_version(self) -> str:
        return decode_firmware_version(self.request(bytes([Code.FW_VERSION_REQUEST, 0x02]), (Code.FW_VERSION_REPLY,)))

    def get_battery(self) -> BatteryState:
        return decode_battery(self.request(bytes([Code.BATTERY_LEVEL_REQUEST, 0x00]), (Code.BATTERY_LEVEL_REPLY,)))

    def get_ambient_sound(self) -> AmbientSoundState:
        return decode_ambient(self.request(bytes([Code.AMBIENT_SOUND_CONTROL_GET, 0x02]), (Code.AMBIENT_SOUND_CONTROL_RET,)))

    def set_ambient_sound(self, state: AmbientSoundState) -> AmbientSoundState:
        """Sets the mode and returns the state read back from the headset."""
        self.request(encode_ambient_set(state), (Code.AMBIENT_SOUND_CONTROL_NOTIFY,))
        return self.get_ambient_sound()
