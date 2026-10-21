"""Real-file reconciliation plus explicitly synthetic parsing fixtures."""
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts.analyze import FIELDS, load, metric

ROOT = Path(__file__).resolve().parents[1]


def synthetic_row(**changes):
    return {**dict(task_id='synthetic-test-only', execute_time='2026-09-01T08:20:00+08:00',
                   last_telemetry_sync='2026-09-01T00:00:00Z', workshop='一号制氢车间',
                   device_type='测试夹具', battery_level='20', ambient_temp='50', task_status='0'),
            **changes}


class IngestionTests(unittest.TestCase):
    def fixture(self, rows):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'synthetic.csv'
        with path.open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_identical_duplicates_count_once(self):
        row = synthetic_row()
        raw, unique, duplicates = load(self.fixture([row, row]))
        self.assertEqual((len(raw), len(unique), len(duplicates)), (2, 1, 1))

    def test_conflicting_task_id_fails_closed(self):
        with self.assertRaisesRegex(ValueError, '冲突'):
            load(self.fixture([synthetic_row(), synthetic_row(task_status='1')]))

    def test_missing_outcome_not_treated_as_success(self):
        _, rows, _ = load(self.fixture([synthetic_row(), synthetic_row(task_id='unknown-test', task_status='')]))
        result = metric(rows, lambda r: True)
        self.assertEqual((result['n'], result['tp'], result['fp']), (1, 1, 0))
        self.assertEqual((result['unknown_status'], result['unknown_alerts']), (1, 1))
        self.assertEqual(result['net_minutes_q50_unknown_all_success'], 25)


class SourceReconciliationTests(unittest.TestCase):
    def test_independent_source_replay_matches_artifact(self):
        # Deliberately independent of load(), metric(), and decide().
        with (ROOT / 'data/inspection_tasks.csv').open(encoding='utf-8-sig', newline='') as f:
            raw = list(csv.DictReader(f))
        unique = {tuple(row.items()): row for row in raw}
        rows = list(unique.values())
        local = timezone(timedelta(hours=8))
        replay = [r for r in rows if datetime.fromisoformat(r['execute_time']).astimezone(local).date().isoformat() >= '2026-09-05']
        alerts = []
        for row in replay:
            age = (datetime.fromisoformat(row['execute_time']) - datetime.fromisoformat(row['last_telemetry_sync'].replace('Z', '+00:00'))).total_seconds() / 60
            if row['workshop'] in ('一号制氢车间', '三号动力车间', '四号综合测试区') and age > 15:
                alerts.append(row)
        tp = sum(r['task_status'] == '0' for r in alerts)
        unknown_alerts = sum(r['task_status'] == '' for r in alerts)
        known = sum(r['task_status'] in ('0', '1') for r in replay)
        failures = sum(r['task_status'] == '0' for r in replay)
        self.assertEqual((len(raw), len(rows), len(replay), known), (1242, 1183, 516, 489))
        self.assertEqual((len(alerts), tp, unknown_alerts, failures), (88, 45, 7, 58))
        self.assertEqual(90 * tp * .5 - 10 * len(alerts), 1145)
        report = json.loads((ROOT / 'results/report.json').read_text(encoding='utf-8'))
        evaluation = report['evaluation']
        self.assertEqual(evaluation['all_alerts_including_unknown'], len(alerts))
        self.assertEqual(evaluation['tp'], tp)
        self.assertEqual(evaluation['unknown_alerts'], unknown_alerts)
        self.assertEqual(evaluation['net_minutes_q50_unknown_all_success'], 1145)


if __name__ == '__main__':
    unittest.main()
