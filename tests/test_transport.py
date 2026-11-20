"""Comprehensive Unit and Integration Tests for Transport Subsystem.

Tests Airoha RACE protocol framing, Sony MDR delimiter escaping and checksums,
Linux RFCOMM socket abstractions, and the VirtualXM4Device mock test harness.
"""

from __future__ import annotations

import struct
import pytest

from xm4_optimizer.transport.protocol_mdr import (
    compute_mdr_checksum,
    mdr_escape,
    mdr_unescape,
    MdrChecksumError,
    MdrEscapeError,
    MdrFrameError,
    MdrLengthError,
    MdrPacket,
    MdrPacketType,
    MdrStreamDecoder,
    START_MARKER,
    END_MARKER,
    ESCAPE_BYTE,
)
from xm4_optimizer.transport.protocol_race import (
    AtciTunnelRequest,
    AtciTunnelResponse,
    compute_fota_checksum,
    FLASH_PAGE_SIZE,
    PageProgramRequest,
    PageProgramResponse,
    PageReadRequest,
    PageReadResponse,
    PartitionEraseRequest,
    PartitionEraseResponse,
    RaceFramingError,
    RaceHead,
    RaceHeaderType,
    RaceLengthError,
    RaceOpcode,
    RacePacket,
    RaceReturnCode,
    RaceStorageType,
)
from xm4_optimizer.transport.rfcomm_socket import (
    DeviceDisconnectedError,
    DeviceTransport,
    FlashOperationError,
    NVDM_PARTITION_BASE,
    NVDM_PARTITION_SIZE,
    read_nvdm_partition,
    RfcommSocket,
)
from xm4_optimizer.transport.virtual_device import VirtualXM4Device


class TestRaceProtocol:
    """Tests Airoha RACE packet framing, serialization, and CRC-8 checksums."""

    def test_race_packet_pack_unpack(self) -> None:
        payload = b"HelloRACE123"
        packet = RacePacket(
            head=RaceHead.STANDARD,
            type=RaceHeaderType.CMD_EXPECTS_RESPONSE,
            cmd_id=RaceOpcode.PAGE_READ,
            payload=payload,
        )
        wire_bytes = packet.to_bytes()
        assert len(wire_bytes) == 6 + len(payload)

        unpacked, remaining = RacePacket.from_bytes(wire_bytes)
        assert remaining == b""
        assert unpacked.head == RaceHead.STANDARD
        assert unpacked.type == RaceHeaderType.CMD_EXPECTS_RESPONSE
        assert unpacked.cmd_id == RaceOpcode.PAGE_READ
        assert unpacked.payload == payload

    def test_race_read_page_command(self) -> None:
        addr = 0x000DF000
        req = PageReadRequest(address=addr)
        pkt = req.to_packet()
        assert pkt.cmd_id == RaceOpcode.PAGE_READ
        assert pkt.head == RaceHead.STANDARD
        assert pkt.type == RaceHeaderType.CMD_EXPECTS_RESPONSE

        storage_type, page_shift, req_addr = struct.unpack("<BBI", pkt.payload)
        assert storage_type == RaceStorageType.NOR_FLASH
        assert page_shift == 1
        assert req_addr == addr

        # Test PageReadResponse unpacking
        resp_payload = struct.pack("<BI", 0x00, addr) + (b"\xAA" * FLASH_PAGE_SIZE)
        resp = PageReadResponse.from_payload(resp_payload)
        assert resp.return_code == RaceReturnCode.SUCCESS
        assert resp.address == addr
        assert resp.data == b"\xAA" * FLASH_PAGE_SIZE

    def test_race_read_page_unaligned_raises(self) -> None:
        with pytest.raises(ValueError, match="aligned to 256 bytes"):
            PageReadRequest(address=0x000DF001).to_packet()

    def test_race_program_page_command(self) -> None:
        addr = 0x000DF100
        data = bytes(range(256))
        req = PageProgramRequest(address=addr, data=data)
        pkt = req.to_packet()

        assert pkt.cmd_id == RaceOpcode.PAGE_PROGRAM
        assert pkt.head == RaceHead.FOTA
        assert pkt.type == RaceHeaderType.CMD_EXPECTS_RESPONSE
        assert len(pkt.to_bytes()) == 6 + 7 + 256

        storage_type, count, fota_crc, req_addr = struct.unpack("<BBBI", pkt.payload[:7])
        assert storage_type == RaceStorageType.NOR_FLASH
        assert count == 1
        assert fota_crc == compute_fota_checksum(data)
        assert req_addr == addr
        assert pkt.payload[7:] == data

        # Test PageProgramResponse unpacking
        resp_payload = struct.pack("<BI", 0x00, addr)
        resp = PageProgramResponse.from_payload(resp_payload)
        assert resp.return_code == RaceReturnCode.SUCCESS
        assert resp.address == addr

    def test_race_program_page_invalid_data_length_raises(self) -> None:
        with pytest.raises(ValueError, match="exactly 256 bytes"):
            PageProgramRequest(address=0x000DF000, data=b"short").to_packet()

    def test_race_partition_erase_command(self) -> None:
        req = PartitionEraseRequest(address=0x000DF000, length=0x20000)
        pkt = req.to_packet()
        assert pkt.cmd_id == RaceOpcode.PARTITION_ERASE

        resp_payload = struct.pack("<B", 0x00)
        resp = PartitionEraseResponse.from_payload(resp_payload)
        assert resp.return_code == 0

    def test_race_atci_tunnel_command(self) -> None:
        cmd_str = "AT+HWPTOOL=COEFGET,0,0"
        req = AtciTunnelRequest(command=cmd_str)
        pkt = req.to_packet()
        assert pkt.cmd_id == RaceOpcode.ATCI_TUNNEL
        assert pkt.payload == b"AT+HWPTOOL=COEFGET,0,0\r\n"

        resp = AtciTunnelResponse.from_payload(b"\r\nOK\r\n")
        assert resp.response_text == "\r\nOK\r\n"

    def test_race_malformed_header(self) -> None:
        # Less than 6 bytes
        with pytest.raises(RaceFramingError, match="Buffer too short"):
            RacePacket.from_bytes(b"\x05\x5A\x04")

        # Invalid length (< 2)
        bad_len_hdr = struct.pack("<BBHH", 0x05, 0x5A, 1, 0x0403)
        with pytest.raises(RaceLengthError, match="Invalid RACE length"):
            RacePacket.from_bytes(bad_len_hdr)

        # Incomplete packet (payload truncated)
        partial_hdr = struct.pack("<BBHH", 0x05, 0x5A, 10, 0x0403) + b"12"
        with pytest.raises(RaceFramingError, match="Incomplete packet"):
            RacePacket.from_bytes(partial_hdr)

    def test_fota_checksum_vectors(self) -> None:
        # Standard Dallas/Maxim test vector
        assert compute_fota_checksum(b"123456789") == 0xA2
        assert compute_fota_checksum(b"\x00" * 256) == 0x00
        assert compute_fota_checksum(bytes(range(256))) == 0x95


