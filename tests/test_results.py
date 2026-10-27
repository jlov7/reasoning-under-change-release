import json
import unittest
from copy import deepcopy
from importlib.resources import files

from reasoning_under_change.core import ScenarioError
from reasoning_under_change.results import verify


def record(name):
    return json.loads(files('reasoning_under_change').joinpath(f'data/{name}.json').read_text())


class ResultTests(unittest.TestCase):
    def test_all_retained_rows_derive_public_tables(self):
        for name, count in [('changing-grid', 448), ('planning-v1', 720), ('planning-v2', 960)]:
            report = record(name)
            checked = verify(report)
            self.assertEqual(checked['rows'], count)
            self.assertEqual(checked['summary'], report['summary'])
        self.assertEqual(verify(record('changing-grid'))['summary']['reactive']['accepted'], 59)
        self.assertEqual(verify(record('changing-grid'))['summary']['adaptive']['accepted'], 43)

    def test_missing_duplicate_and_extra_rows_fail(self):
        for name in ['changing-grid', 'planning-v1', 'planning-v2']:
            report = record(name)
            for rows in [report['rows'][:-1], report['rows'] + [report['rows'][0]],
                         [report['rows'][0]] + report['rows'][1:-1] + [report['rows'][0]]]:
                bad = deepcopy(report)
                bad['rows'] = rows
                with self.subTest(name=name), self.assertRaises(ScenarioError):
                    verify(bad)

    def test_inconsistent_values_tasks_metadata_and_summaries_fail(self):
        for name in ['changing-grid', 'planning-v1', 'planning-v2']:
            report = record(name)
            bad = deepcopy(report)
            first = next(iter(bad['summary'].values()))
            first[next(iter(first))] = -1
            with self.assertRaises(ScenarioError):
                verify(bad)
            for key, value in ([('accepted', True), ('elapsed_ticks', -1)] if name == 'changing-grid'
                               else [('reward', 0), ('task_sha256', 'a' * 64), ('cpu_ns', True),
                                     ('stop_reason', 'UNKNOWN'), ('expansions', 999999)]):
                bad = deepcopy(report)
                bad['rows'][0][key] = value
                with self.subTest(name=name, key=key), self.assertRaises(ScenarioError):
                    verify(bad)
            bad = deepcopy(report)
            if name == 'changing-grid':
                bad['schedules'][0]['events'] = []
            else:
                bad['tasks'][0]['observation']['capacity'] = 0
            with self.assertRaises(ScenarioError):
                verify(bad)

    def test_error_rows_stay_in_denominator_and_cannot_claim_reward(self):
        report = record('planning-v2')
        row = report['rows'][0]
        for key in ['reward', 'gap', 'optimum', 'job_ids', 'expansions', 'stop_reason']:
            row.pop(key)
        row.update(status='ERROR', error_type='RuntimeError')
        report.pop('summary')
        checked = verify(report)
        self.assertEqual(checked['summary']['exposed/exact_dp']['errors'], 1)
        self.assertEqual(checked['summary']['exposed/exact_dp']['rows'], 240)
        row['reward'] = 100
        with self.assertRaises(ScenarioError):
            verify(report)

    def test_planning_claim_boundary_and_environment_are_required(self):
        for name in ['planning-v1', 'planning-v2']:
            for key in ['scope', 'budget_note', 'environment']:
                report = record(name)
                report.pop(key)
                with self.subTest(name=name, key=key), self.assertRaises(ScenarioError):
                    verify(report)

    def test_bundled_verifier_rejects_incomplete_hash_manifest_and_corruption(self):
        import contextlib
        import io
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from reasoning_under_change import results

        original = files('reasoning_under_change').joinpath('data')
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            data = package / 'data'
            data.mkdir()
            for resource in original.iterdir():
                if resource.name.endswith('.json'):
                    (data / resource.name).write_bytes(resource.read_bytes())
            manifest = (data / 'SHA256.json').read_bytes()
            for corruption in ['missing_manifest_entries', 'changed_bytes']:
                (data / 'SHA256.json').write_bytes(manifest)
                if corruption == 'missing_manifest_entries':
                    (data / 'SHA256.json').write_text('{}')
                else:
                    (data / 'supply_change.json').write_text('{}')
                out, err = io.StringIO(), io.StringIO()
                with patch('sys.argv', ['ruc-results']), patch.object(results, 'files', return_value=package), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    status = results.main()
                self.assertEqual(status, 2)
                self.assertEqual(out.getvalue(), '')
                self.assertEqual(json.loads(err.getvalue())['error']['code'], 'INVALID_INPUT')

    def test_planning_manifest_requires_an_object(self):
        for value in [None, [], '', 1, True]:
            with self.subTest(value=value), self.assertRaises(ScenarioError):
                verify({'manifest': value})

    def test_invalid_manifest_cli_returns_structured_input_error(self):
        import contextlib
        import io
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from reasoning_under_change import results

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.json'
            path.write_text('{"manifest":null}')
            out, err = io.StringIO(), io.StringIO()
            with patch('sys.argv', ['ruc-results', str(path)]), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = results.main()
            self.assertEqual(status, 2)
            self.assertEqual(out.getvalue(), '')
            self.assertEqual(json.loads(err.getvalue())['error']['code'], 'INVALID_INPUT')
            self.assertRegex(json.loads(err.getvalue())['request_id'], r'^[0-9a-f]{32}$')

    def test_exhausted_exact_solvers_require_optimal_reward_and_completed_work(self):
        for name, arms in [('planning-v1', ['search_full', 'search_short']),
                           ('planning-v2', ['exact_dp', 'search_full', 'search_short'])]:
            for arm in arms:
                for mutation in ['suboptimal', 'zero_work']:
                    report = record(name)
                    report.pop('summary')
                    row = next(row for row in report['rows'] if row['arm'] == arm and row['gap'] == 0)
                    row['stop_reason'] = 'EXHAUSTED'
                    if mutation == 'suboptimal':
                        row.update(job_ids=[], reward=0, gap=row['optimum'])
                    else:
                        row['expansions'] = 0
                    with self.subTest(name=name, arm=arm, mutation=mutation), self.assertRaises(ScenarioError):
                        verify(report)
        report = record('planning-v2')
        report.pop('summary')
        report['rows'][0]['expansions'] -= 1
        with self.assertRaises(ScenarioError):
            verify(report)

    def test_limit_stop_reasons_require_the_configured_work_ceiling(self):
        for arm, reason in [('exact_dp', 'UPDATE_LIMIT'), ('search_full', 'NODE_LIMIT'),
                            ('search_short', 'NODE_LIMIT')]:
            report = record('planning-v2')
            report.pop('summary')
            row = next(row for row in report['rows'] if row['arm'] == arm)
            row.update(stop_reason=reason, expansions=0)
            with self.subTest(arm=arm), self.assertRaises(ScenarioError):
                verify(report)
        report = record('planning-v2')
        report.pop('summary')
        row = report['rows'][0]
        row.update(stop_reason='UPDATE_LIMIT', expansions=row['node_limit'])
        with self.assertRaises(ScenarioError):
            verify(report)

    def test_zero_work_timeouts_remain_valid_and_in_the_census(self):
        report = record('planning-v2')
        report.pop('summary')
        for arm in ['exact_dp', 'greedy', 'search_full', 'search_short']:
            row = next(row for row in report['rows'] if row['arm'] == arm)
            row.update(stop_reason='TIMEOUT', expansions=0)
        checked = verify(report)
        self.assertEqual(checked['rows'], 960)
        for arm in ['exact_dp', 'greedy', 'search_full', 'search_short']:
            self.assertEqual(checked['summary'][f'exposed/{arm}']['timeouts'], 1)
            self.assertEqual(checked['summary'][f'exposed/{arm}']['rows'], 240)
