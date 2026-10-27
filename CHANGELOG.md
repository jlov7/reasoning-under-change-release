# Changelog

## 1.1.1

- Maintain subset totals incrementally in the selection audit, preserving numeric
  mask order, the first better-selection witness and explicit budget-stop verdicts.
- Cover subset accounting, retained resource totals, required work and bounded
  witness discovery with direct-arithmetic and independent-enumeration regressions.
- Keep the selection-audit interface and report schema unchanged. Accepted inputs
  still need not finish within the default audit budget.

## 1.1.0

- Support `python -m reasoning_under_change.audit` and `audit_plan` for bounded
  selection audits with separate exhaustive constraint logic. Return concrete
  better selections, explicit infeasibility or an unproven budget-stop verdict.
- Document the audit's selection-only scope, exit codes, budget recovery and
  shared-normalization limitation. Preserve the V1 work APIs and artifacts.
- Exercise the audit and direct-sdist installation in the installed release gate.
- Pin CI to Ubuntu 24.04 during the documented ubuntu-latest runner migration.

## 1.0.0

- Stabilize the bounded `ruc-work` CLI, documented Python callables and existing
  work-artifact schemas. Preserve nine pre-V1 plan/report byte fingerprints.
- Add read-only `validate` input preflight with required/completed resource usage,
  explicit mandatory-work deficits and no optimizer call.
- Publish saved outputs only after complete write/close, without replacing a
  destination. Failed partial writes are retryable. Preserve files created by
  another writer and existing symlinks.
- Handle failed work-command stdout with a structured `OUTPUT_ERROR` and exit 1;
  retain a completed saved plan for checking/recovery. Report post-publication
  staging-cleanup failure explicitly without deleting the saved plan.
- Refuse FIFO/device/directory inputs promptly using nonblocking descriptor open
  and `fstat`, preserving regular-file symlinks across all file-input commands.
- Resolve lint diagnostics, pin Ruff in CI, and retain two documented benchmark
  ERROR-row catch exemptions. Extend installed release checks for preflight,
  help/version, dependencies, README examples and packaged docs/assets.
- Enforce the documented Python 3.11 to 3.13 range in package metadata.
- Document stability, support, security, release and recovery policies. Add a real
  queue/revision workflow diagram.

Bundled simulation/planning inputs, results and protocols remain unchanged. Earlier
negative/tied comparisons and unrun empirical qualifications still apply.
