import GameController
import Observation
import SwiftUI

/// Controller input via Apple's GameController framework.
///
/// Named inputs rather than positional indices: an Xbox pad pairs natively in
/// Settings since iOS 13, and `GCExtendedGamepad` exposes buttons by identity,
/// so bindings survive a controller swap.
///
/// `GCVirtualController` presents an on-screen stick through the *same*
/// interface, so the touch fallback costs no extra control code — one input
/// path, two hardware sources.
@MainActor
@Observable
final class GamepadInput {
    struct Sample {
        var lx: Double = 0
        var ly: Double = 0
        var buttons: Set<String> = []
        var connected = false
    }

    private(set) var sample = Sample()
    private var previousButtons: Set<String> = []
    private var virtual: GCVirtualController?
    private let state: DroidState

    /// Actions bound to named buttons. Aim is a *hold*; the rest are edges.
    static let bindings: [String: String] = [
        "aim": "rb", "estop": "b", "toggleDriveMode": "x",
        "toggleProfile": "y", "resetAim": "a",
    ]

    init(state: DroidState) {
        self.state = state
        NotificationCenter.default.addObserver(
            forName: .GCControllerDidConnect, object: nil, queue: .main
        ) { [weak self] _ in Task { @MainActor in self?.adoptController() } }

        NotificationCenter.default.addObserver(
            forName: .GCControllerDidDisconnect, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.state.controllerName = nil
                self?.sample.connected = false
                // Fall back to touch rather than leaving the user with no
                // control at all mid-drive.
                self?.presentVirtualController()
            }
        }
        adoptController()
    }

    private func adoptController() {
        guard let controller = GCController.controllers().first else {
            presentVirtualController()
            return
        }
        // A real pad supersedes the on-screen one.
        dismissVirtualController()
        state.controllerName = controller.vendorName
        state.usingVirtualController = false
        sample.connected = true
    }

    /// On-screen stick, presented through the same `extendedGamepad` interface.
    func presentVirtualController() {
        guard virtual == nil else { return }
        let configuration = GCVirtualController.Configuration()
        configuration.elements = [
            GCInputLeftThumbstick, GCInputButtonA, GCInputButtonB,
            GCInputButtonX, GCInputButtonY,
        ]
        let controller = GCVirtualController(configuration: configuration)
        controller.connect()
        virtual = controller
        state.usingVirtualController = true
        state.controllerName = "on-screen"
        sample.connected = true
    }

    func dismissVirtualController() {
        virtual?.disconnect()
        virtual = nil
        state.usingVirtualController = false
    }

    /// Poll the current stick position.
    ///
    /// Polling rather than events: at 60 Hz we want where the stick *is*, not a
    /// backlog of where it was. An event queue here would reintroduce exactly
    /// the staleness the single-slot cell exists to prevent.
    func poll() -> Sample {
        guard let pad = GCController.controllers().first?.extendedGamepad else {
            sample.connected = false
            return sample
        }
        var buttons: Set<String> = []
        if pad.buttonA.isPressed { buttons.insert("a") }
        if pad.buttonB.isPressed { buttons.insert("b") }
        if pad.buttonX.isPressed { buttons.insert("x") }
        if pad.buttonY.isPressed { buttons.insert("y") }
        if pad.rightShoulder.isPressed { buttons.insert("rb") }
        if pad.leftShoulder.isPressed { buttons.insert("lb") }

        sample.lx = Double(pad.leftThumbstick.xAxis.value)
        sample.ly = Double(pad.leftThumbstick.yAxis.value)   // already +ve = away
        sample.buttons = buttons
        sample.connected = true
        return sample
    }

    /// Buttons that went down since the last call.
    ///
    /// Mode toggles must fire once per press: level-triggering would flip the
    /// drive mode sixty times a second while held.
    func edges() -> Set<String> {
        let down = sample.buttons.subtracting(previousButtons)
        previousButtons = sample.buttons
        return down
    }

    func isHeld(_ action: String) -> Bool {
        guard let binding = Self.bindings[action] else { return false }
        return sample.buttons.contains(binding)
    }

    func pressed(_ action: String, in edges: Set<String>) -> Bool {
        guard let binding = Self.bindings[action] else { return false }
        return edges.contains(binding)
    }
}
