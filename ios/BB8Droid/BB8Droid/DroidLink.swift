import CoreBluetooth
import Foundation
import BB8Kit

/// CoreBluetooth transport and connection lifecycle.
///
/// The iOS counterpart of the Python `transport` + `session` modules. All the
/// protocol knowledge lives in `BB8Kit`; this file is only responsible for
/// getting bytes onto and off the radio, and for the connection state machine.
///
/// Requires `NSBluetoothAlwaysUsageDescription` in Info.plist. Without it iOS
/// kills the process rather than prompting — the same silent SIGABRT that
/// caught the Python tool on macOS, with the same absence of diagnostics.
@MainActor
final class DroidLink: NSObject {
    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var commandCharacteristic: CBCharacteristic?
    private var wakeCharacteristic: CBCharacteristic?
    private var antiDOSCharacteristic: CBCharacteristic?
    private var txPowerCharacteristic: CBCharacteristic?

    private var decoder = SpheroDecoder()
    private var sequence: UInt8 = 0
    private var pending: [UInt8: CheckedContinuation<Sphero.Response, Error>] = [:]
    private var masks: (primary: UInt32, extended: UInt32) = (0, 0)

    private let state: DroidState
    private var wantsConnection = false

    /// Motion stops if no command arrives within this window — a firmware-level
    /// dead-man switch. If the app is backgrounded or crashes mid-roll, the
    /// droid halts instead of driving until the battery dies.
    private let motionTimeoutMs = 2000

    enum LinkError: Error, LocalizedError {
        case notReady, timeout, bluetoothUnavailable

        var errorDescription: String? {
            switch self {
            case .notReady: "Droid is not connected"
            case .timeout: "No reply from the droid"
            case .bluetoothUnavailable: "Bluetooth unavailable"
            }
        }
    }

    /// When true, CoreBluetooth callbacks stop writing to shared state.
    ///
    /// The Simulator has no radio, so `centralManagerDidUpdateState` fires with
    /// `.poweredOff` shortly after init and overwrites whatever the preview
    /// harness set up. Suppressing the writes is cleaner than having the
    /// harness fight the delegate.
    private let suppressed: Bool

    init(state: DroidState, suppressed: Bool = false) {
        self.state = state
        self.suppressed = suppressed
        super.init()
        guard !suppressed else { return }
        central = CBCentralManager(delegate: self, queue: .main)
    }

    // MARK: - Connection

    func connect() {
        guard !suppressed else { return }
        wantsConnection = true
        guard central.state == .poweredOn else { return }   // handled on state change
        startScan()
    }

    func disconnect() {
        wantsConnection = false
        Task { await stopDroid() }
        if let peripheral { central.cancelPeripheralConnection(peripheral) }
        state.link = .idle
    }

    private func startScan() {
        state.link = .scanning
        state.linkDetail = ""
        // Scanning by service UUID is unreliable for these toys — the BLE
        // service is not always in the advertisement — so filter by name.
        central.scanForPeripherals(withServices: nil)
    }

    // MARK: - Sending

    private func nextSequence() -> UInt8 {
        sequence &+= 1
        return sequence
    }

    /// Fire and forget. The hot path — must never wait.
    ///
    /// Uses `.withoutResponse`, which measured 89 pkt/s against 16.7 for
    /// `.withResponse` on this hardware. A dropped drive packet is superseded
    /// by the next one milliseconds later, so the guarantee is not worth 5x.
    func send(_ packet: Data) {
        guard !suppressed else { return }
        guard let peripheral, let characteristic = commandCharacteristic else { return }
        for chunk in Sphero.chunked(packet) {
            peripheral.writeValue(chunk, for: characteristic, type: .withoutResponse)
        }
        state.packetsSent += 1
    }

