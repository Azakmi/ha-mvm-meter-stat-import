"""Config flow for MVM smart meter report imports."""

from __future__ import annotations

import calendar
from pathlib import Path

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_CURRENCY,
    CONF_DIRECTORY,
    CONF_EXPORT_PRICE,
    CONF_IMPORT_PRICE_ABOVE_THRESHOLD,
    CONF_IMPORT_PRICE_FIRST_TIER,
    CONF_MONTHLY_FIXED_COST,
    CONF_ROLLOVER_DAY,
    CONF_ROLLOVER_MONTH,
    DOMAIN,
)
from .importer import ReportError, load_reports


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a folder containing MVM smart meter CSV reports."""

    VERSION = 2

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowHandler:
        """Return the flow for configuring electricity tariffs."""
        return OptionsFlowHandler()

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
                    await self.hass.async_add_executor_job(
                        load_reports, directory, self.hass.config.time_zone
                    )
                except ReportError:
                    errors["base"] = "invalid_report"
                else:
                    await self.async_set_unique_id(directory.as_posix())
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"MVM reports ({directory.name})",
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


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Configure the rates used to estimate the yearly electricity bill."""

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.FlowResult:
        """Show the tariff options form."""
        if user_input is not None:
            currency = user_input.get(CONF_CURRENCY)
            if currency is not None:
                user_input[CONF_CURRENCY] = currency.upper()
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        schema = {}
        for key in (
            CONF_MONTHLY_FIXED_COST,
            CONF_EXPORT_PRICE,
            CONF_IMPORT_PRICE_FIRST_TIER,
            CONF_IMPORT_PRICE_ABOVE_THRESHOLD,
        ):
            marker = (
                vol.Optional(key, default=current[key])
                if key in current
                else vol.Optional(key)
            )
            schema[marker] = vol.All(vol.Coerce(float), vol.Range(min=0))
        currency_marker = (
            vol.Optional(CONF_CURRENCY, default=current[CONF_CURRENCY])
            if CONF_CURRENCY in current
            else vol.Optional(CONF_CURRENCY)
        )
        schema[currency_marker] = vol.All(str, vol.Match(r"^[A-Za-z]{3}$"))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
        )
