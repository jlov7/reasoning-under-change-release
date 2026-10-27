"""Bounded JSON input and consistent command errors, without echoing input contents."""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import uuid
from pathlib import Path

from .core import ScenarioError


def integer(value, lower, upper, name):
    if type(value) is not int or not lower <= value <= upper:
        raise ScenarioError(f'{name} must be an integer in {lower}..{upper}')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScenarioError('duplicate JSON object key')
        result[key] = value
    return result


def _constant(value):
    raise ScenarioError('nonfinite JSON number')


def load_json(path: Path, limit: int, *, with_bytes=False):
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ScenarioError('input must be a regular file')
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                data = stream.read(limit + 1)
        finally:
            os.close(descriptor)
        if len(data) > limit:
            raise ScenarioError(f'input exceeds {limit} bytes')
        document = json.loads(data.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
        return (document, data) if with_bytes else document
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        if isinstance(error, ScenarioError):
            raise
        raise ScenarioError('cannot read valid UTF-8 JSON input') from None


def error_report(message, *, code='INVALID_INPUT', exit_code=2):
    print(json.dumps({'error': {'code': code, 'message': message},
                      'request_id': uuid.uuid4().hex}), file=sys.stderr)
    return exit_code


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(error_report('invalid command arguments; use --help'))
