# Quality Harness Delivery Board

## How to Use

This board is the shared contract between the operator and any coding agent
working on `quality-harness`. It tracks the path from the deterministic OSS core
already in this repository to a self-serve, usage-based cloud tier.

- Pick the lowest-numbered task whose status is `Ready` and whose dependencies
  are all `Done`. Do not start a blocked task.
- Work one task per change. Update the relevant card, the Decision Log, and the
  test suite in the same change.
- A task moves to `In Progress` only when an agent has claimed it and can state
  the exact acceptance test it is about to satisfy.
- `Points` use the Fibonacci set `1, 2, 3, 5, 8`. If a task needs more than `8`,
  split it; do not stretch the estimate.
- `Priority` is `P0` (release or revenue blocker), `P1` (next milestone),
  `P2` (valuable, not urgent), or `P3` (deferred).
- Every task must keep a valid dependency chain: a task may depend only on a
  lower-numbered task, and no cycle is allowed.
- A task is not done until its acceptance criteria pass in CI and the report,
  evidence, or metering contract it changes is documented.

**Current focus:** Deliver the first usage-metered cloud pilot without weakening
the deterministic core: metered runs, signed tier entitlements, and enforced
quota. The critical path is `QH-019 -> QH-020 -> QH-021 -> QH-025`, supported by
`QH-014` for defensible scoring and `QH-010` for live browser evidence. Until
`QH-010` lands, browser checks remain explicitly `blocked`, never converted into
passes.

**Derivation:** Session 1 verified that the OSS harness can run statically,
persist state, hash evidence, halt on a critical finding, and degrade safely
when Playwright is absent. The gaps that shape this board are: much of the check
logic is still hard-coded, confidence weights are uncalibrated, there is no run
history or trend diff, the browser layer is untested in CI, and there is no
tenant, metering, quota, licensing, or billing surface. The cloud epic exists
because the business goal is a self-serve, usage-based tier, not a bespoke
enterprise deployment.

## Backlog

The backlog holds the product outcomes. Each story is broken into one or more
Ready tasks; task-level acceptance criteria live in the Ready section.

### E1: Deterministic Core Engine

**Outcome:** Any release can be evaluated by a reproducible state machine whose
ordering, budget, and report bytes do not depend on an LLM or wall-clock luck.

| Story | Outcome | Tasks |
| --- | --- | --- |
| S1.1 | Workflows are data, not code branches | QH-001 |
| S1.2 | An interrupted run resumes without duplicating findings | QH-002 |
| S1.3 | New checks are installable without editing the orchestrator | QH-003 |
| S1.4 | Two runs over the same inputs produce the same report body | QH-004 |
| S1.5 | Operators can find, compare, and expire historical runs | QH-005 |
| S1.6 | Resume rejects a changed plugin registry | QH-035 |
| S1.7 | A seeded resume continues the id sequence without collisions | QH-036 |

### E2: Specialist Persona Layer

**Outcome:** Each risk domain has one accountable specialist with an explicit
tool boundary, output contract, and refusal path.

| Story | Outcome | Tasks |
| --- | --- | --- |
| S2.1 | Persona files, not Python constants, own check assignment | QH-006 |
| S2.2 | Contract violations are visible and cannot inflate confidence | QH-007 |
| S2.3 | Refusals and blockers are first-class report data | QH-008 |
| S2.4 | Adding a specialist is a guided, validated operation | QH-009 |

### E3: Inspect and Operate Evidence

**Outcome:** Live verification is exercised in CI, produces reviewable
artifacts, and can cover authenticated and small-screen journeys safely.

| Story | Outcome | Tasks |
| --- | --- | --- |
| S3.1 | Browser checks run in CI instead of being permanently blocked | QH-010 |
| S3.2 | A failed journey can be diagnosed from HAR, trace, and video | QH-011 |
| S3.3 | Authenticated surfaces are tested without stored customer secrets | QH-012 |
| S3.4 | Mobile layout and focus order are part of Operate coverage | QH-013 |

### E4: Release Confidence and Reporting

**Outcome:** The confidence score is calibrated, policy-driven, portable, and
comparable across releases.

| Story | Outcome | Tasks |
| --- | --- | --- |
| S4.1 | Weights and caps match labeled historical outcomes | QH-014 |
| S4.2 | Teams can set release policy without patching Python | QH-015 |
| S4.3 | Results enter code scanning and CI without custom parsing | QH-016 |
| S4.4 | Reviewers see new, resolved, and persistent findings | QH-017 |
| S4.5 | Known risk can be suppressed with an owner and an expiry | QH-018 |

### E5: Self-Serve Usage-Based Cloud Tier

**Outcome:** A customer can sign up, run the harness, see usage, and pay for
what they consume, while the operator can meter and enforce limits per tenant.
Plan tiers and signed entitlements make licensing verifiable without a sales
call. This is the primary business epic.

| Story | Outcome | Tasks |
| --- | --- | --- |
| S5.1 | Every billable action emits one canonical usage event | QH-019 |
| S5.2 | Each request is attributable to a workspace and scoped token | QH-020 |
| S5.3 | Free and paid limits are enforced predictably | QH-021 |
| S5.4 | Usage is auditable and reconciles to an invoice | QH-022 |
| S5.5 | A new customer can start and receive a signed tier entitlement without a sales call | QH-023 |
| S5.6 | Metered billing is automated behind a safe feature flag | QH-024 |
| S5.7 | Runs are queued and isolated per tenant | QH-025 |
| S5.8 | Tenant data can be exported and deleted on request | QH-026 |
| S5.9 | Entitlements and billing behavior are regression-tested | QH-027 |

### E6: Developer Experience and Integrations

**Outcome:** Teams can adopt the harness through a signed package and their
existing pull-request workflow.

| Story | Outcome | Tasks |
| --- | --- | --- |
| S6.1 | Installation is reproducible and supply-chain visible | QH-028 |
| S6.2 | A pull request gets one actionable confidence summary | QH-029 |
| S6.3 | The report contract is versioned and machine-verifiable | QH-030 |

