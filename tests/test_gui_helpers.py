"""Headless tests for the desktop app's glue logic (no Tk window is opened)."""

from __future__ import annotations

from pathlib import Path

import pytest

from xm4_optimizer import gui_helpers as helpers
from xm4_optimizer.transport.sony_control import AmbientSoundState, AncMode
from xm4_optimizer.transport.virtual_device import VirtualXM4Device


@pytest.fixture
def nvdm_image(tmp_path: Path) -> Path:
    dev = VirtualXM4Device(populate_defaults=True)
    dev.connect()
    path = tmp_path / "backup.bin"
    path.write_bytes(bytes(dev.flash))
    return path


def test_gui_module_imports_without_a_display() -> None:
    # Importing must work in a headless CI job; only App().mainloop() needs Tk.
    import xm4_optimizer.gui as gui

    assert gui.MIN_FLASH_BATTERY == 50
    assert gui.describe_anc is helpers.describe_anc


def test_describe_anc() -> None:
    assert helpers.describe_anc(AmbientSoundState(AncMode.NOISE_CANCELLING, False, 0)) == "Noise cancelling"
    assert helpers.describe_anc(AmbientSoundState(AncMode.AMBIENT, True, 12)) == (
        "Ambient sound (level 12, voice on)"
    )
    assert helpers.describe_anc(AmbientSoundState(AncMode.WIND, False, 0)) == "Wind noise reduction"
    assert helpers.describe_anc(AmbientSoundState(AncMode.OFF, False, 0)) == "Off"


def test_battery_gate() -> None:
    assert not helpers.battery_ok_for_flash(None)
    assert not helpers.battery_ok_for_flash(49)
    assert helpers.battery_ok_for_flash(50)
    assert helpers.battery_ok_for_flash(100)


def test_coefficient_word_helpers_roundtrip() -> None:
    words = helpers.synthesize_words(7.5)
    assert len(words) == 10
    assert all(0 <= w <= 0xFFFFFFFF for w in words)
    text = helpers.format_words(words)
    assert helpers.parse_words(text) == words
    assert helpers.parse_words("0x10, " + ", ".join(str(w) for w in words[1:]))[0] == 0x10


def test_nvdm_payload_and_value_helpers() -> None:
    assert helpers.parse_value("1", False) == b"1"
    assert helpers.parse_value("00 ff", True) == b"\x00\xff"
    with pytest.raises(ValueError, match="hex"):
        helpers.parse_value("zz", True)

    read_hex = helpers.nvdm_payload_preview("config", "test_vp_disable", b"", write=False)
    write_hex = helpers.nvdm_payload_preview("config", "test_vp_disable", b"1", write=True)
    # FF/06 frame: flag 1 selects read, flag 3 selects the persistent write.
    assert read_hex.startswith("ff0600010000 01".replace(" ", ""))
    assert write_hex.startswith("ff0600010000 03".replace(" ", ""))
    assert write_hex.endswith("31")
    assert "746573745f76705f64697361626c65" in write_hex  # test_vp_disable


def test_format_replies() -> None:
    assert "no replies" in helpers.format_replies([])
    assert helpers.format_replies([b"\x01", b"\xff"]) == (
        "  reply[0]: 01\n  reply[1]: ff"
    )


def test_dry_run_command_is_offline_and_writes_nothing(nvdm_image: Path) -> None:
    rc, text = helpers.dry_run_command(target_gain=7.5, image_path=nvdm_image)
    assert rc == 0
    assert "config/optimizer_coeff" in text
    assert "DRY-RUN BARRIER ACTIVE" in text
    assert "0 bytes" in text


def test_describe_nvdm_image(nvdm_image: Path) -> None:
    text = helpers.describe_nvdm_image(nvdm_image)
    assert "Active items" in text
    assert "PEBs" in text
    assert "config/optimizer_coeff" in text


def test_settings_command_rejects_unknown_key_without_hardware() -> None:
    rc, text = helpers.settings_command("00:00:00:00:00:00", "get", "not-a-key")
    assert rc == 1
    assert "unknown setting" in text
