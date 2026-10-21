"""Real-file reconciliation plus explicitly synthetic parsing fixtures."""
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts.analyze import FIELDS, load, metric, run

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
        self.assertEqual(result['net_minutes_q50_conservative'], 25)


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
        self.assertEqual(evaluation['net_minutes_q50_conservative'], 1145)

    def test_joint_selection_and_workshop_sensitivity(self):
        report = json.loads((ROOT / 'results/report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['joint_policy_selection']['threshold'], 15)
        self.assertEqual(report['joint_policy_selection']['workshops'],
                         ['一号制氢车间', '三号动力车间', '四号综合测试区'])
        self.assertEqual(sum(b['n'] for b in report['age_bins']), report['summary']['labeled_tasks'])
        three = next(w for w in report['workshop_selection'] if w['workshop'] == '三号动力车间')
        self.assertAlmostEqual(three['q50_review_cost_ceiling_minutes'], 11.9117647, places=5)
        scenarios = {(s['q'], s['review_cost_minutes']): s for s in report['policy_selection_sensitivity']}
        self.assertEqual(scenarios[(.4, 10)]['workshops'], ['一号制氢车间', '四号综合测试区'])
        self.assertEqual(scenarios[(.3, 10)]['workshops'], ['一号制氢车间'])
        self.assertEqual(scenarios[(.5, 15)]['workshops'], ['一号制氢车间', '四号综合测试区'])

    def test_run_rejects_dates_outside_fixed_study_window(self):
        with (ROOT / 'data/inspection_tasks.csv').open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        extra = dict(rows[0], task_id='synthetic-outside-study-window',
                     execute_time='2026-09-08T08:00:00+08:00',
                     last_telemetry_sync='2026-09-07T22:30:00Z', task_status='1')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.csv'
            with path.open('w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(rows + [extra])
            output = Path(directory) / 'report'
            with self.assertRaisesRegex(ValueError, '超出本题研究窗口'):
                run(path, output)
            self.assertFalse(output.exists())

    def test_run_rejects_missing_calendar_date(self):
        with (ROOT / 'data/inspection_tasks.csv').open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        rows = [row for row in rows if row['execute_time'][:10] != '2026-09-03']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.csv'
            with path.open('w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            output = Path(directory) / 'report'
            with self.assertRaisesRegex(ValueError, '输入缺少本题研究日期'):
                run(path, output)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
