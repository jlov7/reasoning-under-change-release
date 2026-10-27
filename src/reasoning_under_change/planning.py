"""Bounded classical planning from an immutable, authored static observation."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from time import perf_counter_ns


def _integer(value: int, lower: int, upper: int, name: str):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f'{name} must be an integer in {lower}..{upper}')


@dataclass(frozen=True)
class Job:
    job_id: str
    demand: int
    reward: int

    def __post_init__(self):
        if type(self.job_id) is not str or not 1 <= len(self.job_id) <= 64:
            raise ValueError('job_id must be a string of length 1..64')
        _integer(self.demand, 1, 128, 'demand')
        _integer(self.reward, 1, 10000, 'reward')


@dataclass(frozen=True)
class PlanningObservation:
    jobs: tuple[Job, ...]
    capacity: int

    def __post_init__(self):
        _integer(self.capacity, 0, 128, 'capacity')
        if (type(self.jobs) is not tuple or len(self.jobs) > 20
                or any(type(job) is not Job for job in self.jobs)):
            raise ValueError('jobs must be a tuple of at most 20 Job objects')
        if len({job.job_id for job in self.jobs}) != len(self.jobs):
            raise ValueError('job IDs must be unique')


@dataclass(frozen=True)
class Budget:
    node_limit: int = 8192
    timeout_ns: int = 20000000

    def __post_init__(self):
        _integer(self.node_limit, 1, 65536, 'node_limit')
        _integer(self.timeout_ns, 1, 1000000000, 'timeout_ns')


@dataclass(frozen=True)
class Plan:
    job_ids: tuple[str, ...]
    reward: int
    expansions: int
    stop_reason: str


def _incumbent(obs: PlanningObservation) -> tuple[tuple[str, ...], int]:
    orders = (
        sorted(obs.jobs, key=lambda j: (-Fraction(j.reward, j.demand), j.job_id)),
        sorted(obs.jobs, key=lambda j: (-j.reward, j.job_id)),
        sorted(obs.jobs, key=lambda j: (j.demand, j.job_id)),
    )
    best_ids, best_reward = (), 0
    for jobs in orders:
        selected, used, reward = [], 0, 0
        for job in jobs:
            if used + job.demand <= obs.capacity:
                selected.append(job.job_id)
                used += job.demand
                reward += job.reward
        if reward > best_reward:
            best_ids, best_reward = tuple(sorted(selected)), reward
    return best_ids, best_reward


def greedy(obs: PlanningObservation, budget: Budget, *,
           clock: Callable[[], int] = perf_counter_ns) -> Plan:
    start = clock()
    selected, reward = _incumbent(obs)
    reason = 'TIMEOUT' if clock() - start >= budget.timeout_ns else 'GREEDY'
    return Plan(selected, reward, 0, reason)


def search(obs: PlanningObservation, budget: Budget, *,
           clock: Callable[[], int] = perf_counter_ns) -> Plan:
    """Anytime subset search; no evaluator, optimum, or future state is accepted."""
    start = clock()
    selected, best_reward = _incumbent(obs)
    suffix = [0] * (len(obs.jobs) + 1)
    for index in range(len(obs.jobs) - 1, -1, -1):
        suffix[index] = suffix[index + 1] + obs.jobs[index].reward
    stack = [(0, 0, 0, ())]
    expansions = 0
    reason = 'EXHAUSTED'
    while stack:
        if expansions >= budget.node_limit:
            reason = 'NODE_LIMIT'
            break
        if clock() - start >= budget.timeout_ns:
            reason = 'TIMEOUT'
            break
        index, used, reward, ids = stack.pop()
        expansions += 1
        if used > obs.capacity:
            continue
        if reward > best_reward:
            selected, best_reward = tuple(sorted(ids)), reward
        if index == len(obs.jobs) or reward + suffix[index] <= best_reward:
            continue
        job = obs.jobs[index]
        stack.append((index + 1, used, reward, ids))
        stack.append((index + 1, used + job.demand, reward + job.reward,
                      ids + (job.job_id,)))
    return Plan(selected, best_reward, expansions, reason)


def exact_dp(obs: PlanningObservation, budget: Budget, *,
             clock: Callable[[], int] = perf_counter_ns) -> Plan:
    """Plan from public inputs; the work cap counts candidate DP cell updates."""
    start = clock()
    selected, best_reward = _incumbent(obs)
    values = [0] * (obs.capacity + 1)
    masks = [0] * (obs.capacity + 1)
    best_mask = None
    updates = 0

    def finish(reason: str) -> Plan:
        ids = selected if best_mask is None else tuple(sorted(
            job.job_id for index, job in enumerate(obs.jobs) if best_mask & (1 << index)))
        return Plan(ids, best_reward, updates, reason)

    for index, job in enumerate(obs.jobs):
        for capacity in range(obs.capacity, job.demand - 1, -1):
            if updates >= budget.node_limit:
                return finish('UPDATE_LIMIT')
            if clock() - start >= budget.timeout_ns:
                return finish('TIMEOUT')
            candidate = values[capacity - job.demand] + job.reward
            updates += 1
            if candidate > values[capacity]:
                values[capacity] = candidate
                masks[capacity] = masks[capacity - job.demand] | (1 << index)
            if values[capacity] > best_reward:
                best_reward, best_mask = values[capacity], masks[capacity]
    return finish('EXHAUSTED')
