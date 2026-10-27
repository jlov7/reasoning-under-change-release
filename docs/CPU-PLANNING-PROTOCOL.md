# Frozen CPU planning qualification protocol

Version 1, frozen before benchmark execution on 2026-10-19. This is an authored
static planning experiment, a precondition to the later changing-environment and
model comparisons. It does not complete those milestones.

## Frozen manifest

The benchmark's canonical manifest is this JSON object (sorted keys, compact
separators, UTF-8 for hashing). These settings must not be tuned after holdout.

```json
{"arms":{"greedy":8192,"search_full":8192,"search_short":128},"capacity":64,"development_seeds":[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15],"generator":"cpu-planning-v1","holdout_seeds":[16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47],"jobs":16,"repetitions":5,"timeout_ns":20000000}
```

Each seed creates `random.Random("cpu-planning-v1:{seed}")`, then 16 jobs in
index order, each with demand sampled uniformly from 1..32 and reward from
1..100. All arms receive the identical immutable job list and capacity. Task
identity is a SHA-256 of the canonical observation. Arm order rotates by seed
and repetition to reduce a fixed ordering effect. No warmup or attempted row
is discarded. Development and holdout results are reported separately. This
is a seed holdout within one authored task family, not generalization evidence.

## Planner and evaluator contract

The reactive baseline takes the best feasible greedy packing by reward/demand,
reward, and smallest demand. Search uses the same incumbent, then performs real
bounded branch-and-bound over job subsets. It may return the incumbent without
improvement. It cannot read an oracle, hidden answers, or any future schedule.
An evaluator-only dynamic program computes the maximum reward independently
after planning. No planner receives that result.

All arms have the same 20 ms wall-time ceiling and at most 8192 search
expansions available. The short-search arm deliberately limits itself to 128
expansions. These are common ceilings and a compute ablation, not matched
consumed compute. Greedy consumes zero search expansions. Finite inputs cap
jobs at 20, capacity/demand at 128 and per-job reward at 10000. Runtime checks
occur between bounded operations, so this is a cooperative timeout, not a hard
real-time execution guarantee. A feasible empty or greedy incumbent survives
timeout. Retain timeout, node-limit, exhausted-search and failure statuses.

## Measurement and acceptance

Measure each complete planning call with `perf_counter_ns` and `process_time_ns`.
Report feasible realized reward, exact optimum and absolute reward gap
separately from wall/CPU duration, expansions, allocated caps and stop reason.
Report each row, including errors, and descriptive median/p95 durations by
split/arm. The 5 repetitions are timing observations, not independent tasks.
Do not assert speed rankings, significance or general superiority. No test
contains an empirical speed threshold.

Before implementation, retain expected RED for the greedy trap, independent
oracle, bounds/fallback, immutable inputs and complete accounting tests. Then
run focused/full suites and installed-wheel tests, and verify the historical
448-run grid hash and totals remain unchanged. A negative quality result is
acceptable. Preserve protocol, manifest and complete result hashes in private
evidence before any interpretation. No model, provider, network service,
participant, paid compute, or external environment is part of this experiment.

Closest project predecessor remains [Real-Time Reasoning Agents in Evolving
Environments](https://arxiv.org/abs/2511.04898). This increment makes no new
algorithm or real-time reasoning claim.
