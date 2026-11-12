# quality-harness

`quality-harness` is a deterministic, risk-based testing harness for release
decisions. It inspects a source tree, operates a live target when a browser is
available, stores hashed evidence, and emits a machine-readable confidence score
with findings.

It is not a bug oracle and not a probability that the release has zero defects.
The score is scoped engineering judgment: it states what was executed, what was
not, and which findings still block an advisory ship decision.

## Why this shape

The harness follows five fixed phases:

```text
Risk Analysis -> Test Planning -> Execution -> Evidence Collection -> Confidence Scoring
```

Every phase writes durable state to `.harness/`. The orchestrator, not a model,
decides check order, budget, scoring, and whether the kill switch fires. Three
specialist personas own disjoint checks and declare their tools, output schema,
blocker schema, and refusal rules in `personas/`.

## Requirements

- Python 3.8 or newer. This repository is developed and tested with Python 3.11.
- No runtime dependencies for static inspection.
- Playwright is optional and only needed for the live Operate layer.

## Quick start

```bash
# Static Inspect run against the intentionally imperfect fixture.
python -m harness run \
  --target examples/sample-repo \
  --no-browser \
  --root .harness-demo \
  --personas-dir personas

# Validate persona contracts.
python -m harness personas --personas-dir personas
python -m harness personas lint --personas-dir personas

# Scaffold a new specialist contract without copying by hand.
python -m harness personas scaffold reliability --personas-dir personas

# Run labeled confidence calibration fixtures.
python -m harness calibrate --fixtures-dir calibration/fixtures

# Use a versioned policy file for gates and weights.
python -m harness run --target examples/sample-repo --no-browser \
  --policy policies/advisory-v1.json --root .harness-demo

# Export SARIF and JUnit from the latest report.
python -m harness sarif --root .harness-demo --output report.sarif
python -m harness junit --root .harness-demo --output junit.xml

# Sign and verify the latest report.
python -m harness sign-report --root .harness-demo --key "$REPORT_SIGNING_KEY"
python -m harness verify-report --root .harness-demo --signature .harness-demo/.harness/report.sig --key "$REPORT_SIGNING_KEY"

# Compare with the previous run for the same target.
python -m harness run --target examples/sample-repo --no-browser \
  --fail-on-score-regression 5 --root .harness-demo

# Emit the canonical usage events to a custom local sink.
python -m harness run --target examples/sample-repo --no-browser \
  --workspace local --metering-sink .harness-demo/usage.jsonl \
  --root .harness-demo

# Suppress a known finding baseline with an owner and expiry.
python -m harness suppress add <fingerprint> \
  --owner alice --reason "accepted risk" --ticket T-1 \
  --expires 2027-01-01T00:00:00Z --root .harness-demo

# Inspect the latest persisted run.
python -m harness status --root .harness-demo
python -m harness report --root .harness-demo
```

The `run` command prints one JSON report to stdout. Progress goes to stderr, so
stdout can be piped directly into `jq` or a CI parser.

### Versioned workflows

The built-in `release-confidence` workflow lives in code. To override or add a
versioned definition, place a YAML file under `.harness/workflows/` beneath the
run root and select it by id:

```bash
python -m harness run --target examples/sample-repo --no-browser \
  --workflow release-confidence --root .harness-demo
```

```yaml
schema_version: 1
id: release-confidence
name: Release Confidence
version: "2.0"
phases:
  - name: risk_analysis
    title: Risk Analysis
    description: Rank risks from the target inventory.
  - name: test_planning
    title: Test Planning
    description: Plan intent-linked checks.
  - name: execution
    title: Execution
    description: Run checks and enforce the kill switch.
  - name: evidence_collection
    title: Evidence Collection
    description: Finalize hashed evidence.
  - name: confidence_scoring
    title: Confidence Scoring
    description: Score confidence and gate the release.
```

The five phase names and their order are invariant. A malformed file, an unknown
phase, or a missing required field produces a typed `WorkflowError`; it never
silently falls back. The built-in definition is used only when no override with
that id exists. `status` and `report` expose the workflow id and version that ran.

