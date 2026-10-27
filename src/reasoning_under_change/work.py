"""Offline bounded work selection. Plans never execute work."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from contextlib import suppress
from pathlib import Path
from tempfile import mkstemp
from time import monotonic_ns

from . import __version__
from .core import ScenarioError
from .input import Parser, error_report, integer, load_json
from .planning import Budget, Job, PlanningObservation, exact_dp, greedy

IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
CLAIM = 'User-supplied work; exact static selection, no execution or real-time guarantee.'
_SEARCH_NODE_LIMIT = 2097151
_SCENARIO_NODE_LIMIT = 2097152


def _identifier(value):
    if type(value) is not str or not IDENTIFIER.fullmatch(value):
        raise ScenarioError('IDs and revisions require 1..64 ASCII letters, digits, dots, underscores or hyphens; start with a letter or digit')
    return value


def _ids(value):
    if type(value) is not list or len(value) > 20:
        raise ScenarioError('completed_ids must be a list of at most 20 unique task IDs')
    for item in value:
        _identifier(item)
    if len(set(value)) != len(value):
        raise ScenarioError('completed_ids must be unique')
    return sorted(value)


def _extended(document):
    return _named(document) or any('requires' in task or 'mandatory' in task for task in document['tasks'])


def _named(document):
    return 'resource_limits' in document


def _resources(current, ids):
    if not _named(current):
        return {}
    used = {name: sum(task.get('resources', {}).get(name, 0) for task in current['tasks']
                      if task['id'] in ids) for name in current['resource_limits']}
    return {'resources_used': used,
            'resource_slack': {name: limit - used[name]
                               for name, limit in current['resource_limits'].items()}}


def _order(tasks):
    """ID-stable topological order; dependencies have already been validated."""
    remaining = {task['id']: set(task.get('requires', [])) for task in tasks}
    ordered = []
    while remaining:
        ready = sorted(task_id for task_id, requires in remaining.items() if not requires)
        if not ready:
            raise ScenarioError('task prerequisites contain a cycle')
        task_id = ready[0]
        ordered.append(task_id)
        del remaining[task_id]
        for requires in remaining.values():
            requires.discard(task_id)
    return ordered


def _input(document):
    required = {'revision', 'capacity', 'tasks', 'completed_ids'}
    if (type(document) is not dict or not required <= document.keys()
            or document.keys() - required - {'metadata', 'resource_limits'}):
        raise ScenarioError('work input requires revision, capacity, tasks, completed_ids and optional metadata, resource_limits only')
    _identifier(document['revision'])
    integer(document['capacity'], 0, 128, 'capacity')
    tasks = document['tasks']
    if type(tasks) is not list or len(tasks) > 20:
        raise ScenarioError('tasks must be a list of at most 20 tasks')
    limits = document.get('resource_limits', {})
    if type(limits) is not dict or len(limits) > 4:
        raise ScenarioError('resource_limits must be an object with at most 4 named limits')
    for name, limit in limits.items():
        _identifier(name)
        integer(limit, 0, 128, 'resource limit')
    extended = _named(document) or any(
        type(task) is dict and ('requires' in task or 'mandatory' in task) for task in tasks)
    for task in tasks:
        required = {'id', 'demand', 'value'}
        if (type(task) is not dict or not required <= task.keys()
                or task.keys() - required - {'requires', 'mandatory', 'resources'}):
            raise ScenarioError('each task requires id, demand, value and optional requires, mandatory, resources only')
        _identifier(task['id'])
        integer(task['demand'], 1, 128, 'demand')
        integer(task['value'], 0 if extended else 1, 10000, 'value')
        resources = task.get('resources', {})
        if type(resources) is not dict:
            raise ScenarioError('task resources must be an object of declared resource names')
        if ('resources' in task and not _named(document)) or resources.keys() - limits.keys():
            raise ScenarioError('task resources require names declared in resource_limits')
        for usage in resources.values():
            integer(usage, 0, 128, 'task resource usage')
        if 'mandatory' in task and type(task['mandatory']) is not bool:
            raise ScenarioError('mandatory must be a boolean')
        requires = task.get('requires', [])
        if type(requires) is not list or len(requires) > 19:
            raise ScenarioError('requires must be a list of at most 19 unique prerequisite IDs')
        for task_id in requires:
            _identifier(task_id)
        if len(set(requires)) != len(requires) or task['id'] in requires:
            raise ScenarioError('prerequisites must be unique and cannot include the task itself')
    if len({task['id'] for task in tasks}) != len(tasks):
        raise ScenarioError('task IDs must be unique')
    completed = _ids(document['completed_ids'])
    by_id = {task['id']: task for task in tasks}
    if any(not set(task.get('requires', [])) <= by_id.keys() for task in tasks):
        raise ScenarioError('prerequisite IDs must exist in tasks')
    if extended:
        _order(tasks)
    if not set(completed) <= by_id.keys():
        raise ScenarioError('completed IDs must exist in tasks')
    if any(not set(by_id[item].get('requires', [])) <= set(completed) for item in completed):
        raise ScenarioError('completed work must include every completed task prerequisite')
    if sum(by_id[item]['demand'] for item in completed) > document['capacity']:
        raise ScenarioError('completed work exceeds total capacity; increase capacity or use a new accounting window')
    if any(sum(by_id[item].get('resources', {}).get(name, 0) for item in completed) > limit
           for name, limit in limits.items()):
        raise ScenarioError('completed work exceeds a named resource limit; increase it or use a new accounting window')
    current = {'revision': document['revision'], 'capacity': document['capacity'],
               'tasks': [dict(task) for task in sorted(tasks, key=lambda task: task['id'])],
               'completed_ids': completed}
    if _named(document):
        current['resource_limits'] = dict(sorted(limits.items()))
    for task in current['tasks']:
        if 'requires' in task:
            task['requires'] = sorted(task['requires'])
        if 'resources' in task:
            task['resources'] = dict(sorted(task['resources'].items()))
    if 'metadata' in document:
        metadata = document['metadata']
        if (type(metadata) is not dict
                or set(metadata) != {'schema', 'resource_unit', 'value_basis'}
                or metadata['schema'] != 'ruc-work-metadata-v1'):
            raise ScenarioError('metadata requires schema ruc-work-metadata-v1, resource_unit, value_basis only')
        for field, maximum in [('resource_unit', 80), ('value_basis', 512)]:
            value = metadata[field]
            if type(value) is not str or not value.strip() or len(value) > maximum:
                raise ScenarioError(f'metadata {field} requires nonblank text of at most {maximum} characters')
        current['metadata'] = dict(metadata)
    return current


def _canonical(document):
    try:
        return json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ScenarioError('plan must contain finite JSON values') from None


def _digest(document):
    return hashlib.sha256(_canonical(document).encode('utf-8')).hexdigest()


class _Deadline:
    def __init__(self):
        self.end = monotonic_ns() + 1000000000

    def remaining(self):
        remaining = self.end - monotonic_ns()
        if remaining <= 0:
            raise ScenarioError('planning stopped at TIMEOUT; no exact report produced')
        return min(remaining, 1000000000)


def _required(current, fixed):
    by_id = {task['id']: task for task in current['tasks']}
    fixed = fixed | {task['id'] for task in current['tasks'] if task.get('mandatory', False)}
    pending = list(fixed)
    while pending:
        for task_id in by_id[pending.pop()].get('requires', []):
            if task_id not in fixed:
                fixed.add(task_id)
                pending.append(task_id)
    return fixed


def validate(document):
    """Check bounded input and required-work feasibility without optimizing."""
    current = _input(document)
    completed = set(current['completed_ids'])
    required = _required(current, completed)
    demand = sum(task['demand'] for task in current['tasks'] if task['id'] in required)
    resources = _resources(current, required)
    slack = current['capacity'] - demand
    feasible = slack >= 0 and all(value >= 0 for value in resources.get('resource_slack', {}).values())
    return {'schema': 'ruc-work-validation-v1', 'status': 'FEASIBLE' if feasible else 'INFEASIBLE',
            'input': current, 'input_digest': _digest(current), 'task_count': len(current['tasks']),
            'completed_ids': current['completed_ids'], 'required_ids': sorted(required),
            'completed_capacity_used': sum(task['demand'] for task in current['tasks']
                                           if task['id'] in completed),
            'completed_resources_used': _resources(current, completed).get('resources_used', {}),
            'required_capacity_used': demand, 'required_capacity_slack': slack,
            'required_resources_used': resources.get('resources_used', {}),
            'required_resource_slack': resources.get('resource_slack', {}),
            'claim_boundary': 'Input and required-work feasibility only; no optimal selection or execution.'}


def _capacity_selections(current, fixed, maximum, deadline):
    """Exact table for all capacities, sharing decisions within this invocation."""
    if not _extended(current):
        return _legacy_capacity_selections(current, fixed, maximum, deadline)
    fixed = _required(current, fixed)
    by_id = {task['id']: task for task in current['tasks']}
    deadline.remaining()
    if sum(by_id[task_id]['demand'] for task_id in fixed) > maximum:
        return [None] * (maximum + 1)
    if any(slack < 0 for slack in _resources(current, fixed).get('resource_slack', {}).values()):
        return [None] * (maximum + 1)
    ordered = _order(current['tasks'])
    # Higher bits encode earlier include-first decisions, preserving existing ties.
    bits = {task_id: 1 << (len(ordered) - index - 1) for index, task_id in enumerate(ordered)}
    tasks = [by_id[task_id] for task_id in ordered]
    limits = tuple(current.get('resource_limits', {}).values())
    usages = [tuple(task.get('resources', {}).get(name, 0)
                    for name in current.get('resource_limits', {})) for task in tasks]
    prerequisites = [sum(bits[task_id] for task_id in task.get('requires', [])) for task in tasks]
    future = [0] * (len(tasks) + 1)
    for index in range(len(tasks) - 1, -1, -1):
        future[index] = future[index + 1] | prerequisites[index]
    states, nodes = {(0, 0, (0,) * len(limits)): (0, 0)}, 1
    for index, task in enumerate(tasks):
        following = {}
        for (needed, used, resources), (value, mask) in states.items():
            deadline.remaining()
            options = []
            if task['id'] not in fixed:
                options.append((used, resources, value, mask))
            included = tuple(a + b for a, b in zip(resources, usages[index]))
            if (needed & prerequisites[index] == prerequisites[index]
                    and used + task['demand'] <= maximum
                    and all(usage <= limit for usage, limit in zip(included, limits))):
                options.append((used + task['demand'], included,
                                value + task['value'], mask | bits[task['id']]))
            for demand, profile, reward, selected in options:
                if nodes >= _SEARCH_NODE_LIMIT:
                    raise ScenarioError('planning stopped at NODE_LIMIT; no exact report produced')
                nodes += 1
                key = (selected & future[index + 1], demand, profile)
                candidate = (reward, selected)
                # Same usage vector and future prerequisites have identical continuations.
                # Greater value dominates; equal value retains include-first order.
                if candidate > following.get(key, (-1, 0)):
                    following[key] = candidate
        states = following
    exact = {}
    for (_, used, _), candidate in states.items():
        if candidate > exact.get(used, (-1, 0)):
            exact[used] = candidate
    selections, best = [], None
    for capacity in range(maximum + 1):
        candidate = exact.get(capacity)
        if candidate is not None and (best is None or candidate > best):
            best = candidate
        if best is None:
            selections.append(None)
        else:
            selected = sorted(task_id for task_id in ordered if best[1] & bits[task_id])
            selections.append({'selected_ids': selected, 'total_value': best[0],
                               'capacity_used': sum(by_id[task_id]['demand'] for task_id in selected),
                               **_resources(current, selected)})
    deadline.remaining()
    return selections


def _legacy_capacity_selections(current, fixed, maximum, deadline):
    """Share DP cells while retaining the original greedy/first-improvement ties."""
    tasks = [task for task in current['tasks'] if task['id'] not in fixed]
    used = sum(task['demand'] for task in current['tasks'] if task['id'] in fixed)
    if used > maximum:
        return [None] * (maximum + 1)
    jobs = tuple(Job(task['id'], task['demand'], task['value']) for task in tasks)
    available = maximum - used
    winners = []
    for capacity in range(available + 1):
        solved = greedy(PlanningObservation(jobs, capacity), Budget(timeout_ns=deadline.remaining()))
        winners.append((solved.reward, set(solved.job_ids)))
    values, masks = [0] * (available + 1), [0] * (available + 1)
    for index, task in enumerate(tasks):
        for capacity in range(available, task['demand'] - 1, -1):
            deadline.remaining()
            candidate = values[capacity - task['demand']] + task['value']
            if candidate > values[capacity]:
                values[capacity] = candidate
                masks[capacity] = masks[capacity - task['demand']] | (1 << index)
            # Each smaller-capacity solve sees the same descending cell updates.
            for target in range(capacity, available + 1):
                if values[capacity] > winners[target][0]:
                    winners[target] = (values[capacity], {
                        task['id'] for i, task in enumerate(tasks) if masks[capacity] & (1 << i)})
    selections = [None] * used
    for _, selected in winners:
        ids = fixed | selected
        selections.append({'selected_ids': sorted(ids),
                           'total_value': sum(task['value'] for task in current['tasks'] if task['id'] in ids),
                           'capacity_used': sum(task['demand'] for task in current['tasks'] if task['id'] in ids)})
    deadline.remaining()
    return selections


def _selection(current, fixed, deadline):
    if _extended(current):
        return _capacity_selections(current, fixed, current['capacity'], deadline)[-1]
    used = sum(task['demand'] for task in current['tasks'] if task['id'] in fixed)
    timeout = deadline.remaining()
    if used > current['capacity']:
        return None
    jobs = tuple(Job(task['id'], task['demand'], task['value'])
                 for task in current['tasks'] if task['id'] not in fixed)
    solved = exact_dp(PlanningObservation(jobs, current['capacity'] - used),
                      Budget(node_limit=2560, timeout_ns=timeout))
    if solved.stop_reason != 'EXHAUSTED':
        raise ScenarioError(f'planning stopped at {solved.stop_reason}; no exact plan produced')
    deadline.remaining()
    selected = sorted(fixed | set(solved.job_ids))
    return {'selected_ids': selected,
            'capacity_used': sum(task['demand'] for task in current['tasks'] if task['id'] in selected),
            'total_value': sum(task['value'] for task in current['tasks'] if task['id'] in selected)}


def _plan(document, deadline):
    current = _input(document)
    selected = _selection(current, set(current['completed_ids']), deadline)
    if selected is None:
        raise ScenarioError('mandatory work and prerequisites exceed total capacity' + (
            ' or a named resource limit' if _named(current) else ''))
    extended = _extended(current)
    result = {'schema': 'ruc-work-plan-v3' if _named(current) else (
                'ruc-work-plan-v2' if extended else 'ruc-work-plan-v1'),
            'input': current, 'input_digest': _digest(current),
            **selected,
            'pending_ids': sorted(set(selected['selected_ids']) - set(current['completed_ids'])),
            'completed_ids': current['completed_ids'],
            'solver': 'exact_resources' if _named(current) else ('exact_dependencies' if extended else 'exact_dp'),
            'stop_reason': 'EXHAUSTED', 'claim_boundary': CLAIM}
    if extended:
        pending = set(result['pending_ids'])
        result['pending_order'] = [task_id for task_id in _order(current['tasks']) if task_id in pending]
    return result


def plan(document):
    """Maximize summed value under total capacity, counting completed work once.

    At most 20 indivisible tasks, capacity 128 and four named integer limits.
    Explicit constraints select bounded exact search; legacy inputs retain DP.
    A cooperative one-second timeout fails closed, without a partial plan.
    """
    return _plan(document, _Deadline())


def _revise(previous, document, deadline):
    result = _plan(document, deadline)
    old, new = previous['input'], result['input']
    if new['revision'] == old['revision']:
        raise ScenarioError('replan requires a changed revision')
    old_done, new_done = set(old['completed_ids']), set(new['completed_ids'])
    if not old_done <= new_done or not new_done <= set(previous['selected_ids']):
        raise ScenarioError('completed work must retain prior completions and come from the previous selection')
    old_tasks = {task['id']: task for task in old['tasks']}
    new_tasks = {task['id']: task for task in new['tasks']}
    if any(old_tasks[item] != new_tasks[item] for item in new_done):
        raise ScenarioError('completed task definitions must remain unchanged')
    before, after = set(previous['selected_ids']), set(result['selected_ids'])
    result.update(previous_input=old, previous_input_digest=previous['input_digest'],
                  changes={'retained_ids': sorted(before & after),
                           'added_ids': sorted(after - before),
                           'dropped_ids': sorted(before - after),
                           'previous_total_value': previous['total_value'],
                           'value_delta': result['total_value'] - previous['total_value'],
                           'previous_capacity': old['capacity'], 'current_capacity': new['capacity'],
                           'reason': 'Reoptimized pending work after input revision changed.'})
    return result


def _check(document, submitted, deadline):
    current = _input(document)
    if type(submitted) is not dict:
        raise ScenarioError('plan must be an object')
    if submitted.get('input_digest') != _digest(current):
        raise ScenarioError('stale plan: input revision or content differs')
    expected = (_revise(_plan(submitted['previous_input'], deadline), current, deadline)
                if 'previous_input' in submitted else _plan(current, deadline))
    if _canonical(submitted) != _canonical(expected):
        raise ScenarioError('plan selection, totals or metadata differ from checked input')
    return {'status': 'VALID', 'input_digest': expected['input_digest'],
            'revision': current['revision'], 'total_value': expected['total_value']}


def check(document, submitted):
    """Reject stale or altered plans; identity is consistency, not authentication."""
    return _check(document, submitted, _Deadline())


def replan(previous, document):
    """Validate a prior plan and revise its pending selection against new input."""
    if type(previous) is not dict or 'input' not in previous:
        raise ScenarioError('previous plan requires its input snapshot')
    deadline = _Deadline()
    _check(previous['input'], previous, deadline)
    return _revise(previous, document, deadline)


def counterfactual(document, task_id):
    """Force one excluded pending task while keeping completed work fixed."""
    deadline = _Deadline()
    chosen = _plan(document, deadline)
    current = chosen['input']
    _identifier(task_id)
    if (task_id not in {task['id'] for task in current['tasks']}
            or task_id in chosen['selected_ids']):
        raise ScenarioError('counterfactual requires an existing excluded pending task')
    forced = _selection(current, set(current['completed_ids']) | {task_id}, deadline)
    result = {'schema': 'ruc-work-counterfactual-v3' if _named(current) else (
                'ruc-work-counterfactual-v2' if _extended(current) else 'ruc-work-counterfactual-v1'),
            'input': current,
            'input_digest': chosen['input_digest'], 'task_id': task_id,
            'status': 'FEASIBLE' if forced else 'INFEASIBLE',
            'chosen_ids': chosen['selected_ids'], 'chosen_total_value': chosen['total_value'],
            'forced_ids': forced['selected_ids'] if forced else [],
            'forced_total_value': forced['total_value'] if forced else None,
            'sacrificed_value': chosen['total_value'] - forced['total_value'] if forced else None,
            'replaced_ids': sorted(set(chosen['selected_ids']) - set(forced['selected_ids'])) if forced else [],
            'claim_boundary': CLAIM}
    if _named(current):
        result.update(chosen_resources_used=chosen['resources_used'],
                      chosen_resource_slack=chosen['resource_slack'],
                      forced_resources_used=forced['resources_used'] if forced else None,
                      forced_resource_slack=forced['resource_slack'] if forced else None)
    return result


def sensitivity(document, capacities):
    """Solve up to 32 explicit capacities within one shared one-second deadline."""
    deadline = _Deadline()
    current = _input(document)
    if type(capacities) is not list or not 1 <= len(capacities) <= 32:
        raise ScenarioError('capacities requires a list of 1..32 unique integers')
    for capacity in capacities:
        integer(capacity, 0, 128, 'capacity')
    if len(set(capacities)) != len(capacities):
        raise ScenarioError('capacities must be unique')
    selections = _capacity_selections(current, set(current['completed_ids']), max(capacities), deadline)
    points, previous = [], None
    for capacity in capacities:
        selected = selections[capacity]
        point = {'capacity': capacity, 'status': 'FEASIBLE' if selected else 'INFEASIBLE',
                 'selected_ids': [], 'total_value': None, 'capacity_used': None,
                 'added_ids': [], 'dropped_ids': []}
        if _named(current):
            point.update(resources_used=None, resource_slack=None)
        if selected:
            point.update(selected)
            if previous is not None:
                point['added_ids'] = sorted(set(selected['selected_ids']) - previous)
                point['dropped_ids'] = sorted(previous - set(selected['selected_ids']))
        points.append(point)
        previous = set(selected['selected_ids']) if selected else None
    deadline.remaining()
    return {'schema': 'ruc-work-sensitivity-v3' if _named(current) else (
                'ruc-work-sensitivity-v2' if _extended(current) else 'ruc-work-sensitivity-v1'),
            'input': current,
            'input_digest': _digest(current), 'points': points, 'claim_boundary': CLAIM}


def frontier(document):
    """Report every distinct optimum-value threshold over total capacities 0..128."""
    deadline = _Deadline()
    current = _input(document)
    completed = set(current['completed_ids'])
    selections = _capacity_selections(current, completed, 128, deadline)
    ordered = _order(current['tasks'])
    points, previous_value = [], None
    for capacity, selected in enumerate(selections):
        if selected is not None and selected['total_value'] != previous_value:
            pending = set(selected['selected_ids']) - completed
            points.append({'minimum_capacity': capacity, **selected,
                           'pending_ids': sorted(pending),
                           'pending_order': [task_id for task_id in ordered if task_id in pending]})
            previous_value = selected['total_value']
    deadline.remaining()
    return {'schema': 'ruc-work-frontier-v2' if _named(current) else 'ruc-work-frontier-v1',
            'input': current, 'input_digest': _digest(current),
            'maximum_capacity': 128, 'completed_ids': current['completed_ids'],
            'required_ids': sorted(_required(current, completed)),
            'status': 'FEASIBLE' if points else 'INFEASIBLE', 'points': points,
            'claim_boundary': CLAIM}


def verify_history(plans):
    """Check an anchored supplied history's consistency, not authenticity."""
    deadline = _Deadline()
    if type(plans) is not list or not 2 <= len(plans) <= 32:
        raise ScenarioError('history requires 2..32 ordered plans starting with an initial plan')
    revisions, previous = [], None
    for submitted in plans:
        if type(submitted) is not dict or 'input' not in submitted:
            raise ScenarioError('history plan requires its input snapshot')
        _check(submitted['input'], submitted, deadline)
        revision = submitted['input']['revision']
        if revision in revisions:
            raise ScenarioError('history revisions must be unique')
        if previous is None:
            if 'previous_input' in submitted:
                raise ScenarioError('history must start with an initial plan without a predecessor')
        elif (_canonical(submitted.get('previous_input')) != _canonical(previous['input'])
              or submitted.get('previous_input_digest') != previous['input_digest']
              or _canonical(_revise(previous, submitted['input'], deadline)) != _canonical(submitted)):
            raise ScenarioError('history has a missing, swapped or inconsistent adjacent link')
        revisions.append(revision)
        previous = submitted
    deadline.remaining()
    return {'schema': 'ruc-work-history-v1', 'status': 'VALID', 'revisions': revisions,
            'input_digests': [item['input_digest'] for item in plans],
            'claim_boundary': 'Supplied history consistency, not authenticity or proof of unique ancestry.'}


