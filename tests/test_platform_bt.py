"""Platform-specific Bluetooth helpers (parsing only; no hardware needed).

NOTE: originally contributed by abdulhajiyev (Windows support, PR #4).
"""

from xm4_optimizer.transport import rfcomm_socket as rs


def test_parse_bluetoothctl_devices():
    out = "Device AC:80:0A:2E:45:80 WH-1000XM4\nDevice 11:22:33:44:55:66 Mouse\n"
    devs = rs.parse_bluetoothctl_devices(out)
    assert devs == [("AC:80:0A:2E:45:80", "WH-1000XM4"), ("11:22:33:44:55:66", "Mouse")]
    assert rs._pick_xm4(devs) == "AC:80:0A:2E:45:80"


def test_pick_single_unknown_device_fallback():
    assert rs._pick_xm4([("11:22:33:44:55:66", "Thing")]) == "11:22:33:44:55:66"
    assert rs._pick_xm4([("11:22:33:44:55:66", "A"), ("11:22:33:44:55:67", "B")]) is None


def test_parse_windows_pnp_devices():
    out = (
        "BTHENUM\\DEV_AC800A2E4580\\7&1A2B&0&BLUETOOTHDEVICE_AC800A2E4580|WH-1000XM4|True\n"
        "BTHENUM\\DEV_112233445566\\7&1A2B&0&BLUETOOTHDEVICE_112233445566|Mouse|False\n"
        "garbage line\n"
    )
    devs = rs.parse_windows_pnp_devices(out)
    assert devs == [
        ("AC:80:0A:2E:45:80", "WH-1000XM4", True),
        ("11:22:33:44:55:66", "Mouse", False),
    ]


def test_windows_detection_uses_powershell(monkeypatch):
    monkeypatch.setattr(rs.sys, "platform", "win32")
    monkeypatch.setattr(
        rs, "_list_windows_devices",
        lambda: [("AC:80:0A:2E:45:80", "WH-1000XM4", True), ("11:22:33:44:55:66", "Mouse", False)],
    )
    assert rs.find_connected_xm4_mac() == "AC:80:0A:2E:45:80"
    assert rs.is_link_connected("ac:80:0a:2e:45:80") is True
    assert rs.is_link_connected("11:22:33:44:55:66") is False
    assert rs.is_link_connected("99:99:99:99:99:99") is False


def test_linux_detection_uses_bluetoothctl(monkeypatch):
    import subprocess
    monkeypatch.setattr(rs.sys, "platform", "linux")
    monkeypatch.setattr(
        subprocess, "check_output",
        lambda *args, **kwargs: "Device AC:80:0A:2E:45:80 WH-1000XM4\n",
    )
    assert rs.find_connected_xm4_mac() == "AC:80:0A:2E:45:80"
    assert rs.is_link_connected("AC:80:0A:2E:45:80") is True
    assert rs.is_link_connected("99:99:99:99:99:99") is False


def test_link_state_query_failure_is_unknown(monkeypatch):
    monkeypatch.setattr(rs.sys, "platform", "win32")

    def fail():
        raise OSError("PowerShell unavailable")

    monkeypatch.setattr(rs, "_list_windows_devices", fail)
    assert rs.is_link_connected("AC:80:0A:2E:45:80") is None
    assert rs.find_connected_xm4_mac() is None


def test_sdp_unavailable_on_windows(monkeypatch):
    from xm4_optimizer.transport import sdp
    monkeypatch.setattr(sdp.sys, "platform", "win32")
    assert sdp.query_rfcomm_channel("AC:80:0A:2E:45:80", "96cc203e-5068-46ad-b32d-e316f5e069ba") is None


def test_control_channel_fallback_without_sdp(monkeypatch):
    import socket as socket_mod
    from xm4_optimizer.transport import sony_control
    monkeypatch.setattr(sony_control.sys, "platform", "win32")
    monkeypatch.setattr(sony_control, "query_rfcomm_channel", lambda *args: None)

    used_channels = []

    class FakeSocket:
        def settimeout(self, _timeout):
            pass

        def sendall(self, _data):
            pass

        def recv(self, _n):
            raise socket_mod.timeout()

        def close(self):
            pass

    def fake_connect(bd_addr, channel, timeout_s):
        used_channels.append(channel)
        return FakeSocket()

    monkeypatch.setattr(sony_control, "create_rfcomm_socket", fake_connect)
    client = sony_control.SonyControlClient.connect("AC:80:0A:2E:45:80")
    try:
        assert used_channels == [sony_control.DEFAULT_CONTROL_CHANNEL] == [9]
    finally:
        client.close()
