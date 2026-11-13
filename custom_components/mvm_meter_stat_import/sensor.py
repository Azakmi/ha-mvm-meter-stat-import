"""Sensors for the latest imported MVM meter values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MvmMeterCoordinator
from .const import (
    CONF_CURRENCY,
    CONF_EXPORT_PRICE,
    CONF_IMPORT_PRICE_ABOVE_THRESHOLD,
    CONF_IMPORT_PRICE_FIRST_TIER,
    CONF_MONTHLY_FIXED_COST,
    DOMAIN,
    IMPORT_PRICE_THRESHOLD_KWH,
    SERIES_KEYS,
    STATISTIC_NAMES,
)
from .importer import MeterReading, interval_cumulative, latest_value


@dataclass(frozen=True, kw_only=True)
class MvmSensorDescription(SensorEntityDescription):
    """Describe one imported energy sensor."""

    series_key: str


SENSORS = tuple(
    MvmSensorDescription(
        key=key,
        name=STATISTIC_NAMES[key],
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=3,
        series_key=key,
    )
    for key in SERIES_KEYS
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[MvmMeterCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the meter's energy and net-balance sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            entity
            for meter_id in coordinator.data.meters
            for entity in (
                *(
                    MvmEnergySensor(coordinator, description, meter_id)
                    for description in SENSORS
                ),
                MvmYearlyCostSensor(coordinator, meter_id),
                MvmImportStatusSensor(coordinator, meter_id),
                MvmLastSuccessfulRefreshSensor(coordinator, meter_id),
            )
        ]
    )


class MvmEnergySensor(CoordinatorEntity[MvmMeterCoordinator], SensorEntity):
    """Expose the latest report value while statistics hold the full history."""

    entity_description: MvmSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MvmMeterCoordinator,
        description: MvmSensorDescription,
        meter_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self.meter_id = meter_id
        self._attr_unique_id = f"{meter_id}_{description.series_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter_id)},
            name=f"MVM Smart Meter {meter_id}",
            manufacturer="MVM",
            model="Smart meter CSV report",
        )

    @property
    def available(self) -> bool:
        """Report unavailable if this report type/direction has no readings."""
        meter = self.coordinator.data.meters.get(self.meter_id)
        return super().available and bool(
            meter
            and meter.series[self.entity_description.series_key]
        )

    @property
    def native_value(self) -> Decimal | None:
        """Return the latest register or cumulative interval value."""
        meter = self.coordinator.data.meters.get(self.meter_id)
        if meter is None:
            return None
        readings = meter.series[self.entity_description.series_key]
        if self.entity_description.series_key.startswith("interval_"):
            return interval_cumulative(readings)
        return latest_value(readings)


class MvmImportStatusSensor(CoordinatorEntity[MvmMeterCoordinator], SensorEntity):
    """Expose the outcome and concise summary of the latest refresh."""

    _attr_has_entity_name = True
    _attr_translation_key = "import_status"

    def __init__(self, coordinator: MvmMeterCoordinator, meter_id: str) -> None:
        super().__init__(coordinator)
        self.meter_id = meter_id
        self._attr_unique_id = f"{meter_id}_import_status"
        self._attr_device_info = _device_info(meter_id)

    @property
    def available(self) -> bool:
        """Keep refresh health visible when the latest update failed."""
        return True

    @property
    def native_value(self) -> str:
        """Return whether the latest coordinator refresh succeeded."""
        return "Success" if self.coordinator.last_update_success else "Failed"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Summarize imported report data without exposing report contents."""
        meter = self.coordinator.data.meters.get(self.meter_id)
        series = meter.series if meter else {}
        readings = tuple(
            reading
            for meter_series in series.values()
            for reading in meter_series
        )
        return {
            "reading_counts": {
                key: len(series.get(key, ())) for key in SERIES_KEYS
            },
            "meter_present": meter is not None,
            "latest_imported_timestamp": _latest_timestamp(readings),
        }


