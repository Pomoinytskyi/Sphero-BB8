import XCTest
@testable import BB8Kit

/// Control-math tests, mirroring the Python suite case for case.
///
/// Heaviest coverage sits on the deadzone and the atan2 convention: both are
/// classic sign/quadrant error sites, and both fail *plausibly* rather than
/// obviously — the droid drives, just not where you pointed.
final class ControlTests: XCTestCase {

    // MARK: - Deadzone

    func testCentreIsDead() {
        let (x, y) = Control.radialDeadzone(x: 0, y: 0, deadzone: 0.12)
        XCTAssertEqual(x, 0); XCTAssertEqual(y, 0)
    }

    /// A square gate lets one axis creep while the other is suppressed, which
    /// reads as the droid drifting on its own.
    func testDeadzoneIsRadialNotSquare() {
        let inside = Control.radialDeadzone(x: 0.11, y: 0, deadzone: 0.12)   // |v| = 0.11
        XCTAssertEqual(inside.x, 0)

        let outside = Control.radialDeadzone(x: 0.11, y: 0.11, deadzone: 0.12) // |v| = 0.156
        XCTAssertNotEqual(outside.x, 0)
    }

    /// Without rescaling, output jumps 0 → deadzone at the threshold.
    func testOutputIsRescaledToFullRange() {
        let (_, y) = Control.radialDeadzone(x: 0, y: 1, deadzone: 0.2)
        XCTAssertEqual(y, 1.0, accuracy: 1e-9)
    }

    func testJustOutsideDeadzoneIsNearZero() {
        let (_, y) = Control.radialDeadzone(x: 0, y: 0.13, deadzone: 0.12)
        XCTAssertGreaterThan(y, 0)
        XCTAssertLessThan(y, 0.02)
    }

    /// Square-gated hardware reports |v| up to 1.41 on diagonals; uncorrected,
    /// that makes diagonal travel 41% faster than straight.
    func testDiagonalIsClampedToTheUnitCircle() {
        let (x, y) = Control.radialDeadzone(x: 1, y: 1, deadzone: 0.1)
        XCTAssertLessThanOrEqual((x * x + y * y).squareRoot(), 1.0 + 1e-9)
    }

    // MARK: - Absolute mode

    func testCardinalHeadings() {
        XCTAssertEqual(Control.mapAbsolute(x: 0, y: 1, profile: Control.rabbit).heading, 0)
        XCTAssertEqual(Control.mapAbsolute(x: 1, y: 0, profile: Control.rabbit).heading, 90)
        XCTAssertEqual(Control.mapAbsolute(x: 0, y: -1, profile: Control.rabbit).heading, 180)
        XCTAssertEqual(Control.mapAbsolute(x: -1, y: 0, profile: Control.rabbit).heading, 270)
    }

    func testDiagonalForwardRightIsFortyFive() {
        XCTAssertEqual(Control.mapAbsolute(x: 0.707, y: 0.707, profile: Control.rabbit).heading, 45)
    }

    func testHeadingAlwaysInRange() {
        for i in 0 ..< 72 {
            let angle = Double(i) * 5 * .pi / 180
            let cmd = Control.mapAbsolute(x: sin(angle), y: cos(angle), profile: Control.rabbit)
            XCTAssertTrue((0 ... 359).contains(cmd.heading), "heading \(cmd.heading) out of range")
        }
    }

    func testCentredStickStops() {
        XCTAssertEqual(Control.mapAbsolute(x: 0, y: 0, profile: Control.tortoise).kind, .stop)
    }

    // MARK: - Profiles

    /// Above ~120 BB-8 is unmanageable indoors.
    func testTortoiseIsCappedWellBelowFull() {
        XCTAssertLessThanOrEqual(Control.tortoise.cap, 120)
    }

    func testSpeedNeverExceedsTheCap() {
        for profile in Control.profiles {
            for i in 0 ... 20 {
                let cmd = Control.mapAbsolute(x: 0, y: Double(i) / 20, profile: profile)
                XCTAssertLessThanOrEqual(cmd.speed, profile.cap)
            }
        }
    }