### Resume and crash safety

After every completed check, the harness atomically writes an evidence
checkpoint and then the progress state. A provisional manifest has
`"checkpoint": true`; the evidence phase replaces it with the final manifest.

```bash
python -m harness run --target examples/sample-repo --no-browser \
  --root .harness-demo --resume
```

Resume replays only checks that were pending, running, or errored. Completed
checks keep their findings and evidence entries unchanged, and evidence owned by
a crashed check is discarded before that check reruns. Resume is rejected when
the target or workflow version differs from the saved run. A run halted by the
kill switch cannot be resumed; start a new run after investigating.

### Check plugins

Built-in checks and installed plugins are discovered through the same
`CheckRegistry`. A plugin package exposes a `CheckPlugin` from the
`quality_harness.checks` entry-point group. Its definition declares the check
id, kind, required input, base weight, intent, and expected outcome; its
evaluator receives a `PluginContext` with the target inventory and the run's
evidence store. Check ownership is not part of the definition: it is derived
from the persona contracts, which are the single source of truth.

Plugins are signed with HMAC-SHA256 over the canonical definition and entry-point
value. The trust store stays outside source control:

```json
{
  "keys": {
    "example-key": "replace-with-a-random-secret"
  }
}
```

Write it to `.harness/plugins/trusted_keys.json` with mode `0600`, sign the entry
point with `harness.plugins.sign_plugin`, and put the returned
`hmac-sha256:<key_id>:<digest>` value in `CheckPlugin.signature`.

A plugin check must be listed by exactly one persona in its frontmatter. The
persona contract, not the check definition, owns it. Duplicate ids, unknown
input requirements, zero weights, unsigned packages, import failures, and
ownership failures are rejected as blocked checks with the limitation recorded;
the rest of the run continues. An unowned or multiply owned built-in check is a
configuration error: duplicate ownership is rejected before execution, and
planning fails with the offending check id. Browser plugin kinds are not
supported yet. See `examples/plugins/README.md` and
`examples/plugins/sample_check.py`.

### Reproducible runs

Production runs use real UTC time and UUID entropy. For deterministic reports,
inject a seed and a frozen clock:

```bash
python -m harness run --target examples/sample-repo --no-browser \
  --personas-dir personas \
  --seed 1234 \
  --clock-mode frozen \
  --frozen-clock 2026-01-02T03:04:05Z \
  --root .harness-demo
```

- `--seed` controls finding ids and the deterministic ordering of generated ids.
- `--clock-mode frozen` with `--frozen-clock` controls every timestamp in state,
  evidence, and the report.
- `--generated-at` overrides only the report's `generated_at` value; it does not
  change run timestamps.
- The report records the `determinism` object with `seed`, `clock_mode`, and the
  entropy mode (`uuid4` or `seeded`).

Two runs with the same seed and frozen clock produce identical findings, ids,
weights, phase details, and confidence breakdown. Omit both flags to keep
production behavior.

### Run history and retention

Every run records a history entry in `.harness/history.json` with the run id,
target, status, score, decision, finding counts, evidence path, and timestamps.
The index is written atomically after the run finishes.

```bash
# List every run newest-first.
python -m harness status --all --root .harness-demo

# Keep at most three runs after a run completes.
python -m harness run --target examples/sample-repo --no-browser \
  --root .harness-demo --keep-runs 3

# Prune by count or age, or preview the decision.
python -m harness prune --root .harness-demo --keep-runs 3
python -m harness prune --root .harness-demo --older-than-days 30
python -m harness prune --root .harness-demo --keep-runs 3 --dry-run
```

Pruning removes both the history entry and the run's evidence directory. The
latest run, the latest completed run, and every halted run are always retained.
The current `progress.json` and `report.json` are never removed by pruning.

### Usage metering events

Every local run emits the same versioned usage events that a cloud deployment
will forward to its ledger. The default sink is
`.harness/metering/events.jsonl`; override it with `--metering-sink`, or set the
workspace with `--workspace` (default: `local`).

