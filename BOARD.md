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

### QH-043 - Add harness support for the /assay-issues test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-issues`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-044 - Add harness support for the /assay-demo test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-demo`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-045 - Add harness support for the /assay-more test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-more`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-046 - Add harness support for the /assay-help test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-help`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-047 - Add harness support for the /assay-accessibility test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-accessibility`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-048 - Add harness support for the /assay-ui test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-ui`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-049 - Add harness support for the /assay-ux test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-ux`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-050 - Add harness support for the /assay-usability test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-usability`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-051 - Add harness support for the /assay-personas test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-personas`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-052 - Add harness support for the /assay-compatibility test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-compatibility`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-053 - Add harness support for the /assay-intl test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-intl`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-054 - Add harness support for the /assay-localization test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-localization`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-055 - Add harness support for the /assay-content test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-content`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-056 - Add harness support for the /assay-geo test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-geo`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-057 - Add harness support for the /assay-security test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-security`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-058 - Add harness support for the /assay-security-review test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-security-review`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-059 - Add harness support for the /assay-privacy test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-privacy`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-060 - Add harness support for the /assay-state test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-state`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-061 - Add harness support for the /assay-reliability test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-reliability`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-062 - Add harness support for the /assay-performance test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-performance`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-063 - Add harness support for the /assay-load test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-load`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-064 - Add harness support for the /assay-stress test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-stress`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-065 - Add harness support for the /assay-networking test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-networking`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-066 - Add harness support for the /assay-api test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-api`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-067 - Add harness support for the /assay-data test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-data`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-068 - Add harness support for the /failure test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/failure`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-069 - Add harness support for the /proof test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/proof`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-070 - Add harness support for the /assay-confidence test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-confidence`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-071 - Add harness support for the /assay-confidence-init test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-confidence-init`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-072 - Add harness support for the /assay-confidence-plan test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-confidence-plan`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-073 - Add harness support for the /assay-review test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-review`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-074 - Add harness support for the /assay-skeptical-review test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-skeptical-review`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-075 - Add harness support for the /assay-release test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-release`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-076 - Add harness support for the /assay-release-review test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-release-review`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-077 - Add harness support for the /assay-incident test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-incident`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-078 - Add harness support for the /assay-eval test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-eval`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-079 - Add harness support for the /assay-eval-design-review test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-eval-design-review`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-080 - Add harness support for the /assay-statistical-review test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-statistical-review`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-081 - Add harness support for the /assay-agentic test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-agentic`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-082 - Add harness support for the /chatbot test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/chatbot`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-083 - Add harness support for the /bias test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/bias`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-084 - Add harness support for the /assay-tests test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-tests`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-085 - Add harness support for the /assay-generate test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-generate`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-086 - Add harness support for the /assay-map test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-map`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-087 - Add harness support for the /assay-full-report test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-full-report`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-088 - Add harness support for the /assay-stats test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-stats`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-089 - Add harness support for the /assay-integrations test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-integrations`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-090 - Add harness support for the /ai-upgrade test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/ai-upgrade`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-091 - Add harness support for the /assay-auto test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-auto`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-092 - Add harness support for the /assay-background test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-background`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-093 - Add harness support for the /assay-forever test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-forever`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-094 - Add harness support for the /assay-settings test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-settings`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-095 - Add harness support for the /assay-human test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-human`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

### QH-096 - Add harness support for the /assay-pro test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Ready
- **Description:** Implement deterministic harness coverage for the `/assay-pro`
  category, including any required check, persona ownership, evidence capture,
  scoring impact, redaction, and report representation. Work this card
  independently and add a focused regression test before moving on.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.

## In Progress

No task is claimed. `QH-001` through `QH-036` are complete.

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

### QH-015 - Add policy files for gates and domain weights

- **Epic:** E4 Release Confidence and Reporting
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-014
- **Status:** Done
- **Completed:** 2026-11-11
- **Description:** Moved gate thresholds, domain weights, caps, and severity
  penalties into a versioned policy file with a documented JSON schema and safe
  defaults. Added `Policy` validation, `--policy` CLI support, and report
  `policy` metadata. Invalid or unsafe policies are rejected before execution.
- **Acceptance Criteria:**
  - A policy file can tighten or relax thresholds without code changes.
  - Invalid or unsafe policies are rejected before execution.
  - The report names the policy id and version used.
  - The default policy reproduces the current `advisory-v1` behavior.
- **Evidence:** `policies/advisory-v1.json` ships the default policy.
  `tests/test_policy.py` verifies default behavior, policy loading, gate
  relaxation, invalid/unsafe rejection before execution, and report policy
  metadata. Full suite: 114 passing tests with Python 3.11. `README.md`
  documents `--policy`.

### QH-016 - Export SARIF and JUnit and sign report hashes

- **Epic:** E4 Release Confidence and Reporting
- **Priority:** P1
- **Points:** 3
- **Depends on:** QH-015
- **Status:** Done
- **Completed:** 2026-11-11
- **Description:** Added SARIF 2.1.0 and JUnit XML exporters plus HMAC-SHA256
  detached report signatures. Findings map to SARIF rules with severity and
  remediation, checks map to JUnit test cases with pass, fail, blocked, and
  skipped states, and signatures verify or fail after any report edit.
- **Acceptance Criteria:**
  - Findings map to SARIF rules with severity and remediation.
  - Checks map to JUnit test cases with pass, fail, blocked, and skipped states.
  - The signature verifies with a published public key and fails after any edit.
  - Exporters never include redacted secret values.
- **Evidence:** `harness/exports.py` implements `build_sarif`, `build_junit`,
  `sign_report`, and `verify_report_signature`. CLI commands `sarif`, `junit`,
  `sign-report`, and `verify-report` are available. `tests/test_exports.py`
  verifies rule/result mapping, JUnit statuses, signature verification and
  tamper detection, and redaction. Full suite: 118 passing tests with Python
  3.11.

### QH-017 - Add run-to-run trend diff

- **Epic:** E4 Release Confidence and Reporting
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-005, QH-015
- **Status:** Done
- **Completed:** 2026-11-11
- **Description:** Compare the current run with the previous run for the same
  target and report new, resolved, and persistent findings by fingerprint,
  plus score and coverage deltas. First runs report `trend.baseline: true`.
