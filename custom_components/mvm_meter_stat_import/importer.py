"""Read and reconcile MVM smart meter CSV reports."""

from __future__ import annotations

import csv
from csv import Error as CsvError
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .const import (
    DATASET_DAILY,
    DATASET_INTERVAL,
    DIRECTION_EXPORT,
    DIRECTION_IMPORT,
    DIRECTION_NET,
    SERIES_KEYS,
)

_REQUIRED_COLUMNS = {
    "Gyári szám",
    "Időpont",
    "Adatpont típus",
    "Státusz",
    "Érték",
    "Mértékegység",
}
_DIRECTIONS = {
    "Vételezett": DIRECTION_IMPORT,
    "Visszatáplált": DIRECTION_EXPORT,
    "Szaldó": DIRECTION_NET,
}


class ReportError(ValueError):
    """An MVM CSV report is invalid or cannot be reconciled."""


@dataclass(frozen=True)
class MeterReading:
    """A single reading with its local timestamp and report precedence."""

    timestamp: datetime
    value: Decimal
    priority: tuple[int, str, int]


@dataclass(frozen=True)
class MeterData:
    """Reconciled readings for one meter, split by report type and direction."""

    meter_id: str
    series: dict[str, tuple[MeterReading, ...]]

    @property
    def signature(self) -> tuple[tuple[str, tuple[tuple[float, Decimal], ...]], ...]:
        """Return a stable value signature to avoid reimporting unchanged reports."""
        return tuple(
            (
                key,
                tuple(
                    (reading.timestamp.timestamp(), reading.value)
                    for reading in self.series[key]
                ),
            )
            for key in SERIES_KEYS
        )


def _parse_timestamp(value: str) -> tuple[datetime, str]:
    """Parse a report timestamp and return its dataset type."""
    value = value.strip()
    for pattern, dataset in (
        ("%Y. %m. %d. %H:%M", DATASET_INTERVAL),
        ("%Y. %m. %d.", DATASET_DAILY),
    ):
        try:
            return datetime.strptime(value, pattern), dataset
        except ValueError:
            continue
    raise ReportError(f"Unsupported report timestamp: {value!r}")


def _localize(
    timestamp: datetime, timezone: ZoneInfo, occurrence: int
) -> datetime:
    """Keep both occurrences of an ambiguous fall-back time distinct."""
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = timestamp.replace(tzinfo=timezone, fold=fold)
        round_trip = candidate.astimezone(UTC).astimezone(timezone).replace(tzinfo=None)
        if round_trip == timestamp:
            if not candidates or candidate.utcoffset() != candidates[0].utcoffset():
                candidates.append(candidate)

    if not candidates:
        raise ReportError(f"Report timestamp does not exist in local time: {timestamp}")
    return candidates[min(occurrence, len(candidates) - 1)]


