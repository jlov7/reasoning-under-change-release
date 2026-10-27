"""Offline complete-record verification. No planner timing run or model call is made."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from .core import ScenarioError
from .experiment import POLICIES, run_grid
from .input import Parser, error_report, integer, load_json
from .planning_benchmark import MANIFEST, MANIFEST_V2, _digest, evaluate, make_task


def _same(actual, expected, message):
    if _digest(actual) != _digest(expected):
        raise ScenarioError(message)


def _grid(document):
    expected = run_grid()
    for name in ('schedules', 'schedule_sha256', 'scope', 'feed_assumption', 'selection_note'):
        _same(document[name], expected[name], f'inconsistent grid {name}')
    if type(document['rows']) is not list or len(document['rows']) != 448:
        raise ScenarioError('grid requires all 448 rows')
    indexed = {}
    expected_rows = {(row['seed'], row['regime'], row['policy']): row for row in expected['rows']}
    for row in document['rows']:
        if type(row) is not dict:
            raise ScenarioError('invalid grid row')
        integer(row['seed'], 0, 31, 'seed')
        key = row['seed'], row['regime'], row['policy']
        if key in indexed or key not in expected_rows:
            raise ScenarioError('duplicate or unknown grid row')
        _same(row, expected_rows[key], 'grid row does not reproduce its scripted schedule')
        indexed[key] = row
    summary = {}
    for policy in POLICIES:
        subset = [row for row in indexed.values() if row['policy'] == policy]
        summary[policy] = {name: sum(row[name] for row in subset) for name in (
            'accepted', 'not_accepted', 'invalid_actions', 'refreshes', 'elapsed_ticks',
            'compute_ticks', 'observation_ticks', 'action_ticks')}
        summary[policy]['cases'] = len(subset)
    return summary


def _planning(document):
    if type(document['manifest']) is not dict:
        raise ScenarioError('planning manifest must be a JSON object')
    version = 'v2' if document['manifest'].get('version') == 'v2-exposed-dp-diagnostic' else 'v1'
    manifest = MANIFEST_V2 if version == 'v2' else MANIFEST
    _same(document['manifest'], manifest, 'unknown or changed planning manifest')
    _same(document['manifest_sha256'], _digest(manifest), 'manifest digest mismatch')
    _same(document['executed_seeds'], list(range(48)), 'requires all 48 tasks')
    _same(document['executed_repetitions'], 5, 'requires all five repetitions')
    if document['is_frozen_run'] is not True:
        raise ScenarioError('record is not a complete frozen-protocol run')
    tasks = [{'seed': seed, 'observation': asdict(make_task(seed))} for seed in range(48)]
    _same(document['tasks'], tasks, 'tasks differ from the frozen inputs')
    if version == 'v2':
        _same(document['tasks_sha256'], _digest(tasks), 'task collection digest mismatch')
    environment = document['environment']
    if (type(environment) is not dict or set(environment) != {'python', 'platform', 'machine', 'wall_clock', 'cpu_clock'}
            or any(type(environment[key]) is not str or not 1 <= len(environment[key]) <= 256
                   for key in ('python', 'platform', 'machine'))):
        raise ScenarioError('missing or invalid host timing environment')
    for name in ('wall_clock', 'cpu_clock'):
        clock = environment[name]
        if (type(clock) is not dict or set(clock) != {'adjustable', 'implementation', 'monotonic', 'resolution'}
                or type(clock['adjustable']) is not bool or type(clock['monotonic']) is not bool
                or type(clock['implementation']) is not str
                or type(clock['resolution']) not in (int, float)
                or not math.isfinite(clock['resolution']) or clock['resolution'] <= 0):
            raise ScenarioError('missing or invalid timing clock')
    expected_scope = ('Exposed-task baseline diagnostics; not fresh holdout or real-time results.'
                      if version == 'v2' else
                      'Authored static CPU planning qualification; not model or real-time results.')
    expected_budget = ('Common ceilings, not matched compute: DP cell updates and search '
                       'nodes are different operations; short search is an ablation.'
                       if version == 'v2' else
                       'Common ceilings, not matched consumed compute; short search is an ablation.')
    _same(document['scope'], expected_scope, 'incorrect planning claim boundary')
    _same(document['budget_note'], expected_budget, 'incorrect planning budget qualification')
    rows = document['rows']
    if type(rows) is not list or len(rows) != 48 * 5 * len(manifest['arms']):
        raise ScenarioError('incomplete planning row census')
    seen = set()
    base = {'seed', 'split', 'repetition', 'arm', 'task_sha256', 'node_limit', 'timeout_ns', 'status', 'wall_ns', 'cpu_ns'}
    if version == 'v2':
        base |= {'work_unit'}
    for row in rows:
        if type(row) is not dict or not base <= row.keys():
            raise ScenarioError('missing planning row fields')
        integer(row['seed'], 0, 47, 'seed')
        integer(row['repetition'], 0, 4, 'repetition')
        arm = row['arm']
        if arm not in manifest['arms']:
            raise ScenarioError('unknown planning arm')
        key = row['seed'], row['repetition'], arm
        if key in seen:
            raise ScenarioError('duplicate planning row')
        seen.add(key)
        split = 'exposed' if version == 'v2' else 'development' if row['seed'] < 16 else 'holdout'
        _same(row['split'], split, 'incorrect task exposure/split label')
        _same(row['task_sha256'], _digest(asdict(make_task(row['seed']))), 'row task digest mismatch')
        _same(row['node_limit'], manifest['arms'][arm], 'row work ceiling mismatch')
        _same(row['timeout_ns'], manifest['timeout_ns'], 'row timeout mismatch')
        for field in ('wall_ns', 'cpu_ns'):
            integer(row[field], 0, 10**18, field)
        if version == 'v2':
            unit = 'dp_cell_update' if arm == 'exact_dp' else 'none' if arm == 'greedy' else 'search_node'
            _same(row['work_unit'], unit, 'incorrect work unit')
        if row['status'] == 'ERROR':
            if (set(row) != base | {'error_type'} or type(row['error_type']) is not str
                    or not 1 <= len(row['error_type']) <= 128):
                raise ScenarioError('error rows must retain status/timing without claimed quality')
            continue
        if row['status'] != 'OK' or set(row) != base | {'job_ids', 'reward', 'optimum', 'gap', 'expansions', 'stop_reason'}:
            raise ScenarioError('invalid planning result status or fields')
        if type(row['job_ids']) is not list or any(type(value) is not str for value in row['job_ids']):
            raise ScenarioError('invalid selected job IDs')
        observation = make_task(row['seed'])
        measured = asdict(evaluate(observation, tuple(row['job_ids'])))
        _same({name: row[name] for name in measured}, measured, 'inconsistent realized reward/optimum/gap')
        integer(row['expansions'], 0, row['node_limit'], 'expansions')
        reasons = {'GREEDY', 'TIMEOUT'} if arm == 'greedy' else {'EXHAUSTED', 'TIMEOUT', 'UPDATE_LIMIT' if arm == 'exact_dp' else 'NODE_LIMIT'}
        if row['stop_reason'] not in reasons or (arm == 'greedy' and row['expansions'] != 0):
            raise ScenarioError('invalid stop reason or work count')
        if row['stop_reason'] in ('NODE_LIMIT', 'UPDATE_LIMIT') and row['expansions'] != row['node_limit']:
            raise ScenarioError('work-limit stop requires the configured work ceiling')
        if row['stop_reason'] == 'EXHAUSTED' and (row['gap'] != 0 or row['expansions'] == 0):
            raise ScenarioError('exhausted exact solver must be optimal with completed work')
        if arm == 'exact_dp':
            updates = sum(max(0, observation.capacity - job.demand + 1) for job in observation.jobs)
            if (row['expansions'] > updates
                    or (row['stop_reason'] == 'EXHAUSTED' and row['expansions'] != updates)):
                raise ScenarioError('DP work count disagrees with the task cell-update count')
    summary = {}
    for split in (('exposed',) if version == 'v2' else ('development', 'holdout')):
        for arm in manifest['arms']:
            subset = [row for row in rows if row['split'] == split and row['arm'] == arm]
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
                'wall_p95_ns': durations[math.ceil(.95 * len(durations)) - 1],
                'cpu_median_ns': statistics.median(row['cpu_ns'] for row in subset),
            }
    return summary


def verify(document):
    """Check a complete grid/v1/v2 record and derive tables from every retained row.

    A supplied summary is checked, never trusted. It may be omitted to obtain a
    derived summary. Partial seed/repetition runs are intentionally rejected.
    """
    try:
        if type(document) is not dict:
            raise ScenarioError('record must be a JSON object')
        summary = _planning(document) if 'manifest' in document else _grid(document)
        if 'summary' in document:
            _same(document['summary'], summary, 'recorded summary disagrees with retained rows')
        return {'rows': len(document['rows']), 'summary': summary}
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        if isinstance(error, ScenarioError):
            raise
        raise ScenarioError('incomplete or inconsistent result record') from None


def main():
    parser = Parser(description='Verify complete offline grid/planning records and derive all tables')
    parser.add_argument('record', nargs='?', type=Path, help='JSON record (at most 8 MiB); default: all bundled records')
    args = parser.parse_args()
    try:
        if args.record:
            documents = {args.record.name: args.record}
        else:
            resource = files('reasoning_under_change').joinpath('data')
            manifest = load_json(resource.joinpath('SHA256.json'), 65536)
            expected_resources = {'changing-grid.json', 'planning-v1.json', 'planning-v2.json',
                                  'planning-inputs.json', 'supply_change.json'}
            if type(manifest) is not dict or set(manifest) != expected_resources:
                raise ScenarioError('bundled resource manifest is incomplete or unknown')
            for name, digest in manifest.items():
                _, data = load_json(resource.joinpath(name), 8388608, with_bytes=True)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ScenarioError('bundled resource byte hash mismatch')
            documents = {name: resource.joinpath(name + '.json') for name in ('changing-grid', 'planning-v1', 'planning-v2')}
        reports = {}
        for name, path in documents.items():
            document, data = load_json(path, 8388608, with_bytes=True)
            reports[name] = verify(document)
            reports[name]['file_sha256'] = hashlib.sha256(data).hexdigest()
    except ScenarioError as error:
        return error_report(str(error))
    print(json.dumps(reports, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
