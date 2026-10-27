"""Named budgets, checked by independent exhaustive combinations."""
import copy
import itertools
import random
import unittest
from unittest.mock import patch

from reasoning_under_change import work
from reasoning_under_change.core import ScenarioError


def queue():
    return {'revision': 'r1', 'capacity': 8, 'completed_ids': [],
            'resource_limits': {'engineering': 5, 'review': 3}, 'tasks': [
                {'id': 'base', 'demand': 1, 'value': 0, 'requires': [],
                 'resources': {'engineering': 1}},
                {'id': 'a', 'demand': 3, 'value': 12, 'requires': ['base'],
                 'resources': {'engineering': 4, 'review': 3}},
                {'id': 'b', 'demand': 3, 'value': 11, 'requires': ['base'],
                 'resources': {'engineering': 2, 'review': 1}},
                {'id': 'c', 'demand': 2, 'value': 8, 'resources': {'engineering': 2, 'review': 2}},
                {'id': 'too-much', 'demand': 1, 'value': 1, 'resources': {'review': 4}}]}


def choices(document, capacity=None, forced=()):
    tasks = document['tasks']
    result = []
    for size in range(len(tasks) + 1):
        for subset in itertools.combinations(tasks, size):
            ids = {task['id'] for task in subset}
            if (not (set(document['completed_ids']) | set(forced)) <= ids
                    or any(task.get('mandatory') and task['id'] not in ids for task in tasks)
                    or any(not set(task.get('requires', [])) <= ids for task in subset)
                    or sum(task['demand'] for task in subset) > (
                        document['capacity'] if capacity is None else capacity)
                    or any(sum(task.get('resources', {}).get(name, 0) for task in subset) > limit
                           for name, limit in document['resource_limits'].items())):
                continue
            result.append((sum(task['value'] for task in subset), ids,
                           sum(task['demand'] for task in subset)))
    return result


