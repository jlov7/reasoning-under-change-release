"""Prerequisite selection checked by independent subset enumeration."""
import copy
import hashlib
import itertools
import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reasoning_under_change import work
from reasoning_under_change.core import ScenarioError


def queue():
    return {'revision': 'one', 'capacity': 9, 'completed_ids': [], 'tasks': [
        {'id': 'setup', 'demand': 2, 'value': 0, 'requires': []},
        {'id': 'checks', 'demand': 2, 'value': 0, 'requires': ['setup']},
        {'id': 'required', 'demand': 1, 'value': 1, 'requires': ['checks'], 'mandatory': True},
        {'id': 'a', 'demand': 2, 'value': 9, 'requires': ['checks']},
        {'id': 'b', 'demand': 2, 'value': 8, 'requires': ['setup']},
        {'id': 'c', 'demand': 5, 'value': 25}]}


def oracle(document, capacity=None, forced=()):
    tasks = document['tasks']
    fixed = set(document['completed_ids']) | set(forced)
    fixed |= {task['id'] for task in tasks if task.get('mandatory', False)}
    feasible = []
    for size in range(len(tasks) + 1):
        for subset in itertools.combinations(tasks, size):
            ids = {task['id'] for task in subset}
            if (fixed <= ids and all(set(task.get('requires', [])) <= ids for task in subset)
                    and sum(task['demand'] for task in subset) <= (
                        document['capacity'] if capacity is None else capacity)):
                feasible.append((sum(task['value'] for task in subset), ids))
    return feasible


