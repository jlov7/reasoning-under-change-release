# Frozen v2 protocol: exposed-task baseline diagnostics

Version 2, frozen before its first benchmark/test run on 2026-10-19. V1 omitted
an inexpensive exact-DP planner. Its evaluator uses only public jobs/capacity,
so it was not evidence that search was the strongest useful planning option.
V1's protocol and all 720 measured rows remain historical evidence unchanged.

## Frozen manifest

Canonical hashing uses sorted JSON keys, compact separators and UTF-8.

```json
{"arms":{"exact_dp":8192,"greedy":8192,"search_full":8192,"search_short":128},"capacity":64,"exposed_seeds":[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47],"generator":"cpu-planning-v1","jobs":16,"repetitions":5,"timeout_ns":20000000,"version":"v2-exposed-dp-diagnostic"}
```

Use the identical 48 observations already exposed by v1, with 5 timing
repetitions and all 4 arms in each seed/repetition. All 960 rows, including
failures, are retained. Every task is labelled `exposed`; there is no new
holdout. Freeze/hash the complete observations with this manifest before the
first v2 run. Rotate arm order by seed/repetition; discard no warmup or attempt.
No tuning is permitted after this freeze.

## Additional baseline

The DP planner accepts the same immutable observation as every other arm.
Implement a bounded 0/1 capacity dynamic program with selected-ID traceback.
It must not call or receive results from the evaluator. Independent exhaustive
small-case tests must check plan reward, capacity and absence of duplicate IDs.
It starts with the same feasible greedy incumbent and retains the best feasible
plan when the update cap or cooperative deadline interrupts it.

All arms receive the same 20 ms cooperative wall ceiling and a work ceiling of
8192; short search deliberately uses only 128. DP work units are candidate cell
updates; search work units are popped nodes; greedy performs no search/DP work
units. These are different operations, not equal-cost or matched consumed
compute. The existing Budget.node_limit/Plan.expansions slots carry this count;
v2 rows must also name the work unit. Input limits remain 20 jobs, capacity and
demand at most 128, per-job reward at most 10000. Bounds reject negative, bool,
nonfinite and excessive settings. Bounded preparation and per-unit deadline
checks are cooperative, not a hard real-time guarantee.

## Measurements and interpretation

Measure complete planning calls with wall/CPU clocks, including fallback
preparation and selected-ID reconstruction. Evaluate feasibility, realized
reward and gap after the timed planner has returned. Keep evaluation separate.
Report per-row durations, stop reasons, work counts and limits, and descriptive
median/p95 latency with quality shown separately. Five repetitions are not five
independent tasks. Treat the v2 run as exposed-task baseline diagnostics; do not
infer new holdout/generalization, model or changing-environment results.

Before implementation retain expected RED for DP correctness, infeasibility,
negative/nonnumeric bounds, cap/timeout fallback, evaluator separation and v2
accounting. Run focused/full source and fresh installed-wheel tests, and verify
the original 448-record experiment and preserved v1 protocol/720-result hashes.
The v1 API and default CLI reproduction remain available; v2 is explicitly
selected using `--v2` or `run_benchmark(version='v2')`.