class TestMdrProtocol:
    """Tests Sony MDR packet framing, delimiter escaping, and 8-bit checksums."""

    def test_mdr_framing_delimiters(self) -> None:
        pkt = MdrPacket(packet_type=MdrPacketType.COMMAND, seq_num=1, payload=b"test")
        wire = pkt.to_wire_bytes()
        assert wire[0] == START_MARKER
        assert wire[-1] == END_MARKER

    def test_mdr_byte_escaping_rules(self) -> None:
        # Test bytes needing escaping: 0x3C, 0x3D, 0x3E
        raw = bytes([0x3C, 0x3D, 0x3E])
        escaped = mdr_escape(raw)
        # Expected: 0x3D 0x2C, 0x3D 0x2D, 0x3D 0x2E
        assert escaped == bytes([0x3D, 0x2C, 0x3D, 0x2D, 0x3D, 0x2E])
        unescaped = mdr_unescape(escaped)
        assert unescaped == raw

    def test_mdr_unescaping_roundtrip(self) -> None:
        test_data = bytes(range(256))
        escaped = mdr_escape(test_data)
        assert mdr_unescape(escaped) == test_data

    def test_mdr_unescape_truncated_error(self) -> None:
        with pytest.raises(MdrEscapeError, match="Truncated escape sequence"):
            mdr_unescape(b"abc" + bytes([ESCAPE_BYTE]))

    def test_mdr_unescape_invalid_byte_error(self) -> None:
        with pytest.raises(MdrEscapeError, match="Invalid escaped byte"):
            mdr_unescape(bytes([ESCAPE_BYTE, 0x99]))

    def test_mdr_checksum_modulo256(self) -> None:
        data = b"\x01\x02\x03\x04\x05"
        expected = sum(data) & 0xFF
        assert compute_mdr_checksum(data) == expected

    def test_mdr_checksum_corruption_detected(self) -> None:
        pkt = MdrPacket(packet_type=MdrPacketType.COMMAND, seq_num=5, payload=b"validpayload")
        wire = bytearray(pkt.to_wire_bytes())
        # Corrupt one payload byte inside the wire buffer
        wire[5] ^= 0x01
        with pytest.raises((MdrChecksumError, MdrLengthError, MdrFrameError)):
            MdrPacket.from_wire_bytes(bytes(wire))

    def test_mdr_stream_decoder_chunks(self) -> None:
        decoder = MdrStreamDecoder()
        pkt1 = MdrPacket(packet_type=MdrPacketType.COMMAND, seq_num=1, payload=b"chunk1")
        pkt2 = MdrPacket(packet_type=MdrPacketType.RESPONSE, seq_num=2, payload=b"chunk2")

        full_stream = pkt1.to_wire_bytes() + pkt2.to_wire_bytes()

        # Feed 3 bytes at a time
        received: list[MdrPacket] = []
        for i in range(0, len(full_stream), 3):
            received.extend(decoder.feed(full_stream[i : i + 3]))

        assert len(received) == 2
        assert received[0].payload == b"chunk1"
        assert received[1].payload == b"chunk2"

    def test_mdr_stream_decoder_recovers_from_noise(self) -> None:
        decoder = MdrStreamDecoder()
        pkt = MdrPacket(packet_type=MdrPacketType.COMMAND, seq_num=10, payload=b"recovered")
        wire_with_noise = b"GARBAGE NOISE 123" + pkt.to_wire_bytes()

        received = decoder.feed(wire_with_noise)
        assert len(received) == 1
        assert received[0].payload == b"recovered"


