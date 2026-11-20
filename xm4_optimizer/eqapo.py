"""Equalizer APO (Windows) config handling for the presets in `presets/`.

Equalizer APO reads `config.txt` from its config directory and re-applies it on save.
This module writes a small managed config that limits one preset to one playback
device with a `Device:` line, so the EQ is not applied twice when audio passes through
a virtual mixer such as Voicemeeter on its way to the headphones.

Contributed by abdulhajiyev (PR #4); see CREDITS.md.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from typing import Optional

from xm4_optimizer.paths import PRESETS_DIR


def _find_config_dir() -> Path:
    """Equalizer APO's ConfigPath from the registry, else the default install location."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EqualizerAPO") as key:
            return Path(winreg.QueryValueEx(key, "ConfigPath")[0])
    except (ImportError, OSError):
        return Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "EqualizerAPO" / "config"


EQAPO_CONFIG_DIR = _find_config_dir()
MANAGED_MARKER = "# Managed by xm4_optimizer GUI"
ORIGINAL_BACKUP_NAME = "config.before-xm4-gui.txt"

# Label shown in the UI -> substring Equalizer APO matches against the device name.
EQ_TARGETS = {
    "WH-1000XM4 headphones": "WH-1000XM4",
    "Voicemeeter Input (MAIN)": "Voicemeeter Input",
}


@dataclass(frozen=True)
class EqState:
    preset: Optional[str]
    device: Optional[str]
    enabled: bool


def list_presets(presets_dir: Path = PRESETS_DIR) -> list[str]:
    """Equalizer APO compatible preset files (the EasyEffects ones are Linux only)."""
    if not presets_dir.is_dir():
        return []
    return sorted(p.name for p in presets_dir.glob("*.txt") if "EASYEFFECTS" not in p.name.upper())


def parse_config(text: str) -> EqState:
    """Reads the first Device:/Include: lines, commented out or not."""
    preset: Optional[str] = None
    device: Optional[str] = None
    enabled = False
    for raw in text.splitlines():
        line = raw.strip()
        commented = line.startswith("#")
        key, sep, value = line.lstrip("#").strip().partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        if key == "include" and preset is None:
            preset = value.strip()
            enabled = not commented
        elif key == "device" and device is None:
            device = value.strip()
    return EqState(preset=preset, device=device, enabled=enabled)


def build_config(preset: str, device: str, enabled: bool) -> str:
    prefix = "" if enabled else "# "
    return f"{MANAGED_MARKER}\n{prefix}Device: {device}\n{prefix}Include: {preset}\n"


def read_state(config_dir: Path = EQAPO_CONFIG_DIR) -> Optional[EqState]:
    """Current state, or None when Equalizer APO is not installed."""
    cfg = config_dir / "config.txt"
    if not config_dir.is_dir():
        return None
    if not cfg.exists():
        return EqState(None, None, False)
    return parse_config(cfg.read_text(encoding="utf-8", errors="replace"))


def apply(preset: str, device: str, enabled: bool,
          config_dir: Path = EQAPO_CONFIG_DIR, presets_dir: Path = PRESETS_DIR) -> EqState:
    """Copies the preset into the config dir and writes a managed config.txt.

    The first time an unmanaged config.txt is replaced, it is saved as
    config.before-xm4-gui.txt (never overwritten afterwards).
    """
    if not config_dir.is_dir():
        raise FileNotFoundError(f"Equalizer APO is not installed ({config_dir} not found)")
    source = presets_dir / preset
    if not source.is_file():
        raise FileNotFoundError(f"Preset not found: {source}")
    shutil.copyfile(source, config_dir / preset)

    cfg = config_dir / "config.txt"
    original = config_dir / ORIGINAL_BACKUP_NAME
    if cfg.exists() and MANAGED_MARKER not in cfg.read_text(encoding="utf-8", errors="replace") and not original.exists():
        shutil.copyfile(cfg, original)
    with open(cfg, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(build_config(preset, device, enabled))
    return EqState(preset=preset, device=device, enabled=enabled)