def _scenario_inputs(current, pack):
    if (type(pack) is not dict or set(pack) != {'schema', 'scenarios'}
            or pack['schema'] != 'ruc-work-scenarios-v1'
            or type(pack['scenarios']) is not list or len(pack['scenarios']) > 7):
        raise ScenarioError('scenario pack requires schema ruc-work-scenarios-v1 and at most 7 scenarios')
    normalized, variants, seen = [], [('nominal', current)], {'nominal'}
    by_id = {task['id']: task for task in current['tasks']}
    for scenario in pack['scenarios']:
        if (type(scenario) is not dict or 'id' not in scenario
                or scenario.keys() - {'id', 'capacity', 'overrides'}):
            raise ScenarioError('scenario requires id and optional capacity, overrides only')
        scenario_id = _identifier(scenario['id'])
        if scenario_id in seen:
            raise ScenarioError('scenario IDs must be unique; nominal is reserved')
        seen.add(scenario_id)
        capacity = scenario.get('capacity', current['capacity'])
        integer(capacity, 0, 128, 'capacity')
        overrides = scenario.get('overrides', [])
        if type(overrides) is not list or len(overrides) > 20:
            raise ScenarioError('overrides must be a list of at most 20 pending task changes')
        changes = {}
        for override in overrides:
            if (type(override) is not dict or 'id' not in override or len(override) < 2
                    or override.keys() - {'id', 'demand', 'value'}):
                raise ScenarioError('override requires id and demand and/or value only')
            task_id = _identifier(override['id'])
            if task_id not in by_id or task_id in current['completed_ids']:
                raise ScenarioError('only existing pending task definitions may be overridden')
            if task_id in changes:
                raise ScenarioError('override task IDs must be unique within a scenario')
            changes[task_id] = dict(override)
        document = dict(current, capacity=capacity, tasks=[
            dict(task, **{k: v for k, v in changes.get(task['id'], {}).items() if k != 'id'})
            for task in current['tasks']])
        variants.append((scenario_id, _input(document)))
        item = {'id': scenario_id}
        if 'capacity' in scenario:
            item['capacity'] = capacity
        if 'overrides' in scenario:
            item['overrides'] = [changes[task_id] for task_id in sorted(changes)]
        normalized.append(item)
    canonical = {'schema': 'ruc-work-scenarios-v1',
                 'scenarios': sorted(normalized, key=lambda item: item['id'])}
    return canonical, [variants[0], *sorted(variants[1:], key=lambda item: item[0])]


