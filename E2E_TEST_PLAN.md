# End-to-End Test Plan for `quality-harness`

This plan exercises every documented OSS-core and cloud-boundary feature of
`quality-harness`. Run it from a clean checkout of the repository using
Python 3.11:

```bash
/usr/local/bin/python3.11 --version
```

All commands assume the repository root is the current working directory unless a
command explicitly changes `--root`.

## 1. Prerequisites

Install the base package and optional browser support:

```bash
/usr/local/bin/python3.11 -m pip install -e .
/usr/local/bin/python3.11 -m pip install -e ".[browser]"
/usr/local/bin/python3.11 -m playwright install chromium
```

Validate the environment:

```bash
/usr/local/bin/python3.11 -m harness --version
/usr/local/bin/python3.11 -m harness personas --personas-dir personas
```

Expected:

- Version prints without error.
- Persona contracts load as `accessibility`, `functional`, and `security`.

## 2. Create the testbed

Create a clean, isolated testbed root and deterministic fixture target:

```bash
rm -rf .harness-testbed
mkdir -p .harness-testbed
cp -R examples/sample-repo .harness-testbed/sample-repo
```

Optional browser testbed:

```bash
mkdir -p .harness-testbed/demo-web
cp -R examples/demo-web/* .harness-testbed/demo-web/
cd .harness-testbed/demo-web && /usr/local/bin/python3.11 -m http.server 8080
```

Keep the server running in a separate shell for browser tests.

## 3. Core static run

Run the static fixture:

```bash
/usr/local/bin/python3.11 -m harness run \
  --target examples/sample-repo \
  --no-browser \
  --personas-dir personas \
  --root .harness-testbed/static
```

Expected:

- Exit code `0`.
- JSON report on stdout only.
- Progress and warnings on stderr.
- Report status `completed`.
- At least high, medium, and low findings.
- Confidence around `30.2/100`.

Inspect persisted state:

```bash
/usr/local/bin/python3.11 -m harness status --root .harness-testbed/static
/usr/local/bin/python3.11 -m harness report --root .harness-testbed/static
```

## 4. Determinism and reproducibility

Run the same target twice with a seed and frozen clock:

```bash
/usr/local/bin/python3.11 -m harness run \
  --target examples/sample-repo \
  --no-browser \
  --personas-dir personas \
  --seed 1234 \
  --clock-mode frozen \
  --frozen-clock 2026-01-02T03:04:05Z \
  --root .harness-testbed/det-a

/usr/local/bin/python3.11 -m harness run \
  --target examples/sample-repo \
  --no-browser \
  --personas-dir personas \
  --seed 1234 \
  --clock-mode frozen \
  --frozen-clock 2026-01-02T03:04:05Z \
  --root .harness-testbed/det-b
```

Expected:

- Identical report JSON except root-specific paths where unavoidable.
- Identical finding IDs.
- Determinism metadata records `seed: 1234` and `clock_mode: frozen`.

## 5. Resume

Create an interrupted run by using a kill or partial execution, then resume:

```bash
/usr/local/bin/python3.11 -m harness run \
  --target examples/sample-repo \
  --no-browser \
  --personas-dir personas \
  --root .harness-testbed/resume

/usr/local/bin/python3.11 -m harness run \
  --target examples/sample-repo \
  --no-browser \
  --personas-dir personas \
  --root .harness-testbed/resume \
  --resume
```

Expected:

- Resume completes or reports a typed state error if the run already completed.
- No duplicate findings are created.
- Evidence remains byte-identical.

## 6. Kill switch

Automatic kill switch:

```bash
/usr/local/bin/python3.11 -m harness run \
  --target examples/sample-repo \
  --no-browser \
  --personas-dir personas \
  --kill-on high \
  --root .harness-testbed/kill
```

Expected:

- Exit code `3`.
- Report status `halted`.
- Decision `hold`.
- Remaining checks marked `skipped`.

Manual kill from another shell:

```bash
/usr/local/bin/python3.11 -m harness abort --root .harness-testbed/kill --reason "operator stop"
```

## 7. Personas and plugins

Lint and scaffold:

