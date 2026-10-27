"""File-boundary liveness and descriptor validation for supported CLI readers."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reasoning_under_change import ScenarioError, work
from reasoning_under_change.input import load_json


class InputFileTests(unittest.TestCase):
    def test_regular_files_and_symlinks_keep_bytes_and_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root/'queue.json'; alias = root/'alias.json'
            raw = b'{"revision":"files-1","capacity":2,"completed_ids":[],"tasks":[{"id":"a","demand":1,"value":3}]}'
            source.write_bytes(raw); alias.symlink_to(source)
            self.assertEqual(load_json(source, 65536, with_bytes=True), (json.loads(raw), raw))
            self.assertEqual(load_json(alias, 65536, with_bytes=True), (json.loads(raw), raw))
            with self.assertRaises(ScenarioError):
                load_json(root, 65536)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO boundary')
    def test_every_file_input_position_refuses_fifo_and_reaps_owned_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, previous, pack, fifo = [root/name for name in ['queue.json', 'plan.json', 'pack.json', 'private-fifo']]
            document = {'revision': 'files-1', 'capacity': 2, 'completed_ids': [],
                        'tasks': [{'id': 'a', 'demand': 1, 'value': 3}]}
            source.write_text(json.dumps(document)); previous.write_text(json.dumps(work.plan(document)))
            pack.write_text(json.dumps({'schema': 'ruc-work-scenarios-v1', 'scenarios': []}))
            os.mkfifo(fifo)
            cases = [
                ('work', ['validate', fifo]), ('work', ['plan', fifo, '--out', root/'new.json']),
                ('work', ['check', fifo, previous]), ('work', ['check', source, fifo]),
                ('work', ['replan', fifo, previous, '--out', root/'new.json']),
                ('work', ['replan', source, fifo, '--out', root/'new.json']),
                ('work', ['counterfactual', fifo, 'a']),
                ('work', ['sensitivity', fifo, '--capacities', '1']), ('work', ['frontier', fifo]),
                ('work', ['scenarios', fifo, pack, '--out', root/'new.json']),
                ('work', ['scenarios', source, fifo, '--out', root/'new.json']),
                ('work', ['history', fifo, previous]), ('work', ['history', previous, fifo]),
                ('cli', [fifo]), ('results', [fifo])]
            for module, args in cases:
                with self.subTest(module=module, arguments=[str(a) for a in args]):
                    command = [sys.executable, '-m', 'reasoning_under_change.'+module, *map(str, args)]
                    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
                        try:
                            stdout, stderr = child.communicate(timeout=1)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.communicate()
                            self.fail('non-regular input blocked; owned child killed and reaped')
                    self.assertEqual(child.returncode, 2)
                    self.assertEqual(stdout, '')
                    error = json.loads(stderr)
                    self.assertEqual(error['error']['code'], 'INVALID_INPUT')
                    self.assertEqual(len(error['request_id']), 32)
                    self.assertNotIn(directory, stderr)
                    self.assertNotIn('private-fifo', stderr)
                    self.assertFalse((root/'new.json').exists())

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX descriptor race boundary')
    def test_actual_opened_file_is_validated_without_check_open_race(self):
        original_open = os.open
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root/'input.json'; fifo = root/'pipe'
            source.write_text('{"ok":true}'); os.mkfifo(fifo)
            def replace_before_open(path, flags, *args, **kwargs):
                source.unlink(); source.symlink_to(fifo)
                return original_open(path, flags, *args, **kwargs)
            with (
                patch('reasoning_under_change.input.os.open', side_effect=replace_before_open),
                self.assertRaises(ScenarioError),
            ):
                load_json(source, 64)
            source.unlink(); source.write_text('{"ok":true}')
            def replace_after_open(path, flags, *args, **kwargs):
                descriptor = original_open(path, flags, *args, **kwargs)
                source.unlink(); source.symlink_to(fifo)
                return descriptor
            with patch('reasoning_under_change.input.os.open', side_effect=replace_after_open):
                self.assertEqual(load_json(source, 64), {'ok': True})
