import Foundation
import Observation
import BB8Kit

/// Named connection states, so a failure says *which step* failed.
///
/// Three very different problems — droid asleep, handshake wrong, droid flat —
/// look identical from outside if the app tracks only a boolean.
public enum LinkState: String, Sendable {
    case idle, bluetoothOff, unauthorized, scanning, connecting
    case handshaking, waking, ready, degraded, reconnecting

    var label: String {
        switch self {
        case .idle: "idle"
        case .bluetoothOff: "Bluetooth off"
        case .unauthorized: "no permission"
        case .scanning: "scanning"
        case .connecting: "connecting"
        case .handshaking: "handshaking"
        case .waking: "waking"
        case .ready: "ready"
        case .degraded: "degraded"
        case .reconnecting: "reconnecting"
        }
    }

    var isBusy: Bool {
        switch self {
        case .scanning, .connecting, .handshaking, .waking, .reconnecting: true
        default: false
        }
    }
}

public enum ControlMode: String, Sendable {
    case idle, driving, aiming, estop
}

struct DroidEvent: Identifiable {
    let id = UUID()
    let at: Date
    let kind: String
    let detail: String
}

/// Shared observable state. The views read it; nothing else writes it.
@MainActor
@Observable
final class DroidState {
    var link: LinkState = .idle
    var linkDetail: String = ""
    var droidName: String?
    var reconnects: Int = 0

    var control: ControlMode = .idle
    var driveMode: Control.DriveMode = .absolute
    var profile: Control.SpeedProfile = Control.tortoise

    var heading: Int = 0
    var speed: Int = 0
    var stick: (x: Double, y: Double) = (0, 0)
    /// Signed tank throttle: triggers, or stick Y as a fallback.
    var throttle: Double = 0

    var battery: Sphero.PowerState?
    var telemetry: [String: Double] = [:]
    var usingVirtualController = false
    var controllerName: String?

    var packetsSent = 0
    var packetsReceived = 0
    var coalesced = 0
    private(set) var events: [DroidEvent] = []

    var isReady: Bool { link == .ready }

    /// Measured speed in cm/s, used to drive reactive lighting.
    var measuredSpeed: Double { telemetry["speed"] ?? 0 }

    func log(_ kind: String, _ detail: String = "") {
        events.append(DroidEvent(at: .now, kind: kind, detail: detail))
        if events.count > 100 { events.removeFirst(events.count - 100) }
    }
}
