"""Tests for MVM import status, refresh, and diagnostics controls."""

from datetime import UTC, datetime
from decimal import Decimal
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.sensor import SensorDeviceClass
from custom_components.mvm_meter_stat_import import (
    MvmMeterCoordinator,
    async_migrate_entry,
)
from custom_components.mvm_meter_stat_import.button import MvmRefreshButton
from custom_components.mvm_meter_stat_import.config_flow import ConfigFlow
from custom_components.mvm_meter_stat_import.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.mvm_meter_stat_import.importer import (
    MeterData,
    MeterReading,
    ReportData,
    ReportError,
)
from custom_components.mvm_meter_stat_import.sensor import (
    MvmEnergySensor,
    MvmImportStatusSensor,
    MvmLastSuccessfulRefreshSensor,
    MvmYearlyCostSensor,
    SENSORS,
    estimate_yearly_cost,
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
        self.coordinator.data = ReportData(
            meters={
                "private-meter-serial": MeterData(
                    meter_id="private-meter-serial",
                    series={
                        "daily_import": (reading,),
                        "daily_export": (),
                        "interval_import": (),
                        "interval_export": (),
                        "daily_net": (),
                    },
                )
            }
        )
        self.coordinator.last_update_success = True
        self.coordinator.last_successful_refresh = self.last_successful_refresh
        self.coordinator.async_add_listener.return_value = lambda: None
        self.status = MvmImportStatusSensor(self.coordinator, "private-meter-serial")
        self.last_success = MvmLastSuccessfulRefreshSensor(
            self.coordinator, "private-meter-serial"
        )

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

    def test_removed_meter_remains_safe_in_status_entities(self) -> None:
        """A missing meter's entities do not break other folder refreshes."""
        self.coordinator.data = ReportData(meters={})

        sensor = MvmEnergySensor(self.coordinator, SENSORS[0], "private-meter-serial")

        self.assertFalse(sensor.available)
        self.assertIsNone(sensor.native_value)
        self.assertTrue(self.status.available)
        self.assertFalse(self.status.extra_state_attributes["meter_present"])

    async def test_coordinator_refresh_timestamp_survives_failure_and_recovers(
        self,
    ) -> None:
        """Only successful report loads update the last-success timestamp."""
        meter = MeterData(
            meter_id="private-meter-serial",
            series={
                key: () for key in self.coordinator.data.meters[
                    "private-meter-serial"
                ].series
            },
        )
        data = ReportData(meters={meter.meter_id: meter})
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

    async def test_statistics_are_imported_with_meter_specific_ids(self) -> None:
        """Statistics metadata and values remain isolated by meter."""
        readings = {
            "meter-1": MeterReading(
                datetime(2026, 11, 12, tzinfo=UTC),
                Decimal("10"),
                (1, "one.csv", 2),
            ),
            "meter-2": MeterReading(
                datetime(2026, 11, 12, tzinfo=UTC),
                Decimal("90"),
                (1, "two.csv", 2),
            ),
        }
        data = ReportData(
            meters={
                meter_id: MeterData(
                    meter_id=meter_id,
                    series={
                        "daily_import": (reading,),
                        "daily_export": (),
                        "interval_import": (),
                        "interval_export": (),
                        "daily_net": (),
                    },
                )
                for meter_id, reading in readings.items()
            }
        )
        coordinator = object.__new__(MvmMeterCoordinator)
        coordinator.hass = SimpleNamespace(
            config=SimpleNamespace(time_zone="UTC"),
            async_add_executor_job=AsyncMock(return_value=data),
        )
        coordinator.entry = SimpleNamespace(data={"directory": "/private"})
        coordinator._signature = None
        coordinator.last_successful_refresh = None

        with patch(
            "custom_components.mvm_meter_stat_import.async_add_external_statistics"
        ) as add_statistics:
            await coordinator._async_update_data()

        statistics = {
            call.args[1]["statistic_id"]: call.args[2][0]["state"]
            for call in add_statistics.call_args_list
        }
        self.assertEqual(
            statistics,
            {
                "mvm_meter_stat_import:meter-1_daily_import": 10.0,
                "mvm_meter_stat_import:meter-2_daily_import": 90.0,
            },
        )

    async def test_existing_entry_migrates_to_folder_uniqueness(self) -> None:
        """One-meter entries retain their data while uniqueness moves to folder."""
        entry = SimpleNamespace(
            version=1,
            data={"directory": "/private/reports"},
        )
        update_entry = Mock()
        hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_update_entry=update_entry)
        )

        self.assertTrue(await async_migrate_entry(hass, entry))

        update_entry.assert_called_once_with(
            entry,
            unique_id="/private/reports",
            title="MVM reports (reports)",
            version=2,
        )

    def test_meter_entities_use_separate_devices_and_stable_ids(self) -> None:
        """Each meter's entities remain attached to its own device."""
        other_meter = MeterData(
            meter_id="other-meter",
            series={key: () for key in self.coordinator.data.meters[
                "private-meter-serial"
            ].series},
        )
        self.coordinator.data = ReportData(
            meters={
                **self.coordinator.data.meters,
                other_meter.meter_id: other_meter,
            }
        )

        first = MvmEnergySensor(self.coordinator, SENSORS[0], "private-meter-serial")
        second = MvmEnergySensor(self.coordinator, SENSORS[0], "other-meter")

        self.assertEqual(first.unique_id, "private-meter-serial_daily_import")
        self.assertEqual(second.unique_id, "other-meter_daily_import")
        self.assertNotEqual(
            first.device_info["identifiers"], second.device_info["identifiers"]
        )

    async def test_refresh_button_requests_coordinator_refresh(self) -> None:
        """The button requests an immediate coordinator refresh."""
        self.coordinator.async_request_refresh = AsyncMock()
        button = MvmRefreshButton(self.coordinator, "private-meter-serial")

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