### E7: Hardening, Privacy, and Reliability

**Outcome:** Sensitive data, untrusted targets, and partial failures cannot turn
into a false release signal.

| Story | Outcome | Tasks |
| --- | --- | --- |
| S7.1 | Redaction is proven against a regression corpus | QH-031 |
| S7.2 | Evidence tampering is detectable after the run | QH-032 |
| S7.3 | The target URL cannot be used for server-side request forgery | QH-033 |
| S7.4 | State, evidence, and the kill switch survive injected failures | QH-034 |

## Ready

### QH-015 - Add policy files for gates and domain weights

- **Epic:** E4 Release Confidence and Reporting
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-014
- **Status:** Ready
- **Description:** Move gate thresholds, domain weights, caps, and severity
  penalties into a versioned policy file with a documented schema and safe
  defaults.
- **Acceptance Criteria:**
  - A policy file can tighten or relax thresholds without code changes.
  - Invalid or unsafe policies are rejected before execution.
  - The report names the policy id and version used.
  - The default policy reproduces the current `advisory-v1` behavior.

### QH-016 - Export SARIF and JUnit and sign report hashes

- **Epic:** E4 Release Confidence and Reporting
- **Priority:** P1
- **Points:** 3
- **Depends on:** QH-015
- **Status:** Ready
- **Description:** Add SARIF and JUnit exporters plus a detached signature over
  the report body so CI and code scanning can consume results and verify
  integrity.
- **Acceptance Criteria:**
  - Findings map to SARIF rules with severity and remediation.
  - Checks map to JUnit test cases with pass, fail, blocked, and skipped states.
  - The signature verifies with a published public key and fails after any edit.
  - Exporters never include redacted secret values.

### QH-017 - Add run-to-run trend diff

- **Epic:** E4 Release Confidence and Reporting
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-005, QH-015
- **Status:** Ready
- **Description:** Compare the current run with the previous run for the same
  target and report new, resolved, and persistent findings plus score and
  coverage deltas.
- **Acceptance Criteria:**
  - Findings are matched by fingerprint, not title text alone.
  - The report includes `trend.new`, `trend.resolved`, `trend.persistent`, and
    `trend.score_delta`.
  - A first run reports `trend.baseline: true` with no fabricated delta.
  - The CLI exits non-zero on a configurable score regression.

### QH-018 - Add baseline suppression with owner and expiry

- **Epic:** E4 Release Confidence and Reporting
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-017
- **Status:** Ready
- **Description:** Allow a finding fingerprint to be suppressed with a named
  owner, reason, ticket, and expiry date. Expired suppressions re-enter scoring
  and are called out in the report.
- **Acceptance Criteria:**
  - Suppression requires a non-empty owner, reason, and future expiry.
  - Suppressed findings remain visible but do not add severity penalty.
  - Expired suppressions automatically stop applying and raise a low finding.
  - An audit event is appended for every suppression change.

### QH-019 - Define the metering event contract

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P0
- **Points:** 5
- **Depends on:** None
- **Status:** Ready
- **Description:** Define one canonical usage event for billable actions: run
  started, check executed, browser minute consumed, evidence byte stored, and
  export generated. Events must be idempotent and reconcile to a run id.
- **Acceptance Criteria:**
  - A JSON Schema exists for the event envelope and every event type.
  - Duplicate delivery with the same idempotency key is ignored.
  - A local OSS run emits the same events to a file sink without a cloud account.
  - The schema includes workspace, run, quantity, unit, timestamp, and source.
  - Contract tests freeze the v1 event names.

### QH-020 - Add workspace, API-key, and scoped-token authentication

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P0
- **Points:** 8
- **Depends on:** QH-019
- **Status:** Ready
- **Description:** Introduce workspaces and hashed API keys with scopes for run,
  read, export, and admin. Every cloud request must resolve to exactly one
  workspace before it can consume quota.
- **Acceptance Criteria:**
  - Keys are stored only as hashes with a prefix for lookup and rotation.
  - Missing, expired, revoked, and wrong-scope keys return distinct 401/403
    responses with no workspace data.
  - Cross-workspace access is impossible in tests, including guessed run ids.
  - Key creation returns the secret exactly once and records an audit event.

### QH-021 - Enforce quotas with soft and hard limits

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P0
- **Points:** 5
- **Depends on:** QH-019, QH-020
- **Status:** Ready
- **Description:** Enforce monthly allowances for runs, checks, browser minutes,
  and evidence bytes. Return remaining quota in response headers and block only
  the action that exceeds a hard limit.
- **Acceptance Criteria:**
  - Soft-limit warnings are emitted once per billing period per workspace.
  - A hard-limit action returns 402/429 with the exact limit and reset time.
  - Partial work already completed is billed and reported, not discarded.
  - Concurrency tests cannot overspend a limit through parallel requests.
  - OSS/local mode remains unlimited and unchanged.

### QH-022 - Build the append-only usage ledger and invoice export

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-021
- **Status:** Ready
- **Description:** Aggregate metering events into an append-only ledger with
  period close, correction entries, and a CSV/JSON invoice export that a finance
  operator can reconcile.
- **Acceptance Criteria:**
  - Ledger entries are immutable; corrections reference the original entry.
  - A closed period cannot accept late events without an explicit adjustment.
  - Invoice totals equal the sum of ledger quantities and unit prices.
  - Export includes workspace, period, line items, subtotal, and credits.
  - Reconciliation tests cover duplicate, late, and corrected events.

### QH-023 - Add self-serve signup, free allowance, and plan selection

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-020, QH-021
- **Status:** Ready
- **Description:** Implement the API flow for a new customer to create a
  workspace, receive a scoped key, consume a free allowance, select a
  usage-based tier, and receive a signed entitlement that encodes the tier,
  limits, and effective period without operator involvement.
- **Acceptance Criteria:**
  - Signup creates one workspace, one owner, and one default key.
  - The free allowance is visible in the usage endpoint from the first request.
  - Plan selection records an effective date and cannot silently downgrade
    existing usage.
  - The entitlement is signed, verifiable offline, scoped to the workspace and
    period, and rejected after tampering or expiry.
  - Replaying a signup request is idempotent.
  - No endpoint returns another workspace's identifiers.