```bash
/usr/local/bin/python3.11 -m harness personas --personas-dir personas
/usr/local/bin/python3.11 -m harness personas lint --personas-dir personas
/usr/local/bin/python3.11 -m harness personas scaffold demo --personas-dir /tmp/demo-personas
```

Expected:

- Built-in personas load and own disjoint checks.
- Lint exits `0` for valid contracts.
- Scaffold creates a valid contract with `plugin.demo.placeholder`.

Plugin behavior is covered by unit tests:

```bash
/usr/local/bin/python3.11 -m unittest tests.test_plugins -v
```

## 8. Evidence verification

Verify a completed run's evidence:

```bash
/usr/local/bin/python3.11 -m harness verify-evidence --root .harness-testbed/static
```

Expected:

- `tampered: false`.
- `signature_valid: true`.
- `confidence_cap` absent or null on success.

Tamper one byte and verify failure:

```bash
python - <<'PY'
from pathlib import Path
root = Path(".harness-testbed/static/.harness/evidence")
manifests = list(root.rglob("manifest.json"))
artifact = manifests[0].parent / "source-inventory.json"
artifact.write_text(artifact.read_text() + " ", encoding="utf-8")
PY

/usr/local/bin/python3.11 -m harness verify-evidence --root .harness-testbed/static
```

Expected:

- Exit code `1`.
- `tampered: true`.
- Affected path and check id are listed.
- `confidence_cap: 0.0`.
- `decision: hold`.

## 9. Report schema

Validate the published schema:

```bash
/usr/local/bin/python3.11 -m unittest tests.test_report_schema -v
```

Expected:

- Completed and halted reports validate.
- Removed or retyped fields fail.
- Additive fields pass.

## 10. Usage metering

Run with metering and inspect events:

```bash
/usr/local/bin/python3.11 -m harness run \
  --target examples/sample-repo \
  --no-browser \
  --personas-dir personas \
  --workspace acme \
  --root .harness-testbed/metering

cat .harness-testbed/metering/.harness/metering/events.jsonl
```

Expected:

- Events use `quality-harness.metering/v1`.
- Every event has an idempotency key.
- Workspace is `acme`.
- `run_started`, `check_executed`, and `evidence_byte_stored` are present.

## 11. Workspace authentication

Create and use a workspace and scoped key:

```bash
/usr/local/bin/python3.11 -m harness auth create-workspace --root .harness-testbed/auth --name Acme --workspace-id ws_acme
/usr/local/bin/python3.11 -m harness auth create-key --root .harness-testbed/auth --workspace ws_acme --scopes run,read
/usr/local/bin/python3.11 -m harness auth list-workspaces --root .harness-testbed/auth
```

Expected:

- Key secret is returned once.
- Persisted state contains only salted hashes.
- Cross-workspace access is rejected without leaking identifiers.

## 12. Quota enforcement

Show and consume quota:

```bash
cat > .harness-testbed/quota-plan.json <<'JSON'
{
  "id": "free",
  "version": "2026-11",
  "limits": {
    "runs": {"soft": 1, "hard": 2},
    "checks": {"soft": 1, "hard": 3}
  }
}
JSON

/usr/local/bin/python3.11 -m harness quota show \
  --root .harness-testbed/quota \
  --workspace ws_acme \
  --plan .harness-testbed/quota-plan.json

/usr/local/bin/python3.11 -m harness quota consume \
  --root .harness-testbed/quota \
  --workspace ws_acme \
  --plan .harness-testbed/quota-plan.json \
  --resource checks \
  --quantity 1
```

Expected:

- Soft warnings are emitted once per period.
- Hard-limit denials return HTTP 429 details.
- OSS/local mode remains unlimited.

## 13. Usage ledger and invoices

Aggregate metering events and export an invoice:

```bash
/usr/local/bin/python3.11 -m harness ledger ingest \
  --root .harness-testbed/metering \
  --workspace acme \
  --prices .harness-testbed/prices.json

/usr/local/bin/python3.11 -m harness ledger close \
  --root .harness-testbed/metering \
  --workspace acme \
  --period 2026-11 \
  --at 2026-12-01T00:00:00Z

/usr/local/bin/python3.11 -m harness ledger invoice \
  --root .harness-testbed/metering \
  --workspace acme \
  --period 2026-11 \
  --format json

/usr/local/bin/python3.11 -m harness ledger invoice \
  --root .harness-testbed/metering \
  --workspace acme \
  --period 2026-11 \
  --format csv \
  --output .harness-testbed/invoice.csv

/usr/local/bin/python3.11 -m harness ledger verify --root .harness-testbed/metering
/usr/local/bin/python3.11 -m harness ledger reconcile \
  --root .harness-testbed/metering \
  --workspace acme \
  --period 2026-11
```

