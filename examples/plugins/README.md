# Example check plugin

`sample_check.py` is a complete signed-plugin shape, but its `PLUGIN.signature`
is intentionally empty. An unsigned plugin is rejected and appears as a blocked
check; it is never executed.

## Contract

- Entry-point group: `quality_harness.checks`
- Entry point value: `sample_check:PLUGIN`
- `PLUGIN` is a `harness.plugins.CheckPlugin`
- The definition declares `id`, `kind`, `requires`, `base_weight`, and the
  evidence/reporting contract
- The plugin check id must be listed by exactly one persona in its frontmatter;
  persona ownership, not the definition, decides which plan runs the check
- The legacy `persona` field is optional metadata only and is ignored for
  ownership

`pyproject.toml` is the installable shape. Copy this folder out of the source tree
or install it directly with `pip install ./examples/plugins` before signing.

Supported plugin kinds are `static` and `plugin`. Browser plugins are rejected
until the Operate extension API lands. Supported input requirements are
`source`, `url`, `markup`, `manifest`, and `any`.

## Signing

Create a project-local trust store with a synthetic test key:

```bash
mkdir -p .harness/plugins
python - <<'PY'
import json
from pathlib import Path
path = Path(".harness/plugins/trusted_keys.json")
path.write_text(json.dumps({"keys": {"example-key": "replace-with-a-random-secret"}}))
PY
chmod 600 .harness/plugins/trusted_keys.json
```

Sign the entry point with the same key id and secret:

```bash
python - <<'PY'
from harness.plugins import sign_plugin
from sample_check import DEFINITION
print(sign_plugin(DEFINITION, "sample_check:PLUGIN", "example-key", "replace-with-a-random-secret"))
PY
```

Put the returned `hmac-sha256:...` value in `PLUGIN.signature`. The signature
covers the canonical definition and entry-point value, so changing metadata
invalidates it. The trust store stays under `.harness/`, outside source control.

## Ownership

Add the check id to the owning persona's frontmatter before running:

```yaml
checks:
  - example.package_metadata
```

A plugin that is unowned, owned by multiple personas, unsigned, duplicated, or
malformed becomes a `blocked` check with the reason in `actual` and
`skip_reason`. The rest of the run continues.

## Refusals

A plugin that reaches a tool or scope boundary returns a blocker instead of a
finding:

```python
outcome = CheckOutcome(status=CheckStatus.BLOCKED.value)
outcome.add_blocker(
    attempted_action="Submit a synthetic checkout transaction.",
    boundary="The run is read-only and has no disposable tenant.",
    unblock_evidence=["Provide an approved test tenant and transaction budget."],
)
return outcome
```

The orchestrator persists exactly one blocker for the check, drops any findings
from that outcome, redacts blocker text, and forces the check to `blocked` even
if the plugin reported `passed`. The blocker is validated against the owning
persona's `## Blocker schema` and appears in the report and `status` output.