### QH-024 - Integrate metered subscription billing behind a feature flag

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P1
- **Points:** 8
- **Depends on:** QH-022, QH-023
- **Status:** Ready
- **Description:** Connect the usage ledger to a billing provider for metered
  subscriptions and invoices, disabled by default and enabled only for a named
  workspace in sandbox mode.
- **Acceptance Criteria:**
  - The feature flag off path creates no external customer or subscription.
  - Sandbox mode reconciles a test invoice against the ledger to the cent.
  - Failed billing-provider calls retry idempotently and surface a degraded
    status without blocking runs that already paid quota.
  - Secrets are loaded from the secret manager, never environment dumps or logs.
  - A kill switch disables billing writes without stopping the harness.

### QH-025 - Add the cloud run queue with per-tenant worker isolation

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P0
- **Points:** 8
- **Depends on:** QH-020, QH-021
- **Status:** Ready
- **Description:** Queue cloud runs with a durable job record, enforce one active
  browser context per tenant worker, and isolate filesystem, evidence, and
  network access between tenants.
- **Acceptance Criteria:**
  - A submitted run returns a job id and can be polled without auth leakage.
  - Two tenants running concurrently cannot read each other's files or evidence.
  - Worker crashes requeue the job without double-billing the same check.
  - Cancellation stops remaining checks and records the partial evidence set.
  - Queue depth and run duration are exported as metrics.

### QH-026 - Add tenant retention, export, and deletion controls

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-025
- **Status:** Ready
- **Description:** Give each workspace a retention policy and a tested path to
  export or delete all of its state and evidence without touching another
  tenant.
- **Acceptance Criteria:**
  - Default retention is explicit and documented per plan.
  - Export produces a signed archive with a manifest and no cross-tenant paths.
  - Deletion removes state, evidence, queue records, and cached exports.
  - Deletion is soft for a grace period and auditable after hard deletion.
  - Tests verify deletion of a tenant with active and historical runs.

### QH-027 - Add billing sandbox and entitlement regression tests

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-024
- **Status:** Ready
- **Description:** Add deterministic tests for plan entitlements, quota
  transitions, invoice reconciliation, and feature-flag behavior using a local
  billing fake.
- **Acceptance Criteria:**
  - Every plan has a table-driven entitlement test.
  - Upgrades, downgrades, cancellations, and failed payments are covered.
  - Tests run without network access and fail on an unexpected provider call.
  - CI publishes a coverage report for the billing module.

### QH-028 - Publish a signed PyPI package and container image

- **Epic:** E6 Developer Experience and Integrations
- **Priority:** P1
- **Points:** 3
- **Depends on:** QH-001
- **Status:** Ready
- **Description:** Add a release workflow that builds an sdist, wheel, and
  minimal container image, signs them, publishes provenance, and verifies the
  installed CLI from a clean environment.
- **Acceptance Criteria:**
  - `pip install quality-harness` exposes the `quality-harness` command.
  - Artifacts carry signatures and provenance attestations.
  - A clean container can run the static fixture without extra packages.
  - The browser image is separate and documented.
  - A bad signature fails the release job.

### QH-029 - Add a GitHub Action with PR comment and status check

- **Epic:** E6 Developer Experience and Integrations
- **Priority:** P1
- **Points:** 3
- **Depends on:** QH-016
- **Status:** Ready
- **Description:** Ship an Action that runs the harness, uploads evidence,
  comments one concise confidence summary, and sets a required status check.
- **Acceptance Criteria:**
  - The comment includes score, decision, coverage, new findings, and artifact
    links without leaking redacted values.
  - Re-running updates the existing comment instead of creating duplicates.
  - Fork pull requests run without write tokens or secrets.
  - The Action honors `--fail-on` and `--min-score`.
  - A sample workflow is tested against `examples/sample-repo`.

### QH-030 - Publish the report JSON Schema and contract tests

- **Epic:** E6 Developer Experience and Integrations
- **Priority:** P1
- **Points:** 3
- **Depends on:** QH-016
- **Status:** Ready
- **Description:** Publish a JSON Schema for `quality-harness.report/v1` and run
  contract tests so downstream consumers can rely on field names and types.
- **Acceptance Criteria:**
  - The schema is generated from or checked against the report builder.
  - Additive optional fields are allowed; removing or retyping a field fails.
  - Both a completed and a halted report validate against the schema.
  - The schema is published as a release artifact with a stable URL.

### QH-031 - Add a redaction regression corpus

- **Epic:** E7 Hardening, Privacy, and Reliability
- **Priority:** P0
- **Points:** 3
- **Depends on:** None
- **Status:** Ready
- **Description:** Create a corpus of synthetic secrets, tokens, cookies, URLs,
  and headers and assert that none reach state, evidence, logs, or reports in
  raw form.
- **Acceptance Criteria:**
  - The corpus covers at least 20 credential and token shapes.
  - Every artifact produced by a fixture run is scanned for corpus values.
  - A deliberately broken redactor fails the test.
  - The corpus is clearly synthetic and safe to commit.
  - Redaction failures never print the original value in the failure message.

### QH-032 - Sign evidence manifests and alert on tamper

- **Epic:** E7 Hardening, Privacy, and Reliability
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-016, QH-031
- **Status:** Ready
- **Description:** Sign the evidence manifest and add a verify command that
  detects modified, missing, or substituted artifacts and emits a machine-
  readable alert.
- **Acceptance Criteria:**
  - Manifest signatures verify with a published key.
  - Tampering with one byte in any artifact fails verification.
  - Verification output lists the affected path and check id.
  - A failed verification caps confidence and forces a `hold` decision.
  - Verification works offline.

### QH-033 - Add SSRF-safe target validation and allowlists

