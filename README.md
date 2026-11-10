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
and refusal rules in `personas/`.

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

## What the report contains

- `decision`: `ship`, `review`, or `hold`, with a rationale.
- `workflow`: id, name, version, and the canonical phase order that ran.
- `confidence`: score, band, coverage, limitations, penalties, domain scores,
  formula breakdown, and gate reasons.
- `findings`: severity, consequence, location, reproduction, evidence,
  remediation, verification, fingerprint, and owning persona.
- `checks`: planned checks, status, weight, actual result, intent, and evidence.
- `coverage`: executed, blocked, skipped, errored, and planned-not-run counts.
- `phases`, `risks`, `personas`, `inventory`, kill-switch state, and evidence
  summary.

Example shape:

```json
{
  "schema_version": "quality-harness.report/v1",
  "status": "completed",
  "decision": {"decision": "hold", "rationale": "..."},
  "confidence": {"score": 30.2, "band": "low", "gate": {"status": "fail"}},
  "findings": [{"severity": "high", "title": "..."}],
  "coverage": {"total_checks": 7, "executed": 7, "blocked": 0}
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
schema, and refusal handling. See `personas/README.md`.

## Repository layout

```text
harness/            Core engine, CLI, checks, evidence, scoring, browser layer
personas/           Specialist Markdown contracts
tests/              Unit and end-to-end tests (unittest compatible)
examples/sample-repo/  Static fixture with a high, medium, and low finding
examples/demo-web/  Clean page for manual browser runs
BOARD.md            Epics, ready tasks, blockers, decisions
AGENTS.md           Contract for coding agents working this board
```

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
- The OSS harness has no multi-tenancy, metering, or billing. That is the next
  business epic on `BOARD.md`, and it is intentionally separated from the
  deterministic core.
