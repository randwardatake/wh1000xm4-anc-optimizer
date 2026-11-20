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
| NVDM reads need the MDR update-mode handshake first | **Contradicted** | Full 512 KiB reads without it worked three times on 2026-10-26 (about 70 s while streaming, 22 s idle); the owner reported that video playback was not interrupted. |
| NVDM lives in "Partition 6" at flash address `0x000DF000`, size 128 KB | Unverified / partly contradicted | The working tool uses logical NVDM addressing, not a physical flash address, and the store it reads is 512 KiB, not 128 KB. The physical location is unknown. `RfcommDeviceTransport.read_flash_page` was corrected on 2026-11-20 to use `storage_type=1` and a logical address; the former `storage_type=0` at `0x000DF000` path could not read NVDM on real hardware. |
| NVDM item header layout and checksum | Device-verified | The repository parser (`python3 -m xm4_optimizer parse <dump>`) validates the checksum and name hash of all active items in a real dump. The corrected layout is in [HARDWARE_AND_NVDM.md](HARDWARE_AND_NVDM.md). A dump taken 2026-11-20 on firmware 2.5.0 contained 128 valid PEBs, 231 active items and 0 corrupted items; `config/*` includes `eq_preset`, `eq_manual`, `eq_custom1/2`, `beep_guidance`, `stc_enable`, `test_vp_disable`, `test_nc_disable`, `test_auto_power_off` and an identity `optimizer_coeff`. |
| 16 MB NOR flash, bootloader / FOTA / voice-prompt address map | Unverified | No partition table read has been published. |
| The Sony control service is UUID `96cc203e-5068-46ad-b32d-e316f5e069ba` with the MDR v1 framing used by [Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge) | Device-verified | Live test 2026-10-26: firmware version request returned `"2.5.0"`, battery returned 80%, ambient-sound GET returned the current mode, and SET to ambient (level 10) and back to noise cancelling was confirmed by readback. |
| Channel 11 (`b9b213ce…`, "Serial MC") is the control channel | **Contradicted** | Live test 2026-10-26: it sends a hello frame and ACKs every frame, but answers no GET request. The backup tool uses it only for the update-mode handshake. |
| CLI `anc` and `status` | Device-verified (fixed 2026-10-26) | The old CLI sent to channel 11 with message type `0x00` and had no effect. `xm4_optimizer/transport/sony_control.py` now finds the control service by SDP (`transport/sdp.py`) and reads back every change. Tested: firmware `2.5.0`, battery, noise cancelling, ambient (levels 8, 10, 15; focus on voice on/off), wind noise reduction, off. The headset reports ambient level 1 when level 0 is requested, and keeps its focus-on-voice flag when leaving ambient mode. |
| MDR v1 setting commands for user settings | Source-verified, not yet live-tested here | The official app changes settings through MDR, not raw NVDM writes. XM4 v2.5.0 command bytes (all device-verified by [Fanxingkeming/sony-headphones-control](https://github.com/Fanxingkeming/sony-headphones-control/blob/main/docs/protocol-reference.md)): EQ read `56 01` / write `58 01 …`; touch panel `D6 D1`/`D8 D1 01 v`; speak-to-chat `F6 05`/`F8 05 01 v`; pause-when-removed `F6 03`/`F8 03 01 v`; auto power off `F6 04`/`F8 04 01 <10 00\|11 00>`; DSEE `E6 02`/`E8 02 00 v`; voice guidance (Table 2) `48 01 01 v`. Implemented in `sony_control.py` and exposed as `xm4-optimizer settings`; unit-tested only until a live device is available. Firmware persists the resulting state itself, so no raw flash write is needed for these settings. |
| RFCOMM channel of the control service | Device-verified | SDP on this unit: control `96cc203e…` = channel 9, `b9b213ce…` = 11, Airoha `8901dfa8…` = 21. The V2 service `956c7b26…` is not advertised. Channels can differ between models and firmware; always resolve by SDP. |
| `bluetoothctl info` Modalias encodes the firmware version | Partly verified | `usb:v054Cp0D58d0250` on a unit whose MDR firmware string is `2.5.0`. Other versions not checked. |
| RACE SDK version (`0x0301`) and build version (`0x1E08`) queries | No reply | Live test 2026-10-26 on firmware 2.5.0, after the update-mode handshake: no response, while a page read on the same connection worked. |
| RACE dispatch table location and entries | Firmware-verified | 2026-10-27: Dispatch table at `0x08305B7C` in section_04 (fw base `0x081B9000`). 8 entries, each 8 bytes `{u16 start, u16 end, u32 handler}`: `0x0A00-0x0AFF` (ATCI tunnel, `0x08228659`), `0x1C00-0x1C1F` (FOTA, `0x08229087`), `0x0CD1-0x0CD7` (link key/BD addr, `0x0822A621`), `0x0400-0x0433` (flash R/W, `0x08227FED`), `0x0E01-0x0E0B` (unknown, `0x0822A1F5`), `0x020C-0x020D` (relay, `0x0822A311`), `0x0900-0x09FF` (register/NVDM, `0x0822B3B9`), `0x1680-0x16FF` (RAM read/write, `0x0822A8A5`). |
| `0x0900`/`0x0901` RACE sub-commands and `config/optimizer_coeff` | Firmware-verified | 2026-11-20: the 0x09xx handler `FUN_0822B3B8` is a logging-only stub. `config/optimizer_coeff` is referenced by `FUN_082BB468` (boot restore), `FUN_082BB5D8` (optimizer generation-end write) and `FUN_082BB4B8` (**delete** at power off), all in the audio subsystem. **There is no RACE sub-command for it.** |
| RACE protocol encryption/authentication | **None (CVE-2025-20700/20701/20702)** | 2026-10-27: ERNW White Paper 74 and Insinuator disclosure confirm RACE protocol has zero authentication on both BLE (CVE-2025-20700) and BR/EDR (CVE-2025-20701). No encryption layer. All volatile and non-volatile storage readable and writable by anyone in Bluetooth range. READ_ADDRESS (`0x1680`), WRITE_ADDRESS (`0x1681`), PAGE_READ (`0x0403`), PAGE_PROGRAM (`0x0402`) all work without any handshake on our XM4. The "dedicated NVDM RACE commands" mentioned in the white paper are the 0x09xx sub-commands, which map to specific predefined keys per sub-command (not generic key access). |
| RAM buffer injection → NVDM persistence | **Contradicted** | 2026-10-28: Three power-cycle tests with WRITE_ADDRESS markers. (1) Markers at `0x10004058` only → NVDM unchanged (identity). (2) Markers at `0x10004058` + `optimizer_is_optimized` flag set to 1 → NVDM unchanged. (3) Markers at BOTH `0x10004058` and `0x1001A7B8` + flag → NVDM unchanged. The full decompilation explains this: neither the optimizer completion write nor the power-off batch save reads those buffers. |
| Two separate coefficient data paths | Firmware-verified | 2026-10-28: (1) `0x1001A7B8` = CXD90050 DSP heap buffer, pointed to by `0x100040C8`. After boot contains identity (BE format `0x10000000`), matches NVDM. (2) `0x10004058` = struct+0x10 in the CXD90050 state struct at `0x10004048`. After boot contains non-coefficient data (`0x00550000`, `0x00A50055`, …) that is NOT identity and NOT Q4.28 biquad coefficients. Unicorn emulation suggested `FUN_082bb5d8` reads from path 2, but device tests show path 1 (heap) matches NVDM content. The actual power-off save source buffer remains unconfirmed. |
| `FUN_082bb5d8` is optimizer completion handler, not power-off save | Firmware-verified | 2026-11-20: Full headless decompilation (5406 functions) shows `FUN_082bb5d8` is called only from `FUN_08255878` and from the optimizer VP event handler `FUN_08259090` when it receives message `0x3c` ("generation end"). It reads a 40-byte optimizer result struct, byte-swaps 10 words LE→BE, sends them to CXD90050 and writes NVDM immediately. The power-off batch save is a separate path: `FUN_08240bf4` → `FUN_08255878(1)` → `FUN_082bb4b8`. Neither path reads the NCOPTSET/WRITE_ADDRESS buffers. |
| RACE page-read response layout | Device-verified | 270-byte frame: 6-byte header + 8 bytes (status `0x00`, …, address) + 256 bytes of data. Data starts at byte 14. |
| RACE READ_ADDRESS (`0x1680`) reads 4 bytes from any memory address | Device-verified | Live test 2026-10-26 on firmware 2.5.0. Read SRAM at `0x04000000`–`0x0400000C` (vector table: SP=`0x04004000`, Reset=`0x081B9001`, NMI=`0x100001C9`, HardFault=`0x100001D7`). Payload: `\x00\x00` + LE address. Response: 6-byte header + ret(1) + unk(2) + addr(4) + data(4). |
| RACE WRITE_ADDRESS (`0x1681`) writes 4 bytes to an address | **Device-verified** | Live test 2026-10-26: wrote `0xCAFEBABE` to coefficient buffer word[1] at `0x1001A7BC`, readback returned `0xBEBAFECA` (LE representation of the BE value). Original zero restored and verified. Earlier test at `0x04080F00` failed because that address is outside SRAM. |
| ATCI tunnel (`0x0F92`) on retail firmware 2.5.0 | **Dead** | Live test 2026-10-26: `AT\r\n` and `ATI\r\n` sent over `0x0F92` produced no response. The `AT+HWPTOOL=COEFSET` path is not viable on v2.5.0 via this opcode. |
| RACE relay mode (`0x020C`) enables ATCI | Device-verified | Live test 2026-10-26: sending `0x020C` returns success (`00`), after which `0x0A00` ATCI tunnel commands return `00 00 XX` instead of error `0x11`. Before relay: `0x0A00` returns error code `0x11`. After relay: AT commands accepted. Tested `AT`, `ATI`, `AT+HWPTOOL=COEFSET`, `AT+HWPTOOL=COEFDUMP`, `AT+NCOPTTOOL=NCOPTDUMP`, `AT+NCOPTTOOL=NCOPTSET`, `AT+CXD90050=REG_READ,0x0000`. All return only 3-byte payloads; no indication packets follow. Relay disable is `0x020D`. |
| RACE NVDM write (`0x0E02`) | **Wrong opcode / DSP handler** | 2026-11-20: `0x0E02` is handled by `FUN_0822A1F4` and maps to `am_audio_set_resume` (DSP realtime resume); `0x0E01` maps to `am_audio_set_pause`. It has no payload and cannot write NVDM. In a public FOTA capture the byte pair `0E 02` also appears as the little-endian length field, which is why earlier tests looked plausible. The flash write opcode is `0x0402`. |
| RACE `PAGE_PROGRAM` with `head=0x05` | **Accepted but no write** | 2026-11-20: on a WH-1000XM4, `0x0402` with standard head `0x05` returns `rc=0` for storage types 0–4, but an all-`0xFF` free page written with an `0xA5` pattern stayed `0xFF` on immediate readback. With FOTA head `0x15` outside an FOTA session the device returns `00 80 04`. FOTA sessions are refused on this unit at 30% battery (reason code 4). |
| RACE NVKEY write (`0x0A01`) | **Device-verified (NVKEY store only)** | 2026-11-20: `tools/nvkey_write_probe.py --noop 0x1000` read an AB15 NVKEY, wrote the identical bytes back and read it again unchanged (write response `00`). So a persistent write primitive exists on the XM4, but it is confined to group `AB15` / hex key IDs; `config/*` items are a different store and cannot be reached this way. Format: `<keyID:2 LE><data…>`, cmd `0x0A01`. |
| RACE PAGE_PROGRAM (`0x0402`) for all storage types | **Blocked** | Live test 2026-10-26: tested storage_type 0 through 4 at various addresses. All return `rc=0x0A`. Flash writing is completely blocked via RACE on retail firmware. |
| NVDM is NOT in main NOR flash | Device-verified | Live test 2026-10-26: full 4 MB NOR scan (1024 × 4 KB blocks, `storage_type=0x00`) found zero NVDM blocks (no `NVDM` magic). NVDM uses its own virtual address space (`storage_type=0x01`, starting at `0x00000000`). The physical storage is likely AB1562 internal flash, not the external NOR. |
| READ_ADDRESS/WRITE_ADDRESS regression | **Restriction no longer present** | 2026-10-26/27: reads at non-base addresses returned `rc=3`. 2026-11-20: `0x1680` READ_ADDRESS works again at `0x1001A7B8`, `0x1001A7BC` and `0x10004048`; the coefficient heap buffer reads back as ten identity Q4.28 words. The earlier restriction was transient, not a permanent firmware lockout. |
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
| Ghidra headless decompilation of section 4 (main ARM firmware) | Device-verified (static analysis) | Ghidra headless on the 1.69 MB section 4 binary (`0x081B9000` base, ARM:LE:32:Cortex Thumb). Fully decompiled: 5406 functions. See decompile findings below. |

### Ghidra decompile findings (v2.5.0 section 4)

Firmware function call chain for optimizer coefficient handling, from Ghidra decompilation:

| Function | Address | Role | Key detail |
|---|---|---|---|
| `FUN_082bb468` | `0x082bb468` | Read `optimizer_coeff` from NVDM | Reads 40 bytes (0x28) from NVDM group `"config"` / item `"optimizer_coeff"`, calls CXD90050 set |
| `FUN_082bb5d8` | `0x082bb5d8` | Optimizer result → CXD90050 + NVDM write | Reads 10 words at `FUN_081c8c34(msg)+0x10`, byte-swaps LE→BE, sends to CXD90050, writes 40 bytes to NVDM at generation end. `FUN_081c8c34` is a DSP1→MCU address translator, not a fixed global, so the source is a DSP1 message payload. |
| `FUN_082bb4b8` | `0x082bb4b8` | Power-off **delete** of `config/optimizer_coeff` | Called from `FUN_08255878(1)` via `FUN_08240bf4`. Assembly shows only `"config"`/`"optimizer_coeff"` passed to `FUN_081d32d4` (nvdm delete); no 40-byte buffer is read. It then calls `FUN_082a144c(0)` to reset the DSP to defaults. This, not a save, explains all failed RAM-marker power-cycle tests. |
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
| ATCI `AT+CXD90050=NC_COEFFICIENT_SET` | **Accepted, no persistent effect** | 2026-11-20: the subcommand table at `0x082FA2C4` has `NC_COEFFICIENT_SET` (idx 127, handler `0x0821A008`, maxlen 10) and `NC_OPTIMIZE_BATCH` (idx 124). `AT+CXD90050=NC_COEFFICIENT_SET,0x…` ×10 returned `0x4F` (OK), but READ_ADDRESS at `0x1001A7B8` still showed identity, and `config/optimizer_coeff` in NVDM stayed identity. `NC_OPTIMIZE_BATCH` returned `0x30` and wrote nothing. DSP state was restored to identity after the test. |
| ATCI NCOPTSET via relay mode | **Accepted + RAM write confirmed, NVDM persistence failed** | `AT+NCOPTTOOL=NCOPTSET,<offset>,0x<value>` accepted for all offsets/values. NCOPTGET state change confirms RAM write. Power cycle tests (2026-10-28): NVDM always returns identity after power cycle regardless of NCOPTSET state. NCOPTSET writes to DSP path, not the NVDM save path. |
| RAM WRITE_ADDRESS (`0x1681`) injection → NVDM | **Failed (3 attempts)** | 2026-10-28: Markers written to `0x10004058` (struct+0x10), `0x1001A7B8` (heap), and both simultaneously, with and without `optimizer_is_optimized` flag. All three power-cycle tests showed NVDM unchanged (identity). The full decompilation shows these buffers are not the source of either NVDM write path. |
| DSP realtime (`0x0CD1`–`0x0CD7`) | **Not viable** | BLE/connection management, not audio DSP. |
| 0x09xx RACE sub-commands on XM4 | **True stub** | 2026-11-20: the 0x09xx handler `FUN_0822B3B8` only logs `hdr_id` and returns; it never parses a payload or allocates a response. The table at `0x0834DB94` cited earlier is a pool of logging format strings, not a subcommand dispatch table. |

**Next steps to resolve (updated 2026-11-20):**
1. ~~Full firmware decompilation~~ — done: 5406 functions. The power-off batch path is `FUN_08240bf4` → `FUN_08255878(1)` → `FUN_082bb4b8`; the optimizer completion path is `FUN_08259090` → `FUN_082bb5d8`.
2. **Trigger a real NC Optimizer run with NVDM readback** — `tools/optimizer_trigger.py --start` sends MDR `84 01 00 01`, follows the `0x85` status frames and dumps NVDM before/after. This distinguishes "optimizer wrote identity" from "no write happened".
3. **Target the optimizer result struct** — `FUN_082bb5d8` reads `FUN_081c8c34(param_1) + 0x10`, not the NCOPTSET buffer. Modifying that struct during an optimizer run is the remaining path to a persistent custom coefficient.
4. **Direct NVDM flash write** — Serialize a properly formatted NVDM record with custom coefficients, find free space in the log-structured store, write via PAGE_PROGRAM. Previous attempt failed (`rc=0x0A`), but PAGE_PROGRAM with `storage_type=1` has not been tested.

## ANC calibration (`inject`, `verify`, `rollback`)

| Claim | Level | Evidence / note |
|---|---|---|
| `DNC/ALIGN_FF_L/R`, `DNC/ALIGN_FB_L/R` are 108-byte blocks holding five Q4.28 biquads | **Contradicted** | In a real dump these items are in group `CXD90050`, not `DNC`, and each value is 8 bytes of ASCII hex text. |
| `DNC/HOWLING_PRE_PARAM`, `DNC/HOWLING_DET_PARAM`, `DNC/OPTIMIZER_PARAM` exist | **Contradicted** | None of these items exists in the dump. A 1-byte `DNC/howling_pre_notice_off` exists. |
| A 40-byte `config/optimizer_coeff` item exists; it holds NC Optimizer output | Device-verified | Firmware strings: `[AC][optimizer] generation end! %x` ×10 and "Cannot write optimizer coeff to nvdm". Two older, deleted records hold real values, decoded as 2 stages of signed big-endian Q4.28; this layout is an interpretation. All active records, including the one on 2026-11-20 after all write experiments, are identity. The item is absent from the 79-item MDR whitelist and ATCI ENVDM writes do not reach it, so a persistent custom coefficient is still not achieved. |
| Starting NC Optimizer over MDR (`84 01 00 01`) | Device-verified | 2026-10-26 on firmware 2.5.0: status messages `0x85` went WEARING_CONDITION → ATMOSPHERIC_PRESSURE → ANALYZING → FINISHED → NOT_RUNNING in about 15 s (status names from Gadgetbridge). Pressure reported 1.0 atm. `tools/optimizer_trigger.py` reproduces the start/status sequence and adds NVDM before/after dumps; its dry-run path is covered by tests, but the full run has not been executed on a device. |
| NC Optimizer result is stored immediately | **Unresolved** | Nothing in the NVDM changed after the 2026-10-26 run; after a power cycle `optimizer_coeff` was rewritten, still identity. Static analysis shows a write at generation end (`FUN_082bb5d8`), so an identity result would be indistinguishable from no write. `tools/optimizer_trigger.py` is the device test for this. |
| When `optimizer_coeff` is written | Firmware-verified | Two paths: (1) generation end, `FUN_08259090` → `FUN_082bb5d8`, writes the optimizer result struct to NVDM; (2) power off, `FUN_08240bf4` → `FUN_08255878(1)` → `FUN_082bb4b8`, writes firmware state as the first item of the batch (sequence numbers directly before volume, ANC mode, `nc_atm_value`, `nc_is_optimized`, `power_off_reason`, …). Neither path reads the buffers written by NCOPTSET or WRITE_ADDRESS. `nc_is_optimized` is `"1"` in all 32 records. |
| NVKEY read with RACE `0x0A00` | Device-verified | Four `AB15/<key>` items read live matched the dump byte for byte. Command format from [lx-exe/stealth600-gen3-usb-fix](https://github.com/lx-exe/stealth600-gen3-usb-fix). `config/*` items are not NVKEYs; a RACE command that writes them is not known yet. |
| Writing a new `config/optimizer_coeff` over RACE works | **Contradicted** | The only device attempt (2026-10-25) failed its readback check (`Readback mismatch at 0x00021E00`). The active value in a later dump is still the identity value. |
| "+6.5 to +7.5 dB extra low-frequency noise reduction" | Code/simulation | This is the simulated magnitude response of the synthesized filter. It is not an ANC measurement. Real noise reduction depends on the acoustic paths, which were never measured. |
| A bad NVDM checksum cannot bootloop the headset (ROM fallback) | Unverified | No source or test. |
| Rollback restores the dump "bit-exact" | Unverified | The rollback path only issues page-program commands. NOR flash cannot turn 0 bits back into 1 without an erase, and the emulator in `virtual_device.py` hides this. |
| `AT+HWPTOOL=COEFSET` / `AT+ENVDM` ATCI commands | **Dead on v2.5.0 via 0x0F92; alive via relay mode with correct format** | `0x0F92` produces no response on retail v2.5.0. RACE relay mode (`0x020C`) enables ATCI via `0x0A00`. **Critical format discovery from firmware binary strings (v2.5.0):** previous sessions sent raw hex blobs (`AT+HWPTOOL=COEFSET,<40-byte-hex>`) which was WRONG. Correct formats: `AT+NCOPTTOOL=NCOPTSET,<offset>,0x<value>` (decimal offset, hex value; fw string at `0x082F4B26`: `%d,0x%x`), `AT+NCOPTTOOL=NCOPTGET` → `DSP1_NCOPT_DATA={...}`, `AT+NCOPTTOOL=NCOPTDUMP` → `DSP1_NCOPT_ALL_DATA={...}`, `AT+HWPTOOL=COEFSET,<filter_id>,<stage_idx>,<b0>,<b1>,<b2>,<a1>,<a2>` (fw string at `0x082F3F60`: `iir_coef[%d][%d]`). |
| NCOPTSET with correct format | **Device-verified: accepted, RAM state change confirmed** | 2026-10-27: `AT+NCOPTTOOL=NCOPTSET,<offset>,0x<value>` returns `0x54` ('T') or `0x4F` ('O') for ALL offsets tested (0–49, negatives, up to 1000). All values accepted (identity, markers, zero, max). Offsets 5 and 13 sometimes return `0x00` instead. **Key proof: NCOPTGET returns `0x00` (null/no data) BEFORE NCOPTSET, changes to `0x54` ('T'/data present) AFTER NCOPTSET.** This state change proves NCOPTSET modifies firmware internal state. NVDM unchanged immediately after NCOPTSET — consistent with Ghidra finding that NCOPTSET writes to RAM buffer, not NVDM. Power cycle needed to trigger `FUN_082bb5d8` (RAM → NVDM save). |
| HWPTOOL COEFSET with correct format | **Device-verified: accepted** | 2026-10-27: `AT+HWPTOOL=COEFSET,<filter>,<stage>,<b0>,<b1>,<b2>,<a1>,<a2>` returns `0x54` for all 20 combinations (4 filters × 5 stages). |
| ENVDM read/write via ATCI | **Write contradicted on XM4** | 2026-11-20: the exact four-token form decoded from `FUN_082143fc` was sent in a fresh relay session: `AT+ENVDM=W,config,optimizer_coeff,B,<80 hex digits>` with a marker pattern. The command returned status `0x00`, but a full NVDM dump afterwards still held the identity value. Earlier attempts with other token layouts also left `config/test_vp_disable` unchanged. ATCI ENVDM cannot be used to write this firmware's NVDM over the RACE relay. |
| MDR tandem-test `0xFF/6` NVDM write | **Device-verified (persistent)** | MDR dispatcher `FUN_082483AC` handles code `0xFF` subcommand `6`. Only flag values `1` (read) and `3` (write) are accepted; flag `2` is a NACK/no-op. Write payload: `FF 06 00 01 00 00 03 <group_len><group><item_len><item><value_len:2 BE><value>`. `FUN_0824B6CC` checks a 79-item whitelist at `0x0831CCBC`, calls `nvdm_write(group,item,2,value,len)` and then `FUN_0824B420(idx)` to refresh the settings RAM cache, so the new value survives the power-off batch. Live test 2026-11-20: `config/test_vp_disable` `0`→`1`, `config/beep_guidance` `1`→`0`, `config/test_auto_power_off` `1`→`0`, each confirmed by both MDR read and a fresh 512 KiB NVDM dump. `config/optimizer_coeff` is **not** whitelisted. Implemented in `tools/mdr_nvdm_write.py`. |
| Settings cache / power-off save lifecycle | Firmware-verified | Three layers: settings RAM globals (e.g. `test_vp_disable` at `0x1007473d`), an NVDM write-back RAM cache (`0x10067c30`), and flash. `nvdm_read` (`FUN_081d3148`) checks the RAM cache before flash; MDR GET uses it too. Most SET handlers only change the RAM global. `FUN_0824B514(idx)` flushes one entry; `idx=0x4f` means all. `FUN_08240538` ("bt_sink_app_enter_power_off") and `FUN_08235508` case `0x0f` call `FUN_0824B514(0x4f)` + `FUN_081d3ed0()` to write every cached setting to flash at power-off. A raw `nvdm_write` that bypasses `FUN_0823F188` (e.g. ATCI `AT+ENVDM=W`) does not refresh the global and can be reverted by that batch. The 0xFF/6 flag-3 path avoids this by reloading the cache; it can still be gated by `0x100737F0`. |

Code facts about the old write paths (confirmed by reading the code after a user report, 2026-10-26):

- `inject` reads the NVDM through raw flash reads (`storage_type=0`) at `0x000DF000`. On a real headset this returns error `0x03` before anything is written.
- The injector updates an item in place only when the new value has the same size as the old one. The real `ALIGN_*` values are 8 bytes, the injector's are 108 bytes, so it would fall back to re-serializing every active item. It also requires a 128 KB image, while a real backup is 512 KiB.
- `rollback` accepts only 128 KB images, so it rejects a real 512 KiB backup.
- `--dry-run --image <real dump>` finds no 108-byte `ALIGN_*` block, falls back to an identity filter, and prints the same table as the README example. The "+7.5 dB" is the computed gain of the synthesized filter, never a measurement on a unit.

Because of these findings, the CLI refuses `inject` and `rollback` on real hardware unless `--allow-unverified-write` is given. `--mock` still runs them against the emulator.

## Firmware flashing

| Claim | Level | Evidence / note |
|---|---|---|
| Official firmware v2.5.0 can be flashed over Bluetooth with `tools/flash_firmware.py`, downgrading from v3.0.1 | Device-verified | 2026-10-25: 5,452 pages (1,395,504 bytes) programmed, integrity check `0x1C01` passed, commit `0x1C02`, device rebooted. 2026-10-26, second unit, Windows 11 with Python's native `AF_BLUETOOTH`: same result after the fix below. |
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