- **Acceptance Criteria:**
  - Findings are matched by fingerprint, not title text alone.
  - The report includes `trend.new`, `trend.resolved`, `trend.persistent`, and
    `trend.score_delta`.
  - A first run reports `trend.baseline: true` with no fabricated delta.
  - The CLI exits non-zero on a configurable score regression.
- **Evidence:** `tests/test_trend.py` verifies baseline runs, persistent
  findings across repeated runs, and a non-zero CLI exit on a score
  regression. History entries now store finding fingerprints. Full suite: 121
  passing tests with Python 3.11. `README.md` documents
  `--fail-on-score-regression`.

### QH-018 - Add baseline suppression with owner and expiry

- **Epic:** E4 Release Confidence and Reporting
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-017
- **Status:** Done
- **Completed:** 2026-11-11
- **Description:** Added durable finding suppressions with owner, reason, ticket,
  and future expiry. Suppressed findings remain visible but do not add severity
  penalty. Expired suppressions stop applying and raise a low finding. Every
  add or remove appends an audit event.
- **Acceptance Criteria:**
  - Suppression requires a non-empty owner, reason, and future expiry.
  - Suppressed findings remain visible but do not add severity penalty.
  - Expired suppressions automatically stop applying and raise a low finding.
  - An audit event is appended for every suppression change.
- **Evidence:** `tests/test_suppression.py` verifies validation, visible
  suppressed findings, penalty removal, expired suppression low findings, and
  audit events. Full suite: 125 passing tests with Python 3.11.
  `README.md` documents `suppress`.

### QH-019 - Define the metering event contract

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P0
- **Points:** 5
- **Depends on:** None
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added the frozen `quality-harness.metering/v1` event contract,
  a dependency-free idempotent JSON Lines file sink, local OSS emission for run,
  check, browser-minute, evidence-byte, and export usage, and normative JSON
  Schemas for the envelope and every event type.
- **Acceptance Criteria:**
  - A JSON Schema exists for the event envelope and every event type.
  - Duplicate delivery with the same idempotency key is ignored.
  - A local OSS run emits the same events to a file sink without a cloud account.
  - The schema includes workspace, run, quantity, unit, timestamp, and source.
  - Contract tests freeze the v1 event names.
- **Evidence:** `tests/test_metering.py` freezes the five v1 event names, checks
  every schema file, proves duplicate idempotency keys are ignored across emitter
  instances, verifies a static OSS run writes reconcilable events to
  `.harness/metering/events.jsonl`, and covers export events. Full suite: 136
  passing tests with Python 3.11. `README.md` documents the sink and contract;
  `BOARD.md` D21 records the architecture decision.

### QH-020 - Add workspace, API-key, and scoped-token authentication

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P0
- **Points:** 8
- **Depends on:** QH-019
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added durable workspaces, scoped API keys, and run-ownership
  mapping in `harness/auth.py`, plus an `auth` CLI. Keys are issued once and
  stored only as a per-key salt and SHA-256 hash with a public lookup prefix.
  Authentication failures carry distinct 401/403 codes without workspace data.
- **Acceptance Criteria:**
  - Keys are stored only as hashes with a prefix for lookup and rotation.
  - Missing, expired, revoked, and wrong-scope keys return distinct 401/403
    responses with no workspace data.
  - Cross-workspace access is impossible in tests, including guessed run ids.
  - Key creation returns the secret exactly once and records an audit event.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_auth -v`
- **Evidence:** `tests/test_auth.py` covers one-time secrets, persisted hashes,
  distinct missing/expired/revoked/wrong-scope failures, opaque cross-workspace
  and guessed run ids, atomic rotation, audit events, environment-token CLI
  input, and persistence across store instances. Full suite: 146 passing tests
  with Python 3.11. `README.md` documents the auth CLI and store; `BOARD.md` D22
  records the security decision.

### QH-021 - Enforce quotas with soft and hard limits

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P0
- **Points:** 5
- **Depends on:** QH-019, QH-020
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `harness/quota.py` and a `quota` CLI for monthly
  per-workspace allowances on runs, checks, browser minutes, and evidence bytes.
  Soft-limit warnings are emitted once per resource per billing period; a hard
  limit blocks only the action that exceeds it with a 429 response and exact
  limit/reset time. Completed usage remains durable and reportable.
- **Acceptance Criteria:**
  - Soft-limit warnings are emitted once per billing period per workspace.
  - A hard-limit action returns 402/429 with the exact limit and reset time.
  - Partial work already completed is billed and reported, not discarded.
  - Concurrency tests cannot overspend a limit through parallel requests.
  - OSS/local mode remains unlimited and unchanged.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_quota -v`
- **Evidence:** `tests/test_quota.py` covers plan parsing, once-per-period soft
  warnings and rollover, workspace isolation, 429 payloads with exact
  limit/reset headers, durable partial usage, metering-event mapping,
  AuthContext tenant binding, explicit local enforcement for tests, twenty
  parallel consumers that cannot exceed a four-check hard limit, and CLI
  success/block/invalid-plan paths. Full suite: 167 passing tests with
  Python 3.11. `README.md` documents the plan JSON and headers; `BOARD.md` D23
  records the enforcement decision.

### QH-022 - Build the append-only usage ledger and invoice export

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-021
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `harness/ledger.py` and a `ledger` CLI that append metering
  events into an immutable, hash-chained ledger, close monthly periods, record
  explicit corrections and late-event adjustments, and render reconcilable
  JSON/CSV invoices. Prices are supplied through a versioned price book; the
  OSS core does not embed commercial pricing. Tenant identity binds through
  `AuthContext`, and append-only state is protected by a process plus advisory
  file lock.
