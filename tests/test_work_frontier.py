"""Complete budget tradeoffs checked against independent subset enumeration."""
import copy
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
    return {'revision': 'frontier-1', 'capacity': 12, 'completed_ids': ['base'], 'tasks': [
        {'id': 'base', 'demand': 2, 'value': 0, 'requires': []},
        {'id': 'enable', 'demand': 2, 'value': 0, 'requires': ['base']},
        {'id': 'required', 'demand': 1, 'value': 2, 'requires': ['enable'], 'mandatory': True},
        {'id': 'a', 'demand': 3, 'value': 7, 'requires': ['enable']},
        {'id': 'b', 'demand': 3, 'value': 7, 'requires': ['enable']},
        {'id': 'c', 'demand': 6, 'value': 15, 'requires': ['base']}]}


def oracle(document):
    """Enumerate subsets independently, including the old include-first tie rule."""
    tasks = document['tasks']
    remaining = {task['id']: set(task.get('requires', [])) for task in tasks}
    order = []
    while remaining:
        item = min(item for item, needs in remaining.items() if not needs)
        order.append(item)
        del remaining[item]
        for needs in remaining.values():
            needs.discard(item)
    fixed = set(document['completed_ids']) | {t['id'] for t in tasks if t.get('mandatory')}
    feasible = []
    for size in range(len(tasks) + 1):
        for subset in itertools.combinations(tasks, size):
            ids = {task['id'] for task in subset}
            if fixed <= ids and all(set(t.get('requires', [])) <= ids for t in subset):
                feasible.append((sum(t['demand'] for t in subset),
                                 sum(t['value'] for t in subset),
                                 tuple(item in ids for item in order), sorted(ids)))
    answers = []
    for capacity in range(129):
        options = [item for item in feasible if item[0] <= capacity]
        answers.append(max(options, key=lambda item: (item[1], item[2])) if options else None)
    return answers


