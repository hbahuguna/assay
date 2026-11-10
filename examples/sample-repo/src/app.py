"""Small fixture application surface.

This module is intentionally ordinary. The fixture's problems live in
config.py and page.html so a run has deterministic, non-critical findings.
"""

from typing import Dict


def health() -> Dict[str, str]:
    return {"status": "ok", "service": "sample-service"}


def greeting(name: str) -> str:
    cleaned = name.strip() or "customer"
    return "Hello, {}!".format(cleaned[:40])