- **Acceptance Criteria:**
  - Ledger entries are immutable; corrections reference the original entry.
  - A closed period cannot accept late events without an explicit adjustment.
  - Invoice totals equal the sum of ledger quantities and unit prices.
  - Export includes workspace, period, line items, subtotal, and credits.
  - Reconciliation tests cover duplicate, late, and corrected events.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_ledger -v`
- **Evidence:** `tests/test_ledger.py` covers price-book parsing, immutable
  hash-chained entries and tamper detection, duplicate idempotency and conflict
  detection, closed-period rejection, explicit late adjustments, corrections
  that reduce an invoice, duplicate/late/corrected reconciliation, auth context
  binding, and CLI ingest/close/adjust/invoice/entries/periods/verify/reconcile
  flows. Full suite: 178 passing tests with Python 3.11. `README.md` documents
  the ledger CLI and price book; `BOARD.md` D24 records the storage and
  correction decision.

### QH-023 - Add self-serve signup, free allowance, and plan selection

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-020, QH-021
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `harness/signup.py` (with an `harness/entitlements.py`
  compatibility shim) and a `signup` CLI for self-serve onboarding. Signup
  creates one workspace, one owner key, a quota-backed free allowance, and an
  HMAC-SHA256 signed entitlement. Plan selection records an effective date and
  rejects downgrades below current usage. Replaying a request id is idempotent.
- **Acceptance Criteria:**
  - Signup creates one workspace, one owner, and one default key.
  - The free allowance is visible in the usage endpoint from the first request.
  - Plan selection records an effective date and cannot silently downgrade
    existing usage.
  - The entitlement is signed, verifiable offline, scoped to the workspace and
    period, and rejected after tampering or expiry.
  - Replaying a signup request is idempotent.
  - No endpoint returns another workspace's identifiers.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_signup -v`
- **Evidence:** `tests/test_signup.py` covers workspace/key/entitlement creation,
  free-allowance visibility, idempotent replay, cross-workspace isolation, plan
  selection and effective dates, downgrade rejection, signed entitlement
  tampering/expiry/workspace/period rejection, module aliasing, and CLI
  create/usage/verify/replay flows. Full suite: 188 passing tests with Python 3.11.
  `README.md` and `BOARD.md` D25 document the onboarding contract.

### QH-024 - Integrate metered subscription billing behind a feature flag

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P1
- **Points:** 8
- **Depends on:** QH-022, QH-023
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `harness/billing.py` and a `billing` CLI for feature-gated
  metered billing. Billing is disabled by default and enabled only for
  allow-listed workspaces in sandbox mode. The sandbox provider creates
  customers/subscriptions and reconciles ledger invoices to the cent. Transient
  provider failures retry idempotently and then report degraded status without
  blocking runs or quota. A billing kill switch disables writes independently,
  and secrets are resolved through a secret manager rather than raw values.
- **Acceptance Criteria:**
  - The feature flag off path creates no external customer or subscription.
  - Sandbox mode reconciles a test invoice against the ledger to the cent.
  - Failed billing-provider calls retry idempotently and surface a degraded
    status without blocking runs that already paid quota.
  - Secrets are loaded from the secret manager, never environment dumps or logs.
  - A kill switch disables billing writes without stopping the harness.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_billing -v`
- **Evidence:** `tests/test_billing.py` covers disabled feature-flag behavior,
  workspace-scoped enablement, cent reconciliation, idempotent customer/
  subscription/invoice sync, transient retry and degraded status, kill-switch
  writes, secret-manager use and non-persistence, status reporting, and CLI
  status/sync/disable flows. Full suite: 198 passing tests with Python 3.11.
  `README.md` and `BOARD.md` D26 document the billing contract.

### QH-025 - Add the cloud run queue with per-tenant worker isolation

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P0
- **Points:** 8
- **Depends on:** QH-020, QH-021
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `harness/queue.py` and `JobQueue`, a durable FIFO queue
  with per-tenant roots, owner-only job polling, cancellation through the tenant
  kill file, crash recovery/requeue, and queue metrics. Jobs execute the
  existing orchestrator in isolated tenant roots; metering idempotency keys
  prevent duplicate billing on retry.
- **Acceptance Criteria:**
  - A submitted run returns a job id and can be polled without auth leakage.
  - Two tenants running concurrently cannot read each other's files or evidence.
  - Worker crashes requeue the job without double-billing the same check.
  - Cancellation stops remaining checks and records the partial evidence set.
  - Queue depth and run duration are exported as metrics.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_queue -v`
- **Evidence:** `tests/test_queue.py` covers job submission and owner-only
  polling, processing and metrics, tenant root isolation, queued/running
  cancellation, crash recovery, retry without duplicate metering, and partial
  evidence preservation. Full suite: 207 passing tests with Python 3.11.
  `README.md` and `BOARD.md` D27 document the queue contract.

### QH-026 - Add tenant retention, export, and deletion controls

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-025
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `harness/retention.py` with explicit per-workspace
  retention policy, signed ZIP export with a hashed manifest, soft deletion with
  a grace period, and auditable hard deletion. Hard deletion removes tenant
  roots, queue records, cached exports, billing and quota state, and signup
  records while preserving an audit log.
- **Acceptance Criteria:**
  - Default retention is explicit and documented per plan.
  - Export produces a signed archive with a manifest and no cross-tenant paths.
  - Deletion removes state, evidence, queue records, and cached exports.
  - Deletion is soft for a grace period and auditable after hard deletion.
  - Tests verify deletion of a tenant with active and historical runs.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_retention -v`
- **Evidence:** `tests/test_retention.py` covers default/custom retention,
  signed export and tamper rejection, cross-tenant isolation, soft/hard delete
  grace enforcement, removal of state/queue/exports, and deletion of tenants
  with processed runs. Full suite: 215 passing tests with Python 3.11.
  `README.md` and `BOARD.md` D28 document the retention contract.

### QH-027 - Add billing sandbox and entitlement regression tests

- **Epic:** E5 Self-Serve Usage-Based Cloud Tier
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-024
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `tests/test_billing_regression.py` with table-driven
  entitlement tests for free/pro/enterprise plans, upgrade/downgrade handling,
  cancellation, failed payments, feature-flag behavior, expired/wrong-scope
  entitlement rejection, and unexpected provider-call detection. Added a
  billing-coverage GitHub Actions workflow.
- **Acceptance Criteria:**
  - Every plan has a table-driven entitlement test.
  - Upgrades, downgrades, cancellations, and failed payments are covered.
  - Tests run without network access and fail on an unexpected provider call.
  - CI publishes a coverage report for the billing module.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_billing tests.test_billing_regression -v`
- **Evidence:** `tests/test_billing_regression.py` and
  `.github/workflows/billing-coverage.yml`. Full suite: 224 passing tests with
  Python 3.11. `BOARD.md` D29 records the regression-test decision.

### QH-028 - Publish a signed PyPI package and container image

- **Epic:** E6 Developer Experience and Integrations
- **Priority:** P1
- **Points:** 3
- **Depends on:** QH-001
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `Dockerfile`, `Dockerfile.browser`, and
  `.github/workflows/release.yml`. The release workflow builds an sdist and
  wheel, signs them with Sigstore, attests provenance, verifies signatures,
  publishes to PyPI, builds/signs separate core and browser container images,
  and smoke-tests the installed CLI. `pyproject.toml` already exposes the
  `quality-harness` console script.