- **Epic:** E7 Hardening, Privacy, and Reliability
- **Priority:** P0
- **Points:** 5
- **Depends on:** None
- **Status:** Ready
- **Description:** Validate URL targets before navigation: resolve DNS, reject
  loopback, link-local, metadata, and private ranges by default, and require an
  explicit CIDR allowlist for local test servers.
- **Acceptance Criteria:**
  - Cloud metadata and private-range targets are rejected with a clear error.
  - Redirects are re-validated at every hop.
  - An operator can allowlist localhost for CI through config, not a CLI flag
    that bypasses all checks.
  - DNS rebinding is covered by a test using a changing resolver.
  - The error never includes resolved internal IPs in the public report.

### QH-034 - Add failure-injection tests for state, evidence, and kill switch

- **Epic:** E7 Hardening, Privacy, and Reliability
- **Priority:** P1
- **Points:** 3
- **Depends on:** QH-002, QH-032
- **Status:** Ready
- **Description:** Inject disk-full, permission, partial-write, corrupted
  manifest, and kill-during-evidence failures, and prove the run fails closed
  without claiming confidence.
- **Acceptance Criteria:**
  - Corrupt `progress.json` produces a typed error and preserves the last good
    copy when available.
  - A failed evidence write never produces a passing report.
  - Kill during evidence collection still writes a hold report with the failure.
  - Each injected failure has a deterministic test and a documented recovery.

### QH-035 - Guard resume against a changed plugin registry

- **Epic:** E1 Deterministic Core Engine
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-003
- **Status:** Ready
- **Description:** Persist a plugin-registry fingerprint (check ids, sources,
  signatures/evaluator availability, and trust key ids) with the run and reject
  resume when the registry has changed, so a pending check cannot be replayed
  against a different evaluator or a plugin that is no longer trusted.
- **Acceptance Criteria:**
  - Resume with an added, removed, re-signed, or re-evaluated plugin fails with a
    typed `StateError` before replay.
  - Completed checks and their evidence remain byte-identical after the failed
    resume attempt.
  - The progress and report record the registry fingerprint used by the run.
  - Tests cover a plugin added between runs, a plugin removed between runs, and a
    plugin whose trust key was rotated.

### QH-036 - Continue seeded id sequences across resume

- **Epic:** E1 Deterministic Core Engine
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-004
- **Status:** Ready
- **Description:** Persist the seeded id sequence position and the determinism
  options with the run and restore them on resume so a resumed deterministic run
  cannot reuse a finding id or mix clocks and seeds.
- **Acceptance Criteria:**
  - A resumed seeded run continues the deterministic id sequence from the saved
    position instead of restarting it.
  - Resume with a different seed or clock mode either restores the saved options
    or fails with a typed `StateError`.
  - Existing finding ids and evidence remain byte-identical after resume.
  - The progress and report record the sequence position or a resume-specific
    entropy salt.
  - Tests cover resume after partial execution and compare ids with a fresh
    same-seed run.

## In Progress

No task is claimed. `QH-001` through `QH-014` are complete, so `QH-015` is now
the lowest-numbered unblocked task. `QH-019` remains the first
commercial-critical-path task.

## Blockers

| Blocker | Impact | Unblock |
| --- | --- | --- |
| Playwright and Chromium are not installed in the Session 1 environment. | Live browser checks are verified as `blocked`, not executed. | Land `QH-010` and run it on a machine or CI image with Chromium installed. |
| No billing-provider sandbox credentials or merchant account exist yet. | `QH-024` and `QH-027` cannot verify real invoice behavior. | Provide sandbox API keys through the secret manager and name a test workspace. |
| No labeled historical release outcomes are available. | `QH-014` calibration cannot claim empirical validity yet. | Curate at least 12 past releases with a human ship/hold label and evidence. |
| The repository is not yet a git repository with CI. | The board contract and release workflow cannot be enforced automatically. | Initialize git, add the CI workflow from `QH-010`, and protect the default branch. |

## Done

### QH-001 - Load versioned workflow definitions from YAML

- **Epic:** E1 Deterministic Core Engine
- **Priority:** P1
- **Points:** 5
- **Depends on:** None
- **Status:** Done
- **Completed:** 2026-11-10
- **Description:** Replaced the hard-coded workflow with a dependency-free YAML
  loader for versioned files under `.harness/workflows/`. The loader validates
  the five-phase order and falls back to the built-in definition only when no
  override for the requested id exists.
- **Acceptance Criteria:**
  - A malformed workflow file produces a typed `HarnessError`, not a traceback.
  - The five phase names and their order remain invariant; unknown phases are
    rejected.
  - `status` and `report` show the workflow id and version that actually ran.
  - Tests cover the built-in workflow, a valid override, and three invalid files.
- **Evidence:** `tests/test_workflows.py` covers the built-in definition, fallback,
  valid override, custom workflow selection, duplicate ids, and four invalid
  definitions. An override run reports `release-confidence@2.0`; the CLI returns
  a structured `WorkflowError` with exit code 2 for a malformed file. Full suite:
  45 passing tests with Python 3.11. User-facing contract documented in
  `README.md`.

### QH-002 - Add per-check checkpointing and crash-safe resume

- **Epic:** E1 Deterministic Core Engine
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-001
- **Status:** Done
- **Completed:** 2026-11-10
- **Description:** Persisted an atomic evidence checkpoint after every completed
  check, before the progress write, and reconciled the evidence index with run
  state on resume. `--resume` replays only unfinished work without duplicating
  findings or evidence entries.
- **Acceptance Criteria:**
  - Killing the process after check N leaves exactly N completed checks in
    `progress.json`.
  - Resume re-runs only pending, running, or errored checks.
  - Findings and evidence entries from completed checks are byte-identical after
    resume.
  - A resume against a different target is rejected with an actionable message.
- **Evidence:** `tests/test_resume.py` simulates a crash after two completed
  checks with one errored and one running check. Resume replays exactly the
  unfinished set, preserves completed findings and manifest entries byte for
  byte, and produces no duplicate paths or fingerprints. A second test verifies
  the target-mismatch error. Full suite: 47 passing tests with Python 3.11.
  User-facing behavior documented in `README.md`.

