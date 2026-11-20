"""Builds dist/XM4 Control.exe: one file, no Python install needed on the target PC.

Usage (from the repository root, inside the venv):
    pip install -e ".[build]"
    python packaging/build_windows_exe.py

Build with the python.org Windows Python: its socket module has AF_BLUETOOTH,
which the executable needs to talk to the headset.
"""

from __future__ import annotations

from pathlib import Path
import socket
import sys

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    if sys.platform != "win32":
        sys.exit("Build the Windows executable on Windows.")
    if not hasattr(socket, "AF_BLUETOOTH"):
        sys.exit("This Python has no Bluetooth sockets; use the python.org Windows installer.")
    PyInstaller.__main__.run([
        str(ROOT / "packaging" / "xm4_control.py"),
        "--name", "XM4 Control",
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--paths", str(ROOT),
        # tools/ is not an installed package; the GUI imports these lazily.
        "--hidden-import", "tools.backup_nvdm",
        "--hidden-import", "tools.flash_firmware",
        "--hidden-import", "tools.mdr_nvdm_write",
        "--hidden-import", "tools.anc_coeff_inject",
        "--hidden-import", "tools.config_write_probe",
        "--hidden-import", "tools.optimizer_trigger",
        "--add-data", f"{ROOT / 'presets'};presets",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
    ])
    print(f"\nBuilt: {ROOT / 'dist' / 'XM4 Control.exe'}")


if __name__ == "__main__":
    main()
