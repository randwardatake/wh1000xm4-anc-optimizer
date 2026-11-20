"""Virtual XM4 Mock Device Transport and Flash Emulator.

Provides an in-memory simulation of the Sony WH-1000XM4 Bluetooth RFCOMM
subsystem (Channel 11 MDR and Channel 21 RACE/ATCI), NVDM flash partition,
CXD90050 DSP registers, and NCOPTSET/NCOPTGET commands.
"""

from __future__ import annotations

import struct
from typing import Dict, List, Optional

from xm4_optimizer.nvdm.cxd90050 import (
    CXD90050AlignmentBlock,
    FILTER_ID_ALIGN_FB_L,
    FILTER_ID_ALIGN_FB_R,
    FILTER_ID_ALIGN_FF_L,
    FILTER_ID_ALIGN_FF_R,
    HowlingDetectionParam,
    HowlingPreventionParam,
    OptimizerCoefficient,
    OPTIMIZER_COEFF_IDENTITY,
    OptimizerParam,
    OPTIMIZER_COEFF_WORDS,
)
from xm4_optimizer.nvdm.item import serialize_item_record
from xm4_optimizer.nvdm.parser import parse_nvdm_image
from xm4_optimizer.transport.protocol_mdr import (
    END_MARKER,
    MdrError,
    MdrPacket,
    MdrPacketType,
    START_MARKER,
)
from xm4_optimizer.transport.protocol_race import (
    compute_fota_checksum,
    FLASH_PAGE_SIZE,
    RACE_HEADER_FORMAT,
    RaceHead,
    RaceHeaderType,
    RaceOpcode,
    RaceReturnCode,
)

# Flash Geometry Constants
NVDM_BASE_ADDR: int = 0x000DF000
NVDM_PARTITION_SIZE: int = 512 * 1024  # 524,288 bytes (512 KB)
NVDM_PAGE_SIZE: int = 256
NVDM_PEB_SIZE: int = 4096
NVDM_PEB_COUNT: int = 128

PEB_MAGIC_NVDM: int = 0x4D44564E  # "NVDM" in ASCII LE
PEB_STATUS_ACTIVED: int = 0xE0
PEB_STATUS_EMPTY: int = 0xFE
DATA_ITEM_STATUS_VALID: int = 0xFC
DATA_ITEM_STATUS_DELETE: int = 0xF8


def bkdr_hash(group: str, name: str) -> int:
    """Computes 32-bit polynomial rolling BKDR hash over group and item name."""
    s = (group + name).encode("ascii")
    h, a, b = 0, 63689, 378551
    for char in s:
        h = (h * a + char) & 0xFFFFFFFF
        a = (a * b) & 0xFFFFFFFF
    return h


def compute_nvdm_interleaved_checksum(data: bytes) -> int:
    """Computes 16-bit interleaved accumulator checksum:

    low byte = sum of even indexed bytes & 0xFF
    high byte = sum of odd indexed bytes & 0xFF
    """
    even_sum = sum(data[0::2]) & 0xFF
    odd_sum = sum(data[1::2]) & 0xFF
    return (odd_sum << 8) | even_sum