### QH-003 - Add a plugin check registry with signed entry points

- **Epic:** E1 Deterministic Core Engine
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-001
- **Status:** Done
- **Completed:** 2026-11-10
- **Description:** Moved built-in and installed check discovery behind one
  `CheckRegistry`. Signed `quality_harness.checks` entry points declare a check
  definition and evaluator; plugins are verified with HMAC-SHA256 against a
  project-local trust store, must be owned by exactly one persona, and execute
  through a bounded `PluginContext`.
- **Acceptance Criteria:**
  - Built-in checks are discovered through the same registry as plugins.
  - Duplicate ids, unknown input requirements, and weight zero are rejected.
  - A sample plugin can add a check without modifying `orchestrator.py`.
  - Plugin load failures become a blocked check with a clear limitation, not a
    failed run.
- **Evidence:** `tests/test_plugins.py` verifies the shared built-in registry,
  all three validation rejections, unsigned and invalid signatures, persona
  ownership, end-to-end execution of `examples/plugins/sample_check.py`, and an
  import failure that becomes a blocked check while the run completes. Full
  suite: 56 passing tests with Python 3.11. Plugin contract documented in
  `README.md` and `examples/plugins/README.md`.

### QH-004 - Inject seed and clock for reproducible reports

- **Epic:** E1 Deterministic Core Engine
- **Priority:** P1
- **Points:** 3
- **Depends on:** QH-001
- **Status:** Done
- **Completed:** 2026-11-10
- **Description:** Added injectable clock and entropy providers behind the
  existing `utc_now` and `new_id` call sites. A run can use a seeded id
  sequence and/or a frozen clock; production defaults remain real UTC time and
  UUID entropy.
- **Acceptance Criteria:**
  - Two runs with the same seed and a frozen clock produce identical findings,
    ids, weights, and confidence breakdown.
  - The report records the seed and clock mode used.
  - Production defaults still use real UTC time and UUID entropy.
  - A regression test compares two normalized reports byte for byte.
- **Evidence:** `tests/test_determinism.py` covers the deterministic provider,
  context-scoped `utc_now`/`new_id`, production defaults, invalid options,
  state round-tripping, two frozen/seeded runs compared byte for byte, and a
  `generated_at` override, and CLI flag wiring. Full suite: 65 passing tests
  with Python 3.11. CLI flags and behavior documented in `README.md`.

### QH-005 - Add a run history index and retention policy

- **Epic:** E1 Deterministic Core Engine
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-002
- **Status:** Done
- **Completed:** 2026-11-11
- **Description:** Added `.harness/history.json` with run id, target, status,
  score, decision, finding counts, evidence path, and timestamps. The index is
  written atomically after every run, `status --all` lists runs newest-first,
  and `run --keep-runs` plus the `prune` command enforce count and age retention.
- **Acceptance Criteria:**
  - History is updated atomically at the end of every run.
  - `status --all` lists runs newest-first with score and decision.
  - Pruning by count or age removes both state and evidence for selected runs.
  - The latest completed run and all halted runs are retained by default.
- **Evidence:** `tests/test_history.py` covers atomic history writes,
  `status --all` ordering and metadata, count and age pruning, evidence
  deletion, halted-run protection, dry-run behavior, failed-run recording, and
  CLI wiring for `status --all`, `run --keep-runs`, and `prune`. Full suite: 74
  passing tests with Python 3.11. Behavior documented in `README.md`.

### QH-006 - Move check ownership into persona contracts

- **Epic:** E2 Specialist Persona Layer
- **Priority:** P1
- **Points:** 3
- **Depends on:** QH-003
- **Status:** Done
- **Completed:** 2026-11-11
- **Description:** Made persona frontmatter the single source of truth for check
  ownership. The built-in catalog no longer assigns a `persona`; planning and
  registry validation derive the owner from the contracts. Built-in checks with
  zero or multiple owners fail before execution, while plugin ownership failures
  remain blocked coverage gaps per D13.
- **Acceptance Criteria:**
  - Removing a check from every persona makes planning fail with a precise error.
  - Duplicate ownership fails validation before execution starts.
  - The catalog has no `persona` assignment that can drift from the contracts.
  - Existing persona and planning tests continue to pass.
- **Evidence:** `tests/test_ownership.py` proves the catalog has no persona
  assignments, an unowned built-in check fails `plan_checks` with the check id,
  the orchestrator raises `PersonaError` before creating run state, duplicate
  ownership is rejected before execution, selected-persona plans use the full
  contract set for ownership, and planned owners come from frontmatter. Full
  suite: 80 passing tests with Python 3.11. `README.md`, `personas/README.md`,
  and `examples/plugins/README.md` document the ownership contract.

### QH-007 - Quarantine schema-invalid findings

- **Epic:** E2 Specialist Persona Layer
- **Priority:** P1
- **Points:** 3
- **Depends on:** QH-006
- **Status:** Done
- **Completed:** 2026-11-11
- **Description:** Findings are validated against the owning persona's output
  schema before normalization. Invalid drafts are quarantined in a persisted
  `invalid_findings` collection with redacted payloads and validation errors;
  they never enter `findings`, scoring, finding counts, or kill-switch
  evaluation. Per-persona contract failures and consecutive-failure streaks are
  tracked, and three consecutive failures mark the persona as degraded.
- **Acceptance Criteria:**
  - Invalid findings never change the confidence score or trigger a kill.
  - The report shows the persona, check, validation errors, and redacted payload.
  - Three consecutive invalid findings mark the persona as degraded in the report.
  - Tests cover missing fields, wrong enum values, and wrong nested types.
- **Evidence:** `tests/test_invalid_findings.py` runs a signed plugin that emits
  a missing required field, an out-of-contract severity, and a wrong nested
  `tags` type. It proves the confidence score matches a control run with no
  malformed findings, the kill switch does not fire, no plugin finding reaches
  `findings` or `finding_ids`, all three errors appear with the persona, check,
  and redacted payload, a live-looking token never reaches progress, report, or
  evidence, three consecutive failures degrade the persona, and a valid finding
  resets the consecutive-failure streak. Full suite: 83 passing tests with
  Python 3.11. The report contract and persona rules are documented in
  `README.md` and `personas/README.md`.

