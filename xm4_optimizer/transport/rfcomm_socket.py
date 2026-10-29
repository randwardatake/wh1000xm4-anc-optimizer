"""Native RFCOMM Socket Transport for Sony WH-1000XM4 (Linux and macOS).

Provides native Linux AF_BLUETOOTH RFCOMM socket handling (IOBluetooth relay on macOS), retry with exponential backoff,
timeout management, and the concrete implementation of the DeviceTransport protocol.
"""

from __future__ import annotations

import socket
import struct
import sys
import time
from typing import Callable, List, Optional, Protocol, Tuple, runtime_checkable

from xm4_optimizer.transport.protocol_race import (
    AtciTunnelRequest,
    AtciTunnelResponse,
    PageProgramRequest,
    PageProgramResponse,
    PageReadRequest,
    PageReadResponse,
    RacePacket,
    FLASH_PAGE_SIZE,
    RACE_HEADER_SIZE,
)

DEFAULT_MDR_CHANNEL: int = 11
DEFAULT_RACE_CHANNEL: int = 21
DEFAULT_TIMEOUT_S: float = 3.0
DEFAULT_RETRY_COUNT: int = 5
DEFAULT_BACKOFF_FACTOR: float = 0.2
NVDM_PARTITION_BASE: int = 0x000DF000
NVDM_PARTITION_SIZE: int = 0x80000  # 512 KB (2048 pages of 256 bytes)


def create_rfcomm_socket(bd_addr: str, channel: int, timeout_s: float = DEFAULT_TIMEOUT_S) -> socket.socket:
    """Creates and connects an RFCOMM socket on Linux or macOS.

    Supports native socket.AF_BLUETOOTH if exported by Python, with transparent
    libc socket(31, 1, 3) fallback for Python environments built without Bluetooth headers.
    On macOS the socket is relayed through the IOBluetooth helper in macos_bridge.
    """
    if sys.platform == "darwin":
        from xm4_optimizer.transport.macos_bridge import open_rfcomm_socket
        return open_rfcomm_socket(bd_addr, channel, timeout_s)

    if hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_RFCOMM"):
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        s.settimeout(timeout_s)
        s.connect((bd_addr, channel))
        return s

    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    libc.socket.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    libc.socket.restype = ctypes.c_int
    libc.connect.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    libc.connect.restype = ctypes.c_int

    fd = libc.socket(31, 1, 3)  # AF_BLUETOOTH (31), SOCK_STREAM (1), BTPROTO_RFCOMM (3)
    if fd < 0:
        err = ctypes.get_errno()
        raise OSError(err, f"libc.socket(AF_BLUETOOTH) failed: {err}")

    mac_bytes = bytes.fromhex(bd_addr.replace(":", ""))[::-1]
    # Linux struct sockaddr_rc { sa_family_t rc_family; bdaddr_t rc_bdaddr; uint8_t rc_channel; uint8_t pad; }
    addr_rc = struct.pack("<H6sBB", 31, mac_bytes, channel, 0)
    ret = libc.connect(fd, addr_rc, len(addr_rc))
    if ret != 0:
        err = ctypes.get_errno()
        import os
        os.close(fd)
        raise OSError(err, f"Failed to connect to {bd_addr} on RFCOMM ch {channel}: error {err}")

    s = socket.socket(fileno=fd)
    s.settimeout(timeout_s)
    return s


def list_connected_devices() -> List[Tuple[str, str]]:
    """Returns (MAC, name) of connected Bluetooth devices via bluetoothctl, or IOBluetooth on macOS."""
    if sys.platform == "darwin":
        from xm4_optimizer.transport import macos_bridge
        return macos_bridge.list_connected_devices()

    import re
    import subprocess
    out = subprocess.check_output(["bluetoothctl", "devices", "Connected"], text=True)
    devices = []
    for line in out.splitlines():
        m = re.search(r"Device\s+([0-9A-Fa-f:]{17})\s*(.*)", line)
        if m:
            devices.append((m.group(1), m.group(2)))
    return devices


def is_device_connected(bd_addr: str) -> bool:
    """True if `bd_addr` is among the connected Bluetooth devices."""
    return bd_addr.upper() in (mac.upper() for mac, _ in list_connected_devices())


