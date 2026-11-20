#!/usr/bin/env python3
"""MDR tandem-test command 0xFF/6: whitelisted persistent NVDM write.

Reverse engineered from v2.5.0 firmware: the MDR dispatcher (FUN_082483AC) handles
code 0xFF, subcommand 6 as a raw NVDM read/write. For write, payload[6] = 2 and the
rest encodes group, item and value; FUN_0824B6CC validates them against a 79-entry
whitelist that includes config/test_vp_disable, config/eq_* and config/stc_enable.

Writing a setting this way makes the firmware store it in NVDM immediately; unlike
AT+NCOPTSET it does not depend on a power-off save.

Usage:
    python3 tools/mdr_nvdm_write.py --item test_vp_disable --value 1
    python3 tools/mdr_nvdm_write.py --mac <MAC> --item test_vp_disable --value 1 --write
"""
from __future__ import annotations

import argparse
from pathlib import Path
import socket
import sys
import time
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from xm4_optimizer.transport.protocol_mdr import MdrPacket, MdrStreamDecoder
from xm4_optimizer.transport.rfcomm_socket import create_rfcomm_socket
from xm4_optimizer.transport.sony_control import DEFAULT_CONTROL_CHANNEL, MessageType

CMD_TANDEM_TEST = 0xFF
SUBCMD_NVDM = 0x06


def build_nvdm_write_payload(group: str, item: str, value: bytes) -> bytes:
    """Builds the 0xFF/6 payload for one NVDM item write."""
    group_b = group.encode("ascii")
    item_b = item.encode("ascii")
    if len(group_b) > 255 or len(item_b) > 255:
        raise ValueError("group and item names must fit in one byte each")
    if len(value) > 0xFFFF:
        raise ValueError("value too long")
    return b"".join([
        bytes([CMD_TANDEM_TEST, SUBCMD_NVDM, 0x00, 0x01, 0x00, 0x00, 0x02]),
        bytes([len(group_b)]), group_b,
        bytes([len(item_b)]), item_b,
        len(value).to_bytes(2, "big"), value,
    ])


def send_and_listen(bd_addr: str, payload: bytes, channel: int = DEFAULT_CONTROL_CHANNEL,
                    listen_s: float = 2.0) -> list[bytes]:
    """Sends one MDR frame and returns payloads of all replies during listen_s."""
    sock = create_rfcomm_socket(bd_addr, channel, timeout_s=5.0)
    decoder = MdrStreamDecoder()
    replies: list[bytes] = []
    try:
        try:
            sock.recv(1024)
        except Exception:
            pass
        sock.sendall(MdrPacket(packet_type=MessageType.COMMAND_1, seq_num=0,
                               payload=payload).to_wire_bytes())
        deadline = time.monotonic() + listen_s
        sock.settimeout(0.3)
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(4096)
            except (socket.timeout, TimeoutError):
                continue
            if not chunk:
                break
            for pkt in decoder.feed(chunk):
                sock.sendall(MdrPacket(packet_type=MessageType.ACK,
                                       seq_num=1 - pkt.seq_num, payload=b"").to_wire_bytes())
                if pkt.payload:
                    replies.append(pkt.payload)
    finally:
        sock.close()
    return replies


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mac", default=None)
    parser.add_argument("--group", default="config")
    parser.add_argument("--item", default="test_vp_disable")
    parser.add_argument("--value", default="1", help="ASCII value to store (default: 1)")
    parser.add_argument("--channel", type=int, default=DEFAULT_CONTROL_CHANNEL)
    parser.add_argument("--write", action="store_true", help="actually send the command")
    args = parser.parse_args(argv)

    payload = build_nvdm_write_payload(args.group, args.item, args.value.encode("ascii"))
    print(f"payload: {payload.hex()}")
    if not args.write:
        print("Dry run. Pass --write to send this frame to the headset.")
        return 0

    bd_addr = args.mac
    if not bd_addr:
        from tools.backup_nvdm import find_connected_xm4_mac
        bd_addr = find_connected_xm4_mac()
    if not bd_addr:
        print("ERROR: no connected WH-1000XM4 found; pass --mac <ADDR>.")
        return 2
    replies = send_and_listen(bd_addr, payload, channel=args.channel)
    if not replies:
        print("ERROR: no reply from the headset.")
        return 2
    for reply in replies:
        print(f"reply: {reply.hex()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