### QH-008 - Add structured refusal and blocker records

- **Epic:** E2 Specialist Persona Layer
- **Priority:** P1
- **Points:** 3
- **Depends on:** QH-007
- **Status:** Done
- **Completed:** 2026-11-11
- **Description:** Refusals and blocked work are persisted as one structured
  `Blocker` per blocked check. A blocker records the attempted action, the
  boundary that stopped it, the evidence needed to unblock it, and whether
  another check covers the gap. Blocked checks never carry findings, and a
  blocker-bearing outcome can never become `passed`.
- **Acceptance Criteria:**
  - A blocked check produces one blocker record and zero findings.
  - Blockers are included in the report, status output, and coverage roll-up.
  - A blocker can never be converted to `passed`.
  - Schema validation covers the blocker contract for all three personas.
- **Evidence:** `tests/test_blockers.py` runs signed plugins that refuse with a
  structured blocker, return a finding from a blocked check, and claim `passed`
  while carrying a blocker. It proves each blocked check has exactly one
  blocker, findings are discarded, the pass claim is forced back to `blocked`,
  planning failures materialize blockers, an unavailable browser produces one
  blocker per browser check across all three personas, and the report,
  `status` output, and coverage counts expose the blocker. All three persona
  contracts load a `## Blocker schema` and reject a payload missing `boundary`.
  A synthetic token in blocker text is redacted before progress, report,
  evidence, or `status` persistence. Full suite: 89 passing tests with Python
  3.11. `README.md`, `personas/README.md`, and `examples/plugins/README.md`
  document the blocker contract.

### QH-009 - Add persona lint and scaffold commands

- **Epic:** E2 Specialist Persona Layer
- **Priority:** P2
- **Points:** 2
- **Depends on:** QH-006
- **Status:** Done
- **Completed:** 2026-11-11
- **Description:** Added `personas lint` and `personas scaffold <id>` subcommands
  to validate and generate specialist contracts. `lint` scans all persona Markdown
  files in a directory, aggregating errors for missing sections, duplicate persona
  IDs, unknown check IDs, duplicate check ownership, and schema definition
  violations, exiting non-zero on failure. `scaffold` creates starter contracts
  with complete frontmatter, role, tools table, output schema, blocker schema,
  and refusal handling, writing atomically and refusing to overwrite existing files
  without `--force`. Both commands emit machine-readable JSON on stdout.
- **Acceptance Criteria:**
  - `lint` reports missing sections, unknown check ids, duplicate ownership, and
    schema violations with non-zero exit.
  - `scaffold` writes a contract that passes `lint` unchanged.
  - Scaffolding refuses to overwrite an existing persona without `--force`.
  - Both commands emit JSON on stdout, consistent with the existing CLI.
- **Evidence:** `tests/test_persona_tools.py` exercises both commands through the
  CLI JSON contract. It verifies that clean personas pass lint with exit 0, that
  a directory containing missing sections, unknown checks, duplicate ownership,
  and missing required schema properties reports all failure categories with exit 1,
  that `scaffold` creates a contract that passes `lint` unchanged (with a placeholder
  check warning), that scaffolding refuses to overwrite existing files without `--force`
  (exit 2), that `--force` overwrites successfully, and that custom domains and checks
  are accepted. Full suite: 95 passing tests with Python 3.11. `README.md` and
  `personas/README.md` document the commands.

### QH-010 - Add Playwright-enabled CI and a browser smoke fixture

- **Epic:** E3 Inspect and Operate Evidence
- **Priority:** P0
- **Points:** 3
- **Depends on:** None
- **Status:** Done
- **Completed:** 2026-11-11
- **Description:** Added a GitHub Actions workflow that runs the full test suite
  on Python 3.11 with a two-leg matrix: one leg installs Playwright and Chromium
  and runs live browser checks against the local `examples/demo-web` fixture, and
  the other leg verifies the blocked degradation path without Playwright. The
  workflow starts a localhost server, runs the harness against it, asserts that
  `browser.load` is not `blocked`, and uploads screenshot, console, and network
  evidence as artifacts. `tests/test_browser_smoke.py` exercises the same path
  end to end when Playwright is available and skips otherwise.
- **Acceptance Criteria:**
  - CI fails if `browser.load` is `blocked` for the smoke fixture.
  - The job captures the screenshot and console/network logs as artifacts.
  - A missing-browser matrix leg still verifies the blocked degradation path.
  - No test reaches the public internet.
- **Evidence:** `.github/workflows/ci.yml` defines the matrix and artifact upload.
  `tests/test_browser_smoke.py` passes locally with Playwright and Chromium
  installed, asserting `browser.load` is not `blocked` and that screenshot,
  console-log, and network-log evidence artifacts exist. The missing-browser path
  is covered by `tests/test_blockers.py`. Full suite: 96 passing tests with Python
  3.11.

### QH-011 - Capture HAR, trace, and video with redaction

- **Epic:** E3 Inspect and Operate Evidence
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-010
- **Status:** Done
- **Completed:** 2026-11-11
- **Description:** Added optional HAR, Playwright trace, and video capture to
  the browser Operate layer, controlled by `--browser-har`, `--browser-trace`,
  and `--browser-video`. HAR entries, console locations, DOM snapshots, and
  trace archive text are redacted for authorization, cookie, set-cookie, and
  token-like values before persistence. Artifacts are recorded in the evidence
  manifest with hashes and sizes.
- **Acceptance Criteria:**
  - HAR entries redact `authorization`, `cookie`, and `set-cookie` values.
  - Trace and video paths are recorded in the manifest with hashes and sizes.
  - Capture can be disabled by config without changing check outcomes.
  - A redaction test fails if a known token appears in any artifact.