    /// Send an acknowledged command and await its reply.
    ///
    /// Only for setup and probing — using this on the drive path is what caps
    /// throughput near 8 commands/sec.
    @discardableResult
    func request(_ make: (UInt8) -> Data, timeout: Duration = .seconds(3)) async throws -> Sphero.Response {
        guard let peripheral, let characteristic = commandCharacteristic else { throw LinkError.notReady }
        let seq = nextSequence()
        var packet = make(seq)
        // Force the acknowledge bit, then repair the checksum it invalidates.
        packet[1] = Sphero.SOP2.answer
        let body = Array(packet[2 ..< packet.count - 1])
        packet[packet.count - 1] = Sphero.checksum(body)

        // Reliable write: we are about to block on a reply, so a dropped write
        // would surface as a timeout and look like a dead droid.
        for chunk in Sphero.chunked(packet) {
            peripheral.writeValue(chunk, for: characteristic, type: .withResponse)
        }
        state.packetsSent += 1

        return try await withThrowingTaskGroup(of: Sphero.Response.self) { group in
            group.addTask { @MainActor in
                try await withCheckedThrowingContinuation { continuation in
                    self.pending[seq] = continuation
                }
            }
            group.addTask {
                try await Task.sleep(for: timeout)
                throw LinkError.timeout
            }
            defer { group.cancelAll() }
            let result = try await group.next()!
            self.pending[seq] = nil
            return result
        }
    }

    // MARK: - Session configuration

    /// Connect-time setup, as one idempotent unit.
    ///
    /// Deliberately a single replayable step so reconnection is a *replay*
    /// rather than a re-derivation — scattered init is how a reconnected
    /// session ends up subtly different from a fresh one.
    func configure(streamHz: Double = 8) async {
        // Without SET_STABILIZATION the control system stays off and ROLL is
        // silently inert: well-formed packets, motionless droid, no error.
        send(Sphero.setStabilization(true, seq: nextSequence()))
        send(Sphero.setMotionTimeout(milliseconds: motionTimeoutMs, seq: nextSequence()))
        send(Sphero.setBackLED(brightness: 64, seq: nextSequence()))
        send(Sphero.configureCollisionDetection(seq: nextSequence()))

        if let masks = try? SpheroSensors.masks(for: SpheroSensors.drivePreset) {
            self.masks = masks
            send(Sphero.setDataStreaming(
                divisor: SpheroSensors.divisor(forHz: streamHz),
                mask: masks.primary, extendedMask: masks.extended, seq: nextSequence()
            ))
        }
        await readPower()
    }

    @discardableResult
    func readPower() async -> Sphero.PowerState? {
        guard let response = try? await request({ Sphero.getPowerState(seq: $0) }) else { return nil }
        let power = Sphero.PowerState(payload: response.payload)
        state.battery = power
        if let power, !power.isHealthy {
            state.log("battery", String(format: "%.2f V — low", power.volts))
        }
        return power
    }

    func stopDroid() async {
        send(Sphero.stop(heading: state.heading, seq: nextSequence()))
    }

    // MARK: - Incoming

    private func handle(_ message: Sphero.Message) {
        switch message {
        case .response(let response):
            if let continuation = pending.removeValue(forKey: response.seq) {
                continuation.resume(returning: response)
            }
        case .async(let async):
            handleAsync(async)
        }
    }

    private func handleAsync(_ message: Sphero.AsyncMessage) {
        switch message.kind {
        case .sensorData:
            if let samples = try? SpheroSensors.decodeFrame(
                message.payload, primary: masks.primary, extended: masks.extended),
               let latest = samples.last {
                state.telemetry = latest
            }
        case .collisionDetected:
            state.log("collision")
            NotificationCenter.default.post(name: .droidCollision, object: nil)
        case .sleepingSoon:
            state.log("sleeping-soon", "droid will sleep")
        case .didSleep:
            state.log("slept", "droid slept")
            state.link = .degraded
            state.linkDetail = "droid went to sleep"
        default:
            break
        }
    }
}

extension Notification.Name {
    static let droidCollision = Notification.Name("droidCollision")
}

// MARK: - CBCentralManagerDelegate

extension DroidLink: CBCentralManagerDelegate {
    nonisolated func centralManagerDidUpdateState(_ central: CBCentralManager) {
        Task { @MainActor in
            guard !suppressed else { return }
            switch central.state {
            case .poweredOn:
                if wantsConnection { startScan() }
            case .unauthorized:
                state.link = .unauthorized
                state.linkDetail = "Allow Bluetooth for this app in Settings"
            case .poweredOff:
                state.link = .bluetoothOff
                state.linkDetail = "Turn Bluetooth on"
            default:
                state.link = .idle
            }
        }
    }