- **Acceptance Criteria:**
  - `pip install quality-harness` exposes the `quality-harness` command.
  - Artifacts carry signatures and provenance attestations.
  - A clean container can run the static fixture without extra packages.
  - The browser image is separate and documented.
  - A bad signature fails the release job.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_packaging -v`
- **Evidence:** `tests/test_packaging.py`, `Dockerfile`,
  `Dockerfile.browser`, and `.github/workflows/release.yml`. Full suite:
  229 passing tests with Python 3.11. `README.md` and `BOARD.md` D30 document
  the release contract.

### QH-029 - Add a GitHub Action with PR comment and status check

- **Epic:** E6 Developer Experience and Integrations
- **Priority:** P1
- **Points:** 3
- **Depends on:** QH-016
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `.github/actions/quality-harness/action.yml` and
  `.github/workflows/pr.yml`. The composite Action runs the harness, uploads
  evidence, emits score/decision/coverage outputs, and posts or updates one
  marker-based PR comment. The sample workflow is fork-safe with read-only
  permissions and honors `--fail-on` and `--min-score`.
- **Acceptance Criteria:**
  - The comment includes score, decision, coverage, new findings, and artifact
    links without leaking redacted values.
  - Re-running updates the existing comment instead of creating duplicates.
  - Fork pull requests run without write tokens or secrets.
  - The Action honors `--fail-on` and `--min-score`.
  - A sample workflow is tested against `examples/sample-repo`.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_github_action -v`
- **Evidence:** `tests/test_github_action.py`,
  `.github/actions/quality-harness/action.yml`,
  `.github/actions/quality-harness/comment.py`, and
  `.github/workflows/pr.yml`. Full suite: 232 passing tests with Python 3.11.
  `BOARD.md` D31 records the PR automation decision.

### QH-030 - Publish the report JSON Schema and contract tests

- **Epic:** E6 Developer Experience and Integrations
- **Priority:** P1
- **Points:** 3
- **Depends on:** QH-016
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Published `schemas/report/report.v1.schema.json` and added
  `report_schema()` and `validate_report()` to `harness/report.py`. Contract
  tests validate completed and halted reports, reject removed or retyped fields,
  allow additive fields, and check the schema is staged as a release artifact.
- **Acceptance Criteria:**
  - The schema is generated from or checked against the report builder.
  - Additive optional fields are allowed; removing or retyping a field fails.
  - Both a completed and a halted report validate against the schema.
  - The schema is published as a release artifact with a stable URL.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_report_schema -v`
- **Evidence:** `tests/test_report_schema.py`,
  `schemas/report/report.v1.schema.json`, and release workflow artifact staging.
  Full suite: 238 passing tests with Python 3.11. `BOARD.md` D32 records the
  contract decision.

### QH-031 - Add a redaction regression corpus

- **Epic:** E7 Hardening, Privacy, and Reliability
- **Priority:** P0
- **Points:** 3
- **Depends on:** None
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `tests/fixtures/redaction_corpus.json` with 25 synthetic
  credential shapes and `tests/test_redaction_corpus.py`. The tests run a
  fixture through the orchestrator, scan every artifact for raw corpus values,
  verify a broken redactor leaks a value, and keep corpus values out of failure
  messages.
- **Acceptance Criteria:**
  - The corpus covers at least 20 credential and token shapes.
  - Every artifact produced by a fixture run is scanned for corpus values.
  - A deliberately broken redactor fails the test.
  - The corpus is clearly synthetic and safe to commit.
  - Redaction failures never print the original value in the failure message.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_redaction_corpus -v`
- **Evidence:** `tests/test_redaction_corpus.py` and
  `tests/fixtures/redaction_corpus.json`. Full suite: 241 passing tests with
  Python 3.11. `BOARD.md` D33 records the redaction decision.

### QH-032 - Sign evidence manifests and alert on tamper

- **Epic:** E7 Hardening, Privacy, and Reliability
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-016, QH-031
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Evidence manifests are now HMAC-SHA256 signed during
  checkpoint and finalization. `EvidenceStore.verify()` checks the manifest
  signature, re-hashes every artifact, reports missing or mismatched files with
  their check ids, and emits a machine-readable alert. The `verify-evidence`
  CLI verifies offline and caps confidence at 0 with a `hold` decision when
  tampering is detected.
- **Acceptance Criteria:**
  - Manifest signatures verify with a published key.
  - Tampering with one byte in any artifact fails verification.
  - Verification output lists the affected path and check id.
  - A failed verification caps confidence and forces a `hold` decision.
  - Verification works offline.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_evidence_signing -v`
- **Evidence:** `tests/test_evidence_signing.py`. Full suite: 248 passing tests
  with Python 3.11. `README.md` and `BOARD.md` D34 document the evidence
  signing contract.

### QH-033 - Add SSRF-safe target validation and allowlists

- **Epic:** E7 Hardening, Privacy, and Reliability
- **Priority:** P0
- **Points:** 5
- **Depends on:** None
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `harness/ssrf.py` with DNS resolution, default denial of
  loopback, private, link-local, metadata, multicast, and reserved addresses,
  CIDR/hostname allowlists loaded from config or environment, redirect-chain
  revalidation, and browser navigation checks. Orchestrator URL targets are
  validated before execution; errors avoid exposing resolved IPs.
- **Acceptance Criteria:**
  - Cloud metadata and private-range targets are rejected with a clear error.
  - Redirects are re-validated at every hop.
  - An operator can allowlist localhost for CI through config, not a CLI flag
    that bypasses all checks.
  - DNS rebinding is covered by a test using a changing resolver.
  - The error never includes resolved internal IPs in the public report.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_ssrf -v`
- **Evidence:** `tests/test_ssrf.py`. Full suite: 256 passing tests with Python 3.11.
  `README.md` and `BOARD.md` D35 document the SSRF contract.

### QH-034 - Add failure-injection tests for state, evidence, and kill switch

- **Epic:** E7 Hardening, Privacy, and Reliability
- **Priority:** P1
- **Points:** 3
- **Depends on:** QH-002, QH-032
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `tests/test_failure_injection.py` covering corrupt
  progress state, failed evidence writes, kill during evidence collection,
  normal kill-switch halting, and partial manifest tampering. Each case proves
  the run fails closed or records a hold report.
