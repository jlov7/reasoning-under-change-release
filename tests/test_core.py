import unittest

from reasoning_under_change.core import (
    Event,
    Proposal,
    ScenarioError,
    World,
    evaluate,
    propose,
    trial,
)


def world(events=None,deadline=6,capacity=1):
    return World('J',deadline,capacity,events or [])


class RealTimeTests(unittest.TestCase):
    def test_unrelated_change_does_not_refresh(self):
        w=world([Event(1,'unrelated','noise','other')]); p=propose(w.observe()); w.advance(2)
        self.assertEqual(evaluate(w,p).action,'ACT')

    def test_relevant_capacity_change_refreshes(self):
        w=world([Event(1,'capacity','capacity',0)]);p=propose(w.observe());w.advance(2)
        self.assertEqual(evaluate(w,p).reason,'relevant_change')

    def test_changed_deadline_refreshes(self):
        w=world([Event(1,'job:J','deadline',2)]);p=propose(w.observe());w.advance(2)
        self.assertEqual(evaluate(w,p).action,'REFRESH')

    def test_expiration_without_events_refreshes(self):
        w=world(deadline=1);p=propose(w.observe());w.advance(2)
        self.assertEqual(evaluate(w,p).reason,'expired')

    def test_missing_read_dependency_refreshes(self):
        w=world(); p=propose(w.observe());p=Proposal(p.job_id,p.made_at,p.expires_at,{'capacity':0})
        self.assertEqual(evaluate(w,p).reason,'incomplete_read_set')

    def test_relevant_policy_rejects_stale_action_with_new_observation(self):
        w=world([Event(1,'capacity','capacity',0)])
        r=trial(w,2,'relevant')
        self.assertEqual(r['outcome'],'NO_VALID_ACTION');self.assertEqual(r['refreshes'],1)

    def test_naive_policy_hits_stale_write_protection(self):
        w=world([Event(1,'capacity','capacity',0)])
        r=trial(w,2,'never')
        self.assertEqual(r['outcome'],'REJECT_STALE_STATE')

    def test_all_event_refresh_is_different_baseline(self):
        w=world([Event(1,'unrelated','noise','test')]);r=trial(w,2,'always')
        self.assertEqual(r['outcome'],'ACCEPTED');self.assertEqual(r['refreshes'],1)

    def test_relevant_avoids_unnecessary_refresh(self):
        w=world([Event(1,'unrelated','noise','test')]);r=trial(w,2,'relevant')
        self.assertEqual(r['outcome'],'ACCEPTED');self.assertEqual(r['refreshes'],0)

    def test_ttl_refresh(self):
        w=world(); r=trial(w,3,'ttl',ttl=2)
        self.assertEqual(r['first_decision'],'ttl_elapsed')

    def test_already_expired_refuses_even_replan(self):
        w=world(deadline=1);r=trial(w,2,'relevant')
        self.assertEqual(r['outcome'],'NO_VALID_ACTION')

    def test_zero_capacity_no_proposal(self):
        self.assertEqual(trial(world(capacity=0),2,'never')['outcome'],'NO_PROPOSAL')

    def test_write_is_exactly_once_for_closed_job(self):
        w=world();p=propose(w.observe());self.assertEqual(w.act(p),'ACCEPTED')
        self.assertEqual(w.act(p),'REJECT_STALE_STATE');self.assertEqual(len(w.accepted),1)

    def test_sorted_event_requirement(self):
        with self.assertRaises(ScenarioError):world([Event(2,'unrelated','noise','x'),Event(1,'unrelated','noise','y')])

    def test_invalid_delay_rejected(self):
        with self.assertRaises(ScenarioError):world().advance(-1)

    def test_invalid_negative_capacity_event_rejected(self):
        with self.assertRaises(ScenarioError):world([Event(1,'capacity','capacity',-1)])

    def test_bad_event_entity_rejected(self):
        with self.assertRaises(ScenarioError):world([Event(1,'unrelated','deadline',3)])

    def test_job_closed_change_refreshes(self):
        w=world([Event(1,'job:J','close',True)]);p=propose(w.observe());w.advance(2)
        self.assertEqual(evaluate(w,p).action,'REFRESH')

    def test_unknown_policy_refused(self):
        w=world();p=propose(w.observe())
        with self.assertRaises(ScenarioError):evaluate(w,p,'dream')

    def test_clock_cannot_move_backwards(self):
        w=world();w.advance(5)
        with self.assertRaises(ScenarioError):w.advance(-1)


if __name__=='__main__':unittest.main()
