"""Tests for the macOS IOBluetooth backend, using a fake helper in place of the Swift binary."""

import sys

import pytest

from xm4_optimizer.transport import macos_bridge, rfcomm_socket, sdp
from xm4_optimizer.transport.macos_bridge import MacBridgeError

FAKE_BRIDGE = """\
import os, sys
cmd = sys.argv[1]
if cmd == "list":
    print("AC:80:0A:2E:45:80\\tWH-1000XM4")
    print("00:1A:7D:DA:71:13\\tMX Master 3S")
elif cmd == "sdp":
    if sys.argv[3].startswith("96cc203e"):
        print(9)
    else:
        sys.exit(3)
elif cmd == "connect":
    if sys.argv[3] == "99":
        print("ERR openRFCOMMChannelSync returned 0xE00002BC", flush=True)
        sys.exit(1)
    if sys.argv[3] == "98":
        sys.exit(1)
    fd = int(sys.argv[4])
    print("OK 667", flush=True)
    # Echo with every byte incremented, until the Python side closes.
    while True:
        data = os.read(fd, 4096)
        if not data:
            break
        os.write(fd, bytes((b + 1) % 256 for b in data))
"""


@pytest.fixture
def fake_bridge(tmp_path, monkeypatch):
    script = tmp_path / "rfcomm_bridge"
    script.write_text(f"#!{sys.executable}\n" + FAKE_BRIDGE)
    script.chmod(0o755)
    monkeypatch.setenv("XM4_BRIDGE_BIN", str(script))
    return script


def test_list_connected_devices(fake_bridge):
    assert macos_bridge.list_connected_devices() == [
        ("AC:80:0A:2E:45:80", "WH-1000XM4"),
        ("00:1A:7D:DA:71:13", "MX Master 3S"),
    ]


def test_find_connected_xm4_mac_on_darwin(fake_bridge, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert rfcomm_socket.find_connected_xm4_mac() == "AC:80:0A:2E:45:80"
    assert rfcomm_socket.is_device_connected("ac:80:0a:2e:45:80")
    assert not rfcomm_socket.is_device_connected("00:11:22:33:44:55")


def test_query_rfcomm_channel(fake_bridge, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert sdp.query_rfcomm_channel("AC:80:0A:2E:45:80", "96cc203e-5068-46ad-b32d-e316f5e069ba") == 9
    assert sdp.query_rfcomm_channel("AC:80:0A:2E:45:80", "00000000-0000-0000-0000-000000000000") is None


def test_create_rfcomm_socket_relays_bytes(fake_bridge, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    sock = rfcomm_socket.create_rfcomm_socket("AC:80:0A:2E:45:80", 21, timeout_s=2.0)
    try:
        assert sock.gettimeout() == 2.0
        sock.sendall(b"\x00\x01\xfe")
        assert sock.recv(16) == b"\x01\x02\xff"
    finally:
        sock.close()


def test_rfcomm_device_transport_over_bridge(fake_bridge, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    transport = rfcomm_socket.RfcommDeviceTransport("AC:80:0A:2E:45:80", timeout_s=2.0)
    transport.connect()
    try:
        transport._socket.send_all(b"abc")
        assert transport._socket.recv_exact(3) == b"bcd"
    finally:
        transport.close()


def test_connect_error_is_reported(fake_bridge):
    with pytest.raises(MacBridgeError, match="0xE00002BC"):
        macos_bridge.open_rfcomm_socket("AC:80:0A:2E:45:80", 99, timeout_s=2.0)


def test_helper_exit_without_status(fake_bridge):
    with pytest.raises(MacBridgeError, match="no response from helper"):
        macos_bridge.open_rfcomm_socket("AC:80:0A:2E:45:80", 98, timeout_s=2.0)


def test_missing_swiftc(tmp_path, monkeypatch):
    monkeypatch.delenv("XM4_BRIDGE_BIN", raising=False)
    monkeypatch.setattr(macos_bridge, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(macos_bridge.shutil, "which", lambda _name: None)
    with pytest.raises(MacBridgeError, match="xcode-select --install"):
        macos_bridge.bridge_path()


@pytest.mark.skipif(sys.platform != "darwin", reason="needs swiftc and IOBluetooth")
def test_swift_helper_builds(tmp_path, monkeypatch):
    monkeypatch.delenv("XM4_BRIDGE_BIN", raising=False)
    monkeypatch.setattr(macos_bridge, "CACHE_DIR", tmp_path)
    if macos_bridge.shutil.which("swiftc") is None:
        pytest.skip("swiftc not installed")
    binary = macos_bridge.bridge_path()
    assert binary.exists() and binary.parent == tmp_path
    assert macos_bridge.bridge_path() == binary  # cached