    nonisolated func centralManager(
        _ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
        advertisementData: [String: Any], rssi RSSI: NSNumber
    ) {
        let advertised = advertisementData[CBAdvertisementDataLocalNameKey] as? String
        let name = advertised ?? peripheral.name ?? ""
        guard name.hasPrefix(Sphero.namePrefix) else { return }

        Task { @MainActor in
            guard self.peripheral == nil else { return }
            central.stopScan()
            self.peripheral = peripheral
            peripheral.delegate = self
            state.droidName = name
            state.link = .connecting
            central.connect(peripheral)
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        Task { @MainActor in
            state.link = .handshaking
            peripheral.discoverServices([
                CBUUID(string: Sphero.bleService), CBUUID(string: Sphero.robotService),
            ])
        }
    }

    nonisolated func centralManager(
        _ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?
    ) {
        Task { @MainActor in
            self.peripheral = nil
            commandCharacteristic = nil
            decoder = SpheroDecoder()
            for (_, continuation) in pending { continuation.resume(throwing: LinkError.notReady) }
            pending.removeAll()

            guard wantsConnection else {
                state.link = .idle
                return
            }
            // Reconnect automatically; the operator should never have to restart
            // the app because the link blipped.
            state.reconnects += 1
            state.link = .reconnecting
            state.log("reconnecting", error?.localizedDescription ?? "link dropped")
            try? await Task.sleep(for: .seconds(1))
            if wantsConnection { startScan() }
        }
    }
}

// MARK: - CBPeripheralDelegate

extension DroidLink: CBPeripheralDelegate {
    nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        Task { @MainActor in
            for service in peripheral.services ?? [] {
                peripheral.discoverCharacteristics(nil, for: service)
            }
        }
    }

    nonisolated func peripheral(
        _ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?
    ) {
        Task { @MainActor in
            for characteristic in service.characteristics ?? [] {
                switch characteristic.uuid.uuidString.lowercased() {
                case Sphero.commandCharacteristic: commandCharacteristic = characteristic
                case Sphero.responseCharacteristic: peripheral.setNotifyValue(true, for: characteristic)
                case Sphero.wakeCharacteristic: wakeCharacteristic = characteristic
                case Sphero.antiDOSCharacteristic: antiDOSCharacteristic = characteristic
                case Sphero.txPowerCharacteristic: txPowerCharacteristic = characteristic
                default: break
                }
            }
            if commandCharacteristic != nil, antiDOSCharacteristic != nil, wakeCharacteristic != nil {
                await performHandshake(peripheral)
            }
        }
    }

    /// Order matters. Writing anti-DOS *before* subscribing and waking is what
    /// makes the droid answer at all; skip it and you get a connected, silent
    /// peripheral with no error to explain it.
    private func performHandshake(_ peripheral: CBPeripheral) async {
        guard let antiDOS = antiDOSCharacteristic, let wake = wakeCharacteristic else { return }
        peripheral.writeValue(Data("011i3".utf8), for: antiDOS, type: .withResponse)
        if let txPower = txPowerCharacteristic {
            peripheral.writeValue(Data([0x07]), for: txPower, type: .withResponse)
        }
        state.link = .waking
        peripheral.writeValue(Data([0x01]), for: wake, type: .withResponse)

        try? await Task.sleep(for: .milliseconds(400))   // firmware needs a moment

        // PING gates readiness: connected is not ready.
        do {
            _ = try await request({ Sphero.ping(seq: $0) }, timeout: .seconds(3))
            state.link = .ready
            state.linkDetail = ""
            state.log("connected", state.droidName ?? "")
            await configure()
        } catch {
            state.link = .degraded
            state.linkDetail = "connected but no reply — droid asleep or flat?"
        }
    }

    nonisolated func peripheral(
        _ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?
    ) {
        guard let value = characteristic.value else { return }
        Task { @MainActor in
            state.packetsReceived += 1
            for message in decoder.feed(value) { handle(message) }
        }
    }
}
