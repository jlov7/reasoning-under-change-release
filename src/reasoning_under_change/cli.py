"""Run a bounded authored scenario, with JSON output and structured input errors."""
from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from .core import Event, ScenarioError, World, trial
from .input import Parser, error_report, integer, load_json


def scenario(document):
    required = {'job_id', 'deadline', 'capacity', 'events', 'delay'}
    if type(document) is not dict or not required <= document.keys() or document.keys() - required - {'ttl'}:
        raise ScenarioError('scenario requires job_id, deadline, capacity, events, delay; optional ttl')
    integer(document['delay'], 0, 1000000, 'delay')
    integer(document.get('ttl', 2), 1, 1000000, 'ttl')
    events = document['events']
    if type(events) is not list or len(events) > 10000:
        raise ScenarioError('events must be a list of at most 10000 events')
    if any(type(event) is not dict or set(event) != {'at', 'entity', 'kind', 'value'} for event in events):
        raise ScenarioError('each event requires exactly at, entity, kind, value')
    world = World(document['job_id'], document['deadline'], document['capacity'],
                  [Event(**event) for event in events])
    return world


def main():
    parser = Parser(description='Synthetic reference: no model results')
    parser.add_argument('scenario', type=Path, nargs='?', help='UTF-8 JSON file (at most 1 MiB)')
    parser.add_argument('--example', action='store_true', help='run the packaged supply-change example')
    args = parser.parse_args()
    if bool(args.scenario) == args.example:
        return error_report('provide a scenario file or --example')
    path = args.scenario if args.scenario else files('reasoning_under_change').joinpath('data/supply_change.json')
    try:
        document = load_json(path, 1048576)
        scenario(document)
        results = {policy: trial(scenario(document), document['delay'], policy,
                                 ttl=document.get('ttl', 2))
                   for policy in ('never', 'ttl', 'always', 'relevant')}
    except ScenarioError as error:
        return error_report(str(error))
    source = 'synthetic authored fixture' if args.example else 'user-supplied scenario'
    print(json.dumps({'scenario': path.name, 'source': source,
                      'results': results, 'claim_boundary': 'does not compare learned agent policies'},
                     indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