def find_connected_xm4_mac() -> Optional[str]:
    """Auto-detects connected Sony WH-1000XM4 Bluetooth MAC address."""
    try:
        devices = list_connected_devices()
    except Exception:
        return None
    for mac, name in devices:
        if "1000xm4" in name.lower() or "wh-" in name.lower():
            return mac
    if len(devices) == 1:
        return devices[0][0]
    return None


# --- Exception Hierarchy ---


class TransportError(Exception):
    """Base exception for transport communication errors."""


class BluetoothConnectionError(TransportError):
    """Raised when RFCOMM socket fails to connect or pairing fails."""


class BluetoothTimeoutError(TransportError):
    """Raised when socket operation exceeds timeout threshold."""


class DeviceDisconnectedError(TransportError):
    """Raised on unexpected remote disconnection / broken pipe."""


class FlashOperationError(TransportError):
    """Raised when physical flash read, write, or erase operation fails."""


class AtciCommandError(TransportError):
    """Raised when an AT command execution returns an ERROR status."""


# --- Interface Contract ---


@runtime_checkable
class DeviceTransport(Protocol):
    """Abstract interface contract for device communication."""

    def connect(self) -> None:
        """Connects to the device transport."""
        ...

    def close(self) -> None:
        """Closes the device transport connection."""
        ...

    def read_flash_page(self, address: int) -> bytes:
        """Reads exactly 256 bytes from NOR flash address (must be 0x100 aligned)."""
        ...

    def write_flash_page(self, address: int, data: bytes) -> bool:
        """Writes exactly 256 bytes with Airoha fota_checksum."""
        ...

    def send_atci_command(self, cmd: str, timeout_ms: int = 3000) -> str:
        """Sends AT command string and returns response string."""
        ...


# --- Low-Level Socket Wrapper ---


