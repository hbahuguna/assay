"""Typed errors that produce actionable CLI messages."""


class HarnessError(Exception):
    """Base class for expected harness failures."""

    exit_code = 2


class TargetError(HarnessError):
    pass


class PersonaError(HarnessError):
    pass


class StateError(HarnessError):
    pass


class WorkflowError(HarnessError):
    pass


class BrowserUnavailable(HarnessError):
    pass


class CheckError(HarnessError):
    pass


class PluginError(CheckError):
    pass


class PolicyError(HarnessError):
    pass


class MeteringError(HarnessError):
    pass


class QuotaError(HarnessError):
    pass


class LedgerError(HarnessError):
    pass


class LedgerPeriodClosed(LedgerError):
    """A usage event arrived after its billing period was closed."""

    def __init__(self, workspace: str, period: str) -> None:
        self.workspace = str(workspace)
        self.period = str(period)
        super().__init__(
            "Ledger period {} for workspace {} is closed; add an explicit "
            "adjustment instead of a late usage entry.".format(self.period, self.workspace)
        )