- **Evidence:** `tests/test_browser_capture.py` runs the browser smoke fixture
  with all three capture flags and asserts that `har`, `trace`, and `video`
  artifacts are present in the manifest with positive byte counts and SHA-256
  hashes. It injects `token=not-a-real-token-value` into a requested asset URL
  and asserts that the raw token does not appear in any persisted artifact while
  `[REDACTED]` does. It also verifies that disabling all flags leaves the
  evidence set unchanged. Full suite: 98 passing tests with Python 3.11.
  `README.md` documents the capture flags.

### QH-012 - Support authenticated flows via storage-state injection

- **Epic:** E3 Inspect and Operate Evidence
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-010
- **Status:** Done
- **Completed:** 2026-11-11
- **Description:** Added `--storage-state` support for Playwright browser runs.
  State can be loaded from an explicit file path or an `env:VAR` secret reference.
  Missing, empty, or expired state blocks browser checks with an actionable
  limitation while anonymous static checks still run. Cookies and tokens are
  redacted before they reach reports or evidence.
- **Acceptance Criteria:**
  - Storage state is loaded only from an explicit path or secret provider.
  - Cookies and tokens never appear in reports or evidence.
  - A missing or expired state blocks authenticated checks with an actionable
    limitation; anonymous checks still run.
  - Tests use a local fixture login and assert redaction end to end.
- **Evidence:** `tests/test_authenticated_browser.py` uses a local login fixture
  and Playwright to create a storage state, then verifies that an authenticated
  page loads, that cookies and tokens are redacted end to end, that missing and
  expired states block browser checks while static checks run, and that
  `env:VAR` secret references work. Full suite: 102 passing tests with Python
  3.11. `README.md` documents `--storage-state`.

### QH-013 - Add a responsive and mobile Operate check pack

- **Epic:** E3 Inspect and Operate Evidence
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-010
- **Status:** Done
- **Completed:** 2026-11-11
- **Description:** Added an optional mobile responsive Operate pack with
  `browser.overflow` and `browser.focus_order` checks. The pack runs at a mobile
  viewport (default 390x844) while existing browser checks remain at the desktop
  viewport. Every browser check records its viewport in `metadata`, and mobile
  overflow failures include the offending element and a screenshot.
- **Acceptance Criteria:**
  - The report records the viewport for every browser check.
  - A page that overflows the mobile viewport fails with a screenshot and the
    offending element.
  - Focus order matches DOM order on the clean fixture.
  - The desktop-only result is unchanged when the mobile pack is disabled.
- **Evidence:** `tests/test_mobile_browser.py` verifies viewport metadata for
  every browser check, a failing overflow check with an offending element and
  screenshot, passing focus order on the clean fixture, and unchanged desktop
  results when `--mobile` is disabled. Full suite: 106 passing tests with Python
  3.11. `README.md` documents `--mobile` and `--mobile-viewport`.

### QH-014 - Calibrate weights and caps against labeled fixtures

- **Epic:** E4 Release Confidence and Reporting
- **Priority:** P0
- **Points:** 8
- **Depends on:** QH-004
- **Status:** Done
- **Completed:** 2026-11-11
- **Description:** Added 14 labeled calibration fixtures covering ship, review,
  and hold outcomes, plus `harness/calibration.py` and the `calibrate` CLI
  command. The calibration report records score, band, gate, expected decision,
  actual decision, match status, and the full formula breakdown for each fixture.
  Two fixtures intentionally disagree with the formula (blocked coverage and
  static-only scope), documenting cases where human judgment differs from the
  weighted-coverage calculation. No weights, penalties, or caps were changed.
- **Acceptance Criteria:**
  - At least 12 labeled fixtures cover pass, warn, and hold outcomes.
  - The calibration report shows score, band, gate, and expected decision per
    fixture.
  - Any weight change includes before/after fixture results and a decision-log
    entry.
  - The score remains explainable from the report breakdown alone.
- **Evidence:** `calibration/fixtures/` contains 14 labeled JSON fixtures.
  `calibration/report.json` is generated by `python -m harness calibrate`.
  `tests/test_calibration.py` verifies fixture count, outcome coverage, report
  fields, and CLI output. Full suite: 109 passing tests with Python 3.11.

The Session 1 foundation is recorded here so the next agent does not rebuild it:

- Deterministic five-phase orchestrator with persisted `.harness/progress.json`,
  atomic writes, append-only events, automatic and manual kill switch, resume
  support, and stable exit codes.
- Three persona contracts with frontmatter validation, check ownership lookup,
  JSON-schema-subset output validation, and explicit refusal handling.
- Static Inspect checks for inventory, tests, CI, dependencies, secrets, unsafe
  patterns, and source accessibility; one fixture finding at each of high,
  medium, and low severity.
- Optional Playwright Operate layer with navigation, screenshot, console,
  network, security, accessibility, content, and link checks; missing browser
  degrades to `blocked`.
- Hash-manifested evidence store with reuse, tamper detection, and a `verify()`
  path.
- Weighted, gap-aware confidence formula with severity penalties, caps, domain
  breakdown, limitations, and the `advisory-v1` gate.
- CLI for `run`, `status`, `report`, `abort`, and `personas`; 34 passing tests;
  sample static fixture and clean browser fixture.

## Decision Log

