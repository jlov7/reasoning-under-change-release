"""User work plans, checked independently of the benchmark evaluator."""
import copy
import itertools
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reasoning_under_change.core import ScenarioError
from reasoning_under_change.work import check, plan, replan


def queue(revision='one', capacity=10, completed=None):
    return {'revision': revision, 'capacity': capacity, 'completed_ids': completed or [],
            'tasks': [{'id': 'a', 'demand': 6, 'value': 13},
                      {'id': 'b', 'demand': 5, 'value': 10},
                      {'id': 'c', 'demand': 5, 'value': 10}]}


class WorkTests(unittest.TestCase):
    def test_custom_optima_against_independent_enumeration(self):
        for capacity in range(16):
            document = queue(capacity=capacity)
            tasks = document['tasks']
            feasible = [subset for n in range(4)
                        for subset in itertools.combinations(tasks, n)
                        if sum(t['demand'] for t in subset) <= capacity]
            optimum = max(sum(t['value'] for t in subset) for subset in feasible)
            result = plan(document)
            self.assertEqual(result['total_value'], optimum)
            self.assertLessEqual(result['capacity_used'], capacity)
            self.assertEqual(check(document, result)['status'], 'VALID')
        result = plan(queue())
        self.assertEqual(result['selected_ids'], ['b', 'c'])
        self.assertEqual(result['total_value'], 20)  # FIFO and ratio greedy return 13.

    def test_replan_preserves_completed_and_explains_changes(self):
        first = plan(queue())
        updated = queue('two', 6, ['b'])
        result = replan(first, updated)
        self.assertEqual(result['selected_ids'], ['b'])
        self.assertEqual(result['completed_ids'], ['b'])
        self.assertEqual(result['changes'], {'retained_ids': ['b'], 'added_ids': [],
                                            'dropped_ids': ['c'],
                                            'previous_total_value': 20, 'value_delta': -10,
                                            'previous_capacity': 10, 'current_capacity': 6,
                                            'reason': 'Reoptimized pending work after input revision changed.'})
        self.assertEqual(result['previous_input_digest'], first['input_digest'])
        self.assertEqual(check(updated, result)['status'], 'VALID')
        with self.assertRaisesRegex(ScenarioError, 'stale'):
            check(updated, first)

    def test_added_removed_pending_tasks_and_capacity_boundary(self):
        first = plan(queue())
        updated = queue('two', 8, ['b'])
        updated['tasks'] = [updated['tasks'][1], {'id': 'new', 'demand': 3, 'value': 50}]
        result = replan(first, updated)
        self.assertEqual(result['selected_ids'], ['b', 'new'])
        self.assertEqual(result['changes']['dropped_ids'], ['c'])
        self.assertEqual(result['changes']['added_ids'], ['new'])
        largest = {'revision': 'max', 'capacity': 128, 'completed_ids': [],
                   'tasks': [{'id': f'job{i:02}', 'demand': 1, 'value': 10000}
                             for i in range(20)]}
        result = plan(largest)
        self.assertEqual(result['total_value'], 200000)
        self.assertEqual(len(result['selected_ids']), 20)

    def test_completed_work_cannot_be_invented_or_lost_or_mutated(self):
        first = plan(queue())
        for updated in (queue('two', 10, ['a']), queue('two', 4, ['b'])):
            with self.assertRaises(ScenarioError):
                replan(first, updated)
        second = replan(first, queue('two', 6, ['b']))
        with self.assertRaises(ScenarioError):
            replan(second, queue('three', 10))
        updated = queue('three', 10, ['b'])
        updated['tasks'][1]['value'] = 11
        with self.assertRaises(ScenarioError):
            replan(second, updated)
        with self.assertRaises(ScenarioError):
            replan(first, queue())

    def test_strict_schema_and_limits(self):
        bad = []
        for key, value in [('capacity', True), ('capacity', 1.0), ('capacity', -1),
                           ('capacity', 129), ('capacity', float('inf')),
                           ('revision', ''), ('revision', '../private')]:
            document = queue(); document[key] = value; bad.append(document)
        for tasks in ([queue()['tasks'][0]] * 2, queue()['tasks'] * 7,
                      [{'id': 'x', 'demand': 1, 'value': False}],
                      [{'id': 'x', 'demand': 1, 'value': 1, 'requires': ['z']}],
                      [{'id': 'x', 'demand': 0, 'value': 1}]):
            document = queue(); document['tasks'] = tasks; bad.append(document)
        for completed in (['missing'], ['b', 'b'], True):
            document = queue(); document['completed_ids'] = completed; bad.append(document)
        document = queue(); document['extra'] = None; bad.append(document)
        for document in bad:
            with self.subTest(document=document), self.assertRaises(ScenarioError):
                plan(document)

    def test_empty_infeasible_ties_and_order_are_deterministic(self):
        self.assertEqual(plan({'revision': 'empty', 'capacity': 0, 'tasks': [],
                               'completed_ids': []})['selected_ids'], [])
        self.assertEqual(plan(queue(capacity=0))['selected_ids'], [])
        document = queue()
        reordered = copy.deepcopy(document); reordered['tasks'].reverse()
        self.assertEqual(plan(document), plan(reordered))
        document['tasks'] = [{'id': 'a', 'demand': 1, 'value': 1},
                             {'id': 'b', 'demand': 1, 'value': 1}]
        document['capacity'] = 1
        self.assertEqual(plan(document)['selected_ids'], ['a'])

    def test_tampered_plan_and_boolean_totals_rejected(self):
        first = plan(queue())
        for key, value in [('selected_ids', ['a']), ('total_value', 19),
                           ('capacity_used', True), ('input_digest', '0' * 64),
                           ('extra', 'not-allowed')]:
            bad = copy.deepcopy(first); bad[key] = value
            with self.assertRaises(ScenarioError):
                check(queue(), bad)
        revised = replan(first, queue('two', 6, ['b']))
        revised['changes']['retained_ids'] = ['a']
        with self.assertRaises(ScenarioError):
            check(queue('two', 6, ['b']), revised)

    def test_timeout_fails_without_claiming_exactness(self):
        with patch('reasoning_under_change.work.exact_dp') as solver:
            solver.return_value.stop_reason = 'TIMEOUT'
            with self.assertRaisesRegex(ScenarioError, 'TIMEOUT'):
                plan(queue())

    def test_cli_separate_invocations_and_overwrite_and_malformed_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before, after, first, second = [root / name for name in
                                           ['before.json', 'after.json', 'first.json', 'second.json']]
            before.write_text(json.dumps(queue()))
            after.write_text(json.dumps(queue('two', 6, ['b'])))
            def run(*args):
                return subprocess.run([sys.executable, '-m', 'reasoning_under_change.work',
                                       *map(str, args)], capture_output=True, text=True, timeout=5, check=False)
            self.assertEqual(run('plan', before, '--out', first).returncode, 0)
            self.assertEqual(run('check', before, first).returncode, 0)
            saved = first.read_bytes()
            self.assertEqual(run('plan', before, '--out', first).returncode, 2)
            self.assertEqual(first.read_bytes(), saved)
            self.assertEqual(run('check', after, first).returncode, 2)
            self.assertEqual(run('replan', after, first, '--out', second).returncode, 0)
            self.assertEqual(json.loads(second.read_text())['selected_ids'], ['b'])
            for malformed in ('{"capacity":1,"capacity":2}', '{"capacity":NaN}', '[]'):
                before.write_text(malformed)
                response = run('plan', before, '--out', root / 'bad.json')
                self.assertEqual(response.returncode, 2)
                self.assertNotIn(directory, response.stderr)
                self.assertEqual(json.loads(response.stderr)['error']['code'], 'INVALID_INPUT')
                self.assertFalse((root / 'bad.json').exists())
            before.write_text(json.dumps(queue()))
            self.assertEqual(run('check', before, first).returncode, 0)


if __name__ == '__main__':
    unittest.main()