def load_reports(directory: Path, timezone_name: str) -> MeterData:
    """Load all CSV reports from a directory, with newer files winning overlaps."""
    try:
        timezone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as err:
        raise ReportError(f"Invalid Home Assistant time zone: {timezone_name}") from err

    try:
        paths = sorted(directory.glob("*.csv"))
    except OSError as err:
        raise ReportError(f"Unable to list report folder {directory}: {err}") from err
    if not paths:
        raise ReportError(f"No CSV reports found in {directory}")

    resolved: dict[
        tuple[str, datetime], tuple[tuple[int, str, int], MeterReading]
    ] = {}
    meter_ids: set[str] = set()

    for path in paths:
        try:
            modified = path.stat().st_mtime_ns
            with path.open(encoding="utf-8-sig", newline="") as report:
                reader = csv.DictReader(report, delimiter=";")
                if reader.fieldnames is None or not _REQUIRED_COLUMNS.issubset(
                    reader.fieldnames
                ):
                    raise ReportError(f"{path.name}: missing MVM report columns")

                ambiguous_occurrences: dict[tuple[str, datetime], int] = {}
                for row_number, row in enumerate(reader, start=2):
                    if not row.get("Időpont"):
                        raise ReportError(
                            f"{path.name}:{row_number}: missing timestamp"
                        )

                    status = (row.get("Státusz") or "").strip()
                    if status != "Mért":
                        continue

                    direction_name = (row.get("Adatpont típus") or "").strip()
                    if direction_name not in _DIRECTIONS:
                        raise ReportError(
                            f"{path.name}:{row_number}: unsupported data point "
                            f"type {direction_name!r}"
                        )
                    direction = _DIRECTIONS[direction_name]
                    timestamp, dataset = _parse_timestamp(row["Időpont"])
                    if direction == DIRECTION_NET and dataset != DATASET_DAILY:
                        raise ReportError(
                            f"{path.name}:{row_number}: net balance readings must "
                            "be daily"
                        )

                    unit = (row.get("Mértékegység") or "").strip()
                    if unit != "kWh":
                        raise ReportError(
                            f"{path.name}:{row_number}: unsupported energy unit "
                            f"{unit!r}"
                        )
                    try:
                        value = Decimal((row.get("Érték") or "").strip())
                    except InvalidOperation as err:
                        raise ReportError(
                            f"{path.name}:{row_number}: invalid energy value"
                        ) from err
                    if not value.is_finite() or (
                        value < 0 and direction != DIRECTION_NET
                    ):
                        raise ReportError(
                            f"{path.name}:{row_number}: energy must be finite; "
                            "only net balance readings may be negative"
                        )

                    occurrence_key = (direction, timestamp)
                    occurrence = ambiguous_occurrences.get(occurrence_key, 0)
                    localized = _localize(timestamp, timezone, occurrence)
                    ambiguous_occurrences[occurrence_key] = occurrence + 1

                    meter_id = (row.get("Gyári szám") or "").strip()
                    if not meter_id:
                        raise ReportError(
                            f"{path.name}:{row_number}: missing meter serial"
                        )
                    meter_ids.add(meter_id)
                    if len(meter_ids) > 1:
                        raise ReportError(
                            "CSV reports from multiple meters cannot share one "
                            "directory"
                        )

                    key = f"{dataset}_{direction}"
                    instant = localized.astimezone(UTC)
                    priority = (modified, path.name, row_number)
                    reading = MeterReading(localized, value, priority)
                    map_key = (key, instant)
                    previous = resolved.get(map_key)
                    if previous is None or priority > previous[0]:
                        resolved[map_key] = (priority, reading)
        except (OSError, UnicodeError, CsvError) as err:
            raise ReportError(f"Unable to read report {path}: {err}") from err

    if not resolved:
        raise ReportError(f"No measured energy readings found in {directory}")

    series: dict[str, list[MeterReading]] = {key: [] for key in SERIES_KEYS}
    for (series_key, _), (_, reading) in resolved.items():
        series[series_key].append(reading)

    return MeterData(
        meter_id=next(iter(meter_ids)),
        series={
            key: tuple(
                sorted(
                    readings,
                    key=lambda reading: reading.timestamp.astimezone(UTC),
                )
            )
            for key, readings in series.items()
        },
    )


def build_statistics(
    readings: tuple[MeterReading, ...],
    *,
    cumulative_register: bool,
    signed_register: bool = False,
    rollover_month: int | None = None,
    rollover_day: int | None = None,
) -> list[dict[str, datetime | float]]:
    """Convert meter readings to hourly Energy dashboard statistics."""
    if not readings:
        return []

    if signed_register:
        points: list[dict[str, datetime | float]] = []
        total = Decimal(0)
        previous: MeterReading | None = None
        previous_cycle: int | None = None
        for reading in readings:
            if rollover_month is None or rollover_day is None:
                total = reading.value
            else:
                date = reading.timestamp.date()
                cycle = (
                    date.year
                    if (date.month, date.day) >= (rollover_month, rollover_day)
                    else date.year - 1
                )
                if previous is None:
                    total = reading.value
                elif cycle != previous_cycle:
                    total += reading.value
                else:
                    total += reading.value - previous.value
            points.append(
                {
                    "start": reading.timestamp,
                    "state": float(reading.value),
                    "sum": float(total),
                }
            )
            previous = reading
            previous_cycle = (
                cycle
                if rollover_month is not None and rollover_day is not None
                else None
            )
        return points

    if cumulative_register:
        points: list[tuple[datetime, Decimal, Decimal]] = []
        total = Decimal(0)
        previous: Decimal | None = None
        for reading in readings:
            if previous is not None:
                delta = reading.value - previous
                total += delta if delta >= 0 else reading.value
            points.append((reading.timestamp, reading.value, total))
            previous = reading.value
        return [
            {"start": start, "state": float(state), "sum": float(total_value)}
            for start, state, total_value in points
        ]

    hourly: dict[datetime, tuple[datetime, Decimal]] = {}
    for reading in readings:
        start = reading.timestamp.replace(minute=0, second=0, microsecond=0)
        key = start.astimezone(UTC)
        previous = hourly.get(key)
        hourly[key] = (
            start,
            (previous[1] if previous else Decimal(0)) + reading.value,
        )

    points = []
    total = Decimal(0)
    for start, energy in sorted(
        hourly.values(), key=lambda item: item[0].astimezone(UTC)
    ):
        total += energy
        points.append({"start": start, "state": float(total), "sum": float(total)})
    return points


def interval_cumulative(readings: tuple[MeterReading, ...]) -> Decimal | None:
    """Return the cumulative energy represented by interval readings."""
    if not readings:
        return None
    return sum((reading.value for reading in readings), start=Decimal(0))


def latest_value(readings: tuple[MeterReading, ...]) -> Decimal | None:
    """Return the latest reading value."""
    return readings[-1].value if readings else None
