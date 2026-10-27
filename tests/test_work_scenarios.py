"""Finite supplied scenarios, checked against independent combinations enumeration."""
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
    return {'revision': 'r1', 'capacity': 7, 'completed_ids': ['base'], 'tasks': [
        {'id': 'base', 'demand': 1, 'value': 0, 'requires': []},
        {'id': 'check', 'demand': 1, 'value': 2, 'requires': ['base'], 'mandatory': True},
        {'id': 'a', 'demand': 5, 'value': 12, 'requires': ['base']},
        {'id': 'b', 'demand': 3, 'value': 8, 'requires': ['base']},
        {'id': 'c', 'demand': 2, 'value': 5, 'requires': ['base']}]}


def pack():
    return {'schema': 'ruc-work-scenarios-v1', 'scenarios': [
        {'id': 'tight', 'capacity': 6, 'overrides': [{'id': 'b', 'value': 10}]},
        {'id': 'a-important', 'overrides': [{'id': 'a', 'demand': 4, 'value': 18}]}]}


def cases(document, supplied):
    result = [('nominal', copy.deepcopy(document))]
    for case in sorted(supplied['scenarios'], key=lambda c: c['id']):
        doc = copy.deepcopy(document)
        doc['capacity'] = case.get('capacity', doc['capacity'])
        for change in case.get('overrides', []):
            next(t for t in doc['tasks'] if t['id'] == change['id']).update(
                {k: v for k, v in change.items() if k != 'id'})
        result.append((case['id'], doc))
    return result


def oracle(document, supplied):
    variants = cases(document, supplied)
    subsets = [{t['id'] for t in subset} for size in range(len(document['tasks']) + 1)
               for subset in itertools.combinations(document['tasks'], size)]
    def measure(ids, doc):
        return (sum(t['demand'] for t in doc['tasks'] if t['id'] in ids),
                sum(t['value'] for t in doc['tasks'] if t['id'] in ids))
    def feasible(ids, doc):
        return (set(doc['completed_ids']) <= ids and
                all(not t.get('mandatory') or t['id'] in ids for t in doc['tasks']) and
                all(set(t.get('requires', [])) <= ids for t in doc['tasks'] if t['id'] in ids) and
                measure(ids, doc)[0] <= doc['capacity'])
    optima = []
    for _, doc in variants:
        choices = [ids for ids in subsets if feasible(ids, doc)]
        optima.append(min(choices, key=lambda ids: (-measure(ids, doc)[1], measure(ids, doc)[0],
                                                  tuple(sorted(ids)))) if choices else None)
    common = [ids for ids in subsets if all(feasible(ids, doc) for _, doc in variants)]
    def score(ids):
        totals = [measure(ids, doc) for _, doc in variants]
        regrets = [measure(opt, doc)[1] - value for opt, (_, doc), (_, value)
                   in zip(optima, variants, totals)]
        return (max(regrets), -min(v for _, v in totals), -totals[0][1],
                max(d for d, _ in totals), tuple(sorted(ids)))
    best = min(common, key=score) if common else None
    return variants, optima, best, measure, feasible