class TestVirtualXM4Device:
    """Tests VirtualXM4Device mock transport, flash operations, ATCI handling, and faults."""

    def test_virtual_device_read_flash(self) -> None:
        device = VirtualXM4Device()
        device.connect()
        page = device.read_flash_page(NVDM_PARTITION_BASE)
        assert len(page) == FLASH_PAGE_SIZE
        # Offset 0 is PEB 0 header magic: 0x4D44564E ("NVDM")
        magic = struct.unpack("<I", page[:4])[0]
        assert magic == 0x4D44564E
        device.close()

    def test_virtual_device_write_flash(self) -> None:
        device = VirtualXM4Device()
        device.connect()
        target_addr = NVDM_PARTITION_BASE + 0x1000  # PEB 1
        test_page = b"\x42" * FLASH_PAGE_SIZE
        ok = device.write_flash_page(target_addr, test_page)
        assert ok is True

        read_back = device.read_flash_page(target_addr)
        assert read_back == test_page
        device.close()

    def test_virtual_device_crc_rejection(self) -> None:
        device = VirtualXM4Device()
        device.connect()
        device.corrupt_fota_crc = True
        target_addr = NVDM_PARTITION_BASE + 0x2000
        test_page = b"\x99" * FLASH_PAGE_SIZE
        ok = device.write_flash_page(target_addr, test_page)
        assert ok is False
        device.close()

    def test_virtual_device_disconnect(self) -> None:
        device = VirtualXM4Device()
        device.connect()
        device.disconnect_on_write = True
        with pytest.raises(ConnectionResetError):
            device.write_flash_page(NVDM_PARTITION_BASE + 0x1000, b"\x00" * FLASH_PAGE_SIZE)
        assert device.is_connected is False

    def test_virtual_device_fail_read(self) -> None:
        device = VirtualXM4Device()
        device.connect()
        device.fail_read = True
        with pytest.raises(IOError, match="Simulated flash read"):
            device.read_flash_page(NVDM_PARTITION_BASE)
        device.close()

    def test_virtual_device_unaligned_address(self) -> None:
        device = VirtualXM4Device()
        device.connect()
        with pytest.raises(ValueError, match="not 256-byte aligned"):
            device.read_flash_page(NVDM_PARTITION_BASE + 1)
        with pytest.raises(ValueError, match="not 256-byte aligned"):
            device.write_flash_page(NVDM_PARTITION_BASE + 1, b"\x00" * FLASH_PAGE_SIZE)
        device.close()

    def test_virtual_atci_coefset_and_coefget(self) -> None:
        device = VirtualXM4Device()
        device.connect()

        # Set stage 0 coefficients for ALIGN_FF_L (target 0)
        set_cmd = "AT+HWPTOOL=COEFSET,0,0,10000000,20000000,30000000,40000000,50000000"
        resp = device.send_atci_command(set_cmd)
        assert "+HWPTOOL: COEFSET,OK" in resp

        # Get coefficients back
        get_cmd = "AT+HWPTOOL=COEFGET,0,0"
        resp_get = device.send_atci_command(get_cmd)
        assert "+HWPTOOL: COEFGET,0,0" in resp_get
        assert "0x10000000" in resp_get
        assert "0x50000000" in resp_get

        device.close()

    def test_virtual_atci_envdm(self) -> None:
        device = VirtualXM4Device()
        device.connect()
        resp = device.send_atci_command("AT+ENVDM=W,DNC,ALIGN_FF_L")
        assert "+ENVDM: W,OK" in resp
        device.close()

    def test_virtual_race_packet_loopback(self) -> None:
        device = VirtualXM4Device()
        device.connect()

        # Send raw RACE PageReadRequest
        req = PageReadRequest(address=NVDM_PARTITION_BASE)
        wire_req = req.to_packet().to_bytes()
        wire_resp = device.process_race_packet(wire_req)

        resp_pkt, rem = RacePacket.from_bytes(wire_resp)
        assert rem == b""
        assert resp_pkt.cmd_id == RaceOpcode.PAGE_READ
        read_resp = PageReadResponse.from_payload(resp_pkt.payload)
        assert read_resp.return_code == 0
        assert read_resp.address == NVDM_PARTITION_BASE
        assert len(read_resp.data) == FLASH_PAGE_SIZE
        device.close()

    def test_virtual_mdr_packet_loopback(self) -> None:
        device = VirtualXM4Device()
        device.connect()

        mdr_pkt = MdrPacket(packet_type=MdrPacketType.COMMAND, seq_num=7, payload=b"ping")
        wire_resp = device.process_mdr_packet(mdr_pkt.to_wire_bytes())

        parsed_resp, rem = MdrPacket.from_wire_bytes(wire_resp)
        assert rem == b""
        assert parsed_resp is not None
        assert parsed_resp.packet_type == MdrPacketType.ACK
        assert parsed_resp.seq_num == 7
        device.close()


