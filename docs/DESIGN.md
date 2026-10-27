# Design

`core.py` owns the authoritative clock, job/capacity state, version counters and
scheduled events. Observations are snapshots. Proposals declare the job and
capacity versions they read. The effect checks current deadline, capacity and
open state, so a stale proposal can be rejected even after a decision to act.
The core validates the whole scenario before applying any event.

`experiment.py` charges observation, simulated compute and action ticks against
both a horizon and a round cap. Controllers receive the immutable `View` of
proposal timing, declared read set and notices already delivered. They do not
receive the authoritative world, future schedules or changed values. All seven
arms share a complete, instantaneous entity-change feed. Same-value events
conservatively invalidate a version. This is a modelling assumption, not
inference about hidden dependencies inside a language model.

`planning.py` takes immutable public jobs and capacity. Greedy tries three
packing orders. Bounded subset search and exact capacity DP retain a feasible
incumbent when their cooperative work/time cap interrupts them. DP cell updates
and search nodes have different costs. `planning_benchmark.py` times planner
calls, then independently checks the returned selection and exact optimum.
The evaluator's result is never passed into a planner.

`work.py` validates bounded user queues and reuses exact DP for static selection.
Completed work consumes total capacity and remains fixed across revisions.
Plans carry canonical input digests and one predecessor snapshot; checking
recomputes selections, totals and changes. Digests check consistency rather than
authenticate the supplied history. The CLI exclusively creates new output files
and never executes selected work. An interrupted solve returns an error.

`results.py` reads complete offline records. It reconstructs frozen inputs,
checks the grid against deterministic replay, independently evaluates planning
selections and derives summaries over every retained row. It does not infer
that recorded nanosecond timings are authentic or portable. Byte hashes detect
changed resources relative to the shipped manifest; they are not signatures or
proof of external validation.

The runtime uses only Python's standard library. JSON input is data, never code.
There are no model integrations, subprocesses, sockets, credentials or telemetry
in the runtime. The release-verification script starts bounded local build/test
processes and uses official package tooling to construct isolated installations.
