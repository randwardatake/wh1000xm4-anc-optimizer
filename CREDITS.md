# Credits

The original project is maintained by **Ege (randward)**, who did the device-verified
protocol research and the backup, flasher, DSP and safety tooling.

Contributions incorporated into this repository:

- **abdulhajiyev** (Abdul Hajiyev) — Windows Bluetooth transport support, the original
  "XM4 Control" Tkinter desktop app and standalone-EXE packaging ([PR #4]). The GUI in
  `xm4_optimizer/gui.py` is adapted from that work, as are the `paths`, `eqapo`,
  `firmware_store` and `backup_check` helper modules and their tests. The firmware
  installer's known-hash gating, pre-flash backup and 50% battery rule come from this
  contribution.
- **lonelyversemaker** — macOS IOBluetooth RFCOMM bridge and control-channel fallback
  ([PR #3]).

Parts of the unified GUI, the headless helper module and the new tests were
**AI-assisted**; they were reviewed and exercised with the local test suite. Hardware
claims still follow the evidence ledger in [docs/STATUS.md](docs/STATUS.md).

[PR #3]: https://github.com/randwardatake/wh1000xm4-anc-optimizer/pull/3
[PR #4]: https://github.com/randwardatake/wh1000xm4-anc-optimizer/pull/4
