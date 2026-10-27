# Contributing

Keep changes small and specific. For a bug, include a failure-reproducing test and
verify that it fails for the expected reason before applying the fix. Preserve
invalid-input recovery, deterministic ties, saved-plan compatibility and bounded
fail-closed behavior. Add independent small-case enumeration for solver changes;
include a competent simple baseline and retain ties or negative results.

Run the source suite, pinned lint and installed-package gates in
[Releasing](docs/RELEASING.md). Do not weaken tests or broad lint rules to pass.
The two local `BLE001` exemptions in `planning_benchmark.py` intentionally retain
planner/evaluator failures as ERROR rows, including timing and denominator. Their
regressions must continue to pass. Runtime dependencies remain empty.

Describe the concrete problem, changed behavior, verification and remaining limits.
A supported-interface change must follow [Stability](docs/STABILITY.md). Use synthetic
examples; do not commit confidential inputs/logs, personal data, credentials or
material without redistribution rights. Original project code is MIT-licensed.