Expected:

- Ledger entries are immutable and hash-chained.
- Late events are rejected after close unless an explicit adjustment is used.
- Invoice totals equal quantities times unit prices.
- CSV includes workspace, period, line items, subtotal, and credits.

## 14. Self-serve signup and entitlements

Create a workspace and verify an entitlement:

```bash
cat > .harness-testbed/free-plan.json <<'JSON'
{
  "id": "free",
  "version": "1",
  "limits": {
    "runs": {"soft": 1, "hard": 2},
    "checks": {"hard": 10}
  }
}
JSON

/usr/local/bin/python3.11 -m harness signup create \
  --root .harness-testbed/signup \
  --request-id signup-1 \
  --name Acme \
  --plan .harness-testbed/free-plan.json \
  --effective-at 2026-11-12T00:00:00Z

/usr/local/bin/python3.11 -m harness signup usage \
  --root .harness-testbed/signup \
  --workspace ws_acme

/usr/local/bin/python3.11 -m harness signup verify \
  --root .harness-testbed/signup \
  --entitlement .harness-testbed/signup/.harness/cloud/signup.json
```

Expected:

- One workspace, one owner key, and one entitlement are created.
- Free allowance is visible from the first usage request.
- Replaying the same request id is idempotent.
- Tampering or expiry fails verification.

## 15. Feature-gated billing

Enable billing only for an allow-listed workspace:

```bash
cat > .harness-testbed/billing-flag.json <<'JSON'
{
  "enabled": true,
  "sandbox": true,
  "workspaces": ["ws_acme"]
}
JSON

mkdir -p .harness-testbed/billing/.harness/billing
cp .harness-testbed/billing-flag.json .harness-testbed/billing/.harness/billing/flag.json
echo '{"billing/api_key": "secret-value"}' > .harness-testbed/secrets.json

/usr/local/bin/python3.11 -m harness billing status \
  --root .harness-testbed/billing \
  --workspace ws_acme

/usr/local/bin/python3.11 -m harness billing disable \
  --root .harness-testbed/billing \
  --reason "operator stop"

/usr/local/bin/python3.11 -m harness billing enable \
  --root .harness-testbed/billing
```

Expected:

- Disabled feature flag creates no provider records.
- Sandbox mode reconciles invoices to the cent.
- Kill switch disables writes without stopping runs.
- Secrets are resolved through a secret manager and never persisted.

## 16. Cloud run queue and tenant isolation

Use the Python API to queue and run a tenant job:

```bash
/usr/local/bin/python3.11 - <<'PY'
from pathlib import Path
from harness.queue import JobQueue

root = Path(".harness-testbed/queue")
queue = JobQueue(root, personas_dir=Path("personas").resolve())
job = queue.submit("ws_acme", str(Path("examples/sample-repo").resolve()), options={"no_browser": True})
print("job_id:", job["job_id"])
print(queue.process_next())
print("metrics:", queue.metrics())
print("owner view:", queue.get_job("ws_acme", job["job_id"])["status"])
print("other tenant view:", queue.get_job("ws_other", job["job_id"]))
PY
```

Expected:

- A job id is returned immediately.
- Polling is owner-scoped.
- Two tenants cannot read each other's job or evidence.
- Queue depth and run duration are reported.
- Cancellation and crash recovery are covered by `tests.test_queue`.

## 17. Tenant retention, export, and deletion

Export and delete a tenant:

```bash
/usr/local/bin/python3.11 - <<'PY'
from pathlib import Path
from harness.retention import RetentionStore

store = RetentionStore(Path(".harness-testbed/retention"))
print(store.policy("ws_acme"))
export = store.export("ws_acme")
print(export)
print(store.verify_export(export["archive"]))
print(store.soft_delete("ws_acme", grace_days=1))
print(store.hard_delete("ws_acme", force=True))
PY
```

