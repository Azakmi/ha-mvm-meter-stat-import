# ha-mvm-meter-stat-import

Home Assistant custom integration to import MVM smart meter CSV reports into the
Energy dashboard.

## Installation

Copy `custom_components/mvm_meter_stat_import` into the Home Assistant
configuration's `custom_components` directory, then restart Home Assistant.

In **Settings → Devices & services → Add integration**, select **MVM Smart Meter
Statistics Import** and enter a folder path on the Home Assistant host. The
folder must already contain at least one valid MVM report. Keep reports for one
meter in each folder, and do not place unrelated CSV files there. The integration
uses Home Assistant's configured time zone and checks the folder every 15 minutes.
If the net balance is billed on a yearly cycle, enter its rollover month and day
during setup. Leave both fields blank for users without an annual saldo cycle.

The reports use a semicolon delimiter and the MVM column names shown in
`example_data`. Daily rows (`YYYY. MM. DD.`) are treated as absolute register
readings. Rows with a time (`YYYY. MM. DD. HH:MM`) are treated as 15-minute
energy amounts. Values must be measured and expressed in kWh.

## Example data

The `sample_*.csv` files in `example_data` are shortened, synthetic examples.
Their meter serial is a placeholder, dates have been shifted, and energy
values have been replaced.

## Energy dashboard

The integration creates five latest-value sensors: daily imported/exported
registers, cumulative 15-minute imported/exported energy, and the yearly net
energy balance (`Szaldó`). It also imports long-term statistics for all five
series. Select the corresponding
`MVM Smart Meter Statistics Import` statistics in the Energy dashboard:

- Daily imported energy
- Daily exported energy
- 15-minute imported energy
- 15-minute exported energy
- Yearly net energy balance

The daily readings remain a separate series from the interval data; do not add
both readings for the same direction as separate energy sources in the same
dashboard configuration. The net balance is signed (negative means net export) and is
provided as a separate series; do not configure it as imported or exported
energy in the Energy dashboard. When an annual rollover is configured, its
statistics account for that yearly cycle. Otherwise, each statistic retains the
reported signed balance value.

## Overlapping reports

Reports are merged by meter, reading type, direction, and absolute instant.
When files overlap, the most recently modified CSV wins for a duplicate
timestamp. If a single report contains the repeated local times during the
autumn daylight-saving transition, both occurrences are retained. The complete
statistics are recalculated whenever the reconciled data changes, so corrections
in a later report replace the affected history rather than double-counting it.
