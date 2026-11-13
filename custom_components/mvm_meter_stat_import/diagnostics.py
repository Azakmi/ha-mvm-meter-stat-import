"""Diagnostics for the MVM smart meter statistics importer."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import MvmMeterCoordinator
from .const import CONF_ROLLOVER_DAY, CONF_ROLLOVER_MONTH, SERIES_KEYS


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[MvmMeterCoordinator]
) -> dict[str, Any]:
    """Return import health and counts without exposing identifying report data."""
    coordinator = entry.runtime_data
    latest_reading = max(
        (
            reading.timestamp
            for meter in coordinator.data.meters.values()
            for readings in meter.series.values()
            for reading in readings
        ),
        key=lambda timestamp: timestamp.timestamp(),
        default=None,
    )
    return {
        "config_entry": {
            "version": entry.version,
            "state": entry.state.value,
            CONF_ROLLOVER_MONTH: entry.data.get(CONF_ROLLOVER_MONTH),
            CONF_ROLLOVER_DAY: entry.data.get(CONF_ROLLOVER_DAY),
        },
        "import": {
            "last_refresh_succeeded": coordinator.last_update_success,
            "last_successful_refresh": coordinator.last_successful_refresh,
            "reading_counts": {
                key: sum(
                    len(meter.series[key])
                    for meter in coordinator.data.meters.values()
                )
                for key in SERIES_KEYS
            },
            "meter_count": len(coordinator.data.meters),
            "latest_imported_timestamp": latest_reading,
        },
    }
