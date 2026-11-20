"""End-to-End CLI Integration Tests (`xm4_optimizer.cli`)."""

from __future__ import annotations

from pathlib import Path
import pytest

from xm4_optimizer.cli import CliRunner, cli
from xm4_optimizer.transport.virtual_device import VirtualXM4Device


class TestCliEndToEnd:
    """Tests all subcommands and flag modes of the `xm4-optimizer` CLI."""

    def test_cli_dry_run_subcommand_and_flag_zero_writes(self) -> None:
        runner = CliRunner()
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()
        pristine = bytes(dev.flash)

        # 1. Subcommand style: `xm4-optimizer dry-run --target-gain 7.5`
        res1 = runner.invoke(
            cli,
            ["--mock", "dry-run", "--target-gain", "7.5"],
            obj={"transport": dev},
        )
        assert res1.exit_code == 0, res1.output
        assert "config/optimizer_coeff" in res1.output
        assert "0 bytes (DRY-RUN BARRIER ACTIVE)" in res1.output
        assert dev.write_count == 0
        assert bytes(dev.flash) == pristine

        # 2. Flag style: `xm4-optimizer --dry-run --target-gain 8.0`
        res2 = runner.invoke(
            cli,
            ["--mock", "--dry-run", "--target-gain", "8.0"],
            obj={"transport": dev},
        )
        assert res2.exit_code == 0, res2.output
        assert "config/optimizer_coeff" in res2.output
        assert dev.write_count == 0
        assert bytes(dev.flash) == pristine

    def test_cli_full_lifecycle_backup_parse_inject_verify_rollback(
        self, tmp_path: Path
    ) -> None:
        runner = CliRunner()
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()
        backup_path = tmp_path / "factory_nvdm.bin"

        # Step 1: Backup
        res_backup = runner.invoke(
            cli,
            ["--mock", "backup", str(backup_path)],
            obj={"transport": dev},
        )
        assert res_backup.exit_code == 0, res_backup.output
        assert backup_path.exists()
        assert len(backup_path.read_bytes()) == 524288  # 512 KB

        # Step 2: Parse
        res_parse = runner.invoke(
            cli,
            ["--mock", "parse", str(backup_path)],
            obj={"transport": dev},
        )
        assert res_parse.exit_code == 0, res_parse.output
        assert "DNC/ALIGN_FF_L" in res_parse.output
        assert "HOWLING_PRE_PARAM" in res_parse.output
        assert "config/optimizer_coeff" in res_parse.output

        # Step 3: Optimize preview (dry-run)
        res_opt = runner.invoke(
            cli,
            ["--mock", "optimize", "--target-gain", "7.5"],
            obj={"transport": dev},
        )
        assert res_opt.exit_code == 0, res_opt.output
        assert dev.write_count == 0

        # Step 4: Inject via NCOPTSET
        res_inj = runner.invoke(
            cli,
            ["--mock", "inject", "--target-gain", "7.5"],
            obj={"transport": dev},
        )
        assert res_inj.exit_code == 0, res_inj.output
        assert "INJECTION SUCCEEDED" in res_inj.output
        assert "NCOPTSET" in res_inj.output
        assert dev.ncopt_data is not None

        # Step 5: Verify (NCOPTGET state check)
        res_ver = runner.invoke(
            cli,
            ["--mock", "verify"],
            obj={"transport": dev},
        )
        assert res_ver.exit_code == 0, res_ver.output
        assert "coefficient data" in res_ver.output

        # Step 6: Rollback via NCOPTSET identity
        res_rb = runner.invoke(
            cli,
            ["--mock", "rollback", str(backup_path)],
            obj={"transport": dev},
        )
        assert res_rb.exit_code == 0, res_rb.output
        assert "ROLLBACK SUCCEEDED" in res_rb.output

    def test_cli_flag_workflow_inject_and_rollback(self, tmp_path: Path) -> None:
        runner = CliRunner()
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()
        backup_path = tmp_path / "flag_backup.bin"

        # Backup via flag
        r1 = runner.invoke(
            cli, ["--mock", "--backup", str(backup_path)], obj={"transport": dev}
        )
        assert r1.exit_code == 0, r1.output

        # Inject via flag
        r2 = runner.invoke(
            cli,
            ["--mock", "--inject", "--target-gain", "6.5"],
            obj={"transport": dev},
        )
        assert r2.exit_code == 0, r2.output
        assert dev.ncopt_data is not None

        # Verify via flag
        r3 = runner.invoke(
            cli,
            ["--mock", "--verify"],
            obj={"transport": dev},
        )
        assert r3.exit_code == 0, r3.output

        # Rollback via flag
        r4 = runner.invoke(
            cli,
            ["--mock", "--rollback", str(backup_path)],
            obj={"transport": dev},
        )
        assert r4.exit_code == 0, r4.output

    def test_cli_rejects_out_of_bounds_gain(self) -> None:
        runner = CliRunner()
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()

        res = runner.invoke(
            cli,
            ["--mock", "inject", "--target-gain", "14.0"],
            obj={"transport": dev},
        )
        assert res.exit_code != 0
        assert "ERROR" in res.output
        assert dev.ncopt_data is None

    def test_cli_status_subcommand(self) -> None:
        runner = CliRunner()
        res = runner.invoke(cli, ["status"])
        assert res.exit_code == 0
        assert "Sony WH-1000XM4" in res.output
        assert "Bluetooth Link" in res.output

    def test_cli_flash_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runner = CliRunner()
        # Missing argument
        res1 = runner.invoke(cli, ["flash"])
        assert res1.exit_code == 1
        assert "ERROR: flash requires path" in res1.output

        # Simulated flash failure
        def mock_flash_fail(*args, **kwargs):
            return False
        monkeypatch.setattr("xm4_optimizer.cli.flash_firmware", mock_flash_fail)
        res2 = runner.invoke(cli, ["--mac", "00:11:22:33:44:55", "flash", "fw.bin"])
        assert res2.exit_code == 1

    def test_cli_anc_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runner = CliRunner()
        # Simulated RFCOMM connection failure
        def mock_fail_socket(*args, **kwargs):
            raise ConnectionRefusedError("Simulated connection error")
        monkeypatch.setattr("xm4_optimizer.cli.SonyControlClient.connect", mock_fail_socket)
        res = runner.invoke(cli, ["--mac", "00:11:22:33:44:55", "anc", "status"])
        assert res.exit_code == 1
        assert "Simulated connection error" in res.output

        res = runner.invoke(cli, ["--mac", "00:11:22:33:44:55", "anc", "turbo"])
        assert res.exit_code == 1
        assert "unknown anc mode" in res.output

    def test_cli_settings_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_eq(self):
                return bytes.fromhex("570100060a0a0a0a0a0a")

            def get_general_setting(self, _enum):
                return bytes.fromhex("d7d10101")

            def get_audio_setting(self, _enum):
                return bytes.fromhex("e7020001")

            def get_system_setting(self, _enum):
                return bytes.fromhex("f7050100")

        monkeypatch.setattr("xm4_optimizer.cli.SonyControlClient.connect",
                            lambda _mac: FakeClient())
        runner = CliRunner()
        res = runner.invoke(cli, ["--mac", "00:11:22:33:44:55", "settings", "get", "all"])
        assert res.exit_code == 0, res.output
        assert "touch-panel" in res.output
        assert "speak-to-chat" in res.output

    def test_cli_parse_file_directly(self, tmp_path: Path) -> None:
        runner = CliRunner()
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()
        img_path = tmp_path / "sample_nvdm.bin"
        img_path.write_bytes(bytes(dev.flash))

        res = runner.invoke(cli, ["parse", str(img_path)])
        assert res.exit_code == 0
        assert "Parsed NVDM Calibration Items" in res.output
        assert "ALIGN_FF_L" in res.output
        assert "config/optimizer_coeff" in res.output

    def test_cli_help_displays_unified_commands(self) -> None:
        runner = CliRunner()
        res = runner.invoke(cli, ["--help"])
        assert res.exit_code == 0
        for cmd in ("backup", "flash", "anc", "status", "parse", "optimize", "dry-run", "inject", "verify", "rollback"):
            assert cmd in res.output


class TestCliHardwareWriteGate:
    """inject/rollback must refuse to touch real hardware without an explicit override."""

    @pytest.mark.parametrize(
        "argv",
        [
            ["inject", "--target-gain", "7.5"],
            ["--inject", "--target-gain", "7.5"],
            ["rollback", "backup.bin"],
            ["--rollback", "backup.bin"],
        ],
    )
    def test_write_commands_blocked_without_mock(self, argv) -> None:
        res = CliRunner().invoke(cli, argv)
        assert res.exit_code == 2, res.output
        assert "disabled" in res.output or "not yet verified" in res.output
