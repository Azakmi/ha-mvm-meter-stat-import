"""Tests for overlapping MVM export report fixtures."""

import csv
from pathlib import Path
import unittest

from custom_components.mvm_meter_stat_import.importer import load_reports


class OverlappingReportFixtureTests(unittest.TestCase):
    """Verify matching fixture data is reconciled without losing unique readings."""

    def test_overlapping_reports_keep_matching_rows_once(self) -> None:
        fixture_dir = Path(__file__).parent / "fixtures" / "overlapping_reports"
        report_rows = []
        for path in sorted(fixture_dir.glob("*.csv")):
            with path.open(encoding="utf-8-sig", newline="") as report:
                report_rows.append(
                    {
                        (row["Időpont"], row["Adatpont típus"]): row
                        for row in csv.DictReader(report, delimiter=";")
                    }
                )

        shared_keys = report_rows[0].keys() & report_rows[1].keys()
        self.assertEqual(len(shared_keys), 2)
        self.assertEqual(
            {key: report_rows[0][key] for key in shared_keys},
            {key: report_rows[1][key] for key in shared_keys},
        )

        data = load_reports(fixture_dir, "Europe/Budapest")
        expected_dates = [
            "2026-01-01",
            "2026-01-02",
            "2026-01-03",
            "2026-01-04",
            "2026-01-05",
        ]
        for series_key, expected_values in (
            ("daily_import", ["100.0", "101.0", "102.5", "104.0", "105.5"]),
            ("daily_export", ["10.0", "10.5", "11.0", "11.5", "12.0"]),
        ):
            readings = data.meters["0000000000000000"].series[series_key]
            self.assertEqual(len(readings), 5)
            self.assertEqual(
                [reading.timestamp.date().isoformat() for reading in readings],
                expected_dates,
            )
            self.assertEqual(
                [str(reading.value) for reading in readings], expected_values
            )