class ResourceTests(unittest.TestCase):
    def assert_optimal(self, document, result, capacity=None):
        feasible = choices(document, capacity)
        self.assertIn((result['total_value'], set(result['selected_ids']), result['capacity_used']), feasible)
        self.assertEqual(result['total_value'], max(value for value, _, _ in feasible))
        for name, limit in document['resource_limits'].items():
            used = sum(task.get('resources', {}).get(name, 0) for task in document['tasks']
                       if task['id'] in result['selected_ids'])
            self.assertEqual(result['resources_used'][name], used)
            self.assertEqual(result['resource_slack'][name], limit - used)

    def test_scalar_feasible_queue_is_not_feasible_under_named_budgets(self):
        document = queue()
        scalar = copy.deepcopy(document); del scalar['resource_limits']
        for task in scalar['tasks']: task.pop('resources', None)
        self.assertEqual(work.plan(scalar)['selected_ids'], ['a', 'b', 'base', 'too-much'])
        try:
            result = work.plan(document)
        except ScenarioError as error:
            self.fail(f'named budgets must produce a feasible exact selection: {error}')
        self.assertEqual(result['selected_ids'], ['b', 'base', 'c'])
        self.assertEqual(result['schema'], 'ruc-work-plan-v3')
        self.assert_optimal(document, result)
        self.assertEqual(work.check(document, result)['status'], 'VALID')

    def test_seeded_dags_reports_and_forced_choices_match_independent_oracle(self):
        rng = random.Random(71291)
        for sample in range(24):
            document = {'revision': f'r{sample}', 'capacity': 14, 'completed_ids': [],
                        'resource_limits': {'engineer': 7, 'review': 6, 'lab': 4},
                        'tasks': [{'id': f't{i}', 'demand': rng.randint(1, 5),
                                   'value': rng.randint(0, 20),
                                   'requires': [f't{j}' for j in range(i) if rng.random() < .2],
                                   'mandatory': i == 0 and sample % 3 == 0,
                                   'resources': {name: rng.randint(0, 3)
                                                 for name in ['engineer', 'review', 'lab']}}
                                  for i in range(7)]}
            result = work.plan(document)
            self.assert_optimal(document, result)
            for point in work.sensitivity(document, [0, 4, 8, 14, 24])['points']:
                feasible = choices(document, point['capacity'])
                if feasible:
                    self.assert_optimal(document, point, point['capacity'])
                else:
                    self.assertEqual(point['status'], 'INFEASIBLE')
                    self.assertIsNone(point['resources_used'])
            points = work.frontier(document)['points']
            expected = []
            previous = None
            for capacity in range(129):
                feasible = choices(document, capacity)
                value = max((value for value, _, _ in feasible), default=None)
                if value is not None and value != previous:
                    expected.append((capacity, value)); previous = value
            self.assertEqual([(p['minimum_capacity'], p['total_value']) for p in points], expected)
            for point in points: self.assert_optimal(document, point, point['minimum_capacity'])
            for task in document['tasks']:
                if task['id'] in result['selected_ids']: continue
                report = work.counterfactual(document, task['id'])
                feasible = choices(document, forced=[task['id']])
                self.assertEqual(report['status'], 'FEASIBLE' if feasible else 'INFEASIBLE')
                if feasible:
                    self.assertEqual(report['forced_total_value'], max(v for v, _, _ in feasible))
                    self.assertTrue(all(value >= 0 for value in report['forced_resource_slack'].values()))

    def test_types_names_bounds_and_explicit_zero(self):
        for limits in (None, [], {'a': True}, {'a': 1.0}, {'a': -1}, {'a': 129},
                       {'bad name': 1}, {str(i): 0 for i in range(5)}):
            doc = queue(); doc['resource_limits'] = limits
            with self.subTest(limits=limits), self.assertRaises(ScenarioError): work.plan(doc)
        for resources in (None, [], {'review': True}, {'review': 1.0}, {'review': -1},
                          {'review': 129}, {'unknown': 0}):
            doc = queue(); doc['tasks'][0]['resources'] = resources
            with self.subTest(resources=resources), self.assertRaises(ScenarioError): work.plan(doc)
        doc = queue(); del doc['resource_limits']
        with self.assertRaisesRegex(ScenarioError, 'declared'): work.plan(doc)
        doc = {'revision': 'zero', 'capacity': 2, 'completed_ids': [],
               'resource_limits': {'review': 0}, 'tasks': [
                   {'id': 'a', 'demand': 1, 'value': 0},
                   {'id': 'b', 'demand': 1, 'value': 1, 'resources': {'review': 0}}]}
        self.assertEqual(work.plan(doc)['resources_used'], {'review': 0})
        doc['resource_limits'] = {}
        doc['tasks'][1]['resources'] = {}
        self.assertEqual(work.plan(doc)['resources_used'], {})

    def test_distinct_profiles_survive_state_merge_and_include_first_ties(self):
        doc = {'revision': 'profiles', 'capacity': 2, 'completed_ids': [],
               'resource_limits': {'code': 1, 'review': 1}, 'tasks': [
                   {'id': 'a', 'demand': 1, 'value': 2, 'resources': {'review': 1}},
                   {'id': 'b', 'demand': 1, 'value': 1, 'resources': {'code': 1}},
                   {'id': 'c', 'demand': 1, 'value': 10, 'resources': {'review': 1}}]}
        self.assertEqual(work.plan(doc)['selected_ids'], ['b', 'c'])
        doc['tasks'] = [{'id': 'a', 'demand': 2, 'value': 3},
                        {'id': 'b', 'demand': 1, 'value': 3}]
        self.assertEqual(work.plan(doc)['selected_ids'], ['a'])
        doc['resource_limits'] = {f'r{i}': 128 for i in range(4)}
        doc['capacity'] = 128
        doc['tasks'] = [{'id': 'maximum', 'demand': 128, 'value': 10000,
                         'resources': dict(doc['resource_limits'])}]
        self.assertEqual(work.plan(doc)['resources_used'], doc['resource_limits'])

    def test_identity_completed_accounting_replan_and_history(self):
        doc = queue(); first = work.plan(doc)
        changed = copy.deepcopy(doc); changed.update(revision='r2', completed_ids=['base'])
        changed['resource_limits']['engineering'] = 4
        second = work.replan(first, changed)
        self.assertEqual(second['selected_ids'], ['b', 'base'])
        self.assertEqual(work.verify_history([first, second])['status'], 'VALID')
        reordered = copy.deepcopy(changed); reordered['tasks'].reverse()
        self.assertEqual(work.plan(changed), work.plan(reordered))
        altered = copy.deepcopy(changed); altered['resource_limits']['review'] = 2
        with self.assertRaisesRegex(ScenarioError, 'stale'): work.check(altered, second)
        altered = copy.deepcopy(changed); altered['revision'] = 'r3'
        altered['tasks'][0]['resources']['engineering'] = 0
        with self.assertRaisesRegex(ScenarioError, 'definitions'): work.replan(second, altered)
        altered = copy.deepcopy(changed); altered['resource_limits']['engineering'] = 0
        with self.assertRaisesRegex(ScenarioError, 'completed'): work.plan(altered)
        altered = copy.deepcopy(first); altered['resources_used']['review'] = 0
        with self.assertRaises(ScenarioError): work.check(doc, altered)

    def test_required_and_forced_infeasible_and_fail_closed(self):
        doc = queue()
        self.assertEqual(work.counterfactual(doc, 'too-much')['status'], 'INFEASIBLE')
        doc['tasks'][-1]['mandatory'] = True
        with self.assertRaisesRegex(ScenarioError, 'resource'): work.plan(doc)
        self.assertEqual(work.frontier(doc)['status'], 'INFEASIBLE')
        self.assertEqual(work.scenario_analysis(doc, {'schema': 'ruc-work-scenarios-v1', 'scenarios': []})['status'],
                         'NO_COMMON_FEASIBLE')
        with (
            patch('reasoning_under_change.work._SEARCH_NODE_LIMIT', 1),
            self.assertRaisesRegex(ScenarioError, 'NODE_LIMIT'),
        ):
            work.plan(queue())
        with (
            patch('reasoning_under_change.work._SCENARIO_NODE_LIMIT', 1),
            self.assertRaisesRegex(ScenarioError, 'NODE_LIMIT'),
        ):
            work.scenario_analysis(queue(), {'schema': 'ruc-work-scenarios-v1', 'scenarios': []})
        with (
            patch('reasoning_under_change.work.monotonic_ns', side_effect=[0, 1000000001]),
            self.assertRaisesRegex(ScenarioError, 'TIMEOUT'),
        ):
            work.frontier(queue())

    def test_scenarios_preserve_profiles_and_match_independent_regret(self):
        doc = queue()
        supplied = {'schema': 'ruc-work-scenarios-v1', 'scenarios': [
            {'id': 'a-critical', 'capacity': 6, 'overrides': [{'id': 'a', 'value': 30}]}]}
        report = work.scenario_analysis(doc, supplied)
        changed = copy.deepcopy(doc); changed['capacity'] = 6; changed['tasks'][1]['value'] = 30
        variants = [doc, changed]
        feasible = [choices(case) for case in variants]
        optima = [min(options, key=lambda x: (-x[0], x[2], tuple(sorted(x[1])))) for options in feasible]
        common = [entry[1] for entry in feasible[0] if any(other[1] == entry[1] for other in feasible[1])]
        def score(ids):
            entries = [next(entry for entry in options if entry[1] == ids) for options in feasible]
            return (max(opt[0] - entry[0] for opt, entry in zip(optima, entries)),
                    -min(e[0] for e in entries), -entries[0][0], max(e[2] for e in entries), tuple(sorted(ids)))
        self.assertEqual(report['recommended']['selected_ids'], sorted(min(common, key=score)))
        self.assertEqual(report['schema'], 'ruc-work-scenario-analysis-v2')
        for point, optimum, case in zip(report['scenarios'], optima, variants):
            self.assertEqual(point['optimum']['selected_ids'], sorted(optimum[1]))
            for task, original in zip(point['input']['tasks'], sorted(case['tasks'], key=lambda t: t['id'])):
                self.assertEqual(task.get('resources'), original.get('resources'))
            self.assertTrue(all(v >= 0 for v in point['recommended']['resource_slack'].values()))
        supplied['scenarios'][0]['overrides'][0]['resources'] = {'review': 0}
        with self.assertRaises(ScenarioError): work.scenario_analysis(doc, supplied)


if __name__ == '__main__': unittest.main()
