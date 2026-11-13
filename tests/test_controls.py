"""Tests for MVM import status, refresh, and diagnostics controls."""

from datetime import UTC, datetime
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from custom_components.mvm_meter_stat_import import MvmMeterCoordinator
from custom_components.mvm_meter_stat_import.button import MvmRefreshButton
from custom_components.mvm_meter_stat_import.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.mvm_meter_stat_import.importer import (
    MeterData,
    MeterReading,
    ReportError,
)
from custom_components.mvm_meter_stat_import.sensor import (
    MvmImportStatusSensor,
    MvmLastSuccessfulRefreshSensor,
)
from homeassistant.helpers.update_coordinator import UpdateFailed


class ImportControlsTests(unittest.IsolatedAsyncioTestCase):
    """Verify refresh state remains visible and diagnostics are safe."""

    def setUp(self) -> None:
        """Create a coordinator stand-in with a representative imported reading."""
        self.last_successful_refresh = datetime(2000, 1, 1, tzinfo=UTC)
        reading = MeterReading(
            timestamp=datetime(2026, 11, 12, 0, 0, tzinfo=UTC),
            value=987654.321,
            priority=(1, "private-report.csv", 2),
        )
        self.coordinator = Mock()
        self.coordinator.data = SimpleNamespace(
            meter_id="private-meter-serial",
            series={
                "daily_import": (reading,),
                "daily_export": (),
                "interval_import": (),
                "interval_export": (),
                "daily_net": (),
            },
        )
        self.coordinator.last_update_success = True
        self.coordinator.last_successful_refresh = self.last_successful_refresh
        self.coordinator.async_add_listener.return_value = lambda: None
        self.status = MvmImportStatusSensor(self.coordinator)
        self.last_success = MvmLastSuccessfulRefreshSensor(self.coordinator)

    def test_status_tracks_failure_and_recovery(self) -> None:
        """Status sensors show failed refreshes without losing the last success."""
        self.assertTrue(self.status.available)
        self.assertEqual(self.status.native_value, "Success")
        self.assertEqual(
            self.status.extra_state_attributes["reading_counts"]["daily_import"], 1
        )

        self.coordinator.last_update_success = False
        self.assertTrue(self.status.available)
        self.assertTrue(self.last_success.available)
        self.assertEqual(self.status.native_value, "Failed")
        self.assertEqual(self.last_success.native_value, self.last_successful_refresh)

        self.coordinator.last_update_success = True
        self.coordinator.last_successful_refresh = datetime(
            2026, 11, 13, 19, 15, tzinfo=UTC
        )
        self.assertEqual(self.status.native_value, "Success")
        self.assertEqual(
            self.last_success.native_value,
            datetime(2026, 11, 13, 19, 15, tzinfo=UTC),
        )

    async def test_coordinator_refresh_timestamp_survives_failure_and_recovers(
        self,
    ) -> None:
        """Only successful report loads update the last-success timestamp."""
        data = MeterData(
            meter_id="private-meter-serial",
            series={key: () for key in self.coordinator.data.series},
        )
        coordinator = object.__new__(MvmMeterCoordinator)
        coordinator.hass = SimpleNamespace(
            config=SimpleNamespace(time_zone="UTC"),
            async_add_executor_job=AsyncMock(return_value=data)
        )
        coordinator.entry = SimpleNamespace(data={"directory": "/private"})
        coordinator._signature = data.signature
        coordinator.last_successful_refresh = self.last_successful_refresh

        await coordinator._async_update_data()
        first_success = coordinator.last_successful_refresh
        self.assertGreater(first_success, self.last_successful_refresh)

        coordinator.hass.async_add_executor_job.side_effect = ReportError("bad report")
        with self.assertRaises(UpdateFailed):
            await coordinator._async_update_data()
        self.assertEqual(coordinator.last_successful_refresh, first_success)

        coordinator.hass.async_add_executor_job.side_effect = None
        coordinator.hass.async_add_executor_job.return_value = data
        await coordinator._async_update_data()
        self.assertGreater(coordinator.last_successful_refresh, first_success)

    async def test_refresh_button_requests_coordinator_refresh(self) -> None:
        """The button requests an immediate coordinator refresh."""
        self.coordinator.async_request_refresh = AsyncMock()
        button = MvmRefreshButton(self.coordinator)

        await button.async_press()

        self.coordinator.async_request_refresh.assert_awaited_once_with()
        self.coordinator.last_update_success = False
        self.assertTrue(button.available)

    async def test_diagnostics_omit_sensitive_configuration_and_reports(self) -> None:
        """Diagnostics include useful health data but no identifying content."""
        entry = SimpleNamespace(
            runtime_data=self.coordinator,
            version=1,
            state=SimpleNamespace(value="loaded"),
            data={
                "directory": "/private/config/mvm-reports",
                "rollover_month": 1,
                "rollover_day": 1,
            },
            title="private-meter-serial",
        )

        diagnostics = await async_get_config_entry_diagnostics(None, entry)
        serialized = json.dumps(diagnostics, default=str)

        self.assertTrue(diagnostics["import"]["last_refresh_succeeded"])
        self.assertEqual(diagnostics["import"]["reading_counts"]["daily_import"], 1)
        self.assertNotIn("private-meter-serial", serialized)
        self.assertNotIn("/private/config/mvm-reports", serialized)
        self.assertNotIn("private-report.csv", serialized)
        self.assertNotIn("987654.321", serialized)


if __name__ == "__main__":
    unittest.main()
