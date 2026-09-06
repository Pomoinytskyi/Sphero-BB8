"""Control-math tests. No droid, no controller -- pure functions.

Heaviest coverage is on the deadzone and the atan2 convention. Both are classic
sign/quadrant error sites, and both fail *plausibly* rather than obviously: the
droid drives, just not where you pointed.
"""

import math

import pytest

from bb8ctl import control as c


class TestRadialDeadzone:
    def test_centre_is_dead(self):
        assert c.radial_deadzone(0.0, 0.0, 0.12) == (0.0, 0.0)

    def test_inside_deadzone_is_dead(self):
        assert c.radial_deadzone(0.05, 0.05, 0.12) == (0.0, 0.0)

    def test_deadzone_is_radial_not_square(self):
        """A square gate lets one axis creep while the other is suppressed,
        which reads as the droid drifting on its own."""
        x, y = c.radial_deadzone(0.11, 0.11, 0.12)   # |v| = 0.156, outside
        assert (x, y) != (0.0, 0.0)
        assert c.radial_deadzone(0.11, 0.0, 0.12) == (0.0, 0.0)   # |v| = 0.11, inside

    def test_output_is_rescaled_to_full_range(self):
        """Without rescaling, output jumps 0 -> deadzone at the threshold."""
        x, y = c.radial_deadzone(0.0, 1.0, 0.2)
        assert y == pytest.approx(1.0)

    def test_just_outside_deadzone_is_near_zero_not_a_jump(self):
        _, y = c.radial_deadzone(0.0, 0.13, 0.12)
        assert 0.0 < y < 0.02

    def test_diagonal_is_clamped_to_the_unit_circle(self):
        """Square-gated hardware reports |v| up to 1.41 on diagonals; uncorrected
        that makes diagonal travel 41% faster than straight."""
        x, y = c.radial_deadzone(1.0, 1.0, 0.1)
        assert math.hypot(x, y) <= 1.0 + 1e-9

    def test_direction_is_preserved(self):
        x, y = c.radial_deadzone(0.6, 0.8, 0.1)
        assert math.atan2(x, y) == pytest.approx(math.atan2(0.6, 0.8))


class TestExpo:
    def test_endpoints_are_fixed(self):
        assert c.apply_expo(0.0, 2.0) == 0.0
        assert c.apply_expo(1.0, 2.0) == 1.0

    def test_expo_softens_the_middle(self):
        assert c.apply_expo(0.5, 2.0) < 0.5

    def test_linear_when_exponent_is_one(self):
        assert c.apply_expo(0.5, 1.0) == pytest.approx(0.5)

    def test_monotonic(self):
        vals = [c.apply_expo(i / 20, 2.0) for i in range(21)]
        assert vals == sorted(vals)


class TestAbsoluteMode:
    def test_forward_is_heading_zero(self):
        assert c.map_absolute(0.0, 1.0, c.RABBIT).heading == 0

    def test_right_is_ninety(self):
        assert c.map_absolute(1.0, 0.0, c.RABBIT).heading == 90

    def test_back_is_one_eighty(self):
        assert c.map_absolute(0.0, -1.0, c.RABBIT).heading == 180

    def test_left_is_two_seventy(self):
        assert c.map_absolute(-1.0, 0.0, c.RABBIT).heading == 270

    def test_diagonal_forward_right_is_forty_five(self):
        assert c.map_absolute(0.707, 0.707, c.RABBIT).heading == 45

    def test_heading_is_always_in_range(self):
        for i in range(72):
            angle = i * 5 * math.pi / 180
            cmd = c.map_absolute(math.sin(angle), math.cos(angle), c.RABBIT)
            assert 0 <= cmd.heading <= 359

    def test_centred_stick_stops(self):
        cmd = c.map_absolute(0.0, 0.0, c.TORTOISE)
        assert cmd.stop and cmd.speed == 0

    def test_full_deflection_reaches_the_cap(self):
        assert c.map_absolute(0.0, 1.0, c.TORTOISE).speed == c.TORTOISE.cap
        assert c.map_absolute(0.0, 1.0, c.RABBIT).speed == c.RABBIT.cap


class TestProfiles:
    def test_tortoise_is_capped_well_below_full(self):
        """Above ~120 BB-8 is unmanageable indoors."""
        assert c.TORTOISE.cap <= 120

    def test_rabbit_reaches_full_range(self):
        assert c.RABBIT.cap == 255

    def test_tortoise_is_slower_at_equal_deflection(self):
        assert (c.map_absolute(0.0, 0.7, c.TORTOISE).speed
                < c.map_absolute(0.0, 0.7, c.RABBIT).speed)

    def test_speed_never_exceeds_the_cap(self):
        for profile in (c.TORTOISE, c.RABBIT):
            for i in range(21):
                assert c.map_absolute(0.0, i / 20, profile).speed <= profile.cap

    def test_both_profiles_are_registered(self):
        assert set(c.PROFILES) == {"tortoise", "rabbit"}


class TestTankMode:
    def test_forward_keeps_the_current_heading(self):
        assert c.map_tank(0.0, 1.0, c.RABBIT, heading=90, dt=1 / 60).heading == 90

    def test_reverse_flips_the_bearing(self):
        assert c.map_tank(0.0, -1.0, c.RABBIT, heading=0, dt=1 / 60).heading == 180

    def test_steering_right_increases_heading(self):
        assert c.map_tank(1.0, 1.0, c.RABBIT, heading=0, dt=0.1).heading > 0

    def test_steering_wraps_below_zero(self):
        assert c.map_tank(-1.0, 1.0, c.RABBIT, heading=0, dt=0.1).heading > 180

    def test_turn_rate_is_time_integrated(self):
        """Frame-rate independence: a faster control loop must not steer faster."""
        slow = c.map_tank(1.0, 1.0, c.RABBIT, heading=0, dt=0.2).heading
        fast = c.map_tank(1.0, 1.0, c.RABBIT, heading=0, dt=0.1).heading
        assert slow == pytest.approx(fast * 2, abs=1)

    def test_steering_without_throttle_still_turns(self):
        """Otherwise releasing the throttle mid-turn snaps back to the old bearing."""
        cmd = c.map_tank(1.0, 0.0, c.RABBIT, heading=0, dt=0.1)
        assert cmd.stop and cmd.heading > 0

    def test_centred_stick_stops(self):
        assert c.map_tank(0.0, 0.0, c.TORTOISE, heading=45, dt=1 / 60).stop