```bash
python -m harness run --target examples/sample-repo --no-browser \
  --workspace local --root .harness-demo

head -n 1 .harness-demo/.harness/metering/events.jsonl
```

The v1 event names are frozen:

- `run_started` — one unit `run`.
- `check_executed` — one unit `check`.
- `browser_minute_consumed` — a positive `browser_minute` quantity.
- `evidence_byte_stored` — the stored artifact size in `byte`.
- `export_generated` — one unit `export` for SARIF, JUnit, and signature exports.

Every event includes `workspace`, `run`, `quantity`, `unit`, `timestamp`, and
`source`, plus `event_id`, `event_type`, `idempotency_key`, and event-specific
`metadata`. Duplicate delivery with the same idempotency key is ignored. The
normative JSON Schemas live under `schemas/metering/`: one envelope schema and
one schema per event type. `harness.metering.validate_event` performs the same
checks without adding a runtime dependency.

A run report includes a `metering` summary with the sink, workspace, event count,
and per-type counts. Metering events are operational usage records, not pricing
or billing policy; the OSS core remains unlimited and account-free.

### Workspace authentication

The cloud boundary uses workspaces and hashed, scoped API keys. This is a
dependency-free module (`harness.auth`) and an operator CLI; normal OSS runs do
not require an account. State lives in `.harness/cloud/auth.json` and the
append-only audit trail lives in `.harness/cloud/auth-audit.jsonl`.

```bash
# Create a workspace. The workspace id is required before any quota is consumed.
python -m harness auth create-workspace --name "Acme" --id ws_acme

# Issue a key with explicit scopes: run, read, export, or admin.
python -m harness auth create-key --workspace ws_acme \
  --scope run --scope read --label ci

# The secret is printed exactly once. Store it in your secret manager.
# The durable store keeps only a per-key salt and SHA-256 hash plus a public
# prefix used for lookup and rotation.
python -m harness auth list-keys --workspace ws_acme

# Rotation returns the replacement secret once and revokes the old key.
python -m harness auth rotate-key --key key-...
```

`admin` implies the other scopes. Authentication failures are machine-readable
401/403 responses with distinct codes and no workspace identifiers:
`missing_api_key`, `malformed_api_key`, `invalid_api_key`, `expired_api_key`,
`revoked_api_key`, and `insufficient_scope` (403). The CLI prints the HTTP status
as `http_status` and exits 1 for an authentication or authorization failure.
`--token env:VAR` reads a token from the environment instead of argv.

Run ids are claimed by exactly one workspace. `authorize-run` returns
`run_not_found` for both a guessed id and another workspace's real id, so the
response cannot confirm tenant existence. `claim-run` returns
`run_already_claimed` when a different workspace tries to take an existing id.

```bash
python -m harness auth claim-run --workspace ws_acme --run run-123
python -m harness auth authorize-run --token env:QH_API_KEY --run run-123
```

### Monthly quota enforcement

Cloud callers opt in to monthly allowances with a versioned quota plan
(`quality-harness.quota-plan/v1`). The plan is a JSON object with a soft and hard
allowance for each billable resource. A numeric value is shorthand for a hard
limit.

```json
{
  "id": "free",
  "version": "2026-11",
  "period": "monthly",
  "limits": {
    "runs": {"soft": 10, "hard": 20},
    "checks": {"soft": 100, "hard": 150},
    "browser_minutes": {"soft": 30, "hard": 60},
    "evidence_bytes": {"soft": 5000000, "hard": 10000000}
  }
}
```

The OSS core stays unlimited: without a plan, or when the synthetic `local`
workspace is explicitly selected without enforcement, quota calls return an
allowed decision and do not create quota state. Supplying a plan opts in to
enforcement. The enforcement is per action, not per run submission, so a run can
keep the work it already completed.

