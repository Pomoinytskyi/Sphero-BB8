import Foundation
import BB8Kit

/// One owner of the radio, enforcing the command cadence.
///
/// Input arrives at 60 Hz; the drive loop sends at 30. Bridging that gap by
/// *queueing* is what makes a droid execute stick input from seconds ago. So
/// the drive path is a **single slot, not a queue** — new input overwrites
/// un-sent input, and latency cannot accumulate because there is nowhere for it
/// to accumulate.
@MainActor
final class Transmitter {
    /// Seconds between packets.
    ///
    /// Hardware-measured ceiling is 89 pkt/s (11.2 ms) using writes without
    /// response, so 30 Hz sits at about a third of it — responsive, with room
    /// left for lighting and for whatever the link does on a bad day.
    static let defaultInterval: Double = 1.0 / 30

    private let link: DroidLink
    private let state: DroidState
    private var cell: Control.DriveCommand?
    private var lastSent: Control.DriveCommand?
    private var urgent = false
    private var background: [Data] = []
    private var task: Task<Void, Never>?
    private var sequence: UInt8 = 0

    var interval: Double = Transmitter.defaultInterval

    init(link: DroidLink, state: DroidState) {
        self.link = link
        self.state = state
    }

    private func nextSequence() -> UInt8 {
        sequence &+= 1
        return sequence
    }

    /// Overwrite the desired drive state. Never blocks, never queues.
    ///
    /// Superseding an un-sent command is the design working, so it is counted
    /// rather than treated as loss.
    func setDrive(_ command: Control.DriveCommand, urgent: Bool = false) {
        if cell != nil { state.coalesced += 1 }
        cell = command
        if urgent { self.urgent = true }
    }

    /// Panic stop. Pre-empts whatever is pending.
    func emergencyStop() {
        setDrive(Control.DriveCommand(speed: 0, heading: state.heading, kind: .stop), urgent: true)
    }

    /// Queue a one-off command (lighting, setup).
    ///
    /// Bounded and droppable: background traffic must never be able to starve
    /// the control loop.
    func enqueue(_ packet: Data) {
        guard background.count < 32 else { return }
        background.append(packet)
    }

    /// A parked droid needs no ROLL stream. Skipping unchanged stop commands
    /// frees most of the budget for lighting exactly when driving is not using it.
    private func shouldSend(_ command: Control.DriveCommand) -> Bool {
        if urgent { return true }
        guard let lastSent else { return true }
        if command.kind == .stop && lastSent.kind == .stop { return false }
        return command != lastSent
    }

    func start() {
        task?.cancel()
        task = Task { @MainActor in
            // Paced against a *deadline*, not by sleeping a fixed interval.
            // Sleeping `interval` and then writing makes the real period
            // `interval + work`, which measured 13.9 Hz against a 30 Hz target
            // on the Python side. Advancing a deadline absorbs the work time.
            var deadline = ContinuousClock.now
            while !Task.isCancelled {
                deadline = deadline.advanced(by: .seconds(interval))
                let now = ContinuousClock.now
                if deadline > now {
                    try? await Task.sleep(until: deadline, clock: .continuous)
                } else {
                    // Fell behind: resync rather than sprinting to catch up,
                    // which would burst packets at the droid.
                    deadline = now
                    await Task.yield()
                }
                guard state.isReady else { continue }

                var sentDrive = false
                if let command = cell, shouldSend(command) {
                    link.send(command.packet(seq: nextSequence()))
                    lastSent = command
                    state.speed = command.speed
                    state.heading = command.heading
                    sentDrive = true
                }
                cell = nil
                urgent = false

                // One background packet per tick, and only when driving did not
                // need the slot — so lighting is structurally incapable of
                // delaying the droid.
                if !sentDrive, !background.isEmpty {
                    link.send(background.removeFirst())
                }
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
    }
}
