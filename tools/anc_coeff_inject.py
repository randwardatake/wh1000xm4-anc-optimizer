#!/usr/bin/env python3
"""Persistent ANC coefficient injection via NCOPTSET + NC Optimizer start.

Device-verified on v2.5.0 (2026-11-20): NCOPTSET changes the DSP optimizer input,
then the MDR optimizer start command makes the firmware write a 40-byte result to
config/optimizer_coeff. The value survives a power cycle and is restored from NVDM
at boot. The DSP transforms the supplied bias, so the stored words are not the same
as the injected ones; check stability before listening.

Usage:
    python3 tools/anc_coeff_inject.py --mac <MAC> --gain 7.5
    python3 tools/anc_coeff_inject.py --mac <MAC> --words 0x10000000,0,...
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

from tools.backup_nvdm import find_connected_xm4_mac
from tools.config_write_probe import dump_with_retry, send_write_candidates
from tools.optimizer_trigger import monitor_optimizer
from xm4_optimizer.dsp.synthesizer import synthesize_optimizer_coeff
from xm4_optimizer.nvdm.cxd90050 import OPTIMIZER_COEFF_WORDS, OptimizerCoefficient
from xm4_optimizer.nvdm.parser import parse_nvdm_image
from xm4_optimizer.transport.rfcomm_socket import create_rfcomm_socket

IDENTITY = OptimizerCoefficient().words


def parse_words(text: str) -> list[int]:
    words = [int(w, 0) & 0xFFFFFFFF for w in text.split(",")]
    if len(words) != OPTIMIZER_COEFF_WORDS:
        raise ValueError(f"expected {OPTIMIZER_COEFF_WORDS} words, got {len(words)}")
    return words


def inject(mac: str, words: list[int], out_dir: Path, timeout_s: float = 60.0) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    print("bias words:", [f"0x{w:08X}" for w in words])

    commands = [f"AT+NCOPTTOOL=NCOPTSET,{i},0x{w:08X}" for i, w in enumerate(words)]
    responses = send_write_candidates(mac, commands)
    if any("ERROR" in resp.upper() for _, resp in responses):
        print("ERROR: NCOPTSET was rejected")
        return 2

    sock = create_rfcomm_socket(mac, 9, timeout_s=5.0)
    try:
        try:
            sock.recv(1024)
        except Exception:
            pass
        events = monitor_optimizer(sock, timeout_s=timeout_s)
    finally:
        sock.close()
    print(f"optimizer status frames: {len(events)} "
          f"(FINISHED seen: {any(name == 'FINISHED' for _, name in events)})")

    data = dump_with_retry(mac)
    dump_path = out_dir / "nvdm_after_inject.bin"
    dump_path.write_bytes(data)
    stored = OptimizerCoefficient.unpack(
        parse_nvdm_image(data).get_value("config", "optimizer_coeff")
    ).words
    print("stored words:", [f"0x{w:08X}" for w in stored])
    if stored == IDENTITY:
        print("RESULT: optimizer_coeff is still identity; the write did not take effect.")
        return 1
    print(f"RESULT: persistent non-identity coefficient written (dump: {dump_path}).")
    print("Power-cycle the headset and re-dump to confirm boot restore, then check "
          "for instability/howling before using it with audio.")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mac", default=None)
    parser.add_argument("--gain", type=float, default=7.5,
                        help="synthesize a stable filter with this target gain (dB)")
    parser.add_argument("--words", default=None,
                        help="ten comma-separated 32-bit bias words (hex/decimal)")
    parser.add_argument("--out", type=Path, default=Path("anc_inject"))
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)

    try:
        words = parse_words(args.words) if args.words else list(
            synthesize_optimizer_coeff(target_gain_db=args.gain, fs=48000.0)[0].words
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    mac = args.mac or find_connected_xm4_mac()
    if not mac:
        print("ERROR: no connected WH-1000XM4 found; pass --mac <ADDR>.")
        return 2
    try:
        return inject(mac, words, args.out, timeout_s=args.timeout)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