```bash
# Show usage, remaining allowance, and the monthly reset time.
python -m harness quota show --root .harness-cloud \
  --workspace ws_acme --plan quota-free.json

# Consume one action. Exit 1 means the action was blocked by a hard limit.
python -m harness quota consume --root .harness-cloud \
  --workspace ws_acme --plan quota-free.json \
  --resource checks --quantity 1
```

A soft-limit warning is emitted once per resource per billing period per
workspace and appended to `.harness/quota/events.jsonl`. Once usage crosses the
hard limit, the next action returns HTTP 429 (also exposed as `http_status` in
the CLI payload) with the exact `limit`, `used`, `requested`, and `reset_at`.
Usage from earlier actions remains in `.harness/quota/usage.json` and is visible
in `quota show`; the blocked action does not increment usage.

Every successful decision carries response headers, including
`X-QH-Quota-Remaining-Checks`, `X-QH-Quota-Limit-Checks`,
`X-QH-Quota-Reset`, and the generic `X-Quota-Remaining` aliases. Hard-limit
responses also include `Retry-After`. Consumption is guarded by a process-local
lock plus an advisory file lock, so parallel requests cannot overspend a limit.
`harness.quota.QuotaStore` is the enforcement primitive the cloud execution
queue will use in `QH-025`; metering event names map directly to quota resources
through `consume_metering_event`. Cloud adapters should authenticate first and
build the store with `QuotaStore.from_auth_context(root, context, plan)` so the
tenant id comes from the scoped `AuthContext`, never from the request body.

### Append-only usage ledger and invoices

Metering events are aggregated into an append-only, hash-chained ledger under
`.harness/ledger/entries.jsonl`. A monthly period can be closed, after which
ordinary usage events are rejected unless they are already present. Late work is
recorded as an explicit adjustment that references the period close id or a
correction that references the original ledger entry. Each entry stores its
quantity, unit price, currency, and amount, so historical invoices remain
reproducible when a price book changes.

```bash
# Aggregate metering events into the ledger.
python -m harness ledger ingest --root .harness-cloud --workspace ws_acme \
  --prices prices.json

# Close the billing month.
python -m harness ledger close --root .harness-cloud --workspace ws_acme \
  --period 2026-11 --at 2026-12-01T00:00:00Z

# Record an explicit correction or late adjustment.
python -m harness ledger adjust --root .harness-cloud --workspace ws_acme \
  --period 2026-11 --quantity -1 --reason "duplicate check" \
  --reference ledger-1234567890

# Render an invoice as JSON or CSV.
python -m harness ledger invoice --root .harness-cloud --workspace ws_acme \
  --period 2026-11 --format json
python -m harness ledger invoice --root .harness-cloud --workspace ws_acme \
  --period 2026-11 --format csv --output invoice.csv

# Audit the ledger and compare an invoice to quantity times price.
python -m harness ledger verify --root .harness-cloud
python -m harness ledger reconcile --root .harness-cloud \
  --workspace ws_acme --period 2026-11
```

Ledger state is `.harness/ledger/entries.jsonl` and `.harness/ledger/periods.json`.
`ledger verify` checks sequence and SHA-256 continuity. Prices are supplied
through a versioned price book (`quality-harness.price-book/v1`); the OSS core
does not embed commercial pricing. `harness.ledger.LedgerStore` is the
primitive for cloud delivery and supports `from_auth_context` binding so tenant
identity comes from the scoped `AuthContext`.

### Self-serve signup and signed entitlements

Onboarding is available as a local-first API with no UI in the OSS core. A signup
request is idempotent by request id and creates one workspace, one owner key, a
quota-backed free allowance, and a signed entitlement.

```bash
python -m harness signup create --root .harness-cloud \
  --request-id signup-1 --name Acme --plan free-plan.json \
  --effective-at 2026-11-12T00:00:00Z

python -m harness signup usage --root .harness-cloud --workspace ws_acme
python -m harness signup select-plan --root .harness-cloud \
  --workspace ws_acme --plan pro-plan.json \
  --effective-at 2026-12-01T00:00:00Z

python -m harness signup verify --root .harness-cloud \
  --entitlement entitlement.json
```