- **Acceptance Criteria:**
  - Corrupt `progress.json` produces a typed error and preserves the last good
    copy when available.
  - A failed evidence write never produces a passing report.
  - Kill during evidence collection still writes a hold report with the failure.
  - Each injected failure has a deterministic test and a documented recovery.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_failure_injection -v`
- **Evidence:** `tests/test_failure_injection.py`. Full suite: 261 passing tests
  with Python 3.11. `BOARD.md` D36 documents the failure-injection contract.

### QH-035 - Guard resume against a changed plugin registry

- **Epic:** E1 Deterministic Core Engine
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-003
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added a stable registry fingerprint to `CheckRegistry` and
  persisted it in run options. Resume now compares the saved fingerprint with
  the current registry before loading personas or replaying checks, raising a
  typed `StateError` when plugins or trust keys changed. Failed resume attempts
  do not mutate progress, report, or evidence.
- **Acceptance Criteria:**
  - Resume with an added, removed, re-signed, or re-evaluated plugin fails with a
    typed `StateError` before replay.
  - Completed checks and their evidence remain byte-identical after the failed
    resume attempt.
  - The progress and report record the registry fingerprint used by the run.
  - Tests cover a plugin added between runs, a plugin removed between runs, and a
    plugin whose trust key was rotated.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_resume_registry -v`
- **Evidence:** `tests/test_resume_registry.py`. Full suite: 265 passing tests
  with Python 3.11. `BOARD.md` D37 records the resume-guard decision.

### QH-036 - Continue seeded id sequences across resume

- **Epic:** E1 Deterministic Core Engine
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-004
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Persisted the seeded entropy counter and determinism options
  with the run. Resume restores the saved seed, clock mode, frozen clock, and id
  sequence position, so a resumed deterministic run continues generating ids
  instead of restarting the sequence. Failed or changed options cannot mix seeds
  or clocks.
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
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_seeded_resume -v`
- **Evidence:** `tests/test_seeded_resume.py`. Full suite: 269 passing tests
  with Python 3.11. `BOARD.md` D38 records the determinism-resume decision.

### QH-037 - Add harness support for the /assay test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added an OpenCode `/assay` slash command and native
  plugin command that run the existing risk-based workflow. This exposes the
  deterministic checks, findings, evidence, and confidence report without
  adding a separate execution path or changing scoring.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_github_action -v`
- **Evidence:** `.opencode/commands/assay.md` and
  `.opencode/plugins/assay/index.ts`. Full suite: 270 passing tests with
  Python 3.11. `BOARD.md` D39 records the category-command decision.

### QH-038 - Add harness support for the /jay (alias for /jay) test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** The `/jay` manager entry point is represented by the OpenCode
  `/assay help` command, which prints the harness CLI contract. The existing
  `/assay` command remains the primary risk-based run entry point, so this
  category does not require a separate execution path or scoring change.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.
- **Acceptance test:** `/usr/local/bin/python3.11 -m harness --help`
- **Evidence:** `.opencode/plugins/assay/index.ts` routes `help` to the CLI
  help output. Full suite: 270 passing tests with Python 3.11. `BOARD.md`
  D40 records the manager-command decision.

### QH-039 - Add harness support for the /full test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `static.full_assessment`, owned by the functional
  persona, to represent the `/full` category when an intent explicitly requests
  a full assessment. The check records hashed scope evidence linked to the check
  and runs only for full-category intents, so unrelated runs and weights are
  unchanged.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_full_category -v`
- **Evidence:** `tests/test_full_category.py` verifies full-intent planning,
  execution, report inclusion, hashed manifest linkage, and absence for
  non-full intents. Full suite: 271 passing tests with Python 3.11.

### QH-040 - Add harness support for the /assay-test test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `static.test_category`, owned by the functional persona,
  to represent the `/assay-test` category when an intent explicitly requests a
  test assessment. The check records hashed scope evidence linked to the check
  and runs only for test-category intents, so unrelated runs and weights are
  unchanged.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_test_category -v`
- **Evidence:** `tests/test_test_category.py` verifies test-intent planning,
  execution, report inclusion, hashed manifest linkage, and absence for
  non-test intents. Full suite: 273 passing tests with Python 3.11.

### QH-041 - Add harness support for the /assay-functionality test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `static.functionality_category`, owned by the functional
  persona, to represent the `/assay-functionality` category when an intent
  explicitly requests a functionality assessment. The check records hashed scope
  evidence linked to the check and runs only for functionality-category intents,
  so unrelated runs and weights are unchanged.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_functionality_category -v`
- **Evidence:** `tests/test_functionality_category.py` verifies
  functionality-intent planning, execution, report inclusion, hashed manifest
  linkage, and absence for non-functionality intents. Full suite: 275 passing
  tests with Python 3.11.

### QH-042 - Add harness support for the /assay-browser test category

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P2
- **Points:** 3
- **Depends on:** QH-036
- **Status:** Done
- **Completed:** 2026-11-12
- **Description:** Added `static.browser_category`, owned by the functional
  persona, to represent the `/assay-browser` category when an intent explicitly
  requests a browser assessment. The check records hashed scope evidence linked
  to the check and runs only for browser-category intents, so unrelated runs and
  weights are unchanged.
- **Acceptance Criteria:**
  - The category is represented by at least one owned check or an explicit
    documented no-op with rationale.
  - A deterministic fixture or unit test exercises the category.
  - Evidence is hashed, redacted, and linked to findings.
  - The report and confidence calculation account for the category without
    changing unrelated weights.
  - `python -m unittest discover -s tests` passes.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_browser_category -v`
- **Evidence:** `tests/test_browser_category.py` verifies browser-intent
  planning, execution, report inclusion, hashed manifest linkage, and absence
  for non-browser intents. Full suite: 277 passing tests with Python 3.11.

### QH-097 - Make /assay-browser run live browser checks instead of a static scan

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P0
- **Points:** 3
- **Depends on:** QH-042
- **Status:** Done
- **Completed:** 2026-11-13
- **Description:** Worked out of order at operator request because the shipped
  `/assay-browser` command passed `--no-browser` and a directory target, so it
  could only record `static.browser_category` and never exercised the rendered
  page. The OpenCode plugin and wrapper now require a URL, omit `--no-browser`,
  and pass the URL as the target. When live browser checks are planned, the
  redundant `static.browser_category` scope marker is skipped so the category is
  represented by real browser evidence. Missing URLs produce actionable guidance
  instead of a browser-labelled static pass. Playwright/Chromium absence remains
  a `blocked` coverage gap.
