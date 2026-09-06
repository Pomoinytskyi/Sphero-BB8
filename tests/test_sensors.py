"""Sensor layout and decoding tests.

Field *ordering* gets the most attention here. The wire format carries no tags,
so an ordering bug produces plausible numbers rather than an error -- the worst
kind of defect, and the one most likely to survive to the Swift port.
"""

import pytest

from bb8ctl import sensors as s


class TestMasks:
    def test_build_masks_splits_primary_and_extended(self):
        primary, extended = s.build_masks(["yaw", "locator_x"])
        assert primary == 0x00010000
        assert extended == 0x08000000

    def test_build_masks_ors_multiple_bits(self):
        primary, _ = s.build_masks(["pitch", "roll", "yaw"])
        assert primary == 0x00040000 | 0x00020000 | 0x00010000

    def test_empty_selection_is_zero(self):
        assert s.build_masks([]) == (0, 0)

    def test_unknown_sensor_raises_rather_than_skipping(self):
        """Silently dropping a typo would shift every later field."""
        with pytest.raises(s.UnknownSensor):
            s.build_masks(["yaw", "nonexistent"])

    def test_drive_preset_is_valid(self):
        primary, extended = s.build_masks(s.DRIVE_PRESET)
        assert primary and extended


class TestLayout:
    def test_fields_come_back_in_descending_bit_order(self):
        """The wire order contract. Requested order is irrelevant."""
        primary, extended = s.build_masks(["yaw", "pitch", "roll"])
        assert [f.name for f in s.layout(primary, extended)] == ["pitch", "roll", "yaw"]

    def test_request_order_does_not_affect_layout(self):
        a = s.layout(*s.build_masks(["locator_y", "locator_x"]))
        b = s.layout(*s.build_masks(["locator_x", "locator_y"]))
        assert a == b == (s.BY_NAME["locator_x"], s.BY_NAME["locator_y"])

    def test_primary_fields_precede_extended_fields(self):
        primary, extended = s.build_masks(["locator_x", "yaw"])
        assert [f.name for f in s.layout(primary, extended)] == ["yaw", "locator_x"]

    def test_expected_length_is_two_bytes_per_field(self):
        primary, extended = s.build_masks(["yaw", "locator_x", "speed"])
        assert s.expected_length(primary, extended) == 6


class TestDecoding:
    def test_decodes_signed_values(self):
        primary, extended = s.build_masks(["yaw"])
        assert s.decode_sample((-90).to_bytes(2, "big", signed=True), primary, extended) == {"yaw": -90}

    def test_applies_scale_factors(self):
        primary, extended = s.build_masks(["accel_x"])
        got = s.decode_sample((4096).to_bytes(2, "big", signed=True), primary, extended)
        assert got["accel_x"] == pytest.approx(1.0)      # 4096 raw == 1 g

    def test_gyro_scale(self):
        primary, extended = s.build_masks(["gyro_z"])
        got = s.decode_sample((1000).to_bytes(2, "big", signed=True), primary, extended)
        assert got["gyro_z"] == pytest.approx(100.0)     # 0.1 deg/s per unit

    def test_decodes_multi_field_sample_in_wire_order(self):
        primary, extended = s.build_masks(["yaw", "locator_x", "locator_y"])
        payload = b"".join(v.to_bytes(2, "big", signed=True) for v in (45, 100, -50))
        assert s.decode_sample(payload, primary, extended) == {
            "yaw": 45.0, "locator_x": 100.0, "locator_y": -50.0,
        }

    def test_wrong_length_raises_instead_of_guessing(self):
        primary, extended = s.build_masks(["yaw", "locator_x"])
        with pytest.raises(s.SampleLengthError):
            s.decode_sample(b"\x00\x01", primary, extended)

    def test_frame_splits_into_multiple_samples(self):
        primary, extended = s.build_masks(["yaw"])
        payload = b"".join(v.to_bytes(2, "big", signed=True) for v in (10, 20, 30))
        assert [d["yaw"] for d in s.decode_frame(payload, primary, extended)] == [10, 20, 30]

    def test_ragged_frame_raises(self):
        primary, extended = s.build_masks(["yaw", "speed"])
        with pytest.raises(s.SampleLengthError):
            s.decode_frame(b"\x00\x01\x00\x02\x00", primary, extended)

    def test_empty_mask_decodes_to_nothing(self):
        assert s.decode_frame(b"", 0, 0) == []

    def test_round_trip_through_drive_preset(self):
        primary, extended = s.build_masks(s.DRIVE_PRESET)
        fields = s.layout(primary, extended)
        payload = b"".join((i * 3).to_bytes(2, "big", signed=True) for i in range(len(fields)))
        got = s.decode_sample(payload, primary, extended)
        assert set(got) == set(s.DRIVE_PRESET)