def scenario_analysis(document, pack):
    """Exact minimax additive regret over at most eight explicit supplied cases.

    Two finite subset passes and the nominal comparator share one cooperative
    second. The report is a decision calculation, never a saved executable plan.
    """
    deadline = _Deadline()
    current = _input(document)
    canonical, variants = _scenario_inputs(current, pack)
    deadline.remaining()
    completed = set(current['completed_ids'])
    required = _required(current, completed)
    tasks = current['tasks']
    bits = {task['id']: 1 << i for i, task in enumerate(tasks)}
    fixed_mask = sum(bits[item] for item in required)
    optional = [i for i, task in enumerate(tasks) if task['id'] not in required]
    prerequisites = [sum(bits[item] for item in task.get('requires', [])) for task in tasks]
    affected = [[j for j in range(len(tasks)) if j == i or prerequisites[j] & (1 << i)]
                for i in range(len(tasks))]
    demands = [[task['demand'] for task in case['tasks']] for _, case in variants]
    values = [[task['value'] for task in case['tasks']] for _, case in variants]
    capacities = [case['capacity'] for _, case in variants]
    limits = tuple(current.get('resource_limits', {}).values())
    usages = [tuple(task.get('resources', {}).get(name, 0)
                    for name in current.get('resource_limits', {})) for task in tasks]
    nodes = 0

    def subsets():
        nonlocal nodes
        mask, previous, invalid = fixed_mask, 0, set()
        used = [sum(row[i] for i in range(len(tasks)) if mask & (1 << i)) for row in demands]
        rewards = [sum(row[i] for i in range(len(tasks)) if mask & (1 << i)) for row in values]
        resources = [sum(usages[i][j] for i in range(len(tasks)) if mask & (1 << i))
                     for j in range(len(limits))]
        for number in range(1 << len(optional)):
            deadline.remaining()
            if nodes >= _SCENARIO_NODE_LIMIT:
                raise ScenarioError('scenario analysis stopped at NODE_LIMIT; no exact report produced')
            nodes += 1
            gray = number ^ (number >> 1)
            if number:
                changed = (gray ^ previous).bit_length() - 1
                index = optional[changed]
                bit = 1 << index
                sign = -1 if mask & bit else 1
                mask ^= bit
                for j in range(len(limits)):
                    resources[j] += sign * usages[index][j]
                for j in range(len(variants)):
                    used[j] += sign * demands[j][index]
                    rewards[j] += sign * values[j][index]
                for j in affected[index]:
                    if mask & (1 << j) and mask & prerequisites[j] != prerequisites[j]:
                        invalid.add(j)
                    else:
                        invalid.discard(j)
            previous = gray
            if not invalid and all(usage <= limit for usage, limit in zip(resources, limits)):
                yield mask, used, rewards

    def selected(mask):
        return [task['id'] for i, task in enumerate(tasks) if mask & (1 << i)]

    optima = [None] * len(variants)
    optimum_keys = [None] * len(variants)
    for mask, used, rewards in subsets():
        for j in range(len(variants)):
            if used[j] <= capacities[j]:
                prefix = (-rewards[j], used[j])
                old = optimum_keys[j]
                if old is None or prefix <= old[:2]:
                    key = (*prefix, tuple(selected(mask)))
                    if old is None or key < old:
                        optimum_keys[j] = key
                        optima[j] = {'selected_ids': list(key[2]), 'capacity_used': used[j],
                                     'total_value': rewards[j], 'slack': capacities[j] - used[j],
                                     **_resources(current, key[2])}
    recommendation, best_key = None, None
    if all(optimum is not None for optimum in optima):
        for mask, used, rewards in subsets():
            if all(demand <= capacity for demand, capacity in zip(used, capacities)):
                regret = max(optimum['total_value'] - value for optimum, value in zip(optima, rewards))
                prefix = (regret, -min(rewards), -rewards[0], max(used))
                if best_key is None or prefix <= best_key[:4]:
                    ids = selected(mask)
                    key = (*prefix, tuple(ids))
                    if best_key is None or key < best_key:
                        best_key = key
                        recommendation = {'selected_ids': ids, 'maximum_regret': regret,
                                          'minimum_value': min(rewards), 'nominal_value': rewards[0],
                                          'worst_demand': max(used),
                                          'pending_ids': sorted(set(ids) - completed),
                                          **_resources(current, ids),
                                          'pending_order': [item for item in _order(tasks)
                                                            if item in ids and item not in completed]}
    # Preserve the pre-existing nominal plan's exact tie behavior.
    nominal = _selection(current, completed, deadline) if optima[0] is not None else None

    def evaluation(ids, j, *, nominal_comparison=False):
        if ids is None:
            return None
        indices = [i for i, task in enumerate(tasks) if task['id'] in ids]
        demand = sum(demands[j][i] for i in indices)
        value = sum(values[j][i] for i in indices)
        resource_totals = _resources(current, ids)
        feasible = demand <= capacities[j] and all(
            slack >= 0 for slack in resource_totals.get('resource_slack', {}).values())
        result = {'capacity_used': demand, 'total_value': value,
                  'slack': capacities[j] - demand,
                  'regret': optima[j]['total_value'] - value if feasible and optima[j] is not None else None,
                  **resource_totals}
        if nominal_comparison:
            result['feasible'] = feasible
        return result

    scenarios = []
    for j, (scenario_id, case) in enumerate(variants):
        scenarios.append({'id': scenario_id, 'input': case, 'input_digest': _digest(case),
                          'scenario_digest': _digest({'id': scenario_id, 'input': case}),
                          'status': 'FEASIBLE' if optima[j] is not None else 'INFEASIBLE',
                          'optimum': optima[j],
                          'recommended': evaluation(recommendation['selected_ids'], j) if recommendation else None,
                          'nominal_plan': evaluation(nominal['selected_ids'], j, nominal_comparison=True) if nominal else None})
    deadline.remaining()
    return {'schema': 'ruc-work-scenario-analysis-v2' if _named(current) else 'ruc-work-scenario-analysis-v1',
            'input': current, 'input_digest': _digest(current),
            'scenario_pack': canonical,
            'scenario_pack_digest': _digest({'input': current, 'scenario_pack': canonical}),
            'status': 'FEASIBLE' if recommendation is not None else 'NO_COMMON_FEASIBLE',
            'recommended': recommendation, 'nominal_plan': nominal, 'scenarios': scenarios,
            'required_ids': sorted(required), 'completed_ids': current['completed_ids'],
            'solver': 'exact_finite_scenarios', 'stop_reason': 'EXHAUSTED',
            'claim_boundary': 'Exact additive-value regret for supplied finite scenarios and comparable user estimates only. No probabilities, scheduling, execution or production safety guarantee.'}


