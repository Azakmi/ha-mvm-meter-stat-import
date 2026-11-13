"""Tests for parsing and reconciling MVM meter reports."""

import csv
from datetime import UTC
from decimal import Decimal
import os
from pathlib import Path
import tempfile
import unittest

from custom_components.mvm_meter_stat_import.importer import (
    ReportError,
    build_statistics,
    load_reports,
)

_HEADER = (
    "Gyári szám",
    "Időpont",
    "Adatpont típus",
    "Státusz",
    "Érték",
    "Mértékegység",
)


def _write_report(
    directory: Path,
    name: str,
    rows: list[tuple[str, str, str]],
    modified: int,
    meter_id: str = "0000000000000000",
) -> None:
    """Write a small MVM-format report with a chosen file modification time."""
    path = directory / name
    with path.open("w", encoding="utf-8-sig", newline="") as report:
        writer = csv.writer(report, delimiter=";")
        writer.writerow(_HEADER)
        writer.writerows(
            (meter_id, timestamp, direction, "Mért", value, "kWh")
            for timestamp, direction, value in rows
        )
    path.touch()
    os.utime(path, ns=(modified, modified))


class ReportImporterTests(unittest.TestCase):
    """Verify overlap handling and Home Assistant statistics preparation."""

    def test_overlap_precedence_and_dst_repeated_hour(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            _write_report(
                directory,
                "older.csv",
                [
                    ("2026. 10. 01.", "Vételezett", "100"),
                    ("2026. 10. 02.", "Vételezett", "110"),
                    ("2026. 10. 25. 02:00", "Vételezett", "0.1"),
                    ("2026. 10. 25. 02:00", "Vételezett", "0.2"),
                ],
                1_000_000_000,
            )
            _write_report(
                directory,
                "newer.csv",
                [
                    ("2026. 10. 02.", "Vételezett", "112"),
                    ("2026. 10. 25. 02:00", "Vételezett", "0.3"),
                    ("2026. 10. 25. 02:00", "Vételezett", "0.4"),
                ],
                2_000_000_000,
            )

            data = load_reports(directory, "Europe/Budapest")
            series = data.meters["0000000000000000"].series
            daily = series["daily_import"]
            intervals = series["interval_import"]

            self.assertEqual(daily[-1].value, Decimal("112"))
            self.assertEqual(len(intervals), 2)
            self.assertEqual(
                [reading.timestamp.astimezone(UTC).hour for reading in intervals],
                [0, 1],
            )
            self.assertEqual(
                [reading.value for reading in intervals],
                [Decimal("0.3"), Decimal("0.4")],
            )
            daily_stats = build_statistics(daily, cumulative_register=True)
            self.assertEqual(daily_stats[-1]["state"], 112)
            self.assertEqual(daily_stats[-1]["sum"], 12)
            self.assertEqual(
                len(build_statistics(intervals, cumulative_register=False)), 2
            )

    def test_interval_values_are_aggregated_hourly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            _write_report(
                directory,
                "intervals.csv",
                [
                    ("2026. 11. 01. 10:00", "Visszatáplált", "0.1"),
                    ("2026. 11. 01. 10:15", "Visszatáplált", "0.2"),
                    ("2026. 11. 01. 11:00", "Visszatáplált", "0.3"),
                ],
                1_000_000_000,
            )

            readings = load_reports(directory, "Europe/Budapest").meters[
                "0000000000000000"
            ].series["interval_export"]
            stats = build_statistics(readings, cumulative_register=False)

            self.assertEqual(len(stats), 2)
            self.assertEqual(stats[0]["sum"], 0.3)
            self.assertEqual(stats[1]["sum"], 0.6)

    def test_interval_net_rows_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            _write_report(
                directory,
                "intervals.csv",
                [
                    ("2026. 11. 01. 10:00", "Vételezett", "0.1"),
                    ("2026. 11. 01. 10:00", "Visszatáplált", "0.2"),
                    ("2026. 11. 01. 10:00", "Szaldó", "-0.1"),
                    ("2026. 11. 01.", "Szaldó", "-10"),
                ],
                1_000_000_000,
            )

            data = load_reports(directory, "Europe/Budapest")

            series = data.meters["0000000000000000"].series
            self.assertEqual(
                [reading.value for reading in series["interval_import"]],
                [Decimal("0.1")],
            )
            self.assertEqual(
                [reading.value for reading in series["interval_export"]],
                [Decimal("0.2")],
            )
            self.assertNotIn("interval_net", series)
            self.assertEqual(
                [reading.value for reading in series["daily_net"]],
                [Decimal("-10")],
            )

    def test_signed_daily_net_readings_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            _write_report(
                directory,
                "net.csv",
                [
                    ("2026. 01. 01.", "Szaldó", "-2277.0"),
                    ("2026. 01. 02.", "Szaldó", "-2288.0"),
                    ("2026. 01. 03.", "Szaldó", "12.0"),
                ],
                1_000_000_000,
            )

            readings = load_reports(directory, "Europe/Budapest").meters[
                "0000000000000000"
            ].series["daily_net"]
            stats = build_statistics(
                readings, cumulative_register=True, signed_register=True
            )

            self.assertEqual(
                [reading.value for reading in readings],
                [Decimal("-2277.0"), Decimal("-2288.0"), Decimal("12.0")],
            )
            self.assertEqual(
                [point["state"] for point in stats],
                [-2277.0, -2288.0, 12.0],
            )
            self.assertEqual(
                [point["sum"] for point in stats],
                [-2277.0, -2288.0, 12.0],
            )

    def test_signed_daily_net_sum_restarts_at_configured_rollover(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            _write_report(
                directory,
                "net.csv",
                [
                    ("2025. 05. 14.", "Szaldó", "-10"),
                    ("2025. 05. 15.", "Szaldó", "3"),
                    ("2025. 05. 16.", "Szaldó", "5"),
                    ("2026. 05. 14.", "Szaldó", "-2"),
                    ("2026. 05. 15.", "Szaldó", "4"),
                ],
                1_000_000_000,
            )

            readings = load_reports(directory, "Europe/Budapest").meters[
                "0000000000000000"
            ].series["daily_net"]
            stats = build_statistics(
                readings,
                cumulative_register=True,
                signed_register=True,
                rollover_month=5,
                rollover_day=15,
            )

            self.assertEqual(
                [point["state"] for point in stats],
                [-10, 3, 5, -2, 4],
            )
            self.assertEqual(
                [point["sum"] for point in stats],
                [-10, -7, -5, -12, -8],
            )

    def test_signed_net_without_rollover_keeps_reported_balance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            _write_report(
                directory,
                "net.csv",
                [
                    ("2025. 12. 31.", "Szaldó", "-10"),
                    ("2026. 01. 01.", "Szaldó", "-12"),
                    ("2026. 01. 02.", "Szaldó", "-8"),
                ],
                1_000_000_000,
            )

            readings = load_reports(directory, "Europe/Budapest").meters[
                "0000000000000000"
            ].series["daily_net"]
            stats = build_statistics(
                readings,
                cumulative_register=True,
                signed_register=True,
            )

            self.assertEqual(
                [point["state"] for point in stats],
                [-10, -12, -8],
            )
            self.assertEqual(
                [point["sum"] for point in stats],
                [-10, -12, -8],
            )

    def test_negative_imported_energy_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            _write_report(
                directory,
                "invalid.csv",
                [("2026. 01. 01.", "Vételezett", "-1.0")],
                1_000_000_000,
            )

            with self.assertRaises(ReportError):
                load_reports(directory, "Europe/Budapest")

    def test_missing_report_folder_data_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ReportError):
                load_reports(Path(temp_dir), "Europe/Budapest")

    def test_multiple_meters_keep_readings_and_statistics_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            path = directory / "shared.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as report:
                writer = csv.writer(report, delimiter=";")
                writer.writerow(_HEADER)
                writer.writerows(
                    (
                        meter_id,
                        "2026. 11. 01.",
                        "Vételezett",
                        "Mért",
                        value,
                        "kWh",
                    )
                    for meter_id, value in (
                        ("meter-1", "100"),
                        ("meter-2", "900"),
                    )
                )
                writer.writerows(
                    (
                        meter_id,
                        "2026. 11. 01. 10:00",
                        "Visszatáplált",
                        "Mért",
                        value,
                        "kWh",
                    )
                    for meter_id, value in (
                        ("meter-1", "0.1"),
                        ("meter-2", "0.9"),
                    )
                )
                writer.writerows(
                    (
                        meter_id,
                        "2026. 10. 25. 02:00",
                        "Vételezett",
                        "Mért",
                        value,
                        "kWh",
                    )
                    for meter_id, value in (
                        ("meter-1", "1.1"),
                        ("meter-1", "1.2"),
                        ("meter-2", "2.1"),
                        ("meter-2", "2.2"),
                    )
                )

            data = load_reports(directory, "Europe/Budapest")

            self.assertEqual(set(data.meters), {"meter-1", "meter-2"})
            meter_1 = data.meters["meter-1"]
            meter_2 = data.meters["meter-2"]
            self.assertEqual(meter_1.series["daily_import"][0].value, Decimal("100"))
            self.assertEqual(meter_2.series["daily_import"][0].value, Decimal("900"))
            self.assertEqual(
                meter_1.series["interval_export"][0].value, Decimal("0.1")
            )
            self.assertEqual(
                meter_2.series["interval_export"][0].value, Decimal("0.9")
            )
            self.assertNotEqual(meter_1.signature, meter_2.signature)
            for meter in (meter_1, meter_2):
                repeated_hour = meter.series["interval_import"]
                self.assertEqual(
                    [reading.timestamp.astimezone(UTC).hour for reading in repeated_hour],
                    [0, 1],
                )


if __name__ == "__main__":
    unittest.main()
