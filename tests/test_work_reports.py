"""Offline reports, compared with an independent exhaustive subset oracle."""
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
    return {'revision': 'release-1', 'capacity': 10, 'completed_ids': [], 'tasks': [
        {'id': 'urgent', 'demand': 7, 'value': 12},
        {'id': 'lint', 'demand': 5, 'value': 9},
        {'id': 'tests', 'demand': 5, 'value': 9}]}


def oracle(document, capacity, forced=()):
    tasks = document['tasks']
    fixed = set(document['completed_ids']) | set(forced)
    feasible = []
    for size in range(len(tasks) + 1):
        for subset in itertools.combinations(tasks, size):
            ids = {task['id'] for task in subset}
            if fixed <= ids and sum(task['demand'] for task in subset) <= capacity:
                feasible.append((sum(task['value'] for task in subset), ids))
    return feasible


class WorkReportTests(unittest.TestCase):
    def test_reports_match_oracle_on_varied_queues_and_completions(self):
        randomizer = random.Random(417)
        for index in range(12):
            document = {'revision': f'queue-{index}', 'capacity': 14,
                        'completed_ids': ['job0'] if index % 2 else [],
                        'tasks': [{'id': f'job{item}', 'demand': randomizer.randint(1, 8),
                                   'value': randomizer.randint(1, 30)} for item in range(7)]}
            for point in work.sensitivity(document, [0, 4, 8, 14, 20, 32])['points']:
                feasible = oracle(document, point['capacity'])
                if feasible:
                    self.assertEqual(point['total_value'], max(value for value, _ in feasible))
                    self.assertIn((point['total_value'], set(point['selected_ids'])), feasible)
                else:
                    self.assertEqual(point['status'], 'INFEASIBLE')
            chosen = work.plan(document)
            for task in document['tasks']:
                if task['id'] in chosen['selected_ids']:
                    continue
                result = work.counterfactual(document, task['id'])
                feasible = oracle(document, document['capacity'], [task['id']])
                self.assertEqual(result['status'], 'FEASIBLE' if feasible else 'INFEASIBLE')
                if feasible:
                    self.assertEqual(result['forced_total_value'], max(value for value, _ in feasible))
                    self.assertIn((result['forced_total_value'], set(result['forced_ids'])), feasible)

    def test_counterfactual_trap_and_independent_oracle(self):
        result = work.counterfactual(queue(), 'urgent')
        self.assertEqual(result['status'], 'FEASIBLE')
        self.assertEqual(result['chosen_total_value'], 18)
        self.assertEqual(result['forced_total_value'], 12)
        self.assertEqual(result['sacrificed_value'], 6)
        self.assertEqual(result['replaced_ids'], ['lint', 'tests'])
        for capacity in range(21):
            document = queue(); document['capacity'] = capacity
            chosen = work.plan(document)
            for task in document['tasks']:
                if task['id'] in chosen['selected_ids']:
                    continue
                result = work.counterfactual(document, task['id'])
                feasible = oracle(document, capacity, [task['id']])
                self.assertEqual(result['status'], 'FEASIBLE' if feasible else 'INFEASIBLE')
                if feasible:
                    self.assertIn((result['forced_total_value'], set(result['forced_ids'])), feasible)
                    self.assertEqual(result['forced_total_value'], max(v for v, _ in feasible))
                    self.assertEqual(result['sacrificed_value'], chosen['total_value'] - result['forced_total_value'])
                else:
                    self.assertIsNone(result['forced_total_value'])

    def test_counterfactual_fixes_completed_and_rejects_selected_missing(self):
        document = queue(); document['completed_ids'] = ['lint']
        result = work.counterfactual(document, 'urgent')
        self.assertEqual(result['status'], 'INFEASIBLE')
        for task_id in ('lint', 'tests', 'missing'):
            with self.assertRaises(ScenarioError):
                work.counterfactual(document, task_id)

    def test_capacity_points_and_completed_infeasibility(self):
        document = queue(); document['completed_ids'] = ['lint']
        saved = copy.deepcopy(document)
        result = work.sensitivity(document, [0, 4, 5, 6, 10, 12, 17])
        values = []
        for point in result['points']:
            feasible = oracle(document, point['capacity'])
            self.assertEqual(point['status'], 'FEASIBLE' if feasible else 'INFEASIBLE')
            if feasible:
                self.assertIn((point['total_value'], set(point['selected_ids'])), feasible)
                self.assertEqual(point['total_value'], max(v for v, _ in feasible))
                values.append(point['total_value'])
            else:
                self.assertIsNone(point['total_value'])
        self.assertEqual(values, sorted(values))
        self.assertEqual(document, saved)

    def test_metadata_is_optional_strict_inert_and_identity_bound(self):
        document = queue()
        old = work.plan(document)
        document['metadata'] = {'schema': 'ruc-work-metadata-v1',
                                'resource_unit': 'CPU minutes',
                                'value_basis': 'Authored priority estimates; $(false) is inert.'}
        result = work.plan(document)
        self.assertEqual(result['selected_ids'], old['selected_ids'])
        self.assertEqual(result['input']['metadata'], document['metadata'])
        self.assertEqual(work.check(json.loads(json.dumps(document)), result)['status'], 'VALID')
        for key in ('resource_unit', 'value_basis'):
            changed = copy.deepcopy(document); changed['metadata'][key] += ' revised'
            with self.assertRaisesRegex(ScenarioError, 'stale'):
                work.check(changed, result)
        for metadata in (None, {}, dict(document['metadata'], extra=1),
                         dict(document['metadata'], schema='v2'),
                         dict(document['metadata'], resource_unit=''),
                         dict(document['metadata'], resource_unit='x' * 81),
                         dict(document['metadata'], value_basis='x' * 513),
                         dict(document['metadata'], value_basis=True)):
            bad = copy.deepcopy(document); bad['metadata'] = metadata
            with self.assertRaises(ScenarioError):
                work.plan(bad)
        bad = copy.deepcopy(document); bad['unexpected'] = True
        with self.assertRaises(ScenarioError):
            work.plan(bad)

    def history(self):
        document = queue()
        history = [work.plan(document)]
        for revision, capacity, completed in [('release-2', 12, ['lint']),
                                               ('release-3', 17, ['lint', 'urgent']),
                                               ('release-4', 20, ['lint', 'urgent'])]:
            document = dict(document, revision=revision, capacity=capacity, completed_ids=completed)
            history.append(work.replan(history[-1], document))
        return history

    def test_history_validates_every_edge_without_mutating(self):
        history = self.history(); saved = copy.deepcopy(history)
        result = work.verify_history(history)
        self.assertEqual(result['status'], 'VALID')
        self.assertEqual(result['revisions'], ['release-1', 'release-2', 'release-3', 'release-4'])
        self.assertEqual(history, saved)
        self.assertIn('not authenticity', result['claim_boundary'])

    def test_history_rejects_missing_swapped_forked_and_edited_links(self):
        history = self.history()
        fork = dict(queue(), revision='fork', capacity=9)
        forked = work.replan(history[0], fork)
        cases = [history[1:], [history[0], *history[2:]],
                 [history[0], history[2], history[1], history[3]],
                 [history[0], forked, *history[2:]]]
        for key in ('previous_input_digest', 'completed_ids', 'total_value'):
            changed = copy.deepcopy(history)
            changed[2][key] = 'edited'
            cases.append(changed)
        repeated = copy.deepcopy(history)
        repeated.append(work.replan(history[-1], dict(history[0]['input'], capacity=20,
                                                     completed_ids=['lint', 'urgent'])))
        cases.append(repeated)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ScenarioError):
                work.verify_history(case)

    def test_report_bounds_and_total_timeout_fail_closed(self):
        for capacities in ([], list(range(33)), [True], [-1], [129], [1, 1], '1'):
            with self.assertRaises(ScenarioError):
                work.sensitivity(queue(), capacities)
        for history in ([], [work.plan(queue())], [work.plan(queue())] * 33):
            with self.assertRaises(ScenarioError):
                work.verify_history(history)
        for function, args in [(work.sensitivity, (queue(), [1, 2])),
                               (work.counterfactual, (queue(), 'urgent')),
                               (work.verify_history, (self.history(),))]:
            with (
                patch('reasoning_under_change.work.monotonic_ns', side_effect=[0, 1000000001]),
                self.assertRaisesRegex(ScenarioError, 'TIMEOUT'),
            ):
                function(*args)

    def test_cli_reports_history_rejection_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); input_path = root / 'queue.json'
            input_path.write_text(json.dumps(queue()))
            def run(*args):
                return subprocess.run([sys.executable, '-m', 'reasoning_under_change.work',
                                       *map(str, args)], text=True, capture_output=True, timeout=5, check=False)
            for args in [('counterfactual', input_path, 'urgent'),
                         ('sensitivity', input_path, '--capacities', '0', '10', '12')]:
                response = run(*args)
                self.assertEqual(response.returncode, 0, response.stderr)
                self.assertIsInstance(json.loads(response.stdout), dict)
            paths = []
            for index, item in enumerate(self.history()):
                path = root / f'plan-{index}.json'; paths.append(path)
                path.write_text(json.dumps(item))
            saved = [path.read_bytes() for path in paths]
            self.assertEqual(run('history', *paths).returncode, 0)
            rejected = run('history', paths[0], paths[2], paths[3])
            self.assertEqual(rejected.returncode, 2)
            self.assertNotIn(directory, rejected.stderr)
            self.assertEqual(json.loads(rejected.stderr)['error']['code'], 'INVALID_INPUT')
            self.assertEqual(run('history', *paths).returncode, 0)
            self.assertEqual([path.read_bytes() for path in paths], saved)


if __name__ == '__main__':
    unittest.main()
