import Foundation

extension Sphero {

    /// A synchronous reply to a command we sent, matched by `seq`.
    public struct Response: Sendable, Equatable {
        public let code: UInt8
        public let seq: UInt8
        public let payload: [UInt8]
        public var isOK: Bool { code == 0 }
    }

    /// An unsolicited message: sensor stream, collision, sleep warning.
    public struct AsyncMessage: Sendable, Equatable {
        public let idCode: UInt8
        public let payload: [UInt8]
        public var kind: AsyncID? { AsyncID(rawValue: idCode) }
    }

    public enum AsyncID: UInt8, Sendable {
        case powerNotification = 0x01
        case sensorData = 0x03
        case sleepingSoon = 0x05
        case collisionDetected = 0x07
        case gyroAxisLimit = 0x0C
        case didSleep = 0x0E
    }

    public enum Message: Sendable, Equatable {
        case response(Response)
        case async(AsyncMessage)
    }
}

/// Reassembles notification fragments into whole packets.
///
/// BLE delivers arbitrary fragments, so this buffers until a complete frame is
/// present. Two frame shapes share the characteristic and must be told apart by
/// the second byte — synchronous replies use a one-byte length, asynchronous
/// messages a two-byte one. Getting that wrong desynchronises the stream
/// permanently rather than failing loudly.
///
/// Never throws. A malformed frame must not be able to kill the notification
/// path: a dead decoder makes a live droid look deaf.
public struct SpheroDecoder: Sendable {
    private var buffer: [UInt8] = []

    public init() {}

    public mutating func feed(_ data: Data) -> [Sphero.Message] {
        buffer.append(contentsOf: data)
        var messages: [Sphero.Message] = []

        while true {
            // Resynchronise: drop anything before a plausible start byte.
            while let first = buffer.first, first != Sphero.sop1 {
                buffer.removeFirst()
            }
            guard buffer.count >= 5 else { return messages }

            let sop2 = buffer[1]
            if sop2 == Sphero.sop1 {                    // synchronous response
                let dlen = Int(buffer[4])
                let total = 5 + dlen
                guard buffer.count >= total else { return messages }
                let frame = Array(buffer[0 ..< total])
                buffer.removeFirst(total)
                messages.append(.response(Sphero.Response(
                    code: frame[2], seq: frame[3],
                    payload: Array(frame[5 ..< max(5, frame.count - 1)])
                )))
            } else if sop2 == Sphero.SOP2.noAnswer {    // asynchronous message
                let dlen = Int(buffer[3]) << 8 | Int(buffer[4])
                let total = 5 + dlen
                guard buffer.count >= total else { return messages }
                let frame = Array(buffer[0 ..< total])
                buffer.removeFirst(total)
                messages.append(.async(Sphero.AsyncMessage(
                    idCode: frame[2],
                    payload: Array(frame[5 ..< max(5, frame.count - 1)])
                )))
            } else {
                // Not a frame we recognise; drop a byte and resynchronise.
                buffer.removeFirst()
            }
        }
    }
}
