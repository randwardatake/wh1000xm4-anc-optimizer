"""Platform-specific Bluetooth helpers (parsing only; no hardware needed)."""

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
