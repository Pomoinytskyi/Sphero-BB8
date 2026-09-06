import Foundation

/// Sphero API v1.20 wire protocol for BB-8.
///
/// A direct port of the Python `bb8ctl.protocol` module, validated against the
/// same golden vectors (`bb8_vectors.json`). Both implementations must produce
/// byte-identical output; `ProtocolTests` enforces that.
///
/// Every constant here was verified against real hardware on 2026-09-06 — see
/// `docs/03-capability-catalog.md` §10 in the parent repository.
///
/// Deliberately free of CoreBluetooth: this type encodes and decodes bytes and
/// nothing else, so it can be exercised without a device, a simulator, or a droid.
public enum Sphero {

    // MARK: - BLE topology

    /// Holds the wake/anti-DOS plumbing that must be poked before the robot
    /// will communicate at all.
    public static let bleService = "22bb746f-2bb0-7554-2d6f-726568705327"
    public static let wakeCharacteristic = "22bb746f-2bbf-7554-2d6f-726568705327"
    public static let txPowerCharacteristic = "22bb746f-2bb2-7554-2d6f-726568705327"
    public static let antiDOSCharacteristic = "22bb746f-2bbd-7554-2d6f-726568705327"

    /// Carries the actual command protocol.
    public static let robotService = "22bb746f-2ba0-7554-2d6f-726568705327"
    public static let commandCharacteristic = "22bb746f-2ba1-7554-2d6f-726568705327"
    public static let responseCharacteristic = "22bb746f-2ba6-7554-2d6f-726568705327"

    /// Written in order, *before* subscribing to the response characteristic.
    ///
    /// Skipping the anti-DOS write leaves the droid connected but permanently
    /// mute — no error, no reply, nothing. It is the single most common reason
    /// a from-scratch implementation appears to connect and then does nothing.
    public static let handshake: [(uuid: String, payload: Data)] = [
        (antiDOSCharacteristic, Data("011i3".utf8)),
        (txPowerCharacteristic, Data([0x07])),
    ]

    /// BB-8 advertises with this local-name prefix.
    public static let namePrefix = "BB-"

    /// Characteristic writes are chunked to this many bytes.
    public static let mtuChunk = 20

    // MARK: - Framing

    public static let sop1: UInt8 = 0xFF

    /// Second start-of-packet byte — a flags field, not a constant.
    ///
    /// Bit 0 requests an acknowledgement; bit 1 resets the inactivity timer.
    /// Hardware-verified: BB-8 honours `.noAnswer`, replying to none of 50
    /// packets sent that way. That is what lets the drive loop run without ever
    /// waiting on the droid.
    public enum SOP2 {
        /// Acknowledged. Use for setup and anything whose result you read.
        public static let answer: UInt8 = 0xFF
        /// Unacknowledged. Use for the drive loop.
        public static let noAnswer: UInt8 = 0xFE
    }

    public enum DeviceID: UInt8 {
        case core = 0x00
        case bootloader = 0x01
        case sphero = 0x02
    }

    public enum CoreCommand: UInt8 {
        case ping = 0x01
        case getVersions = 0x02
        case getPowerState = 0x20
        case enableBatteryNotify = 0x21
        case sleep = 0x22
        case setInactivityTimeout = 0x25
        case getChargerState = 0x38
    }

    public enum SpheroCommand: UInt8 {
        case setHeading = 0x01
        case setStabilization = 0x02
        case setRotationRate = 0x03
        case selfLevel = 0x09
        case setDataStreaming = 0x11
        case configureCollisionDetection = 0x12
        case configureLocator = 0x13
        case setMainLED = 0x20
        case setBackLED = 0x21
        case roll = 0x30
        case boost = 0x31
        case setRawMotors = 0x33
        case setMotionTimeout = 0x34
    }

    public enum RollMode: UInt8 {
        case stop = 0
        case go = 1
        /// Rotates the heading reference *without* driving the motors.
        /// This is the primitive aim/calibration mode is built on.
        case calibrate = 2
    }

    /// Sphero's packet checksum: `0xFF - (sum mod 256)`.
    ///
    /// Computed across DID through the last data byte — the two SOP bytes are
    /// excluded, and the checksum byte itself obviously so.
    public static func checksum<C: Collection>(_ payload: C) -> UInt8 where C.Element == UInt8 {
        let total = payload.reduce(into: UInt32(0)) { $0 &+= UInt32($1) }
        return 0xFF &- UInt8(total & 0xFF)
    }

    /// Frame one command packet.
    ///
    /// `answer` defaults to `false` because the drive loop dominates traffic and
    /// must never wait. Commands whose result you actually read should pass
    /// `true` and match the reply on `seq`.
    public static func build(
        did: DeviceID,
        cid: UInt8,
        seq: UInt8 = 0,
        data: [UInt8] = [],
        answer: Bool = false
    ) -> Data {
        var body: [UInt8] = [did.rawValue, cid, seq, UInt8(data.count + 1)]
        body.append(contentsOf: data)
        var packet: [UInt8] = [sop1, answer ? SOP2.answer : SOP2.noAnswer]
        packet.append(contentsOf: body)
        packet.append(checksum(body))
        return Data(packet)
    }

    /// Split a packet into writable chunks.
    public static func chunked(_ packet: Data, size: Int = mtuChunk) -> [Data] {
        stride(from: 0, to: packet.count, by: size).map { offset in
            packet.subdata(in: offset ..< min(offset + size, packet.count))
        }
    }

    private static func u16(_ value: Int) -> [UInt8] {
        [UInt8((value >> 8) & 0xFF), UInt8(value & 0xFF)]
    }

