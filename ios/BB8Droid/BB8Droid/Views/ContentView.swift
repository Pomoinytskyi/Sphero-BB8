import SwiftUI
import BB8Kit

struct ContentView: View {
    let controller: DroidController
    private var state: DroidState { controller.state }

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [Color(white: 0.07), Color(white: 0.02)],
                startPoint: .top, endPoint: .bottom
            )
            .ignoresSafeArea()

            VStack(spacing: 16) {
                LinkBar(state: state, controller: controller)

                if state.isReady {
                    Spacer(minLength: 8)
                    DriveReadout(state: state)
                    Spacer(minLength: 8)
                    TelemetryStrip(state: state)
                    MoodPicker(lighting: controller.lighting)
                    ControlBar(controller: controller)
                    // The on-screen controller is a system-owned overlay across
                    // the bottom of the screen, and it does not participate in
                    // layout — so the app has to leave room for it explicitly or
                    // its buttons land on top of ours.
                    if state.usingVirtualController {
                        Color.clear.frame(height: 190)
                    }
                } else {
                    Spacer()
                    ConnectPrompt(state: state, controller: controller)
                    Spacer()
                }
            }
            .padding(.horizontal, 18)
            .padding(.top, 8)
        }
        .onAppear {
            if PreviewHarness.isEnabled { PreviewHarness.start(controller) }
            else { controller.start() }
        }
        .onDisappear { controller.stop() }
    }
}

// MARK: - Link

private struct LinkBar: View {
    let state: DroidState
    let controller: DroidController

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(tint)
                .frame(width: 9, height: 9)
                .overlay(Circle().stroke(tint.opacity(0.35), lineWidth: 5))

            VStack(alignment: .leading, spacing: 1) {
                Text(state.droidName ?? "BB-8")
                    .font(.system(.subheadline, design: .rounded).weight(.semibold))
                Text(state.linkDetail.isEmpty ? state.link.label : state.linkDetail)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            Spacer()

            if let battery = state.battery {
                Label(String(format: "%.1fV", battery.volts),
                      systemImage: battery.isHealthy ? "battery.75" : "battery.25")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(battery.isHealthy ? Color.secondary : Color.orange)
                    .labelStyle(.titleAndIcon)
            }
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 14)
        .background(.white.opacity(0.06), in: Capsule())
    }

    private var tint: Color {
        switch state.link {
        case .ready: .green
        case .degraded, .reconnecting, .bluetoothOff, .unauthorized: .orange
        default: state.link.isBusy ? .yellow : .gray
        }
    }
}

private struct ConnectPrompt: View {
    let state: DroidState
    let controller: DroidController

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: state.link.isBusy ? "dot.radiowaves.left.and.right" : "poweroutlet.type.b")
                .font(.system(size: 44, weight: .light))
                .foregroundStyle(.secondary)
                .symbolEffect(.pulse, isActive: state.link.isBusy)

            Text(headline)
                .font(.headline)
            Text(detail)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)

            if !state.link.isBusy {
                Button("Connect") { controller.link.connect() }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
            }
        }
    }

    private var headline: String {
        switch state.link {
        case .unauthorized: "Bluetooth permission needed"
        case .bluetoothOff: "Bluetooth is off"
        case .degraded: "Droid not responding"
        default: state.link.isBusy ? "Looking for BB-8…" : "Not connected"
        }
    }

    /// Says *which* step failed. Three very different problems — asleep,
    /// handshake wrong, flat battery — look identical without this.
    private var detail: String {
        switch state.link {
        case .unauthorized: "Allow Bluetooth for BB8Droid in Settings."
        case .bluetoothOff: "Turn Bluetooth on to reach the droid."
        case .degraded: state.linkDetail
        case .scanning: "Roll BB-8 or seat it briefly on its charger to wake it."
        default: "Make sure BB-8 is awake and nearby."
        }
    }
}

// MARK: - Driving

private struct DriveReadout: View {
    let state: DroidState

    var body: some View {
        VStack(spacing: 12) {
            HeadingDial(heading: state.heading, speed: state.speed,
                        cap: state.profile.cap, aiming: state.control == .aiming)
                .frame(maxWidth: 260)
                .aspectRatio(1, contentMode: .fit)

            HStack(spacing: 8) {
                Chip(text: state.control.rawValue,
                     tint: state.control == .estop ? Color.red
                         : state.control == .aiming ? Color.cyan : Color.secondary)
                Chip(text: state.driveMode.rawValue, tint: Color.secondary)
                Chip(text: state.profile.name,
                     tint: state.profile.name == "rabbit" ? Color.orange : Color.green)
            }
        }
    }
}

/// Heading and speed as one dial.
///
/// Absolute drive mode only makes sense if you know where "forward" points, so
/// the aim indicator is as important as the speed readout.
private struct HeadingDial: View {
    let heading: Int
    let speed: Int
    let cap: Int
    let aiming: Bool

