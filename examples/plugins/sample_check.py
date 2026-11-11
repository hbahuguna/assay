"""Example signed check plugin.

Install this module as a package and expose ``PLUGIN`` from the
``quality_harness.checks`` entry-point group.  The signature is left empty in
the repository; sign it with a key from ``.harness/plugins/trusted_keys.json``
before installing it.  See ``examples/plugins/README.md``.
"""

from typing import Any, Dict

from harness.checks import CheckDefinition
from harness.models import CheckStatus
from harness.outcomes import CheckOutcome
from harness.plugins import CheckPlugin, PluginContext

DEFINITION = CheckDefinition(
    id="example.package_metadata",
    title="The project declares package metadata",
    domain="data_integrity",
    kind="plugin",
    intent="Confirm the release has a machine-readable package identity.",
    expected="At least one package manifest is present in the source inventory.",
    base_weight=0.6,
    requires="manifest",
)


def evaluate(context: PluginContext) -> CheckOutcome:
    manifests = list(context.inventory.manifests)
    evidence = context.save_json(
        "example-package-metadata",
        {"manifests": manifests},
        "Package manifests observed by the example plugin.",
    )
    payload: Dict[str, Any] = {"manifests": manifests}
    return CheckOutcome(
        status=CheckStatus.PASSED.value,
        actual="{} package manifest(s) declared.".format(len(manifests)),
        evidence=[evidence],
        metadata=payload,
    )


PLUGIN = CheckPlugin(
    definition=DEFINITION,
    evaluate=evaluate,
    signature="",
)
