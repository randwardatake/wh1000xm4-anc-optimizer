"""Minimal Bluetooth SDP client: resolve the RFCOMM channel of a service UUID.

Sends a ServiceSearchAttributeRequest (Bluetooth Core Spec, Vol 3, Part B, §4.7) over
L2CAP PSM 1 and walks the ProtocolDescriptorList (attribute 0x0004) of each matching
record for the RFCOMM protocol UUID (0x0003) followed by its channel number.
Linux needs a Python built with AF_BLUETOOTH / BTPROTO_L2CAP; on macOS the lookup is
delegated to IOBluetooth through macos_bridge.
"""

from __future__ import annotations

import socket
import struct
import sys
import uuid as uuid_mod
from typing import Any, List, Optional, Tuple

SDP_PSM: int = 1
PDU_SERVICE_SEARCH_ATTR_REQ: int = 0x06
PDU_SERVICE_SEARCH_ATTR_RSP: int = 0x07
PDU_ERROR_RSP: int = 0x01
ATTR_PROTOCOL_DESCRIPTOR_LIST: int = 0x0004
UUID_RFCOMM: int = 0x0003

# Data element types (high 5 bits of the header byte)
DE_NIL, DE_UINT, DE_INT, DE_UUID, DE_TEXT, DE_BOOL, DE_SEQ, DE_ALT, DE_URL = range(9)
_FIXED_SIZES = {0: 1, 1: 2, 2: 4, 3: 8, 4: 16}


class SdpError(Exception):
    """Raised when the SDP server returns an error or a malformed response."""


def parse_data_element(buf: bytes, pos: int = 0) -> Tuple[Any, int]:
    """Decodes one SDP data element at `pos`. Returns (value, next_pos).

    Sequences and alternatives decode to lists, UUIDs to ints (16/32-bit) or
    uuid.UUID (128-bit), unsigned/signed integers to ints, text/URL to bytes.
    """
    header = buf[pos]
    de_type, size_idx = header >> 3, header & 0x07
    pos += 1
    if de_type == DE_NIL:
        return None, pos
    if size_idx in _FIXED_SIZES:
        length = _FIXED_SIZES[size_idx]
    elif size_idx == 5:
        length = buf[pos]
        pos += 1
    elif size_idx == 6:
        (length,) = struct.unpack_from(">H", buf, pos)
        pos += 2
    else:
        (length,) = struct.unpack_from(">I", buf, pos)
        pos += 4
    raw = buf[pos:pos + length]
    if len(raw) != length:
        raise SdpError("Truncated SDP data element")
    end = pos + length
    if de_type in (DE_SEQ, DE_ALT):
        items: List[Any] = []
        while pos < end:
            item, pos = parse_data_element(buf, pos)
            items.append(item)
        return items, end
    if de_type == DE_UUID:
        return (uuid_mod.UUID(bytes=bytes(raw)) if length == 16 else int.from_bytes(raw, "big")), end
    if de_type == DE_UINT:
        return int.from_bytes(raw, "big"), end
    if de_type == DE_INT:
        return int.from_bytes(raw, "big", signed=True), end
    if de_type == DE_BOOL:
        return bool(raw[0]), end
    return bytes(raw), end


def find_rfcomm_channels(attribute_lists: Any) -> List[int]:
    """Extracts RFCOMM channel numbers from decoded AttributeLists of matching records."""
    channels: List[int] = []

    def walk(node: Any) -> None:
        if not isinstance(node, list):
            return
        if len(node) >= 2 and node[0] == UUID_RFCOMM and isinstance(node[1], int):
            channels.append(node[1])
            return
        for child in node:
            walk(child)

    for record in attribute_lists or []:
        # Each record is [attr_id, value, attr_id, value, ...]
        for i in range(0, len(record) - 1, 2):
            if record[i] == ATTR_PROTOCOL_DESCRIPTOR_LIST:
                walk(record[i + 1])
    return channels


def build_search_attr_request(service_uuid: uuid_mod.UUID, transaction_id: int, continuation: bytes = b"") -> bytes:
    """Builds a ServiceSearchAttributeRequest for one 128-bit UUID and attribute 0x0004."""
    pattern = b"\x35\x11\x1c" + service_uuid.bytes
    attr_ids = b"\x35\x03\x09" + struct.pack(">H", ATTR_PROTOCOL_DESCRIPTOR_LIST)
    params = pattern + struct.pack(">H", 0xFFFF) + attr_ids + bytes([len(continuation)]) + continuation
    return struct.pack(">BHH", PDU_SERVICE_SEARCH_ATTR_REQ, transaction_id, len(params)) + params


def query_rfcomm_channel(bd_addr: str, service_uuid: str, timeout_s: float = 5.0) -> Optional[int]:
    """Returns the first RFCOMM channel the device advertises for `service_uuid`, or None."""
    if sys.platform == "darwin":
        from xm4_optimizer.transport import macos_bridge
        return macos_bridge.query_rfcomm_channel(bd_addr, service_uuid, timeout_s)
    target = uuid_mod.UUID(service_uuid)
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    sock.settimeout(timeout_s)
    try:
        sock.connect((bd_addr, SDP_PSM))
        collected = b""
        continuation = b""
        for transaction_id in range(1, 33):
            sock.send(build_search_attr_request(target, transaction_id, continuation))
            rsp = sock.recv(4096)
            pdu_id, _tid, _plen = struct.unpack_from(">BHH", rsp, 0)
            if pdu_id == PDU_ERROR_RSP:
                raise SdpError(f"SDP error response: {rsp[5:7].hex()}")
            if pdu_id != PDU_SERVICE_SEARCH_ATTR_RSP:
                raise SdpError(f"Unexpected SDP PDU 0x{pdu_id:02X}")
            (byte_count,) = struct.unpack_from(">H", rsp, 5)
            collected += rsp[7:7 + byte_count]
            cont_len = rsp[7 + byte_count]
            continuation = rsp[8 + byte_count:8 + byte_count + cont_len]
            if cont_len == 0:
                break
        else:
            raise SdpError("SDP continuation did not terminate")
    finally:
        sock.close()
    if not collected:
        return None
    lists, _ = parse_data_element(collected)
    channels = find_rfcomm_channels(lists)
    return channels[0] if channels else None
