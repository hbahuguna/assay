"""Compatibility shim for entitlement-focused imports.

The implementation lives in :mod:`harness.signup`.  Both names are supported so
cloud adapters and tests can import either the onboarding or entitlement view.
"""

from .signup import *  # noqa: F401,F403
