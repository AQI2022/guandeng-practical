"""Synthetic boundary inputs test code only; they are not business evidence."""
import unittest
from src.policy import age_minutes, decide


def sample(**changes):
    row = dict(task_id='synthetic-test-only', execute_time='2026-09-01T08:20:00+08:00',
               last_telemetry_sync='2026-09-01T00:00:00Z', workshop='一号制氢车间',
               device_type='测试夹具', battery_level=20, ambient_temp=50)
    return {**row, **changes}


class PolicyTests(unittest.TestCase):
    def test_same_instant_with_different_timezones(self):
        row = sample(execute_time='2026-09-01T08:00:00+08:00')
        self.assertEqual(age_minutes(row), 0)
        self.assertEqual(decide(row)['decision'], 'continue')

    def test_exact_boundary_and_one_second_after(self):
        self.assertEqual(decide(sample(execute_time='2026-09-01T08:15:00+08:00'))['decision'], 'continue')
        self.assertEqual(decide(sample(execute_time='2026-09-01T08:15:01+08:00'))['decision'], 'review')

    def test_future_sync_rejected(self):
        self.assertEqual(decide(sample(last_telemetry_sync='2026-09-01T00:21:00Z'))['decision'], 'invalid')

    def test_time_requires_timezone_and_presence(self):
        for value in ('', None, 'not a date', '2026-09-01T00:00:00'):
            with self.subTest(value=value):
                self.assertEqual(decide(sample(last_telemetry_sync=value))['decision'], 'invalid')

    def test_outcome_cannot_change_decision(self):
        baseline = decide(sample())
        for status in ('0', '1', '', None):
            self.assertEqual(decide(sample(task_status=status)), baseline)

    def test_second_workshop_is_outside_pilot(self):
        decision = decide(sample(workshop='二号装配车间'))
        self.assertEqual(decision['decision'], 'continue')
        self.assertIn('暂不纳入提示试点', decision['reasons'][0])

    def test_unknown_workshop_requires_manual_check(self):
        self.assertEqual(decide(sample(workshop='未知车间'))['decision'], 'invalid')

    def test_missing_id_rejected(self):
        self.assertEqual(decide(sample(task_id=''))['decision'], 'invalid')
        for value in (None, False, [], {}, '  '):
            with self.subTest(value=value):
                self.assertEqual(decide(sample(task_id=value))['decision'], 'invalid')
        missing = sample()
        del missing['task_id']
        self.assertEqual(decide(missing)['decision'], 'invalid')

    def test_invalid_numeric_inputs(self):
        for changes in ({'battery_level': -1}, {'battery_level': 101},
                        {'battery_level': 'NaN'}, {'ambient_temp': 'inf'},
                        {'ambient_temp': ''}):
            with self.subTest(changes=changes):
                self.assertEqual(decide(sample(**changes))['decision'], 'invalid')

    def test_boolean_numbers_and_missing_device_type_are_rejected(self):
        for changes in ({'battery_level': True}, {'ambient_temp': False},
                        {'device_type': ''}, {'device_type': None}):
            with self.subTest(changes=changes):
                self.assertEqual(decide(sample(**changes))['decision'], 'invalid')


if __name__ == '__main__':
    unittest.main()
