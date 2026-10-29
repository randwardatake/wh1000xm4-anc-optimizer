// macOS RFCOMM bridge for xm4_optimizer.
//
// macOS has no AF_BLUETOOTH sockets; RFCOMM is only reachable through IOBluetooth.
// This helper opens an RFCOMM channel and relays bytes between it and an inherited
// stream socket, so the Python side keeps working with a plain socket.socket.
//
// Usage:
//   rfcomm_bridge list                       connected devices, one "MAC<TAB>name" per line
//   rfcomm_bridge sdp <mac> <uuid>           RFCOMM channel of a service UUID, or exit 3 if absent
//   rfcomm_bridge connect <mac> <ch> <fd>    relay RFCOMM channel <ch> <-> socket <fd>
//
// `connect` prints "OK <mtu>" or "ERR <message>" as its first stdout line, then relays
// until either side closes. Closing <fd> closes the channel and vice versa.

import Foundation
import IOBluetooth

setvbuf(stdout, nil, _IOLBF, 0)

func fail(_ message: String, code: Int32 = 1) -> Never {
    print("ERR \(message)")
    exit(code)
}

func device(_ address: String) -> IOBluetoothDevice {
    guard let dev = IOBluetoothDevice(addressString: address) else { fail("invalid address \(address)", code: 2) }
    return dev
}

func normalized(_ address: String?) -> String {
    (address ?? "").replacingOccurrences(of: "-", with: ":").uppercased()
}

final class Bridge: NSObject, IOBluetoothRFCOMMChannelDelegate {
    let fd: Int32
    var channel: IOBluetoothRFCOMMChannel?
    var source: DispatchSourceRead?

    init(fd: Int32) {
        self.fd = fd
    }

    func start(_ channel: IOBluetoothRFCOMMChannel) {
        self.channel = channel
        let src = DispatchSource.makeReadSource(fileDescriptor: fd, queue: .main)
        src.setEventHandler { [weak self] in self?.pump() }
        src.resume()
        source = src
    }

    // socket -> RFCOMM, split into MTU-sized frames
    func pump() {
        guard let channel = channel else { return }
        var buf = [UInt8](repeating: 0, count: 65536)
        let n = Darwin.read(fd, &buf, buf.count)
        if n <= 0 { finish() }
        let mtu = max(Int(channel.getMTU()), 1)
        var offset = 0
        while offset < n {
            let len = min(mtu, n - offset)
            let status = buf.withUnsafeMutableBytes { raw in
                channel.writeSync(raw.baseAddress! + offset, length: UInt16(len))
            }
            if status != kIOReturnSuccess {
                FileHandle.standardError.write("rfcomm write failed: \(status)\n".data(using: .utf8)!)
                finish()
            }
            offset += len
        }
    }

    // RFCOMM -> socket
    func rfcommChannelData(_ rfcommChannel: IOBluetoothRFCOMMChannel!, data dataPointer: UnsafeMutableRawPointer!, length dataLength: Int) {
        var offset = 0
        while offset < dataLength {
            let n = Darwin.write(fd, dataPointer + offset, dataLength - offset)
            if n <= 0 { finish() }
            offset += n
        }
    }

    func rfcommChannelClosed(_ rfcommChannel: IOBluetoothRFCOMMChannel!) {
        finish()
    }

    func finish() -> Never {
        source?.cancel()
        channel?.close()
        Darwin.close(fd)
        exit(0)
    }
}

let args = CommandLine.arguments
guard args.count >= 2 else { fail("usage: rfcomm_bridge list | sdp <mac> <uuid> | connect <mac> <channel> <fd>", code: 2) }

switch args[1] {
case "list":
    for case let dev as IOBluetoothDevice in IOBluetoothDevice.pairedDevices() ?? [] where dev.isConnected() {
        print("\(normalized(dev.addressString))\t\(dev.name ?? "")")
    }

case "sdp" where args.count == 4:
    let dev = device(args[2])
    guard let uuid = UUID(uuidString: args[3]) else { fail("invalid uuid \(args[3])", code: 2) }
    let bytes = withUnsafeBytes(of: uuid.uuid) { Array($0) }
    let sdpUUID = IOBluetoothSDPUUID(bytes: bytes, length: bytes.count)
    // Refresh the cached SDP records; ignore failure and fall back to the cache.
    dev.performSDPQuery(nil)
    var record = dev.getServiceRecord(for: sdpUUID)
    if record == nil {
        let deadline = Date().addingTimeInterval(5)
        while record == nil && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.2))
            record = dev.getServiceRecord(for: sdpUUID)
        }
    }
    var channelID: BluetoothRFCOMMChannelID = 0
    guard let rec = record, rec.getRFCOMMChannelID(&channelID) == kIOReturnSuccess else { exit(3) }
    print(channelID)

case "connect" where args.count == 5:
    guard let channelID = UInt8(args[3]), let fd = Int32(args[4]) else { fail("invalid channel or fd", code: 2) }
    let dev = device(args[2])
    let bridge = Bridge(fd: fd)
    // The first open after the RFCOMM session comes up fails with kIOReturnError
    // (0xE00002BC) after ~3 s on macOS 27 with a WH-1000XM4; the next attempt succeeds.
    var channel: IOBluetoothRFCOMMChannel?
    var status: IOReturn = kIOReturnError
    for _ in 1...3 {
        status = dev.openRFCOMMChannelSync(&channel, withChannelID: channelID, delegate: bridge)
        if status == kIOReturnSuccess { break }
    }
    guard status == kIOReturnSuccess, let ch = channel else {
        fail(String(format: "openRFCOMMChannelSync(%@, %d) returned 0x%08X", args[2], channelID, UInt32(bitPattern: status)))
    }
    print("OK \(ch.getMTU())")
    bridge.start(ch)
    RunLoop.main.run()

default:
    fail("usage: rfcomm_bridge list | sdp <mac> <uuid> | connect <mac> <channel> <fd>", code: 2)
}