Expected:

- Default retention is explicit.
- Export is a signed ZIP with a manifest.
- No cross-tenant paths appear in the archive.
- Soft delete respects the grace period.
- Hard delete removes tenant state, queue records, and cached exports while retaining an audit log.

## 18. Redaction regression corpus

Run the redaction corpus tests:

```bash
/usr/local/bin/python3.11 -m unittest tests.test_redaction_corpus -v
```

Expected:

- At least 20 credential and token shapes are covered.
- Every artifact from a fixture run is scanned.
- A broken redactor fails the test.
- Corpus values never appear in failure messages.

## 19. SSRF-safe target validation

Run SSRF tests:

```bash
/usr/local/bin/python3.11 -m unittest tests.test_ssrf -v
```

Test localhost allowlisting through config:

```bash
mkdir -p .harness-testbed/ssrf/.harness
cat > .harness-testbed/ssrf/.harness/ssrf-allowlist.json <<'JSON'
{
  "allow": ["127.0.0.0/8", "localhost"]
}
JSON

/usr/local/bin/python3.11 - <<'PY'
from pathlib import Path
from harness.ssrf import validate_url

with __import__("unittest.mock", fromlist=["patch"]).patch(
    "harness.ssrf.socket.getaddrinfo",
    return_value=[(None, None, None, None, ("127.0.0.1", 0))],
):
    print(validate_url("http://localhost:8080/", root=Path(".harness-testbed/ssrf")))
PY
```

Expected:

- Private, loopback, link-local, and metadata targets are rejected.
- DNS rebinding is detected on repeated validation.
- Redirect chains are re-validated.
- Errors do not include resolved internal IPs.

## 20. Browser checks

Run the browser smoke test if Chromium is available:

```bash
/usr/local/bin/python3.11 -m unittest tests.test_browser_smoke -v
```

Expected:

- `browser.load` is not blocked.
- Evidence includes screenshots, console, and network artifacts when enabled.

Manual browser run:

```bash
QH_SSRF_ALLOWLIST="127.0.0.0/8,localhost" \
/usr/local/bin/python3.11 -m harness run \
  --target http://127.0.0.1:8080/ \
  --source examples/demo-web \
  --personas-dir personas \
  --root .harness-testbed/browser
```

## 21. Release packaging

Verify packaging and release workflow contracts:

```bash
/usr/local/bin/python3.11 -m unittest tests.test_packaging -v
```

Build artifacts locally:

```bash
/usr/local/bin/python3.11 -m build
```

Expected:

- `quality-harness` console script is declared.
- Core and browser Dockerfiles are separate.
- Release workflow signs and attests artifacts.

## 22. GitHub Action PR workflow

Validate the Action and PR workflow:

```bash
/usr/local/bin/python3.11 -m unittest tests.test_github_action -v
```

Expected:

- Composite Action declares inputs, outputs, and steps.
- PR workflow is fork-safe with read-only permissions.
- Comment script updates an existing marker comment instead of duplicating it.

## 23. Full regression suite

Run the complete suite:

```bash
/usr/local/bin/python3.11 -m unittest discover -s tests -v
```

Expected:

- All tests pass.
- The final test count is recorded in the completion summary.

## 24. Cleanup

Remove generated testbed state:

```bash
rm -rf .harness-testbed .harness .harness-demo .harness-kill .harness-smoke
rm -f smoke_*.py
```

Do not commit generated `.harness*` directories.

## Feature coverage checklist

- [ ] Version and persona loading
- [ ] Static run
- [ ] Determinism and reproducibility
- [ ] Resume
- [ ] Kill switch
- [ ] Persona contracts
- [ ] Plugin registry
- [ ] Evidence hashing and verification
- [ ] Report schema
- [ ] Metering
- [ ] Authentication
- [ ] Quota enforcement
- [ ] Usage ledger and invoices
- [ ] Signup and entitlements
- [ ] Feature-gated billing
- [ ] Cloud run queue
- [ ] Tenant retention, export, and deletion
- [ ] Redaction corpus
- [ ] SSRF validation
- [ ] Browser checks
- [ ] Release packaging
- [ ] GitHub Action
- [ ] Full regression suite
PY