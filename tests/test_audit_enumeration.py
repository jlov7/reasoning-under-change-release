"""Incremental exhaustive accounting must agree with a simple subset oracle."""
import copy
import random
import unittest
from unittest.mock import patch

from reasoning_under_change import work
from reasoning_under_change.audit import audit_plan


class IncrementalAuditTests(unittest.TestCase):
    def test_every_subset_total_matches_direct_arithmetic(self):
        from reasoning_under_change.audit import _subset_totals
        rng = random.Random(782411)
        for count in range(9):
            tasks = [{'id': f't{i}', 'value': rng.randrange(0, 9),
                      'demand': rng.randrange(1, 6),
                      'resources': {'a': rng.randrange(0, 4), 'b': rng.randrange(0, 3)}}
                     for i in range(count)]
            before = copy.deepcopy(tasks)
            rows = list(_subset_totals(tasks, ('a', 'b', 'absent')))
            self.assertEqual(len(rows), 1 << count)
            for mask, value, demand, resource_use in rows:
                selected = [task for i, task in enumerate(tasks) if mask & (1 << i)]
                with self.subTest(count=count, mask=mask):
                    self.assertEqual(value, sum(task['value'] for task in selected))
                    self.assertEqual(demand, sum(task['demand'] for task in selected))
                    self.assertEqual(resource_use, tuple(sum(task.get('resources', {}).get(k, 0)
                                                            for task in selected)
                                                        for k in ('a', 'b', 'absent')))
            self.assertEqual([row[0] for row in rows], list(range(1 << count)))
            self.assertEqual(tasks, before)

    def test_yielded_resource_profiles_do_not_alias_later_updates(self):
        from reasoning_under_change.audit import _subset_totals
        tasks = [{'id': 'x', 'value': 0, 'demand': 1, 'resources': {'a': 3}},
                 {'id': 'y', 'value': 4, 'demand': 2, 'resources': {'a': 1}}]
        rows = list(_subset_totals(tasks, ('a',)))
        self.assertEqual(rows, [(0, 0, 0, (0,)), (1, 0, 1, (3,)),
                                (2, 4, 2, (1,)), (3, 4, 3, (4,))])

    def test_witness_order_and_budget_exits_match_independent_enumeration(self):
        rng = random.Random(2026102607)
        for case in range(96):
            tasks = []
            for i in range(rng.randrange(2, 9)):
                tasks.append({'id': f't{i}', 'demand': rng.randrange(1, 5),
                              'value': rng.randrange(0, 13),
                              'requires': [f't{j}' for j in range(i) if rng.random() < .2],
                              'resources': {'a': rng.randrange(0, 5), 'b': rng.randrange(0, 4)}})
            source = {'revision': f'audit-{case}', 'capacity': rng.randrange(0, 13),
                      'resource_limits': {'a': rng.randrange(0, 13), 'b': rng.randrange(0, 11)},
                      'tasks': tasks, 'completed_ids': []}
            current = work.validate(source)
            submitted = {'input_digest': current['input_digest'], 'selected_ids': [], 'total_value': 0}
            candidates = current['input']['tasks']
            first = None
            for mask in range(1 << len(candidates)):
                selected = [t for i, t in enumerate(candidates) if mask & (1 << i)]
                ids = {t['id'] for t in selected}
                if (sum(t['demand'] for t in selected) > source['capacity'] or
                        any(not set(t.get('requires', [])) <= ids for t in selected) or
                        any(sum(t['resources'].get(k, 0) for t in selected) > limit
                            for k, limit in source['resource_limits'].items())):
                    continue
                value = sum(t['value'] for t in selected)
                if value > 0:
                    first = (mask, sorted(ids), value)
                    break
            for limit in (1, 5, 1 << len(tasks)):
                with self.subTest(case=case, limit=limit):
                    report = audit_plan(source, submitted, node_limit=limit, clock=lambda: 0)
                    if first is not None and first[0] < limit:
                        self.assertEqual(report['status'], 'SUBOPTIMAL')
                        self.assertEqual(report['subsets_checked'], first[0] + 1)
                        self.assertEqual(report['better_selection'],
                                         {'selected_ids': first[1], 'total_value': first[2]})
                    elif limit < (1 << len(tasks)):
                        self.assertEqual(report['status'], 'FEASIBLE_UNPROVEN')
                        self.assertEqual(report['subsets_checked'], limit)
                    else:
                        self.assertEqual(report['status'], 'OPTIMAL_FOR_INPUT')

    def test_required_closure_and_named_constraints_cannot_be_pruned_away(self):
        source = {'revision': 'mandatory', 'capacity': 7, 'completed_ids': ['a'],
                  'resource_limits': {'build': 4, 'review': 3},
                  'tasks': [{'id': 'a', 'demand': 1, 'value': 0},
                            {'id': 'b', 'demand': 2, 'value': 4, 'requires': ['a'],
                             'mandatory': True, 'resources': {'build': 2, 'review': 1}},
                            {'id': 'c', 'demand': 4, 'value': 9, 'resources': {'build': 2, 'review': 2}},
                            {'id': 'd', 'demand': 1, 'value': 99, 'resources': {'review': 4}}]}
        plan = work.plan(source)
        with patch.object(work, 'plan', side_effect=AssertionError('planner reuse')):
            report = audit_plan(source, plan, clock=lambda: 0)
        self.assertEqual(report['status'], 'OPTIMAL_FOR_INPUT')
        self.assertEqual(report['subsets_checked'], 16)
        self.assertEqual(plan['selected_ids'], ['a', 'b', 'c'])

    def test_late_deadline_never_claims_exactness(self):
        source = {'revision': 'small', 'capacity': 2, 'completed_ids': [],
                  'tasks': [{'id': 'a', 'demand': 1, 'value': 2}]}
        plan = work.plan(source)
        ticks = iter([0, 0, 0, 1_000_000_000])
        report = audit_plan(source, plan, clock=lambda: next(ticks))
        self.assertEqual(report['status'], 'FEASIBLE_UNPROVEN')
        self.assertEqual(report['stop_reason'], 'TIMEOUT')
        self.assertEqual(report['subsets_checked'], 2)
        self.assertFalse(report['enumeration_complete'])

    def test_nonempty_submission_witness_keeps_required_work_and_three_budgets(self):
        source = {'revision': 'three-budgets', 'capacity': 5, 'completed_ids': ['a'],
                  'resource_limits': {'build': 3, 'review': 2, 'gpu': 1},
                  'tasks': [{'id': 'a', 'demand': 1, 'value': 1, 'resources': {'build': 1}},
                            {'id': 'b', 'demand': 1, 'value': 2, 'requires': ['a'],
                             'mandatory': True, 'resources': {'review': 1}},
                            {'id': 'c', 'demand': 1, 'value': 50, 'resources': {'gpu': 2}},
                            {'id': 'd', 'demand': 2, 'value': 8,
                             'resources': {'build': 1, 'review': 1, 'gpu': 1}},
                            {'id': 'e', 'demand': 1, 'value': 4, 'resources': {'build': 2}},
                            {'id': 'f', 'demand': 6, 'value': 99}]}
        current = work.validate(source)
        submitted = {'input_digest': current['input_digest'],
                     'selected_ids': ['a', 'b', 'e'], 'total_value': 7}
        candidates = current['input']['tasks']
        for mask in range(1 << len(candidates)):
            subset = [task for i, task in enumerate(candidates) if mask & (1 << i)]
            ids = {task['id'] for task in subset}
            value = sum(task['value'] for task in subset)
            if ({'a', 'b'} <= ids and value > submitted['total_value']
                    and sum(task['demand'] for task in subset) <= source['capacity']
                    and all(set(task.get('requires', [])) <= ids for task in subset)
                    and all(sum(task.get('resources', {}).get(name, 0) for task in subset) <= limit
                            for name, limit in source['resource_limits'].items())):
                break
        else:
            self.fail('fixture must contain a better feasible selection')
        self.assertEqual((mask, sorted(ids), value), (11, ['a', 'b', 'd'], 11))
        stopped = audit_plan(source, submitted, node_limit=mask, clock=lambda: 0)
        self.assertEqual(stopped['status'], 'FEASIBLE_UNPROVEN')
        self.assertEqual(stopped['subsets_checked'], mask)
        self.assertIsNone(stopped['better_selection'])
        report = audit_plan(source, submitted, node_limit=mask + 1, clock=lambda: 0)
        self.assertEqual(report['status'], 'SUBOPTIMAL')
        self.assertEqual(report['subsets_checked'], mask + 1)
        self.assertEqual(report['better_selection'], {'selected_ids': sorted(ids), 'total_value': value})
