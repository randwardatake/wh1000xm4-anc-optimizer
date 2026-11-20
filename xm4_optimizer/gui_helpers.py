"""Headless-safe helpers for the Tkinter desktop app.

Everything in this module can be imported and tested without a display: it holds
the small glue functions that turn CLI/library return values into text for the
log panel, plus payload builders for the NVDM item panel. All protocol work is
delegated to the existing public APIs in :mod:`xm4_optimizer.cli`,
:mod:`xm4_optimizer.nvdm` and ``tools/``; nothing here reimplements a wire
format.

The GUI base and the payload-preview idea come from abdulhajiyev (PR #4);
see CREDITS.md.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys
from typing import Callable, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from xm4_optimizer import cli
from xm4_optimizer.dsp.synthesizer import synthesize_optimizer_coeff
from xm4_optimizer.nvdm.cxd90050 import OPTIMIZER_COEFF_SIZE, OptimizerCoefficient
from xm4_optimizer.nvdm.parser import parse_nvdm_image
from xm4_optimizer.transport.sony_control import AmbientSoundState, AncMode

MIN_FLASH_BATTERY = 50

MDR_SETTING_KEYS = list(cli.SETTING_KEYS) + [
    "eq-preset", "eq-bands", "voice-guidance"
]

ANC_CHOICES = [
    (AncMode.NOISE_CANCELLING, "Noise cancelling"),
    (AncMode.AMBIENT, "Ambient sound"),
    (AncMode.WIND, "Wind noise reduction"),
    (AncMode.OFF, "Off"),
]


def describe_anc(state: AmbientSoundState) -> str:
    """Human-readable ANC state, shared with the status panel."""
    text = dict(ANC_CHOICES)[state.mode]
    if state.mode is AncMode.AMBIENT:
        text += f" (level {state.ambient_level}, voice {'on' if state.focus_on_voice else 'off'})"
    return text


def battery_ok_for_flash(level: Optional[int]) -> bool:
    """Firmware installs are blocked below this battery level."""
    return level is not None and level >= MIN_FLASH_BATTERY


def capture_stdout(fn: Callable[..., int], *args: object, **kwargs: object) -> tuple[int, str]:
    """Runs an existing CLI workflow and returns ``(exit_code, printed_text)``."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*args, **kwargs)
    return int(rc), buf.getvalue()


def settings_command(
    mac: str, action: str, key: str, value: Optional[str] = None
) -> tuple[int, str]:
    """Read/write an MDR setting through the documented CLI workflow."""
    return capture_stdout(
        cli._run_settings_workflow, mac=mac, action=action, key=key, value=value
    )


def spatial_command(
    action: str, app_node: str = "spotify", azimuth: float = 60.0, gain: float = 1.0
) -> tuple[int, str]:
    """Status/start/stop of the experimental AeroSpatial pipeline."""
    return capture_stdout(
        cli._run_spatial_workflow,
        action=action,
        app_node=app_node,
        azimuth=azimuth,
        gain=gain,
    )


def dry_run_command(
    target_gain: float = 7.5,
    sample_rate: float = 48000.0,
    image_path: Optional[Path] = None,
    mac: Optional[str] = None,
) -> tuple[int, str]:
    """Zero-byte simulation using the read-only guard path.

    With ``image_path`` the simulation is fully offline. Without it, a real
    read-only RFCOMM transport is opened, used, and closed again.
    """
    if image_path is None:
        transport = cli._create_transport(mac=mac, mock=False)
        try:
            return capture_stdout(
                cli._run_dry_run_workflow,
                transport=transport,
                target_gain=target_gain,
                sample_rate=sample_rate,
                image_path=None,
            )
        finally:
            transport.close()
    return capture_stdout(
        cli._run_dry_run_workflow,
        transport=None,
        target_gain=target_gain,
        sample_rate=sample_rate,
        image_path=Path(image_path),
    )


def synthesize_words(target_gain: float, sample_rate: float = 48000.0) -> list[int]:
    """Bias words for the ANC coefficient injector, as a list of 32-bit ints."""
    coeff, _result = synthesize_optimizer_coeff(
        target_gain_db=target_gain, fs=sample_rate
    )
    return list(coeff.words)


def format_words(words: Sequence[int]) -> str:
    """Comma-separated 0x-prefixed words, ready for the injector's ``--words``."""
    return ", ".join(f"0x{w:08X}" for w in words)


def parse_words(text: str) -> list[int]:
    """Parse the injector's ten comma-separated words (hex or decimal)."""
    from tools.anc_coeff_inject import parse_words as _parse_words

    return _parse_words(text)


def format_replies(replies: Sequence[bytes]) -> str:
    """Format MDR 0xFF/6 replies for the log panel."""
    if not replies:
        return "  (no replies from the headset)"
    return "\n".join(f"  reply[{i}]: {reply.hex()}" for i, reply in enumerate(replies))


def parse_value(text: str, as_hex: bool) -> bytes:
    """Encode the NVDM item value entry as ASCII or raw hex bytes."""
    if as_hex:
        cleaned = text.strip().replace(" ", "")
        try:
            return bytes.fromhex(cleaned)
        except ValueError as exc:
            raise ValueError("Value is not valid hex (use pairs of hex digits)") from exc
    return text.encode("ascii")


def nvdm_payload_preview(group: str, item: str, value: bytes, write: bool) -> str:
    """Hex of the MDR 0xFF/6 frame, without sending anything to the headset."""
    from tools.mdr_nvdm_write import build_nvdm_read_payload, build_nvdm_write_payload

    payload = (
        build_nvdm_write_payload(group, item, value)
        if write
        else build_nvdm_read_payload(group, item)
    )
    return payload.hex()


def describe_nvdm_image(path: Path) -> str:
    """Short parse summary for a local NVDM backup file."""
    data = Path(path).read_bytes()
    partition = parse_nvdm_image(data, strict=False)
    lines = [
        f"File: {path}",
        f"Size: {len(data):,} bytes",
        f"PEBs: {len(partition.pebs)}",
        f"Active items: {len(partition.active_items)}",
        f"Corrupted items: {len(partition.corrupted_items)}",
    ]
    coeff = partition.get_value("config", "optimizer_coeff")
    if coeff is not None and len(coeff) == OPTIMIZER_COEFF_SIZE:
        words = OptimizerCoefficient.unpack(coeff).words
        label = "identity" if OptimizerCoefficient.unpack(coeff).is_identity else "custom"
        lines.append(f"config/optimizer_coeff ({label}): {format_words(words)}")
    return "\n".join(lines)
