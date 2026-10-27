import dataclasses
import unittest

from reasoning_under_change.planning import (
    Budget,
    Job,
    PlanningObservation,
    greedy,
    search,
)


def observation():
    # The greedy portfolio gets 34; the optimum is jobs b and d, worth 35.
    return PlanningObservation((Job('a', 6, 20), Job('b', 5, 18),
                                Job('c', 4, 14), Job('d', 5, 17)), 10)


class PlanningTests(unittest.TestCase):
    def test_real_search_improves_greedy_trap(self):
        obs = observation()
        self.assertEqual(greedy(obs, Budget()).reward, 34)
        result = search(obs, Budget())
        self.assertEqual(result.reward, 35)
        self.assertEqual(set(result.job_ids), {'b', 'd'})
        self.assertEqual(result.stop_reason, 'EXHAUSTED')
        self.assertGreater(result.expansions, 0)

    def test_node_cap_returns_feasible_incumbent(self):
        obs = observation()
        result = search(obs, Budget(node_limit=1))
        self.assertEqual(result.stop_reason, 'NODE_LIMIT')
        self.assertEqual(result.expansions, 1)
        self.assertGreaterEqual(result.reward, greedy(obs, Budget()).reward)
        self.assertLessEqual(sum(j.demand for j in obs.jobs if j.job_id in result.job_ids), 10)

    def test_timeout_returns_feasible_fallback(self):
        times = iter((0, 2, 2, 2))
        result = search(observation(), Budget(timeout_ns=1), clock=lambda: next(times))
        self.assertEqual(result.stop_reason, 'TIMEOUT')
        self.assertEqual(result.expansions, 0)
        self.assertEqual(result.reward, 34)

    def test_more_nodes_do_not_lower_quality_with_constant_clock(self):
        rewards = [search(observation(), Budget(node_limit=n), clock=lambda: 0).reward
                   for n in (1, 4, 16, 128)]
        self.assertEqual(rewards, sorted(rewards))

    def test_empty_infeasible_inputs_have_feasible_fallback(self):
        for obs in (PlanningObservation((), 0),
                    PlanningObservation((Job('a', 4, 5),), 0)):
            result = search(obs, Budget())
            self.assertEqual(result.job_ids, ())
            self.assertEqual(result.reward, 0)

    def test_input_is_immutable_and_node_limited_result_deterministic(self):
        obs = observation()
        before = dataclasses.asdict(obs)
        first = search(obs, Budget(node_limit=4), clock=lambda: 0)
        self.assertEqual(first, search(obs, Budget(node_limit=4), clock=lambda: 0))
        self.assertEqual(dataclasses.asdict(obs), before)
        self.assertEqual({f.name for f in dataclasses.fields(obs)}, {'jobs', 'capacity'})
        with self.assertRaises(dataclasses.FrozenInstanceError):
            obs.capacity = 1

    def test_invalid_and_unbounded_inputs_rejected(self):
        for kwargs in ({'node_limit': True}, {'node_limit': 0}, {'node_limit': 65537},
                       {'timeout_ns': True}, {'timeout_ns': float('nan')},
                       {'timeout_ns': float('inf')}, {'timeout_ns': 0},
                       {'timeout_ns': 1000000001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Budget(**kwargs)
        for args in ((True, 2), (1, True), (0, 1), (1, float('inf')), (129, 1)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                Job('a', *args)
        for jobs, capacity in (([Job('a', 1, 2)], 1), ((), True), ((), 129),
                               ((Job('a', 1, 2),) * 21, 1),
                               ((Job('a', 1, 2), Job('a', 2, 3)), 3)):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                PlanningObservation(jobs, capacity)
