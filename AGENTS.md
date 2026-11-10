# Agent Contract

This file is the operating contract for any coding agent working on
`quality-harness`. `BOARD.md` is the work queue; this file is how to use it
without weakening the product.

## Mission

Make release confidence inspectable. The harness must tell an operator what was
executed, what was not, what still blocks a ship decision, and why the score is
what it is. The business goal is a self-serve, usage-based cloud tier built on a
deterministic OSS core, not a bespoke services engagement.

## Read order

1. `README.md` for the user-facing contract and scoring formula.
2. `BOARD.md` for the current task and its acceptance criteria.
3. `AGENTS.md` (this file) for invariants and definition of done.
4. `personas/README.md` before adding or changing a specialist.
5. The relevant module and its tests before editing.

## Non-negotiable invariants

- The phase order is fixed: Risk Analysis -> Test Planning -> Execution ->
  Evidence Collection -> Confidence Scoring. Do not let a check or persona
  reorder phases.
- The orchestrator, not a model, owns ordering, budget, scoring, and the kill
  switch. A specialist may propose a finding; it may not set the final score.
- `run` prints one JSON report to stdout. Progress and warnings go to stderr.
  Never mix human prose into stdout.
- State lives under `.harness/`. Writes must stay atomic. A crash must not leave
  a half-written `progress.json` or a manifest that claims an artifact exists.
- Every finding needs an evidence path under `.harness/evidence/<run_id>/` with a
  SHA-256 hash in the manifest. No evidence, no finding.
- Redact before persistence. A credential, cookie, authorization header, or
  private key must never reach state, evidence, logs, stdout, or a report.
- A blocked, skipped, errored, or refused check is a coverage gap. It is never a
  pass, and it never raises confidence.
- The kill switch halts remaining checks, marks them skipped, sets the run to
  `halted`, and forces a `hold` decision. Do not add a path that resumes a halted
  run automatically.
- The OSS core must remain dependency-free. Optional browser and cloud behavior
  loads behind explicit extras or feature flags.
- No UI in the OSS core. Onboarding and usage APIs are allowed; a dashboard is a
  separate product decision.
- Persona checks must be disjoint. A check owned by zero personas, or by more
  than one, is a configuration error.
- Do not lower a severity to make a gate pass. Fix the evidence, add a justified
  suppression through the board task that owns it, or leave the finding open.

## Board protocol

1. Choose the lowest-numbered task in `Ready` whose dependencies are all `Done`.
   If a higher-priority `P0` exists and is unblocked, state why you are taking it
   out of order in the change description.
2. Claim one task per change. Move it to `In Progress` and state the exact
   acceptance test you will run.
3. Read the task's acceptance criteria literally. Do not add adjacent scope; put
   discovered work in `Backlog` as a new story with a new higher-numbered task.
4. Keep dependency chains valid: a task may depend only on a lower-numbered
   task, and no cycle may exist. Renumbering is not allowed after work starts.
5. Move the task to `Done` only when every acceptance criterion has a passing
   test or a verified artifact. Update the Decision Log when a choice changes
   the architecture, scoring, security posture, or commercial model.
6. If you are blocked, add a row to `Blockers` with the impact and the exact
   unblock action. Do not silently park a task in `In Progress`.
7. Never edit a completed task's acceptance criteria to match the code. If the
   outcome changed, add a new task and link the old one.

## Setup and commands

Use Python 3.8+; this repository is verified with Python 3.11 at
`/usr/local/bin/python3.11`.

```bash
# Validate the environment and persona contracts.
python -m harness --version
python -m harness personas --personas-dir personas

# Static smoke run (the fixture intentionally has high, medium, and low findings).
python -m harness run \
  --target examples/sample-repo \
  --no-browser \
  --personas-dir personas \
  --root .harness-demo

# Kill-switch behavior.
python -m harness run \
  --target examples/sample-repo \
  --no-browser \
  --personas-dir personas \
  --kill-on high \
  --root .harness-kill

# Persisted state and report.
python -m harness status --root .harness-demo
python -m harness report --root .harness-demo

# Manual stop from another shell.
python -m harness abort --root .harness-demo --reason "operator stop"

# Tests.
python -m unittest discover -s tests -v
python -m pytest   # if installed
```