| ID | Date | Decision | Rationale | Revisit |
| --- | --- | --- | --- | --- |
| D1 | 2026-11-10 | The orchestrator is deterministic code, not an autonomous agent loop. | Release decisions need replayable ordering, budgets, and kill behavior that an LLM cannot guarantee. | If adaptive planning is added, keep it inside a bounded, auditable phase. |
| D2 | 2026-11-10 | Playwright is optional; missing browser blocks checks instead of failing the run. | Static evidence remains useful in air-gapped CI, and a missing dependency must never look like a pass. | Revisit after `QH-010` makes Chromium a CI prerequisite. |
| D3 | 2026-11-10 | Confidence is `weighted-coverage-v1`, not a model-generated probability. | The score must be explainable from weights, coverage, scope, and penalties alone. | Revisit after `QH-014` calibration; keep the published fixture results. |
| D4 | 2026-11-10 | The product stays CLI/JSON-first with no UI in the OSS core. | Agents, CI, and integrations consume JSON; a UI would add a second contract before the first is stable. | `QH-023` may add an onboarding API, not a UI. |
| D5 | 2026-11-10 | Metering is defined before pricing or billing integration. | Usage events are the durable contract; prices and providers will change. | Revisit pricing in `QH-024`; keep event v1 frozen. |
| D6 | 2026-11-10 | Three personas own disjoint checks; a fourth is a product decision, not a count exercise. | Each persona must have a distinct risk domain, tool boundary, and output contract. | Revisit when reliability or performance evidence justifies a new contract. |
| D7 | 2026-11-10 | Run state and evidence live under `.harness/` with an internal `.gitignore`. | The harness must be resumable and auditable without polluting the project or leaking artifacts into commits. | `QH-005` adds retention; `QH-026` adds tenant controls. |
| D8 | 2026-11-10 | Redaction happens before persistence, not only at report time. | A secret written to state or evidence is already leaked to anyone with filesystem access. | `QH-031` turns this into a regression corpus. |
| D9 | 2026-11-10 | Cloud quota enforcement is per action, not per run submission. | A run can be partially useful; blocking only the action that exceeds a limit preserves value and billing accuracy. | Revisit with `QH-021` concurrency tests. |
| D10 | 2026-11-10 | All board tasks start in `Ready`; no task is claimed by documentation alone. | A card is a promise of acceptance criteria, not proof of work. | Move a card only when a test or artifact proves it. |
| D11 | 2026-11-10 | Workflow overrides use a constrained, dependency-free YAML subset and are strict about phase order. | Adding PyYAML would break the dependency-free OSS core, while silently ignoring a malformed override could run the wrong phase contract. | Expand the parser only when a real workflow field requires it; keep unknown phases and fields as errors. |
| D12 | 2026-11-10 | Evidence is checkpointed atomically after each completed check, before progress state, and reconciled on resume. | Progress and manifest are separate files; ordering the evidence write first plus reconciliation prevents a completed check from losing its artifacts, while stale entries from a crashed check are discarded. | Revisit if state and evidence move into a single transactional store. |
| D13 | 2026-11-10 | Built-in and installed checks share a registry; plugins use signed `quality_harness.checks` entry points and a project-local HMAC trust store. | The dependency-free core cannot bundle a public-key library, while symmetric signatures still let an operator pin trusted plugin publishers. Load, signature, and ownership failures are blocked coverage gaps rather than run failures. | Replace HMAC with a public-key scheme when an optional plugin-signing extra or package verifier exists. |
| D14 | 2026-11-10 | Clock and entropy are context-scoped providers; seeded runs use SHA-256 based id generation and an optional frozen clock. | Existing `utc_now`/`new_id` call sites stay intact, production defaults remain real UTC and UUID entropy, and seeded runs can reproduce report bytes without patching every module. | Replace the context-scoped provider with explicit provider arguments if concurrent in-process runs need independent determinism. |
| D15 | 2026-11-11 | `.harness/history.json` is the run index; pruning writes the retained index before deleting evidence and always protects the latest run, latest completed run, and halted runs. | A crash during pruning must not leave history claiming deleted evidence, and operators need a stable latest run plus halted runs for incident review. | Move to per-run state snapshots if pruning must also remove non-latest progress state transactionally. |
| D16 | 2026-11-11 | Persona frontmatter is the only source of check ownership. The catalog's `persona` field is legacy plugin metadata and never drives planning. Built-in zero/multiple ownership is a fatal configuration error; plugin ownership failures are blocked coverage gaps. | A duplicated assignment can drift silently. Deriving owners from the contracts makes disjoint ownership machine-checkable, while preserving D13 for optional plugins. | Remove the legacy field after external plugins have migrated or a plugin schema v2 is published. |
| D17 | 2026-11-11 | Findings are validated against the owning persona schema before normalization. Invalid drafts persist only as redacted `invalid_findings` records, never in scoring, finding counts, or kill-switch evaluation. Three consecutive invalid findings degrade the persona; a valid finding resets the streak. | Normalization can otherwise turn a malformed finding into an apparently valid one. Quarantining preserves the audit trail while preventing contract failures from inflating or deflating release confidence. | Add per-check contract-failure budgets or automatic remediation hints if specialist output quality becomes a recurring gap. |
| D18 | 2026-11-11 | Every blocked check has exactly one persisted `Blocker`, validated against the owning persona's blocker schema and redacted before persistence. A blocked check carries zero findings, and a blocker-bearing outcome is forced to `blocked` even if a plugin reports `passed`. Planning failures materialize blockers before execution; runtime refusals replace them through the same one-per-check path. | A free-text blocked status hides why work stopped and whether it is covered elsewhere. A separate structured record preserves the refusal boundary without polluting findings, scoring, or the kill switch, and makes a blocked check impossible to misreport as a pass. | Add automatic `covered_by` resolution when another check's evidence covers the same risk, or add per-blocker expiry when a target changes. |
| D19 | 2026-11-11 | `personas lint` aggregates all contract errors across all files rather than failing on the first error, exiting 1 on validation failure. `personas scaffold <id>` generates complete, valid contracts with a `plugin.<id>.placeholder` check so they pass lint immediately without duplicating ownership of built-in checks; lint treats these matching placeholders as advisory warnings. | Halting on the first file error forces tedious fix-and-rerun loops. Using a namespaced placeholder allows scaffolding a valid contract into an existing repo where all built-in checks are already claimed, keeping disjoint ownership invariant while avoiding false-positive errors. | When plugin authoring is expanded, allow `scaffold` to generate a companion plugin stub or register the placeholder in local dev configs. |
| D20 | 2026-11-11 | QH-014 publishes 14 labeled calibration fixtures and a `calibrate` CLI command; the current `weighted-coverage-v1` weights, penalties, and caps are retained without changes. | The fixtures make score behavior auditable and document two intentional disagreements (blocked coverage and static-only scope), but no fixture evidence currently justifies changing the published formula. | Revisit weights only if a labeled historical-outcome corpus shows systematic disagreement. |
