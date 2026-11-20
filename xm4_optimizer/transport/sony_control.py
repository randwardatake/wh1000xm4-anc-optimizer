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
from typing import Optional, Sequence

from xm4_optimizer.transport.protocol_mdr import MdrPacket, MdrStreamDecoder
from xm4_optimizer.transport.rfcomm_socket import create_rfcomm_socket
from xm4_optimizer.transport.sdp import SdpError, query_rfcomm_channel

SONY_CONTROL_UUID_V1: str = "96cc203e-5068-46ad-b32d-e316f5e069ba"
# Device-verified channel on the test unit. Used only when SDP cannot be reached
# (for example, a host without L2CAP SDP support); prefer SDP whenever available.
DEFAULT_CONTROL_CHANNEL: int = 9


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
    # Table 1 (0x0C) setting families. Verified on XM4 v2.5.0; see
    # https://github.com/Fanxingkeming/sony-headphones-control/blob/main/docs/protocol-reference.md
    EQ_GET = 0x56
    EQ_RET = 0x57
    EQ_SET = 0x58
    GENERAL_GET = 0xD6
    GENERAL_RET = 0xD7
    GENERAL_SET = 0xD8
    AUDIO_GET = 0xE6
    AUDIO_RET = 0xE7
    AUDIO_SET = 0xE8
    SYSTEM_GET = 0xF6
    SYSTEM_RET = 0xF7
    SYSTEM_SET = 0xF8
    NC_OPTIMIZER_START = 0x84
    NC_OPTIMIZER_STATE_GET = 0x86
    NC_OPTIMIZER_STATE_RET = 0x87
    # Table 2 (0x0E) voice guidance
    VOICE_GUIDANCE_SET = 0x48
    VOICE_GUIDANCE_RET = 0x49


class GeneralSetting(IntEnum):
    TOUCH_PANEL = 0xD1
    MULTIPOINT = 0xD2


class AudioSetting(IntEnum):
    CONNECTION_QUALITY = 0x01
    DSEE = 0x02


class SystemSetting(IntEnum):
    PAUSE_WHEN_REMOVED = 0x03
    AUTO_POWER_OFF = 0x04
    SPEAK_TO_CHAT = 0x05


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
            try:
                channel = query_rfcomm_channel(bd_addr, SONY_CONTROL_UUID_V1)
            except (OSError, SdpError):
                # SDP needs a raw L2CAP socket, which some hosts do not allow.
                channel = DEFAULT_CONTROL_CHANNEL
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

    def _send(self, payload: bytes, table: MessageType = MessageType.COMMAND_1) -> None:
        self._sock.sendall(MdrPacket(packet_type=table, seq_num=self._seq, payload=payload).to_wire_bytes())
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

    def request(self, payload: bytes, reply_codes: tuple,
                table: MessageType = MessageType.COMMAND_1) -> bytes:
        self._send(payload, table=table)
        for reply in self._receive(time.monotonic() + self._timeout_s):
            if reply[0] in reply_codes:
                return reply
        raise SonyControlError(f"No reply to request {payload.hex()} (table 0x{table:02X})")

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

    # --- Settings exposed by the MDR v1 control service ---------------------
    # These are the commands the official app uses. The firmware persists the
    # resulting state to NVDM itself, so no raw flash write is needed.

    def get_eq(self) -> bytes:
        """6-band EQ: reply is 57 01 <preset> 06 <6 bytes>, gains biased by +10."""
        return self.request(bytes([Code.EQ_GET, 0x01]), (Code.EQ_RET,))

    def set_eq_preset(self, preset: int) -> bytes:
        if not 0 <= preset <= 0xFF:
            raise ValueError("preset must be a byte")
        self._send(bytes([Code.EQ_SET, 0x01, preset, 0x00]))
        time.sleep(0.2)
        return self.get_eq()

    def set_eq_bands(self, bands_gain_db: Sequence[float]) -> bytes:
        """Six gains in dB (-10..10); encoded as gain+10."""
        if len(bands_gain_db) != 6:
            raise ValueError("exactly six EQ bands are required")
        encoded = []
        for gain in bands_gain_db:
            value = int(round(gain)) + 10
            if not 0 <= value <= 20:
                raise ValueError("EQ gains must be within -10..10 dB")
            encoded.append(value)
        self._send(bytes([Code.EQ_SET, 0x01, 0xFF, 0x06, *encoded]))
        time.sleep(0.2)
        return self.get_eq()

    def get_general_setting(self, setting: GeneralSetting) -> bytes:
        return self.request(bytes([Code.GENERAL_GET, setting]), (Code.GENERAL_RET,))

    def set_general_setting(self, setting: GeneralSetting, value: int) -> bytes:
        self._send(bytes([Code.GENERAL_SET, setting, 0x01, value & 0xFF]))
        time.sleep(0.2)
        return self.get_general_setting(setting)

    def get_audio_setting(self, setting: AudioSetting) -> bytes:
        return self.request(bytes([Code.AUDIO_GET, setting]), (Code.AUDIO_RET,))

    def set_audio_setting(self, setting: AudioSetting, value: int) -> bytes:
        self._send(bytes([Code.AUDIO_SET, setting, 0x00, value & 0xFF]))
        time.sleep(0.2)
        return self.get_audio_setting(setting)

    def get_system_setting(self, setting: SystemSetting) -> bytes:
        return self.request(bytes([Code.SYSTEM_GET, setting]), (Code.SYSTEM_RET,))

    def set_system_setting(self, setting: SystemSetting, value: int) -> bytes:
        self._send(bytes([Code.SYSTEM_SET, setting, 0x01, value & 0xFF]))
        time.sleep(0.2)
        return self.get_system_setting(setting)

    def set_auto_power_off(self, mode: int) -> bytes:
        """XM4 v2.5.0 accepts only 0x1000 (off when removed) or 0x1100 (disabled)."""
        if mode not in (0x1000, 0x1100):
            raise ValueError("mode must be 0x1000 (off when removed) or 0x1100 (disabled)")
        self._send(bytes([Code.SYSTEM_SET, SystemSetting.AUTO_POWER_OFF, 0x01,
                          (mode >> 8) & 0xFF, mode & 0xFF]))
        time.sleep(0.2)
        return self.get_system_setting(SystemSetting.AUTO_POWER_OFF)

    def set_voice_guidance(self, enabled: bool) -> bytes:
        """Table 2 command; 0x00 = on, 0x01 = off (libmdr OnOffSettingValue)."""
        value = 0x00 if enabled else 0x01
        return self.request(
            bytes([Code.VOICE_GUIDANCE_SET, 0x01, 0x01, value]),
            (Code.VOICE_GUIDANCE_RET,),
            table=MessageType.COMMAND_2,
        )

    def start_nc_optimizer(self) -> None:
        self._send(bytes([Code.NC_OPTIMIZER_START, 0x01, 0x00, 0x01]))

    def get_nc_optimizer_state(self) -> bytes:
        return self.request(bytes([Code.NC_OPTIMIZER_STATE_GET, 0x01]),
                            (Code.NC_OPTIMIZER_STATE_RET,))
