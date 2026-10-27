# Stability and compatibility

Package 1.0.0 stabilized the bounded work-selection interface. Package 1.1.0 adds
the selection audit below. Package semantic
versions and JSON schema versions are separate. Existing 0.7 work artifacts retain
their schema strings, input normalization, digests and deterministic tie behavior.

## Supported CLI

`ruc-work --help`, `--version`, `validate INPUT`, `plan INPUT --out NEW`,
`check INPUT PLAN`, `replan INPUT PLAN --out NEW`, `counterfactual INPUT TASK_ID`,
`sensitivity INPUT --capacities N ...`, `frontier INPUT`,
`scenarios INPUT PACK --out NEW`, and `history PLAN PLAN ...` are the V1 interface.
`python -m reasoning_under_change.work` is equivalent.

Successful calculations return 0. Malformed input, stale/altered plans, invalid
accounting, existing outputs and incomplete search return 2 with `INVALID_INPUT`.
A completed infeasibility report returns 0 with its explicit status and no
recommendation. A failed work-command stdout or cleanup after completed publication returns 1
with `OUTPUT_ERROR` on
stderr when stderr remains writable; a requested saved output may already exist.
Error objects include a request ID. Do not parse error prose, help layout or random
request IDs as program identifiers. JSON stdout is the machine interface.

## Selection audit added in 1.1

`python -m reasoning_under_change.audit --input INPUT --plan PLAN` is the supported
module CLI. `--help` and `--version` need no files. Optional `--node-limit` is
1..1,048,576 (default 65,536); `--timeout-seconds` is 1..30 (default 1).

`reasoning_under_change.audit.audit_plan(document, submitted, *, node_limit=65536,
timeout_ns=1_000_000_000)` returns `ruc-selection-audit-v1`. The optional clock
hook is test instrumentation outside the supported API. Inputs are not mutated.
Invalid input raises `ScenarioError`; a budget stop returns a report.

Only input digest, selected IDs and total value are audited. Other saved-plan
fields require `work.check`. Feasibility checks required/completed membership,
prerequisites, main demand and named budgets. A `SUBOPTIMAL` report includes a
feasible selection with strictly greater assigned value. Complete enumeration
with no better value returns `OPTIMAL_FOR_INPUT`; alternative tied optima pass.
A node/time stop returns `FEASIBLE_UNPROVEN`, never an optimality claim.

CLI exit 0 means `OPTIMAL_FOR_INPUT`; exit 1 means `SUBOPTIMAL`,
`INFEASIBLE_SELECTION` or `FEASIBLE_UNPROVEN`; invalid inputs return exit 2 with
`INVALID_INPUT` and a request ID on stderr. JSON stdout is the report interface.
The audit shares normalization with `work.validate` but does not call the optimizer
or full-artifact checker. This is algorithmic diversity, not independent
certification. Deadlines are cooperative; accepted 20-task inputs may not exhaust
within the default budget. No work is executed or business value authenticated.

## Supported Python API

All functions below are in `reasoning_under_change.work`. Inputs/outputs are the
JSON-compatible objects described in [Usage](USAGE.md). Functions do not mutate
supplied inputs. `ScenarioError` is importable from `reasoning_under_change`;
its existing `reasoning_under_change.core` path is retained.

| Callable | Contract |
|---|---|
| `validate(document)` | Check schema and required closure feasibility without optimizing |
| `plan(document)` | Produce an exact revision-bound nominal plan |
| `check(document, submitted)` | Recompute and verify the entire saved plan against its input |
| `replan(previous, document)` | Validate a prior plan and revise with immutable completions |
| `counterfactual(document, task_id)` | Force an excluded pending task, or report infeasibility |
| `sensitivity(document, capacities)` | Exact selections at 1..32 explicit capacities |
| `frontier(document)` | Every main-capacity value-improvement threshold, named limits fixed |
| `verify_history(plans)` | Validate 2..32 supplied ordered plan snapshots |
| `scenario_analysis(document, pack)` | Exact minimax additive regret for supplied finite cases |

These functions raise `ScenarioError` for invalid bounded JSON-shaped inputs,
stale/altered artifacts, or incomplete exact calculation. Internal helpers,
solver state, exception message wording and `main`'s embedding behavior are not
supported Python API. Resource and deadline limits remain part of the contract.

## Artifact inventory

| Surface | Schema |
|---|---|
| Work input | Required fields and optional extensions in Usage; no schema marker |
| Input preflight | `ruc-work-validation-v1` |
| Saved plans | `ruc-work-plan-v1` legacy, `-v2` prerequisites/mandatory, `-v3` named budgets |
| Counterfactual | `ruc-work-counterfactual-v1`, `-v2`, `-v3` by the same feature split |
| Sensitivity | `ruc-work-sensitivity-v1`, `-v2`, `-v3` by the same feature split |
| Frontier | `ruc-work-frontier-v1`, or `-v2` with named budgets |
| Selection audit | `ruc-selection-audit-v1` |
| Supplied history check | `ruc-work-history-v1` |
| Scenario pack | `ruc-work-scenarios-v1` |
| Scenario analysis | `ruc-work-scenario-analysis-v1`, or `-v2` with named budgets |
| Optional metadata envelope | `ruc-work-metadata-v1` |
| `check` response | `status`, `input_digest`, `revision`, `total_value`; no schema marker |

An explicit empty resource map still selects named-budget semantics. Explicit
default fields remain identity-bound. Reports/preflight are not saved plans.
Digests bind supplied content; they are not signatures or proof of authentic
ancestry, completion, estimate quality or execution.

## Compatibility policy

Within 1.x, supported command syntax, documented callable parameter names,
existing schema meanings, input normalization/digests and deterministic selection
ties remain compatible. Existing valid saved work plans remain checkable against
their original inputs. A schema's existing fields and meanings are not silently
changed or extended; new report shapes use a new schema. A minor release may add
commands, optional interfaces or new schema versions while retaining existing ones.
Patch releases correct defects and documentation without removing supported valid
behavior. Any correction to previously accepted invalid behavior is described in
release notes. A breaking supported-interface change requires a new major package
version and migration instructions.

Canonical JSON bytes used for input identity remain stable. Whitespace/key layout
of CLI JSON is not an API guarantee, though 1.0 regression tests preserve nine
pre-V1 serialized outputs. Field order in parsed JSON is not significant.

`ruc-scenario`, `ruc-grid`, `ruc-planning` and `ruc-results` are retained research
interfaces. Their fresh output and internal APIs are outside V1 work-artifact
stability. Benchmark timing/host fields and scenario input basenames can vary.
Bundled historical records are immutable and checked by their shipped hashes.
No release changes a negative comparison into a positive one by editing evidence.

CI targets Python 3.11, 3.12 and 3.13 on Ubuntu. Local macOS verification is reported
per release. Windows and other platforms are unqualified. Runtime commands are
offline; installing/building development tooling may require package-index access.
