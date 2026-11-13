"""MVM smart meter statistics importer integration."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from homeassistant.components.recorder.models import (
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .const import (
    CONF_DIRECTORY,
    CONF_ROLLOVER_DAY,
    CONF_ROLLOVER_MONTH,
    DATASET_DAILY,
    DIRECTION_NET,
    DOMAIN,
    PLATFORMS,
    SERIES_KEYS,
    STATISTIC_NAMES,
    UPDATE_INTERVAL,
)
from .importer import MeterData, ReportError, build_statistics, load_reports

_LOGGER = logging.getLogger(__name__)
MvmConfigEntry = ConfigEntry[DataUpdateCoordinator[MeterData]]


class MvmMeterCoordinator(DataUpdateCoordinator[MeterData]):
    """Load reports and refresh the imported long-term statistics."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.entry = entry
        self._signature: tuple | None = None
        self.last_successful_refresh: datetime | None = None

    async def _async_update_data(self) -> MeterData:
        """Read reports and replace statistics only when their data changes."""
        try:
            data = await self.hass.async_add_executor_job(
                load_reports,
                Path(self.entry.data[CONF_DIRECTORY]),
                self.hass.config.time_zone,
            )
        except ReportError as err:
            raise UpdateFailed(str(err)) from err

        if data.signature != self._signature:
            try:
                for key in SERIES_KEYS:
                    readings = data.series[key]
                    if not readings:
                        continue
                    metadata: StatisticMetaData = {
                        "mean_type": StatisticMeanType.NONE,
                        "has_sum": True,
                        "name": f"{data.meter_id} {STATISTIC_NAMES[key]}",
                        "source": DOMAIN,
                        "statistic_id": f"{DOMAIN}:{data.meter_id}_{key}",
                        "unit_class": EnergyConverter.UNIT_CLASS,
                        "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
                    }
                    async_add_external_statistics(
                        self.hass,
                        metadata,
                        build_statistics(
                            readings,
                            cumulative_register=key.startswith(DATASET_DAILY),
                            signed_register=key == f"{DATASET_DAILY}_{DIRECTION_NET}",
                            rollover_month=self.entry.data.get(CONF_ROLLOVER_MONTH),
                            rollover_day=self.entry.data.get(CONF_ROLLOVER_DAY),
                        ),
                    )
            except (HomeAssistantError, ValueError) as err:
                _LOGGER.exception("Unable to import MVM meter statistics")
                raise UpdateFailed(
                    f"Unable to import meter statistics: {err}"
                ) from err

            self._signature = data.signature
        self.last_successful_refresh = dt_util.utcnow()
        return data


async def async_setup_entry(hass: HomeAssistant, entry: MvmConfigEntry) -> bool:
    """Set up the MVM smart meter statistics importer."""
    coordinator = MvmMeterCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MvmConfigEntry) -> bool:
    """Unload the integration platforms."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
