# Project Status and Evidence Ledger

Last reviewed: 2026-10-28. This file is the single place where claims about the project are graded by evidence. If another document disagrees with this file, this file wins until the other document is fixed.

## Evidence levels

| Label | Meaning |
|---|---|
| **Device-verified** | Observed on a real WH-1000XM4 (`MDRID294300`): a tool log, a flash readback, or a dump. The raw logs and dumps are kept by the maintainer and are not published, because dumps contain personal data (see [Privacy](#privacy-what-an-nvdm-dump-contains)). |
| **User-reported** | A listening observation by the device owner. Real evidence, but subjective and not measured. |
| **Code/simulation** | Covered by the test suite or an offline simulation. Says nothing about the real device. |
| **Unverified** | Stated somewhere earlier in the project without a source or measurement. Do not rely on it. |
| **Contradicted** | Evidence exists against it. |

## Hardware access

| Claim | Level | Evidence / note |
|---|---|---|
| RFCOMM channel 11 speaks Sony MDR; channel 21 speaks Airoha RACE (`0x05 0x5A` frames) | Device-verified | Backup and firmware flash both completed over these channels (2026-10-25). |
| A 512 KiB NVDM store (128 × 4 KiB blocks) can be read with RACE `0x0403` page reads, `storage_type=1`, addresses starting at 0 | Device-verified | `tools/backup_nvdm.py`. All 128 blocks start with the `NVDM` magic. |
| RFCOMM and SDP work on macOS through the IOBluetooth helper (`transport/macos_bridge.py`) | Device-verified | 2026-10-29, macOS 27.2 on Apple M1, firmware 3.0.1: `status`, `anc ambient`/`anc nc` with readback, and a full 512 KiB `tools/backup_nvdm.py` dump (78 s, all 230 item checksums OK). SDP lists MDR v1 on channel 9, `Serial MC` on 11 and `Airoha_APP` on 21. The first `openRFCOMMChannelSync` after the RFCOMM session comes up fails with `0xE00002BC` after about 3 s; the helper retries and the second attempt succeeds. Firmware flashing: see [Firmware flashing](#firmware-flashing). |
| NVDM reads need the MDR update-mode handshake first | **Contradicted** | Full 512 KiB reads without it worked three times on 2026-10-26 (about 70 s while streaming, 22 s idle); the owner reported that video playback was not interrupted. |
| NVDM lives in "Partition 6" at flash address `0x000DF000`, size 128 KB | Unverified / partly contradicted | The working tool uses logical NVDM addressing, not a physical flash address, and the store it reads is 512 KiB, not 128 KB. The physical location is unknown. |
| NVDM item header layout and checksum | Device-verified | The repository parser (`python3 -m xm4_optimizer parse <dump>`) validates the checksum and name hash of all 230 active items in a real dump. The corrected layout is in [HARDWARE_AND_NVDM.md](HARDWARE_AND_NVDM.md). |
| 16 MB NOR flash, bootloader / FOTA / voice-prompt address map | Unverified | No partition table read has been published. |
| The Sony control service is UUID `96cc203e-5068-46ad-b32d-e316f5e069ba` with the MDR v1 framing used by [Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge) | Device-verified | Live test 2026-10-26: firmware version request returned `"2.5.0"`, battery returned 80%, ambient-sound GET returned the current mode, and SET to ambient (level 10) and back to noise cancelling was confirmed by readback. |
| Channel 11 (`b9b213ce…`, "Serial MC") is the control channel | **Contradicted** | Live test 2026-10-26: it sends a hello frame and ACKs every frame, but answers no GET request. The backup tool uses it only for the update-mode handshake. |
| CLI `anc` and `status` | Device-verified (fixed 2026-10-26) | The old CLI sent to channel 11 with message type `0x00` and had no effect. `xm4_optimizer/transport/sony_control.py` now finds the control service by SDP (`transport/sdp.py`) and reads back every change. Tested: firmware `2.5.0`, battery, noise cancelling, ambient (levels 8, 10, 15; focus on voice on/off), wind noise reduction, off. The headset reports ambient level 1 when level 0 is requested, and keeps its focus-on-voice flag when leaving ambient mode. |
| RFCOMM channel of the control service | Device-verified | SDP on this unit: control `96cc203e…` = channel 9, `b9b213ce…` = 11, Airoha `8901dfa8…` = 21. The V2 service `956c7b26…` is not advertised. Channels can differ between models and firmware; always resolve by SDP. |
| `bluetoothctl info` Modalias encodes the firmware version | Partly verified | `usb:v054Cp0D58d0250` on a unit whose MDR firmware string is `2.5.0`. Other versions not checked. |
| RACE SDK version (`0x0301`) and build version (`0x1E08`) queries | No reply | Live test 2026-10-26 on firmware 2.5.0, after the update-mode handshake: no response, while a page read on the same connection worked. |
| RACE dispatch table location and entries | Firmware-verified | 2026-10-27: Dispatch table at `0x08305B7C` in section_04 (fw base `0x081B9000`). 8 entries, each 8 bytes `{u16 start, u16 end, u32 handler}`: `0x0A00-0x0AFF` (ATCI tunnel, `0x08228659`), `0x1C00-0x1C1F` (FOTA, `0x08229087`), `0x0CD1-0x0CD7` (link key/BD addr, `0x0822A621`), `0x0400-0x0433` (flash R/W, `0x08227FED`), `0x0E01-0x0E0B` (unknown, `0x0822A1F5`), `0x020C-0x020D` (relay, `0x0822A311`), `0x0900-0x09FF` (register/NVDM, `0x0822B3B9`), `0x1680-0x16FF` (RAM read/write, `0x0822A8A5`). |
| `0x0900`/`0x0901` RACE sub-commands and `config/optimizer_coeff` | Firmware-verified | 2026-10-27: The 0x09xx handler (`0x0822B3B8`) dispatches via sub-command table at `0x0834DB94`. The `optimizer_coeff` string (`0x0834B657`) has 3 cross-references, all in the optimizer audio subsystem (`FUN_082BB468` = power-on restore, `FUN_082BB5D8` area = power-off save), NOT in the 0x09xx RACE handler. **There is no RACE sub-command for reading or writing `config/optimizer_coeff`.** The optimizer coefficients are managed exclusively by firmware audio callbacks. |
| RACE protocol encryption/authentication | **None (CVE-2025-20700/20701/20702)** | 2026-10-27: ERNW White Paper 74 and Insinuator disclosure confirm RACE protocol has zero authentication on both BLE (CVE-2025-20700) and BR/EDR (CVE-2025-20701). No encryption layer. All volatile and non-volatile storage readable and writable by anyone in Bluetooth range. READ_ADDRESS (`0x1680`), WRITE_ADDRESS (`0x1681`), PAGE_READ (`0x0403`), PAGE_PROGRAM (`0x0402`) all work without any handshake on our XM4. The "dedicated NVDM RACE commands" mentioned in the white paper are the 0x09xx sub-commands, which map to specific predefined keys per sub-command (not generic key access). |
| RAM buffer injection → NVDM persistence | **Not proven** | 2026-10-28: Three power-cycle tests with WRITE_ADDRESS markers. (1) Markers at `0x10004058` only → NVDM unchanged (identity). (2) Markers at `0x10004058` + `optimizer_is_optimized` flag set to 1 → NVDM unchanged. (3) Markers at BOTH `0x10004058` and `0x1001A7B8` + flag → test pending. After each power cycle, RAM buffer `0x10004058` contained unrelated DSP data (not identity, not markers), while heap buffer `0x1001A7B8` contained identity (matching NVDM). The firmware's power-off save either reads from a third location, or has a conditional path that bypasses injected values. |
| Two separate coefficient data paths | Firmware-verified | 2026-10-28: (1) `0x1001A7B8` = CXD90050 DSP heap buffer, pointed to by `0x100040C8`. After boot contains identity (BE format `0x10000000`), matches NVDM. (2) `0x10004058` = struct+0x10 in the CXD90050 state struct at `0x10004048`. After boot contains non-coefficient data (`0x00550000`, `0x00A50055`, …) that is NOT identity and NOT Q4.28 biquad coefficients. Unicorn emulation suggested `FUN_082bb5d8` reads from path 2, but device tests show path 1 (heap) matches NVDM content. The actual power-off save source buffer remains unconfirmed. |
| `FUN_082bb5d8` is optimizer completion handler, not power-off save | Firmware-verified | 2026-10-28: Disassembly shows `FUN_082bb5d8` references log strings "hal_audio_sony_optimizer_analyzing_done" and "generation end! %x …". It clears `optimizer_is_optimized` (`0x100798B4`) to 0, byte-swaps 10 words LE→BE, then calls `nvdm_write("config", "optimizer_coeff", …)`. No conditional branch before the write. This function runs when the NC Optimizer finishes analysis, not necessarily at power-off. The actual power-off batch save mechanism (which writes `optimizer_coeff` as the first item alongside volume, ANC mode, etc.) may be a separate code path. |
| RACE page-read response layout | Device-verified | 270-byte frame: 6-byte header + 8 bytes (status `0x00`, …, address) + 256 bytes of data. Data starts at byte 14. |
| RACE READ_ADDRESS (`0x1680`) reads 4 bytes from any memory address | Device-verified | Live test 2026-10-26 on firmware 2.5.0. Read SRAM at `0x04000000`–`0x0400000C` (vector table: SP=`0x04004000`, Reset=`0x081B9001`, NMI=`0x100001C9`, HardFault=`0x100001D7`). Payload: `\x00\x00` + LE address. Response: 6-byte header + ret(1) + unk(2) + addr(4) + data(4). |
| RACE WRITE_ADDRESS (`0x1681`) writes 4 bytes to an address | **Device-verified** | Live test 2026-10-26: wrote `0xCAFEBABE` to coefficient buffer word[1] at `0x1001A7BC`, readback returned `0xBEBAFECA` (LE representation of the BE value). Original zero restored and verified. Earlier test at `0x04080F00` failed because that address is outside SRAM. |
| ATCI tunnel (`0x0F92`) on retail firmware 2.5.0 | **Dead** | Live test 2026-10-26: `AT\r\n` and `ATI\r\n` sent over `0x0F92` produced no response. The `AT+HWPTOOL=COEFSET` path is not viable on v2.5.0 via this opcode. |
| RACE relay mode (`0x020C`) enables ATCI | Device-verified | Live test 2026-10-26: sending `0x020C` returns success (`00`), after which `0x0A00` ATCI tunnel commands return `00 00 XX` instead of error `0x11`. Before relay: `0x0A00` returns error code `0x11`. After relay: AT commands accepted. Tested `AT`, `ATI`, `AT+HWPTOOL=COEFSET`, `AT+HWPTOOL=COEFDUMP`, `AT+NCOPTTOOL=NCOPTDUMP`, `AT+NCOPTTOOL=NCOPTSET`, `AT+CXD90050=REG_READ,0x0000`. All return only 3-byte payloads; no indication packets follow. Relay disable is `0x020D`. |
| RACE NVDM write (`0x0E02`) | **Non-functional** | Live test 2026-10-26: tested ~15 different payload formats (1-byte to 16-byte, with/without group+item strings, with/without storage type). All return single-byte `00` RSP. Readback of flash page after write showed value unchanged. The handler appears to be a stub or disabled. |
| RACE PAGE_PROGRAM (`0x0402`) for all storage types | **Blocked** | Live test 2026-10-26: tested storage_type 0 through 4 at various addresses. All return `rc=0x0A`. Flash writing is completely blocked via RACE on retail firmware. |
| NVDM is NOT in main NOR flash | Device-verified | Live test 2026-10-26: full 4 MB NOR scan (1024 × 4 KB blocks, `storage_type=0x00`) found zero NVDM blocks (no `NVDM` magic). NVDM uses its own virtual address space (`storage_type=0x01`, starting at `0x00000000`). The physical storage is likely AB1562 internal flash, not the external NOR. |
| READ_ADDRESS/WRITE_ADDRESS regression | **Device-verified (persistent, base-only restriction)** | 2026-10-26: reads worked initially; at some point began returning `rc=3` for all non-base addresses. Persists across reconnections AND power cycles. 2026-10-27 systematic test: **only addresses with lower 24 bits = 0 work** (`0x04000000` rc=0, `0x10000000` rc=0, `0x08000000` rc=0, `0x0C000000` rc=0). All others (`0x04000004`, `0x1001A7B8`, `0x10004048`) return `rc=3` with `0000ffff` error payload. Decompile: `rc=3` = `CHECKSUM_ERROR` in protocol code. Root cause likely firmware-level protection triggered by extensive probing; the restriction is to major memory region base addresses only. `0x00000000` returns `rc=255`. |
| DSP realtime RACE commands (`0x0CD1`–`0x0CD7`) | **Device-verified: NOT audio DSP** | 2026-10-27 analysis: these are BLE/connection management commands, not CXD90050 audio DSP pass-through. They cannot be used to read/write ANC coefficients. |
| Firmware signature is RSA-2048 checked against eFuses | Unverified | No source. The flasher only shows that the device ran an integrity check (`0x1C01`) and accepted an official image. |

## Firmware reverse engineering

| Claim | Level | Evidence / note |
|---|---|---|
| v2.5.0 FOTA image is LZMA_AES encrypted, 7 sections | Device-verified (header parse) | Header at offset 0 parsed with [ramikg/airoha-firmware-parser](https://github.com/ramikg/airoha-firmware-parser) 010 template. Compression type: LZMA_AES. Firmware data at offset `0x1000`, size 1,391,408 bytes. |
| Main firmware loads at `0x1B9000` | Device-verified (cross-check) | Section 4: `dest=0x001B9000`, decompressed size `0x19D000` (~1.7 MB). Matches Reset vector `0x081B9001` read via `0x1680`. |
| DSP code sections at `0x0C480000` / `0x0C490000` | Parsed, unverified | Sections 5–6 target these addresses. Possibly CXD90050 DSP code (hypothesis). |
| MT2811 memory map from vector table | Device-verified | SP=`0x04004000` (SRAM), code region 1 at `0x08xxxxxx`, code region 2 at `0x10xxxxxx`. Flash-mapped code appears at `0x08000000`+. |
| AES-128-CBC key/IV for WH-1000XM4 FOTA image decryption | Verified offline | The key and IV published by [HelgeSverre/sony-vp-extract](https://github.com/HelgeSverre/sony-vp-extract) also decrypted our SHA-256-identified official v2.5.0 and v3.0.1 FOTA images. Both resulting LZMA streams decompressed, and all 7 / 8 section SHA-256 digests matched the image headers. No live memory dump was needed for these two files. |
| v2.5.0 versus v3.0.1 firmware sections | Verified offline; ANC effect unknown | The FOTA header contains 7 sections in v2.5.0 and 8 in v3.0.1. The sections targeting `0x00032000` (90,112 bytes) and `0x0C480000` (32,768 bytes) are bit-identical. Other sections differ, and v3.0.1 adds a 3,145,728-byte section targeting `0x0C080000`. These differences do not identify ANC filter changes or quantify acoustic performance. |
| All 5 official FOTA images downloaded | Verified (SHA-256) | v2.5.0, v2.6.0, v2.7.1, v2.9.0, v3.0.1 from [lzghzr/MDR_Proxy](https://github.com/lzghzr/MDR_Proxy). All LZMA_AES encrypted. |
| Device firmware dump matches official image | Device-verified | All 7 sections read from the headset via `0x1680` page reads (2,756,608 bytes total, 673 × 4 KiB chunks). Combined SHA-256 matches the decompressed official v2.5.0 image. Files at `~/Desktop/XM4_Firmware_Device_Dump_2.5.0/`. |
| Ghidra headless decompilation of section 4 (main ARM firmware) | Device-verified (static analysis) | Ghidra 12.2 headless on the 1.69 MB section 4 binary (`0x081B9000` base, ARM:LE:32:Cortex Thumb). Decompiled 7 functions with `optimizer_coeff` cross-references + 5 nearby. See decompile findings below. |

### Ghidra decompile findings (v2.5.0 section 4)

Firmware function call chain for optimizer coefficient handling, from Ghidra decompilation:

| Function | Address | Role | Key detail |
|---|---|---|---|
| `FUN_082bb468` | `0x082bb468` | Read `optimizer_coeff` from NVDM | Reads 40 bytes (0x28) from NVDM group `"config"` / item `"optimizer_coeff"`, calls CXD90050 set |
| `FUN_082bb5d8` | `0x082bb5d8` | Optimizer result → CXD90050 + NVDM save | Gets optimizer struct, byte-swaps 10 words LE→BE, sends to CXD90050, writes to NVDM. This is the power-off save path. |
| `FUN_082a144c` | `0x082a144c` | `cxd90050_set_nc_optimize_coefficient` | Checks DSP state, calls `FUN_082b02f8` to write to CXD90050 |
| `FUN_082b02f8` | `0x082b02f8` | `cxd90050_nc_set_nc_optimize_coefficient` | Calls `FUN_082b19a8` for actual coefficient write |
| `FUN_082b19a8` | `0x082b19a8` | CXD90050 coefficient buffer management | Allocates 40-byte heap buffer at `*(0x10004048 + 0x80)` = `*(0x100040C8)`, stores/compares coefficients, logs values |
| `FUN_0821a008` | `0x0821a008` | Byte-order converter + CXD90050 set | Converts `param_2` words from LE to BE, calls `FUN_082a144c` |
| `FUN_08255878` | `0x08255878` | Top-level coefficient apply (boot/optimizer) | Calls `FUN_082bb468` (NVDM read path) or `FUN_082bb4b8` (write path) depending on `param_1` |
| `FUN_0822a518` | `0x0822a518` | Address-based read router | Routes by bits 26-27: `(addr & 0x0C000000) == 0x0C000000` → SPI flash via `FUN_0822abb0`, else → MCU memory via `FUN_081bd0f0(addr & 0xf7ffffff)`. Dispatch table at `0x08304BB4` points to handler at `0x0822A8A5` (Thumb). |

Key RAM addresses from decompilation:

| Address | What | Evidence |
|---|---|---|
| `0x10004048` | CXD90050 NC state struct base | Global pointer `DAT_082b1acc`. Live read returned `0x00000013` at offset 0 (state field, not pointer). |
| `0x100040C8` | Pointer to 40-byte coefficient heap buffer | `*(struct + 0x80)`. **Live read: `*(0x100040C8) = 0x1001A7B8`** — this is the heap-allocated coefficient buffer. 40 bytes read: identity biquad `[0x10000000, 0, 0, 0, 0, 0x10000000, 0, 0, 0, 0]` (big-endian Q4.28, where 0x10000000 = 1.0). |
| `0x1001A7B8` | Live coefficient buffer (40 bytes, heap) | **Device-verified**. Contains 2 × 5 Q4.28 biquad coefficients in big-endian. READ_ADDRESS returns LE words, so `0x10000000` BE reads as `0x00000010` LE. WRITE_ADDRESS verified: wrote test pattern, readback matched, original restored. **This is the inject target.** |
| `0x100798B4` | `optimizer_is_optimized` flag | Zeroed in `FUN_082bb5d8`. Live read confirmed: `0x00000000`. |
| `0x100797AC` | CXD90050 state flags | `DAT_082b0360`. Bit 0 at `+4` set after coefficient update. Live read: `0x00B313FF`. |

ATCI relay mode limitation (2026-10-27): the relay mode (`0x020C` + `0x0A00`) always returns exactly 3 bytes: `[00][00][XX]` where `XX` is the first character of the AT response text. No additional data follows, even with extended waits. The full AT response (e.g., `DSP1_NCOPT_DATA={...}` from NCOPTGET) is truncated to a single byte. This means we cannot read coefficient values via ATCI — only detect state changes (null vs data-present). The full response text likely goes to UART only.

NCOPTSET handler analysis (2026-10-27): Function at `0x0820F0B0` (`atci_cmd_hdlr_ncopttool`), literal pool at `0x0820F31C`. Firmware strings confirm: NCOPTSET parses `%d,0x%x` (decimal offset, hex value), validates with "Invalid offset or invalid value" error, allocates via `pvPortMallocNC()`, and **sends directly to DSP1** ("Failed to send message to dsp1" error path). This means NCOPTSET writes to the CXD90050 DSP in real-time, not just to a RAM buffer. The earlier wind noise incident (user-reported) is consistent with DSP-direct write of bad coefficients. **Implication for NVDM verification:** NCOPTSET may bypass the RAM buffer at `0x1001A7B8`, so the power-off save (`FUN_082bb5d8`) may NOT capture NCOPTSET values in NVDM. A negative NVDM result does not disprove DSP write.

Byte-swap confirmed in decompile: `FUN_082bb5d8` line 131–132 does `val << 24 | (val >> 8 & 0xFF) << 16 | (val >> 16 & 0xFF) << 8 | val >> 24` — full 32-bit endian swap on each of the 10 words before NVDM write. This confirms NVDM stores big-endian Q4.28, while CXD90050 receives big-endian (the swap happens before both the DSP call and the NVDM write, from the LE optimizer output).

### Inject strategy (updated after Ghidra decompilation)

The decompilation reveals the complete coefficient pipeline. Two inject paths:

**Path A — RAM inject (preferred, needs live device test):**

1. Read `*(0x100040C8)` via `0x1680` to get the heap-allocated 40-byte coefficient buffer address.
2. Write 10 custom Q4.28 big-endian words to that buffer via `0x1681`.
3. Power off → `FUN_082bb5d8` saves the buffer to NVDM → persistent.
4. On next boot → `FUN_082bb468` reads from NVDM → sends to CXD90050 → custom ANC active.

**Path B — Direct CXD90050 DSP write (bypass NVDM):**

1. Call `FUN_082a144c` indirectly by writing coefficients to the buffer and triggering the coefficient-update flag at `0x100797B0` (bit 0).
2. This applies coefficients to the DSP immediately, without waiting for power cycle.

**Both blockers cleared (2026-10-26):**
- ✓ `*(0x100040C8) = 0x1001A7B8` — coefficient buffer address read from live device.
- ✓ WRITE_ADDRESS verified at `0x1001A7BC` (buffer word[1]) — wrote `0xCAFEBABE`, readback confirmed, original restored.
- **Remaining:** Write full custom biquad to buffer, verify DSP picks it up (listening test), verify persistence across power cycle.

**Write path status (2026-10-26, all tested):**

| Path | Status | Detail |
|---|---|---|
| RACE NVDM write (`0x0E02`) | **FAILED** | Returns `00` for all payload formats, but does not actually write. Flash readback unchanged. |
| RACE PAGE_PROGRAM (`0x0402`) | **FAILED** | Returns `rc=0x0A` for storage types 0–4. Flash writing completely blocked. |
| Physical NOR flash write | **N/A** | NVDM is not in external NOR — it's in AB1562 internal flash. |
| ATCI COEFSET via relay mode | **Accepted (correct format), DSP effect unverified** | `AT+HWPTOOL=COEFSET,<filter>,<stage>,<b0>,...,<a2>` returns `0x54` for all 20 filter×stage combos. Previous hex-blob format was wrong. |
| ATCI NCOPTSET via relay mode | **Accepted + RAM write confirmed, NVDM persistence failed** | `AT+NCOPTTOOL=NCOPTSET,<offset>,0x<value>` accepted for all offsets/values. NCOPTGET state change confirms RAM write. Power cycle tests (2026-10-28): NVDM always returns identity after power cycle regardless of NCOPTSET state. NCOPTSET writes to DSP path, not the NVDM save path. |
| RAM WRITE_ADDRESS (`0x1681`) injection → NVDM | **Failed (3 attempts)** | 2026-10-28: Markers written to `0x10004058` (struct+0x10), `0x1001A7B8` (heap), and both simultaneously, with and without `optimizer_is_optimized` flag. All three power-cycle tests showed NVDM unchanged (identity). The firmware's power-off save either reads from an unidentified third buffer, or conditionally skips the write when no real optimizer run occurred. |
| DSP realtime (`0x0CD1`–`0x0CD7`) | **Not viable** | BLE/connection management, not audio DSP. |
| 0x09xx RACE sub-commands on XM4 | **No response** | 2026-10-28: Probed all sub-commands 0x00–0x3F for both 0x0900 (write) and 0x0901 (read). Zero responses. Either disabled on XM4 firmware or requires a different payload format than the Maxwell (AB1568). |

**Next steps to resolve (2026-10-28):**
1. **Full firmware decompilation** — Ghidra headless decompile of all functions to readable C in progress. Once complete, identify the actual power-off NVDM batch-save function and trace which buffer it reads `optimizer_coeff` from.
2. **Trigger real NC Optimizer run** — Send MDR `84 01 00 01`, wait for optimizer to complete, then power cycle. If the optimizer's own output persists to NVDM, we can intercept and modify the RAM buffer between optimizer completion and power-off.
3. **Direct NVDM flash write** — Serialize a properly formatted NVDM record with custom coefficients, find free space in the log-structured store, write via PAGE_PROGRAM. Previous attempt failed (`rc=0x0A`), but PAGE_PROGRAM with `storage_type=1` has not been tested.
4. **Firmware patching** — Modify the power-off save function via FOTA to always save from a known buffer, or to skip the identity-overwrite path.

## ANC calibration (`inject`, `verify`, `rollback`)

| Claim | Level | Evidence / note |
|---|---|---|
| `DNC/ALIGN_FF_L/R`, `DNC/ALIGN_FB_L/R` are 108-byte blocks holding five Q4.28 biquads | **Contradicted** | In a real dump these items are in group `CXD90050`, not `DNC`, and each value is 8 bytes of ASCII hex text. |
| `DNC/HOWLING_PRE_PARAM`, `DNC/HOWLING_DET_PARAM`, `DNC/OPTIMIZER_PARAM` exist | **Contradicted** | None of these items exists in the dump. A 1-byte `DNC/howling_pre_notice_off` exists. |
| A 40-byte `config/optimizer_coeff` item exists; it holds NC Optimizer output | Device-verified | Firmware strings: `[AC][optimizer] generation end! %x` ×10 and "Cannot write optimizer coeff to nvdm". Two older, deleted records hold real values. Decoded as 2 stages × `[b0, b1, b2, −a1, −a2]`, signed Q4.28, big-endian, stage 1 is a stable low-frequency shelf (+3.84 dB at 20 Hz, +2.39 dB at 50 Hz, +0.53 dB at 100 Hz, 0 dB above 500 Hz; pole radius 0.995) and stage 2 is near identity. The layout is an interpretation supported by these plausible, stable responses. All other records, including the active one, are identity. |
| Starting NC Optimizer over MDR (`84 01 00 01`) | Device-verified | 2026-10-26 on firmware 2.5.0: status messages `0x85` went WEARING_CONDITION → ATMOSPHERIC_PRESSURE → ANALYZING → FINISHED → NOT_RUNNING in about 15 s (status names from Gadgetbridge). Pressure reported 1.0 atm. |
| NC Optimizer result is stored immediately | **Contradicted** | Nothing in the NVDM changed after the run. After a power cycle `optimizer_coeff` was rewritten, still identity. |
| When `optimizer_coeff` is written | Device-verified | It is the first item of a batch the firmware writes at power off (sequence numbers directly before volume, ANC mode, `nc_atm_value`, `nc_is_optimized`, `power_off_reason`, …). So the firmware saves its RAM value at power off: a value written directly into the NVDM while the headset runs would be overwritten at the next power off. `nc_is_optimized` is `"1"` in all 32 records. |
| NVKEY read with RACE `0x0A00` | Device-verified | Four `AB15/<key>` items read live matched the dump byte for byte. Command format from [lx-exe/stealth600-gen3-usb-fix](https://github.com/lx-exe/stealth600-gen3-usb-fix). `config/*` items are not NVKEYs; a RACE command that writes them is not known yet. |
| Writing a new `config/optimizer_coeff` over RACE works | **Contradicted** | The only device attempt (2026-10-25) failed its readback check (`Readback mismatch at 0x00021E00`). The active value in a later dump is still the identity value. |
| "+6.5 to +7.5 dB extra low-frequency noise reduction" | Code/simulation | This is the simulated magnitude response of the synthesized filter. It is not an ANC measurement. Real noise reduction depends on the acoustic paths, which were never measured. |
| A bad NVDM checksum cannot bootloop the headset (ROM fallback) | Unverified | No source or test. |
| Rollback restores the dump "bit-exact" | Unverified | The rollback path only issues page-program commands. NOR flash cannot turn 0 bits back into 1 without an erase, and the emulator in `virtual_device.py` hides this. |
| `AT+HWPTOOL=COEFSET` / `AT+ENVDM` ATCI commands | **Dead on v2.5.0 via 0x0F92; alive via relay mode with correct format** | `0x0F92` produces no response on retail v2.5.0. RACE relay mode (`0x020C`) enables ATCI via `0x0A00`. **Critical format discovery from firmware binary strings (v2.5.0):** previous sessions sent raw hex blobs (`AT+HWPTOOL=COEFSET,<40-byte-hex>`) which was WRONG. Correct formats: `AT+NCOPTTOOL=NCOPTSET,<offset>,0x<value>` (decimal offset, hex value; fw string at `0x082F4B26`: `%d,0x%x`), `AT+NCOPTTOOL=NCOPTGET` → `DSP1_NCOPT_DATA={...}`, `AT+NCOPTTOOL=NCOPTDUMP` → `DSP1_NCOPT_ALL_DATA={...}`, `AT+HWPTOOL=COEFSET,<filter_id>,<stage_idx>,<b0>,<b1>,<b2>,<a1>,<a2>` (fw string at `0x082F3F60`: `iir_coef[%d][%d]`). |
| NCOPTSET with correct format | **Device-verified: accepted, RAM state change confirmed** | 2026-10-27: `AT+NCOPTTOOL=NCOPTSET,<offset>,0x<value>` returns `0x54` ('T') or `0x4F` ('O') for ALL offsets tested (0–49, negatives, up to 1000). All values accepted (identity, markers, zero, max). Offsets 5 and 13 sometimes return `0x00` instead. **Key proof: NCOPTGET returns `0x00` (null/no data) BEFORE NCOPTSET, changes to `0x54` ('T'/data present) AFTER NCOPTSET.** This state change proves NCOPTSET modifies firmware internal state. NVDM unchanged immediately after NCOPTSET — consistent with Ghidra finding that NCOPTSET writes to RAM buffer, not NVDM. Power cycle needed to trigger `FUN_082bb5d8` (RAM → NVDM save). |
| HWPTOOL COEFSET with correct format | **Device-verified: accepted** | 2026-10-27: `AT+HWPTOOL=COEFSET,<filter>,<stage>,<b0>,<b1>,<b2>,<a1>,<a2>` returns `0x54` for all 20 combinations (4 filters × 5 stages). |
| ENVDM read/write via ATCI | **Mixed** | 2026-10-27: `AT+ENVDM=R,config,optimizer_coeff` returns `0x54` or `0x00` depending on session state. `AT+ENVDM=W,...` with data returns `0x4F`/`0x87`, flash unchanged. |

Code facts about the old write paths (confirmed by reading the code after a user report, 2026-10-26):

- `inject` reads the NVDM through raw flash reads (`storage_type=0`) at `0x000DF000`. On a real headset this returns error `0x03` before anything is written.
- The injector updates an item in place only when the new value has the same size as the old one. The real `ALIGN_*` values are 8 bytes, the injector's are 108 bytes, so it would fall back to re-serializing every active item. It also requires a 128 KB image, while a real backup is 512 KiB.
- `rollback` accepts only 128 KB images, so it rejects a real 512 KiB backup.
- `--dry-run --image <real dump>` finds no 108-byte `ALIGN_*` block, falls back to an identity filter, and prints the same table as the README example. The "+7.5 dB" is the computed gain of the synthesized filter, never a measurement on a unit.

Because of these findings, the CLI refuses `inject` and `rollback` on real hardware unless `--allow-unverified-write` is given. `--mock` still runs them against the emulator.

## Firmware flashing

| Claim | Level | Evidence / note |
|---|---|---|
| Official firmware v2.5.0 can be flashed over Bluetooth with `tools/flash_firmware.py`, downgrading from v3.0.1 | Device-verified | 2026-10-25: 5,452 pages (1,395,504 bytes) programmed, integrity check `0x1C01` passed, commit `0x1C02`, device rebooted. 2026-10-26, second unit, Windows 11 with Python's native `AF_BLUETOOTH`: same result after the fix below. 2026-10-29, third unit, macOS 27.2 (Apple M1) through the IOBluetooth helper: 341 blocks erased, 5,452 pages programmed in 504 s (2.7 KB/s), integrity check passed, commit accepted; the headset reconnected about 70 s later and reported 2.5.0. |
| Official firmware v3.0.1 can be flashed from v2.5.0 on the tested unit | Device-verified | 2026-10-26: 16,219 pages staged to the device-reported FOTA partition; `0x1C01` accepted and `0x1C02` committed. After a manual power-on, MDR reported firmware `3.0.1`, battery 80%, ANC noise cancelling. |
| ANC-related active NVDM values after v2.5.0 → v3.0.1 | Device-verified; no acoustic inference | Full 512 KiB backups on this unit contained 128 valid PEBs, 230 active items, and zero corrupted items in both versions. Nine active values differed, involving pairing, boot, power and service state. `config/optimizer_coeff`, `config/nc_is_optimized`, `config/nc_atm_value`, and the four `CXD90050/ALIGN_*` values were unchanged. Firmware code can still change ANC behavior; no attenuation was measured. |
| Return to v2.5.0 after the comparison | Device-verified | 2026-10-26: 5,452 pages staged; `0x1C01` accepted and `0x1C02` committed. After a manual power-on, MDR reported `2.5.0`, battery 80%, ANC noise cancelling. A third full NVDM backup again had 128 valid PEBs, 230 active items, zero corrupted items, and the same seven ANC-related active values. The original automatic-power-off setting was restored and confirmed by readback. See [ANC_FIRMWARE_COMPARISON.md](research/ANC_FIRMWARE_COMPARISON.md). |
| The flasher must erase and program at the partition address the headset reports in `0x1C00`, on the reported storage type | Device-verified | 2026-10-26: a unit on v3.0.1 answered `0x1C00` with partition ID `0x00`, storage `0x01`, address `0x00510000`, length `0x5FF000`, and refused every erase at address 0 on storage 0 with status `0x02`. The earlier flasher read the status byte as the storage type and erased from address 0. It now parses the reply (status, ID, storage, u32 address, u32 length), erases with `storage, u32 0x1000, u32 address` per 4 KB block, programs at absolute addresses, and writes FOTA states `0x0200`/`0x0201`/`0x0210` around erase and program, as the Sony app does. Reply and payload layouts cross-checked against [aaron-p1/fix-whxm4-voice](https://github.com/aaron-p1/fix-whxm4-voice) `docs/upload-protocol.md`. |
| After the downgrade, ANC sounded stronger and the hiss was gone | User-reported | No measurement. |
| Sony reduced ANC by 4–6 dB in later firmware | Unverified | No measurement or source. Treat it as a hypothesis. |
| Brick risk is "none" with official firmware | Unverified | No source. Since 2026-10-26 the flasher refuses files whose SHA-256 is not one of the five known official WH-1000XM4 images (`xm4_optimizer/firmware.py`) unless `--allow-unknown-firmware` is given. |

### Where the v2.5.0 file comes from

The project does not host Sony firmware. The v2.5.0 image used on the test device came from the community archive [lzghzr/MDR_Proxy](https://github.com/lzghzr/MDR_Proxy) (MIT). Its `mdrdownload.ts` downloads firmware from Sony's own update server (`info.update.sony.net`).

| Field | Value |
|---|---|
| Path in that repository | `firmware/2943/MDRID294300/UPG_2.5.0_20210413_enc.97529f0f6630238e77ce76bfc6766aa5616e78e7.bin` |
| Added in | commit `4724e44`, 2022-12-11 |
| Size | 1,395,504 bytes |
| SHA-1 | `97529f0f6630238e77ce76bfc6766aa5616e78e7` (also embedded in the file name) |
| SHA-256 | `ee2a4b60c5f322d56fd9e6ea51778cbbc847f816781c385f40d45959555ac508` |

The same directory also holds 2.6.0, 2.7.1, 2.9.0 and 3.0.1. `MDRID294301`–`294303` contain only pointer files ("same as MDRID294300").

## Voice prompts

| Claim | Level | Evidence / note |
|---|---|---|
| A custom voice pack (Disco Elysium) was installed and heard | Device-verified / user-reported | Install script reported success (2026-10-25) and the owner heard the prompts. The script is not in this repository yet. |
| Safer route: the same upload sequence the Sony app uses for voice packs | Source | [aaron-p1/fix-whxm4-voice](https://github.com/aaron-p1/fix-whxm4-voice) (MIT). Its `docs/adr/0001-direct-bluetooth-upload.md` says "stray RACE flash writes are known to brick devices" and that its tool refuses writes outside the FOTA partition. The same file says "Firmware cannot be downgraded". Our v2.5.0 log above contradicts that for the FOTA flasher used here. Voice-pack support is planned for the CLI. |

## Spatial audio (AeroSpatial, Linux)

| Claim | Level | Evidence / note |
|---|---|---|
| Stereo is rendered as two virtual speakers with Steam Audio HRTFs | Code/simulation | C++ tests in `aerospatial/tests`. |
| Variable PipeWire block sizes no longer drop samples | Code/simulation | Ring-buffer FIFO and `test_variable_quantum_continuity` (2026-10-26). |
| First live Spotify trial sounded muffled, lost the EQ and collapsed to the center | User-reported | The routing script bypassed EasyEffects; this was fixed in the script. A new live listening test is still pending. |
| At ±30° a deep notch near 10 kHz appears; ±60° removes it | Reported by a previous session | The measurement script is not in the repository and has not been reproduced. |
| "Sub-2 ms deterministic latency" | Unverified | Default block is 256 samples at 48 kHz = 5.33 ms. Nothing has been measured end to end. |
| Content-aware or "real" 3D (instrument positions from stereo) | Not provided | Stereo input cannot recover object positions. See [SPATIAL_ROADMAP.md](SPATIAL_ROADMAP.md). |

## Privacy: what an NVDM dump contains

A real dump contains items such as `production/bt_addr`, `production/model_serial`, `config/device1_addr`, `config/device2_addr` and `BT/paired_00` … `BT/paired_03` (70 bytes each, the pairing records of phones and computers). Never post a raw dump publicly. A redaction step is planned before any "share your dump" workflow is offered.

## Known documentation and repository issues fixed on 2026-10-26

- License: the repository is GPL-3.0-or-later. README and `pyproject.toml` wrongly said MIT.
- README preset paths pointed to files that do not exist.
- The README test count (407) was outdated. The suite currently has 430 tests.
- Turkish documents were translated to English. Agent working notes were removed from the repository; their verified content is summarized here.
