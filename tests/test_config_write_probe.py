"""Tests for the NVDM config write probe tool."""

from __future__ import annotations

from tools.config_write_probe import candidate_commands, main, read_item_value
from xm4_optimizer.transport.virtual_device import VirtualXM4Device


class TestCandidates:
    def test_known_envdm_layout_included(self) -> None:
        commands = candidate_commands("config", "test_vp_disable", 1)
        assert "AT+ENVDM=W,config,test_vp_disable,1" in commands
        assert all(c.startswith("AT+ENVDM=W,") for c in commands)


class TestReadback:
    def test_reads_seeded_item(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        value = read_item_value(bytes(dev.flash), "config", "optimizer_coeff")
        assert value is not None
        assert len(value) == 40


class TestCli:
    def test_dry_run_sends_nothing(self, capsys) -> None:
        rc = main(["--item", "test_vp_disable", "--value", "1"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Dry run" in out
        assert "AT+ENVDM=W,config,test_vp_disable,1" in out
