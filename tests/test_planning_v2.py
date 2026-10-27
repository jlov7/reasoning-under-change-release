import itertools
import unittest
from unittest.mock import patch

from reasoning_under_change.planning import Budget, Job, PlanningObservation, exact_dp
from reasoning_under_change.planning_benchmark import make_task, run_benchmark


class ExactDPTests(unittest.TestCase):
    def test_plan_matches_independent_exhaustive_reward_and_ids(self):
        for seed in range(8):
            obs = PlanningObservation(make_task(seed).jobs[:7], 18)
            optimum = max(sum(j.reward for j in subset)
                          for size in range(8) for subset in itertools.combinations(obs.jobs, size)
                          if sum(j.demand for j in subset) <= obs.capacity)
            plan = exact_dp(obs, Budget(), clock=lambda: 0)
            selected = [job for job in obs.jobs if job.job_id in plan.job_ids]
            self.assertEqual(plan.reward, optimum)
            self.assertEqual(sum(job.reward for job in selected), optimum)
            self.assertLessEqual(sum(job.demand for job in selected), obs.capacity)
            self.assertEqual(len(plan.job_ids), len(set(plan.job_ids)))
            self.assertEqual(plan.stop_reason, 'EXHAUSTED')

    def test_traceback_does_not_reuse_job(self):
        obs = PlanningObservation((Job('a', 2, 3), Job('b', 3, 4)), 6)
        plan = exact_dp(obs, Budget(), clock=lambda: 0)
        self.assertEqual(plan.job_ids, ('a', 'b'))
        self.assertEqual(plan.reward, 7)

    def test_update_cap_and_timeout_preserve_feasible_fallback(self):
        obs = PlanningObservation((Job('a', 6, 20), Job('b', 5, 18),
                                   Job('c', 4, 14), Job('d', 5, 17)), 10)
        capped = exact_dp(obs, Budget(node_limit=1), clock=lambda: 0)
        self.assertEqual(capped.stop_reason, 'UPDATE_LIMIT')
        self.assertEqual(capped.expansions, 1)
        self.assertEqual(capped.reward, 34)
        times = iter((0, 2))
        timed = exact_dp(obs, Budget(timeout_ns=1), clock=lambda: next(times))
        self.assertEqual(timed.stop_reason, 'TIMEOUT')
        self.assertEqual(timed.expansions, 0)
        self.assertEqual(timed.reward, 34)

    def test_infeasibility_and_negative_bounds(self):
        obs = PlanningObservation((Job('a', 2, 5),), 1)
        plan = exact_dp(obs, Budget(), clock=lambda: 0)
        self.assertEqual((plan.job_ids, plan.reward), ((), 0))
        for kwargs in ({'node_limit': -1}, {'timeout_ns': -1}, {'node_limit': True},
                       {'timeout_ns': float('inf')}, {'node_limit': float('nan')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                exact_dp(obs, Budget(**kwargs))

    def test_planner_does_not_call_evaluator(self):
        with patch('reasoning_under_change.planning_benchmark.evaluate',
                   side_effect=AssertionError('oracle must not enter planner')):
            plan = exact_dp(PlanningObservation((Job('a', 1, 2),), 1), Budget())
        self.assertEqual((plan.job_ids, plan.reward), (('a',), 2))

    def test_v2_accounting_is_explicit_and_errors_remain(self):
        report = run_benchmark(seeds=(0,), repetitions=1, version='v2')
        self.assertEqual(len(report['rows']), 4)
        self.assertEqual(report['manifest_sha256'],
                         '0a3d99b6ff1d2db27054068de4deeac6992ed57e36b44ca216c332670d25137d')
        self.assertEqual({row['split'] for row in report['rows']}, {'exposed'})
        dp = next(row for row in report['rows'] if row['arm'] == 'exact_dp')
        self.assertEqual(dp['work_unit'], 'dp_cell_update')
        self.assertEqual(dp['gap'], 0)
        self.assertLessEqual(dp['expansions'], dp['node_limit'])
        with patch('reasoning_under_change.planning_benchmark.exact_dp',
                   side_effect=ValueError('retained failure')):
            failed = run_benchmark(seeds=(0,), repetitions=1, version='v2')
        self.assertEqual(len(failed['rows']), 4)
        self.assertEqual(sum(row['status'] == 'ERROR' for row in failed['rows']), 1)
        with self.assertRaises(ValueError):
            run_benchmark(version='unknown')
