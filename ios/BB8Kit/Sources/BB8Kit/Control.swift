import Foundation

/// Stick input to drive command: deadzone, response curve, drive models, profiles.
///
/// Pure functions, ported from the Python `bb8ctl.control` module and tested
/// against the same cases. Keeping the feel of driving out of the view layer
/// means it stays tunable and verifiable without a device or a droid.
public enum Control {

    /// A named speed/response envelope.
    ///
    /// BB-8's speed byte runs 0–255, but above roughly 120 it is unmanageable
    /// indoors: it reaches a wall faster than a person reacts. Rather than bury
    /// that in a setting, it is two named modes the user can switch mid-drive.
    public struct SpeedProfile: Sendable, Equatable {
        public let name: String
        /// Maximum speed byte actually emitted.
        public let cap: Int
        /// Response-curve exponent. >1 gives fine control near centre.
        public let expo: Double
        /// Degrees/second of heading change at full deflection, tank mode.
        public let turnRate: Double
        public let deadzone: Double

        public init(name: String, cap: Int, expo: Double, turnRate: Double, deadzone: Double = 0.12) {
            self.name = name; self.cap = cap; self.expo = expo
            self.turnRate = turnRate; self.deadzone = deadzone
        }
    }

    /// Default. Indoors, near furniture, and while aiming.
    public static let tortoise = SpeedProfile(name: "tortoise", cap: 90, expo: 2.0, turnRate: 120)
    /// Open floor. Sharper response, full speed range.
    public static let rabbit = SpeedProfile(name: "rabbit", cap: 255, expo: 1.4, turnRate: 220)
    public static let profiles = [tortoise, rabbit]

    public enum DriveMode: String, Sendable, CaseIterable {
        /// Stick direction is world direction, like a top-down game.
        case absolute
        /// Y is throttle, X is turn rate, relative to current facing.
        case tank
    }

    public enum CommandKind: Sendable, Equatable {
        case drive, stop, calibrate
    }

    public struct DriveCommand: Sendable, Equatable {
        public let speed: Int
        public let heading: Int
        public let kind: CommandKind

        public init(speed: Int, heading: Int, kind: CommandKind = .drive) {
            self.speed = max(0, min(255, speed))
            self.heading = ((heading % 360) + 360) % 360
            self.kind = kind
        }

        /// The packet this command becomes. The one place control vocabulary
        /// meets the wire format.
        public func packet(seq: UInt8) -> Data {
            switch kind {
            case .drive: return Sphero.roll(speed: speed, heading: heading, seq: seq)
            case .stop: return Sphero.stop(heading: heading, seq: seq)
            case .calibrate: return Sphero.calibrate(heading: heading, seq: seq)
            }
        }
    }

    /// Apply a *radial* deadzone and rescale the remainder to full range.
    ///
    /// Radial, not per-axis: a square deadzone lets a stick resting slightly off
    /// centre creep in one axis while the other is suppressed, which reads as the
    /// droid drifting on its own. Rescaling matters too — without it, output
    /// jumps discontinuously from 0 to `deadzone` at the threshold.
    public static func radialDeadzone(x: Double, y: Double, deadzone: Double) -> (x: Double, y: Double) {
        var magnitude = (x * x + y * y).squareRoot()
        guard magnitude > deadzone else { return (0, 0) }
        var nx = x, ny = y
        if magnitude > 1 {   // square-gated hardware exceeds 1 on diagonals
            nx /= magnitude; ny /= magnitude; magnitude = 1
        }
        let scaled = (magnitude - deadzone) / (1 - deadzone)
        return (nx / magnitude * scaled, ny / magnitude * scaled)
    }

    /// Per-axis deadzone, for controls whose axes mean different things.
    ///
    /// Tank mode steers with X and throttles with Y — two independent commands
    /// that happen to share a stick. Running them through `radialDeadzone`
    /// couples them: the circular clamp normalises a forward-and-right push to
    /// (0.707, 0.707), so steering silently cuts throttle by 30%. Correct for a
    /// direction vector, wrong for two separate controls.
    public static func axialDeadzone(_ value: Double, _ deadzone: Double) -> Double {
        let magnitude = abs(value)
        guard magnitude > deadzone else { return 0 }
        let scaled = (magnitude - deadzone) / (1 - deadzone)
        return (value < 0 ? -1 : 1) * min(1, scaled)
    }