    var body: some View {
        // Geometry-driven so the heading marker sits *on* the ring at any size,
        // rather than at a hardcoded radius that only matches one layout.
        GeometryReader { geometry in
            let side = min(geometry.size.width, geometry.size.height)
            let inset = side * 0.08

            ZStack {
                Circle()
                    .stroke(.white.opacity(0.09), lineWidth: 3)
                    .padding(inset)

                Circle()
                    .trim(from: 0, to: cap > 0 ? min(1, Double(speed) / Double(cap)) : 0)
                    .stroke(
                        AngularGradient(colors: [.cyan, .blue, .purple, .cyan], center: .center),
                        style: StrokeStyle(lineWidth: 9, lineCap: .round)
                    )
                    .rotationEffect(.degrees(-90))
                    .padding(inset)
                    .animation(.easeOut(duration: 0.15), value: speed)

                Image(systemName: "location.north.fill")
                    .font(.system(size: side * 0.11))
                    .foregroundStyle(aiming ? Color.cyan : Color.white)
                    .offset(y: -(side / 2 - inset))
                    .rotationEffect(.degrees(Double(heading)))
                    .shadow(color: (aiming ? Color.cyan : Color.white).opacity(0.5), radius: 6)
                    .animation(.easeOut(duration: 0.12), value: heading)

                VStack(spacing: 0) {
                    Text("\(speed)")
                        .font(.system(size: side * 0.26, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                        .contentTransition(.numericText())
                    Text("\(heading)°")
                        .font(.system(size: side * 0.07).monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
        }
    }
}

private struct TelemetryStrip: View {
    let state: DroidState

    var body: some View {
        HStack(spacing: 0) {
            metric("speed", state.telemetry["speed"].map { String(format: "%.0f", $0) } ?? "–", "cm/s")
            divider
            metric("x", state.telemetry["locatorX"].map { String(format: "%.0f", $0) } ?? "–", "cm")
            divider
            metric("y", state.telemetry["locatorY"].map { String(format: "%.0f", $0) } ?? "–", "cm")
            divider
            metric("sent", "\(state.packetsSent)", "pkt")
        }
        .padding(.vertical, 10)
        .background(.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 14))
    }

    private var divider: some View {
        Rectangle().fill(.white.opacity(0.08)).frame(width: 1, height: 26)
    }

    private func metric(_ label: String, _ value: String, _ unit: String) -> some View {
        VStack(spacing: 1) {
            Text(value).font(.system(.body, design: .rounded).weight(.medium)).monospacedDigit()
            Text("\(label) \(unit)").font(.system(size: 9)).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: - Controls

private struct MoodPicker: View {
    let lighting: Lighting

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Mood").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Circle()
                    .fill(lighting.current.color)
                    .frame(width: 14, height: 14)
                    .overlay(Circle().stroke(.white.opacity(0.2)))
                    .shadow(color: lighting.current.color.opacity(0.8), radius: 6)
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(Lighting.Mood.allCases) { mood in
                        Button {
                            lighting.mood = mood
                        } label: {
                            Text(mood.label)
                                .font(.footnote.weight(.medium))
                                .padding(.horizontal, 14).padding(.vertical, 8)
                                .background(
                                    lighting.mood == mood ? .white.opacity(0.18) : .white.opacity(0.06),
                                    in: Capsule()
                                )
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }
}

private struct ControlBar: View {
    let controller: DroidController

    var body: some View {
        HStack(spacing: 10) {
            action("Aim 0", "location.north.line") { controller.resetAim() }
            action(controller.state.driveMode == .absolute ? "Absolute" : "Tank",
                   "dpad") { controller.toggleDriveMode() }
            action(controller.state.profile.name.capitalized,
                   controller.state.profile.name == "rabbit" ? "hare" : "tortoise") {
                controller.toggleProfile()
            }
            Button {
                controller.emergencyStop()
            } label: {
                Label("Stop", systemImage: "hand.raised.fill")
                    .font(.footnote.weight(.semibold))
                    .frame(maxWidth: .infinity).padding(.vertical, 12)
                    .background(.red.opacity(0.85), in: RoundedRectangle(cornerRadius: 12))
            }
            .buttonStyle(.plain)
        }
        .padding(.bottom, 6)
    }

    private func action(_ title: String, _ icon: String, _ perform: @escaping () -> Void) -> some View {
        Button(action: perform) {
            Label(title, systemImage: icon)
                .font(.footnote.weight(.medium))
                .frame(maxWidth: .infinity).padding(.vertical, 12)
                .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
    }
}

private struct Chip: View {
    let text: String
    let tint: Color

    var body: some View {
        Text(text)
            .font(.caption2.weight(.medium))
            .padding(.horizontal, 10).padding(.vertical, 4)
            .background(tint.opacity(0.18), in: Capsule())
            .foregroundStyle(tint)
    }
}
