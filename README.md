# ha-mvm-meter-stat-import

Home Assistant custom integration to import MVM smart meter CSV reports into the
Energy dashboard.

## Installation

### Install with HACS

This integration can be installed from HACS as a custom repository. In HACS,
open the menu, select **Custom repositories**, enter
`Azakmi/ha-mvm-meter-stat-import`, and choose **Integration** as the category.
Add the repository, then install **MVM Smart Meter Statistics Import**. Restart
Home Assistant and add the integration from **Settings → Devices & services →
Add integration**.

### Install manually

From the repository root, run the install helper with the path to your existing
Home Assistant configuration directory:

```sh
./scripts/ha-integration install /path/to/home-assistant/config
```

The helper creates `custom_components` if needed and refuses to replace an
existing integration directory. To update an existing copy, run:

```sh
./scripts/ha-integration update /path/to/home-assistant/config
```

Updating replaces files with matching names in this integration's directory,
but does not delete extra files there or touch other integrations. Back up any
local edits to integration files before updating. Restart Home Assistant after
installing or updating. You can also install manually by copying
`custom_components/mvm_meter_stat_import` into the Home Assistant
configuration's `custom_components` directory.

### Local test instance with Docker Compose

Start Docker, then run `docker compose -f compose.test.yaml up -d` from the
repository root. The test instance is available only from this machine at
`http://localhost:8123`; its Home Assistant configuration is kept in a named
Docker volume, and the integration and example CSVs are mounted read-only.
Configure the integration with the report directory `/config/mvm_test`.
Stop it with `docker compose -f compose.test.yaml down` (the named configuration
volume is retained).

In **Settings → Devices & services → Add integration**, select **MVM Smart Meter
Statistics Import** and enter a folder path on the Home Assistant host. The
folder must already contain valid measured MVM report data. Reports for multiple
meter serial numbers may share a folder; the integration creates a separate
device and statistics series for each meter. Do not place unrelated CSV files
there. The integration uses Home Assistant's configured time zone and checks the
folder every 15 minutes.
If the net balance is billed on a yearly cycle, enter its rollover month and day
during setup. Leave both fields blank for users without an annual saldo cycle.

The reports use a semicolon delimiter and the MVM column names shown in
`example_data`. Daily rows (`YYYY. MM. DD.`) are treated as absolute register
readings. Rows with a time (`YYYY. MM. DD. HH:MM`) are treated as 15-minute
energy amounts. Measured interval `Szaldó` rows are ignored; daily `Szaldó`
readings are retained. Other imported rows must be measured and expressed in
kWh.

## Downloading reports from MVM

1. Sign in to [MVM Next online customer service](https://onlineugyintezes.mvmnext.hu/)
   and select the electricity supply point or meter whose data you need.
2. Open the portal's consumption or meter-data area, set the date range, and
   export the report as CSV. Portal labels and available options can change.
3. Choose the data type you need:
   - For interval data, choose **15-minute** resolution. The CSV should have
     timestamps with a time, such as `2026. 11. 01. 10:00`, and measured import
     and/or export amounts for each interval.
   - For daily absolute readings, choose daily or register readings rather than
     15-minute consumption. The CSV should have date-only rows, such as
     `2026. 11. 01.`, with the daily import and/or export register values.
4. Check that the downloaded CSV has the meter serial number and the MVM
   columns and semicolon delimiter used by the files in `example_data`.
   Measured interval import/export and daily absolute import/export readings
   are supported. Interval `Szaldó` rows are skipped; daily `Szaldó` is retained.
5. Copy the CSV into the report folder configured in Home Assistant. The
   integration refreshes reports from that folder every 15 minutes; you can
   also use that meter's **Refresh now** button. One folder can contain reports
   for multiple meter serial numbers, but should not contain unrelated CSVs.

## Example data

The `sample_*.csv` files in `example_data` are shortened, synthetic examples.
Their meter serial is a placeholder, dates have been shifted, and energy
values have been replaced.

## Energy dashboard

Each meter gets five latest-value sensors: daily imported/exported registers,
cumulative 15-minute imported/exported energy, and the yearly net energy balance
(`Szaldó`). It also gets an **Import status** sensor, a **Last successful
refresh** timestamp, and a **Refresh now** button. Import status remains
available when a refresh fails, reports the latest refresh outcome, and includes
reading counts and the latest imported timestamp.
The refresh button remains available to recover from a failed refresh.
Download diagnostics from the integration's config entry to see refresh health,
reading counts, and the newest imported timestamp. Diagnostics omit the meter
serial, configured folder, and report contents.

The integration imports long-term statistics for all five series. Select the
corresponding `MVM Smart Meter Statistics Import` statistics in the Energy
dashboard:

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

## Estimated yearly electricity bill

Configure the yearly bill rates from the integration's **Configure** options.
Enter the monthly fixed charge, export price per kWh, import price per kWh for
the first 2,523 kWh, import price per kWh above 2,523 kWh, and an ISO 4217
currency code. All four rates must be configured before the estimate is
available; leaving any rate or the currency unset safely keeps it unavailable.
The sensor is created separately for each meter.

The estimate uses the latest reading in that meter's yearly net energy balance
(`Szaldó`) series directly; it does not recalculate a balance from imported and
exported meter readings. A nonnegative balance is charged at the first import
rate for up to 2,523 kWh and at the second import rate for any excess. A
negative balance earns an export-price credit for its absolute kWh value. The
monthly fixed charge is multiplied by 12 and added in either case:

`yearly estimate = 12 × monthly fixed cost + import charges − export credit`

This is a full-year estimate, not a prorated bill: it assumes the configured
monthly charge and rates apply for all 12 months and excludes taxes, fees, and
other charges. The displayed value changes as new yearly-balance readings are
imported. Configure the annual rollover date during setup when the report's
yearly balance resets on a known date. The currency is explicit and is not
inferred from meter data or tariff values.

## Overlapping reports

Reports are merged by meter, reading type, direction, and absolute instant.
When files overlap, the most recently modified CSV wins for a duplicate
timestamp. If a single report contains the repeated local times during the
autumn daylight-saving transition, both occurrences are retained. The complete
statistics are recalculated whenever the reconciled data changes, so corrections
in a later report replace the affected history rather than double-counting it.
