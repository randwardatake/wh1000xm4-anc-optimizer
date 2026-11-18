"""Locate or download the official WH-1000XM4 firmware images listed in `firmware.py`.

Files come from the community archive lzghzr/MDR_Proxy, which mirrors Sony's update
server (see README). Every file is identified by SHA-256 before it is returned, so a
wrong or corrupted download is never handed to the flasher.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.request import Request, urlopen

from xm4_optimizer.firmware import KNOWN_FIRMWARE, identify_firmware

from xm4_optimizer.paths import FIRMWARE_DIR

ARCHIVE_BASE = "https://raw.githubusercontent.com/lzghzr/MDR_Proxy/master/firmware/2943/MDRID294300/"
ARCHIVE_FILES = {
    "2.5.0": "UPG_2.5.0_20210413_enc.97529f0f6630238e77ce76bfc6766aa5616e78e7.bin",
    "2.6.0": "UPG_2.6.0_enc.b05cfdecc230c3dda652591c5c9f9ab0785dbb37.bin",
    "2.7.1": "UPG_2.7.1_enc.d30f546e4a57111263afc2a013c58dd783ee7d21.bin",
    "2.9.0": "UPG_2.9.0_enc.e4decc6b3015d1450c3fb56ab7b5d96780d184b5.bin",
    "3.0.1": "UPG_3.0.1_enc.51b88a0dd729e7bc6da4d7e9c8af1c98e975ba1e.bin",
}


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def known_versions() -> list[str]:
    """Known official versions, newest first."""
    return sorted({fw.version for fw in KNOWN_FIRMWARE.values()}, key=version_key, reverse=True)


def identify_path(path: Path) -> Optional[str]:
    """Version of an official image at `path`, or None."""
    known = identify_firmware(Path(path).read_bytes())
    return known.version if known else None


def find_local(version: str, search_dirs: Iterable[Path] = (FIRMWARE_DIR,)) -> Optional[Path]:
    """An already downloaded, hash-verified image for `version`, if any."""
    sizes = {fw.size for fw in KNOWN_FIRMWARE.values() if fw.version == version}
    for d in search_dirs:
        if not Path(d).is_dir():
            continue
        for p in sorted(Path(d).glob("*.bin")):
            if p.stat().st_size in sizes and identify_path(p) == version:
                return p
    return None


def download(version: str, dest_dir: Path = FIRMWARE_DIR,
             progress: Optional[Callable[[int, int], None]] = None,
             opener: Callable = urlopen) -> Path:
    """Downloads `version` from the archive, verifies its SHA-256 and returns the path."""
    if version not in ARCHIVE_FILES:
        raise ValueError(f"No archive file known for firmware {version}")
    name = ARCHIVE_FILES[version]
    req = Request(ARCHIVE_BASE + name, headers={"User-Agent": "xm4-optimizer"})
    chunks = []
    with opener(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
            got += len(chunk)
            if progress:
                progress(got, total)
    data = b"".join(chunks)
    known = identify_firmware(data)
    if known is None or known.version != version:
        raise ValueError(f"Downloaded file for {version} failed the SHA-256 check; it was not saved.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / name
    tmp = out.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(out)
    return out
