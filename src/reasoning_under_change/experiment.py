"""Budgeted deterministic experiments. Tick costs are simulated, NOT model latency."""
from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterable
from dataclasses import dataclass

from .core import Event, ScenarioError, World, propose

POLICIES = ('reactive', 'never', 'ttl', 'all_changes', 'relevant', 'relevant_short', 'adaptive')


@dataclass(frozen=True)
class Config:
    horizon: int = 50
    max_rounds: int = 8
    observe_ticks: int = 1
    action_ticks: int = 1
    ttl: int = 6
    delays: tuple[int, ...] = (4, 3, 7, 2)

    def __post_init__(self):
        for name in ('horizon', 'max_rounds', 'ttl'):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ScenarioError(f'{name} must be a positive integer')
        for name in ('observe_ticks', 'action_ticks'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ScenarioError(f'{name} must be a nonnegative integer')
        if (type(self.delays) is not tuple or not self.delays or len(self.delays) > 1000
                or any(type(v) is not int or v < 0 for v in self.delays)):
            raise ScenarioError('delays must be a bounded nonempty tuple of nonnegative integers')
        if self.max_rounds > 10000 or self.horizon > 1000000:
            raise ScenarioError('experiment exceeds execution bounds')


@dataclass(frozen=True)
class View:
    tick: int
    made_at: int
    expires_at: int
    changed: frozenset[str]
    read_set: frozenset[str]
    feed_complete: bool


def decide(view: View, policy: str, ttl: int) -> str:
    """Controller receives notices and its own read set, never World or future events."""
    if policy not in POLICIES:
        raise ScenarioError('unknown policy')
    if policy in ('reactive', 'never'):
        return 'ACT'
    if policy == 'ttl':
        return 'REFRESH' if view.tick - view.made_at >= ttl else 'ACT'
    if not view.feed_complete:
        return 'REFRESH'
    if view.tick > view.expires_at:
        return 'REFRESH'
    changed = bool(view.changed) if policy == 'all_changes' else bool(view.changed & view.read_set)
    return 'REFRESH' if changed else 'ACT'


def run_case(events: list[Event], policy: str, config: Config, *, deadline: int = 40) -> dict:
    if policy not in POLICIES:
        raise ScenarioError('unknown policy')
    world = World('j', deadline, 1, list(events))
    trace, costs = [], {'compute': 0, 'observation': 0, 'action': 0}
    refreshes = 0

    def spend(kind: str, requested: int) -> bool:
        allowed = min(requested, config.horizon - world.tick)
        before = world.tick
        world.advance(allowed)
        costs[kind] += allowed
        trace.append({'kind': kind, 'start': before, 'end': world.tick,
                      'requested': requested, 'completed': allowed == requested})
        return allowed == requested

    def finish(outcome: str) -> dict:
        accepted = int(outcome == 'ACCEPTED')
        return {'policy': policy, 'outcome': outcome, 'accepted': accepted,
                'not_accepted': 1 - accepted, 'invalid_actions': int(outcome.startswith('REJECT_')),
                'refreshes': refreshes, 'elapsed_ticks': world.tick,
                'compute_ticks': costs['compute'], 'observation_ticks': costs['observation'],
                'action_ticks': costs['action'], 'applied_events': len(world.applied),
                'trace': trace}

    recently_invalidated = False
    for round_id in range(config.max_rounds):
        if not spend('observation', config.observe_ticks):
            return finish('TIME_BUDGET')
        obs = world.observe()
        observed_events = len(world.applied)
        proposal = propose(obs)
        if proposal is None:
            return finish('NO_VALID_ACTION')
        delay = config.delays[round_id % len(config.delays)]
        if policy == 'reactive':
            delay = 0
        elif policy == 'relevant_short' or (policy == 'adaptive' and recently_invalidated):
            delay = min(delay, 1)
        if not spend('compute', delay):
            return finish('TIME_BUDGET')
        # Same complete, zero-lag entity-ID notification feed for every arm.
        # Values, future event times and the authoritative World are not exposed.
        changed = frozenset(event.entity for event in world.applied[observed_events:])
        view = View(world.tick, proposal.made_at, proposal.expires_at, changed,
                    frozenset(proposal.read_versions), True)
        decision = decide(view, policy, config.ttl)
        trace.append({'kind': 'decision', 'tick': world.tick, 'action': decision,
                      'changed_entities': sorted(changed), 'round': round_id})
        if decision == 'REFRESH':
            refreshes += 1
            recently_invalidated = bool(changed & view.read_set)
            continue
        if not spend('action', config.action_ticks):
            return finish('TIME_BUDGET')
        # The independent world can reject an action invalidated during effect latency.
        return finish(world.act(proposal))
    return finish('ROUND_BUDGET')


def run_grid(seeds: Iterable[int] = range(32)) -> dict:
    seeds = tuple(seeds)
    if not seeds or len(seeds) > 1000 or len(set(seeds)) != len(seeds):
        raise ScenarioError('provide 1..1000 unique seeds')
    if any(type(seed) is not int or seed < 0 for seed in seeds):
        raise ScenarioError('seeds must be nonnegative integers')
    rows, schedules = [], []
    for seed in seeds:
        for regime, horizon in [('short', 12), ('long', 36)]:
            rng = random.Random(f'events-v1:{seed}:{regime}')
            events = []
            for tick in range(2, horizon):
                if rng.random() < 0.25:
                    if rng.random() < 0.5:
                        events.append(Event(tick, 'unrelated', 'noise', None))
                    else:
                        events.append(Event(tick, 'capacity', 'capacity', rng.randrange(2)))
            delay_rng = random.Random(f'delays-v1:{seed}:{regime}')
            delays = tuple(delay_rng.randint(1, 8) for _ in range(8))
            config = Config(horizon=horizon, delays=delays)
            schedules.append({'seed': seed, 'regime': regime, 'delays': delays,
                              'events': [(e.at, e.entity, e.kind, e.value) for e in events]})
            for policy in POLICIES:
                row = run_case(events, policy, config, deadline=horizon - 1)
                row.update(seed=seed, regime=regime)
                rows.append(row)
    canonical = json.dumps(schedules, sort_keys=True, separators=(',', ':')).encode()
    summary = {}
    for policy in POLICIES:
        subset = [r for r in rows if r['policy'] == policy]
        summary[policy] = {name: sum(row[name] for row in subset) for name in (
            'accepted', 'not_accepted', 'invalid_actions', 'refreshes', 'elapsed_ticks',
            'compute_ticks', 'observation_ticks', 'action_ticks')}
        summary[policy]['cases'] = len(subset)
    return {'scope': 'Scripted simulator qualification; not measured LLM or wall-clock performance.',
            'feed_assumption': 'Same complete instantaneous entity-change feed for all arms.',
            'schedule_sha256': hashlib.sha256(canonical).hexdigest(),
            'selection_note': 'No tuning or independent holdout claim; short-delay ablation included.',
            'summary': summary, 'schedules': schedules, 'rows': rows}


def main():
    print(json.dumps(run_grid(), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
