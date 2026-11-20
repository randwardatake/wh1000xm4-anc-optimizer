"""Tests for the MDR tandem-test NVDM write payload."""

from __future__ import annotations

import pytest

from tools.mdr_nvdm_write import build_nvdm_write_payload, main


class TestPayload:
    def test_known_example(self) -> None:
        payload = build_nvdm_write_payload("config", "test_vp_disable", b"1")
        assert payload.hex() == (
            "ff06000100000206"
            "636f6e666967"
            "0f746573745f76705f64697361626c65"
            "000131"
        )

    def test_lengths_are_big_endian(self) -> None:
        payload = build_nvdm_write_payload("config", "eq_manual", b"0:0:0:2:6:10")
        assert payload[-len(b"0:0:0:2:6:10") - 2:-len(b"0:0:0:2:6:10")] == b"\x00\x0c"

    def test_rejects_oversized_names(self) -> None:
        with pytest.raises(ValueError):
            build_nvdm_write_payload("x" * 256, "item", b"1")


class TestCli:
    def test_dry_run(self, capsys) -> None:
        assert main(["--item", "test_vp_disable", "--value", "1"]) == 0
        assert "ff06000100000206" in capsys.readouterr().out
