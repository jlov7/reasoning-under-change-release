"""A bounded exhaustive decision audit, separate from the optimizing algorithms.

Input normalization is shared with work.validate. Feasibility and optimality are
recomputed here without calling a planner, its DP tables, or its check function.
This is algorithmic diversity, not independent organizational certification.
"""
from __future__ import annotations

import json
from pathlib import Path
from time import monotonic_ns

from . import __version__
from .core import ScenarioError
from .input import Parser, error_report, integer, load_json
from .work import validate

MAX_SUBSETS = 1 << 20


def _subset_totals(tasks, resource_names):
    """Yield numeric-mask order with incrementally maintained integer totals.

    At most twenty bits flip between neighbours; amortized work is fewer than
    two task updates per subset. Storage is O(tasks * named budgets), not O(2**n).
    This enumerator neither calls the planner nor skips masks.
    """
    values = [task['value'] for task in tasks]
    demands = [task['demand'] for task in tasks]
    profiles = [tuple(task.get('resources', {}).get(name, 0) for name in resource_names)
                for task in tasks]
    value = demand = previous = 0
    usage = [0] * len(resource_names)
    for mask in range(1 << len(tasks)):
        changed = previous ^ mask
        while changed:
            bit = changed & -changed
            index = bit.bit_length() - 1
            direction = 1 if mask & bit else -1
            value += direction * values[index]
            demand += direction * demands[index]
            for resource, amount in enumerate(profiles[index]):
                usage[resource] += direction * amount
            changed ^= bit
        previous = mask
        yield mask, value, demand, tuple(usage)


def audit_plan(document, submitted, *, node_limit=65536, timeout_ns=1_000_000_000,
               clock=monotonic_ns):
    """Audit selection, input digest and total value; do not execute selected work.

    A better feasible subset is a concrete counterexample. Exhaustion with no
    better subset establishes optimal value for this bounded integer problem.
    Stopping early is explicitly FEASIBLE_UNPROVEN, even when the plan is optimal.
    Other plan metadata, business values and provenance are not authenticated.
    """
    integer(node_limit, 1, MAX_SUBSETS, 'node_limit')
    integer(timeout_ns, 1, 30_000_000_000, 'timeout_ns')
    deadline = clock() + timeout_ns
    normalized = validate(document)
    current = normalized['input']
    if type(submitted) is not dict or submitted.get('input_digest') != normalized['input_digest']:
        raise ScenarioError('audit requires a plan bound to the current input')
    selected = submitted.get('selected_ids')
    by_id = {task['id']: task for task in current['tasks']}
    if (type(selected) is not list or len(selected) > 20 or
            any(type(item) is not str or item not in by_id for item in selected) or
            len(set(selected)) != len(selected)):
        raise ScenarioError('audit selection requires unique known task IDs')
    integer(submitted.get('total_value'), 0, 200000, 'total_value')
    actual_value = sum(by_id[item]['value'] for item in selected)
    if actual_value != submitted['total_value']:
        raise ScenarioError('submitted total value does not match its selection')
    tasks = current['tasks']
    required = set(current['completed_ids']) | {
        task['id'] for task in tasks if task.get('mandatory', False)}

    def violations(ids):
        selected_set = set(ids)
        reasons = []
        if not required <= selected_set:
            reasons.append('REQUIRED_WORK_MISSING')
        if any(not set(by_id[item].get('requires', [])) <= selected_set for item in ids):
            reasons.append('PREREQUISITE_MISSING')
        if sum(by_id[item]['demand'] for item in ids) > current['capacity']:
            reasons.append('CAPACITY_EXCEEDED')
        if any(sum(by_id[item].get('resources', {}).get(name, 0) for item in ids) > limit
               for name, limit in current.get('resource_limits', {}).items()):
            reasons.append('RESOURCE_LIMIT_EXCEEDED')
        return reasons

    report = {'schema': 'ruc-selection-audit-v1', 'input_digest': normalized['input_digest'],
              'audited_fields': ['input_digest', 'selected_ids', 'total_value'],
              'selected_ids': sorted(selected), 'total_value': actual_value,
              'status': 'FEASIBLE_UNPROVEN', 'violations': violations(selected),
              'subsets_checked': 0, 'subsets_total': 1 << len(tasks),
              'enumeration_complete': False, 'better_selection': None,
              'stop_reason': None,
              'scope': 'Selection/value audit of supplied integer work only; shared input parser, '
                       'separate exhaustive algorithm; no execution, authenticity or business-value proof.'}
    if report['violations']:
        report.update(status='INFEASIBLE_SELECTION', stop_reason='INVALID_SELECTION')
        return report
    indexes = {task['id']: index for index, task in enumerate(tasks)}
    required_mask = sum(1 << indexes[task_id] for task_id in required)
    prerequisites = [sum(1 << indexes[parent] for parent in task.get('requires', []))
                     for task in tasks]
    limits = current.get('resource_limits', {})
    for mask, value, demand, usage in _subset_totals(tasks, tuple(limits)):
        if report['subsets_checked'] >= node_limit:
            report['stop_reason'] = 'NODE_LIMIT'
            return report
        if clock() >= deadline:
            report['stop_reason'] = 'TIMEOUT'
            return report
        report['subsets_checked'] += 1
        # A lower/equal value cannot refute the submitted optimum. The mask is
        # still counted, preserving exhaustive coverage and deterministic order.
        if (value <= actual_value or mask & required_mask != required_mask or
                demand > current['capacity'] or
                any(used > limit for used, limit in zip(usage, limits.values()))):
            continue
        if all(not mask & (1 << index) or mask & needed == needed
               for index, needed in enumerate(prerequisites)):
            ids = [task['id'] for index, task in enumerate(tasks) if mask & (1 << index)]
            report.update(status='SUBOPTIMAL', stop_reason='BETTER_SELECTION_FOUND',
                          better_selection={'selected_ids': sorted(ids), 'total_value': value},
                          enumeration_complete=report['subsets_checked'] == report['subsets_total'])
            return report
    # Do not report exactness after overrunning the cooperative deadline.
    if clock() >= deadline:
        report['stop_reason'] = 'TIMEOUT'
        return report
    report.update(status='OPTIMAL_FOR_INPUT', stop_reason='EXHAUSTED', enumeration_complete=True)
    return report


def main():
    parser = Parser(description=__doc__)
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--node-limit', type=int, default=65536)
    parser.add_argument('--timeout-seconds', type=int, default=1)
    args = parser.parse_args()
    try:
        integer(args.timeout_seconds, 1, 30, 'timeout_seconds')
        report = audit_plan(load_json(args.input, 131072), load_json(args.plan, 262144),
                            node_limit=args.node_limit,
                            timeout_ns=args.timeout_seconds * 1_000_000_000)
    except ScenarioError:
        return error_report('Check the current bounded work input, plan selection, digest, totals and audit budget.')
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report['status'] == 'OPTIMAL_FOR_INPUT' else 1


if __name__ == '__main__':
    raise SystemExit(main())
