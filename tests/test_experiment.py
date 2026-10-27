import dataclasses
import importlib.util
import unittest

from reasoning_under_change.core import Event, ScenarioError


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('reasoning_under_change.experiment'),
                             'Missing budgeted experiment runner')
        from reasoning_under_change.experiment import Config, View, decide, run_case
        self.run_case, self.Config, self.View, self.decide = run_case, Config, View, decide

    def test_no_future_state_in_controller_view(self):
        fields = {f.name for f in dataclasses.fields(self.View)}
        self.assertEqual(fields, {'tick', 'made_at', 'expires_at', 'changed', 'read_set',
                                 'feed_complete'})
        view = self.View(4, 0, 20, frozenset({'unrelated'}), frozenset({'job:j', 'capacity'}), True)
        self.assertEqual(self.decide(view, 'relevant', 6), 'ACT')
        self.assertEqual(self.decide(view, 'all_changes', 6), 'REFRESH')

    def test_refresh_and_replanning_have_nonzero_cost(self):
        events = [Event(2, 'capacity', 'capacity', 0), Event(4, 'capacity', 'capacity', 1)]
        result = self.run_case(events, 'relevant', self.Config(delays=(4,), observe_ticks=1))
        self.assertEqual(result['refreshes'], 1)
        self.assertEqual(result['compute_ticks'], 8)
        self.assertEqual(result['observation_ticks'], 2)
        self.assertEqual(result['outcome'], 'ACCEPTED')
        self.assertEqual(result['elapsed_ticks'], 11)

    def test_irrelevant_change_does_not_trigger_relevant_refresh(self):
        r = self.run_case([Event(2, 'unrelated', 'noise', None)], 'relevant', self.Config())
        self.assertEqual(r['refreshes'], 0)
        self.assertEqual(r['outcome'], 'ACCEPTED')

    def test_incomplete_feed_forces_refresh(self):
        view = self.View(3, 0, 20, frozenset(), frozenset({'capacity'}), False)
        self.assertEqual(self.decide(view, 'relevant', 6), 'REFRESH')

    def test_ttl_can_starve_and_stays_in_denominator(self):
        r = self.run_case([], 'ttl', self.Config(ttl=1, max_rounds=3))
        self.assertEqual(r['outcome'], 'ROUND_BUDGET')
        self.assertEqual(r['accepted'], 0)
        self.assertEqual(r['refreshes'], 3)

    def test_horizon_cannot_be_exceeded(self):
        r = self.run_case([], 'never', self.Config(horizon=3, delays=(20,)))
        self.assertEqual(r['outcome'], 'TIME_BUDGET')
        self.assertEqual(r['elapsed_ticks'], 3)
        self.assertEqual(r['compute_ticks'] + r['observation_ticks'] + r['action_ticks'], 3)

    def test_change_between_decision_and_effect_remains_visible(self):
        r = self.run_case([Event(6, 'job:j', 'close', True)], 'relevant',
                          self.Config(delays=(4,), action_ticks=2))
        self.assertEqual(r['outcome'], 'REJECT_STALE_STATE')
        self.assertEqual(r['invalid_actions'], 1)

    def test_events_unchanged_and_all_results_deterministic(self):
        events = [Event(2, 'capacity', 'capacity', 2)]
        before = list(events)
        a = self.run_case(events, 'adaptive', self.Config())
        b = self.run_case(events, 'adaptive', self.Config())
        self.assertEqual(a, b)
        self.assertEqual(events, before)

    def test_bad_budget_values_rejected(self):
        for kwargs in [{'horizon': True}, {'delays': ()}, {'observe_ticks': -1},
                       {'delays': (True,)}, {'max_rounds': 0}, {'ttl': 0}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ScenarioError):
                self.Config(**kwargs)

    def test_reactive_is_a_real_strong_baseline(self):
        r = self.run_case([], 'reactive', self.Config())
        self.assertEqual(r['compute_ticks'], 0)
        self.assertEqual(r['outcome'], 'ACCEPTED')

    def test_complete_grid_preserves_all_seeds_and_arms(self):
        from reasoning_under_change.experiment import run_grid
        report = run_grid(seeds=range(4))
        self.assertEqual(len(report['rows']), 56)
        self.assertEqual(len({(r['seed'], r['regime'], r['policy']) for r in report['rows']}), 56)
        self.assertTrue(all(r['accepted'] + r['not_accepted'] == 1 for r in report['rows']))