Entitlements are HMAC-SHA256 signed and verified locally. They encode the
workspace, tier, limits, and effective billing period; tampering, workspace or
period mismatch, and expiry all fail verification. Plan changes record an
effective date and are rejected when they would place current usage above the
new limits. `harness.signup.SignupStore` and `harness.entitlements` expose the
same API; cloud adapters should resolve tenant identity first and bind quota
through `QuotaStore.from_auth_context`.

### Feature-gated metered billing

Billing is disabled by default. Enable it only for allow-listed workspaces in
sandbox mode with `QH_BILLING_ENABLED=true`, `QH_BILLING_SANDBOX=true`, and
`QH_BILLING_WORKSPACES=ws_acme`, or with a billing flag JSON file. A billing
kill switch at `.harness/billing/KILL` disables writes without stopping the
harness.

```bash
python -m harness billing status --root .harness-cloud --workspace ws_acme
python -m harness billing sync --root .harness-cloud --workspace ws_acme \
  --period 2026-11 --invoice-id inv-1 --secret-store secrets.json
python -m harness billing disable --root .harness-cloud --reason "operator stop"
python -m harness billing enable --root .harness-cloud
```

`harness.billing.BillingStore` uses ledger invoices as the billing source of
truth, creates sandbox customers/subscriptions, retries transient provider
failures idempotently, and reports `degraded` without changing run or quota
state. Secrets are resolved through `SecretManager` implementations.

### Cloud run queue and tenant isolation

Cloud runs are queued durably and executed in per-tenant roots under
`.harness/tenants/<workspace>/`.

```bash
# Submission and polling are performed through harness.queue.JobQueue in cloud
# adapters. A job id is returned immediately and can be polled by its owner.
python - <<'PY'
from pathlib import Path
from harness.queue import JobQueue

queue = JobQueue(Path(".harness-cloud"), personas_dir=Path("personas"))
job = queue.submit("ws_acme", "/path/to/target", options={"no_browser": True})
print(job["job_id"])
print(queue.process_next())
print(queue.metrics())
PY
```

Jobs are owner-scoped: another workspace cannot read the job or its evidence.
Cancellation writes the tenant kill file, so the orchestrator stops remaining
checks and preserves partial evidence. Crashed running jobs can be recovered
with `recover()` and retried without double-billing the same check because
metering events are idempotent by run id. `harness.queue.JobQueue` exposes
queue depth and run-duration metrics.

### Tenant retention, export, and deletion

Each workspace has an explicit retention policy (default 30 days). Exports are
signed ZIP archives with a SHA-256 manifest, and deletion is soft for a grace
period before hard deletion.

```bash
python - <<'PY'
from pathlib import Path
from harness.retention import RetentionStore

store = RetentionStore(Path(".harness-cloud"))
print(store.policy("ws_acme"))
print(store.export("ws_acme"))
print(store.soft_delete("ws_acme", grace_days=7))
print(store.hard_delete("ws_acme", force=True))
PY
```

Hard deletion removes tenant roots, queue records, cached exports, billing and
quota state, and signup records while preserving an audit log. Archives are
verified with `verify_export`.

### Signed releases and containers

The `quality-harness` console script is declared in `pyproject.toml`. Release
artifacts are built, signed with Sigstore, and attested in
`.github/workflows/release.yml`. The core container image is `Dockerfile`; the
browser image is `Dockerfile.browser`.

```bash
docker build -t quality-harness .
docker build -f Dockerfile.browser -t quality-harness-browser .
```

### Live browser checks

```bash
pip install ".[browser]"
python -m playwright install chromium

# Serve examples/demo-web first, then point the harness at it.
python -m harness run --target http://127.0.0.1:8765/ \
  --source examples/sample-repo \
  --personas-dir personas \
  --root .harness-live
```

If Playwright or Chromium cannot start, browser checks are reported as
`blocked` with an actionable reason. They are never converted into passes, and
the run continues with source evidence.

Authenticated flows can load a Playwright storage state from an explicit file or
an environment variable:

