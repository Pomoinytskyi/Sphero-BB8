import Foundation
import BB8Kit

/// Drives the UI with synthetic state, for when no droid is reachable.
///
/// The Simulator has no Bluetooth radio, so without this the entire driving
/// interface — dial, telemetry, moods — can never be seen except on a physical
/// device with a live droid. That is the same problem the Python tool solved
/// with its replay transport, and the same answer: make the UI drivable from
/// recorded or synthetic data rather than only from hardware.
///
/// Enabled with the `-preview` launch argument. Never runs otherwise.
@MainActor
enum PreviewHarness {
    static var isEnabled: Bool {
        ProcessInfo.processInfo.arguments.contains("-preview")
    }

    static func start(_ controller: DroidController) {
        let state = controller.state
        state.link = .ready
        state.droidName = "BB-D36B"
        state.controllerName = "Xbox Wireless Controller"
        // Real values from the hardware session, so the layout is exercised
        // with numbers of the size it will actually meet.
        state.battery = Sphero.PowerState(payload: [0x01, 0x02, 0x03, 0x27, 0x00, 0x1F])
        state.profile = Control.rabbit
        state.log("connected", "BB-D36B")

        controller.lighting.start()

        Task { @MainActor in
            var phase = 0.0
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(50))
                phase += 0.05
                // A lazy figure-of-eight, so heading sweeps the whole circle
                // and speed rises and falls.
                let throttle = (sin(phase * 0.7) + 1) / 2
                state.heading = Int((phase * 40).truncatingRemainder(dividingBy: 360))
                state.speed = Int(throttle * Double(state.profile.cap))
                state.control = state.speed > 4 ? .driving : .idle
                state.telemetry = [
                    "speed": throttle * 110,
                    "locatorX": sin(phase * 0.3) * 180,
                    "locatorY": cos(phase * 0.4) * 140,
                    "yaw": Double(state.heading),
                ]
                state.packetsSent += 1
                if Int(phase * 10) % 130 == 0 {
                    NotificationCenter.default.post(name: .droidCollision, object: nil)
                    state.log("collision")
                }
            }
        }
    }
}
