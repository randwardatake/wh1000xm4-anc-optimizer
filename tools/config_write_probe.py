#!/usr/bin/env python3
"""Probe write paths for one NVDM config item, with NVDM readback.

Only the item named on the command line is targeted. Without --write the tool only
prints the candidate ATCI commands. With --write it enables the RACE relay, sends the
candidates, dumps NVDM again and reports whether the item changed. No automatic
restore is performed; rerun with the old value to revert.

Dumps contain personal data and are kept in the local output directory.

Usage:
    python3 tools/config_write_probe.py --item test_vp_disable --value 1 --dry-run
    python3 tools/config_write_probe.py --mac <MAC> --item test_vp_disable --value 1 --write
"""
from __future__ import annotations

import argparse
from pathlib import Path
import socket
import struct
import sys
import time
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.backup_nvdm import dump_nvdm_bytes, find_connected_xm4_mac
from xm4_optimizer.nvdm.parser import parse_nvdm_image
from xm4_optimizer.transport.protocol_race import (
    AtciTunnelRequest,
    RaceHeaderType,
    RacePacket,
    RACE_HEADER_SIZE,
)
from xm4_optimizer.transport.rfcomm_socket import create_rfcomm_socket

RACE_CHANNEL = 21
ENABLE_RELAY = 0x020C
DISABLE_RELAY = 0x020D


def candidate_commands(group: str, item: str, value: int) -> list[str]:
    """Known and plausible ENVDM write argument layouts, tried in order."""
    hexval = f"{value:02X}"
    return [
        f"AT+ENVDM=W,{group},{item},{value}",
        f"AT+ENVDM=W,{group},{item},{hexval}",
        f"AT+ENVDM=W,{group},{item},0x{hexval}",
        f"AT+ENVDM=W,{group}/{item},{value}",
        f"AT+ENVDM=W,{group},{item},{value},1",
        f"AT+ENVDM=W,{group},{item},{hexval},1",
        # Firmware decompile: the handler skips one parameter after W, then parses
        # group, item, length (or "B" for hex) and the value.
        f"AT+ENVDM=W,1,{group},{item},1,{value}",
        f"AT+ENVDM=W,2,{group},{item},1,{value}",
        f"AT+ENVDM=W,0,{group},{item},1,{value}",
        f"AT+ENVDM=W,B,{group},{item},B,{hexval}",
        f"AT+ENVDM=W,1,{group},{item},B,{hexval}",
        f"AT+ENVDM=W,2,{group},{item},B,{hexval}",
    ]


def dump_with_retry(bd_addr: str, attempts: int = 5, delay_s: float = 3.0) -> bytes:
    """Releases relay mode and retries RACE connection after ATCI sessions.

    The firmware briefly refuses a new channel-21 connection (EBUSY) right after
    the relay is disabled, so a plain dump immediately afterwards fails.
    """
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            sock = create_rfcomm_socket(bd_addr, RACE_CHANNEL, timeout_s=5.0)
            try:
                _exchange(sock, RacePacket(cmd_id=DISABLE_RELAY, payload=b""))
            finally:
                sock.close()
            time.sleep(0.5)
            return dump_nvdm_bytes(bd_addr, show_progress=True)
        except OSError as exc:
            last_error = exc
            time.sleep(delay_s)
    raise RuntimeError(f"Could not reopen RACE channel after {attempts} attempts: {last_error}")


def read_item_value(dump: bytes, group: str, item: str) -> Optional[bytes]:
    partition = parse_nvdm_image(dump)
    return partition.get_value(group, item)


def _exchange(sock: socket.socket, packet: RacePacket, timeout: float = 5.0) -> bytes:
    sock.sendall(packet.to_bytes())
    sock.settimeout(timeout)
    data = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
            if len(data) >= RACE_HEADER_SIZE:
                # length is the RACE payload length plus two command-id bytes.
                _head, _type, plen, _cmd = struct.unpack("<BBHH", data[:RACE_HEADER_SIZE])
                if len(data) >= RACE_HEADER_SIZE + plen - 2:
                    break
    except socket.timeout:
        pass
    return data


def send_write_candidates(bd_addr: str, commands: list[str]) -> list[tuple[str, str]]:
    """Sends commands through ATCI relay mode and returns (command, response hex)."""
    sock = create_rfcomm_socket(bd_addr, RACE_CHANNEL, timeout_s=8.0)
    results: list[tuple[str, str]] = []
    try:
        resp = _exchange(sock, RacePacket(cmd_id=ENABLE_RELAY, payload=b""))
        print(f"  relay enable: {resp.hex() or '(no reply)'}")
        time.sleep(0.3)
        for command in commands:
            resp = _exchange(sock, AtciTunnelRequest(command=command).to_packet())
            payload = ""
            if len(resp) >= RACE_HEADER_SIZE:
                parsed, _ = RacePacket.from_bytes(resp)
                payload = parsed.payload.hex()
            results.append((command, payload))
            print(f"  {command}  ->  {payload or '(no reply)'}")
            time.sleep(0.2)
        resp = _exchange(sock, RacePacket(cmd_id=DISABLE_RELAY, payload=b""))
        print(f"  relay disable: {resp.hex() or '(no reply)'}")
    finally:
        sock.close()
    return results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mac", default=None)
    parser.add_argument("--group", default="config")
    parser.add_argument("--item", default="test_vp_disable")
    parser.add_argument("--value", type=int, default=1)
    parser.add_argument("--out", type=Path, default=Path("config_write_probe"))
    parser.add_argument("--baseline", type=Path, default=None,
                        help="Existing baseline dump to compare against")
    parser.add_argument("--write", action="store_true",
                        help="Actually send the commands and dump NVDM again")
    args = parser.parse_args(argv)

    commands = candidate_commands(args.group, args.item, args.value)
    if not args.write:
        print("Dry run. No command sent. Candidate commands:")
        for command in commands:
            print(f"  {command}")
        return 0

    bd_addr = args.mac or find_connected_xm4_mac()
    if not bd_addr:
        print("ERROR: no connected WH-1000XM4 found; pass --mac <ADDR>.")
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    baseline_path = args.baseline or args.out / "nvdm_before.bin"
    if args.baseline:
        baseline = baseline_path.read_bytes()
        print(f"[*] Using baseline {baseline_path}")
    else:
        print("[*] Dumping NVDM before the write attempt...")
        baseline = dump_nvdm_bytes(bd_addr, show_progress=True)
        baseline_path.write_bytes(baseline)

    old = read_item_value(baseline, args.group, args.item)
    print(f"[*] {args.group}/{args.item} before: {old!r}")

    print("[*] Sending ENVDM write candidates through ATCI relay...")
    send_write_candidates(bd_addr, commands)

    print("[*] Dumping NVDM after the write attempt...")
    after = dump_with_retry(bd_addr)
    after_path = args.out / "nvdm_after.bin"
    after_path.write_bytes(after)
    new = read_item_value(after, args.group, args.item)
    print(f"[*] {args.group}/{args.item} after:  {new!r}")

    if new == old:
        print("\nRESULT: item unchanged. None of the probed paths wrote NVDM.")
        return 1
    print(f"\nRESULT: item changed to {new!r}. Re-run with --value {int.from_bytes(old or b'0', 'big')}"
          " to restore it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
