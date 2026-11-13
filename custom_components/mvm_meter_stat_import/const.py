"""Constants for the MVM smart meter statistics importer."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "mvm_meter_stat_import"
PLATFORMS = [Platform.BUTTON, Platform.SENSOR]

CONF_DIRECTORY = "directory"
CONF_ROLLOVER_MONTH = "rollover_month"
CONF_ROLLOVER_DAY = "rollover_day"
CONF_MONTHLY_FIXED_COST = "monthly_fixed_cost"
CONF_EXPORT_PRICE = "export_price"
CONF_IMPORT_PRICE_FIRST_TIER = "import_price_first_tier"
CONF_IMPORT_PRICE_ABOVE_THRESHOLD = "import_price_above_threshold"
CONF_CURRENCY = "currency"
IMPORT_PRICE_THRESHOLD_KWH = 2523
UPDATE_INTERVAL = timedelta(minutes=15)

DATASET_DAILY = "daily"
DATASET_INTERVAL = "interval"

DIRECTION_IMPORT = "import"
DIRECTION_EXPORT = "export"
DIRECTION_NET = "net"

SERIES_KEYS = (
    "daily_import",
    "daily_export",
    "interval_import",
    "interval_export",
    "daily_net",
)

STATISTIC_NAMES = {
    "daily_import": "Daily imported energy",
    "daily_export": "Daily exported energy",
    "interval_import": "15-minute imported energy",
    "interval_export": "15-minute exported energy",
    "daily_net": "Yearly net energy balance",
}