- **Acceptance Criteria:**
  - `/assay-browser <url>` plans and executes the `browser.*` checks against the
    URL and does not pass `--no-browser`.
  - `/assay-browser` without a URL refuses to run and explains the required
    argument; it never silently falls back to a static scan.
  - When live browser checks are planned, the redundant
    `static.browser_category` marker is not planned.
  - The generic `/assay` command treats an explicit URL as a browser target
    instead of ignoring it.
  - Missing Playwright/Chromium still yields `blocked` browser checks, never a
    pass.
  - `python -m unittest discover -s tests` passes.
- **Acceptance test:** `/usr/local/bin/python3.11 -m unittest tests.test_assay_command_contract -v`
- **Evidence:** `tests/test_assay_command_contract.py` verifies the pure Node
  command builder, URL-required behavior, planning of real browser checks for a
  URL target, and a live Chromium run that produces non-blocked browser checks.
  `.opencode/plugins/assay/command-args.ts` isolates the argument contract for
  `node --test`; `tests/plugin/command-args.test.mjs` covers URL extraction,
  flag pass-through, and the absence of `--no-browser`.

### QH-098 - Run a comprehensive same-origin browser crawl for /assay-browser

- **Epic:** E8 Carbon Test Category Coverage
- **Priority:** P1
- **Points:** 5
- **Depends on:** QH-097
- **Status:** Done
- **Completed:** 2026-11-13
- **Description:** Worked out of order at operator request because the live
  `/assay-browser` command still evaluated only the landing page at one desktop
  viewport, so a healthy-looking report could miss broken routes and mobile
  layout defects. `harness run` now supports `--crawl`, `--max-pages`, and
  `--comprehensive`; the browser layer follows same-origin links rendered on the
  seed page, captures per-route evidence, and evaluates every browser check on
  each route. The OpenCode `/assay-browser` command defaults to
  `--crawl --max-pages 8 --mobile --headed` and falls back to headless when a
  visible window cannot open. External, destructive, download, API, and
  non-HTML links are excluded from the crawl.
- **Acceptance Criteria:**
  - `/assay-browser <url>` defaults to a comprehensive visible run and explicit
    `--no-crawl`, `--no-mobile`, `--headless`, or `--max-pages N` flags override
    the defaults without duplicating `--target` or adding `--no-browser`.
  - `harness run --crawl` discovers same-origin routes, skips external,
    destructive, download, API, and non-HTML links, and runs each browser check
    against every captured route.
  - Every route records hashed screenshot, DOM snapshot, response headers,
    console, network, cookies, and navigation evidence; duplicate findings are
    grouped and capped per check.
  - Check metadata records `crawled`, `page_count`, `routes`, and
    `routes_not_evaluated`; a route that cannot navigate is reported by
    `browser.load` and is not silently counted as a pass by other checks.
  - `--max-pages 1` preserves the previous single-page behavior, and a headed
    launch failure degrades to headless with a recorded limitation.
  - `python -m unittest discover -s tests` passes.
- **Acceptance test:**
  `PYTHONPATH=/Users/skaparwan/github/assay /usr/local/bin/python3.11 -m unittest tests.test_browser_crawl tests.test_assay_command_contract -v`
