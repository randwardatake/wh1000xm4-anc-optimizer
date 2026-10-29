# wh1000xm4-anc-optimizer

[![CI](https://github.com/randwardatake/wh1000xm4-anc-optimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/randwardatake/wh1000xm4-anc-optimizer/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-v0.1.0-blue.svg)](pyproject.toml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![Email](https://img.shields.io/badge/email-randward%40proton.me-6d4aff.svg?logo=protonmail&logoColor=white)](mailto:randward@proton.me)

Open tools for the **Sony WH-1000XM4** over Bluetooth on Linux: back up and inspect the headset's NVDM settings store, flash official Sony firmware (including downgrades), switch ANC modes, read and write device RAM over RACE, apply EQ presets, and an experimental spatial-audio prototype. Includes firmware reverse-engineering notes for the Airoha AB1562 platform. The goal is to grow this into a toolkit for other Sony headphones built on the same chipset.

> **Unofficial project.** Not affiliated with, endorsed by or supported by Sony. Use at your own risk: writing to a headset can damage it and may void your warranty.

Every claim in this repository is graded by evidence in **[docs/STATUS.md](docs/STATUS.md)** (device-verified, user-reported, simulation only, unverified, contradicted). Read it before you write anything to a device.

## What works today

| Feature | Command | Status |
|---|---|---|
| Back up the 512 KiB NVDM store over Bluetooth | `python3 tools/backup_nvdm.py` | Device-verified |
| Parse a dump (all items, checksums, name hashes) | `python3 -m xm4_optimizer parse <dump.bin>` | Device-verified on a real dump |
| Flash official Sony firmware, e.g. downgrade 3.0.1 → 2.5.0 | `python3 tools/flash_firmware.py --mac <MAC> --firmware <file.bin>` | Device-verified in both directions between 2.5.0 and 3.0.1 ([test record](docs/research/ANC_FIRMWARE_COMPARISON.md)) |
| Read firmware version, battery and ANC mode | `python3 -m xm4_optimizer status` | Device-verified |
| Switch noise cancelling / ambient / wind / off | `python3 -m xm4_optimizer anc nc\|ambient [--level 1-20] [--voice]\|wind\|off\|status` | Device-verified, with readback |
| Read/write device RAM via RACE | `tools/verify_coefset.py`, `tools/post_cycle_verify.py` | Device-verified; READ_ADDRESS (`0x1680`) and WRITE_ADDRESS (`0x1681`) work without authentication |
| RACE protocol dispatch table and firmware analysis | — | Firmware-verified; full command table mapped, NVDM handler structure identified ([details](docs/STATUS.md#hardware-access)) |
| EQ presets for EasyEffects and Equalizer APO | [`presets/`](presets) | Ready to use |
| ANC filter synthesis and dry-run | `python3 -m xm4_optimizer --mock --dry-run` | Simulation only |
| `inject` / `verify` / `rollback` of ANC filters | — | **Disabled on hardware.** Their storage model is contradicted by a real dump ([details](docs/STATUS.md#anc-calibration-inject-verify-rollback)). They run with `--mock` only. |
| Persistent ANC coefficient injection | — | **In progress.** RAM injection tested (3 power-cycle attempts), NVDM persistence not yet achieved. See [STATUS.md](docs/STATUS.md) for details. |
| Spatial audio (AeroSpatial: PipeWire + Steam Audio) | see [aerospatial/pipewire/README.md](aerospatial/pipewire/README.md) | Experimental; failed its first listening test |
| Custom voice prompt packs | — | Planned ([Roadmap](#roadmap)) |

## Requirements

- Linux with BlueZ (`bluetoothctl`), or macOS with the Xcode Command Line Tools (see [macOS](#macos)); Python 3.10+, `numpy`.
- The headset paired and connected.
- At least 50% battery, the headset within about 1 m of the adapter, and audio playback paused before long transfers. Do not power off the headset during a write.

```sh
git clone https://github.com/randwardatake/wh1000xm4-anc-optimizer.git
cd wh1000xm4-anc-optimizer
pip install numpy

bluetoothctl devices | grep -i "WH-1000XM4"   # note the MAC address
```

### macOS

macOS Python has no `AF_BLUETOOTH`, so RFCOMM and SDP go through a small IOBluetooth helper ([`xm4_optimizer/transport/macos/rfcomm_bridge.swift`](xm4_optimizer/transport/macos/rfcomm_bridge.swift)). It is compiled with `swiftc` on first use and cached in `~/Library/Caches/xm4_optimizer`; the rest of the code sees an ordinary socket. Pair and connect the headset in System Settings, then use the same commands as on Linux. `--mac` is optional, the connected headset is found automatically.

```sh
xcode-select --install          # once, if swiftc is missing
python3 -m venv .venv && .venv/bin/pip install numpy
system_profiler SPBluetoothDataType | grep -A1 "WH-1000XM4"   # MAC address, if needed
.venv/bin/python -m xm4_optimizer status
```

macOS may ask the terminal app for Bluetooth access on the first run. Device-verified on macOS 27.2 (Apple M1), firmware 3.0.1: `status`, `anc`, `tools/backup_nvdm.py` (512 KiB in 78 s, all 230 item checksums OK) and `tools/flash_firmware.py` (downgrade 3.0.1 → 2.5.0: 5,452 pages in 504 s, integrity check passed, headset reconnected on 2.5.0 about 70 s after commit). Spatial audio needs PipeWire and stays Linux only.

## 1. Back up first

```sh
python3 tools/backup_nvdm.py --mac <MAC> --out my_xm4_nvdm.bin
python3 -m xm4_optimizer parse my_xm4_nvdm.bin
```

The backup writes a `.sha256` file next to the dump. Keep both.

**Do not post your dump publicly.** It contains your headset's Bluetooth address and serial number, and the pairing records of your phones and computers (`BT/paired_*`). See [privacy](docs/STATUS.md#privacy-what-an-nvdm-dump-contains).

## 2. Flash official firmware (upgrade or downgrade)

The flasher sends an official, Sony-signed firmware image over RFCOMM using the Airoha FOTA sequence. The headset checks the image (`0x1C01`) before it commits and reboots. This project does not host Sony firmware.

**Where to get v2.5.0.** The image used on the test device comes from the community archive [lzghzr/MDR_Proxy](https://github.com/lzghzr/MDR_Proxy), which mirrors files from Sony's update server (`info.update.sony.net`):

```text
firmware/2943/MDRID294300/UPG_2.5.0_20210413_enc.97529f0f6630238e77ce76bfc6766aa5616e78e7.bin
SHA-1   97529f0f6630238e77ce76bfc6766aa5616e78e7
SHA-256 ee2a4b60c5f322d56fd9e6ea51778cbbc847f816781c385f40d45959555ac508
```

The flasher checks the file's SHA-256 against the five known official WH-1000XM4 images (2.5.0, 2.6.0, 2.7.1, 2.9.0, 3.0.1, listed in `xm4_optimizer/firmware.py`) and refuses anything else. You can also check it yourself:

```sh
sha256sum UPG_2.5.0_20210413_enc.*.bin
python3 tools/flash_firmware.py --mac <MAC> --firmware UPG_2.5.0_20210413_enc.97529f0f6630238e77ce76bfc6766aa5616e78e7.bin
```

`MDRID294300` is the WH-1000XM4. Do not flash it onto any other model. The owner reported stronger ANC and no hiss after downgrading to 2.5.0; that is a listening report, not a measurement.

## 3. EQ presets

| File | Use |
|---|---|
| `presets/Sony WH-1000XM4 (ANC on) ParametricEq_EASYEFFECTS.txt` | AutoEq parametric EQ for the WH-1000XM4 with ANC on, EasyEffects import format |
| `presets/Sony WH-1000XM4 (ANC on) ParametricEq_EQAPO.txt` | The same EQ for Equalizer APO (Windows) |
| `presets/Sony_WF-1000XM4_SuperReview_3D_Spatial.json` | EasyEffects preset (EQ + crossfeed + stereo tools) built on the **WF**-1000XM4 Super Review target, which the owner preferred by ear on the WH-1000XM4 |
| `presets/Sony_WF-1000XM4_ANC_on_SuperReview_EQ.txt` | The WF-target EQ for Equalizer APO |

EasyEffects (Flatpak):

```sh
cp presets/Sony_WF-1000XM4_SuperReview_3D_Spatial.json ~/.var/app/com.github.wwmm.easyeffects/data/easyeffects/output/
flatpak run --command=easyeffects com.github.wwmm.easyeffects -l Sony_WF-1000XM4_SuperReview_3D_Spatial
```

## 4. Spatial audio (experimental)

AeroSpatial places the left and right channels on two virtual speakers using an HRTF (Valve Steam Audio). It is stereo virtualization: it cannot recover instrument positions or height from a stereo recording, and it does not decode Sony 360 Reality Audio or Dolby Atmos. The first live Spotify test sounded muffled and collapsed to the center. Fixes are in place but have not passed a new listening test. See the [Linux notes](aerospatial/pipewire/README.md), the [roadmap](docs/SPATIAL_ROADMAP.md) and the [literature review](docs/research/SPATIAL_AUDIO_LITERATURE.md).

## Documentation

| Document | Content |
|---|---|
| [docs/STATUS.md](docs/STATUS.md) | Evidence ledger for every claim |
| [docs/HARDWARE_AND_NVDM.md](docs/HARDWARE_AND_NVDM.md) | NVDM format verified against a real dump |
| [docs/WIRE_PROTOCOLS.md](docs/WIRE_PROTOCOLS.md) | Sony MDR (RFCOMM 11) and Airoha RACE (RFCOMM 21) framing |
| [docs/DSP_ACOUSTIC_THEORY.md](docs/DSP_ACOUSTIC_THEORY.md) | Filter math behind the simulator |
| [docs/SPATIAL_ROADMAP.md](docs/SPATIAL_ROADMAP.md) | Spatial audio plan and acceptance gates |
| [docs/research/](docs/research) | Linux, Windows, Android, Sony 360RA and literature notes |

## Project layout

```text
xm4_optimizer/   Python package and CLI (python3 -m xm4_optimizer)
  transport/     MDR and RACE framing, RFCOMM sockets, in-memory device emulator
  nvdm/          NVDM parser (validated on a real dump); cxd90050.py holds the unverified biquad model
  dsp/           Q4.28 fixed point, biquad synthesis, stability checks, frequency response
  safety/        dry-run, injector, verifier, rollback (emulator only, see STATUS)
tools/           backup_nvdm.py, flash_firmware.py, Steam Audio download and PipeWire scripts
aerospatial/     C++ spatial audio prototype (Steam Audio + PipeWire)
presets/         EQ presets
tests/           430 Python tests (emulator, parser fuzzing, DSP); C++ tests live in aerospatial/tests
docs/            Documentation
```

## Tests

```sh
pip install pytest numpy
python3 -m pytest
```

The tests run against an in-memory emulator. Passing tests do not show that a write path works on a real headset.

## Roadmap

1. **Repository hygiene (done 2026-10-26):** English documentation, evidence ledger, write commands disabled on hardware, licence fixed.
2. **Firmware reverse engineering (in progress):** Full Ghidra decompilation of v2.5.0 firmware, RACE dispatch table mapped, NVDM handler structure identified. NC Optimizer data flow partially traced. Goal: identify the exact power-off save path for `config/optimizer_coeff` to enable persistent ANC coefficient injection.
3. **Device-proven tools into the package:** move the tested backup and flash code paths into the library, check firmware files against known hashes, and add **voice-pack** commands (extract, build, install) using the same upload sequence as Sony's app ([fix-whxm4-voice](https://github.com/aaron-p1/fix-whxm4-voice) documents it).
4. **More Sony models:** per-model profiles, a dump → partition extraction → **redaction** workflow so owners can share data safely, and a contributor guide for forks.
5. **Apps:** Linux, Windows and Android front ends on a shared core. On Android, features that need root are listed in the normal app and marked "root only".

## License

GPL-3.0-or-later, see [LICENSE](LICENSE). Third-party components keep their own licences: Valve Steam Audio (Apache-2.0) is downloaded separately by `tools/download_steamaudio.py` and is not part of this repository.