    // MARK: - Driving

    /// The workhorse: speed *and* heading in a single packet.
    ///
    /// Sending this rather than separate SET_HEADING + SET_SPEED halves the
    /// radio traffic per control tick.
    ///
    /// - Parameters:
    ///   - speed: 0–255. Above roughly 120 is unmanageable indoors.
    ///   - heading: degrees relative to the current aim origin.
    public static func roll(
        speed: Int, heading: Int, mode: RollMode = .go, seq: UInt8 = 0
    ) -> Data {
        let clamped = UInt8(max(0, min(255, speed)))
        let wrapped = ((heading % 360) + 360) % 360
        return build(did: .sphero, cid: SpheroCommand.roll.rawValue, seq: seq,
                     data: [clamped] + u16(wrapped) + [mode.rawValue, 0])
    }

    public static func stop(heading: Int = 0, seq: UInt8 = 0) -> Data {
        roll(speed: 0, heading: heading, mode: .stop, seq: seq)
    }

    /// Rotate the heading reference without driving. Used by aim mode.
    public static func calibrate(heading: Int, seq: UInt8 = 0) -> Data {
        roll(speed: 0, heading: heading, mode: .calibrate, seq: seq)
    }

    /// Declare the current orientation to be `heading` degrees.
    /// `setHeading(0)` is the "reset aim" operation.
    public static func setHeading(_ heading: Int, seq: UInt8 = 0) -> Data {
        build(did: .sphero, cid: SpheroCommand.setHeading.rawValue, seq: seq,
              data: u16(((heading % 360) + 360) % 360))
    }

    /// Without this the control system stays off and `roll` is silently inert —
    /// well-formed packets, motionless droid, no error. Learned the hard way.
    public static func setStabilization(_ enabled: Bool, seq: UInt8 = 0) -> Data {
        build(did: .sphero, cid: SpheroCommand.setStabilization.rawValue, seq: seq,
              data: [enabled ? 1 : 0])
    }

    public static func setRotationRate(_ rate: Int, seq: UInt8 = 0) -> Data {
        build(did: .sphero, cid: SpheroCommand.setRotationRate.rawValue, seq: seq,
              data: [UInt8(max(0, min(255, rate)))])
    }

    // MARK: - Lights

    public static func setMainLED(red: Int, green: Int, blue: Int, seq: UInt8 = 0) -> Data {
        let c = { (v: Int) in UInt8(max(0, min(255, v))) }
        return build(did: .sphero, cid: SpheroCommand.setMainLED.rawValue, seq: seq,
                     data: [c(red), c(green), c(blue)])
    }

    /// The blue tail light. It marks the *back* of the droid and is the visual
    /// reference for aiming.
    public static func setBackLED(brightness: Int, seq: UInt8 = 0) -> Data {
        build(did: .sphero, cid: SpheroCommand.setBackLED.rawValue, seq: seq,
              data: [UInt8(max(0, min(255, brightness)))])
    }

    // MARK: - Safety and power

    /// Auto-stop if no command arrives within `milliseconds`.
    ///
    /// A firmware-level dead-man switch: if the app crashes mid-roll the droid
    /// stops itself rather than driving into a wall until the battery dies.
    public static func setMotionTimeout(milliseconds: Int, seq: UInt8 = 0) -> Data {
        build(did: .sphero, cid: SpheroCommand.setMotionTimeout.rawValue, seq: seq,
              data: u16(milliseconds))
    }

    public static func configureCollisionDetection(
        method: UInt8 = 1, xThreshold: UInt8 = 100, xSpeed: UInt8 = 100,
        yThreshold: UInt8 = 100, ySpeed: UInt8 = 100, deadTimeMs: Int = 100, seq: UInt8 = 0
    ) -> Data {
        build(did: .sphero, cid: SpheroCommand.configureCollisionDetection.rawValue, seq: seq,
              data: [method, xThreshold, xSpeed, yThreshold, ySpeed, UInt8(deadTimeMs / 10)])
    }

    public static func ping(seq: UInt8 = 0) -> Data {
        build(did: .core, cid: CoreCommand.ping.rawValue, seq: seq, answer: true)
    }

    public static func getPowerState(seq: UInt8 = 0) -> Data {
        build(did: .core, cid: CoreCommand.getPowerState.rawValue, seq: seq, answer: true)
    }

    public static func sleep(seq: UInt8 = 0) -> Data {
        build(did: .core, cid: CoreCommand.sleep.rawValue, seq: seq, data: [0, 0, 0, 0])
    }

    /// Battery reading, decoded from a `getPowerState` reply.
    ///
    /// Payload layout, verified on hardware: `[0]` record version,
    /// `[1]` power state, `[2..3]` centivolts, `[4..5]` charge count.
    /// (Reading the voltage one byte early reports ~5.15 V for an 8.07 V pack,
    /// which looks convincingly like a flat battery.)
    public struct PowerState: Sendable, Equatable {
        public enum Level: UInt8, Sendable { case charging = 1, ok = 2, low = 3, critical = 4 }
        public let level: Level?
        public let volts: Double
        public let chargeCount: Int

        public init?(payload: [UInt8]) {
            guard payload.count >= 6 else { return nil }
            level = Level(rawValue: payload[1])
            volts = Double(Int(payload[2]) << 8 | Int(payload[3])) / 100.0
            chargeCount = Int(payload[4]) << 8 | Int(payload[5])
        }

        public var isHealthy: Bool { level == .ok || level == .charging }
    }
}
