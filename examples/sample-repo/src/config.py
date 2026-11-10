"""Fixture configuration with one deliberate credential-like value.

The value is fake, but it has the shape and entropy of a real API key so the
secret scanner can demonstrate a high-severity finding without exposing
anything live.
"""

API_KEY = "a8f3k2m9q1z7"
DEFAULT_TIMEOUT_SECONDS = 10
