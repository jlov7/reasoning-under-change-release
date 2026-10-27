"""Input authoring preflight, separate from optimal selection."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reasoning_under_change import work
from reasoning_under_change.core import ScenarioError


def queue():
    return {'revision': 'preflight-1', 'capacity': 9, 'completed_ids': ['inventory'],
            'resource_limits': {'engineering': 5, 'review': 2}, 'tasks': [
                {'id': 'inventory', 'demand': 1, 'value': 0, 'resources': {'engineering': 1}},
                {'id': 'baseline', 'demand': 2, 'value': 0, 'requires': ['inventory'],
                 'resources': {'engineering': 1}},
                {'id': 'smoke', 'demand': 1, 'value': 4, 'requires': ['baseline'], 'mandatory': True,
                 'resources': {'engineering': 1, 'review': 1}},
                {'id': 'audit', 'demand': 2, 'value': 6, 'requires': ['baseline'], 'mandatory': True,
                 'resources': {'engineering': 2, 'review': 1}},
                {'id': 'optional', 'demand': 3, 'value': 20, 'resources': {'review': 3}}]}


class InputValidationTests(unittest.TestCase):
    def test_required_shared_closure_and_completed_resources_count_once_without_solver(self):
        document = queue()
        saved = copy.deepcopy(document)
        with patch.object(work, '_selection', side_effect=AssertionError('preflight must not optimize')):
            result = work.validate(document)
        self.assertEqual(result['schema'], 'ruc-work-validation-v1')
        self.assertEqual(result['status'], 'FEASIBLE')
        self.assertEqual(result['required_ids'], ['audit', 'baseline', 'inventory', 'smoke'])
        self.assertEqual(result['completed_ids'], ['inventory'])
        self.assertEqual(result['task_count'], 5)
        self.assertEqual(result['required_capacity_used'], 6)
        self.assertEqual(result['required_capacity_slack'], 3)
        self.assertEqual(result['required_resources_used'], {'engineering': 5, 'review': 2})
        self.assertEqual(result['required_resource_slack'], {'engineering': 0, 'review': 0})
        self.assertEqual(result['completed_capacity_used'], 1)
        self.assertEqual(result['completed_resources_used'], {'engineering': 1, 'review': 0})
        self.assertNotIn('selected_ids', result)
        self.assertEqual(document, saved)
        self.assertEqual(result['input_digest'], work.plan(document)['input_digest'])

    def test_required_deficits_are_explicit_but_completed_accounting_remains_invalid(self):
        document = queue(); document['capacity'] = 5; document['resource_limits']['review'] = 0
        result = work.validate(document)
        self.assertEqual(result['status'], 'INFEASIBLE')
        self.assertEqual(result['required_capacity_slack'], -1)
        self.assertEqual(result['required_resource_slack']['review'], -2)
        self.assertNotIn('selected_ids', result)
        document['resource_limits']['engineering'] = 0
        with self.assertRaisesRegex(ScenarioError, 'completed'):
            work.validate(document)
        document = queue(); document['completed_ids'] = ['smoke']
        with self.assertRaisesRegex(ScenarioError, 'prerequisite'):
            work.validate(document)

    def test_empty_legacy_and_named_zero_budget_inputs(self):
        document = {'revision': 'empty', 'capacity': 0, 'tasks': [], 'completed_ids': []}
        result = work.validate(document)
        self.assertEqual((result['status'], result['required_ids'], result['required_resources_used']),
                         ('FEASIBLE', [], {}))
        document['resource_limits'] = {'review': 0}
        self.assertEqual(work.validate(document)['required_resource_slack'], {'review': 0})
        document['tasks'] = [{'id': 'bad', 'demand': 1, 'value': 1, 'resources': {'typo': 0}}]
        with self.assertRaises(ScenarioError):
            work.validate(document)

    def test_cli_is_read_only_and_invalid_input_can_be_corrected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root/'queue.json'
            document = queue(); source.write_text(json.dumps(document)); before = source.read_bytes()
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(work.main(['validate', str(source)]), 0)
            self.assertEqual(json.loads(out.getvalue()), work.validate(document))
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(list(root.iterdir()), [source])
            source.write_text('{"revision":"duplicate","revision":"two"}')
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(work.main(['validate', str(source)]), 2)
            self.assertEqual(out.getvalue(), '')
            self.assertEqual(json.loads(err.getvalue())['error']['code'], 'INVALID_INPUT')
            source.write_bytes(before)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(work.main(['validate', str(source)]), 0)
