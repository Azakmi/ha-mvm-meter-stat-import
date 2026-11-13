"""Buttons for the MVM smart meter statistics importer."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MvmMeterCoordinator
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[MvmMeterCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the manual refresh button."""
    async_add_entities(
        [
            MvmRefreshButton(entry.runtime_data, meter_id)
            for meter_id in entry.runtime_data.data.meters
        ]
    )


class MvmRefreshButton(CoordinatorEntity[MvmMeterCoordinator], ButtonEntity):
    """Request an immediate refresh of meter reports and statistics."""

    _attr_has_entity_name = True
    _attr_translation_key = "refresh"

    def __init__(self, coordinator: MvmMeterCoordinator, meter_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{meter_id}_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter_id)},
            name=f"MVM Smart Meter {meter_id}",
            manufacturer="MVM",
            model="Smart meter CSV report",
        )

    @property
    def available(self) -> bool:
        """Remain usable to recover from a failed report refresh."""
        return True

    async def async_press(self) -> None:
        """Ask the coordinator to refresh reports and imported statistics."""
        await self.coordinator.async_request_refresh()
