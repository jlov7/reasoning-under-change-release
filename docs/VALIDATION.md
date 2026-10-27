# Validation

Run the source and isolated-package checks in [README](../README.md) and the
[release procedure](RELEASING.md) against the exact revision being evaluated.

| Contract | Discriminating checks | Limit |
| --- | --- | --- |
| Selection respects required work, dependencies and named budgets | [Constraints](../tests/test_work_constraints.py), [resources](../tests/test_work_resources.py) | At most the documented bounded task set |
| Revision checks preserve completed work and reject stale artifacts | [Contracts](../tests/test_work_contracts.py), [reports](../tests/test_work_reports.py) | Hash identity does not prove that work happened |
| The auditor separately enumerates alternatives | [Independent audit](../tests/test_independent_audit.py), [incremental enumeration](../tests/test_audit_enumeration.py) | It shares input normalization; this is not independent certification |
| Exhausting the node or time budget produces an explicit unproven verdict | [Auditor budgets and witnesses](../tests/test_independent_audit.py) | A feasible answer is not automatically optimal |

The package gate builds source and wheel distributions, installs each into a
separate environment, runs their full suites and checks practical commands and
small-case independent oracles. [Evaluation](EVALUATION.md) retains the original
negative planning comparisons and workload-specific timing scope. No universal
speed, real-model, human-productivity or production claim follows.
