import Foundation
import Observation
import BB8Kit

/// The control loop: gamepad to drive cell at 60 Hz.
///
/// Reads input twice as fast as the radio sends, deliberately. The extra
/// samples keep the drive cell holding a *current* desired state, so whichever
/// tick the transmitter fires on, it sends where the stick is now.
@MainActor
@Observable
final class DroidController {
    let state: DroidState
    let link: DroidLink
    let input: GamepadInput
    let transmitter: Transmitter
    let lighting: Lighting

    private var loop: Task<Void, Never>?
    private var batteryLoop: Task<Void, Never>?
    private var aimHeading = 0
    private var estopLatched = false

    private let controlHz: Double = 60

    init(suppressLink: Bool = false) {
        let state = DroidState()
        let link = DroidLink(state: state, suppressed: suppressLink)
        let transmitter = Transmitter(link: link, state: state)
        self.state = state
        self.link = link
        self.transmitter = transmitter
        self.input = GamepadInput(state: state)
        self.lighting = Lighting(state: state, transmitter: transmitter)
    }

    func start() {
        link.connect()
        transmitter.start()
        lighting.start()

        loop?.cancel()
        loop = Task { @MainActor in
            var previous = ContinuousClock.now
            var deadline = previous
            while !Task.isCancelled {
                deadline = deadline.advanced(by: .seconds(1 / controlHz))
                try? await Task.sleep(until: deadline, clock: .continuous)
                let now = ContinuousClock.now
                let dt = Double(previous.duration(to: now).components.attoseconds) / 1e18
                    + Double(previous.duration(to: now).components.seconds)
                previous = now
                step(dt: max(dt, 1 / 240))
            }
        }

        batteryLoop?.cancel()
        batteryLoop = Task { @MainActor in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(30))
                if state.isReady { await link.readPower() }
            }
        }
    }

    func stop() {
        loop?.cancel(); batteryLoop?.cancel()
        lighting.stop()
        Task { await link.stopDroid() }
        transmitter.stop()
        link.disconnect()
    }

    /// One control tick. Kept free of timing so it can be reasoned about — and
    /// tested — as an ordinary function.
    func step(dt: Double) {
        let sample = input.poll()
        state.stick = (sample.lx, sample.ly)
        let edges = input.edges()
        handle(edges: edges)

        guard state.isReady else { return }

        if input.isHeld("aim") {
            // Aim is modal: the stick rotates the heading reference via ROLL
            // mode 2 and normal drive output is suppressed, so the two can
            // never emit at once.
            if state.control != .aiming {
                aimHeading = state.heading
                state.log("aim", "entered")
            }
            state.control = .aiming
            aimHeading = Control.aimDelta(x: sample.lx, profile: state.profile,
                                          heading: aimHeading, dt: dt)
            transmitter.setDrive(Control.DriveCommand(speed: 0, heading: aimHeading, kind: .calibrate))
            return
        }
        if state.control == .aiming {
            state.log("aim", "released")
            state.control = .idle
        }

        // Tank mode throttles on the triggers, falling back to stick Y. Absolute
        // mode ignores the throttle argument — there the stick *is* the command.
        let throttle = Control.tankThrottle(
            stickY: sample.ly, leftTrigger: sample.lt, rightTrigger: sample.rt
        )
        state.throttle = throttle
        let command = Control.map(x: sample.lx, y: sample.ly, throttle: throttle,
                                  mode: state.driveMode, profile: state.profile,
                                  heading: state.heading, dt: dt)

        // An e-stop stays latched until the stick recentres, so releasing the
        // panic button does not immediately resume driving.
        if estopLatched {
            if command.kind == .stop {
                estopLatched = false
                state.control = .idle
            } else {
                return
            }
        }

        state.control = command.kind == .stop ? .idle : .driving
        transmitter.setDrive(command)
    }

    private func handle(edges: Set<String>) {
        if input.pressed("estop", in: edges) {
            transmitter.emergencyStop()
            estopLatched = true
            state.control = .estop
            state.log("estop", "operator")
        }
        if input.pressed("toggleDriveMode", in: edges) {
            state.driveMode = state.driveMode == .absolute ? .tank : .absolute
            state.log("drive-mode", state.driveMode.rawValue)
        }
        if input.pressed("toggleProfile", in: edges) {
            state.profile = state.profile.name == "tortoise" ? Control.rabbit : Control.tortoise
            state.log("profile", state.profile.name)
        }
        if input.pressed("resetAim", in: edges) {
            transmitter.enqueue(Sphero.setHeading(0))
            state.log("aim", "reset")
        }
    }

    // Exposed for the UI, which offers the same actions as the pad so the app
    // is usable with no controller attached.
    func toggleDriveMode() { handle(edges: ["x"]) }
    func toggleProfile() { handle(edges: ["y"]) }
    func resetAim() { handle(edges: ["a"]) }
    func emergencyStop() { handle(edges: ["b"]) }
}
