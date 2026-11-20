"""Firmware power-cycle and optimizer-completion emulation tests.

Models the two distinct write paths found in v2.5.0 section 4:
- FUN_082bb5d8: NC Optimizer generation end -> CXD90050 + NVDM write.
- FUN_08240bf4 -> FUN_08255878(1) -> FUN_082bb4b8: power-off batch save from
  firmware state, which NCOPTSET does not update.
- FUN_082bb468: power-on restore from NVDM.

See docs/STATUS.md for the evidence and docs/SPATIAL_ROADMAP.md for scope.
"""

from __future__ import annotations

from pathlib import Path

from xm4_optimizer.dsp.synthesizer import synthesize_optimizer_coeff
from xm4_optimizer.nvdm.cxd90050 import (
    OptimizerCoefficient,
    OPTIMIZER_COEFF_IDENTITY,
)
from xm4_optimizer.nvdm.parser import parse_nvdm_image
from xm4_optimizer.safety.injector import execute_injection
from xm4_optimizer.safety.rollback import create_nvdm_backup, execute_rollback
from xm4_optimizer.transport.virtual_device import VirtualXM4Device


def _nvdm_words(dev: VirtualXM4Device) -> list[int]:
    partition = parse_nvdm_image(bytes(dev.flash), strict=False)
    item = partition.get_item("config", "optimizer_coeff")
    assert item is not None
    return OptimizerCoefficient.unpack(item.value).words


class TestNcoptsetDoesNotPersist:
    """NCOPTSET writes DSP state, not the buffer used by the power-off save."""

    def test_ncoptset_markers_are_lost_on_power_cycle(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()

        marker_words = [0xDEADBE00 + i for i in range(10)]
        for i, word in enumerate(marker_words):
            dev.send_atci_command(f"AT+NCOPTTOOL=NCOPTSET,{i},0x{word:08X}")

        assert dev.ncopt_data == marker_words
        assert _nvdm_words(dev) == list(OPTIMIZER_COEFF_IDENTITY)

        dev.power_cycle()

        assert dev.ncopt_data == list(OPTIMIZER_COEFF_IDENTITY)
        assert _nvdm_words(dev) == list(OPTIMIZER_COEFF_IDENTITY)

    def test_ncoptset_ram_buffer_also_does_not_persist(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()
        assert dev._ncoptset_updates_ram is True

        marker_words = [0xCAFE0000 + i for i in range(10)]
        for i, word in enumerate(marker_words):
            dev.send_atci_command(f"AT+NCOPTTOOL=NCOPTSET,{i},0x{word:08X}")

        assert dev.optimizer_ram_buffer == marker_words
        dev.power_cycle()
        assert _nvdm_words(dev) == list(OPTIMIZER_COEFF_IDENTITY)

    def test_inject_pipeline_alone_is_not_persistent(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()

        coeff, _ = synthesize_optimizer_coeff(target_gain_db=7.5, fs=48000.0)
        result = execute_injection(dev, coeff)
        assert result.success is True
        assert dev.ncopt_data == coeff.words

        dev.power_cycle()
        assert _nvdm_words(dev) == list(OPTIMIZER_COEFF_IDENTITY)


class TestOptimizerCompletionPersists:
    """The optimizer's own result is written to NVDM at generation end."""

    def test_optimizer_result_is_written_immediately(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()

        result = OptimizerCoefficient(words=[0xDEAD0000 + i for i in range(10)])
        dev.run_nc_optimizer(result)

        assert dev.ncopt_data == result.words
        assert _nvdm_words(dev) == result.words
        assert dev.optimizer_is_optimized is False

    def test_optimizer_result_survives_power_cycle(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()

        result = OptimizerCoefficient(words=[0x12340000 + i for i in range(10)])
        dev.run_nc_optimizer(result)
        dev.power_cycle()

        assert dev.ncopt_data == result.words
        assert dev.optimizer_ram_buffer == result.words
        assert dev.power_off_save_buffer == result.words
        assert _nvdm_words(dev) == result.words
        assert dev.is_connected is True

    def test_optimizer_result_overwrites_ncoptset_values(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()

        for i in range(10):
            dev.send_atci_command(f"AT+NCOPTTOOL=NCOPTSET,{i},0x{0xDEAD0000 + i:08X}")
        assert dev.ncopt_data[0] == 0xDEAD0000

        result = OptimizerCoefficient(words=[0x10000000 if i % 5 == 0 else i for i in range(10)])
        dev.run_nc_optimizer(result)

        assert dev.ncopt_data == result.words
        assert dev.optimizer_ram_buffer == result.words
        assert _nvdm_words(dev) == result.words

        dev.power_cycle()
        assert _nvdm_words(dev) == result.words


class TestFullLifecycle:
    """Optimizer run, power cycle, rollback and another power cycle."""

    def test_full_lifecycle(self, tmp_path: Path) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()

        backup_path = tmp_path / "backup.bin"
        create_nvdm_backup(dev, backup_path)

        custom, _ = synthesize_optimizer_coeff(target_gain_db=8.0, fs=48000.0)
        dev.run_nc_optimizer(custom)
        assert _nvdm_words(dev) == custom.words

        dev.power_cycle()
        assert dev.ncopt_data == custom.words
        assert _nvdm_words(dev) == custom.words

        execute_rollback(dev, backup_path, verify=True)
        dev.run_nc_optimizer(OptimizerCoefficient())
        dev.power_cycle()

        assert dev.ncopt_data == list(OPTIMIZER_COEFF_IDENTITY)
        assert _nvdm_words(dev) == list(OPTIMIZER_COEFF_IDENTITY)


class TestMultipleRounds:
    """Each optimizer run produces a later NVDM record; the last one wins."""

    def test_successive_optimizer_runs_last_value_wins(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()

        coeff1, _ = synthesize_optimizer_coeff(target_gain_db=6.5, fs=48000.0)
        coeff2, _ = synthesize_optimizer_coeff(target_gain_db=8.5, fs=48000.0)

        dev.run_nc_optimizer(coeff1)
        dev.power_cycle()
        dev.run_nc_optimizer(coeff2)
        dev.power_cycle()

        assert dev.ncopt_data == coeff2.words
        assert _nvdm_words(dev) == coeff2.words

    def test_three_cycles_sequence_numbers_increase(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()

        for gain in (6.0, 7.0, 8.0):
            coeff, _ = synthesize_optimizer_coeff(target_gain_db=gain, fs=48000.0)
            dev.run_nc_optimizer(coeff)
            dev.power_cycle()

        partition = parse_nvdm_image(bytes(dev.flash), strict=False)
        item = partition.get_item("config", "optimizer_coeff")
        assert item is not None
        assert item.header.sequence_number > 9  # seed uses seq 1-8


class TestByteOrder:
    """Big-endian Q4.28 round trip through the optimizer write path."""

    def test_be_q428_round_trip_integrity(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()

        test_words = [
            0x10000000, 0xE3B571CF, 0x1C4A8E31, 0xF0000000, 0x0FFFFFFF,
            0x10000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000,
        ]
        dev.run_nc_optimizer(OptimizerCoefficient(words=test_words))
        dev.power_cycle()

        assert _nvdm_words(dev) == test_words
        assert OptimizerCoefficient(words=test_words).pack()[:4] == b"\x10\x00\x00\x00"