class FrontierTests(unittest.TestCase):
    def test_sensitivity_reuses_one_search_for_many_capacities(self):
        document = queue()
        with patch.object(work, '_selection', wraps=work._selection) as solve:
            report = work.sensitivity(document, list(range(32)))
        self.assertEqual(solve.call_count, 0, 'a capacity list must not repeat single-capacity solves')
        answers = oracle(document)
        for point in report['points']:
            expected = answers[point['capacity']]
            self.assertEqual(point['total_value'], expected[1] if expected else None)
            self.assertEqual(point['selected_ids'], expected[3] if expected else [])

    def test_frontier_all_tradeoffs_required_completed_shared_and_ties(self):
        document = queue(); saved = copy.deepcopy(document)
        result = work.frontier(document)
        self.assertEqual(result['schema'], 'ruc-work-frontier-v1')
        self.assertEqual(result['required_ids'], ['base', 'enable', 'required'])
        self.assertEqual(result['completed_ids'], ['base'])
        self.assertEqual(result['maximum_capacity'], 128)
        self.assertEqual(result['status'], 'FEASIBLE')
        self.assertEqual(document, saved)
        answers = oracle(document)
        thresholds = [c for c, answer in enumerate(answers)
                      if answer and (c == 0 or answers[c - 1] is None or answer[1] > answers[c - 1][1])]
        self.assertEqual([p['minimum_capacity'] for p in result['points']], thresholds)
        for point in result['points']:
            expected = answers[point['minimum_capacity']]
            self.assertEqual((point['capacity_used'], point['total_value'], point['selected_ids']),
                             (expected[0], expected[1], expected[3]))
            seen = set(document['completed_ids'])
            by_id = {task['id']: task for task in document['tasks']}
            for item in point['pending_order']:
                self.assertLessEqual(set(by_id[item].get('requires', [])), seen)
                seen.add(item)
            self.assertEqual(sorted(seen), point['selected_ids'])
            self.assertEqual(point['pending_ids'], sorted(seen - set(document['completed_ids'])))
        reversed_input = copy.deepcopy(document); reversed_input['tasks'].reverse()
        self.assertEqual(work.frontier(reversed_input), result)

    def test_varied_frontiers_and_capacity_order_match_exhaustive_oracle(self):
        rng = random.Random(93724)
        for sample in range(25):
            document = {'revision': f'r{sample}', 'capacity': 128,
                        'completed_ids': ['t0'] if sample % 2 else [],
                        'tasks': [{'id': f't{i}', 'demand': rng.randint(1, 7),
                                   'value': rng.randint(0, 10),
                                   'requires': [f't{j}' for j in range(i) if rng.random() < .2],
                                   'mandatory': i == 3 and sample % 3 == 0} for i in range(8)]}
            answers = oracle(document)
            capacities = [31, 0, 16, 2, 128, 7]
            points = work.sensitivity(document, capacities)['points']
            for point, capacity in zip(points, capacities):
                expected = answers[capacity]
                self.assertEqual(point['selected_ids'], expected[3] if expected else [])
                self.assertEqual(point['total_value'], expected[1] if expected else None)
            thresholds = [c for c, answer in enumerate(answers) if answer and
                          (not c or not answers[c - 1] or answer[1] > answers[c - 1][1])]
            self.assertEqual([p['minimum_capacity'] for p in work.frontier(document)['points']], thresholds)

    def test_legacy_shared_table_retains_single_plan_incumbents(self):
        rng = random.Random(430)
        for sample in range(10):
            document = {'revision': f'legacy{sample}', 'capacity': 128,
                        'completed_ids': ['a'] if sample % 2 else [],
                        'tasks': [{'id': chr(97 + i), 'demand': rng.randint(1, 7),
                                   'value': rng.randint(1, 8)} for i in range(7)]}
            points = work.sensitivity(document, list(range(32)))['points']
            for point in points:
                capacity = point['capacity']
                if capacity < sum(t['demand'] for t in document['tasks'] if t['id'] in document['completed_ids']):
                    self.assertEqual(point['status'], 'INFEASIBLE')
                else:
                    selected = work.plan(dict(document, capacity=capacity))
                    for key in ('selected_ids', 'total_value', 'capacity_used'):
                        self.assertEqual(point[key], selected[key])
            for point in work.frontier(document)['points']:
                selected = work.plan(dict(document, capacity=point['minimum_capacity']))
                self.assertEqual(point['selected_ids'], selected['selected_ids'])

    def test_limits_empty_zero_value_infeasible_and_fail_closed(self):
        empty = {'revision': 'empty', 'capacity': 0, 'completed_ids': [], 'tasks': []}
        self.assertEqual(work.frontier(empty)['points'][0]['minimum_capacity'], 0)
        document = queue()
        document['tasks'][2]['demand'] = 128
        result = work.frontier(document)
        self.assertEqual((result['status'], result['points']), ('INFEASIBLE', []))
        maximum = {'revision': 'max', 'capacity': 128, 'completed_ids': [], 'tasks': [
            {'id': f't{i:02}', 'demand': 1, 'value': 1, 'requires': []} for i in range(20)]}
        self.assertEqual(len(work.frontier(maximum)['points']), 21)
        self.assertEqual(work.sensitivity(maximum, list(range(32)))['points'][-1]['total_value'], 20)
        zero = dict(empty, tasks=[{'id': 'zero', 'demand': 1, 'value': 0, 'requires': []}])
        self.assertEqual(len(work.frontier(zero)['points']), 1)
        for function, args in [(work.frontier, (queue(),)), (work.sensitivity, (queue(), [4, 12]))]:
            with (
                patch('reasoning_under_change.work.monotonic_ns', side_effect=[0, 1000000001]),
                self.assertRaisesRegex(ScenarioError, 'TIMEOUT'),
            ):
                function(*args)
            with (
                patch('reasoning_under_change.work._SEARCH_NODE_LIMIT', 1),
                self.assertRaisesRegex(ScenarioError, 'NODE_LIMIT'),
            ):
                function(*args)

    def test_cli_frontier_is_read_only_and_errors_have_no_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            path.write_text(json.dumps(queue()))
            original = path.read_bytes()
            command = [sys.executable, '-m', 'reasoning_under_change.work', 'frontier', str(path)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), work.frontier(queue()))
            self.assertEqual(path.read_bytes(), original)
            path.write_text('{}')
            result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')
            self.assertEqual(json.loads(result.stderr)['error']['code'], 'INVALID_INPUT')