    public static func applyExpo(_ magnitude: Double, _ expo: Double) -> Double {
        pow(magnitude, expo)
    }

    /// Throttle for tank mode, from triggers with the stick as a fallback.
    ///
    /// Triggers win when either is pressed: they are analog, they are squeezed
    /// against spring tension rather than balanced, and they free the thumb to
    /// do nothing but steer. Stick Y still works for anyone who prefers it, and
    /// for the on-screen controller.
    public static func tankThrottle(stickY: Double, leftTrigger: Double, rightTrigger: Double) -> Double {
        let fromTriggers = rightTrigger - leftTrigger
        return abs(fromTriggers) > 0.02 ? fromTriggers : stickY
    }

    /// Stick direction is world direction.
    ///
    /// `atan2(x, y)` — x first — yields 0° for "stick pushed away", matching
    /// BB-8's convention where 0 is forward and angles increase clockwise. The
    /// conventional `atan2(y, x)` would drive at 90° to the stick.
    public static func mapAbsolute(x: Double, y: Double, profile: SpeedProfile) -> DriveCommand {
        let (dx, dy) = radialDeadzone(x: x, y: y, deadzone: profile.deadzone)
        let magnitude = (dx * dx + dy * dy).squareRoot()
        guard magnitude > 0 else { return DriveCommand(speed: 0, heading: 0, kind: .stop) }
        let heading = Int(atan2(dx, dy) * 180 / .pi)
        let speed = Int((applyExpo(magnitude, profile.expo) * Double(profile.cap)).rounded())
        return DriveCommand(speed: speed, heading: heading)
    }

    /// Steering and throttle as independent controls.
    ///
    /// Deliberately takes them separately rather than as one stick vector: they
    /// are different commands, and treating them as a vector is what coupled
    /// them in the first place. Each gets its own axial deadzone, so steering at
    /// full throttle stays at full throttle.
    ///
    /// Turn rate is integrated over `dt` so steering feel is frame-rate
    /// independent — otherwise the droid turns faster simply because the control
    /// loop ran quicker.
    public static func mapTank(
        steer: Double, throttle: Double, profile: SpeedProfile, heading: Int, dt: Double
    ) -> DriveCommand {
        let steerInput = axialDeadzone(steer, profile.deadzone)
        let throttleInput = axialDeadzone(throttle, profile.deadzone)
        var newHeading = Int(Double(heading) + steerInput * profile.turnRate * dt)

        guard throttleInput != 0 else {
            // Steering with no throttle still updates the heading, so releasing
            // the throttle mid-turn does not snap back to the old bearing.
            return DriveCommand(speed: 0, heading: newHeading, kind: .stop)
        }
        if throttleInput < 0 { newHeading += 180 }   // reverse: opposite bearing
        let speed = Int((applyExpo(abs(throttleInput), profile.expo) * Double(profile.cap)).rounded())
        return DriveCommand(speed: speed, heading: newHeading)
    }

    public static func map(
        x: Double, y: Double, throttle: Double? = nil, mode: DriveMode,
        profile: SpeedProfile, heading: Int = 0, dt: Double = 1.0 / 60
    ) -> DriveCommand {
        switch mode {
        case .absolute:
            return mapAbsolute(x: x, y: y, profile: profile)
        case .tank:
            return mapTank(steer: x, throttle: throttle ?? y,
                           profile: profile, heading: heading, dt: dt)
        }
    }

    /// Heading change while aiming.
    ///
    /// Aiming uses ROLL mode 2 (calibrate), which rotates the heading reference
    /// without driving. Deliberately slower than steering — aiming is precision
    /// work and full turn rate overshoots.
    public static func aimDelta(x: Double, profile: SpeedProfile, heading: Int, dt: Double) -> Int {
        guard abs(x) >= profile.deadzone else { return heading }
        let updated = Double(heading) + x * profile.turnRate * 0.5 * dt
        return ((Int(updated) % 360) + 360) % 360
    }
}
