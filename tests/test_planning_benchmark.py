import itertools
import unittest
from unittest.mock import patch

from reasoning_under_change.planning import Job, PlanningObservation
from reasoning_under_change.planning_benchmark import (
    MANIFEST,
    evaluate,
    make_task,
    run_benchmark,
)


class PlanningBenchmarkTests(unittest.TestCase):
    def test_independent_oracle_matches_exhaustive_small_cases(self):
        for seed in range(5):
            full = make_task(seed)
            obs = PlanningObservation(full.jobs[:6], 12)
            rewards = [sum(j.reward for j in subset)
                       for size in range(7) for subset in itertools.combinations(obs.jobs, size)
                       if sum(j.demand for j in subset) <= obs.capacity]
            self.assertEqual(evaluate(obs, ()).optimum, max(rewards))

    def test_evaluator_rejects_invalid_selection(self):
        obs = PlanningObservation((Job('a', 2, 3),), 1)
        for selected in (('a',), ('missing',), ('a', 'a')):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                evaluate(obs, selected)

    def test_frozen_manifest_and_complete_accounting(self):
        self.assertEqual(MANIFEST['development_seeds'], list(range(16)))
        self.assertEqual(MANIFEST['holdout_seeds'], list(range(16, 48)))
        report = run_benchmark(seeds=(0, 16), repetitions=1)
        self.assertEqual(len(report['rows']), 6)
        self.assertEqual({(r['seed'], r['arm']) for r in report['rows']},
                         set(itertools.product((0, 16), MANIFEST['arms'])))
        self.assertEqual({r['split'] for r in report['rows']}, {'development', 'holdout'})
        for row in report['rows']:
            self.assertEqual(row['status'], 'OK')
            self.assertGreaterEqual(row['gap'], 0)
            self.assertGreaterEqual(row['wall_ns'], 0)
            self.assertGreaterEqual(row['cpu_ns'], 0)
            self.assertLessEqual(row['expansions'], row['node_limit'])
        self.assertEqual(len({r['task_sha256'] for r in report['rows']}), 2)
        self.assertEqual(len(report['manifest_sha256']), 64)
        self.assertEqual(report['manifest_sha256'],
                         'b254ccc078cde56c7c1c56ad50ec3c33d998c38621bccaf4a1d23163bc5c2303')

    def test_errors_remain_in_census(self):
        with patch('reasoning_under_change.planning_benchmark.search', side_effect=ValueError('test')):
            report = run_benchmark(seeds=(0,), repetitions=1)
        self.assertEqual(len(report['rows']), 3)
        self.assertEqual(sum(r['status'] == 'ERROR' for r in report['rows']), 2)
        self.assertEqual(sum(s['errors'] for s in report['summary'].values()), 2)

    def test_benchmark_bounds_and_task_determinism(self):
        self.assertEqual(make_task(16), make_task(16))
        for kwargs in ({'seeds': ()}, {'seeds': (True,)}, {'seeds': (0, 0)},
                       {'seeds': (48,)}, {'repetitions': True}, {'repetitions': 6}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_benchmark(**kwargs)
