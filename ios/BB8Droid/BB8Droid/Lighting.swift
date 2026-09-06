import Foundation
import SwiftUI
import BB8Kit

/// Expressive lighting — the app's signature behaviour.
///
/// Hardware measurement is what makes this possible at all. The link sustains
/// 89 packets/sec; driving at 30 Hz plus lighting at 10 Hz uses under half of
/// that, so lighting never has to compete with control. It is still routed
/// through the transmitter's background lane, which only ever uses ticks the
/// drive loop did not want — so it is *structurally* incapable of adding
/// latency to driving, rather than merely unlikely to.
@MainActor
@Observable
final class Lighting {
    enum Mood: String, CaseIterable, Identifiable {
        case off, reactive, pulse, rainbow, alert
        var id: String { rawValue }

        var label: String {
            switch self {
            case .off: "Off"
            case .reactive: "Reactive"
            case .pulse: "Pulse"
            case .rainbow: "Rainbow"
            case .alert: "Alert"
            }
        }

        var detail: String {
            switch self {
            case .off: "Lights out"
            case .reactive: "Colour tracks speed — calm when parked, hot when quick"
            case .pulse: "Slow breathing while idle"
            case .rainbow: "Continuous hue cycle"
            case .alert: "Insistent red strobe"
            }
        }
    }

    struct RGB: Equatable {
        var r: Int, g: Int, b: Int

        /// Perceptual-ish distance, used to decide whether a change is worth a packet.
        func differs(from other: RGB, by threshold: Int) -> Bool {
            abs(r - other.r) + abs(g - other.g) + abs(b - other.b) > threshold
        }

        var color: Color { Color(red: Double(r) / 255, green: Double(g) / 255, blue: Double(b) / 255) }
    }

    var mood: Mood = .reactive
    private(set) var current = RGB(r: 0, g: 0, b: 0)

    private var phase: Double = 0
    private var collisionFlash: Double = 0
    private var lastSent = RGB(r: -1, g: -1, b: -1)
    private var task: Task<Void, Never>?

    /// Update rate. Deliberately far below the drive loop — the eye cannot
    /// resolve LED changes faster than this, so spending packets on it would
    /// buy nothing.
    private let updateHz: Double = 10

    private let state: DroidState
    private let transmitter: Transmitter
    private var sequence: UInt8 = 200   // separate range from drive traffic

    init(state: DroidState, transmitter: Transmitter) {
        self.state = state
        self.transmitter = transmitter
        NotificationCenter.default.addObserver(
            forName: .droidCollision, object: nil, queue: .main
        ) { [weak self] _ in Task { @MainActor in self?.collisionFlash = 1.0 } }
    }

    func start() {
        task?.cancel()
        task = Task { @MainActor in
            var deadline = ContinuousClock.now
            while !Task.isCancelled {
                deadline = deadline.advanced(by: .seconds(1 / updateHz))
                try? await Task.sleep(until: deadline, clock: .continuous)
                tick(dt: 1 / updateHz)
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
    }

    private func tick(dt: Double) {
        phase += dt
        collisionFlash = max(0, collisionFlash - dt * 2.5)   // ~400ms decay

        var target = colour(for: mood)

        // A collision overrides whatever mood is running — it is the one thing
        // the droid should always be able to tell you about.
        if collisionFlash > 0 {
            let intensity = collisionFlash
            target = RGB(
                r: Int(255 * intensity) + Int(Double(target.r) * (1 - intensity)),
                g: Int(Double(target.g) * (1 - intensity)),
                b: Int(Double(target.b) * (1 - intensity))
            )
        }

        current = target
        guard state.isReady else { return }

        // Only spend a packet on a *material* change. A mood that barely moves
        // should cost almost nothing.
        guard target.differs(from: lastSent, by: 6) else { return }
        lastSent = target
        sequence = sequence &+ 1
        transmitter.enqueue(Sphero.setMainLED(red: target.r, green: target.g, blue: target.b,
                                              seq: sequence))
    }

    private func colour(for mood: Mood) -> RGB {
        switch mood {
        case .off:
            return RGB(r: 0, g: 0, b: 0)

        case .reactive:
            // Speed drives hue: calm blue when parked through to hot red at
            // pace. Uses *measured* speed from the sensor stream, not commanded
            // speed, so it reflects what the droid is actually doing.
            let fraction = min(1, state.measuredSpeed / 120)
            let hue = (1 - fraction) * 0.58        // 0.58 blue -> 0.0 red
            return rgb(hue: hue, saturation: 0.9, brightness: 0.35 + 0.65 * fraction)

        case .pulse:
            let breath = (sin(phase * 1.4) + 1) / 2
            let tint = state.profile.name == "rabbit" ? 0.02 : 0.33   // red vs green
            return rgb(hue: tint, saturation: 0.75, brightness: 0.15 + 0.75 * breath)

        case .rainbow:
            return rgb(hue: (phase * 0.12).truncatingRemainder(dividingBy: 1),
                       saturation: 1, brightness: 0.85)

        case .alert:
            let on = sin(phase * 9) > 0
            return RGB(r: on ? 255 : 20, g: 0, b: 0)
        }
    }

    private func rgb(hue: Double, saturation: Double, brightness: Double) -> RGB {
        let sector = hue * 6
        let index = Int(sector) % 6
        let fraction = sector - Double(Int(sector))
        let p = brightness * (1 - saturation)
        let q = brightness * (1 - saturation * fraction)
        let t = brightness * (1 - saturation * (1 - fraction))

        let (r, g, b): (Double, Double, Double) = switch index {
        case 0: (brightness, t, p)
        case 1: (q, brightness, p)
        case 2: (p, brightness, t)
        case 3: (p, q, brightness)
        case 4: (t, p, brightness)
        default: (brightness, p, q)
        }
        return RGB(r: Int(r * 255), g: Int(g * 255), b: Int(b * 255))
    }
}