def _write_new(path, encoded):
    """Publish only a completely closed file, without replacing any destination."""
    descriptor, temporary = mkstemp(prefix='.ruc-', suffix='.tmp', dir=path.parent)
    published, cleanup_failed = False, False
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            output.write(encoded)
        os.link(temporary, path)
        published = True
    finally:
        # Only this invocation's staging path is ours to remove, never the destination.
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError:
            if not published:
                raise
            cleanup_failed = True
    return cleanup_failed


def main(argv=None):
    parser = Parser(description='Plan, check and inspect bounded work offline. Never executes jobs.')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('validate', help='check input and required budgets without optimizing')
    command.add_argument('input', type=Path, help='work JSON, at most 64 KiB')
    for name in ('plan', 'check', 'replan'):
        command = commands.add_parser(name)
        command.add_argument('input', type=Path, help='work JSON, at most 64 KiB')
        if name != 'plan':
            command.add_argument('previous', type=Path, help='plan JSON, at most 128 KiB')
        if name != 'check':
            command.add_argument('--out', required=True, type=Path, help='new output file; overwrite refused')
    command = commands.add_parser('counterfactual')
    command.add_argument('input', type=Path)
    command.add_argument('task_id')
    command = commands.add_parser('sensitivity')
    command.add_argument('input', type=Path)
    command.add_argument('--capacities', required=True, type=int, nargs='+')
    command = commands.add_parser('frontier')
    command.add_argument('input', type=Path)
    command = commands.add_parser('scenarios')
    command.add_argument('input', type=Path, help='work JSON, at most 64 KiB')
    command.add_argument('pack', type=Path, help='scenario pack JSON, at most 64 KiB')
    command.add_argument('--out', required=True, type=Path, help='new decision report; overwrite refused')
    command = commands.add_parser('history')
    command.add_argument('plans', type=Path, nargs='+', help='2..32 ordered plan files, at most 128 KiB each')
    args = parser.parse_args(argv)
    try:
        if args.command == 'validate':
            result = validate(load_json(args.input, 65536))
        elif args.command == 'history':
            if not 2 <= len(args.plans) <= 32:
                raise ScenarioError('history requires 2..32 ordered plan files')
            result = verify_history([load_json(path, 131072) for path in args.plans])
        elif args.command == 'scenarios':
            result = scenario_analysis(load_json(args.input, 65536), load_json(args.pack, 65536))
        elif args.command == 'frontier':
            result = frontier(load_json(args.input, 65536))
        elif args.command in ('counterfactual', 'sensitivity'):
            document = load_json(args.input, 65536)
            result = (counterfactual(document, args.task_id) if args.command == 'counterfactual'
                      else sensitivity(document, args.capacities))
        elif args.command == 'plan':
            document = load_json(args.input, 65536)
            result = plan(document)
        else:
            document = load_json(args.input, 65536)
            previous = load_json(args.previous, 131072)
            result = check(document, previous) if args.command == 'check' else replan(previous, document)
        encoded = json.dumps(result, sort_keys=True, indent=2) + '\n'
        if args.command in ('plan', 'replan', 'scenarios'):
            try:
                if _write_new(args.out, encoded):
                    return error_report('output saved completely, but temporary cleanup failed; check saved output',
                                        code='OUTPUT_ERROR', exit_code=1)
            except FileExistsError:
                raise ScenarioError('output already exists; choose a new output file') from None
            except OSError:
                raise ScenarioError('cannot create output file') from None
    except ScenarioError as error:
        return error_report(str(error))
    try:
        print(encoded, end='')
        sys.stdout.flush()
    except OSError:
        with suppress(OSError):
            sys.stdout.close()
        return error_report('cannot write stdout; a saved output may already exist',
                            code='OUTPUT_ERROR', exit_code=1)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
