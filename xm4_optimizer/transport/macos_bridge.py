"""macOS Bluetooth backend: RFCOMM sockets, SDP lookup and device listing via IOBluetooth.

macOS Python has no AF_BLUETOOTH, so RFCOMM goes through a small Swift helper
(`macos/rfcomm_bridge.swift`) that opens the channel with IOBluetooth and relays
bytes over one end of a Unix socketpair. Callers get the other end as an ordinary
`socket.socket`, so `sendall`, `recv` and `settimeout` behave as they do on Linux.

The helper is compiled with `swiftc` (Xcode Command Line Tools) on first use and
cached under ~/Library/Caches/xm4_optimizer, keyed by the hash of its source.
"""

from __future__ import annotations

import hashlib
import os
import select
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

SOURCE_DIR: Path = Path(__file__).resolve().parent / "macos"
SWIFT_SOURCE: Path = SOURCE_DIR / "rfcomm_bridge.swift"
INFO_PLIST: Path = SOURCE_DIR / "Info.plist"
CACHE_DIR: Path = Path(os.environ.get("XM4_BRIDGE_CACHE", Path.home() / "Library" / "Caches" / "xm4_optimizer"))
CONNECT_TIMEOUT_S: float = 15.0

_build_lock = threading.Lock()


class MacBridgeError(OSError):
    """Raised when the helper cannot be built or reports a failure."""


def bridge_path() -> Path:
    """Returns the compiled helper, building it first if the source changed.

    XM4_BRIDGE_BIN overrides the binary (used by tests and for prebuilt helpers).
    """
    override = os.environ.get("XM4_BRIDGE_BIN")
    if override:
        return Path(override)

    digest = hashlib.sha256(SWIFT_SOURCE.read_bytes() + INFO_PLIST.read_bytes()).hexdigest()[:16]
    binary = CACHE_DIR / f"rfcomm_bridge-{digest}"
    with _build_lock:
        if binary.exists():
            return binary
        swiftc = shutil.which("swiftc")
        if swiftc is None:
            raise MacBridgeError("swiftc not found; install the Xcode Command Line Tools: xcode-select --install")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = binary.with_suffix(".tmp")
        cmd = [
            swiftc, "-O", str(SWIFT_SOURCE), "-o", str(tmp),
            # Embed Info.plist so macOS can show the Bluetooth permission prompt.
            "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist", "-Xlinker", str(INFO_PLIST),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise MacBridgeError(f"Failed to build {SWIFT_SOURCE.name}:\n{result.stderr}")
        tmp.replace(binary)
    return binary


def list_connected_devices() -> List[Tuple[str, str]]:
    """Returns (MAC, name) for every paired device that is currently connected."""
    out = subprocess.check_output([str(bridge_path()), "list"], text=True, timeout=10)
    devices = []
    for line in out.splitlines():
        mac, _, name = line.partition("\t")
        if mac:
            devices.append((mac, name))
    return devices


def query_rfcomm_channel(bd_addr: str, service_uuid: str, timeout_s: float = 5.0) -> Optional[int]:
    """Returns the RFCOMM channel the device advertises for `service_uuid`, or None."""
    result = subprocess.run(
        [str(bridge_path()), "sdp", bd_addr, service_uuid],
        capture_output=True, text=True, timeout=timeout_s + 5,
    )
    if result.returncode == 3:
        return None
    if result.returncode != 0:
        raise MacBridgeError(f"SDP query failed: {result.stdout.strip() or result.stderr.strip()}")
    return int(result.stdout.strip())


def open_rfcomm_socket(bd_addr: str, channel: int, timeout_s: float) -> socket.socket:
    """Opens RFCOMM `channel` on `bd_addr` and returns a connected stream socket."""
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        proc = subprocess.Popen(
            [str(bridge_path()), "connect", bd_addr, str(channel), str(theirs.fileno())],
            pass_fds=(theirs.fileno(),),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
        )
    except Exception:
        ours.close()
        raise
    finally:
        theirs.close()

    status = _read_status_line(proc, max(timeout_s, CONNECT_TIMEOUT_S))
    if not status.startswith("OK"):
        ours.close()
        proc.kill()
        proc.wait()
        raise MacBridgeError(f"Failed to connect to {bd_addr} on RFCOMM ch {channel}: {status or 'no response from helper'}")

    # The helper exits when either side closes; reap it in the background.
    threading.Thread(target=proc.wait, daemon=True).start()
    ours.settimeout(timeout_s)
    return ours


def _read_status_line(proc: subprocess.Popen, timeout_s: float) -> str:
    """Reads the helper's first stdout line ("OK <mtu>" / "ERR <msg>") within `timeout_s`."""
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    deadline = time.monotonic() + timeout_s
    line = b""
    while not line.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "ERR timed out waiting for the RFCOMM channel to open"
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            continue
        chunk = os.read(fd, 1)
        if not chunk:
            break
        line += chunk
    return line.decode(errors="replace").strip()
