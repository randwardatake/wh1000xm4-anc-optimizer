from pathlib import Path

import pytest

from xm4_optimizer import eqapo

PRESET = "Sony_WF-1000XM4_ANC_on_SuperReview_EQ.txt"


@pytest.fixture
def dirs(tmp_path: Path):
    presets = tmp_path / "presets"
    config = tmp_path / "config"
    presets.mkdir()
    config.mkdir()
    (presets / PRESET).write_text("Preamp: -6.4 dB\n")
    (presets / "X ParametricEq_EASYEFFECTS.txt").write_text("")
    return presets, config


def test_list_presets_skips_easyeffects(dirs):
    presets, _ = dirs
    assert eqapo.list_presets(presets) == [PRESET]


def test_parse_default_and_managed_configs():
    default = "Preamp: -6 dB\r\nInclude: example.txt\r\nGraphicEQ: 25 0; 40 0"
    assert eqapo.parse_config(default) == eqapo.EqState("example.txt", None, True)
    on = eqapo.build_config(PRESET, "WH-1000XM4", True)
    off = eqapo.build_config(PRESET, "WH-1000XM4", False)
    assert eqapo.parse_config(on) == eqapo.EqState(PRESET, "WH-1000XM4", True)
    assert eqapo.parse_config(off) == eqapo.EqState(PRESET, "WH-1000XM4", False)


def test_apply_backs_up_original_once_and_toggles(dirs):
    presets, config = dirs
    (config / "config.txt").write_text("Include: example.txt\n")

    eqapo.apply(PRESET, "WH-1000XM4", True, config_dir=config, presets_dir=presets)
    assert (config / PRESET).read_text() == "Preamp: -6.4 dB\n"
    assert (config / eqapo.ORIGINAL_BACKUP_NAME).read_text() == "Include: example.txt\n"
    assert eqapo.read_state(config) == eqapo.EqState(PRESET, "WH-1000XM4", True)
    assert b"\r\n" in (config / "config.txt").read_bytes()

    eqapo.apply(PRESET, "Voicemeeter Input", False, config_dir=config, presets_dir=presets)
    assert eqapo.read_state(config) == eqapo.EqState(PRESET, "Voicemeeter Input", False)
    assert (config / eqapo.ORIGINAL_BACKUP_NAME).read_text() == "Include: example.txt\n"


def test_missing_install_and_preset(dirs, tmp_path):
    presets, config = dirs
    assert eqapo.read_state(tmp_path / "nope") is None
    with pytest.raises(FileNotFoundError):
        eqapo.apply(PRESET, "WH-1000XM4", True, config_dir=tmp_path / "nope", presets_dir=presets)
    with pytest.raises(FileNotFoundError):
        eqapo.apply("missing.txt", "WH-1000XM4", True, config_dir=config, presets_dir=presets)
