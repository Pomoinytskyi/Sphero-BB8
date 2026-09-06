"""Preflight tests.

This check exists because the failure it detects is *silent*: exit code 134, no
traceback, no stderr, no crash report. Without it, the first hardware session
would be spent debugging working code.
"""

import subprocess
from unittest.mock import patch

from bb8ctl import preflight
from bb8ctl.preflight import Authorization


def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


class TestAuthorizationStates:
    def test_denied_reports_guidance_without_probing(self):
        with patch.object(preflight, "authorization", return_value=Authorization.DENIED), \
             patch("subprocess.run") as run:
            ok, message = preflight.bluetooth_available()
        assert not ok and "Privacy & Security" in message
        run.assert_not_called()

    def test_restricted_is_distinguished_from_denied(self):
        """Policy restriction is not something the user can click through."""
        with patch.object(preflight, "authorization", return_value=Authorization.RESTRICTED):
            ok, message = preflight.bluetooth_available()
        assert not ok and "restricted" in message.lower()

    def test_allowed_skips_the_subprocess_probe(self):
        with patch.object(preflight, "authorization", return_value=Authorization.ALLOWED), \
             patch("subprocess.run") as run:
            ok, _ = preflight.bluetooth_available()
        assert ok
        run.assert_not_called()


class TestSubprocessProbe:
    def test_sigabrt_is_recognised_as_the_permission_failure(self):
        """The signature symptom: killed by signal 6 with no output at all."""
        with patch.object(preflight, "authorization", return_value=Authorization.NOT_DETERMINED), \
             patch("subprocess.run", return_value=_completed(-6)):
            ok, message = preflight.bluetooth_available()
        assert not ok and "Bluetooth access" in message

    def test_clean_exit_means_bluetooth_works(self):
        with patch.object(preflight, "authorization", return_value=Authorization.NOT_DETERMINED), \
             patch("subprocess.run", return_value=_completed(0)):
            ok, message = preflight.bluetooth_available()
        assert ok and message == ""

    def test_other_failures_surface_their_own_error(self):
        """A powered-off adapter is a different problem with a different fix."""
        with patch.object(preflight, "authorization", return_value=Authorization.NOT_DETERMINED), \
             patch("subprocess.run", return_value=_completed(1, "BleakError: turned off")):
            ok, message = preflight.bluetooth_available()
        assert not ok and "turned off" in message

    def test_timeout_is_reported_not_raised(self):
        with patch.object(preflight, "authorization", return_value=Authorization.NOT_DETERMINED), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
            ok, message = preflight.bluetooth_available()
        assert not ok and "Timed out" in message

    def test_probe_actually_starts_a_scan(self):
        """Constructing a CBCentralManager does not trip the abort; only
        scanning does. A weaker probe passes and the real command then dies."""
        assert "discover" in preflight._CHECK_SCRIPT

    def test_probe_runs_out_of_process(self):
        """An in-process check cannot survive the abort it is checking for."""
        with patch.object(preflight, "authorization", return_value=Authorization.NOT_DETERMINED), \
             patch("subprocess.run", return_value=_completed(0)) as run:
            preflight.bluetooth_available()
        assert run.called


class TestRequireBluetooth:
    def test_returns_false_and_prints_when_unavailable(self, capsys):
        with patch.object(preflight, "bluetooth_available", return_value=(False, "nope")):
            assert preflight.require_bluetooth() is False
        assert "nope" in capsys.readouterr().err

    def test_returns_true_silently_when_available(self, capsys):
        with patch.object(preflight, "bluetooth_available", return_value=(True, "")):
            assert preflight.require_bluetooth() is True
        assert capsys.readouterr().err == ""
