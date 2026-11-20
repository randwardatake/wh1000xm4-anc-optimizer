#!/usr/bin/env python3
"""Controlled NC Optimizer experiment for the Sony WH-1000XM4.

Starts the firmware's Personal NC Optimizer, follows its 0x85 status notifications
and compares config/optimizer_coeff in NVDM before and after the run. This tests the
only code path that is known to write a non-identity optimizer coefficient
(FUN_082bb5d8, called at optimizer generation end).

The optimizer start is a device action: it runs the analysis and may change NVDM.
Dumps contain personal data and stay in the local output directory.

Usage:
    python3 tools/optimizer_trigger.py --mac AC:80:0A:2E:45:80 --status-only
    python3 tools/optimizer_trigger.py --mac <MAC> --out ./optimizer_experiment
    python3 tools/optimizer_trigger.py --mac <MAC> --out ./optimizer_experiment --start
"""
from __future__ import annotations

import argparse
from pathlib import Path
import socket
import sys
import time
from typing import Callable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.backup_nvdm import dump_nvdm_bytes, find_connected_xm4_mac
from xm4_optimizer.nvdm.cxd90050 import OptimizerCoefficient
from xm4_optimizer.nvdm.parser import parse_nvdm_image
from xm4_optimizer.transport.protocol_mdr import (
    MdrPacket,
    MdrPacketType,
    MdrStreamDecoder,
)
from xm4_optimizer.transport.rfcomm_socket import create_rfcomm_socket

# Device-verified on firmware 2.5.0 (docs/STATUS.md). The exact MDR service/channel
# combination is selectable because it can differ between units.
OPTIMIZER_START_PAYLOAD: bytes = bytes.fromhex("84010001")
OPTIMIZER_STATUS_CODE: int = 0x85
DEFAULT_MDR_CHANNEL: int = 11
DEFAULT_TIMEOUT_S: float = 45.0
ACK_PACKET_TYPE: int = 0x01  # ACK type used by the working channel-11 handshake

# Names from Gadgetbridge's Sony NC Optimizer status values. The numeric mapping
# follows the order observed on device; the raw code is always printed as well.
STATUS_NAMES: dict[int, str] = {
    0x00: "NOT_RUNNING",
    0x01: "WEARING_CONDITION",
    0x02: "ATMOSPHERIC_PRESSURE",
    0x03: "ANALYZING",
    0x04: "FINISHED",
}
FINISHED_NAME = "FINISHED"

COEFF_LABELS = [
    "L:b0", "L:b1", "L:b2", "L:a1", "L:a2",
    "R:b0", "R:b1", "R:b2", "R:a1", "R:a2",
]


def decode_optimizer_status(payload: bytes) -> tuple[int, str]:
    """Returns (raw status code, label) for an 0x85 status payload."""
    if not payload or payload[0] != OPTIMIZER_STATUS_CODE:
        raise ValueError(f"Not an optimizer status payload: {payload.hex()}")
    code = payload[1] if len(payload) > 1 else 0
    return code, STATUS_NAMES.get(code, f"UNKNOWN(0x{code:02X})")


def extract_coeff_words(nvdm_bytes: bytes) -> list[int]:
    """Extracts the ten big-endian Q4.28 words of config/optimizer_coeff."""
    partition = parse_nvdm_image(nvdm_bytes)
    item = partition.get_value("config", "optimizer_coeff")
    if item is None:
        raise ValueError("config/optimizer_coeff not found in NVDM image")
    if len(item) != 40:
        raise ValueError(f"config/optimizer_coeff has {len(item)} bytes, expected 40")
    return list(OptimizerCoefficient.unpack(item).words)


def format_coeff(words: list[int], label: str) -> str:
    lines = [f"  {label}:"]
    for i, word in enumerate(words):
        name = COEFF_LABELS[i] if i < len(COEFF_LABELS) else f"[{i}]"
        lines.append(f"    [{i}] {name:5s} = 0x{word:08X}")
    return "\n".join(lines)


def build_start_frame(seq: int = 0) -> bytes:
    return MdrPacket(
        packet_type=MdrPacketType.COMMAND,
        seq_num=seq,
        payload=OPTIMIZER_START_PAYLOAD,
    ).to_wire_bytes()