class TestRfcommTransport:
    """Tests partition extraction and DeviceTransport protocol adherence."""

    def test_device_transport_protocol_conformance(self) -> None:
        device = VirtualXM4Device()
        assert isinstance(device, DeviceTransport)

    def test_read_nvdm_partition_via_virtual_device(self) -> None:
        device = VirtualXM4Device()
        device.connect()

        progress_calls: list[tuple[int, int]] = []

        def on_progress(current: int, total: int) -> None:
            progress_calls.append((current, total))

        image = read_nvdm_partition(device, progress_callback=on_progress)
        assert len(image) == NVDM_PARTITION_SIZE
        assert len(progress_calls) == 2048
        assert progress_calls[-1] == (2048, 2048)

        # Offset 0 is PEB magic 0x4D44564E
        magic = struct.unpack("<I", image[:4])[0]
        assert magic == 0x4D44564E
        device.close()

    def test_rfcomm_socket_unconnected_raises(self) -> None:
        sock = RfcommSocket("00:11:22:33:44:55", 21)
        with pytest.raises(DeviceDisconnectedError):
            sock.send_all(b"test")
        with pytest.raises(DeviceDisconnectedError):
            sock.recv_exact(10)

    def test_real_transport_reads_nvdm_storage_and_logical_address(self) -> None:
        """Hardware NVDM lives in storage_type=1 at logical address 0, not NOR flash."""
        from xm4_optimizer.transport.rfcomm_socket import RfcommDeviceTransport

        captured: list[RacePacket] = []
        page = bytes(range(256))

        def fake_exchange(packet: RacePacket) -> RacePacket:
            captured.append(packet)
            # Real device returns an 8-byte payload header followed by 256 data bytes.
            payload = struct.pack("<BBHI", 0x00, 0x01, 0x01, 0x00001000) + page
            return RacePacket(
                head=RaceHead.STANDARD,
                type=RaceHeaderType.RESPONSE,
                cmd_id=RaceOpcode.PAGE_READ,
                payload=payload,
            )

        transport = RfcommDeviceTransport("00:11:22:33:44:55")
        transport.send_race_packet = fake_exchange  # type: ignore[method-assign]
        data = transport.read_flash_page(NVDM_PARTITION_BASE + 0x1000)

        storage, page_count, logical_addr = struct.unpack("<BBI", captured[0].payload)
        assert storage == RaceStorageType.NVDM
        assert page_count == 1
        assert logical_addr == 0x1000
        assert data == page

    def test_real_transport_rejects_address_outside_nvdm_store(self) -> None:
        from xm4_optimizer.transport.rfcomm_socket import (
            FlashOperationError,
            RfcommDeviceTransport,
        )

        transport = RfcommDeviceTransport("00:11:22:33:44:55")
        with pytest.raises(FlashOperationError):
            transport.read_flash_page(NVDM_PARTITION_BASE + NVDM_PARTITION_SIZE)
