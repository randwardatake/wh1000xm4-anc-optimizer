#!/usr/bin/env python3
"""RACE NVKEY read/write probe for Airoha-based headsets.

NVKEYs are a separate key-value store from the `config/*` NVDM items. On Airoha
devices the write opcode is 0x0A01 with payload <keyID:2 LE><data...>; it is proven
on a Turtle Beach Stealth 600 Gen 3 and untested on the WH-1000XM4.

`noop` reads a key, writes the same bytes back and reads again. This proves the
write path without changing any value.

Usage:
    python3 tools/nvkey_write_probe.py --mac <MAC> --noop 0x1003
    python3 tools/nvkey_write_probe.py --mac <MAC> --read 0x1003
    python3 tools/nvkey_write_probe.py --mac <MAC> --write 0xFFFF 01
"""
from __future__ import annotations

import argparse
from pathlib import Path
import socket
import struct
import sys
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from xm4_optimizer.transport.protocol_race import RacePacket, RaceHeaderType, RACE_HEADER_SIZE
from xm4_optimizer.transport.rfcomm_socket import create_rfcomm_socket

RACE_CHANNEL = 21
CMD_NVKEY_READ = 0x0A00
CMD_NVKEY_WRITE = 0x0A01


def request(sock: socket.socket, cmd: int, payload: bytes, timeout: float = 5.0) -> bytes:
    packet = RacePacket(
        type=RaceHeaderType.CMD_EXPECTS_RESPONSE,
        cmd_id=cmd,
        payload=payload,
    )
    sock.sendall(packet.to_bytes())
    sock.settimeout(timeout)
    header = b""
    while len(header) < RACE_HEADER_SIZE:
        header += sock.recv(RACE_HEADER_SIZE - len(header))
    _head, _type, length, resp_cmd = struct.unpack("<BBHH", header)
    body = b""
    while len(body) < length - 2:
        body += sock.recv(length - 2 - len(body))
    if resp_cmd != cmd:
        raise RuntimeError(f"unexpected RACE response cmd 0x{resp_cmd:04X}")
    return body


def nvkey_read(sock: socket.socket, key_id: int, length: int = 256) -> bytes:
    body = request(sock, CMD_NVKEY_READ, struct.pack("<HH", key_id, length))
    if len(body) < 2:
        raise RuntimeError(f"short NVKEY read response: {body.hex()}")
    size = struct.unpack("<H", body[:2])[0]
    return body[2:2 + size]


def nvkey_write(sock: socket.socket, key_id: int, data: bytes) -> bytes:
    if not data:
        raise ValueError("zero-length NVKEY writes are rejected by firmware")
    return request(sock, CMD_NVKEY_WRITE, struct.pack("<H", key_id) + data)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mac", default=None)
    parser.add_argument("--read", type=lambda v: int(v, 0), default=None)
    parser.add_argument("--write", nargs=2, metavar=("KEY", "HEX"), default=None)
    parser.add_argument("--noop", type=lambda v: int(v, 0), default=None)
    args = parser.parse_args(argv)

    if args.mac is None:
        from tools.backup_nvdm import find_connected_xm4_mac
        args.mac = find_connected_xm4_mac()
    if not args.mac:
        print("ERROR: no connected WH-1000XM4 found; pass --mac <ADDR>.")
        return 2

    sock = create_rfcomm_socket(args.mac, RACE_CHANNEL, timeout_s=5.0)
    try:
        if args.read is not None:
            value = nvkey_read(sock, args.read)
            print(f"NVKEY 0x{args.read:04X} = {value.hex()}")
        elif args.write is not None:
            key, hexdata = args.write
            key = int(key, 0)
            data = bytes.fromhex(hexdata)
            resp = nvkey_write(sock, key, data)
            print(f"write 0x{key:04X} = {data.hex()} -> {resp.hex()}")
            print(f"readback: {nvkey_read(sock, key, max(len(data), 16)).hex()}")
        elif args.noop is not None:
            before = nvkey_read(sock, args.noop)
            print(f"before: {before.hex()}")
            resp = nvkey_write(sock, args.noop, before)
            print(f"write response: {resp.hex()}")
            after = nvkey_read(sock, args.noop)
            print(f"after:  {after.hex()}")
            print("NOOP OK" if before == after else "CHANGED (unexpected)")
            return 0 if before == after else 1
        else:
            parser.print_help()
            return 1
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