def monitor_optimizer(
    sock: socket.socket,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    on_status: Optional[Callable[[int, str], None]] = None,
) -> list[tuple[int, str]]:
    """Sends the start command and collects 0x85 status events until FINISHED.

    Every received MDR frame is acknowledged, as in the working control client.
    """
    sock.settimeout(0.3)
    decoder = MdrStreamDecoder()
    events: list[tuple[int, str]] = []
    sock.sendall(build_start_frame())
    deadline = time.monotonic() + timeout_s
    saw_finished = False

    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except (socket.timeout, TimeoutError):
            continue
        if not chunk:
            break
        for packet in decoder.feed(chunk):
            ack = MdrPacket(
                packet_type=ACK_PACKET_TYPE,
                seq_num=1 - packet.seq_num,
                payload=b"",
            )
            sock.sendall(ack.to_wire_bytes())
            if not packet.payload or packet.payload[0] != OPTIMIZER_STATUS_CODE:
                continue
            code, name = decode_optimizer_status(packet.payload)
            events.append((code, name))
            if on_status is not None:
                on_status(code, name)
            if name == FINISHED_NAME:
                saw_finished = True
            elif saw_finished and name == "NOT_RUNNING":
                return events

    return events


def run_experiment(
    bd_addr: str,
    out_dir: Path,
    channel: int = DEFAULT_MDR_CHANNEL,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    skip_baseline: bool = False,
    status_only: bool = False,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = out_dir / "nvdm_before_optimizer.bin"
    after_path = out_dir / "nvdm_after_optimizer.bin"

    before_words: Optional[list[int]] = None
    if not status_only:
        if skip_baseline and baseline_path.exists():
            print(f"[*] Reusing baseline: {baseline_path}")
            before_words = extract_coeff_words(baseline_path.read_bytes())
        else:
            print("[*] Dumping NVDM before the optimizer run...")
            before = dump_nvdm_bytes(bd_addr, show_progress=True)
            baseline_path.write_bytes(before)
            before_words = extract_coeff_words(before)
        print(format_coeff(before_words, "BEFORE"))

    print(f"[*] Connecting to MDR channel {channel} and starting NC Optimizer...")
    sock = create_rfcomm_socket(bd_addr, channel, timeout_s=5.0)
    try:
        sock.recv(1024)
    except Exception:
        pass

    try:
        events = monitor_optimizer(
            sock,
            timeout_s=timeout_s,
            on_status=lambda code, name: print(f"    status 0x{code:02X} {name}"),
        )
    finally:
        sock.close()

    if not events:
        print("ERROR: no optimizer status frames received; check the MDR channel and payload.")
        return 2
    if not any(name == FINISHED_NAME for _, name in events):
        print("ERROR: optimizer did not report FINISHED before the timeout.")
        return 2

    if status_only:
        print(f"[+] Optimizer finished ({len(events)} status frames). NVDM was not read.")
        return 0

    print("[*] Dumping NVDM after the optimizer run...")
    after = dump_nvdm_bytes(bd_addr, show_progress=True)
    after_path.write_bytes(after)
    after_words = extract_coeff_words(after)
    print(format_coeff(after_words, "AFTER"))

    if after_words == before_words:
        print("\nRESULT: config/optimizer_coeff is unchanged. The optimizer result did not")
        print("        persist to NVDM on this run (same outcome as the earlier runs).")
        return 1
    print("\nRESULT: config/optimizer_coeff changed. The optimizer generation-end write")
    print("        is confirmed on this device; inspect the values before using them.")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mac", default=None, help="Bluetooth MAC (auto-detected if omitted)")
    parser.add_argument("--out", type=Path, default=Path("optimizer_experiment"),
                        help="Directory for the locally kept NVDM dumps")
    parser.add_argument("--channel", type=int, default=DEFAULT_MDR_CHANNEL,
                        help=f"RFCOMM channel for the MDR optimizer command (default {DEFAULT_MDR_CHANNEL})")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                        help=f"Seconds to wait for FINISHED (default {DEFAULT_TIMEOUT_S})")
    parser.add_argument("--start", action="store_true",
                        help="Actually send the optimizer start command (required)")
    parser.add_argument("--status-only", action="store_true",
                        help="Only follow status frames; do not dump NVDM")
    parser.add_argument("--skip-baseline", action="store_true",
                        help="Reuse an existing nvdm_before_optimizer.bin")
    args = parser.parse_args(argv)

    if not args.start:
        print("Dry run. No command was sent. Pass --start to run the optimizer on the headset.")
        print(f"  target MAC      : {args.mac or 'auto-detect'}")
        print(f"  MDR channel     : {args.channel}")
        print(f"  start payload   : {OPTIMIZER_START_PAYLOAD.hex()}")
        print(f"  output directory: {args.out.resolve()}")
        return 0

    bd_addr = args.mac or find_connected_xm4_mac()
    if not bd_addr:
        print("ERROR: no connected WH-1000XM4 found; pass --mac <ADDR>.")
        return 2

    try:
        return run_experiment(
            bd_addr,
            args.out,
            channel=args.channel,
            timeout_s=args.timeout,
            skip_baseline=args.skip_baseline,
            status_only=args.status_only,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