class TestDriveCommand:
    def test_rejects_out_of_range_speed(self):
        with pytest.raises(ValueError):
            c.DriveCommand(speed=300, heading=0)

    def test_rejects_out_of_range_heading(self):
        with pytest.raises(ValueError):
            c.DriveCommand(speed=0, heading=360)

    def test_every_mapped_command_is_valid(self):
        """The mappers must never construct an invalid command."""
        for i in range(-10, 11):
            for j in range(-10, 11):
                x, y = i / 10, j / 10
                for mode in c.DriveMode:
                    c.map_input(x, y, mode, c.RABBIT, heading=200, dt=1 / 60)


class TestAiming:
    def test_small_input_does_not_drift_the_aim(self):
        assert c.aim_delta(0.05, c.TORTOISE, heading=10, dt=0.1) == 10

    def test_aim_rotates_with_stick(self):
        assert c.aim_delta(1.0, c.TORTOISE, heading=0, dt=0.1) > 0

    def test_aiming_is_slower_than_steering(self):
        """Aiming is precision work; full turn rate overshoots."""
        aim = c.aim_delta(1.0, c.RABBIT, heading=0, dt=0.1)
        steer = c.map_tank(1.0, 1.0, c.RABBIT, heading=0, dt=0.1).heading
        assert aim < steer

    def test_aim_wraps(self):
        assert 0 <= c.aim_delta(-1.0, c.RABBIT, heading=0, dt=0.1) <= 359


class TestTankDecoupling:
    """The regression this redesign exists to prevent.

    Steering and throttle are independent commands. With a radial deadzone and
    circular clamp, a full forward-and-right push normalised to (0.707, 0.707),
    so turning quietly cut speed by 30% -- which is what made tank mode feel
    like the stick was fighting itself.
    """

    def test_steering_does_not_reduce_throttle(self):
        straight = c.map_tank(0.0, 1.0, c.RABBIT, heading=0, dt=1 / 60)
        turning = c.map_tank(1.0, 1.0, c.RABBIT, heading=0, dt=1 / 60)
        assert straight.speed == turning.speed == c.RABBIT.cap

    def test_throttle_does_not_reduce_steering(self):
        coasting = c.map_tank(1.0, 0.0, c.RABBIT, heading=0, dt=0.1)
        driving = c.map_tank(1.0, 1.0, c.RABBIT, heading=0, dt=0.1)
        assert coasting.heading == driving.heading


class TestAxialDeadzone:
    def test_suppresses_small_input(self):
        assert c.axial_deadzone(0.05, 0.12) == 0.0
        assert c.axial_deadzone(-0.05, 0.12) == 0.0

    def test_preserves_sign_and_reaches_full_range(self):
        assert c.axial_deadzone(1.0, 0.12) == pytest.approx(1.0)
        assert c.axial_deadzone(-1.0, 0.12) == pytest.approx(-1.0)

    def test_continuous_at_the_threshold(self):
        assert 0.0 < c.axial_deadzone(0.13, 0.12) < 0.02


class TestTankThrottleSource:
    """tank_throttle conditions its output, so these assert behaviour rather
    than pass-through: the deadzone applied depends on the input's source."""

    def test_triggers_take_precedence_over_stick(self):
        # Stick pushed fully forward, trigger only half -- the trigger wins.
        assert c.tank_throttle(1.0, 0.0, 0.5) < 0.6

    def test_left_trigger_reverses(self):
        assert c.tank_throttle(0.0, 0.8, 0.0) < 0

    def test_right_trigger_drives_forward(self):
        assert c.tank_throttle(0.0, 0.0, 0.8) > 0

    def test_both_triggers_cancel(self):
        assert c.tank_throttle(0.0, 0.6, 0.6) == pytest.approx(0.0)

    def test_full_trigger_reaches_full_throttle(self):
        assert c.tank_throttle(0.0, 0.0, 1.0) == pytest.approx(1.0)

    def test_stick_is_the_fallback(self):
        assert c.tank_throttle(-0.7, 0.0, 0.0) < 0
        assert c.tank_throttle(0.0, 0.0, 0.0) == 0.0

    def test_triggers_use_a_smaller_deadzone_than_the_stick(self):
        """A trigger rests at exactly zero, so 12% of its travel is wasted.
        The same light input should register on a trigger but not a stick."""
        light = 0.06
        assert c.tank_throttle(light, 0.0, 0.0) == 0.0        # stick: below 0.12
        assert c.tank_throttle(0.0, 0.0, light) > 0.0         # trigger: above 0.03

    def test_trigger_response_is_monotonic(self):
        values = [c.tank_throttle(0.0, 0.0, i / 20) for i in range(21)]
        assert values == sorted(values)

    def test_absolute_mode_ignores_throttle(self):
        with_throttle = c.map_input(0.0, 1.0, c.DriveMode.ABSOLUTE, c.RABBIT, throttle=0.0)
        without = c.map_input(0.0, 1.0, c.DriveMode.ABSOLUTE, c.RABBIT)
        assert with_throttle == without