class RfcommSocket:
    """Manages raw Linux AF_BLUETOOTH RFCOMM socket with timeout and reconnect."""

    def __init__(self, bd_addr: str, channel: int, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.bd_addr = bd_addr
        self.channel = channel
        self.timeout_s = timeout_s
        self._sock: Optional[socket.socket] = None

    def connect(self, retry_count: int = DEFAULT_RETRY_COUNT) -> None:
        last_error: Optional[Exception] = None
        for attempt in range(retry_count):
            try:
                self.close()
                self._sock = create_rfcomm_socket(
                    bd_addr=self.bd_addr,
                    channel=self.channel,
                    timeout_s=self.timeout_s,
                )
                return
            except (OSError, TimeoutError) as e:
                last_error = e
                delay = DEFAULT_BACKOFF_FACTOR * (2 ** attempt)
                time.sleep(delay)

        raise BluetoothConnectionError(
            f"Failed to connect to {self.bd_addr} on channel {self.channel} after {retry_count} attempts: {last_error}"
        )

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            finally:
                self._sock = None

    def is_connected(self) -> bool:
        return self._sock is not None

    def send_all(self, data: bytes) -> None:
        if self._sock is None:
            raise DeviceDisconnectedError("Cannot send: socket is not connected")
        try:
            self._sock.sendall(data)
        except (BrokenPipeError, ConnectionResetError) as e:
            self.close()
            raise DeviceDisconnectedError(f"Connection lost during send: {e}") from e
        except TimeoutError as e:
            raise BluetoothTimeoutError(f"Send timed out: {e}") from e

    def recv_exact(self, exact_bytes: int) -> bytes:
        if self._sock is None:
            raise DeviceDisconnectedError("Cannot recv: socket is not connected")
        buf = bytearray()
        while len(buf) < exact_bytes:
            try:
                chunk = self._sock.recv(exact_bytes - len(buf))
                if not chunk:
                    self.close()
                    raise DeviceDisconnectedError("Socket closed prematurely by remote device")
                buf.extend(chunk)
            except TimeoutError as e:
                raise BluetoothTimeoutError(f"Recv timed out waiting for {exact_bytes} bytes (received {len(buf)}B): {e}") from e
            except (BrokenPipeError, ConnectionResetError) as e:
                self.close()
                raise DeviceDisconnectedError(f"Socket connection reset during recv: {e}") from e
        return bytes(buf)


# --- High-Level RFCOMM Transport ---


class RfcommDeviceTransport:
    """Concrete DeviceTransport implementing communication over RFCOMM Channel 21 (RACE)."""

    def __init__(
        self,
        bd_addr: str,
        race_channel: int = DEFAULT_RACE_CHANNEL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.bd_addr = bd_addr
        self.race_channel = race_channel
        self.timeout_s = timeout_s
        self._socket = RfcommSocket(bd_addr=bd_addr, channel=race_channel, timeout_s=timeout_s)

    def connect(self) -> None:
        self._socket.connect()

    def close(self) -> None:
        self._socket.close()

    def is_connected(self) -> bool:
        return self._socket.is_connected()

    def send_race_packet(self, packet: RacePacket) -> RacePacket:
        """Sends a RACE packet and reads back the response RACE packet."""
        wire_cmd = packet.to_bytes()
        self._socket.send_all(wire_cmd)

        hdr_bytes = self._socket.recv_exact(RACE_HEADER_SIZE)
        head, type_, length, cmd_id = struct.unpack("<BBHH", hdr_bytes)
        payload_len = length - 2
        payload_bytes = self._socket.recv_exact(payload_len) if payload_len > 0 else b""
        return RacePacket(head=head, type=type_, cmd_id=cmd_id, payload=payload_bytes)

    def read_flash_page(self, address: int) -> bytes:
        """Reads exactly 256 bytes from NOR flash address (must be 0x100 aligned)."""
        req = PageReadRequest(address=address)
        resp_pkt = self.send_race_packet(req.to_packet())
        resp = PageReadResponse.from_payload(resp_pkt.payload)
        if resp.return_code != 0:
            raise FlashOperationError(f"RACE read flash page failed at 0x{address:08X} with code 0x{resp.return_code:02X}")
        return resp.data

    def write_flash_page(self, address: int, data: bytes) -> bool:
        """Writes exactly 256 bytes with Airoha fota_checksum."""
        req = PageProgramRequest(address=address, data=data)
        resp_pkt = self.send_race_packet(req.to_packet())
        resp = PageProgramResponse.from_payload(resp_pkt.payload)
        if resp.return_code != 0:
            raise FlashOperationError(f"RACE write flash page failed at 0x{address:08X} with code 0x{resp.return_code:02X}")
        return True

    def send_atci_command(self, cmd: str, timeout_ms: int = 3000) -> str:
        """Sends AT command string and returns response string."""
        req = AtciTunnelRequest(command=cmd)
        resp_pkt = self.send_race_packet(req.to_packet())
        resp = AtciTunnelResponse.from_payload(resp_pkt.payload)
        text = resp.response_text
        if "ERROR" in text:
            raise AtciCommandError(f"ATCI command '{cmd.strip()}' failed with response: {text.strip()}")
        return text


BluetoothRfcommTransport = RfcommDeviceTransport


def read_nvdm_partition(
    transport: DeviceTransport,
    base_addr: int = NVDM_PARTITION_BASE,
    size: int = NVDM_PARTITION_SIZE,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> bytes:
    """Reads NVDM partition image as contiguous bytes (default 512 KB = 2048 pages of 256 bytes)."""
    if size % FLASH_PAGE_SIZE != 0:
        raise ValueError(f"Partition size 0x{size:X} must be a multiple of 256 bytes")

    num_pages = size // FLASH_PAGE_SIZE
    partition_buffer = bytearray()

    for page_idx in range(num_pages):
        page_addr = base_addr + (page_idx * FLASH_PAGE_SIZE)
        page_data = transport.read_flash_page(page_addr)
        if len(page_data) != FLASH_PAGE_SIZE:
            raise FlashOperationError(f"Page at 0x{page_addr:08X} returned {len(page_data)} bytes, expected {FLASH_PAGE_SIZE}")
        partition_buffer.extend(page_data)
        if progress_callback is not None:
            progress_callback(page_idx + 1, num_pages)

    return bytes(partition_buffer)
