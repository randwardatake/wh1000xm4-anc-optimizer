"""Take and verify NVDM settings backups, used before every firmware change.

A backup counts as good when it is the full 512 KiB, starts with the `NVDM` magic and
parses into at least one active item. Corrupted items are reported but do not fail the
check: they describe the headset's store, not a failed read.

Contributed by abdulhajiyev (PR #4); see CREDITS.md.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import hashlib
from pathlib import Path
import sys
from typing import Optional

from xm4_optimizer.nvdm.parser import NvdmError, parse_nvdm_image

from xm4_optimizer.paths import BACKUP_DIR, REPO_ROOT

NVDM_DUMP_SIZE = 512 * 1024


class BackupCheckError(Exception):
    """Raised when a backup is missing, truncated or unreadable."""


@dataclass(frozen=True)
class BackupSummary:
    path: Path
    sha256: str
    active_items: int
    corrupted_items: int

    def describe(self) -> str:
        return (f"{self.path.name}: {self.active_items} active items, "
                f"{self.corrupted_items} corrupted, SHA-256 {self.sha256[:16]}…")


def verify_backup(path: Path) -> BackupSummary:
    path = Path(path)
    if not path.is_file():
        raise BackupCheckError(f"Backup file not found: {path}")
    data = path.read_bytes()
    if len(data) != NVDM_DUMP_SIZE:
        raise BackupCheckError(f"Backup is {len(data)} bytes, expected {NVDM_DUMP_SIZE}")
    if data[:4] != b"NVDM":
        raise BackupCheckError("Backup does not start with the NVDM header")
    try:
        part = parse_nvdm_image(data)
    except NvdmError as e:
        raise BackupCheckError(f"Backup could not be parsed: {e}") from e
    if not part.active_items:
        raise BackupCheckError("Backup contains no active settings items")
    return BackupSummary(path, hashlib.sha256(data).hexdigest(), len(part.active_items), len(part.corrupted_items))


def pre_firmware_backup_path(target_version: str, current_version: Optional[str] = None,
                             backup_dir: Path = BACKUP_DIR) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    current = f"fw{current_version}_" if current_version else ""
    return Path(backup_dir) / f"xm4_nvdm_{current}{stamp}_before-{target_version}.bin"


def take_verified_backup(bd_addr: str, out_path: Path) -> BackupSummary:
    """Reads the full NVDM store over Bluetooth to `out_path` and verifies it."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from tools.backup_nvdm import backup_xm4_nvdm

    backup_xm4_nvdm(bd_addr=bd_addr, output_path=Path(out_path))
    return verify_backup(Path(out_path))
