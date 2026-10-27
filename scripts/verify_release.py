"""Build sdist -> wheel -> isolated installed tests and advertised offline commands.

Run with: uv run --no-project python scripts/verify_release.py
Artifacts and logs are retained in a unique .release-scratch/verify-* directory.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(os.environ.get('RUC_RELEASE_SCRATCH', ROOT / '.release-scratch'))


def _check_public_docs(source):
    names = ['README.md', 'CONTRIBUTING.md', 'SECURITY.md', 'SUPPORT.md', 'CHANGELOG.md',
             'docs/USAGE.md', 'docs/DESIGN.md', 'docs/EVALUATION.md', 'docs/LIMITATIONS.md',
             'docs/REFERENCES.md', 'docs/STABILITY.md', 'docs/RELEASING.md',
             'docs/CPU-PLANNING-PROTOCOL.md', 'docs/CPU-PLANNING-V2-PROTOCOL.md',
             'docs/VALIDATION.md']
    checked = 0
    for name in names:
        document = source / name
        text = document.read_text(encoding='utf-8')
        for link in re.findall(r'!?\[[^]\n]*\]\(([^)\s]+)\)', text):
            parsed = urlsplit(link)
            if parsed.scheme:
                assert parsed.scheme in ('http', 'https') and parsed.netloc, (name, link)
                continue
            target = (document.parent / unquote(parsed.path)).resolve() if parsed.path else document
            assert target.is_relative_to(source.resolve()) and target.is_file(), (name, link)
            if parsed.fragment and target.suffix == '.md':
                anchors = {re.sub(r'[^\w\s-]', '', heading.lower()).replace(' ', '-')
                           for heading in re.findall(r'^#+ +(.+)$', target.read_text(), re.MULTILINE)}
                assert unquote(parsed.fragment) in anchors, (name, link)
            checked += 1
    assert '![A queue' in (source / 'README.md').read_text()
    image = ET.parse(source / 'docs/images/workflow.svg').getroot()
    assert image.tag == '{http://www.w3.org/2000/svg}svg' and image.get('viewBox') == '0 0 920 500'
    assert all(element.tag.rsplit('}', 1)[-1] not in ('script', 'foreignObject')
               for element in image.iter())
    return {'documents': len(names), 'local_links': checked, 'workflow_svg': 'docs/images/workflow.svg',
            'external_links': 'syntax checked only; no network requests'}


def main():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='verify-', dir=SCRATCH))
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.setdefault('UV_CACHE_DIR', str(SCRATCH / 'uv-cache'))
    env['UV_PYTHON_DOWNLOADS'] = 'never'
    (run / 'tmp').mkdir()
    env['TMPDIR'] = str(run / 'tmp')
    commands = []

    def command(argv, label, cwd=run, expected_exit=0):
        result = subprocess.run([str(arg) for arg in argv], cwd=cwd, env=env,
                                capture_output=True, text=True, timeout=180, check=False)
        (run / f'{label}.stdout.txt').write_text(result.stdout)
        (run / f'{label}.stderr.txt').write_text(result.stderr)
        commands.append({'label': label, 'argv': [str(arg) for arg in argv],
                         'exit_code': result.returncode, 'expected_exit': expected_exit})
        if result.returncode != expected_exit:
            raise RuntimeError(f'{label} failed; inspect {run}')
        return result.stdout

    try:
        command(['uv', 'build', ROOT, '--sdist', '--build-constraints', ROOT / 'requirements-build.txt',
                 '--out-dir', run / 'dist'], 'sdist')
        sdist, = (run / 'dist').glob('*.tar.gz')
        with tarfile.open(sdist) as archive:
            names = archive.getnames()
            assert any(name.endswith('/examples/supply_change.json') for name in names)
            assert not any(any(part in name.split('/') for part in ['.release-scratch', 'LICENSE-STATUS.md', 'STATUS.md', 'AGENTS.md']) for name in names)
            archive.extractall(run / 'source', filter='data')
        source, = (run / 'source').iterdir()
        public_docs = _check_public_docs(source)
        command(['uv', 'build', source, '--wheel', '--build-constraints', source / 'requirements-build.txt',
                 '--out-dir', run / 'wheel'], 'wheel')
        wheel, = (run / 'wheel').glob('*.whl')
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            assert 'reasoning_under_change/data/supply_change.json' in names
            assert 'reasoning_under_change/data/SHA256.json' in names
            assert not any('LICENSE-STATUS' in name for name in names)
        venv = run / 'venv'
        command(['uv', 'venv', '--python', sys.executable, venv], 'venv')
        python = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        binary = python.parent
        command(['uv', 'pip', 'install', '--python', python, '--no-deps', '--offline', wheel], 'install')
        command([python, '-I', '-c',
                 "import pathlib,sys,reasoning_under_change as r; p=pathlib.Path(r.__file__).resolve(); assert p.is_relative_to(pathlib.Path(sys.prefix).resolve()),p; print(p)"], 'installed-import')
        command([python, '-I', '-c',
                 "from importlib.metadata import version; import reasoning_under_change as r; assert version('reasoning-under-change') == r.__version__ == '1.1.1'"], 'installed-version')
        command(['uv', 'pip', 'check', '--python', python], 'installed-dependencies')
        for executable in ['ruc-work', 'ruc-scenario', 'ruc-grid', 'ruc-planning', 'ruc-results']:
            command([binary / executable, '--help'], f'help-{executable}')
        for verb in ['validate', 'plan', 'check', 'replan', 'counterfactual', 'sensitivity',
                     'frontier', 'scenarios', 'history']:
            command([binary / 'ruc-work', verb, '--help'], f'help-work-{verb}')
        assert command([binary / 'ruc-work', '--version'], 'work-version').strip() == '1.1.1'
        command([python, '-I', '-m', 'reasoning_under_change.audit', '--help'], 'audit-help')
        assert command([python, '-I', '-m', 'reasoning_under_change.audit', '--version'],
                       'audit-version').strip() == '1.1.1'
        command([python, '-I', '-m', 'unittest', 'discover', '-s', source / 'tests', '-v'], 'installed-tests')
        direct_venv = run / 'sdist-venv'
        command(['uv', 'venv', '--python', sys.executable, direct_venv], 'sdist-venv')
        direct_python = direct_venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        command(['uv', 'pip', 'install', '--python', direct_python, '--no-deps',
                 '--build-constraints', ROOT / 'requirements-build.txt', sdist], 'sdist-install')
        command([direct_python, '-I', '-c',
                 "import pathlib,sys,reasoning_under_change as r; from importlib.metadata import version; assert pathlib.Path(r.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve()); assert version('reasoning-under-change') == r.__version__ == '1.1.1'"],
                'sdist-import-version')
        command([direct_python, '-I', '-m', 'unittest', 'discover', '-s', source / 'tests', '-v'],
                'sdist-tests')
        command([direct_python, '-I', '-m', 'reasoning_under_change.audit', '--help'], 'sdist-audit-help')
        work = {'revision': 'fresh-1', 'capacity': 10, 'completed_ids': [], 'tasks': [
            {'id': 'compile', 'demand': 6, 'value': 25},
            {'id': 'lint', 'demand': 5, 'value': 23},
            {'id': 'smoke', 'demand': 4, 'value': 18},
            {'id': 'unit', 'demand': 5, 'value': 22}]}
        before, after = run / 'work.json', run / 'work-updated.json'
        before.write_text(json.dumps(work))
        updated = dict(work, revision='fresh-2', capacity=9, completed_ids=['lint'])
        after.write_text(json.dumps(updated))
        first, second = run / 'plan-1.json', run / 'plan-2.json'
        command([binary / 'ruc-work', 'plan', before, '--out', first], 'work-plan')
        command([binary / 'ruc-work', 'check', before, first], 'work-check')
        command([binary / 'ruc-work', 'replan', after, first, '--out', second], 'work-replan')
        command([binary / 'ruc-work', 'check', after, second], 'work-check-revised')
        initial, revised = json.loads(first.read_text()), json.loads(second.read_text())
        # Independent enumeration, separate from the planner/evaluator implementation.
        for document, result in [(work, initial), (updated, revised)]:
            tasks = document['tasks']
            feasible = []
            for mask in range(1 << len(tasks)):
                subset = [task for i, task in enumerate(tasks) if mask & (1 << i)]
                ids = {task['id'] for task in subset}
                if (set(document['completed_ids']) <= ids
                        and sum(task['demand'] for task in subset) <= document['capacity']):
                    feasible.append((sum(task['value'] for task in subset), ids))
            assert (result['total_value'], set(result['selected_ids'])) in feasible
            assert result['total_value'] == max(value for value, _ in feasible)
        assert initial['total_value'] == 45 and revised['total_value'] == 41
        assert revised['completed_ids'] == ['lint'] and revised['capacity_used'] == 9
        resource_input = source / 'examples/work_resources.json'
        resource_changed = source / 'examples/work_resources_changed.json'
        resource_first, resource_second = run / 'resources-1.json', run / 'resources-2.json'
        preflight = json.loads(command([binary / 'ruc-work', 'validate', resource_input], 'resources-validate'))
        assert preflight['schema'] == 'ruc-work-validation-v1' and preflight['status'] == 'FEASIBLE'
        assert 'selected_ids' not in preflight
        command([binary / 'ruc-work', 'validate', resource_changed], 'resources-validate-changed')
        command([binary / 'ruc-work', 'plan', resource_input, '--out', resource_first], 'resources-plan')
        command([binary / 'ruc-work', 'check', resource_input, resource_first], 'resources-check')
        command([binary / 'ruc-work', 'replan', resource_changed, resource_first, '--out', resource_second],
                'resources-replan')
        command([binary / 'ruc-work', 'history', resource_first, resource_second], 'resources-history')
        for input_path, plan_path in [(resource_input, resource_first), (resource_changed, resource_second)]:
            document, result = json.loads(input_path.read_text()), json.loads(plan_path.read_text())
            tasks = document['tasks']
            feasible = []
            for mask in range(1 << len(tasks)):
                subset = [task for i, task in enumerate(tasks) if mask & (1 << i)]
                ids = {task['id'] for task in subset}
                if (set(document['completed_ids']) <= ids
                        and all(not task.get('mandatory', False) or task['id'] in ids for task in tasks)
                        and all(set(task.get('requires', [])) <= ids for task in subset)
                        and sum(task['demand'] for task in subset) <= document['capacity']
                        and all(sum(task.get('resources', {}).get(name, 0) for task in subset) <= limit
                                for name, limit in document['resource_limits'].items())):
                    feasible.append((sum(task['value'] for task in subset), ids))
            assert result['schema'] == 'ruc-work-plan-v3'
            assert (result['total_value'], set(result['selected_ids'])) in feasible
            assert result['total_value'] == max(value for value, _ in feasible)
        resource_plan = json.loads(resource_first.read_text())
        audit_args = ['-I', '-m', 'reasoning_under_change.audit', '--input', resource_input,
                      '--plan', resource_first]
        audit = json.loads(command([python, *audit_args], 'audit-optimal'))
        assert audit['status'] == 'OPTIMAL_FOR_INPUT' and audit['enumeration_complete']
        unproven = json.loads(command([python, *audit_args, '--node-limit', '1'],
                                     'audit-unproven', expected_exit=1))
        assert unproven['status'] == 'FEASIBLE_UNPROVEN'
        assert unproven['subsets_checked'] == 1 and not unproven['enumeration_complete']
        command([direct_python, *audit_args], 'sdist-audit-optimal')
        suboptimal_path = run / 'suboptimal-selection.json'
        suboptimal_path.write_text(json.dumps(dict(resource_plan, selected_ids=[], total_value=0)))
        suboptimal = json.loads(command([python, '-I', '-m', 'reasoning_under_change.audit',
                                        '--input', resource_input, '--plan', suboptimal_path],
                                       'audit-suboptimal', expected_exit=1))
        assert suboptimal['status'] == 'SUBOPTIMAL'
        witness = suboptimal['better_selection']
        witness_ids = set(witness['selected_ids'])
        document = json.loads(resource_input.read_text())
        subset = [task for task in document['tasks'] if task['id'] in witness_ids]
        assert len(subset) == len(witness_ids)
        assert set(document['completed_ids']) <= witness_ids
        assert all(not task.get('mandatory') or task['id'] in witness_ids for task in document['tasks'])
        assert all(set(task.get('requires', [])) <= witness_ids for task in subset)
        assert sum(task['demand'] for task in subset) <= document['capacity']
        assert all(sum(task.get('resources', {}).get(name, 0) for task in subset) <= limit
                   for name, limit in document['resource_limits'].items())
        assert witness['total_value'] == sum(task['value'] for task in subset) > 0
        infeasible_path = run / 'infeasible-selection.json'
        all_ids = [task['id'] for task in document['tasks']]
        infeasible_path.write_text(json.dumps(dict(resource_plan, selected_ids=all_ids,
            total_value=sum(task['value'] for task in document['tasks']))))
        infeasible = json.loads(command([python, '-I', '-m', 'reasoning_under_change.audit',
                                        '--input', resource_input, '--plan', infeasible_path],
                                       'audit-infeasible', expected_exit=1))
        assert infeasible['status'] == 'INFEASIBLE_SELECTION' and infeasible['violations']
        invalid_audit = run / 'invalid-selection.json'
        invalid_audit.write_text(json.dumps(dict(resource_plan, input_digest='0' * 64)))
        assert not command([python, '-I', '-m', 'reasoning_under_change.audit',
                            '--input', resource_input, '--plan', invalid_audit],
                           'audit-invalid', expected_exit=2)
        error = json.loads((run / 'audit-invalid.stderr.txt').read_text())
        assert error['error']['code'] == 'INVALID_INPUT' and len(error['request_id']) == 32
        assert resource_plan['selected_ids'] == ['baseline', 'reliability', 'telemetry']
        assert resource_plan['resources_used'] == {'engineering': 5, 'review': 3}
        assert resource_plan['resource_slack'] == {'engineering': 0, 'review': 0}
        forced = json.loads(command([binary / 'ruc-work', 'counterfactual', resource_input,
                                     'migration-review'], 'resources-forced-infeasible'))
        assert forced['status'] == 'INFEASIBLE' and forced['forced_resources_used'] is None
        resource_points = json.loads(command([binary / 'ruc-work', 'sensitivity', resource_input,
                                              '--capacities', '0', '4', '6', '8'], 'resources-sensitivity'))
        assert [point['total_value'] for point in resource_points['points']] == [0, 12, 19, 19]
        resource_frontier = json.loads(command([binary / 'ruc-work', 'frontier', resource_input],
                                               'resources-frontier'))
        assert resource_frontier['schema'] == 'ruc-work-frontier-v2'
        assert resource_frontier['points'][-1]['total_value'] == 19
        resource_pack = run / 'resource-pack.json'
        resource_pack.write_text(json.dumps({'schema': 'ruc-work-scenarios-v1', 'scenarios': [
            {'id': 'search-critical', 'overrides': [{'id': 'search', 'value': 30}]}]}))
        resource_report = json.loads(command([binary / 'ruc-work', 'scenarios', resource_input,
                                               resource_pack, '--out', run / 'resource-report.json'],
                                              'resources-scenarios'))
        assert resource_report['schema'] == 'ruc-work-scenario-analysis-v2'
        assert all(all(slack >= 0 for slack in point['recommended']['resource_slack'].values())
                   for point in resource_report['scenarios'])
        counterfactual = json.loads(command(
            [binary / 'ruc-work', 'counterfactual', before, 'compile'], 'work-counterfactual'))
        assert counterfactual['forced_total_value'] == 43
        assert counterfactual['sacrificed_value'] == 2
        sensitivity = json.loads(command(
            [binary / 'ruc-work', 'sensitivity', after, '--capacities', '0', '4', '5', '9', '10'],
            'work-sensitivity'))
        assert [point['total_value'] for point in sensitivity['points']] == [None, None, 23, 41, 45]
        third_input, third = run / 'work-third.json', run / 'plan-3.json'
        metadata = {'schema': 'ruc-work-metadata-v1', 'resource_unit': 'CPU minutes',
                    'value_basis': 'Authored relative priorities, not observed utility.'}
        third_input.write_text(json.dumps(dict(updated, revision='fresh-3', capacity=10,
                                              metadata=metadata)))
        command([binary / 'ruc-work', 'replan', third_input, second, '--out', third], 'work-replan-third')
        assert json.loads(third.read_text())['input']['metadata'] == metadata
        history = json.loads(command([binary / 'ruc-work', 'history', first, second, third], 'work-history'))
        assert history['revisions'] == ['fresh-1', 'fresh-2', 'fresh-3']
        constrained = source / 'examples/work_prerequisites.json'
        constrained_changed = source / 'examples/work_prerequisites_changed.json'
        fourth, fifth = run / 'constrained-1.json', run / 'constrained-2.json'
        preflight = json.loads(command([binary / 'ruc-work', 'validate', constrained], 'constrained-validate'))
        assert preflight['required_ids'] == ['baseline', 'inventory', 'smoke']
        assert preflight['status'] == 'FEASIBLE'
        command([binary / 'ruc-work', 'plan', constrained, '--out', fourth], 'constrained-plan')
        command([binary / 'ruc-work', 'check', constrained, fourth], 'constrained-check')
        command([binary / 'ruc-work', 'replan', constrained_changed, fourth, '--out', fifth], 'constrained-replan')
        command([binary / 'ruc-work', 'history', fourth, fifth], 'constrained-history')
        for input_path, plan_path, expected_value in [(constrained, fourth, 42),
                                                       (constrained_changed, fifth, 32)]:
            document, result = json.loads(input_path.read_text()), json.loads(plan_path.read_text())
            tasks = document['tasks']
            feasible = []
            for mask in range(1 << len(tasks)):
                subset = [task for i, task in enumerate(tasks) if mask & (1 << i)]
                ids = {task['id'] for task in subset}
                if (set(document['completed_ids']) <= ids
                        and all(not task.get('mandatory', False) or task['id'] in ids for task in tasks)
                        and all(set(task.get('requires', [])) <= ids for task in subset)
                        and sum(task['demand'] for task in subset) <= document['capacity']):
                    feasible.append((sum(task['value'] for task in subset), ids))
            assert (result['total_value'], set(result['selected_ids'])) in feasible
            assert result['total_value'] == max(value for value, _ in feasible) == expected_value
            seen = set(document['completed_ids'])
            by_id = {task['id']: task for task in tasks}
            for task_id in result['pending_order']:
                assert set(by_id[task_id].get('requires', [])) <= seen
                seen.add(task_id)
            assert seen == set(result['selected_ids'])
        command([binary / 'ruc-work', 'counterfactual', constrained, 'mobile-booking'], 'constrained-counterfactual')
        command([binary / 'ruc-work', 'sensitivity', constrained_changed,
                 '--capacities', '7', '12', '16', '20'], 'constrained-sensitivity')
        frontier = json.loads(command([binary / 'ruc-work', 'frontier', constrained_changed],
                                      'constrained-frontier'))
        assert frontier['schema'] == 'ruc-work-frontier-v1'
        assert frontier['required_ids'] == ['baseline', 'inventory', 'smoke']
        assert frontier['completed_ids'] == ['baseline', 'inventory', 'smoke']
        assert frontier['maximum_capacity'] == 128 and frontier['status'] == 'FEASIBLE'
        points = frontier['points']
        assert all(a['minimum_capacity'] < b['minimum_capacity'] and a['total_value'] < b['total_value']
                   for a, b in itertools.pairwise(points))
        assert max(p['total_value'] for p in points if p['minimum_capacity'] <= 16) == 32
        legacy_frontier = json.loads(command([binary / 'ruc-work', 'frontier', before], 'work-frontier'))
        assert max(p['total_value'] for p in legacy_frontier['points'] if p['minimum_capacity'] <= 10) == 45
        scenario_pack = source / 'examples/work_scenarios.json'
        scenario_report = run / 'scenario-report.json'
        decision = json.loads(command([binary / 'ruc-work', 'scenarios', constrained_changed,
                                      scenario_pack, '--out', scenario_report], 'work-scenarios'))
        assert json.loads(scenario_report.read_text()) == decision
        assert decision['schema'] == 'ruc-work-scenario-analysis-v1'
        assert decision['status'] == 'FEASIBLE'
        # Independent enumeration uses only effective supplied inputs, not solver helpers.
        variants = [item['input'] for item in decision['scenarios']]
        tasks = variants[0]['tasks']
        choices = []
        for mask in range(1 << len(tasks)):
            ids = {task['id'] for i, task in enumerate(tasks) if mask & (1 << i)}
            if (set(variants[0]['completed_ids']) <= ids
                    and all(not task.get('mandatory') or task['id'] in ids for task in tasks)
                    and all(set(task.get('requires', [])) <= ids for task in tasks if task['id'] in ids)):
                totals = [(sum(t['demand'] for t in case['tasks'] if t['id'] in ids),
                           sum(t['value'] for t in case['tasks'] if t['id'] in ids)) for case in variants]
                choices.append((sorted(ids), totals))
        optimum_values = [max(totals[j][1] for _, totals in choices if totals[j][0] <= case['capacity'])
                          for j, case in enumerate(variants)]
        common = [(ids, totals) for ids, totals in choices
                  if all(totals[j][0] <= case['capacity'] for j, case in enumerate(variants))]
        def scenario_score(choice):
            ids, totals = choice
            return (max(optimum_values[j] - total[1] for j, total in enumerate(totals)),
                    -min(value for _, value in totals), -totals[0][1],
                    max(demand for demand, _ in totals), ids)
        best_ids, best_totals = min(common, key=scenario_score)
        assert decision['recommended']['selected_ids'] == best_ids
        for j, point in enumerate(decision['scenarios']):
            assert point['optimum']['total_value'] == optimum_values[j]
            assert point['recommended']['regret'] == optimum_values[j] - best_totals[j][1]
        bad_pack = run / 'completed-override.json'
        bad_pack.write_text(json.dumps({'schema': 'ruc-work-scenarios-v1', 'scenarios': [
            {'id': 'bad', 'overrides': [{'id': 'baseline', 'demand': 4}]}]}))
        refusal = subprocess.run([str(binary / 'ruc-work'), 'scenarios', str(constrained_changed),
                                 str(bad_pack), '--out', str(run / 'refused-report.json')],
                                cwd=run, env=env, capture_output=True, text=True, timeout=10, check=False)
        assert refusal.returncode == 2 and not refusal.stdout
        assert not (run / 'refused-report.json').exists()
        (run / 'completed-override.stderr.txt').write_text(refusal.stderr)
        for label, argv in [
                ('stale-work', ['check', after, first]),
                ('missing-history-link', ['history', first, third]),
                ('metadata-stale', ['check', third_input, second]),
                ('overwrite-work', ['plan', before, '--out', first])]:
            response = subprocess.run([str(binary / 'ruc-work'), *map(str, argv)],
                                      cwd=run, env=env, capture_output=True, text=True, timeout=10, check=False)
            assert response.returncode == 2 and not response.stdout
            assert json.loads(response.stderr)['error']['code'] == 'INVALID_INPUT'
            (run / f'{label}.stderr.txt').write_text(response.stderr)
        assert json.loads(first.read_text()) == initial
        command([binary / 'ruc-scenario', '--example'], 'example')
        command([binary / 'ruc-scenario', source / 'examples/supply_change.json'], 'sdist-example')
        command([binary / 'ruc-results'], 'bundled-results')
        for name, args in [('grid', []), ('planning', []), ('planning-v2', ['--v2'])]:
            executable = binary / ('ruc-grid' if name == 'grid' else 'ruc-planning')
            output = command([executable, *args], name)
            path = run / f'{name}.json'
            path.write_text(output)
            command([binary / 'ruc-results', path], f'verify-{name}')
        invalid = subprocess.run([str(binary / 'ruc-scenario'), str(run / 'missing.json')],
                                 cwd=run, env=env, capture_output=True, text=True, timeout=10, check=False)
        assert invalid.returncode == 2 and not invalid.stdout
        error = json.loads(invalid.stderr)
        assert error['error']['code'] == 'INVALID_INPUT' and len(error['request_id']) == 32
        (run / 'invalid-input.stderr.txt').write_text(invalid.stderr)
        receipt = {'status': 'PASS', 'python': sys.version, 'commands': commands,
                   'public_docs': public_docs,
                   'artifacts': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (sdist, wheel)},
                   'logs': str(run), 'installed_tests': 'full suite, isolated imports',
                   'remote_CI': 'NOT RUN by this local command'}
    except (AssertionError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        receipt = {'status': 'FAIL', 'commands': commands, 'error': type(error).__name__, 'error_message': str(error), 'logs': str(run)}
        (run / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps(receipt, indent=2))
        return 1
    (run / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
