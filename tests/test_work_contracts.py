"""Public V1 call surface and pre-V1 deterministic artifact compatibility."""
import contextlib
import hashlib
import inspect
import io
import json
import unittest
from pathlib import Path

import reasoning_under_change
from reasoning_under_change import work
from reasoning_under_change.core import ScenarioError

ROOT = Path(__file__).resolve().parents[1]
LEGACY_HASHES = {'plan-0': 'dd8a580e15c654dc20e012cafdd876ffb6041c911f39a1e92eed2f830bbc0dda', 'plan-1': 'ef36d80e522e1cc2d3a9e291ff5f8a12fd1ebeda259d43a771a6d14610e53b7b', 'plan-2': 'a549d892f6fec2a52609c4a3c80f85ecf6cf2afae62f121c57135a3efb856231', 'sensitivity-0': '481d841626195942274ebfa16606af72ec2f0fba6321640e4611ab9738a8e311', 'frontier-0': 'cabe1f64131400d3cc17b07da04e0f650bd59f4e32af3c3246d5e14b9e28e52a', 'sensitivity-1': '8e5272b92e48c32d47749600ed599ebb7209fa6dd7cfa254fe7d521349aaf4b7', 'frontier-1': '5c35d8c983c8070129aa7106adf3dea78803cbee68c661e33575662f3bfd4ab0', 'sensitivity-2': '4e6f1b0daecc556dfddcc2dc397f6c00cc4da3870a869588742ee0af8c88ea36', 'frontier-2': '9c6f7ad167b8af7a3f52babd51a198a72e907338ddb0f6f708898f2602d5a348'}


class WorkContractTests(unittest.TestCase):
    def test_v1_package_exception_and_cli_version(self):
        self.assertEqual(reasoning_under_change.__version__, '1.1.1')
        self.assertIs(reasoning_under_change.ScenarioError, ScenarioError)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            work.main(['--version'])
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(output.getvalue(), '1.1.1\n')

    def test_documented_api_parameter_names_remain_callable(self):
        signatures = {'plan': ['document'], 'check': ['document', 'submitted'],
                      'replan': ['previous', 'document'], 'validate': ['document'],
                      'counterfactual': ['document', 'task_id'], 'sensitivity': ['document', 'capacities'],
                      'frontier': ['document'], 'verify_history': ['plans'],
                      'scenario_analysis': ['document', 'pack']}
        for name, parameters in signatures.items():
            with self.subTest(function=name):
                self.assertEqual(list(inspect.signature(getattr(work, name)).parameters), parameters)

    def test_pre_v1_plan_sensitivity_and_frontier_bytes_remain_compatible(self):
        documents = [json.loads((ROOT/'examples'/name).read_text()) for name in
                     ['work_queue.json', 'work_prerequisites.json', 'work_resources.json']]
        for index, document in enumerate(documents):
            outputs = {f'plan-{index}': work.plan(document),
                       f'sensitivity-{index}': work.sensitivity(document, [0, 4, 8, 16]),
                       f'frontier-{index}': work.frontier(document)}
            for name, value in outputs.items():
                encoded = (json.dumps(value, sort_keys=True, indent=2)+'\n').encode()
                with self.subTest(contract=name):
                    self.assertEqual(hashlib.sha256(encoded).hexdigest(), LEGACY_HASHES[name])