```bash
python -m harness run --target http://127.0.0.1:8765/ \
  --source examples/sample-repo \
  --storage-state .harness/auth-state.json \
  --root .harness-live

# Or from a secret provider:
python -m harness run --target http://127.0.0.1:8765/ \
  --source examples/sample-repo \
  --storage-state env:AUTH_STATE \
  --root .harness-live
```

If the state is missing, empty, or expired, browser checks are blocked with an
actionable limitation while anonymous static checks still run. Cookies and
tokens are redacted before they reach reports or evidence.

The responsive/mobile Operate pack is optional and runs at a mobile viewport:

```bash
python -m harness run --target http://127.0.0.1:8765/ \
  --source examples/sample-repo \
  --mobile --mobile-viewport 390x844 \
  --root .harness-live
```

Every browser check records its viewport in `metadata`. The pack adds
`browser.overflow` and `browser.focus_order`; disabling `--mobile` leaves the
desktop browser result unchanged.

Optional browser capture is off by default and can be enabled per run:

```bash
python -m harness run --target http://127.0.0.1:8765/ \
  --source examples/sample-repo \
  --browser-har --browser-trace --browser-video \
  --root .harness-live
```

HAR, trace, and video artifacts are recorded in the evidence manifest with
hashes and sizes. HAR entries and trace text redact `authorization`, `cookie`,
`set-cookie`, and token-like values before persistence. Disable all three flags
to keep the run's evidence set unchanged.

## What the report contains

- `decision`: `ship`, `review`, or `hold`, with a rationale.
- `determinism`: the seed, clock mode, and entropy mode used for the run.
- `workflow`: id, name, version, and the canonical phase order that ran.
- `confidence`: score, band, coverage, limitations, penalties, domain scores,
  formula breakdown, and gate reasons.
- `metering`: the v1 usage-event sink, workspace, total event count, and counts
  by event type. Events themselves live in the file sink, not the report.
- `findings`: severity, consequence, location, reproduction, evidence,
  remediation, verification, fingerprint, and owning persona.
- `invalid_findings`: quarantined findings with the owning persona, check id,
  validation errors, and a redacted payload. They never affect scoring, finding
  counts, or kill-switch evaluation.
- `blockers`: one structured record per blocked check, with the attempted
  action, the boundary that stopped it, the evidence needed to unblock it, and
  whether another check covers the gap. `skipped` checks are not blockers.
  `status` exposes the same records plus per-persona and per-check counts.
- `contract_failures`: per-persona schema-failure counts and the list of
  personas marked degraded after three consecutive invalid findings.
- `checks`: planned checks, status, weight, actual result, intent, and evidence.
- `coverage`: executed, blocked, skipped, errored, planned-not-run, and blocker
  counts by persona and domain. Blockers stay visible as coverage gaps.
- `phases`, `risks`, `personas`, `inventory`, kill-switch state, and evidence
  summary. Each persona entry includes its contract-failure count, current
  streak, and `degraded` flag.

Example shape:

```json
{
  "schema_version": "quality-harness.report/v1",
  "status": "completed",
  "determinism": {"seed": null, "clock_mode": "real", "entropy": "uuid4"},
  "decision": {"decision": "hold", "rationale": "..."},
  "confidence": {"score": 30.2, "band": "low", "gate": {"status": "fail"}},
  "findings": [{"severity": "high", "title": "..."}],
  "blockers": [],
  "coverage": {"total_checks": 7, "executed": 7, "blocked": 0, "blockers": 0}
}
```

## Scoring

The formula is `weighted-coverage-v1`:

```text
base   = quality * coverage_factor * scope_factor
final  = clamp(base - severity_penalty, cap rules)
```

- `quality` is the passed weight divided by executed weight.
- `coverage_factor` is `0.25 + 0.75 * (executed weight / planned weight)`.
- `scope_factor` is `1.0` for source and live evidence together, `0.9` for one
  side only, and `0.75` when nothing ran.
- Severity penalties are 45 critical, 20 high, 8 medium, 2 low, and 0 info.
  Suspected findings carry half penalty; duplicate fingerprints count once.
