import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reasoning_under_change import cli
from reasoning_under_change.core import Event, ScenarioError, World


class BoundaryTests(unittest.TestCase):
    def test_world_rejects_bool_float_and_excessive_counts(self):
        for deadline, capacity in [(True, 1), (1.5, 1), (5, False), (5, 1.0), (1000001, 1)]:
            with (
                self.subTest(deadline=deadline, capacity=capacity),
                self.assertRaises(ScenarioError),
            ):
                World('J', deadline, capacity, [])

    def test_future_events_are_validated_at_construction(self):
        for event in [Event(10, 'capacity', 'capacity', -1),
                      Event(10, 'unrelated', 'deadline', 3),
                      Event(1000001, 'unrelated', 'noise', None)]:
            with self.subTest(event=event), self.assertRaises(ScenarioError):
                World('J', 5, 1, [event])

    def invoke(self, path):
        out, err = io.StringIO(), io.StringIO()
        with patch('sys.argv', ['ruc-scenario', str(path)]), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main()
        return status, out.getvalue(), err.getvalue()

    def test_cli_invalid_input_is_structured_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scenario.json'
            for text in ['{', '{}', '[]', '{"job_id":"J","job_id":"K"}',
                         '{"private":"do-not-echo"}', ' ' * 1048577]:
                path.write_text(text)
                status, out, err = self.invoke(path)
                self.assertEqual(status, 2)
                self.assertEqual(out, '')
                report = json.loads(err)
                self.assertEqual(report['error']['code'], 'INVALID_INPUT')
                self.assertRegex(report['request_id'], r'^[0-9a-f]{32}$')
                self.assertNotIn('do-not-echo', err)
            status, out, err = self.invoke(Path(directory) / 'absent')
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(err)['error']['code'], 'INVALID_INPUT')

    def test_cli_rejects_schema_delay_events_and_ttl(self):
        valid = {'job_id': 'J', 'deadline': 6, 'capacity': 1, 'delay': 2, 'events': []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'
            for update in [{'delay': True}, {'ttl': 1.0}, {'events': [{}]},
                           {'events': [{'at': 2, 'entity': 'capacity', 'kind': 'capacity', 'value': False}]},
                           {'events': [{'at': 2, 'entity': 'unrelated', 'kind': 'noise', 'value': None},
                                        {'at': 1, 'entity': 'unrelated', 'kind': 'noise', 'value': None}]},
                           {'extra': 1}]:
                path.write_text(json.dumps(valid | update))
                self.assertEqual(self.invoke(path)[0], 2)
            path.write_text(json.dumps(valid))
            status, out, err = self.invoke(path)
            self.assertEqual((status, err), (0, ''))
            self.assertEqual(json.loads(out)['results']['never']['outcome'], 'ACCEPTED')

    def test_cli_distinguishes_supplied_scenario_from_packaged_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'
            path.write_text(json.dumps({'job_id': 'J', 'deadline': 6, 'capacity': 1,
                                            'delay': 2, 'events': []}))
            status, out, err = self.invoke(path)
            self.assertEqual((status, err), (0, ''))
            self.assertEqual(json.loads(out)['source'], 'user-supplied scenario')
        out = io.StringIO()
        with patch('sys.argv', ['ruc-scenario', '--example']), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(json.loads(out.getvalue())['source'], 'synthetic authored fixture')

    def test_utf16_nonfinite_and_excessive_events_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'
            path.write_bytes('{}'.encode('utf-16'))
            self.assertEqual(self.invoke(path)[0], 2)
            path.write_text('{"job_id":"J","deadline":NaN,"capacity":1,"events":[],"delay":0}')
            self.assertEqual(self.invoke(path)[0], 2)
            with self.assertRaises(ScenarioError):
                World('J', 5, 1, [Event(1, 'unrelated', 'noise', None)] * 10001)