    func testTortoiseIsSlowerAtEqualDeflection() {
        XCTAssertLessThan(
            Control.mapAbsolute(x: 0, y: 0.7, profile: Control.tortoise).speed,
            Control.mapAbsolute(x: 0, y: 0.7, profile: Control.rabbit).speed
        )
    }

    // MARK: - Tank mode

    func testForwardKeepsCurrentHeading() {
        XCTAssertEqual(Control.mapTank(x: 0, y: 1, profile: Control.rabbit, heading: 90, dt: 1.0 / 60).heading, 90)
    }

    func testReverseFlipsTheBearing() {
        XCTAssertEqual(Control.mapTank(x: 0, y: -1, profile: Control.rabbit, heading: 0, dt: 1.0 / 60).heading, 180)
    }

    /// Frame-rate independence: a faster control loop must not steer faster.
    func testTurnRateIsTimeIntegrated() {
        let slow = Control.mapTank(x: 1, y: 1, profile: Control.rabbit, heading: 0, dt: 0.2).heading
        let fast = Control.mapTank(x: 1, y: 1, profile: Control.rabbit, heading: 0, dt: 0.1).heading
        XCTAssertEqual(Double(slow), Double(fast * 2), accuracy: 1.0)
    }

    /// Otherwise releasing the throttle mid-turn snaps back to the old bearing.
    func testSteeringWithoutThrottleStillTurns() {
        let cmd = Control.mapTank(x: 1, y: 0, profile: Control.rabbit, heading: 0, dt: 0.1)
        XCTAssertEqual(cmd.kind, .stop)
        XCTAssertGreaterThan(cmd.heading, 0)
    }

    // MARK: - Aiming

    func testSmallInputDoesNotDriftTheAim() {
        XCTAssertEqual(Control.aimDelta(x: 0.05, profile: Control.tortoise, heading: 10, dt: 0.1), 10)
    }

    /// Aiming is precision work; full turn rate overshoots.
    func testAimingIsSlowerThanSteering() {
        let aim = Control.aimDelta(x: 1, profile: Control.rabbit, heading: 0, dt: 0.1)
        let steer = Control.mapTank(x: 1, y: 1, profile: Control.rabbit, heading: 0, dt: 0.1).heading
        XCTAssertLessThan(aim, steer)
    }

    // MARK: - Command encoding

    /// The kind must select the right ROLL mode, or aim mode drives away.
    func testCommandKindSelectsRollMode() {
        XCTAssertEqual(Control.DriveCommand(speed: 0, heading: 90, kind: .calibrate).packet(seq: 1),
                       Sphero.calibrate(heading: 90, seq: 1))
        XCTAssertEqual(Control.DriveCommand(speed: 0, heading: 90, kind: .stop).packet(seq: 1),
                       Sphero.stop(heading: 90, seq: 1))
        XCTAssertEqual(Control.DriveCommand(speed: 50, heading: 90).packet(seq: 1),
                       Sphero.roll(speed: 50, heading: 90, seq: 1))
    }

    func testCommandClampsRatherThanTrapping() {
        XCTAssertEqual(Control.DriveCommand(speed: 999, heading: 720).speed, 255)
        XCTAssertEqual(Control.DriveCommand(speed: -5, heading: -90).heading, 270)
    }

    func testEveryMappedCommandIsValid() {
        for i in -10 ... 10 {
            for j in -10 ... 10 {
                for mode in Control.DriveMode.allCases {
                    let cmd = Control.map(x: Double(i) / 10, y: Double(j) / 10,
                                          mode: mode, profile: Control.rabbit, heading: 200)
                    XCTAssertTrue((0 ... 255).contains(cmd.speed))
                    XCTAssertTrue((0 ... 359).contains(cmd.heading))
                }
            }
        }
    }
}