class ConstraintTests(unittest.TestCase):
    def assert_optimal(self, document, result, capacity=None, forced=()):
        feasible = oracle(document, capacity, forced)
        self.assertIn((result['total_value'], set(result['selected_ids'])), feasible)
        self.assertEqual(result['total_value'], max(value for value, _ in feasible))

    def test_required_transitive_shared_prerequisites_and_pending_order(self):
        document = queue(); saved = copy.deepcopy(document)
        result = work.plan(document)
        self.assertEqual(result['schema'], 'ruc-work-plan-v2')
        self.assertEqual(result['selected_ids'], ['a', 'b', 'checks', 'required', 'setup'])
        self.assertEqual(result['capacity_used'], 9)
        self.assertEqual(result['pending_order'], ['setup', 'b', 'checks', 'a', 'required'])
        self.assert_optimal(document, result)
        self.assertEqual(work.check(document, result)['status'], 'VALID')
        reordered = copy.deepcopy(document); reordered['tasks'].reverse()
        self.assertEqual(work.plan(reordered), result)
        self.assertEqual(document, saved)

    def test_reports_and_seeded_dags_match_independent_oracle(self):
        rng = random.Random(80421)
        for sample in range(24):
            document = {'revision': f'dag-{sample}', 'capacity': 16, 'completed_ids': [],
                        'tasks': [{'id': f't{i}', 'demand': rng.randint(1, 6),
                                   'value': rng.randint(0, 25),
                                   'requires': [f't{j}' for j in range(i) if rng.random() < .22],
                                   'mandatory': i == 0 and sample % 3 == 0}
                                  for i in range(8)]}
            chosen = work.plan(document)
            self.assert_optimal(document, chosen)
            order = document['completed_ids'][:]
            by_id = {task['id']: task for task in document['tasks']}
            for task_id in chosen['pending_order']:
                self.assertTrue(set(by_id[task_id]['requires']) <= set(order))
                order.append(task_id)
            for point in work.sensitivity(document, [0, 4, 8, 16, 24])['points']:
                feasible = oracle(document, point['capacity'])
                if feasible:
                    self.assert_optimal(document, point, point['capacity'])
                else:
                    self.assertEqual(point['status'], 'INFEASIBLE')
                    self.assertIsNone(point['total_value'])
            for task in document['tasks']:
                if task['id'] in chosen['selected_ids']:
                    continue
                forced = work.counterfactual(document, task['id'])
                feasible = oracle(document, forced=[task['id']])
                self.assertEqual(forced['status'], 'FEASIBLE' if feasible else 'INFEASIBLE')
                if feasible:
                    self.assertIn((forced['forced_total_value'], set(forced['forced_ids'])), feasible)
                    self.assertEqual(forced['forced_total_value'], max(v for v, _ in feasible))

    def test_infeasible_mandatory_closure_and_counterfactual(self):
        document = queue(); document['capacity'] = 4
        with self.assertRaisesRegex(ScenarioError, 'mandatory'):
            work.plan(document)
        points = work.sensitivity(document, [4, 5])['points']
        self.assertEqual([p['status'] for p in points], ['INFEASIBLE', 'FEASIBLE'])
        forced = work.counterfactual(queue(), 'c')
        self.assertEqual(forced['status'], 'INFEASIBLE')
        self.assertEqual(forced['forced_ids'], [])
        self.assertIsNone(forced['forced_total_value'])

    def test_graph_types_bounds_and_closed_completion(self):
        for requires in (None, 'setup', ['missing'], ['a'], ['setup', 'setup'], [True],
                         ['setup'] * 20):
            document = queue(); document['tasks'][3]['requires'] = requires
            with self.subTest(requires=requires), self.assertRaises(ScenarioError):
                work.plan(document)
        for mandatory in (0, 1, None, 'true', []):
            document = queue(); document['tasks'][3]['mandatory'] = mandatory
            with self.subTest(mandatory=mandatory), self.assertRaises(ScenarioError):
                work.plan(document)
        document = queue(); document['tasks'][0]['requires'] = ['required']
        with self.assertRaisesRegex(ScenarioError, 'cycle'):
            work.plan(document)
        document = queue(); document['completed_ids'] = ['checks']
        with self.assertRaisesRegex(ScenarioError, 'prerequisite'):
            work.plan(document)
        document = queue(); document['tasks'][0]['value'] = -1
        with self.assertRaises(ScenarioError):
            work.plan(document)
        document = {'revision': 'old', 'capacity': 1, 'completed_ids': [],
                    'tasks': [{'id': 'zero', 'demand': 1, 'value': 0}]}
        with self.assertRaises(ScenarioError):
            work.plan(document)
        document['tasks'][0]['mandatory'] = False
        self.assertEqual(work.plan(document)['total_value'], 0)
        self.assertEqual(work.plan(dict(document, capacity=0))['selected_ids'], [])
        document['tasks'][0]['mandatory'] = True
        self.assertEqual(work.plan(document)['selected_ids'], ['zero'])

    def test_replan_completed_closure_tamper_and_mixed_history(self):
        legacy = {'revision': 'old', 'capacity': 9, 'completed_ids': [],
                  'tasks': [{'id': 'setup', 'demand': 2, 'value': 1}]}
        first = work.plan(legacy)
        document = queue(); document['tasks'][0]['value'] = 1
        del document['tasks'][0]['requires']
        document['completed_ids'] = ['setup']
        second = work.replan(first, document)
        self.assertNotIn('setup', second['pending_order'])
        document = dict(document, revision='two', completed_ids=['setup', 'checks'])
        third = work.replan(second, document)
        self.assert_optimal(document, third)
        self.assertEqual(work.verify_history([first, second, third])['status'], 'VALID')
        for field, value in [('pending_order', list(reversed(third['pending_order']))),
                             ('selected_ids', ['c']), ('solver', 'exact_dp')]:
            altered = copy.deepcopy(third); altered[field] = value
            with self.assertRaises(ScenarioError):
                work.check(document, altered)
        bad = copy.deepcopy(document); bad['revision'] = 'three'
        bad['tasks'][1]['requires'] = []
        with self.assertRaisesRegex(ScenarioError, 'definitions'):
            work.replan(third, bad)
        bad = copy.deepcopy(document); bad['revision'] = 'three'; bad['completed_ids'] += ['c']
        with self.assertRaises(ScenarioError):
            work.replan(third, bad)

    def test_legacy_plan_bytes_remain_identical(self):
        root = Path(__file__).resolve().parents[1]
        first = work.plan(json.loads((root / 'examples/work_queue.json').read_text()))
        second = work.replan(first, json.loads((root / 'examples/work_queue_changed.json').read_text()))
        actual = [hashlib.sha256((json.dumps(item, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
                  for item in (first, second)]
        self.assertEqual(actual, ['dd8a580e15c654dc20e012cafdd876ffb6041c911f39a1e92eed2f830bbc0dda',
                                  '1b7c4417741f3a6ea09e223355d7aa2dfb63601b5fc4f938cae77d247e6bfbc0'])

    def test_deadline_node_limit_and_maximum_input(self):
        with (
            patch('reasoning_under_change.work.monotonic_ns', side_effect=[0, 1000000001]),
            self.assertRaisesRegex(ScenarioError, 'TIMEOUT'),
        ):
            work.plan(queue())
        with (
            patch('reasoning_under_change.work._SEARCH_NODE_LIMIT', 1),
            self.assertRaisesRegex(ScenarioError, 'NODE_LIMIT'),
        ):
            work.plan(queue())
        document = {'revision': 'max', 'capacity': 128, 'completed_ids': [],
                    'tasks': [{'id': f't{i:02}', 'demand': 1, 'value': 10000,
                               'requires': [f't{j:02}' for j in range(i)]} for i in range(20)]}
        self.assertEqual(work.plan(document)['total_value'], 200000)

    def test_cli_infeasibility_has_no_output_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'; output = Path(directory) / 'plan.json'
            document = queue(); document['capacity'] = 4
            path.write_text(json.dumps(document))
            args = [sys.executable, '-m', 'reasoning_under_change.work', 'plan', str(path), '--out', str(output)]
            response = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
            self.assertEqual(response.returncode, 2)
            self.assertFalse(output.exists())
            self.assertNotIn(directory, response.stderr)
            self.assertEqual(json.loads(response.stderr)['error']['code'], 'INVALID_INPUT')
            path.write_text(json.dumps(queue()))
            response = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
            self.assertEqual(response.returncode, 0, response.stderr)
            self.assertEqual(json.loads(output.read_text())['schema'], 'ruc-work-plan-v2')
            saved = output.read_bytes()
            second = Path(directory) / 'revised.json'
            document = queue(); document.update(revision='two', completed_ids=['checks'])
            path.write_text(json.dumps(document))
            args = [sys.executable, '-m', 'reasoning_under_change.work', 'replan', str(path),
                    str(output), '--out', str(second)]
            response = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
            self.assertEqual(response.returncode, 2)
            self.assertFalse(second.exists())
            self.assertEqual(output.read_bytes(), saved)
            document['completed_ids'] = ['setup', 'checks']
            path.write_text(json.dumps(document))
            response = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
            self.assertEqual(response.returncode, 0, response.stderr)
