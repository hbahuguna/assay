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