Live browser work needs `pip install ".[browser]"` and
`python -m playwright install chromium`. Serve `examples/demo-web` on localhost
for a deterministic target. If Chromium is unavailable, verify that checks are
`blocked` with an actionable limitation rather than passed.

## Definition of done

A task is done only when all of the following are true:

- The acceptance criteria pass in a command another agent can rerun.
- New behavior has a unit or end-to-end test; bug fixes have a regression test.
- The JSON report or metering/event contract changed in a backward-compatible
  way, or the version was bumped with a migration note.
- Evidence remains hashed and redacted; no fixture value can leak into a report.
- The task card is updated, the Decision Log is updated when relevant, and any
  new work is captured as a higher-numbered task.
- `python -m unittest discover -s tests` passes from a clean checkout.
- The story is committed as one story-scoped commit and pushed to `origin`.
  Report the commit SHA and the remote branch in the completion summary.

## File map

| Path | Responsibility |
| --- | --- |
| `harness/orchestrator.py` | Five-phase state machine, kill switch, resume, report finalization |
| `harness/workflows.py` | Versioned workflow loader, phase-order validation, built-in fallback |
| `harness/cli.py` | Machine-first CLI and exit codes |
| `harness/checks.py` | Stable check catalog and intent-linked planning |
| `harness/personas.py` | Frontmatter parsing and output-schema validation |
| `harness/static_analysis.py` | Source inventory and deterministic Inspect checks |
| `harness/browser.py` | Optional Playwright Operate layer and evidence capture |
| `harness/scoring.py` | `weighted-coverage-v1` confidence math and gate policy |
| `harness/evidence.py` | Hashed artifact store, manifest, and verification |
| `harness/state.py` | Atomic progress state, events, and manual kill file |
| `harness/report.py` | Stable `quality-harness.report/v1` report |
| `personas/` | Specialist contracts with tools, schema, and refusals |
| `tests/` | Unit and end-to-end contract tests |
| `examples/` | Static fixture and clean browser fixture |

## Security and data handling

- Treat every target as hostile until its URL passes the validation in `QH-033`.
- Do not add a tool or check that exploits, brute-forces, submits real
  transactions, creates accounts, or reads another tenant's data.
- Keep secrets out of prompts, test fixtures, logs, and commit history. Use
  synthetic values that are clearly fake.
- A suspected live credential must be reported as a blocker and escalated, not
  copied into a finding.
- Evidence is untrusted input too. Validate paths and never execute an artifact.

## Cloud-tier guardrails

- Metering events are the billing source of truth. Emit them from the action
  that consumes the resource, not from a periodic guess.
- A quota failure must block only the action that exceeds the limit. Preserve
  already completed work and report partial billing accurately.
- Tenant identity must be resolved before any quota, evidence, or job lookup.
  Never accept a tenant id from the request body when a scoped token exists.
- Billing integrations stay behind a feature flag and a kill switch. A provider
  outage must not stop the core harness.
- Do not add pricing or plan names to the OSS core. Keep commercial policy in
  cloud configuration.

## First session summary

On 2026-11-10 the first session built the deterministic OSS foundation of
`quality-harness`: a five-phase orchestrator with atomic `.harness/` state,
resume support, automatic and manual kill switches, hashed evidence manifests,
three specialist persona contracts with schema validation and refusal handling,
source Inspect checks, an optional Playwright Operate layer that degrades to
`blocked` when Chromium is absent, a weighted and gap-aware confidence report
with `advisory-v1` gates, and a machine-first CLI exposing `run`, `status`,
`report`, `abort`, and `personas`. The session studied CARBON's orchestrator,
specialist, skill-contract, and evidence patterns, then implemented only the
deterministic subset that could be verified locally. It closed with 34 passing
tests, a static fixture that produces high/medium/low findings at a 30.2/100
score, a verified kill-switch halt at exit code 3, and a browser run that
correctly reported all seven Operate checks as blocked because Playwright was
not installed. The remaining work is intentionally deferred to `BOARD.md`, with
the first commercial critical path being metering (`QH-019`), workspace auth
(`QH-020`), quota enforcement (`QH-021`), and isolated cloud execution
(`QH-025`), while `QH-010` closes the browser-in-CI gap and `QH-014` calibrates
the score against labeled outcomes.
