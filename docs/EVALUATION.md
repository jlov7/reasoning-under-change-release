# Evaluation and reproduction

All tables below are derived by `ruc-results` from every retained row. The
packaged JSON files are the original local records, copied byte-for-byte. The
recorded platform is macOS arm64 with Python 3.13.15. Nanosecond timings are
historical host observations; a new run may differ. No timing rank or threshold
is required for tests.

## Changing scripted grid

The frozen generator uses 32 seeds, regimes `short` (12 ticks) and `long` (36
ticks), eight rounds, one observation tick and one action tick. TTL is six.
Each event/delay stream is independently seeded using `events-v1:{seed}:{regime}`
and `delays-v1:{seed}:{regime}`. Per tick from 2 to horizon minus one, the event
chance is 0.25; half the events are unrelated noise, otherwise capacity is 0 or
1. Eight reasoning delays are sampled from 1..8. Every arm gets the same schedule
and delay sequence. Reactive uses zero compute delay; the short arm caps it at
one; adaptive does so after relevant invalidation. All 448 rows, including
rejections and no-action outcomes, remain in the census. No independent holdout
or tuning claim is made. The complete schedules and traces are in the record.

| Policy | Cases | Accepted | Not accepted | Invalid actions | Refreshes | Elapsed ticks | Compute ticks | Observation ticks | Action ticks |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| reactive | 64 | 59 | 5 | 5 | 0 | 128 | 0 | 64 | 64 |
| never | 64 | 48 | 16 | 16 | 0 | 409 | 281 | 64 | 64 |
| ttl | 64 | 39 | 25 | 11 | 25 | 483 | 344 | 89 | 50 |
| all_changes | 64 | 30 | 34 | 2 | 57 | 565 | 412 | 121 | 32 |
| relevant | 64 | 36 | 28 | 6 | 33 | 486 | 347 | 97 | 42 |
| relevant_short | 64 | 53 | 11 | 6 | 10 | 202 | 69 | 74 | 59 |
| adaptive | 64 | 43 | 21 | 8 | 31 | 445 | 299 | 95 | 51 |

Reactive wins this authored grid (59/64), versus adaptive 43/64. Low invalid-action
counts alone can conceal failure to act. Compute ticks are simulated costs, not
measured inference or CPU durations.

## Static planning records

The unchanged [v1 protocol](CPU-PLANNING-PROTOCOL.md) and
[v2 protocol](CPU-PLANNING-V2-PROTOCOL.md) define inputs, arm order, common
ceilings, stop reasons and the evaluator. V1 has 720 rows: 16 development and
32 holdout seeds, five repetitions and three arms. That holdout is within one
authored family. V2 has 960 rows: the same 48 already-exposed tasks, five
repetitions and four arms. It adds exact DP without creating a new holdout.
The shared input bundle contains all 48 observations and the v2 manifest.

Each arm receives a 20 ms cooperative ceiling and 8192 work units, except
short search at 128. DP units count cell updates; search units count popped
nodes; greedy consumes neither. Common ceilings are not matched compute.
Reward and gap sums below include valid rows only; errors remain in rows and
are reported explicitly. Repetitions measure timing, not new independent tasks.

### planning-v1

| Split/arm | Tasks | Rows | Errors | Timeouts | Overruns | Reward sum | Gap sum | Wall median ns | Wall p95 ns | CPU median ns |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| development/greedy | 16 | 80 | 0 | 0 | 0 | 32865 | 185 | 24750.0 | 35541 | 25000.0 |
| development/search_full | 16 | 80 | 0 | 0 | 0 | 33050 | 0 | 544833.5 | 1543833 | 534000.0 |
| development/search_short | 16 | 80 | 0 | 0 | 0 | 32875 | 175 | 50521.0 | 65958 | 50500.0 |
| holdout/greedy | 32 | 160 | 0 | 0 | 0 | 70690 | 550 | 24333.0 | 32833 | 24000.0 |
| holdout/search_full | 32 | 160 | 0 | 0 | 0 | 71240 | 0 | 576083.5 | 1067666 | 574500.0 |
| holdout/search_short | 32 | 160 | 0 | 0 | 0 | 70790 | 450 | 49521.0 | 67875 | 49000.0 |

### planning-v2

| Split/arm | Tasks | Rows | Errors | Timeouts | Overruns | Reward sum | Gap sum | Wall median ns | Wall p95 ns | CPU median ns |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| exposed/exact_dp | 48 | 240 | 0 | 0 | 0 | 104290 | 0 | 135916.5 | 155083 | 136000.0 |
| exposed/greedy | 48 | 240 | 0 | 0 | 0 | 103555 | 735 | 24271.0 | 29583 | 24000.0 |
| exposed/search_full | 48 | 240 | 0 | 0 | 0 | 104290 | 0 | 524750.0 | 1063167 | 524500.0 |
| exposed/search_short | 48 | 240 | 0 | 0 | 0 | 103665 | 625 | 49250.0 | 60334 | 49000.0 |

In v2, exact DP and full search each realize total reward 104290 with zero gap
over 240 rows. Their observed median wall times differ on this host. This
supports quality parity and a host-specific timing observation on exposed tasks,
not algorithm novelty, matched compute, model reasoning or generalization.

## Byte custody and replay

Resource SHA-256 values are below. Canonical task/schedule/manifest hashes are
also carried inside the JSON. `ruc-results` checks the manifest and derives all
tables from rows. A changed value, truncated census, duplicate key/row, wrong
task digest or inconsistent supplied summary is rejected. The verifier cannot
authenticate the original timing hardware or prove scientific validity.

| Resource | Byte SHA-256 |
|---|---|
| `changing-grid.json` | `2733ae36e6b6e7d71967e95c808c5b2a49a83213ab5113c206a8ec96d8fdbaaf` |
| `planning-inputs.json` | `0bb2971d29ae847a6d6be38aaa0b5a3dba6c173dc0370b630d7b917312b3232a` |
| `planning-v1.json` | `020edc95dec9b3d5a025cc2c60ed0f80a2f7e592a1f0dfbcde63e2f995ee899f` |
| `planning-v2.json` | `ad7c5298fef29f9b08ca5ca4c919a90231f0206a57ebdaf9f4e3ffb0537b820b` |
| `supply_change.json` | `4d7a48d4a100df241387e748f85483e37d951e6dcd8c2f7378330d41c7c47be4` |

Use the README commands for a new complete run. Default planning retains v1;
select v2 explicitly with `--v2`. Every result is retained, with no warmup or
failed attempt discarded. `ruc-results` intentionally rejects partial runs.
The full source suite and `scripts/verify_release.py` cover boundary rejection,
negative controls, complete census verification and installed reproduction.
Remote exact-head CI and independent release review are separate gates.
Actual model/Gym reproduction and human or production evaluation remain NOT RUN.
