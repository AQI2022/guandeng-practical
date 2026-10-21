import http.client
import json
from http.server import ThreadingHTTPServer
from threading import Thread
import unittest

from app import Handler


class QuietHandler(Handler):
    def log_message(self, *_args):
        pass


class ApiBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def post(self, payload):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            connection.request('POST', '/api/check', payload,
                               {'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_boolean_sensor_values_return_manual_check(self):
        base = {'task_id': 'http-test-only', 'execute_time': '2026-09-01T08:00:00+08:00',
                'last_telemetry_sync': '2026-09-01T00:00:00Z', 'workshop': '一号制氢车间',
                'device_type': 'test fixture', 'battery_level': 20, 'ambient_temp': 50}
        for key, value in (('battery_level', True), ('ambient_temp', False)):
            with self.subTest(key=key):
                status, body = self.post(json.dumps({**base, key: value}))
                self.assertEqual(status, 200)
                self.assertEqual(body['decision'], 'invalid')

    def test_deep_json_gets_bounded_error_and_server_keeps_working(self):
        nested = b'{"x":' + b'[' * 7000 + b'0' + b']' * 7000 + b'}'
        status, body = self.post(nested)
        self.assertEqual(status, 400)
        self.assertIn('error', body)

        valid = {'task_id': 'after-error', 'execute_time': '2026-09-01T08:00:00+08:00',
                 'last_telemetry_sync': '2026-09-01T00:00:00Z', 'workshop': '一号制氢车间',
                 'device_type': 'test fixture', 'battery_level': 20, 'ambient_temp': 50}
        status, body = self.post(json.dumps(valid))
        self.assertEqual((status, body['decision']), (200, 'continue'))
