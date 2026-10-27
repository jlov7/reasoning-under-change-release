# Usage

Install from the checkout, extracted sdist or a built wheel with `uv pip install`.
The console commands and their `python -m reasoning_under_change.<module>`
equivalents use the same implementation:

| Console command | Module | Job |
|---|---|---|
| `ruc-scenario --example` | `cli` | Four simple refresh rules on the packaged example |
| `ruc-grid` | `experiment` | Seven-policy complete changing grid, JSON stdout |
| `ruc-planning --v2` | `planning_benchmark` | Four-arm exposed static-planning diagnostic |
| `ruc-work validate/plan/check/replan` | `work` | Select and revise user work under total capacity and optional named budgets |
| `ruc-work counterfactual/sensitivity/history` | `work` | Inspect exclusion costs, capacities and supplied history offline |
| `python -m reasoning_under_change.audit --input INPUT --plan PLAN` | `audit` | Challenge a supplied selection with a separate bounded exhaustive algorithm |
| `ruc-results [record.json]` | `results` | Verify one complete record, or all bundled records |

The stable V1 work interface and retained research commands have different compatibility
boundaries. See [Stability](STABILITY.md) for callable names, schema versions and support.
`ruc-work --version` and `python -m reasoning_under_change.audit --version` print the installed package version.
The [selection audit contract](STABILITY.md#selection-audit-added-in-11) defines its
report scope, budgets, verdicts and exit codes.

File inputs must resolve to regular files. Symlinks to regular files remain accepted.
The reader opens a nonblocking descriptor and validates that actual descriptor
before reading; FIFOs, directories and devices are refused without waiting for
content or creating output. File size/UTF-8/JSON checks still apply.

## Scenario input

Create `my-scenario.json` with this synthetic delayed build-job example:

```json
{
  "job_id": "build-cache",
  "deadline": 10,
  "capacity": 1,
  "delay": 3,
  "ttl": 2,
  "events": [
    {"at": 2, "entity": "capacity", "kind": "capacity", "value": 0}
  ]
}
```

Run `.venv/bin/ruc-scenario my-scenario.json`. Each policy starts from its own
copy of this scenario. The proposal consumes one capacity unit. `delay` advances
the simulated clock between its initial observation and attempted action;
it does not run a planner or model. The output identifies file input as
`user-supplied scenario`; only `--example` is labelled a packaged authored fixture.

| Input | `never` | `relevant` | Interpretation |
|---|---|---|---|
| As above: delay 3, capacity falls at tick 2 | `REJECT_STALE_STATE`, 0 refreshes | `NO_VALID_ACTION`, 1 refresh | Refresh discovers lost capacity; it cannot rescue the job. |
| Change only delay to 0 | `ACCEPTED`, 0 refreshes | `ACCEPTED`, 0 refreshes | The action happens before the capacity loss. |
| Restore delay 3; replace the event with `{"at":2,"entity":"unrelated","kind":"noise","value":null}` | `ACCEPTED`, 0 refreshes | `ACCEPTED`, 0 refreshes | Irrelevant change does not require refresh; `ttl` and `always` each refresh once. |

`NO_PROPOSAL` means the job was already unavailable at the initial observation.
`NO_VALID_ACTION` means refresh found no feasible proposal. `REJECT_EXPIRED`
means the attempted proposal was past its deadline. These are simulated outcomes,
not recommendations learned from your workload. A direct feasibility check
immediately before action is a useful simple baseline; this example's refresh
is free and uses that same check. Event notices are also instantaneous and free.

This command accepts custom single-job scenarios. `ruc-grid` and `ruc-planning`
rerun fixed authored protocol tasks; they do not accept custom task files.
`ruc-results` validates those complete benchmark records, not scenario output.
Static packing and changing-job experiments are separate; the toolkit does not
measure how a custom planner's computation affects a changing environment.

`ruc-scenario examples/supply_change.json` reads at most 1 MiB of UTF-8 JSON.
The object requires exactly `job_id`, `deadline`, `capacity`, `delay`, `events`,
and optionally `ttl`. Duplicate JSON keys and nonfinite numbers are rejected.
`job_id` is a string of 1 to 64 characters. Numeric counts must be integers;
bools and floats are rejected. Initial deadline is 1..1,000,000, capacity and
delay are 0..1,000,000, and TTL is 1..1,000,000 (default 2).

`events` contains at most 10,000 objects, each with exactly `at`, `entity`,
`kind`, `value`. Event times are integers in 0..1,000,000 in nondecreasing
order. Equal-time events apply in supplied order. Capacity events target
`capacity` with an integer value in 0..1,000,000. Deadline events target
`job:<job_id>` with an integer value in 0..1,000,000. Close events target that
job with a boolean (`true` closes, `false` reopens). Noise events target
`unrelated`; their value is inert JSON. All future events are checked before
any execution. The core `World` API applies the same count/event bounds.

The engine only advances through supplied events and bounded operations.
The grid caps its horizon at 1,000,000 ticks, rounds at 10,000 and seeds at
1,000; the published run fixes 32 seeds. Planning accepts at most 20 jobs,
capacity/demand at most 128 and reward at most 10,000. Its cooperative timeout
is at most one second and work cap at most 65,536 units; published settings
are smaller. These are process bounds, not real-time guarantees.

## Results and errors

The reader accepts at most 8 MiB per file. It requires a complete published
protocol census, checks tasks/schedules, row identity, counts, splits, budgets,
quality and supplied summaries. Planning selection feasibility/reward/optimum
are independently evaluated. Exhausted exact solvers must report zero reward gap
and completed work; work-limit stops must consume their configured ceiling. DP
work cannot exceed the task's cell-update count. These checks do not authenticate
actual execution or timing. A supplied summary must agree with all rows;
if absent it is derived. ERROR rows retain their timing and denominator and
cannot claim reward. Fresh partial-seed runs are intentionally rejected.
Bundled files are also checked against `data/SHA256.json`.

Commands write results to stdout. Scenario/result input or argument failures
return exit code 2 with one JSON object on stderr:

```json
{"error":{"code":"INVALID_INPUT","message":"..."},"request_id":"<32 hexadecimal characters>"}
```

No input contents or absolute input paths are echoed in those errors. Successful
commands return 0; `--help` prints usage and returns 0. Planning reports retain
individual planner ERROR rows instead of dropping failed attempts. These rows
do not by themselves make the benchmark process fail.

The simulator and benchmark commands do not create or replace result files.
`ruc-work` requires a new `--out` file for plan/replan and refuses existing paths. Shell redirection (`>`) replaces
its target, so choose a new filename when preserving an earlier run. Input
files are never modified. No installed runtime command starts a server or makes a network call. Installation
and development/release tooling can acquire packages from an index.

Work-command stdout I/O failures or post-publication staging-cleanup failures
return exit 1 with `OUTPUT_ERROR` on writable
stderr. The cleanup-failure message states that the output was saved completely.
A saved output may also have been published before stdout fails; inspect
and check that file before retrying. Other documented work failures retain exit 2
and `INVALID_INPUT`. A saved file is completely written/closed in a unique sibling
staging file, then published with a no-replacement hard link. Existing files and
symlinks are refused, including a destination created during publication. Only this
invocation's staging path is cleaned up. Hard-link filesystem support is required;
crash durability and hostile local filesystem writers are outside the contract.

## User work planning

`ruc-work` accepts arbitrary bounded user JSON, separate from the fixed research
benchmarks. A work object requires `revision`, `capacity`, `tasks`,
`completed_ids`, with optional `metadata` and `resource_limits`. Each task requires `id`, `demand`,
`value`, with optional `requires`, `mandatory` and `resources`. The examples contain invented
maintenance work, not measured durations or utilities. Supply your
own estimates and priorities. Demand uses one consistent common unit; each named
resource has its own unit and limit. Value is additive. Jobs are indivisible.
Without requires/mandatory they remain independent and optional. Deadline,
command and unrecognized resource fields are rejected.

Task IDs and revisions contain 1..64 ASCII letters, digits, underscores, hyphens
or dots, starting with a letter or digit. IDs are unique. At most 20 tasks are
accepted. Demand is 1..128 and total capacity 0..128. Legacy value is 1..10,000;
explicitly extended inputs allow value 0..10,000. All numeric
fields must be integers; booleans, floats, nonfinite values, duplicate JSON keys
and unknown fields fail. Input files are limited to 64 KiB, plan files to
128 KiB. Python APIs apply the same schema/count bounds.

`completed_ids` is a unique list of existing task IDs. On initial planning it is
a user assertion of previously completed work. On replan it must retain prior
completions and may add only tasks selected by the previous checked plan. Every
completed task must remain present with its entire definition unchanged. Total capacity
includes completed demand. If it falls below completed demand, replan fails;
choose a capacity consistent with this accounting window. Pending tasks may be
added, removed or edited. A changed revision is required for every replan.
Completion is reported by the user, never inferred or executed.

```python
import json
from reasoning_under_change.work import plan, check, replan

work = json.loads(open("examples/work_queue.json").read())
initial = plan(work)
assert check(work, initial)["status"] == "VALID"
updated = json.loads(open("examples/work_queue_changed.json").read())
revised = replan(initial, updated)
assert revised["completed_ids"] == ["lint"]
```

`plan` maximizes selected total value, including the fixed completed tasks. Legacy
inputs reuse the bounded exact DP, with at most 2,560 candidate cell updates and a
one-second cooperative timeout. Incomplete search fails without producing a
plan. Tasks are sorted by ID before solving; equal-value alternatives retain
the solver's first incumbent. Thus task order does not change the result. It
does not optimize for retaining pending jobs, minimal demand, or fewer jobs.

### Input preflight

Run `ruc-work validate my-work.json` or call `validate(document)` from
`reasoning_under_change.work` before saving a selection. It uses the same strict
input rules and digest as `plan`, without calling the optimizer or creating a file.
The `ruc-work-validation-v1` report includes normalized `input`, `input_digest`,
`task_count`, `completed_ids`, and `required_ids` (the closure of completed plus
mandatory work). `completed_capacity_used` and `completed_resources_used` describe
asserted completions. `required_capacity_used`, `required_capacity_slack`,
`required_resources_used` and `required_resource_slack` describe the entire required
closure, counting shared prerequisites and completed tasks once. Resource maps are
empty for a resource-free input.

`status: FEASIBLE` means that closure fits every limit. The closure itself is a
feasible subset; the report neither optimizes nor includes `selected_ids`. Optional
tasks that do not fit are permitted. Negative required slack produces a completed
`INFEASIBLE` report, with exit 0 and no recommendation. Malformed schema, cycles,
missing completed prerequisites or limits below completed usage are invalid inputs,
returning exit 2. Correct them and repeat preflight. Preflight reports are not saved
plans and are rejected by `check`, `replan` and `history`.

### Named resource budgets

Version 0.7.0 accepts this optional extension to the same work input:

```json
{
  "revision": "engineering-1", "capacity": 8, "completed_ids": [],
  "resource_limits": {"engineering": 5, "review": 3},
  "tasks": [
    {"id": "baseline", "demand": 1, "value": 0, "resources": {"engineering": 1}},
    {"id": "fix", "demand": 3, "value": 11, "requires": ["baseline"],
     "resources": {"engineering": 2, "review": 1}}
  ]
}
```

`resource_limits` is an object containing zero to four names with the task-ID
identifier rules. Each limit and task usage is an integer 0..128; booleans and
floats fail. A task's optional `resources` object contains only names declared
in `resource_limits`. Even an explicit empty `resources` object requires an explicit
`resource_limits` declaration. Omitted task usage is zero. Empty maps and
explicit zeroes remain explicit, identity-bound content. Normalization sorts map
keys and copies supplied maps; it never inserts defaults. Unknown names fail,
including a misspelled name with usage zero. These budgets are additional to
the original integer capacity/demand, which keeps its existing bounds.

Presence of `resource_limits`, including `{}`, selects the extended input rules
(value 0..10,000), `ruc-work-plan-v3` and solver `exact_resources`. Plans include
`resources_used` and `resource_slack`, each keyed by every declared resource,
plus the v2 prerequisite order. Values are maximized subject to every budget;
unlike units are never added or converted to a weighted score. Equal values
retain the include-first topological tie rule. Named usage is not a new tie
objective. Without resource fields, existing normalization, identities, plan
bytes and tie behavior are unchanged.

Completed tasks consume all of their resources once. Their complete definitions,
including explicit resource maps, remain immutable in replan/history. Limits may
change on a new revision, but must still cover completed usage. Mandatory and
forced work includes all prerequisites and cannot exceed any limit. `plan` and
`replan` refuse infeasible required work; counterfactuals mark forced infeasibility.
Input and plan digests bind named definitions and limits, so a changed limit or
usage invalidates a saved plan even when its selection would still fit.

Counterfactual v3 reports chosen/forced `resources_used` and `resource_slack`
with `chosen_`/`forced_` prefixes; forced metrics are null if infeasible.
Sensitivity v3 points report usage/slack maps, or null maps if infeasible.
Frontier v2 points report usage/slack maps and vary only the original capacity
0..128, keeping every named limit fixed. They do not enumerate a multidimensional
Pareto frontier. The input's declared accounting capacity must still be valid.
Scenario analysis v2 reports named usage/slack for its recommendation, individual
optima and per-case evaluations. The existing v1 scenario-pack schema continues
to permit only pending demand/value and main-capacity overrides. Named limits
and task profiles are fixed across all cases and enforced in every case.
Scenario ties still use main demand followed by task IDs; resource use adds no
weighted or lexicographic objective. Reports remain separate from saved plans.

The exact DP merges states only when their main demand, entire named usage
vector and future prerequisite availability match. It retains the existing
2,097,151-node table ceiling and shared one-second cooperative deadline. Scenario
analysis retains its two-pass 2,097,152-visit bound and same shared deadline.
Several resources can retain many more states than one resource. Bounds admit
inputs without guaranteeing every maximum-size request finishes. Exhaustion is
required; timeout/node-limit errors produce no partial exact result.

`examples/work_resources.json` and `examples/work_resources_changed.json` contain
an invented engineering/review queue. Use each budget for a clearly declared
accounting window and unit. This computes selections from estimates, without
assigning people, times or concurrent work. Realized value and human utility
remain NOT RUN.

### Prerequisites and mandatory work

Version 0.4.0 adds two optional task fields:

```json
{"id":"smoke","demand":2,"value":4,"requires":["baseline"],"mandatory":true}
```

`requires` defaults to `[]` and accepts at most 19 unique existing task IDs.
Self-dependencies, unknown IDs and cycles fail. `mandatory` defaults to `false`
and accepts only a JSON boolean. Selecting a task selects every transitive
prerequisite. Completed work, mandatory work and a counterfactually forced task
are fixed together with their prerequisites. Shared prerequisites count once.
Completed tasks must have all prerequisites in `completed_ids`; selection alone
does not assert completion. Initial completion remains an unverified user assertion.

A document is extended when it declares named budgets or any task explicitly contains either prerequisite field,
including `requires: []` or `mandatory: false`. This permits value zero throughout
that document for enabling work without standalone assigned value. Without named
budgets, an empty task list remains legacy. Normalization sorts task and prerequisite IDs, preserves
explicit fields, and does not insert omitted defaults. Adding an explicit default
therefore changes input identity. Across replans even a completed task's explicit
fields must remain unchanged. A legacy plan can precede an extended plan if this
rule and the existing completion rules hold.

Prerequisite-only extended plans use `ruc-work-plan-v2`, solver `exact_dependencies` and the existing
fields plus `pending_order`. This order repeatedly chooses the smallest ready ID
after prerequisites, then removes completed IDs. `pending_ids` stays ID-sorted.
The order is descriptive; no start times, deadlines, parallel execution or actual
job completion are established. Tasks and prerequisite-list order do not change
the plan. Equal-value selections retain the first incumbent of include-first
topological search, without minimizing demand, task count or change from a prior plan.

The exact dependency-state DP visits at most 2,097,151 partial selections per
table (the complete binary tree for 20 decisions). It merges selections only
when used demand and selected prerequisites still needed by future tasks match.
Their future feasible decisions are identical, so greatest value dominates;
equal value retains the include-first choice. It shares the existing one-second
cooperative deadline with every solve in a report, check, replan or history.
Incomplete search raises an error without any partial exact result. The bounds
do not promise every maximum-size request finishes within the deadline.
No new dependency or model is used.

`plan` and `replan` refuse when mandatory work and its prerequisite closure
cannot fit. `sensitivity` reports such capacities as `INFEASIBLE`, even if the
declared input capacity also cannot fit mandatory work. The original rule that
declared capacity must cover completed demand remains. Counterfactuals require a
feasible base plan, then force the excluded task with every prerequisite and
mandatory task; a closure that cannot fit returns `INFEASIBLE`. Extended
counterfactual and sensitivity reports use their corresponding `v2` schema names.
`check` recomputes all fields, including pending order. History retains its v1
envelope and accepts a consistent mixture of v1 and v2 plans.

`examples/work_prerequisites.json` and `examples/work_prerequisites_changed.json`
provide the 14-task authored maintenance example in the README. The unchanged
`work_queue` examples exercise legacy v1 inputs and plans.

Plans include normalized input, its revision-bound SHA-256, selected/pending/
completed IDs, total demand and value. Replans also carry the immediate previous
input snapshot and digest, retained/added/dropped IDs, prior/current capacity,
prior total value and value delta. All pending choices
are recomputed under the updated input; retained IDs include completed work.
`check` recomputes the selection and totals, rejects stale input digests and
modified output, and checks change accounting against the previous snapshot.
Digests detect inconsistency, not authorship or authenticity: someone who can
replace both inputs and plans can create a consistent alternate history.
Replan retains only one predecessor snapshot, not an audit log.

CLI examples:

```sh
ruc-work plan my-work.json --out plan-1.json
ruc-work check my-work.json plan-1.json
ruc-work replan my-updated-work.json plan-1.json --out plan-2.json
```

Plan/replan print the result and create the named new file with exclusive
creation. They never replace an existing file or follow an existing output
symlink. Check prints a small validation report. Failures return 2 with the same
structured error contract above, without echoing filenames or task contents.
Inputs are never modified. API failures raise `ScenarioError`. No commands,
providers, services, models or clocks of an external scheduler are controlled.
Static optimality concerns your supplied numbers only. Duration accuracy,
operational utility, live scheduling and model/adaptive improvements are NOT RUN.

## Work reports and metadata

The optional metadata envelope is strict and versioned:

```json
{"schema":"ruc-work-metadata-v1","resource_unit":"CPU minutes","value_basis":"Estimated relative priorities, not measured utility."}
```

`resource_unit` is nonblank text of at most 80 characters; `value_basis` is
nonblank text of at most 512 characters. Both are required inside this envelope.
Unknown fields and versions fail. Text is preserved as inert data, never
executed or interpreted by the solver. The envelope is included in normalized
input and its digest, so editing either declaration stales an old plan. It has
no effect on demand/value arithmetic or selected IDs. Omitting metadata in a
legacy input keeps the original input and `ruc-work-plan-v1` format and meaning.

```sh
ruc-work counterfactual my-work.json excluded-task-id
ruc-work sensitivity my-work.json --capacities 0 5 9 10 12
ruc-work frontier my-work.json
ruc-work history plan-1.json plan-2.json plan-3.json
```

`counterfactual` requires one existing excluded pending task. It solves the
same problem while fixing completed and mandatory work and forcing that task
with its prerequisites. The report
contains `chosen_ids`, `chosen_total_value`, `forced_ids`, `forced_total_value`,
`sacrificed_value` (chosen minus forced value), and `replaced_ids` (chosen IDs
absent from the forced selection). If fixed demand exceeds capacity, status
is `INFEASIBLE`, forced IDs are empty and forced/sacrificed values are null.
It does not invent a partial selection or provide a model-generated rationale.
Tied alternatives may replace IDs at zero sacrificed value.

`sensitivity` takes 1..32 explicit unique integer capacities in 0..128, in
the requested order. One shared exact table covers all requested capacities
with the same completed work, including
points below completed demand as `INFEASIBLE` with null totals and empty IDs.
Each feasible point reports its optimal total value, selected IDs and demand.
`added_ids` and `dropped_ids` compare adjacent feasible points; these lists
are empty at the first point and after an infeasible point. The report includes
the original input and digest; it does not change its capacity or revision.
Only an ascending capacity list has a nondecreasing optimum-value guarantee.
The input itself must still be a valid work object at its declared capacity.

### Explicit finite scenarios

Version 0.6.0 adds a separate decision report:

```sh
ruc-work scenarios examples/work_prerequisites_changed.json examples/work_scenarios.json --out scenario-report.json
```

The Python API is `scenario_analysis(document, pack)` from
`reasoning_under_change.work`. A compact pack has this strict shape:

```json
{
  "schema": "ruc-work-scenarios-v1",
  "scenarios": [
    {"id": "less-availability", "capacity": 14},
    {"id": "search-complexity", "overrides": [
      {"id": "search-index", "demand": 3},
      {"id": "search-fix", "demand": 7, "value": 22}
    ]}
  ]
}
```

The unchanged input is always included as `nominal`; that ID is reserved. Supply
zero to seven additional unique safe IDs with the same identifier rules as tasks.
Each scenario requires `id` and permits only `capacity` and `overrides`.
Omitted fields inherit the nominal input. An override requires an existing
pending task ID and at least one of `demand`, `value`. Duplicate task overrides
are rejected. Completed definitions cannot be overridden, even with equal values.
Task IDs, prerequisites, mandatory flags, named limits/resource profiles, metadata and completion assertions stay
fixed across the cases. Capacity below completed demand is invalid accounting
input. Other required work that cannot fit is a valid infeasible case.
All existing integer, task-count and schema bounds apply. Each CLI input file is
limited to 64 KiB. Unknown fields, probabilities, booleans in integer fields,
duplicate JSON keys and nonfinite numbers are rejected.

The report minimizes **maximum additive regret** across exactly the supplied
cases: each regret is that case's own best feasible total value minus the common
selection's value in that case. Individual optima are not restricted to common
feasible choices. Changes within a case stay correlated; no extra combinations,
intervals, likelihoods or unseen cases are inferred. Values must be comparable
additive estimates, including any completed value once. Their accuracy is not
established by the calculation.

Ties in the common recommendation prefer greater minimum scenario value, then
greater nominal value, then lower worst-case used demand, then lexicographically
smaller sorted selected task-ID lists. Each individual optimum maximizes its own
value, then minimizes used demand, then uses the same task-ID tie rule. Task and
scenario input ordering do not affect results. The nominal comparator preserves
the existing `plan` selection and its original tie behavior, which can differ
from the scenario optimum on equal value.

The `ruc-work-scenario-analysis-v1` report includes the normalized base input and
digest, canonical pack and a digest binding that pack to the base input, and each
case's effective input, input digest and ID-bound scenario digest. Digests prove
supplied-content consistency, not authenticity. It reports required/completed
IDs, recommended pending IDs in prerequisite order, each individual optimum,
and recommended demand/value/slack/regret in every case. The nominal comparator
reports feasibility, demand/value and signed slack (a negative value is its
capacity deficit). Its regret is `null` when infeasible, never a negative or
misleading finite regret for an invalid choice.

If any scenario has no feasible required closure, the report has status
`NO_COMMON_FEASIBLE`, `recommended: null`, and null recommendation metrics. The
infeasible case has `optimum: null`. A feasible nominal comparator and feasible
individual optima may still be reported. The CLI exits 0 for this complete exact
refusal report, distinct from exit 2 for malformed input, invalid accounting,
timeout or node-limit errors. Errors write no report or partial exact output.
Saved outputs use exclusive creation; choose a fresh path on recomputation.

All exact subset passes and the nominal comparison share one cooperative
one-second deadline. The new analysis has a separate 2,097,152-visit cap, counting
both passes over at most 2^20 subsets. Fixed required work reduces the optional
subset count. Legacy solver limits are unchanged. A maximum-sized input can time
out, especially with many scenarios; bounds admit inputs without promising that
every one finishes. No partial or approximate recommendation is returned.

This report is a read-only choice aid, distinct from the v1/v2/v3 nominal plan
formats. `check`, `replan` and `history` reject it. It executes no tasks and assigns
no start times or concurrency. Recompute from updated estimates instead of
claiming that a previous scenario decision remains feasible. Production safety,
realized values, estimate calibration and human time savings are NOT RUN.

### Complete budget frontier

Version 0.5.0 adds `ruc-work frontier my-work.json` and `frontier(document)`.
The `ruc-work-frontier-v1` report includes normalized input and its original
revision-bound digest, `maximum_capacity: 128`, `completed_ids` and
`required_ids` (the prerequisite closure of completed and mandatory work).
It computes one exact table covering total capacities 0..128. The declared
capacity remains part of input identity, rather than limiting this report's
range. It must cover completed demand as in every other work input.

`points` contains the first feasible capacity, then only capacities where the
optimal total value strictly increases. Each point reports `minimum_capacity`,
`capacity_used`, `total_value`, `selected_ids`, ID-sorted `pending_ids` and
prerequisite-first `pending_order`. Values include completed work. The first
point can have zero value, and an empty queue has one point at capacity zero.
A required closure exceeding 128 returns `status: INFEASIBLE` and empty points;
otherwise status is `FEASIBLE`. A required closure exceeding only the declared
capacity can still have feasible points at larger capacities.

Each point's selection uses the existing optimal tie rule at that minimum
capacity. An equal-value alternative may appear at a larger capacity without
adding a point. Use sensitivity to inspect those exact selections. The frontier
describes assigned value versus one supplied resource, not a timed schedule,
predicted utility or recommendation of which budget to buy. It creates no files,
changes no revision and executes no work. It does not prove an advantage over
a greedy baseline, meaningful priorities or human usefulness.

### Supplied history

`history` accepts 2..32 ordered existing plan files, each at most 128 KiB. A
complete supplied history must start with an initial plan without a predecessor.
It checks every plan, unique revision IDs, and every adjacent predecessor input
snapshot/digest and completion transition. Missing middle, swapped, forked or
modified supplied links fail. It prints revisions and input digests without
rewriting any file. Existing plans retain only one predecessor input, not a
predecessor plan-byte hash or a signature. A completely replaced consistent
history, or different ancestry converging on the same predecessor input, cannot
be distinguished. Passing establishes internal consistency, not authorship,
authenticity, completeness outside the supplied anchor, or actual job completion.

Python APIs are `counterfactual(document, task_id)`,
`sensitivity(document, capacities)`, `frontier(document)` and `verify_history(plans)` in
`reasoning_under_change.work`. Reports share one one-second cooperative deadline
across all their solver calls, with at most 2,560 DP cell updates per legacy solve,
or shared legacy table, or 2,097,151 partial selections per extended table, and
the list bounds above. Check/replan also share one deadline across validation and
selection. Timeout or incomplete search fails without returning a partial report.
All four report CLI commands print JSON only, create no files and execute no work.
Metadata and reports do not establish that supplied utilities are meaningful.