class ScenarioAnalysisTests(unittest.TestCase):
    def compare(self, document, supplied):
        result = work.scenario_analysis(document, supplied)
        variants, optima, best, measure, feasible = oracle(document, supplied)
        self.assertEqual(result['status'], 'FEASIBLE' if best is not None else 'NO_COMMON_FEASIBLE')
        self.assertEqual(result['recommended']['selected_ids'] if best is not None else None,
                         sorted(best) if best is not None else None)
        self.assertEqual(result['recommended'] is None, best is None)
        for point, (name, doc), optimum in zip(result['scenarios'], variants, optima):
            self.assertEqual(point['id'], name)
            self.assertEqual(point['optimum']['selected_ids'] if optimum is not None else None,
                             sorted(optimum) if optimum is not None else None)
            if best is not None:
                demand, value = measure(best, doc)
                self.assertEqual(point['recommended'], {'capacity_used': demand, 'total_value': value,
                    'slack': doc['capacity'] - demand, 'regret': measure(optimum, doc)[1] - value})
            nominal = result['nominal_plan']
            if nominal is not None:
                ids = set(nominal['selected_ids']); demand, value = measure(ids, doc)
                self.assertEqual(point['nominal_plan']['feasible'], feasible(ids, doc))
                self.assertEqual(point['nominal_plan']['regret'],
                                 measure(optimum, doc)[1] - value if feasible(ids, doc) else None)
        return result

    def test_common_selection_regret_completed_order_and_nominal_fragility(self):
        document, supplied = queue(), pack(); saved = copy.deepcopy((document, supplied))
        result = self.compare(document, supplied)
        self.assertEqual((document, supplied), saved)
        self.assertEqual(result['nominal_plan']['selected_ids'], work.plan(document)['selected_ids'])
        self.assertTrue(any(not s['nominal_plan']['feasible'] for s in result['scenarios']))
        self.assertEqual(result['required_ids'], ['base', 'check'])
        seen = set(result['completed_ids'])
        by_id = {t['id']: t for t in document['tasks']}
        for item in result['recommended']['pending_order']:
            self.assertLessEqual(set(by_id[item].get('requires', [])), seen)
            seen.add(item)
        self.assertEqual(sorted(seen), result['recommended']['selected_ids'])
        with self.assertRaises(ScenarioError): work.check(document, result)
        with self.assertRaises(ScenarioError): work.verify_history([result, result])

    def test_individual_optimum_is_not_restricted_to_common_feasible_choices(self):
        document = {'revision': 'fragile', 'capacity': 5, 'completed_ids': [], 'tasks': [
            {'id': 'a', 'demand': 5, 'value': 10}, {'id': 'b', 'demand': 5, 'value': 6}]}
        supplied = {'schema': 'ruc-work-scenarios-v1', 'scenarios': [
            {'id': 'a-longer', 'overrides': [{'id': 'a', 'demand': 6}]}]}
        result = self.compare(document, supplied)
        self.assertEqual(result['recommended']['selected_ids'], ['b'])
        self.assertEqual([s['recommended']['regret'] for s in result['scenarios']], [4, 0])
        alternate = result['scenarios'][1]['nominal_plan']
        self.assertEqual((alternate['feasible'], alternate['slack'], alternate['regret']), (False, -1, None))

    def test_correlated_changes_do_not_invent_envelope_scenario(self):
        document = {'revision': 'correlated', 'capacity': 5, 'completed_ids': [], 'tasks': [
            {'id': 'a', 'demand': 4, 'value': 5}, {'id': 'b', 'demand': 1, 'value': 5}]}
        supplied = {'schema': 'ruc-work-scenarios-v1', 'scenarios': [
            {'id': 'swap', 'overrides': [{'id': 'a', 'demand': 1}, {'id': 'b', 'demand': 4}]}]}
        self.assertEqual(self.compare(document, supplied)['recommended']['selected_ids'], ['a', 'b'])

    def test_small_seeded_dags_match_exhaustive_independent_oracle(self):
        rng = random.Random(81237)
        for sample in range(35):
            document = {'revision': f'r{sample}', 'capacity': 16, 'completed_ids': ['t0'],
                        'tasks': [{'id': f't{i}', 'demand': rng.randint(1, 5),
                                   'value': rng.randint(0, 12),
                                   'requires': [f't{j}' for j in range(i) if rng.random() < .25],
                                   'mandatory': i == 2 and sample % 3 == 0} for i in range(7)]}
            supplied = {'schema': 'ruc-work-scenarios-v1', 'scenarios': [
                {'id': f's{j}', 'capacity': rng.randint(5, 19), 'overrides': [
                    {'id': f't{i}', 'demand': rng.randint(1, 6), 'value': rng.randint(0, 15)}
                    for i in range(1, 7)]} for j in range(3)]}
            self.compare(document, supplied)

    def test_tie_rules_and_reordered_canonical_identities(self):
        document = {'revision': 'tie', 'capacity': 3, 'completed_ids': [], 'tasks': [
            {'id': 'z', 'demand': 3, 'value': 5}, {'id': 'b', 'demand': 2, 'value': 5},
            {'id': 'a', 'demand': 2, 'value': 5}]}
        empty = {'schema': 'ruc-work-scenarios-v1', 'scenarios': []}
        result = self.compare(document, empty)
        self.assertEqual(result['recommended']['selected_ids'], ['a'])
        document, supplied = queue(), pack()
        first = work.scenario_analysis(document, supplied)
        document['tasks'].reverse(); supplied['scenarios'].reverse()
        self.assertEqual(first, work.scenario_analysis(document, supplied))
        supplied['scenarios'][0]['id'] = 'renamed'
        self.assertNotEqual(first['scenario_pack_digest'], work.scenario_analysis(document, supplied)['scenario_pack_digest'])
        document['revision'] = 'r2'
        self.assertNotEqual(first['input_digest'], work.scenario_analysis(document, supplied)['input_digest'])

    def test_minimum_value_then_nominal_value_break_equal_regret(self):
        document = {'revision': 'ties', 'capacity': 1, 'completed_ids': [], 'tasks': [
            {'id': 'a', 'demand': 1, 'value': 10}, {'id': 'b', 'demand': 1, 'value': 8}]}
        supplied = {'schema': 'ruc-work-scenarios-v1', 'scenarios': [
            {'id': 'other', 'overrides': [{'id': 'a', 'value': 2}, {'id': 'b', 'value': 4}]}]}
        self.assertEqual(self.compare(document, supplied)['recommended']['selected_ids'], ['b'])
        document['tasks'][1]['value'] = 9
        supplied['scenarios'] = [
            {'id': 'one', 'overrides': [{'id': 'a', 'value': 5}, {'id': 'b', 'value': 5}]},
            {'id': 'two', 'overrides': [{'id': 'a', 'value': 10}, {'id': 'b', 'value': 11}]}]
        self.assertEqual(self.compare(document, supplied)['recommended']['selected_ids'], ['a'])

    def test_capacity_override_and_base_changes_bind_identity(self):
        document, supplied = queue(), pack()
        original = work.scenario_analysis(document, supplied)
        changed_pack = copy.deepcopy(supplied)
        changed_pack['scenarios'][0]['capacity'] += 1
        changed = work.scenario_analysis(document, changed_pack)
        self.assertNotEqual(original['scenario_pack_digest'], changed['scenario_pack_digest'])
        supplied['scenarios'][0]['overrides'][0]['value'] += 1
        changed = work.scenario_analysis(document, supplied)
        self.assertNotEqual(original['scenario_pack_digest'], changed['scenario_pack_digest'])
        document['capacity'] += 1
        changed = work.scenario_analysis(document, pack())
        self.assertNotEqual(original['input_digest'], changed['input_digest'])
        self.assertNotEqual(original['scenario_pack_digest'], changed['scenario_pack_digest'])

    def test_no_common_feasible_and_infeasible_nominal(self):
        supplied = pack(); supplied['scenarios'][0]['capacity'] = 1
        result = self.compare(queue(), supplied)
        self.assertIsNone(result['recommended'])
        self.assertIsNone(next(s for s in result['scenarios'] if s['id'] == 'tight')['optimum'])
        document = queue(); document['capacity'] = 1
        self.assertIsNone(self.compare(document, pack())['nominal_plan'])

    def test_strict_pack_fields_ids_overrides_numbers_and_completed(self):
        invalid = [None, {}, {'schema': 'v2', 'scenarios': []}, dict(pack(), extra=1),
                   dict(pack(), scenarios=[{'id': 'nominal'}]),
                   dict(pack(), scenarios=[{'id': '../unsafe'}]),
                   dict(pack(), scenarios=[{'id': 'same'}, {'id': 'same'}]),
                   dict(pack(), scenarios=[{'id': str(i)} for i in range(8)])]
        for fields in ({'capacity': True}, {'capacity': -1}, {'capacity': 129}, {'capacity': 0},
                       {'probability': .2}, {'overrides': {}}, {'overrides': [None]},
                       {'overrides': [{'id': 'missing', 'value': 2}]},
                       {'overrides': [{'id': 'base', 'value': 0}]},
                       {'overrides': [{'id': 'a'}]},
                       {'overrides': [{'id': 'a', 'demand': 0}]},
                       {'overrides': [{'id': 'a', 'value': True}]},
                       {'overrides': [{'id': 'a', 'value': -1}]},
                       {'overrides': [{'id': 'a', 'requires': []}]},
                       {'overrides': [{'id': 'a', 'mandatory': True}]},
                       {'overrides': [{'id': 'a', 'value': 2}] * 2}):
            invalid.append(dict(pack(), scenarios=[dict(id='bad', **fields)]))
        for supplied in invalid:
            with self.subTest(supplied=supplied), self.assertRaises(ScenarioError):
                work.scenario_analysis(queue(), supplied)

    def test_shared_deadline_work_cap_and_no_partial_report(self):
        with (
            patch('reasoning_under_change.work.monotonic_ns', side_effect=[0, 1000000001]),
            self.assertRaisesRegex(ScenarioError, 'TIMEOUT'),
        ):
            work.scenario_analysis(queue(), pack())
        with (
            patch('reasoning_under_change.work._SCENARIO_NODE_LIMIT', 1),
            self.assertRaisesRegex(ScenarioError, 'NODE_LIMIT'),
        ):
            work.scenario_analysis(queue(), pack())
        with patch('reasoning_under_change.work._Deadline', wraps=work._Deadline) as deadline:
            work.scenario_analysis(queue(), pack())
            self.assertEqual(deadline.call_count, 1)
        maximum = {'revision': 'max', 'capacity': 128, 'completed_ids': [], 'tasks': [
            {'id': f't{i:02}', 'demand': 1, 'value': 1, 'mandatory': True} for i in range(20)]}
        supplied = {'schema': 'ruc-work-scenarios-v1', 'scenarios': [{'id': f's{i}'} for i in range(7)]}
        self.assertEqual(len(work.scenario_analysis(maximum, supplied)['recommended']['selected_ids']), 20)
        empty = {'revision': 'empty', 'capacity': 0, 'completed_ids': [], 'tasks': []}
        self.assertEqual(work.scenario_analysis(empty, dict(pack(), scenarios=[]))['recommended']['selected_ids'], [])
        maximum['tasks'].append({'id': 'extra', 'demand': 1, 'value': 1})
        with self.assertRaises(ScenarioError): work.scenario_analysis(maximum, supplied)

    def test_cli_saved_report_changes_invalid_completed_infeasible_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root/'queue.json'; supplied = root/'scenarios.json'; out = root/'report.json'
            source.write_text(json.dumps(queue())); supplied.write_text(json.dumps(pack()))
            def run(*args):
                return subprocess.run([sys.executable, '-m', 'reasoning_under_change.work',
                    *map(str, args)], capture_output=True, text=True, timeout=5, check=False)
            response = run('scenarios', source, supplied, '--out', out)
            self.assertEqual(response.returncode, 0, response.stderr)
            saved = out.read_bytes()
            self.assertEqual(json.loads(saved), json.loads(response.stdout))
            response = run('scenarios', source, supplied, '--out', out)
            self.assertEqual(response.returncode, 2); self.assertEqual(response.stdout, '')
            self.assertEqual(out.read_bytes(), saved)
            bad = pack(); bad['scenarios'][0]['overrides'] = [{'id': 'base', 'demand': 2}]
            supplied.write_text(json.dumps(bad)); refused = root/'refused.json'
            response = run('scenarios', source, supplied, '--out', refused)
            self.assertEqual(response.returncode, 2); self.assertFalse(refused.exists())
            self.assertEqual(json.loads(response.stderr)['error']['code'], 'INVALID_INPUT')
            bad = pack(); bad['scenarios'][0]['capacity'] = 1; supplied.write_text(json.dumps(bad))
            response = run('scenarios', source, supplied, '--out', root/'infeasible.json')
            self.assertEqual(response.returncode, 0, response.stderr)
            self.assertEqual(json.loads(response.stdout)['status'], 'NO_COMMON_FEASIBLE')
            document = queue(); document['revision'] = 'updated'; source.write_text(json.dumps(document))
            supplied.write_text(json.dumps(pack())); recovered = root/'recovered.json'
            response = run('scenarios', source, supplied, '--out', recovered)
            self.assertEqual(response.returncode, 0, response.stderr)
            self.assertNotEqual(json.loads(response.stdout)['input_digest'], json.loads(saved)['input_digest'])
            supplied.write_text(' ' * 65537)
            response = run('scenarios', source, supplied, '--out', root/'oversize.json')
            self.assertEqual(response.returncode, 2); self.assertEqual(response.stdout, '')
