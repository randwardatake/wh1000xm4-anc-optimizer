"""Where the app keeps its files.

From a source checkout everything lives in the repository (backups/, firmware/,
presets/). In the standalone Windows executable (PyInstaller), bundled read-only
files come from the unpack directory and user files go to Documents\\XM4 Control.

NOTE: originally contributed by abdulhajiyev (Windows support, PR #4).
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

FROZEN = bool(getattr(sys, "frozen", False))
REPO_ROOT = Path(__file__).resolve().parent.parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", REPO_ROOT))


def _documents_dir() -> Path:
    """The user's Documents folder, following OneDrive or other redirection on Windows."""
    if sys.platform == "win32":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # CSIDL_PERSONAL
                return Path(buf.value)
        except (OSError, AttributeError):
            pass
    return Path(os.path.expanduser("~")) / "Documents"


DATA_DIR = _documents_dir() / "XM4 Control" if FROZEN else REPO_ROOT
BACKUP_DIR = DATA_DIR / "backups"
FIRMWARE_DIR = DATA_DIR / "firmware"
PRESETS_DIR = RESOURCE_DIR / "presets"
