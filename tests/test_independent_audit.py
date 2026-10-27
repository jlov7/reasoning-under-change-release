"""Decision checks using a separate exhaustive algorithm, not planner self-replay."""
import contextlib
import copy
import io
import random
import unittest
from unittest.mock import patch

from reasoning_under_change import work
from reasoning_under_change.audit import audit_plan, main
from reasoning_under_change.core import ScenarioError


def document():
    return {'revision': 'review-1', 'capacity': 4, 'completed_ids': [], 'tasks': [
        {'id': 'a', 'demand': 3, 'value': 4},
        {'id': 'b', 'demand': 2, 'value': 3},
        {'id': 'c', 'demand': 2, 'value': 3}]}


class IndependentAuditTests(unittest.TestCase):
    def test_module_cli_version_does_not_require_input_files(self):
        output = io.StringIO()
        with patch('sys.argv', ['audit', '--version']), contextlib.redirect_stdout(output), \
                self.assertRaises(SystemExit) as stopped:
            main()
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(output.getvalue(), '1.1.1\n')

    def test_returns_actual_better_selection_not_just_a_red_badge(self):
        source = document(); plan = work.plan(source)
        plan.update(selected_ids=['a'], total_value=4)
        report = audit_plan(source, plan)
        self.assertEqual(report['status'], 'SUBOPTIMAL')
        self.assertEqual(report['better_selection'], {'selected_ids': ['b', 'c'], 'total_value': 6})

    def test_optimal_selection_does_not_call_any_planner_or_checker(self):
        source = document(); plan = work.plan(source)
        with patch.object(work, 'plan', side_effect=AssertionError('planner called')), \
                patch.object(work, 'check', side_effect=AssertionError('checker called')), \
                patch.object(work, '_selection', side_effect=AssertionError('solver called')):
            report = audit_plan(source, plan)
        self.assertEqual(report['status'], 'OPTIMAL_FOR_INPUT')
        self.assertTrue(report['enumeration_complete'])
        self.assertEqual(report['subsets_checked'], 8)

    def test_budget_exhaustion_never_claims_optimality(self):
        source = document(); plan = work.plan(source)
        report = audit_plan(source, plan, node_limit=1)
        self.assertEqual(report['status'], 'FEASIBLE_UNPROVEN')
        self.assertFalse(report['enumeration_complete'])
        self.assertEqual(report['stop_reason'], 'NODE_LIMIT')

    def test_expired_deadline_never_claims_optimality(self):
        source = document(); plan = work.plan(source)
        times = iter((0, 2_000_000_000))
        report = audit_plan(source, plan, clock=lambda: next(times))
        self.assertEqual(report['status'], 'FEASIBLE_UNPROVEN')
        self.assertEqual(report['subsets_checked'], 0)
        self.assertEqual(report['stop_reason'], 'TIMEOUT')

    def test_infeasible_and_missing_required_work(self):
        source = {'revision': 'r1', 'capacity': 4, 'completed_ids': ['a'],
                  'resource_limits': {'review': 1}, 'tasks': [
                      {'id': 'a', 'demand': 1, 'value': 1},
                      {'id': 'b', 'demand': 1, 'value': 5, 'requires': ['a'], 'resources': {'review': 1}},
                      {'id': 'c', 'demand': 1, 'value': 1, 'mandatory': True},
                      {'id': 'd', 'demand': 1, 'value': 6, 'resources': {'review': 1}}]}
        baseline = work.plan(source)
        for ids in (['b', 'c'], ['a', 'b'], ['a', 'b', 'c', 'd']):
            changed = copy.deepcopy(baseline)
            changed.update(selected_ids=ids, total_value=sum(t['value'] for t in source['tasks'] if t['id'] in ids))
            with self.subTest(ids=ids):
                self.assertEqual(audit_plan(source, changed)['status'], 'INFEASIBLE_SELECTION')

    def test_alternative_optimal_tie_is_accepted(self):
        source = {'revision': 'r1', 'capacity': 1, 'completed_ids': [], 'tasks': [
            {'id': 'a', 'demand': 1, 'value': 1}, {'id': 'b', 'demand': 1, 'value': 1}]}
        selected = work.plan(source)
        selected.update(selected_ids=['b'], total_value=1)
        self.assertEqual(audit_plan(source, selected)['status'], 'OPTIMAL_FOR_INPUT')

    def test_stale_duplicate_unknown_boolean_and_wrong_total_are_refused(self):
        source = document(); good = work.plan(source)
        for change in ({'input_digest': '0' * 64}, {'selected_ids': ['b', 'b']},
                       {'selected_ids': ['missing']}, {'selected_ids': [True]},
                       {'total_value': True}, {'total_value': 5}):
            with self.subTest(change=change), self.assertRaises(ScenarioError):
                audit_plan(source, good | change)

    def test_inputs_are_not_mutated(self):
        source = document(); plan = work.plan(source)
        saved = copy.deepcopy((source, plan))
        audit_plan(source, plan)
        self.assertEqual((source, plan), saved)

    def test_differential_random_small_dependencies_and_resources(self):
        rng = random.Random(20261025)
        for index in range(128):
            tasks = []
            for i in range(rng.randrange(1, 8)):
                tasks.append({'id': f't{i}', 'demand': rng.randrange(1, 5),
                              'value': rng.randrange(0, 10),
                              'requires': [f't{j}' for j in range(i) if rng.random() < .15],
                              'resources': {'review': rng.randrange(0, 3)}})
            source = {'revision': f'r{index}', 'capacity': rng.randrange(0, 16),
                      'completed_ids': [], 'resource_limits': {'review': rng.randrange(0, 8)}, 'tasks': tasks}
            with self.subTest(index=index):
                report = audit_plan(source, work.plan(source))
                self.assertEqual(report['status'], 'OPTIMAL_FOR_INPUT')
                self.assertEqual(report['subsets_checked'], 2 ** len(tasks))

    def test_empty_problem_and_budget_validation(self):
        source = {'revision': 'empty', 'capacity': 0, 'tasks': [], 'completed_ids': []}
        plan = work.plan(source)
        self.assertEqual(audit_plan(source, plan)['subsets_checked'], 1)
        for kwargs in ({'node_limit': 0}, {'node_limit': True}, {'timeout_ns': 0},
                       {'timeout_ns': 30_000_000_001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ScenarioError):
                audit_plan(source, plan, **kwargs)