class VirtualXM4Device:
    """In-memory mock device implementing DeviceTransport protocol."""

    def __init__(self, populate_defaults: bool = True):
        self.is_connected: bool = False
        self.flash: bytearray = bytearray(b"\xff" * NVDM_PARTITION_SIZE)
        self.dsp_registers: Dict[str, List[int]] = {
            "ALIGN_FF_L": [0, 0, 1 << 28, 0, 0] * 5,
            "ALIGN_FF_R": [0, 0, 1 << 28, 0, 0] * 5,
            "ALIGN_FB_L": [0, 0, 1 << 28, 0, 0] * 5,
            "ALIGN_FB_R": [0, 0, 1 << 28, 0, 0] * 5,
        }
        self.ncopt_data: Optional[List[int]] = None
        # DSP heap buffer at 0x1001A7B8. NCOPTSET may update it, but neither the
        # power-off batch save nor the optimizer completion handler reads it.
        self.optimizer_ram_buffer: List[int] = list(OPTIMIZER_COEFF_IDENTITY)
        # Source of the power-off batch save (FUN_08240bf4 -> FUN_08255878(1) ->
        # FUN_082bb4b8). NCOPTSET does not touch it; only the optimizer result does.
        self.power_off_save_buffer: List[int] = list(OPTIMIZER_COEFF_IDENTITY)
        self.optimizer_is_optimized: bool = False
        self._ncoptset_updates_ram: bool = True
        # Fault Injection Flags
        self.fail_read: bool = False
        self.fail_write: bool = False
        self.corrupt_fota_crc: bool = False
        self.disconnect_on_write: bool = False
        self.write_count: int = 0
        self.read_count: int = 0

        if populate_defaults:
            self._seed_default_nvdm_partition()

    def connect(self) -> None:
        """Simulates establishing RFCOMM connection."""
        self.is_connected = True

    def close(self) -> None:
        """Simulates closing RFCOMM connection."""
        self.is_connected = False

    # --- DeviceTransport Protocol Implementation ---

    def read_flash_page(self, address: int) -> bytes:
        """Reads 256 bytes from NOR flash at 256-byte aligned address."""
        if not self.is_connected:
            raise ConnectionError("Device is not connected")
        if self.fail_read:
            raise IOError("Simulated flash read hardware error (RACE code 0x01)")
        if address < NVDM_BASE_ADDR or address + NVDM_PAGE_SIZE > (
            NVDM_BASE_ADDR + NVDM_PARTITION_SIZE
        ):
            raise ValueError(f"Flash address 0x{address:08X} out of partition bounds")
        if address % NVDM_PAGE_SIZE != 0:
            raise ValueError(
                f"Flash address 0x{address:08X} is not 256-byte aligned"
            )

        self.read_count += 1
        offset = address - NVDM_BASE_ADDR
        return bytes(self.flash[offset : offset + NVDM_PAGE_SIZE])

    def write_flash_page(self, address: int, data: bytes) -> bool:
        """Writes 256 bytes to NOR flash with CRC-8 fota_checksum validation."""
        if not self.is_connected:
            raise ConnectionError("Device is not connected")
        if self.disconnect_on_write:
            self.is_connected = False
            raise ConnectionResetError(
                "Connection reset by peer during staged flash write"
            )
        if self.fail_write:
            return False
        if len(data) != NVDM_PAGE_SIZE:
            raise ValueError(
                f"Data length must be exactly {NVDM_PAGE_SIZE} bytes, got {len(data)}"
            )
        if address < NVDM_BASE_ADDR or address + NVDM_PAGE_SIZE > (
            NVDM_BASE_ADDR + NVDM_PARTITION_SIZE
        ):
            raise ValueError(f"Flash address 0x{address:08X} out of partition bounds")
        if address % NVDM_PAGE_SIZE != 0:
            raise ValueError(
                f"Flash address 0x{address:08X} is not 256-byte aligned"
            )

        calc_crc = compute_fota_checksum(data)
        if self.corrupt_fota_crc:
            return False  # Firmware rejects with checksum mismatch

        self.write_count += 1
        offset = address - NVDM_BASE_ADDR
        self.flash[offset : offset + NVDM_PAGE_SIZE] = data
        return True

    def send_atci_command(self, cmd: str, timeout_ms: int = 3000) -> str:
        """Executes Hayes ATCI command string and returns response."""
        if not self.is_connected:
            raise ConnectionError("Device is not connected")

        cmd = cmd.strip()
        if cmd == "AT":
            return "\r\nOK\r\n"

        if cmd.startswith("AT+HWPTOOL=COEFSET"):
            # Syntax: AT+HWPTOOL=COEFSET,<target>,<filter_idx>,<a1>,<a2>,<b0>,<b1>,<b2>
            parts = cmd.split(",")
            if len(parts) >= 8:
                try:
                    target_idx = int(parts[1])
                    stage_idx = int(parts[2])
                    if not (0 <= target_idx < 4 and 0 <= stage_idx < 5):
                        return "\r\n+HWPTOOL: ERROR,INVALID_PARAMS\r\n\r\nERROR\r\n"
                    coeffs = [int(p, 16) for p in parts[3:8]]
                    target_name = ["ALIGN_FF_L", "ALIGN_FF_R", "ALIGN_FB_L", "ALIGN_FB_R"][
                        target_idx
                    ]
                    start_idx = stage_idx * 5
                    self.dsp_registers[target_name][start_idx : start_idx + 5] = coeffs
                    return "\r\n+HWPTOOL: COEFSET,OK\r\n\r\nOK\r\n"
                except Exception:
                    return "\r\n+HWPTOOL: ERROR,INVALID_PARAMS\r\n\r\nERROR\r\n"
            return "\r\n+HWPTOOL: ERROR,INVALID_PARAMS\r\n\r\nERROR\r\n"

        if cmd.startswith("AT+HWPTOOL=COEFGET"):
            parts = cmd.split(",")
            if len(parts) >= 3:
                try:
                    target_idx = int(parts[1])
                    stage_idx = int(parts[2])
                    if not (0 <= target_idx < 4 and 0 <= stage_idx < 5):
                        return "\r\n+HWPTOOL: ERROR,INVALID_PARAMS\r\n\r\nERROR\r\n"
                    target_name = ["ALIGN_FF_L", "ALIGN_FF_R", "ALIGN_FB_L", "ALIGN_FB_R"][
                        target_idx
                    ]
                    start_idx = stage_idx * 5
                    coeffs = self.dsp_registers[target_name][start_idx : start_idx + 5]
                    coeff_str = ",".join(f"0x{c:08X}" for c in coeffs)
                    return f"\r\n+HWPTOOL: COEFGET,{target_idx},{stage_idx},{coeff_str}\r\n\r\nOK\r\n"
                except Exception:
                    return "\r\nERROR\r\n"

        if cmd.startswith("AT+NCOPTTOOL=NCOPTSET"):
            parts = cmd.split(",")
            if len(parts) >= 3:
                try:
                    offset = int(parts[1])
                    value = int(parts[2], 16) if parts[2].startswith("0x") else int(parts[2])
                    if self.ncopt_data is None:
                        self.ncopt_data = [0] * OPTIMIZER_COEFF_WORDS
                    if 0 <= offset < OPTIMIZER_COEFF_WORDS:
                        self.ncopt_data[offset] = value & 0xFFFFFFFF
                        self.optimizer_is_optimized = True
                        if self._ncoptset_updates_ram:
                            self.optimizer_ram_buffer[offset] = value & 0xFFFFFFFF
                    return "\r\nOK\r\n"
                except Exception:
                    return "\r\nERROR\r\n"
            return "\r\nERROR\r\n"

        if cmd.startswith("AT+NCOPTTOOL=NCOPTGET"):
            if self.ncopt_data is not None:
                return "\r\n+NCOPTTOOL: DSP1_NCOPT_DATA=T\r\n\r\nOK\r\n"
            return "\r\n+NCOPTTOOL: DSP1_NCOPT_DATA=\r\n\r\nOK\r\n"

        if cmd.startswith("AT+NCOPTTOOL=NCOPTDUMP"):
            if self.ncopt_data is not None:
                vals = ",".join(f"0x{w:08X}" for w in self.ncopt_data)
                return f"\r\n+NCOPTTOOL: DSP1_NCOPT_ALL_DATA={vals}\r\n\r\nOK\r\n"
            return "\r\n+NCOPTTOOL: DSP1_NCOPT_ALL_DATA=\r\n\r\nOK\r\n"

        if cmd.startswith("AT+ENVDM=W"):
            return "\r\n+ENVDM: W,OK\r\n\r\nOK\r\n"

        return "\r\nERROR\r\n"

    # --- Wire Protocol Simulation (RACE & MDR) ---

    def process_race_packet(self, packet: bytes) -> bytes:
        """Processes raw RACE binary frame and returns raw response frame."""
        if len(packet) < 6:
            raise ValueError("RACE packet too short")
        head, type_, length, cmd_id = struct.unpack(RACE_HEADER_FORMAT, packet[:6])
        payload = packet[6 : 4 + length]

        if cmd_id == RaceOpcode.PAGE_READ:
            storage_type, page_shift, address = struct.unpack("<BBI", payload[:6])
            try:
                page_data = self.read_flash_page(address)
                resp_payload = struct.pack("<BI", 0x00, address) + page_data
                resp_hdr = struct.pack(
                    RACE_HEADER_FORMAT, RaceHead.STANDARD, RaceHeaderType.RESPONSE, len(resp_payload) + 2, RaceOpcode.PAGE_READ
                )
                return resp_hdr + resp_payload
            except Exception:
                err_payload = struct.pack("<BI", 0x01, address)
                resp_hdr = struct.pack(
                    RACE_HEADER_FORMAT, RaceHead.STANDARD, RaceHeaderType.RESPONSE, len(err_payload) + 2, RaceOpcode.PAGE_READ
                )
                return resp_hdr + err_payload

        elif cmd_id == RaceOpcode.PAGE_PROGRAM:
            if len(payload) < 7:
                err_payload = struct.pack("<BI", RaceReturnCode.INVALID_PARAM, 0)
                resp_hdr = struct.pack(
                    RACE_HEADER_FORMAT,
                    RaceHead.STANDARD,
                    RaceHeaderType.RESPONSE,
                    len(err_payload) + 2,
                    RaceOpcode.PAGE_PROGRAM,
                )
                return resp_hdr + err_payload

            storage_type, num_pages, fota_crc, address = struct.unpack("<BBBI", payload[:7])
            page_data = payload[7 : 7 + 256]
            calc_crc = compute_fota_checksum(page_data)
            if calc_crc != fota_crc or self.corrupt_fota_crc:
                resp_payload = struct.pack("<BI", 0x02, address)  # Checksum error
            else:
                try:
                    ok = self.write_flash_page(address, page_data)
                    ret_code = RaceReturnCode.SUCCESS if ok else RaceReturnCode.INVALID_PARAM
                except (ConnectionError, ConnectionResetError):
                    raise
                except ValueError as exc:
                    if "aligned" in str(exc).lower():
                        ret_code = RaceReturnCode.ADDR_MISALIGNED  # 0x04
                    else:
                        ret_code = RaceReturnCode.INVALID_PARAM  # 0x01
                except Exception:
                    ret_code = RaceReturnCode.INVALID_PARAM  # 0x01
                resp_payload = struct.pack("<BI", int(ret_code), address)
            resp_hdr = struct.pack(
                RACE_HEADER_FORMAT, RaceHead.STANDARD, RaceHeaderType.RESPONSE, len(resp_payload) + 2, RaceOpcode.PAGE_PROGRAM
            )
            return resp_hdr + resp_payload

        elif cmd_id == RaceOpcode.ATCI_TUNNEL:
            cmd_str = payload.decode("ascii", errors="ignore")
            resp_str = self.send_atci_command(cmd_str)
            resp_payload = resp_str.encode("ascii")
            resp_hdr = struct.pack(
                RACE_HEADER_FORMAT, RaceHead.STANDARD, RaceHeaderType.RESPONSE, len(resp_payload) + 2, RaceOpcode.ATCI_TUNNEL
            )
            return resp_hdr + resp_payload

        # Default unknown command response
        err_payload = struct.pack("<B", 0xFF)
        return (
            struct.pack(RACE_HEADER_FORMAT, RaceHead.STANDARD, RaceHeaderType.RESPONSE, len(err_payload) + 2, cmd_id)
            + err_payload
        )

    def process_mdr_packet(self, packet: bytes) -> bytes:
        """Processes raw MDR packet with delimiters and escaping."""
        if len(packet) < 2 or packet[0] != START_MARKER or packet[-1] != END_MARKER:
            raise ValueError("Invalid MDR framing delimiters")

        pkt, remaining = MdrPacket.from_wire_bytes(packet)
        if pkt is None or remaining:
            raise ValueError("Invalid MDR packet framing or incomplete packet")

        # Send ACK (Type=0x02)
        return self._format_mdr_frame(int(MdrPacketType.ACK), pkt.seq_num, b"")

    def _format_mdr_frame(self, pkt_type: int, seq: int, payload: bytes) -> bytes:
        """Builds framed and escaped MDR response packet."""
        inner = struct.pack(">BBI", pkt_type, seq, len(payload)) + payload
        cksum = sum(inner) & 0xFF
        inner += bytes([cksum])

        escaped = bytearray([0x3E])
        for b in inner:
            if b in (0x3C, 0x3D, 0x3E):
                escaped.extend([0x3D, (b - 0x10) & 0xFF])
            else:
                escaped.append(b)
        escaped.append(0x3C)
        return bytes(escaped)

    # --- Seed Helper ---

    def _seed_default_nvdm_partition(self) -> None:
        """Pre-populates PEB 0 with valid NVDM header and default items."""
        # PEB 0 Header at offset 0
        peb_hdr = struct.pack(
            "<IIBBBB", PEB_MAGIC_NVDM, 1, PEB_STATUS_ACTIVED, 0xF0, 0x01, 0x00
        )
        self.flash[0:12] = peb_hdr

        offset = 12
        seq = 1

        items_to_seed = [
            (
                "DNC",
                "ALIGN_FF_L",
                CXD90050AlignmentBlock(filter_id=FILTER_ID_ALIGN_FF_L).pack(),
            ),
            (
                "DNC",
                "ALIGN_FF_R",
                CXD90050AlignmentBlock(filter_id=FILTER_ID_ALIGN_FF_R).pack(),
            ),
            (
                "DNC",
                "ALIGN_FB_L",
                CXD90050AlignmentBlock(filter_id=FILTER_ID_ALIGN_FB_L).pack(),
            ),
            (
                "DNC",
                "ALIGN_FB_R",
                CXD90050AlignmentBlock(filter_id=FILTER_ID_ALIGN_FB_R).pack(),
            ),
            ("DNC", "HOWLING_PRE_PARAM", HowlingPreventionParam().pack()),
            ("DNC", "HOWLING_DET_PARAM", HowlingDetectionParam().pack()),
            ("DNC", "OPTIMIZER_PARAM", OptimizerParam().pack()),
            ("config", "optimizer_coeff", OptimizerCoefficient().pack()),
        ]

        for grp, name, val in items_to_seed:
            grp_b = grp.encode("ascii")
            name_b = name.encode("ascii")
            h = bkdr_hash(grp, name)
            hdr = struct.pack(
                "<BBHHBBHBBII",
                DATA_ITEM_STATUS_VALID,
                0,
                0,
                offset,
                len(grp_b),
                len(name_b),
                len(val),
                0,
                1,
                seq,
                h,
            )
            cksum_target = hdr[1:20] + grp_b + name_b + val
            cksum = compute_nvdm_interleaved_checksum(cksum_target)
            item_bytes = hdr + grp_b + name_b + val + struct.pack("<H", cksum)
            pad = (4 - (len(item_bytes) % 4)) % 4
            item_bytes += b"\xff" * pad

            self.flash[offset : offset + len(item_bytes)] = item_bytes
            offset += len(item_bytes)
            seq += 1
        self._next_seq = seq

    def run_nc_optimizer(self, result: OptimizerCoefficient) -> None:
        """Emulates the NC Optimizer generation-end handler (FUN_082bb5d8).

        The firmware builds a 40-word result struct, byte-swaps 10 words LE->BE,
        sends them to the CXD90050 and writes them to NVDM immediately. This is
        the only path that puts a non-identity value into config/optimizer_coeff.
        NCOPTSET values are overwritten by the optimizer result.
        """
        words = list(result.words)
        self.ncopt_data = list(words)
        self.optimizer_ram_buffer = list(words)
        self.power_off_save_buffer = list(words)
        self.optimizer_is_optimized = False
        self._append_nvdm_item(
            "config", "optimizer_coeff", OptimizerCoefficient(words=words).pack()
        )

    def power_cycle(self) -> None:
        """Emulates a power-off batch save followed by a power-on restore.

        Power-off (FUN_08240bf4 -> FUN_08255878(1) -> FUN_082bb4b8) saves the
        firmware's own state buffer. Power-on (FUN_082bb468) reads
        config/optimizer_coeff from NVDM and restores it to the DSP and RAM.
        NCOPTSET writes DSP state directly and is not part of either path, so
        marker values written only with NCOPTSET are lost on power cycle.
        """
        self.is_connected = False

        # --- Power-off: firmware state buffer -> NVDM ---
        self._append_nvdm_item(
            "config",
            "optimizer_coeff",
            OptimizerCoefficient(words=list(self.power_off_save_buffer)).pack(),
        )

        # --- Power-on: FUN_082bb468 emulation ---
        partition = parse_nvdm_image(bytes(self.flash), strict=False)
        item = partition.get_item("config", "optimizer_coeff")
        if item is not None and len(item.value) == 40:
            restored = OptimizerCoefficient.unpack(item.value)
            self.ncopt_data = list(restored.words)
            self.optimizer_ram_buffer = list(restored.words)
            self.power_off_save_buffer = list(restored.words)
        else:
            self.ncopt_data = None
            self.optimizer_ram_buffer = list(OPTIMIZER_COEFF_IDENTITY)
            self.power_off_save_buffer = list(OPTIMIZER_COEFF_IDENTITY)

        self.is_connected = True

    def _append_nvdm_item(self, group: str, name: str, value: bytes) -> None:
        """Appends a new NVDM item record to PEB 0 with a higher sequence number."""
        peb0_end = NVDM_PEB_SIZE

        # Walk item records to find the true end of data in PEB 0.
        # Each record: 20-byte header + group + name + value + 2-byte checksum, padded to 4.
        free_offset = 12  # after PEB header
        while free_offset < peb0_end - 20:
            status_byte = self.flash[free_offset]
            if status_byte == 0xFF:
                break  # empty slot — this is the real free space
            grp_sz = self.flash[free_offset + 6]
            name_sz = self.flash[free_offset + 7]
            val_sz = struct.unpack_from("<H", self.flash, free_offset + 8)[0]
            record_sz = 20 + grp_sz + name_sz + val_sz + 2
            record_sz += (-record_sz) % 4  # pad to 4
            free_offset += record_sz

        self._next_seq += 1
        record = serialize_item_record(
            group_name=group,
            item_name=name,
            value=value,
            pnum=0,
            offset=free_offset,
            sequence_number=self._next_seq,
        )

        if free_offset + len(record) > peb0_end:
            raise OverflowError(
                f"PEB 0 full: need {len(record)} bytes at offset {free_offset}, "
                f"but PEB ends at {peb0_end}"
            )

        self.flash[free_offset : free_offset + len(record)] = record