class MvmYearlyCostSensor(CoordinatorEntity[MvmMeterCoordinator], SensorEntity):
    """Estimate the yearly bill from the latest signed yearly saldo."""

    _attr_has_entity_name = True
    _attr_name = "Estimated yearly electricity bill"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: MvmMeterCoordinator, meter_id: str) -> None:
        super().__init__(coordinator)
        self.meter_id = meter_id
        self._attr_unique_id = f"{meter_id}_estimated_yearly_electricity_bill"
        self._attr_device_info = _device_info(meter_id)
        currency = coordinator.entry.options.get(CONF_CURRENCY)
        if isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency):
            self._attr_device_class = SensorDeviceClass.MONETARY
            self._attr_native_unit_of_measurement = currency

    @property
    def available(self) -> bool:
        """Require complete tariff configuration and a yearly saldo reading."""
        meter = self.coordinator.data.meters.get(self.meter_id)
        return (
            super().available
            and _yearly_tariff_values(self.coordinator.entry.options) is not None
            and bool(meter and meter.series["daily_net"])
        )

    @property
    def native_value(self) -> Decimal | None:
        """Return the estimated annual charge in the configured currency."""
        meter = self.coordinator.data.meters.get(self.meter_id)
        tariffs = _yearly_tariff_values(self.coordinator.entry.options)
        if meter is None or tariffs is None:
            return None
        balance = latest_value(meter.series["daily_net"])
        if balance is None:
            return None
        return estimate_yearly_cost(balance, **tariffs)


class MvmLastSuccessfulRefreshSensor(
    CoordinatorEntity[MvmMeterCoordinator], SensorEntity
):
    """Expose the timestamp of the most recent successful refresh."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True
    _attr_translation_key = "last_successful_refresh"

    def __init__(self, coordinator: MvmMeterCoordinator, meter_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{meter_id}_last_successful_refresh"
        self._attr_device_info = _device_info(meter_id)

    @property
    def available(self) -> bool:
        """Keep the last success timestamp visible after a failed refresh."""
        return True

    @property
    def native_value(self) -> datetime | None:
        """Return the timestamp recorded by the latest successful refresh."""
        return self.coordinator.last_successful_refresh


def _device_info(meter_id: str) -> DeviceInfo:
    """Return the shared meter device information."""
    return DeviceInfo(
        identifiers={(DOMAIN, meter_id)},
        name=f"MVM Smart Meter {meter_id}",
        manufacturer="MVM",
        model="Smart meter CSV report",
    )


def _latest_timestamp(readings: tuple[MeterReading, ...]) -> datetime | None:
    """Return the latest imported timestamp, if any."""
    return max(
        (reading.timestamp for reading in readings),
        key=lambda timestamp: timestamp.timestamp(),
        default=None,
    )


def _yearly_tariff_values(options: dict[str, Any]) -> dict[str, Decimal] | None:
    """Return complete, non-negative tariff values or no estimate configuration."""
    keys = (
        CONF_MONTHLY_FIXED_COST,
        CONF_EXPORT_PRICE,
        CONF_IMPORT_PRICE_FIRST_TIER,
        CONF_IMPORT_PRICE_ABOVE_THRESHOLD,
    )
    if not all(key in options for key in (*keys, CONF_CURRENCY)):
        return None
    if not isinstance(options[CONF_CURRENCY], str) or not re.fullmatch(
        r"[A-Z]{3}", options[CONF_CURRENCY]
    ):
        return None
    try:
        values = {key: Decimal(str(options[key])) for key in keys}
    except (InvalidOperation, TypeError, ValueError):
        return None
    if any(not value.is_finite() or value < 0 for value in values.values()):
        return None
    return {
        "monthly_fixed_cost": values[CONF_MONTHLY_FIXED_COST],
        "export_price": values[CONF_EXPORT_PRICE],
        "import_price_first_tier": values[CONF_IMPORT_PRICE_FIRST_TIER],
        "import_price_above_threshold": values[CONF_IMPORT_PRICE_ABOVE_THRESHOLD],
    }


def estimate_yearly_cost(
    net_balance: Decimal,
    *,
    monthly_fixed_cost: Decimal,
    export_price: Decimal,
    import_price_first_tier: Decimal,
    import_price_above_threshold: Decimal,
) -> Decimal:
    """Estimate 12 months of fixed charges plus the balance-based energy cost."""
    fixed_cost = monthly_fixed_cost * 12
    if net_balance < 0:
        return fixed_cost - abs(net_balance) * export_price
    first_tier_kwh = min(net_balance, Decimal(IMPORT_PRICE_THRESHOLD_KWH))
    above_tier_kwh = max(net_balance - IMPORT_PRICE_THRESHOLD_KWH, Decimal(0))
    return (
        fixed_cost
        + first_tier_kwh * import_price_first_tier
        + above_tier_kwh * import_price_above_threshold
    )
