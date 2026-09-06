import XCTest
@testable import BB8Kit

/// Decoder tests. The reassembly cases matter most: BLE hands you arbitrary
/// fragments, and a decoder that loses sync makes a live droid look deaf.
final class DecoderTests: XCTestCase {

    private func response(code: UInt8 = 0, seq: UInt8 = 5, payload: [UInt8] = []) -> Data {
        let body: [UInt8] = [code, seq, UInt8(payload.count + 1)] + payload
        return Data([0xFF, 0xFF] + body + [Sphero.checksum(body)])
    }

    private func asyncMessage(id: UInt8 = 0x03, payload: [UInt8] = [1, 2]) -> Data {
        let dlen = payload.count + 1
        let body: [UInt8] = [id, UInt8(dlen >> 8), UInt8(dlen & 0xFF)] + payload
        return Data([0xFF, 0xFE] + body + [Sphero.checksum(body)])
    }

    func testDecodesResponse() {
        var decoder = SpheroDecoder()
        let messages = decoder.feed(response(seq: 5))
        guard case .response(let r) = messages.first else { return XCTFail("no response") }
        XCTAssertEqual(r.seq, 5)
        XCTAssertTrue(r.isOK)
    }

    /// Async frames carry a two-byte length where responses carry one. Reading
    /// the wrong width desynchronises the stream permanently.
    func testDecodesAsyncWithTwoByteLength() {
        var decoder = SpheroDecoder()
        let messages = decoder.feed(asyncMessage(id: 0x07, payload: [0xAA, 0xBB]))
        guard case .async(let a) = messages.first else { return XCTFail("no async") }
        XCTAssertEqual(a.kind, .collisionDetected)
        XCTAssertEqual(a.payload, [0xAA, 0xBB])
    }

    /// Worst case: BLE delivers one byte at a time. Nothing may be lost.
    func testReassemblesAcrossFragments() {
        var decoder = SpheroDecoder()
        let frame = response(seq: 0x11, payload: [1, 2, 3])
        var collected: [Sphero.Message] = []
        for byte in frame {
            collected += decoder.feed(Data([byte]))
        }
        XCTAssertEqual(collected.count, 1)
    }

    func testDecodesTwoFramesInOneChunk() {
        var decoder = SpheroDecoder()
        XCTAssertEqual(decoder.feed(response(seq: 1) + asyncMessage()).count, 2)
    }

    /// Sensor data arrives interleaved with command replies on one characteristic.
    func testDemultiplexesInterleavedTraffic() {
        var decoder = SpheroDecoder()
        let stream = asyncMessage() + response(seq: 2) + asyncMessage(id: 0x07)
        let kinds = decoder.feed(stream).map { message -> String in
            if case .response = message { return "response" }
            return "async"
        }
        XCTAssertEqual(kinds, ["async", "response", "async"])
    }

    func testResynchronisesAfterGarbage() {
        var decoder = SpheroDecoder()
        let messages = decoder.feed(Data([0x00, 0x11, 0x22]) + response(seq: 9))
        guard case .response(let r) = messages.first else { return XCTFail("no response") }
        XCTAssertEqual(r.seq, 9)
    }

    func testPartialFrameWaitsRatherThanEmitting() {
        var decoder = SpheroDecoder()
        let frame = response(seq: 3, payload: [1, 2, 3])
        XCTAssertTrue(decoder.feed(frame.prefix(4)).isEmpty)
        XCTAssertEqual(decoder.feed(frame.dropFirst(4)).count, 1)
    }

    /// A malformed stream must not be able to kill the notification path.
    func testNeverCrashesOnArbitraryBytes() {
        var decoder = SpheroDecoder()
        var generator = SystemRandomNumberGenerator()
        for _ in 0 ..< 200 {
            let junk = (0 ..< 16).map { _ in UInt8.random(in: 0 ... 255, using: &generator) }
            _ = decoder.feed(Data(junk))
        }
    }

    // MARK: - Sensors

    func testSensorRateIsADivisorNotMilliseconds() {
        XCTAssertEqual(SpheroSensors.hz(forDivisor: 100), 4.0, accuracy: 0.001)
        XCTAssertEqual(SpheroSensors.hz(forDivisor: 50), 8.0, accuracy: 0.001)
        XCTAssertEqual(SpheroSensors.hz(forDivisor: 25), 16.0, accuracy: 0.001)
    }

    func testDecodesScaledSample() throws {
        let (p, e) = try SpheroSensors.masks(for: ["yaw", "locatorX", "locatorY"])
        let payload: [UInt8] = [0, 45, 0, 100, 0xFF, 0xCE]   // 45, 100, -50
        let sample = try SpheroSensors.decodeSample(payload, primary: p, extended: e)
        XCTAssertEqual(sample["yaw"], 45)
        XCTAssertEqual(sample["locatorX"], 100)
        XCTAssertEqual(sample["locatorY"], -50)
    }

    /// Silently dropping a typo would shift every later field.
    func testUnknownSensorThrows() {
        XCTAssertThrowsError(try SpheroSensors.masks(for: ["yaw", "nope"]))
    }

    /// With no field tags on the wire, a wrong mask yields confidently wrong
    /// numbers rather than an error. Fail loudly instead.
    func testWrongLengthThrows() throws {
        let (p, e) = try SpheroSensors.masks(for: ["yaw", "locatorX"])
        XCTAssertThrowsError(try SpheroSensors.decodeSample([0, 1], primary: p, extended: e))
    }
}
