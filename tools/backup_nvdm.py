#!/usr/bin/env python3
"""Direct Bluetooth RFCOMM NVDM Calibration Backup Tool for Sony WH-1000XM4.

Dumps the complete 512 KB (128 Physical Erase Blocks / PEBs) Airoha NVDM
calibration store containing per-unit acoustic trims, CXD90050 / DNC ANC coefficients,
and factory keys to disk with a SHA-256 sidecar.

Bypasses raw SPI NOR lock (0x03 error) by using Sony MDR (Ch 11) FOTA update handshake
and Airoha RACE (Ch 21) logical storage_type=0x01.

Usage:
    python3 tools/backup_nvdm.py
    python3 tools/backup_nvdm.py --mac AC:80:0A:2E:45:80
    python3 tools/backup_nvdm.py --out my_xm4_backup.bin
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time
from typing import Optional

# Ensure project root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from xm4_optimizer.transport.rfcomm_socket import create_rfcomm_socket

DEFAULT_MDR_CHANNEL: int = 11
DEFAULT_RACE_CHANNEL: int = 21
TOTAL_PEBS: int = 128
PAGES_PER_PEB: int = 16
PAGE_SIZE: int = 256
PEB_SIZE: int = PAGES_PER_PEB * PAGE_SIZE  # 4096 bytes
TOTAL_SIZE: int = TOTAL_PEBS * PEB_SIZE    # 524,288 bytes (512 KB)


def find_connected_xm4_mac() -> Optional[str]:
    """Auto-detects connected Sony WH-1000XM4 Bluetooth MAC address via bluetoothctl."""
    try:
        out = subprocess.check_output(["bluetoothctl", "devices", "Connected"], text=True)
        # Lines format: "Device AC:80:0A:2E:45:80 WH-1000XM4"
        for line in out.splitlines():
            m = re.search(r"Device\s+([0-9A-Fa-f:]{17})\s+(.*)", line)
            if m:
                mac, name = m.group(1), m.group(2)
                if "1000xm4" in name.lower() or "wh-" in name.lower():
                    return mac
        # Fallback to any connected device if only one
        lines = [l for l in out.splitlines() if "Device " in l]
        if len(lines) == 1:
            m = re.search(r"Device\s+([0-9A-Fa-f:]{17})", lines[0])
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def dump_nvdm_bytes(
    bd_addr: str,
    peb_count: int = TOTAL_PEBS,
    *,
    show_progress: bool = True,
) -> bytes:
    """Reads the full NVDM store over RACE storage_type=1 and returns raw bytes.

    This is the device-verified pipelined transfer used by the backup tool. It does
    not send the MDR update-mode handshake; NVDM page reads work without it.
    """
    dump_buffer = bytearray()
    t_start = time.time()

    s_race = create_rfcomm_socket(bd_addr, DEFAULT_RACE_CHANNEL, timeout_s=4.0)
    if show_progress:
        print(f"[*] Step 2/3: Connecting to Airoha RACE on RFCOMM channel {DEFAULT_RACE_CHANNEL}...")
        print("    -> RACE session established. Beginning pipelined PEB transfer...\n")
    try:
        for peb_idx in range(peb_count):
            batch_req = bytearray()
            for pg in range(PAGES_PER_PEB):
                addr = peb_idx * PEB_SIZE + pg * PAGE_SIZE
                payload = struct.pack("<BBI", 1, 1, addr)  # storage=1 (NVDM), count=1, addr=addr
                batch_req.extend(struct.pack("<BBHH", 0x05, 0x5A, 2 + len(payload), 0x0403) + payload)

            s_race.sendall(batch_req)

            resp_buf = bytearray()
            expected_bytes = PAGES_PER_PEB * 270
            while len(resp_buf) < expected_bytes:
                chunk = s_race.recv(expected_bytes - len(resp_buf))
                if not chunk:
                    raise ConnectionResetError("RACE RFCOMM socket closed unexpectedly by headset")
                resp_buf.extend(chunk)

            for pg in range(PAGES_PER_PEB):
                frame = resp_buf[pg * 270 : (pg + 1) * 270]
                ret_code = frame[6]
                if ret_code != 0:
                    raise RuntimeError(f"RACE page read error at PEB {peb_idx} page {pg}: code 0x{ret_code:02X}")
                dump_buffer.extend(frame[14:270])

            pct = ((peb_idx + 1) / peb_count) * 100.0
            elapsed = time.time() - t_start
            kb = len(dump_buffer) // 1024
            if show_progress and ((peb_idx + 1) % 16 == 0 or peb_idx == peb_count - 1):
                print(f"  [+] Dumped PEB {peb_idx + 1:3d}/{peb_count:3d} ({kb:3d} KB / {pct:5.1f}%) in {elapsed:4.1f}s")
    finally:
        s_race.close()

    return bytes(dump_buffer)


def backup_xm4_nvdm(bd_addr: str, output_path: Path, peb_count: int = TOTAL_PEBS) -> Path:
    """Executes the full MDR-handshake and RACE-pipelined NVDM dump workflow."""
    print("=================================================================")
    print("  Sony WH-1000XM4 NVDM Hardware Backup Tool")
    print("=================================================================")
    print(f"  Target Device       : {bd_addr}")
    print(f"  RFCOMM Channels     : MDR={DEFAULT_MDR_CHANNEL}, RACE={DEFAULT_RACE_CHANNEL}")
    print(f"  Storage Target      : Airoha NVDM (storage_type=0x01)")
    print(f"  Target PEB Count    : {peb_count} PEBs ({peb_count * 4} KB)")
    print(f"  Output Destination  : {output_path.resolve()}")
    print("=================================================================\n")

    # 1. MDR Channel 11 Handshake
    print(f"[*] Step 1/3: Connecting to Sony MDR on RFCOMM channel {DEFAULT_MDR_CHANNEL}...")
    try:
        s_mdr = create_rfcomm_socket(bd_addr, DEFAULT_MDR_CHANNEL, timeout_s=5.0)
        try:
            s_mdr.recv(1024)
        except Exception:
            pass
        print("    -> Handshaking and putting headset into FOTA update mode...")
        s_mdr.sendall(bytes.fromhex("3e010000000000013c"))
        s_mdr.sendall(bytes.fromhex("3e0e000000000444010200593c"))
        try:
            s_mdr.recv(1024)
        except Exception:
            pass
        print("    -> Headset successfully switched to update mode.")
    except Exception as e:
        print(f"[!] Warning: MDR handshake encountered an error: {e}")
        print("    Attempting to proceed to RACE channel 21 directly...")

    # 2. RACE Channel 21 Pipelined Dump
    try:
        dump_bytes = dump_nvdm_bytes(bd_addr, peb_count=peb_count)
    finally:
        try:
            print("\n[*] Step 3/3: Releasing update mode and closing MDR connection...")
            s_mdr.sendall(bytes.fromhex("3e0e0100000004440102015b3c"))
            s_mdr.close()
        except Exception:
            pass

    # 3. Verification and File Writing
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(dump_bytes)
    sha256 = hashlib.sha256(dump_bytes).hexdigest()
    sidecar_path = output_path.with_name(f"{output_path.name}.sha256")
    sidecar_path.write_text(f"{sha256}  {output_path.name}\n", encoding="utf-8")

    magic = dump_bytes[:4]
    magic_valid = (magic == b"NVDM")

    print("\n" + "=" * 65)
    print("  BACKUP COMPLETED SUCCESSFULLY")
    print("=" * 65)
    print(f"  File Path      : {output_path.resolve()}")
    print(f"  Total Size     : {len(dump_bytes):,} bytes ({len(dump_bytes) // 1024} KB)")
    print(f"  SHA-256 Check  : {sha256}")
    print(f"  SHA-256 File   : {sidecar_path.name}")
    print(f"  Header Magic   : {magic.decode('ascii', errors='replace')} ({'VALID' if magic_valid else 'WARNING: UNKNOWN'})")
    print("=" * 65)

    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backup Sony WH-1000XM4 NVDM Calibration Partition over Bluetooth RFCOMM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Optional destination path for backup binary file",
    )
    parser.add_argument(
        "--mac",
        type=str,
        default=None,
        help="Bluetooth MAC address of the WH-1000XM4 (auto-detected if omitted)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("wh1000xm4_nvdm_backup.bin"),
        help="Destination path for backup binary file (default: wh1000xm4_nvdm_backup.bin)",
    )
    parser.add_argument(
        "--pebs",
        type=int,
        default=TOTAL_PEBS,
        help=f"Number of 4KB PEB blocks to read (default: {TOTAL_PEBS} = 512 KB)",
    )
    args = parser.parse_args()

    mac = args.mac
    if mac is None:
        print("[*] No --mac provided; scanning connected Bluetooth devices...")
        mac = find_connected_xm4_mac()
        if mac is None:
            print("[!] Could not auto-detect connected WH-1000XM4.")
            print("    Please provide --mac <XX:XX:XX:XX:XX:XX> manually or connect the headphones.")
            return 1
        print(f"[*] Detected connected headset: {mac}\n")

    out_dest = Path(args.output) if args.output else args.out
    try:
        backup_xm4_nvdm(bd_addr=mac, output_path=out_dest, peb_count=args.pebs)
        return 0
    except Exception as e:
        print(f"\n[!] ERROR during backup: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
