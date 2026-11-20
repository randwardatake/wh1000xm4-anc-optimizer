"""NCOPTSET-based Coefficient Injector for Sony WH-1000XM4 ANC Optimization.

Sends optimizer coefficients to the CXD90050 DSP via AT+NCOPTTOOL=NCOPTSET through
the ATCI relay tunnel. Verified on device 2026-10-27. NCOPTSET changes DSP state but
is not the source of either NVDM write path, so values do not persist on power-off
(see docs/STATUS.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from xm4_optimizer.dsp.stability import compute_biquad_poles
from xm4_optimizer.nvdm.cxd90050 import (
    OptimizerCoefficient,
    OPTIMIZER_COEFF_WORDS,
)
from xm4_optimizer.transport.rfcomm_socket import DeviceTransport


class InjectionError(Exception):
    """Raised when coefficient injection fails."""


class UnstableFilterHoldError(InjectionError):
    """Raised when a candidate filter violates the Nyquist unit-circle stability gate."""


@dataclass
class InjectionResult:
    """Detailed report of an NCOPTSET injection operation."""

    success: bool
    words_sent: int = 0
    ncoptget_confirmed: bool = False
    modified_words: List[int] = field(default_factory=list)


def _enforce_stability_gate(coeff: OptimizerCoefficient) -> None:
    """Verifies that both biquads have poles strictly inside |z| < 1.0."""
    biquads = coeff.to_biquads()
    for bq_idx, bq in enumerate(biquads):
        coeffs = (bq.a1, bq.a2, bq.b0, bq.b1, bq.b2)
        if not all(np.isfinite(c) for c in coeffs):
            raise UnstableFilterHoldError(
                f"Biquad {bq_idx} contains non-finite coefficients: {coeffs}"
            )
        poles = compute_biquad_poles(bq.a1, bq.a2)
        if not bool(np.all(np.abs(poles) < 1.0)):
            max_p = float(np.max(np.abs(poles))) if poles.size > 0 else float("inf")
            raise UnstableFilterHoldError(
                f"UNSTABLE FILTER REJECTED at biquad {bq_idx}: max pole magnitude "
                f"|p| = {max_p:.6f} >= 1.0 (a1={bq.a1}, a2={bq.a2})"
            )


def execute_injection(
    transport: DeviceTransport,
    optimized_coeff: OptimizerCoefficient,
    verify: bool = True,
) -> InjectionResult:
    """Sends optimized coefficients to the DSP via NCOPTSET.

    Steps:
    1. Enforce Nyquist stability gate on both biquads.
    2. Send AT+NCOPTTOOL=NCOPTSET,<offset>,0x<value> for each of the 10 words.
    3. Optionally verify via NCOPTGET state change (null→data).

    No flash writes. NVDM persistence does not happen through this path.
    """
    _enforce_stability_gate(optimized_coeff)

    commands = optimized_coeff.to_ncoptset_commands()
    words_sent = 0
    modified = []

    for i, cmd in enumerate(commands):
        resp = transport.send_atci_command(cmd)
        if "ERROR" in resp.upper():
            raise InjectionError(
                f"NCOPTSET failed at offset {i}: {resp!r}"
            )
        words_sent += 1
        modified.append(i)

    ncoptget_ok = False
    if verify:
        resp = transport.send_atci_command("AT+NCOPTTOOL=NCOPTGET")
        ncoptget_ok = "DSP1_NCOPT_DATA=" in resp and resp.strip().endswith("OK")
        if "DSP1_NCOPT_DATA=\r" in resp or "DSP1_NCOPT_DATA=\n" in resp:
            ncoptget_ok = False

    return InjectionResult(
        success=True,
        words_sent=words_sent,
        ncoptget_confirmed=ncoptget_ok,
        modified_words=modified,
    )
