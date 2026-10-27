"""Static CPU qualification, separate from the frozen scripted change experiments."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import random
import statistics
from dataclasses import asdict, dataclass
from time import get_clock_info, perf_counter_ns, process_time_ns

from .planning import Budget, Job, PlanningObservation, exact_dp, greedy, search

MANIFEST = {
    'arms': {'greedy': 8192, 'search_full': 8192, 'search_short': 128},
    'capacity': 64,
    'development_seeds': list(range(16)),
    'generator': 'cpu-planning-v1',
    'holdout_seeds': list(range(16, 48)),
    'jobs': 16,
    'repetitions': 5,
    'timeout_ns': 20000000,
}

MANIFEST_V2 = {
    'arms': {'exact_dp': 8192, 'greedy': 8192, 'search_full': 8192, 'search_short': 128},
    'capacity': 64,
    'exposed_seeds': list(range(48)),
    'generator': 'cpu-planning-v1',
    'jobs': 16,
    'repetitions': 5,
    'timeout_ns': 20000000,
    'version': 'v2-exposed-dp-diagnostic',
}


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def make_task(seed: int) -> PlanningObservation:
    if type(seed) is not int or not 0 <= seed < 48:
        raise ValueError('seed must be an integer in 0..47')
    rng = random.Random(f'cpu-planning-v1:{seed}')
    return PlanningObservation(tuple(Job(f'j{index:02}', rng.randint(1, 32), rng.randint(1, 100))
                                     for index in range(MANIFEST['jobs'])), MANIFEST['capacity'])


@dataclass(frozen=True)
class Evaluation:
    reward: int
    optimum: int
    gap: int


def evaluate(obs: PlanningObservation, selected: tuple[str, ...]) -> Evaluation:
    """Evaluator-only exact dynamic program. Its output never enters a planner."""
    jobs = {job.job_id: job for job in obs.jobs}
    if len(set(selected)) != len(selected) or any(job_id not in jobs for job_id in selected):
        raise ValueError('invalid or duplicate selected job')
    if sum(jobs[job_id].demand for job_id in selected) > obs.capacity:
        raise ValueError('selected jobs exceed capacity')
    reward = sum(jobs[job_id].reward for job_id in selected)
    optimum = [0] * (obs.capacity + 1)
    for job in obs.jobs:
        for capacity in range(obs.capacity, job.demand - 1, -1):
            optimum[capacity] = max(optimum[capacity], optimum[capacity - job.demand] + job.reward)
    return Evaluation(reward, optimum[-1], optimum[-1] - reward)


def run_benchmark(*, seeds: tuple[int, ...] = tuple(range(48)), repetitions: int = 5,
                  version: str = 'v1') -> dict:
    if version not in ('v1', 'v2'):
        raise ValueError('version must be v1 or v2')
    manifest = MANIFEST if version == 'v1' else MANIFEST_V2
    if (type(seeds) is not tuple or not seeds or len(seeds) > 48
            or any(type(seed) is not int or not 0 <= seed < 48 for seed in seeds)
            or len(set(seeds)) != len(seeds)):
        raise ValueError('provide 1..48 unique frozen seeds')
    if type(repetitions) is not int or not 1 <= repetitions <= manifest['repetitions']:
        raise ValueError('repetitions must be an integer in 1..5')
    rows = []
    arms = tuple(manifest['arms'])
    for seed in seeds:
        obs = make_task(seed)
        split = 'development' if seed in MANIFEST['development_seeds'] else 'holdout'
        if version == 'v2':
            split = 'exposed'
        for repetition in range(repetitions):
            offset = (seed + repetition) % len(arms)
            for arm in arms[offset:] + arms[:offset]:
                budget = Budget(manifest['arms'][arm], manifest['timeout_ns'])
                row = {'seed': seed, 'split': split, 'repetition': repetition, 'arm': arm,
                       'task_sha256': _digest(asdict(obs)), 'node_limit': budget.node_limit,
                       'timeout_ns': budget.timeout_ns}
                if version == 'v2':
                    row['work_unit'] = ('dp_cell_update' if arm == 'exact_dp' else
                                        'none' if arm == 'greedy' else 'search_node')
                planner = exact_dp if arm == 'exact_dp' else greedy if arm == 'greedy' else search
                wall_start, cpu_start = perf_counter_ns(), process_time_ns()
                try:
                    plan = planner(obs, budget)
                except Exception as error:  # noqa: BLE001 - retain each failed attempt as an ERROR row
                    row.update(status='ERROR', error_type=type(error).__name__)
                    plan = None
                row.update(wall_ns=perf_counter_ns() - wall_start,
                           cpu_ns=process_time_ns() - cpu_start)
                if plan is not None:
                    try:
                        evaluation = evaluate(obs, plan.job_ids)
                        if plan.reward != evaluation.reward:
                            raise ValueError('planner reward disagrees with evaluator')
                        row.update(status='OK', **(asdict(plan) | asdict(evaluation)))
                    except Exception as error:  # noqa: BLE001 - retain each failed attempt as an ERROR row
                        row.update(status='ERROR', error_type=type(error).__name__)
                rows.append(row)
    summary = {}
    for split in (('development', 'holdout') if version == 'v1' else ('exposed',)):
        for arm in arms:
            subset = [row for row in rows if row['split'] == split and row['arm'] == arm]
            if not subset:
                continue
            valid = [row for row in subset if row['status'] == 'OK']
            durations = sorted(row['wall_ns'] for row in subset)
            summary[f'{split}/{arm}'] = {
                'rows': len(subset), 'tasks': len({row['seed'] for row in subset}),
                'errors': len(subset) - len(valid),
                'timeouts': sum(row.get('stop_reason') == 'TIMEOUT' for row in subset),
                'wall_overruns': sum(row['wall_ns'] > row['timeout_ns'] for row in subset),
                'reward_sum': sum(row['reward'] for row in valid),
                'gap_sum': sum(row['gap'] for row in valid),
                'wall_median_ns': statistics.median(durations),
                'wall_p95_ns': durations[math.ceil(0.95 * len(durations)) - 1],
                'cpu_median_ns': statistics.median(row['cpu_ns'] for row in subset),
            }
    report = {
        'scope': 'Authored static CPU planning qualification; not model or real-time results.',
        'budget_note': 'Common ceilings, not matched consumed compute; short search is an ablation.',
        'manifest': manifest, 'manifest_sha256': _digest(manifest),
        'is_frozen_run': seeds == tuple(range(48)) and repetitions == manifest['repetitions'],
        'executed_seeds': seeds, 'executed_repetitions': repetitions,
        'environment': {'python': platform.python_version(), 'platform': platform.platform(),
                        'machine': platform.machine(),
                        'wall_clock': vars(get_clock_info('perf_counter')),
                        'cpu_clock': vars(get_clock_info('process_time'))},
        'tasks': [{'seed': seed, 'observation': asdict(make_task(seed))} for seed in seeds],
        'summary': summary, 'rows': rows,
    }
    if version == 'v2':
        report['scope'] = 'Exposed-task baseline diagnostics; not fresh holdout or real-time results.'
        report['budget_note'] = ('Common ceilings, not matched compute: DP cell updates and search '
                                 'nodes are different operations; short search is an ablation.')
        report['tasks_sha256'] = _digest(report['tasks'])
    return report


def main():
    parser = argparse.ArgumentParser(description='Static CPU planning qualification')
    parser.add_argument('--v2', action='store_true', help='run exposed-task exact-DP diagnostics')
    args = parser.parse_args()
    print(json.dumps(run_benchmark(version='v2' if args.v2 else 'v1'), indent=2, sort_keys=True))

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
