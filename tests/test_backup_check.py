from pathlib import Path

import pytest

from xm4_optimizer.backup_check import (
    NVDM_DUMP_SIZE,
    BackupCheckError,
    pre_firmware_backup_path,
    verify_backup,
)


def test_rejects_missing_short_and_wrong_magic(tmp_path: Path):
    with pytest.raises(BackupCheckError, match="not found"):
        verify_backup(tmp_path / "none.bin")
    short = tmp_path / "short.bin"
    short.write_bytes(b"NVDM" + b"\xff" * 100)
    with pytest.raises(BackupCheckError, match="bytes"):
        verify_backup(short)
    blank = tmp_path / "blank.bin"
    blank.write_bytes(b"\xff" * NVDM_DUMP_SIZE)
    with pytest.raises(BackupCheckError, match="header"):
        verify_backup(blank)


def test_backup_path_names_versions(tmp_path: Path):
    p = pre_firmware_backup_path("3.0.1", "2.5.0", backup_dir=tmp_path)
    assert p.parent == tmp_path
    assert p.name.startswith("xm4_nvdm_fw2.5.0_") and p.name.endswith("_before-3.0.1.bin")
    assert pre_firmware_backup_path("2.5.0", backup_dir=tmp_path).name.startswith("xm4_nvdm_2")


@pytest.mark.skipif(not (Path(__file__).resolve().parent.parent / "backups" / "my_xm4_nvdm.bin").exists(),
                    reason="no real device backup present")
def test_accepts_real_device_backup():
    s = verify_backup(Path(__file__).resolve().parent.parent / "backups" / "my_xm4_nvdm.bin")
    assert s.active_items > 0 and len(s.sha256) == 64