- **Evidence:** `tests/test_browser_crawl.py` proves route discovery, skipped
  link classes, per-route evidence, merged metadata, 404 detection, mobile crawl
  coverage, and the `--max-pages 1` fallback. `tests/plugin/command-args.test.mjs`
  proves the comprehensive defaults and explicit overrides. `README.md` documents
  the crawl and default command behavior.

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
| D21 | 2026-11-12 | Usage metering is a versioned JSON Lines contract (`quality-harness.metering/v1`) with a frozen event name set and an idempotency key on every event. OSS runs write the same events to `.harness/metering/events.jsonl`; workspace defaults to `local` and source to `oss`. | A local file sink proves the billing contract without a cloud account, while idempotency lets a future delivery layer retry safely. Keeping metering separate from operational `events.jsonl` prevents audit logs from becoming billing records. | Add a cloud sink adapter in `QH-024`; any new billable action needs a new schema version or a frozen v1 addendum, never a silent rename. |
| D22 | 2026-11-12 | Cloud identity resolves through `harness.auth`: every request authenticates to exactly one workspace, API keys are stored as salted SHA-256 hashes with a public prefix, and run ids are claimed by one workspace. Wrong-scope requests return 403; missing, expired, revoked, and guessed/foreign run ids return 401/404 without workspace data. | Returning the same 404 for a guessed run id and another tenant's real id prevents tenant enumeration. A one-time secret plus a hashed store keeps plaintext keys out of state, logs, and the audit trail. Resolving workspace before quota in `QH-021` makes limits impossible to apply to the wrong tenant. | Add a KMS-backed keyed-hash pepper, rotation grace windows, and per-key rate limiting when the cloud execution layer lands in `QH-025`. |
| D23 | 2026-11-12 | Quota enforcement is a durable per-action usage store (`quality-harness.quota/v1`) loaded from a monthly plan. A hard limit returns 429 before the requested quantity is recorded; earlier usage remains. Soft warnings are appended once per resource per period, and every decision carries remaining-quota headers. The `local` workspace and the no-plan OSS path are unlimited and write nothing. Cloud callers bind the store to `AuthContext.workspace_id`, never a request-body workspace. | Per-action enforcement preserves partial value and billing accuracy, while one atomic read-modify-write under a process and file lock prevents parallel workers from overspending. Keeping warnings in a separate quota event log avoids changing the frozen metering v1 contract because a warning is not a billable action. | Wire `QuotaStore` into the isolated cloud queue in `QH-025`; add plan/tier provisioning in `QH-023` and period-close corrections in `QH-022`. |
| D24 | 2026-11-12 | Usage billing is an append-only hash-chained ledger (`quality-harness.ledger/v1`) under `.harness/ledger/entries.jsonl`, with immutable monthly close records in `periods.json`. Corrections are signed entries that reference the original entry or close id; a closed period rejects ordinary usage but accepts an explicit adjustment. Invoices derive from stored entry quantities and unit prices, and reconciliation checks the sum of quantity times unit price against the invoice total. Prices are supplied through a versioned price book; the OSS core does not embed commercial pricing. | Append-only records and hash continuity make tampering and accidental edits detectable, while explicit references preserve an auditable correction chain. Keeping close state separate from usage rows lets late events be rejected without losing earlier entries, and storing unit prices on entries keeps historical invoices reproducible even if a price book changes. | Wire ledger ingestion into `QH-024` cloud delivery, add billing-provider invoice IDs and tax handling there, and consider per-period entry compaction if run volume makes JSON Lines scanning too slow. |
| D25 | 2026-11-12 | Self-serve signup is an idempotent onboarding store (`quality-harness.signup/v1`) that creates one workspace, one owner key, and a quota-backed free allowance, then issues an HMAC-SHA256 signed entitlement (`quality-harness.entitlement/v1`) encoding tier, limits, workspace, and effective period. Plan changes record an effective date and are rejected when they would downgrade below current usage. Entitlement verification is local and rejects tampering, workspace/period mismatch, and expiry. | Idempotency keys make retries safe without duplicate tenants or keys. Keeping the free allowance in the existing quota store makes usage visible immediately and reuses atomic monthly enforcement. HMAC signing uses only the standard library, stays offline-verifiable, and keeps commercial plan policy in caller-supplied plan JSON rather than the OSS core. | Add public-key signing and key rotation when an optional cryptography extra is approved; add email/identity proofing and billing-provider customer creation in `QH-024`. |
| D26 | 2026-11-12 | Metered billing is an explicit, off-by-default `BillingFeatureFlag` (`enabled`, `sandbox`, allow-listed workspaces) plus a separate billing kill switch. A sandbox provider implements the provider boundary and reconciles ledger invoices to the cent. Billing calls use idempotency keys, retry transient failures, and report `degraded` without changing run or quota state. Secrets are resolved through `SecretManager` implementations (environment, in-memory, or file) and never persisted in billing state. | Keeping billing behind both a feature flag and a kill switch lets the OSS core and runs remain available during provider outages. Reusing ledger invoices as the billing source of truth preserves auditability, while idempotency keys make retries safe. Secret references keep credentials out of state and logs. | Replace the sandbox provider with a real provider adapter in a later task; add webhook handling, tax, and provider invoice IDs when sandbox credentials are available. |
| D27 | 2026-11-12 | Cloud runs are queued in `.harness/cloud/queue.json` and execute in per-tenant roots under `.harness/tenants/<workspace>/`. Job records expose only owner-scoped fields; cross-tenant lookups return no data. Cancellation writes the tenant `.harness/KILL` file so the existing orchestrator kill switch stops remaining checks and preserves partial evidence. Crashed running jobs are recovered to queued and retried; retry uses persisted state and metering idempotency keys to avoid double billing. Queue metrics expose depth, status counts, and run durations. | Reusing the deterministic orchestrator keeps phase order, evidence, scoring, and kill behavior unchanged. Separate tenant roots provide filesystem isolation, while owner checks in the queue API prevent job metadata leakage. Idempotent metering keys make retries safe. | Add true process-level workers, sandboxed filesystem namespaces, and cancellation tokens if stronger isolation is required; add queue-depth alerting and retention in `QH-026`. |
| D28 | 2026-11-12 | Tenant retention is an explicit per-workspace policy (default 30 days) with soft deletion for a grace period and auditable hard deletion. Exports are ZIP archives containing tenant state/evidence and filtered central records, plus a signed manifest of SHA-256 hashes. Hard deletion removes tenant roots, queue jobs, cached exports, billing/quota state, and signup records, while appending an audit record. | Explicit retention and grace periods prevent accidental data loss. Signed manifests make export tampering detectable and keep exports scoped to one tenant. Leaving an audit trail after hard deletion preserves accountability without retaining tenant data. | Add scheduled retention enforcement, encrypted archives, and GDPR-style deletion certificates when the cloud control plane lands. |
| D29 | 2026-11-12 | Billing regression coverage is table-driven over free/pro/enterprise plans and exercises upgrades, downgrades, cancellation, failed payments, feature-flag denial, and unexpected provider calls using the local sandbox fake. A dedicated GitHub Actions workflow runs only the billing tests under `coverage` and uploads the report. | Table-driven tests make plan behavior auditable and prevent silent drift when limits change. The local fake keeps tests deterministic and network-free, while the coverage workflow makes billing regressions visible in CI. | Expand the table when new tiers are added; add provider-specific contract tests when sandbox credentials become available. |
| D30 | 2026-11-12 | Releases use a GitHub Actions workflow that builds an sdist/wheel, signs artifacts with Sigstore, attests provenance, verifies signatures before publishing, publishes to PyPI with trusted publishing, and builds/signs separate core and browser container images. The core image contains no Playwright; the browser image is based on the Playwright Python image. A bad signature fails the verification job before publication. | Sigstore keyless signing and GitHub artifact attestations provide supply-chain evidence without managing long-lived signing keys. Separate images keep the deterministic core small while making browser support explicit. | Add SBOM generation and container vulnerability scanning before the first public release. |
| D31 | 2026-11-12 | PR automation is a composite GitHub Action plus a fork-safe workflow. The Action runs the harness, uploads evidence, exposes score/decision/coverage outputs, and posts or updates one marker-based comment. Comment writes are skipped when the GitHub context is unavailable, so fork pull requests can run with read-only permissions. The workflow passes `--fail-on` and `--min-score` directly to the harness. | Reusing the machine-first CLI keeps the PR summary derived from the same JSON report as CI. A single marker comment avoids duplicate noise, and read-only permissions prevent secret exposure on forked pull requests. | Add check-run annotations and changed-file scoping in a later integration task. |
| D32 | 2026-11-12 | The report contract is published as `schemas/report/report.v1.schema.json` and checked by `harness.report.report_schema()` and `validate_report()`. The schema requires all current top-level fields, types additive optional fields, and is staged as a release artifact. Completed and halted reports both validate. | A published JSON Schema lets downstream consumers validate reports without importing the harness. Checking it in tests prevents silent contract drift. Allowing additional properties preserves backward-compatible evolution. | Generate the schema directly from the report builder if the field set grows beyond manual maintenance. |
| D33 | 2026-11-12 | Redaction is proven by a 25-shape synthetic corpus stored in `tests/fixtures/redaction_corpus.json`. A fixture run is executed and every artifact under `.harness/` is scanned for each raw value. The test also monkeypatches the redactor to an identity function and requires a leak, while failure messages never include the original value. | A committed synthetic corpus makes redaction regressions reproducible and reviewable. Scanning all artifacts catches leaks in state, evidence, reports, logs, and metering. The broken-redactor assertion ensures the test would fail if redaction stopped working. | Extend the corpus as new redaction patterns are added; add browser-capture redaction cases when Playwright is available in CI. |
| D34 | 2026-11-12 | Evidence manifests are HMAC-SHA256 signed with a local evidence signing key. `EvidenceStore.verify()` checks the manifest signature, re-hashes every artifact, reports missing or substituted files with their path and check id, and returns a machine-readable alert. The `verify-evidence` CLI works offline and caps confidence at 0 with a `hold` decision when tampering is detected. | A signed manifest detects both artifact edits and manifest edits without a network dependency. Including check ids makes failures actionable for operators. Treating any verification failure as a hard hold prevents tampered evidence from supporting a release. | Move to public-key signatures when an optional cryptography extra is approved; add key rotation and per-run key identifiers. |
| D35 | 2026-11-12 | URL targets are validated before navigation by resolving DNS and rejecting loopback, private, link-local, metadata, multicast, and reserved addresses unless an explicit CIDR or hostname allowlist permits them. Allowlists load from `.harness/ssrf-allowlist.json`, a config path, or `QH_SSRF_ALLOWLIST`; they are not a blanket bypass flag. Redirect chains and browser navigation are re-validated, and DNS rebinding is covered by tests. Target errors do not include resolved internal IPs. | SSRF protection must fail closed by default while still allowing local CI servers through documented configuration. Re-validating redirects closes a common bypass. Keeping internal addresses out of errors prevents information disclosure in reports and logs. | Add DNS pinning/proxy controls and IPv6 coverage expansion when browser execution moves to isolated workers. |
| D36 | 2026-11-12 | Failure injection is covered by deterministic tests: corrupt `progress.json` raises a typed `StateError` without overwriting the last good file; a failed evidence write yields zero-confidence, non-shipping report; kill during evidence collection records a halted hold report; and a truncated manifest fails evidence verification. Recovery is documented as restoring the last good progress file or rerunning after fixing the underlying failure. | Failing closed prevents a partial or corrupted run from claiming confidence. Keeping the corrupt file in place preserves forensic evidence, while hold reports make kill behavior auditable. | Add disk-full and permission-denied injection once a cross-platform fault-injection helper is available. |
| D37 | 2026-11-12 | Resume persists a SHA-256 registry fingerprint containing check ids, sources, blocked reasons, evaluator availability, and trust-key ids. A mismatch raises `StateError` before personas are loaded or any check is replayed, and failed resume attempts leave progress, report, and evidence byte-identical. | A changed evaluator or untrusted plugin must not be silently used for pending work. Comparing before persona validation prevents partial planning against a different registry. Keeping the failed attempt read-only preserves forensic evidence. | Add cryptographic signing of the registry fingerprint if plugin trust moves beyond HMAC. |
| D38 | 2026-11-12 | Seeded runs persist `seed`, `clock_mode`, `frozen_clock`, and an `id_sequence` counter in run options. `make_determinism` accepts an `entropy_counter`, and the orchestrator saves the current counter with every state write. Resume restores those options before creating the determinism provider, so a resumed run continues the SHA-256 id sequence instead of restarting it. | Continuing the counter prevents finding-id collisions and makes resumed deterministic runs comparable to an uninterrupted run. Restoring clock and seed options prevents mixing determinism modes. Persisting the counter at each state save keeps partial runs recoverable. | Replace the context-scoped provider with explicit provider arguments if concurrent in-process runs need independent counters. |
| D39 | 2026-11-12 | The assay category is represented by an OpenCode `/assay` slash command and native plugin command that run the existing risk-based workflow. This keeps the category user-facing without adding a separate execution path or changing scoring. | Reusing the deterministic orchestrator preserves phase order, evidence, redaction, and reporting. The command makes the assay entry point available in OpenCode. | Add category-specific check packs only when a concrete risk or fixture justifies them. |
| D40 | 2026-11-12 | The `/jay` manager category is represented by `/assay help`, which prints the harness CLI contract. The existing `/assay` command remains the primary risk-based entry point, so no separate scoring or execution path is needed. | A manager entry point should expose available commands and options without duplicating run logic. Keeping it as help preserves the deterministic CLI contract and avoids a parallel command implementation. | Add richer interactive guidance only if the harness gains a conversational layer beyond the CLI. |
| D41 | 2026-11-13 | `/assay-browser` is a live-browser command, not a static category alias. It requires an explicit http(s) URL, passes the URL as `--target`, and never injects `--no-browser`. The OpenCode plugin adds a loopback-only SSRF allowlist (`127.0.0.0/8`, `::1`, `localhost`) for that invocation unless the operator already set one; private and metadata networks remain blocked. A missing URL returns actionable guidance instead of running. | The previous wrapper combined a browser intent with `--no-browser` and a directory target, which could only produce `static.browser_category`. Requiring a URL keeps the command honest, while the loopback allowlist makes local dev servers usable without weakening the default SSRF posture for arbitrary hosts. | Revisit the loopback default if browser runs move to isolated cloud workers where localhost has different meaning. |
| D42 | 2026-11-13 | A comprehensive browser run is a bounded, same-origin crawl inside one deterministic check pack. The seed page is always captured; up to `--max-pages` routes are discovered from rendered links, with external hosts, non-HTTP schemes, forms, downloads, API paths, destructive actions, and known non-HTML suffixes excluded. Each route is captured separately, then each check merges route outcomes; duplicate findings are grouped and capped at ten, and a route that cannot navigate is reported by `browser.load` while other checks mark it `routes_not_evaluated`. `/assay-browser` defaults to crawl plus mobile plus a visible browser, falling back to headless if a window cannot open. | The one-page desktop run was technically real browser testing but could not find broken routes or mobile defects, which made a clean report look more complete than it was. Keeping the crawl same-origin and link-only avoids submitting data or wandering off-site, while per-route evidence preserves the no-evidence-no-finding invariant. Merging into the existing check ids avoids changing scoring weights or the report contract. | Add project-source route hints and authenticated multi-route journeys when a concrete framework or customer fixture justifies them; keep the crawl bound and SSRF-validated. |
