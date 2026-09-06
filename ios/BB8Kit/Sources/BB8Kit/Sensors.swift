import Foundation

/// Sensor stream masks and sample decoding.
///
/// BB-8 streams samples as a flat sequence of big-endian **signed 16-bit**
/// values. There are no field tags on the wire: the receiver must know which
/// sensors it requested and reconstruct the layout, in **descending mask-bit
/// order**, primary-mask fields first and extended-mask fields after.
///
/// That implicit contract is why this type exists. Get the ordering wrong and
/// every field decodes as its neighbour — plausible-looking numbers that are
/// entirely wrong, which is far worse than a crash.
public enum SpheroSensors {

    public struct Spec: Sendable, Hashable {
        public let name: String
        public let bit: UInt32
        public let scale: Double
        public let unit: String

        init(_ name: String, _ bit: UInt32, scale: Double = 1, unit: String = "") {
            self.name = name; self.bit = bit; self.scale = scale; self.unit = unit
        }
    }

    /// Fields selected by the primary 32-bit mask, descending bit order.
    public static let primary: [Spec] = [
        Spec("pitch", 0x0004_0000, unit: "deg"),
        Spec("roll", 0x0002_0000, unit: "deg"),
        Spec("yaw", 0x0001_0000, unit: "deg"),
        Spec("accelX", 0x0000_8000, scale: 1.0 / 4096, unit: "g"),
        Spec("accelY", 0x0000_4000, scale: 1.0 / 4096, unit: "g"),
        Spec("accelZ", 0x0000_2000, scale: 1.0 / 4096, unit: "g"),
        Spec("gyroX", 0x0000_1000, scale: 0.1, unit: "deg/s"),
        Spec("gyroY", 0x0000_0800, scale: 0.1, unit: "deg/s"),
        Spec("gyroZ", 0x0000_0400, scale: 0.1, unit: "deg/s"),
        Spec("emfLeft", 0x0000_0040),
        Spec("emfRight", 0x0000_0020),
    ]

    /// Fields selected by the extended 32-bit mask, descending bit order.
    public static let extended: [Spec] = [
        Spec("quatX", 0x8000_0000, scale: 1.0 / 10000),
        Spec("quatY", 0x4000_0000, scale: 1.0 / 10000),
        Spec("quatZ", 0x2000_0000, scale: 1.0 / 10000),
        Spec("quatW", 0x1000_0000, scale: 1.0 / 10000),
        Spec("locatorX", 0x0800_0000, unit: "cm"),
        Spec("locatorY", 0x0400_0000, unit: "cm"),
        Spec("accelOne", 0x0200_0000),
        Spec("velocityX", 0x0100_0000, scale: 0.1, unit: "cm/s"),
        Spec("velocityY", 0x0080_0000, scale: 0.1, unit: "cm/s"),
        Spec("speed", 0x0040_0000, unit: "cm/s"),
    ]

    /// Enough to see motion and position without saturating the link.
    public static let drivePreset = ["yaw", "speed", "locatorX", "locatorY", "velocityX", "velocityY"]

    /// The firmware's sensor sample clock.
    ///
    /// The streaming command takes a **divisor** of this, not a period in
    /// milliseconds — verified on hardware to within 0.25% (divisor 100 →
    /// 4.01 Hz, 50 → 8.01 Hz, 25 → 15.96 Hz). Reading it as milliseconds
    /// understates the rate by 25x and makes a healthy stream look like heavy
    /// packet loss.
    public static let baseHz: Double = 400

    public static func divisor(forHz hz: Double) -> Int {
        max(1, min(0xFFFF, Int((baseHz / hz).rounded())))
    }

    public static func hz(forDivisor divisor: Int) -> Double {
        divisor == 0 ? 0 : baseHz / Double(divisor)
    }

    public enum SensorError: Error, Equatable {
        case unknownSensor(String)
        case badLength(expected: Int, got: Int)
    }

    /// Turn sensor names into the `(primary, extended)` mask pair.
    ///
    /// Throws on an unrecognised name rather than skipping it: a dropped field
    /// would shift every subsequent value in the decoded sample.
    public static func masks(for names: [String]) throws -> (primary: UInt32, extended: UInt32) {
        var p: UInt32 = 0, e: UInt32 = 0
        for name in names {
            if let spec = primary.first(where: { $0.name == name }) {
                p |= spec.bit
            } else if let spec = extended.first(where: { $0.name == name }) {
                e |= spec.bit
            } else {
                throw SensorError.unknownSensor(name)
            }
        }
        return (p, e)
    }

    /// The field order a sample with these masks will arrive in.
    public static func layout(primary p: UInt32, extended e: UInt32) -> [Spec] {
        primary.filter { $0.bit & p != 0 } + extended.filter { $0.bit & e != 0 }
    }

    /// Decode one scaled sample.
    ///
    /// The length check is the only defence against a mask/payload mismatch:
    /// with no field tags on the wire, a wrong mask yields confidently wrong
    /// numbers rather than an error. Fail loudly instead.
    public static func decodeSample(
        _ payload: [UInt8], primary p: UInt32, extended e: UInt32
    ) throws -> [String: Double] {
        let fields = layout(primary: p, extended: e)
        guard payload.count == fields.count * 2 else {
            throw SensorError.badLength(expected: fields.count * 2, got: payload.count)
        }
        var out: [String: Double] = [:]
        for (index, spec) in fields.enumerated() {
            let raw = Int16(bitPattern: UInt16(payload[index * 2]) << 8 | UInt16(payload[index * 2 + 1]))
            out[spec.name] = Double(raw) * spec.scale
        }
        return out
    }

    /// Decode an async `0x03` payload, which may carry several samples.
    public static func decodeFrame(
        _ payload: [UInt8], primary p: UInt32, extended e: UInt32
    ) throws -> [[String: Double]] {
        let size = layout(primary: p, extended: e).count * 2
        guard size > 0 else { return [] }
        guard payload.count % size == 0 else {
            throw SensorError.badLength(expected: size, got: payload.count)
        }
        return try stride(from: 0, to: payload.count, by: size).map {
            try decodeSample(Array(payload[$0 ..< $0 + size]), primary: p, extended: e)
        }
    }
}

extension Sphero {
    /// Start (or stop, with `mask: 0`) async sensor streaming.
    ///
    /// - Parameter divisor: divides `SpheroSensors.baseHz`. Rate is
    ///   `400 / divisor` Hz. Use `SpheroSensors.divisor(forHz:)` rather than
    ///   passing a period in milliseconds.
    public static func setDataStreaming(
        divisor: Int, samplesPerPacket: Int = 1, mask: UInt32,
        count: UInt8 = 0, extendedMask: UInt32 = 0, seq: UInt8 = 0
    ) -> Data {
        var data: [UInt8] = []
        data += [UInt8((divisor >> 8) & 0xFF), UInt8(divisor & 0xFF)]
        data += [UInt8((samplesPerPacket >> 8) & 0xFF), UInt8(samplesPerPacket & 0xFF)]
        data += [UInt8((mask >> 24) & 0xFF), UInt8((mask >> 16) & 0xFF),
                 UInt8((mask >> 8) & 0xFF), UInt8(mask & 0xFF)]
        data += [count]
        data += [UInt8((extendedMask >> 24) & 0xFF), UInt8((extendedMask >> 16) & 0xFF),
                 UInt8((extendedMask >> 8) & 0xFF), UInt8(extendedMask & 0xFF)]
        return build(did: .sphero, cid: SpheroCommand.setDataStreaming.rawValue,
                     seq: seq, data: data, answer: true)
    }
}
