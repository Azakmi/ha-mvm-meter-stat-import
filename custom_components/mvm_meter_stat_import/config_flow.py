"""Config flow for MVM smart meter report imports."""

import calendar
from pathlib import Path

import voluptuous as vol

from homeassistant import config_entries

from .const import CONF_DIRECTORY, CONF_ROLLOVER_DAY, CONF_ROLLOVER_MONTH, DOMAIN
from .importer import ReportError, load_reports


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a folder containing MVM smart meter CSV reports."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.FlowResult:
        """Ask for the report folder and validate its contents."""
        errors: dict[str, str] = {}
        if user_input is not None:
            month = user_input.get(CONF_ROLLOVER_MONTH)
            day = user_input.get(CONF_ROLLOVER_DAY)
            if (month is None) != (day is None) or (
                month is not None
                and day is not None
                and day > calendar.monthrange(2000, month)[1]
            ):
                errors["base"] = "invalid_rollover_date"

            directory = Path(user_input[CONF_DIRECTORY]).expanduser().resolve()
            if "base" not in errors and not directory.is_dir():
                errors["base"] = "directory_not_found"
            elif "base" not in errors:
                try:
                    data = await self.hass.async_add_executor_job(
                        load_reports, directory, self.hass.config.time_zone
                    )
                except ReportError:
                    errors["base"] = "invalid_report"
                else:
                    await self.async_set_unique_id(data.meter_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"MVM Smart Meter {data.meter_id}",
                        data={
                            CONF_DIRECTORY: str(directory),
                            **(
                                {
                                    CONF_ROLLOVER_MONTH: month,
                                    CONF_ROLLOVER_DAY: day,
                                }
                                if month is not None and day is not None
                                else {}
                            ),
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DIRECTORY): str,
                    vol.Optional(CONF_ROLLOVER_MONTH): vol.All(
                        int, vol.Range(min=1, max=12)
                    ),
                    vol.Optional(CONF_ROLLOVER_DAY): vol.All(
                        int, vol.Range(min=1, max=31)
                    ),
                }
            ),
            errors=errors,
        )