- A critical finding caps the score at 30. Two or more high findings cap it at
  65. A kill-switch halt caps it at 25.

The gate policy is `advisory-v1`: critical findings or a score below 60 fail;
high findings, incomplete coverage, or a score below 80 warn; otherwise pass.

## Kill switch and exit codes

The automatic threshold is `--kill-on critical` by default. `--kill-on high`
halts on either severity; `--kill-on never` records findings without halting.
On a halt, remaining checks are marked `skipped`, the run status becomes
`halted`, and the report decision becomes `hold`.

A manual stop can be requested from another shell:

```bash
python -m harness abort --root .harness-demo --reason "operator stop"
```

The running harness checks `.harness/KILL` between checks and phases.

| Exit | Meaning |
| --- | --- |
| 0 | Run finished and no configured policy threshold failed. |
| 1 | `--fail-on` or `--min-score` policy failed. |
| 2 | Harness error or failed run. |
| 3 | Run halted by the kill switch. |
| 130 | Operator interrupted. |

## Evidence

Every artifact is written below `.harness/evidence/<run_id>/` and recorded in
`manifest.json` with its SHA-256 hash, size, content type, kind, and owning
check. The evidence phase re-hashes every artifact and records mismatches or
missing files. Sensitive values are redacted before they reach state, evidence,
or the report.

## Personas

| Persona | Owns |
| --- | --- |
| `functional` | Inventory, tests, CI, dependencies, load, console, network, content, links |
| `security` | Secret scan, unsafe code patterns, transport and header defaults |
| `accessibility` | Source markup heuristics and rendered accessibility signals |

Each file declares YAML frontmatter, role, available tools, a JSON output
schema, a JSON blocker schema, and refusal handling. Check ownership is derived
from the frontmatter `checks` lists. Every built-in check must be listed by
exactly one persona; an unowned check fails planning and duplicate ownership is
rejected before execution. `personas lint` checks contracts across all files,
and `personas scaffold <id>` creates a starter specialist contract that passes
lint unchanged. See `personas/README.md`.

A finding that violates its owner's output schema is quarantined before it can
enter state as a normal finding. The report keeps the persona, check id,
validation errors, and a redacted payload under `invalid_findings`. Quarantined
findings are excluded from confidence penalties and kill-switch evaluation, but
the failure is counted under `contract_failures`; three consecutive invalid
findings from one persona mark that persona `degraded`.

## Repository layout

```text
harness/            Core engine, CLI, checks, plugins, evidence, scoring, browser layer
schemas/metering/   Versioned usage-event envelope and per-event JSON Schemas
personas/           Specialist Markdown contracts
tests/              Unit and end-to-end tests (unittest compatible)
examples/sample-repo/  Static fixture with a high, medium, and low finding
examples/demo-web/  Clean page for manual browser runs
examples/plugins/   Signed sample check plugin and signing instructions
BOARD.md            Epics, ready tasks, blockers, decisions
AGENTS.md           Contract for coding agents working this board
```

## Continuous integration

`.github/workflows/ci.yml` runs the unit and contract tests on Python 3.11.
One matrix leg installs Playwright and Chromium, serves `examples/demo-web` on
localhost, runs the browser Operate checks against it, and uploads screenshot,
console, and network evidence as artifacts. The other leg verifies the blocked
degradation path without Playwright. No test reaches the public internet.

## Tests

```bash
python -m unittest discover -s tests -v
# or, if pytest is installed:
python -m pytest
```

## Known limits

- Static checks are heuristics, not language-aware data-flow analysis.
- Browser checks cover one page load, not a full customer journey.
- Automated accessibility checks do not establish WCAG conformance.
- Security checks are bounded and non-destructive; they are not a penetration
  test or a certification.
- The OSS harness remains account-free and unlimited. Workspace authentication,
  quota enforcement, and canonical usage events are dependency-free local
  modules; a hosted multi-tenant service, billing provider, and dashboard remain
  cloud-epic work on `BOARD.md`.