class YearlyCostSensorTests(unittest.TestCase):
    """Verify tariff estimates use the latest signed yearly saldo."""

    def setUp(self) -> None:
        reading = MeterReading(
            timestamp=datetime(2026, 11, 12, tzinfo=UTC),
            value=Decimal("1000"),
            priority=(1, "report.csv", 2),
        )
        self.options = {
            "monthly_fixed_cost": 10,
            "export_price": 0.05,
            "import_price_first_tier": 0.2,
            "import_price_above_threshold": 0.3,
            "currency": "EUR",
        }
        self.coordinator = Mock()
        self.coordinator.data = ReportData(
            meters={
                "meter-1": MeterData(
                    meter_id="meter-1",
                    series={
                        "daily_import": (),
                        "daily_export": (),
                        "interval_import": (),
                        "interval_export": (),
                        "daily_net": (reading,),
                    },
                )
            }
        )
        self.coordinator.entry.options = self.options
        self.coordinator.async_add_listener.return_value = lambda: None
        self.sensor = MvmYearlyCostSensor(self.coordinator, "meter-1")

    def test_import_tiers_below_at_and_above_threshold(self) -> None:
        rates = {
            "monthly_fixed_cost": Decimal("10"),
            "export_price": Decimal("0.05"),
            "import_price_first_tier": Decimal("0.2"),
            "import_price_above_threshold": Decimal("0.3"),
        }

        self.assertEqual(
            estimate_yearly_cost(Decimal("1000"), **rates), Decimal("320.0")
        )
        self.assertEqual(
            estimate_yearly_cost(Decimal("2523"), **rates), Decimal("624.60")
        )
        self.assertEqual(
            estimate_yearly_cost(Decimal("3000"), **rates), Decimal("767.70")
        )

    def test_negative_saldo_receives_export_credit_and_fixed_cost(self) -> None:
        self.assertEqual(self.sensor.native_value, Decimal("320.00"))

        readings = self.coordinator.data.meters["meter-1"].series["daily_net"]
        self.coordinator.data.meters["meter-1"].series["daily_net"] = (
            MeterReading(readings[0].timestamp, Decimal("-1000"), readings[0].priority),
        )
        self.assertEqual(self.sensor.native_value, Decimal("70.00"))

        self.coordinator.data.meters["meter-1"].series["daily_net"] = (
            MeterReading(readings[0].timestamp, Decimal("0"), readings[0].priority),
        )
        self.assertEqual(self.sensor.native_value, Decimal("120"))

    def test_currency_metadata_and_missing_configuration_or_saldo(self) -> None:
        self.assertEqual(
            self.sensor.device_class, SensorDeviceClass.MONETARY
        )
        self.assertEqual(self.sensor.native_unit_of_measurement, "EUR")
        self.assertTrue(self.sensor.available)

        del self.options["import_price_first_tier"]
        self.assertFalse(self.sensor.available)
        self.assertIsNone(self.sensor.native_value)
        self.options["import_price_first_tier"] = 0.2
        self.coordinator.data.meters["meter-1"].series["daily_net"] = ()
        self.assertFalse(self.sensor.available)
        self.assertIsNone(self.sensor.native_value)


class YearlyCostOptionsTests(unittest.IsolatedAsyncioTestCase):
    """Verify tariff options are stored with an explicit uppercase currency."""

    async def test_currency_is_normalized_and_empty_options_are_allowed(self) -> None:
        flow = ConfigFlow.async_get_options_flow(None)
        flow.async_create_entry = Mock(return_value={"type": "create_entry"})
        values = {
            "monthly_fixed_cost": 10.5,
            "export_price": 0.05,
            "import_price_first_tier": 0.2,
            "import_price_above_threshold": 0.3,
            "currency": "eur",
        }

        result = await flow.async_step_init(values)

        self.assertEqual(result, {"type": "create_entry"})
        flow.async_create_entry.assert_called_once_with(
            title="",
            data={
                **values,
                "currency": "EUR",
            },
        )

        empty_flow = ConfigFlow.async_get_options_flow(None)
        empty_flow.async_create_entry = Mock(return_value={"type": "create_entry"})
        await empty_flow.async_step_init({})
        empty_flow.async_create_entry.assert_called_once_with(title="", data={})


if __name__ == "__main__":
    unittest.main()
