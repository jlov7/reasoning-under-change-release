"""Saved-output ownership and recoverable CLI I/O failures."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reasoning_under_change import work


def queue():
    return {'revision': 'io-1', 'capacity': 2, 'completed_ids': [],
            'tasks': [{'id': 'check', 'demand': 1, 'value': 3}]}


class InterruptedFile:
    def __init__(self, stream, failure):
        self.stream, self.failure = stream, failure

    def __enter__(self):
        return self

    def write(self, text):
        if self.failure == 'write':
            self.stream.write(text[:12])
            raise OSError('injected partial write')
        return self.stream.write(text)

    def __exit__(self, *args):
        self.stream.close()
        if self.failure == 'close':
            raise OSError('injected close failure')


class BrokenOutput(io.StringIO):
    def write(self, text):
        raise BrokenPipeError('injected closed downstream pipe')


class FlushFailure(io.StringIO):
    def flush(self):
        raise OSError('injected flush-only failure')


class WorkOutputTests(unittest.TestCase):
    def invoke(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = work.main(args)
        return status, out.getvalue(), err.getvalue()

    def test_interrupted_write_or_close_leaves_no_destination_and_retry_works(self):
        original_open = io.open
        for failure in ('write', 'close'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, target = root/'input.json', root/'plan.json'
                source.write_text(json.dumps(queue()))
                def interrupted(file, mode='r', *args, injected_failure=failure, **kwargs):
                    stream = original_open(file, mode, *args, **kwargs)
                    return InterruptedFile(stream, injected_failure) if mode in ('x', 'w') else stream
                with patch('io.open', side_effect=interrupted):
                    status, out, err = self.invoke(['plan', str(source), '--out', str(target)])
                self.assertEqual(status, 2)
                self.assertEqual(out, '')
                self.assertEqual(json.loads(err)['error']['code'], 'INVALID_INPUT')
                self.assertFalse(target.exists(), 'failed save must not occupy the requested output')
                self.assertEqual(list(root.glob('.ruc-*')), [])
                self.assertEqual(self.invoke(['plan', str(source), '--out', str(target)])[0], 0)
                self.assertEqual(work.check(queue(), json.loads(target.read_text()))['status'], 'VALID')

    def test_broken_stdout_is_structured_and_preserves_a_complete_saved_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root/'input.json', root/'plan.json'
            source.write_text(json.dumps(queue()))
            for args in (['frontier', str(source)], ['plan', str(source), '--out', str(target)]):
                err = io.StringIO()
                with contextlib.redirect_stdout(BrokenOutput()), contextlib.redirect_stderr(err):
                    status = work.main(args)
                self.assertEqual(status, 1)
                response = json.loads(err.getvalue())
                self.assertEqual(response['error']['code'], 'OUTPUT_ERROR')
                self.assertEqual(len(response['request_id']), 32)
                self.assertNotIn(directory, err.getvalue())
            self.assertEqual(work.check(queue(), json.loads(target.read_text()))['status'], 'VALID')

    def test_existing_file_and_dangling_symlink_remain_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root/'input.json', root/'plan.json'
            source.write_text(json.dumps(queue()))
            target.write_bytes(b'keep-existing')
            self.assertEqual(self.invoke(['plan', str(source), '--out', str(target)])[0], 2)
            self.assertEqual(target.read_bytes(), b'keep-existing')
            target.unlink()
            target.symlink_to(root/'absent.json')
            self.assertEqual(self.invoke(['plan', str(source), '--out', str(target)])[0], 2)
            self.assertTrue(target.is_symlink())
            self.assertFalse((root/'absent.json').exists())

    def test_competing_destination_and_link_failure_do_not_remove_other_files(self):
        original_link = work.os.link
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root/'input.json', root/'plan.json'
            source.write_text(json.dumps(queue()))
            def competing(staging, destination):
                Path(destination).write_bytes(b'created-by-another-process')
                return original_link(staging, destination)
            with patch.object(work.os, 'link', side_effect=competing):
                self.assertEqual(self.invoke(['plan', str(source), '--out', str(target)])[0], 2)
            self.assertEqual(target.read_bytes(), b'created-by-another-process')
            self.assertEqual(list(root.glob('.ruc-*')), [])
            target.unlink()
            with patch.object(work.os, 'link', side_effect=OSError('hard links unavailable')):
                self.assertEqual(self.invoke(['plan', str(source), '--out', str(target)])[0], 2)
            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob('.ruc-*')), [])
            self.assertEqual(self.invoke(['plan', str(source), '--out', str(target)])[0], 0)

    def test_post_publication_cleanup_failure_reports_saved_output_explicitly(self):
        original_unlink = Path.unlink
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root/'input.json', root/'plan.json'
            source.write_text(json.dumps(queue()))
            def fail_staging_cleanup(path, *args, **kwargs):
                if path.name.startswith('.ruc-'):
                    raise OSError('injected unlink failure after publication')
                return original_unlink(path, *args, **kwargs)
            with patch.object(Path, 'unlink', fail_staging_cleanup):
                status, out, err = self.invoke(['plan', str(source), '--out', str(target)])
            self.assertEqual(status, 1)
            self.assertEqual(out, '')
            self.assertEqual(json.loads(err)['error']['code'], 'OUTPUT_ERROR')
            self.assertIn('saved', json.loads(err)['error']['message'])
            self.assertEqual(work.check(queue(), json.loads(target.read_text()))['status'], 'VALID')
            staging, = root.glob('.ruc-*')
            self.assertEqual(staging.read_bytes(), target.read_bytes())
            self.assertEqual(staging.stat().st_mode & 0o077, 0)
            staging.unlink()

    def test_flush_only_stdout_failure_and_real_closed_pipe_have_no_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root/'input.json', root/'plan.json'
            source.write_text(json.dumps(queue()))
            err = io.StringIO()
            with contextlib.redirect_stdout(FlushFailure()), contextlib.redirect_stderr(err):
                self.assertEqual(work.main(['frontier', str(source)]), 1)
            self.assertEqual(json.loads(err.getvalue())['error']['code'], 'OUTPUT_ERROR')
            command = [sys.executable, '-m', 'reasoning_under_change.work', 'plan', str(source), '--out', str(target)]
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
                child.stdout.close()
                stderr = child.stderr.read()
                status = child.wait(timeout=10)
            self.assertEqual(status, 1)
            self.assertNotIn('Traceback', stderr)
            self.assertEqual(json.loads(stderr)['error']['code'], 'OUTPUT_ERROR')
            self.assertEqual(work.check(queue(), json.loads(target.read_text()))['status'], 'VALID')
