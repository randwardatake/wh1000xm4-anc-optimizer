"""NVDM Backup and NCOPTSET-based Rollback for Sony WH-1000XM4.

Backup: reads the full NVDM partition (512 KB on real hardware, 128 KB on mock) with
SHA-256 sidecar. Rollback: sends identity coefficients via NCOPTSET to reset the DSP
to pass-through. Flash writes are blocked on retail firmware, and NCOPTSET does not
persist to NVDM on power-off (see docs/STATUS.md).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import time
from typing import Union

from xm4_optimizer.nvdm.cxd90050 import (
    OptimizerCoefficient,
    OPTIMIZER_COEFF_WORDS,
)
from xm4_optimizer.nvdm.parser import parse_nvdm_image
from xm4_optimizer.nvdm.peb import LEGACY_PARTITION_SIZE, NVDM_PARTITION_SIZE
from xm4_optimizer.transport.rfcomm_socket import (
    DeviceTransport,
    NVDM_PARTITION_BASE,
    NVDM_PARTITION_SIZE as TRANSPORT_PARTITION_SIZE,
    read_nvdm_partition,
)


class RollbackError(Exception):
    """Raised when NVDM partition backup or rollback fails."""


@dataclass
class BackupMetadata:
    """Metadata describing a verified NVDM backup image."""

    backup_path: Path
    sidecar_sha256_path: Path
    sha256_hex: str
    size_bytes: int
    active_item_count: int
    timestamp: float


def create_nvdm_backup(
    transport: DeviceTransport,
    backup_path: Union[str, Path],
    base_addr: int = NVDM_PARTITION_BASE,
    size: int = TRANSPORT_PARTITION_SIZE,
) -> BackupMetadata:
    """Reads the full NVDM partition, validates structure, and writes backup + .sha256."""
    dest = Path(backup_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    image_bytes = read_nvdm_partition(transport, base_addr=base_addr, size=size)

    partition = parse_nvdm_image(image_bytes, strict=True)
    sha256_hex = hashlib.sha256(image_bytes).hexdigest()

    dest.write_bytes(image_bytes)
    sidecar = Path(f"{dest}.sha256")
    sidecar.write_text(f"{sha256_hex}  {dest.name}\n", encoding="utf-8")

    return BackupMetadata(
        backup_path=dest,
        sidecar_sha256_path=sidecar,
        sha256_hex=sha256_hex,
        size_bytes=len(image_bytes),
        active_item_count=len(partition.active_items),
        timestamp=time.time(),
    )


def execute_rollback(
    transport: DeviceTransport,
    backup_file_path: Union[str, Path],
    verify: bool = True,
) -> bool:
    """Resets optimizer coefficients to identity via NCOPTSET.

    The backup file is validated but not flashed — flash writes are blocked on retail
    firmware. Instead, identity coefficients (b0=1.0, rest=0) are sent via NCOPTSET
    to reset live DSP state. NVDM persistence is not part of this path.
    """
    bpath = Path(backup_file_path)
    if not bpath.exists():
        raise FileNotFoundError(f"Backup file not found: {bpath}")

    backup_bytes = bpath.read_bytes()
    if len(backup_bytes) not in (NVDM_PARTITION_SIZE, LEGACY_PARTITION_SIZE):
        raise RollbackError(
            f"Invalid backup size: {len(backup_bytes)} bytes "
            f"(expected {NVDM_PARTITION_SIZE} or {LEGACY_PARTITION_SIZE})"
        )

    sidecar = Path(f"{bpath}.sha256")
    if sidecar.exists():
        expected_hash = sidecar.read_text(encoding="utf-8").strip().split()[0].lower()
        actual_hash = hashlib.sha256(backup_bytes).hexdigest().lower()
        if expected_hash != actual_hash:
            raise RollbackError(
                f"Backup SHA-256 sidecar mismatch: expected {expected_hash}, got {actual_hash}"
            )

    try:
        parse_nvdm_image(backup_bytes, strict=True)
    except Exception as exc:
        raise RollbackError(f"Backup NVDM image failed structural validation: {exc}") from exc

    identity = OptimizerCoefficient()
    for cmd in identity.to_ncoptset_commands():
        resp = transport.send_atci_command(cmd)
        if "ERROR" in resp.upper():
            raise RollbackError(f"NCOPTSET rollback failed: {resp!r}")

    if verify:
        resp = transport.send_atci_command("AT+NCOPTTOOL=NCOPTGET")
        if "ERROR" in resp.upper():
            raise RollbackError(f"NCOPTGET verify failed after rollback: {resp!r}")

    return True
