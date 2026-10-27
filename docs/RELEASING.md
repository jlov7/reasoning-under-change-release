# Release policy

Use semantic package versions under the [V1 stability policy](STABILITY.md).
Every release needs a clean source checkout, a changelog entry, matching package
metadata/version, and evidence from that exact revision. Keep research fixtures
and historical results unchanged.

For audit performance changes, compare the prior auditor and candidate on the
same declared inputs, selections and budgets. Retain every timing and budget-stop
verdict, including an 18-task case with a one-second budget. Check witnesses and
optimal values against direct small-case enumeration. Report the host, sample
size and timing spread; local timings do not establish a universal speedup.

Run the source suite, exact-pinned lint and installed-package release check:

```sh
PYTHONPATH=src uv run --no-project python -m unittest discover -s tests -v
uvx --from ruff==0.17.1 ruff check --no-cache src tests scripts
uv run --no-project python scripts/verify_release.py
```

The CI matrix runs the source and package gates on Python 3.11 to 3.13 and the
same pinned Ruff version. Record whether each check ran on hosted CI or in a
local clean environment. If hosted execution is unavailable, reproduce the
configured jobs locally and disclose OS, architecture, tool versions and omitted
coverage. Local execution does not establish hosted CI or branch-protection status.

Inspect the sdist/wheel contents, licence, public docs/assets and hashes. The sdist
must include examples, tests, support/stability/release docs and the README SVG;
exclude private plans/handoffs, credentials, caches and local evidence. The release gate installs the produced wheel and directly installs the sdist into
separate new environments, runs both full installed suites and exercises audit
verdicts including independently checked witness constraints. Reinstall
from the produced sdist and wheel, verify isolated imports and run the relevant
first-use, repeated-use and recovery journeys. Check saved artifacts against an
independent small-case oracle, including a competent simple alternative and any
tied/negative results. Report skips, lint/type results and unrun platforms separately.

Distribute only the intended source/history and built artifacts with their
licenses and provenance. Record exact revision, Python/tool versions,
commands/results and package SHA-256 values. Review release assets and actual
rendered README/image links at the distribution destination. Source or docs changes
after verification require the affected checks again. Never treat software gates
as evidence of scientific efficacy, human utility or production safety.
