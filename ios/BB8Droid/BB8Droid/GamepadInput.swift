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
    /// Set when the user has a physical pad and does not want the overlay.
    private var suppressVirtual = false
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
        ) { [weak self] _ in
            Task { @MainActor in
                // GCVirtualController populates its `controller` property
                // *asynchronously* after connect(), so this notification can
                // arrive before the identity check can recognise our own pad.
                // One turn of deferral is the difference between a stable UI and
                // dismissing the on-screen controller the instant we create it.
                try? await Task.sleep(for: .milliseconds(80))
                self?.adoptController()
            }
        }

        NotificationCenter.default.addObserver(
            forName: .GCControllerDidDisconnect, object: nil, queue: .main
        ) { [weak self] notification in
            Task { @MainActor in
                guard let self else { return }
                // Ignore our own on-screen pad disconnecting; otherwise
                // dismissing it re-presents it, forever.
                if let disconnected = notification.object as? GCController,
                   disconnected === self.virtual?.controller { return }
                self.adoptController()
            }
        }
        adoptController()
    }

    /// The first *physical* controller, if any.
    ///
    /// `GCController.controllers()` includes the on-screen pad, so this filter
    /// is load-bearing: without it the virtual controller looks like a real one,
    /// gets dismissed as redundant, fires a disconnect, and is presented again —
    /// an oscillation that flickers the whole UI several times a second.
    private var physicalController: GCController? {
        GCController.controllers().first { $0 !== virtual?.controller }
    }

    /// Reconcile which controller we are driving from.
    ///
    /// Fails *safe*: the on-screen pad is only dismissed when a physical
    /// controller has been positively identified. If identity is ambiguous we
    /// keep touch control, because the failure mode of guessing wrong is a user
    /// with no way to stop a moving droid.
    private func adoptController() {
        if let controller = physicalController {
            dismissVirtualController()          // a real pad supersedes touch
            state.controllerName = controller.vendorName ?? "Controller"
            state.usingVirtualController = false
            sample.connected = true
        } else if virtual == nil {
            presentVirtualController()
        }
    }

    /// On-screen stick, presented through the same `extendedGamepad` interface.
    func presentVirtualController() {
        guard virtual == nil, !suppressVirtual else { return }
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

    /// Hide the on-screen pad. Used both when a real controller appears and
    /// when the user turns it off explicitly.
    func setVirtualControllerHidden(_ hidden: Bool) {
        suppressVirtual = hidden
        if hidden { dismissVirtualController() } else { adoptController() }
    }

    func dismissVirtualController() {
        guard let controller = virtual else { return }
        virtual = nil                 // cleared first, so the disconnect
        controller.disconnect()       // notification recognises it as ours
        state.usingVirtualController = false
    }

    /// Poll the current stick position.
    ///
    /// Polling rather than events: at 60 Hz we want where the stick *is*, not a
    /// backlog of where it was. An event queue here would reintroduce exactly
    /// the staleness the single-slot cell exists to prevent.
    func poll() -> Sample {
        // Prefer a physical pad; fall back to the on-screen one.
        let active = physicalController ?? virtual?.controller
        guard let pad = active?.extendedGamepad else {
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
