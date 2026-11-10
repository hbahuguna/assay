"""Injectable clock and entropy providers for reproducible runs.

Production runs use ``RealClock`` and ``RealEntropy``.  A run may opt into a
seeded entropy source and/or a frozen clock through ``make_determinism``.  The
active provider is kept in a :class:`contextvars.ContextVar` so the existing
``models.utc_now`` and ``models.new_id`` helpers remain the single call sites
used across the harness without passing a provider through every function.
"""

from __future__ import annotations

import contextvars
import dataclasses
import datetime
import hashlib
import uuid
from typing import Optional, Protocol

from .errors import HarnessError


CLOCK_MODES = {"real", "frozen"}


def parse_timestamp(value: object) -> datetime.datetime:
    """Parse an ISO-8601 timestamp or datetime into an aware UTC datetime."""

    if isinstance(value, datetime.datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("expected an ISO-8601 timestamp: {}".format(exc))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def format_timestamp(value: object) -> str:
    """Format a timestamp as second-precision ISO-8601 UTC with a Z suffix."""

    return (
        parse_timestamp(value)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class Clock(Protocol):
    def now(self) -> datetime.datetime:
        ...


class Entropy(Protocol):
    def hex_chars(self, length: int) -> str:
        ...


class RealClock:
    def now(self) -> datetime.datetime:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


class FrozenClock:
    def __init__(self, value: object) -> None:
        self._value = parse_timestamp(value)

    def now(self) -> datetime.datetime:
        return self._value


class RealEntropy:
    def hex_chars(self, length: int) -> str:
        if length <= 0:
            return ""
        out = uuid.uuid4().hex
        while len(out) < length:
            out += uuid.uuid4().hex
        return out[:length]


class SeededEntropy:
    """Deterministic SHA-256 based entropy, independent of Python's PRNG."""

    def __init__(self, seed: int) -> None:
        self._seed = str(seed)
        self._counter = 0

    def hex_chars(self, length: int) -> str:
        if length <= 0:
            return ""
        chunks = []
        produced = 0
        while produced < length:
            digest = hashlib.sha256(
                "{}:{}".format(self._seed, self._counter).encode("utf-8")
            ).hexdigest()
            chunks.append(digest)
            produced += len(digest)
            self._counter += 1
        return "".join(chunks)[:length]


@dataclasses.dataclass(frozen=True)
class Determinism:
    clock: Clock
    entropy: Entropy
    seed: Optional[int]
    clock_mode: str

    def now(self) -> str:
        return format_timestamp(self.clock.now())

    def new_id(self, prefix: str) -> str:
        return "{}-{}".format(prefix, self.entropy.hex_chars(10))

    def run_id(self) -> str:
        stamp = self.now().replace("-", "").replace(":", "").replace("Z", "Z")
        return "{}-{}".format(stamp, self.entropy.hex_chars(6))


PRODUCTION = Determinism(
    clock=RealClock(),
    entropy=RealEntropy(),
    seed=None,
    clock_mode="real",
)


def make_determinism(
    seed: Optional[int] = None,
    clock_mode: str = "real",
    frozen_clock: object = None,
) -> Determinism:
    if clock_mode not in CLOCK_MODES:
        raise HarnessError("clock_mode must be one of: real, frozen")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise HarnessError("seed must be an integer or null")
    if clock_mode == "frozen":
        if frozen_clock is None:
            raise HarnessError("frozen_clock is required when clock_mode is 'frozen'")
        try:
            clock = FrozenClock(frozen_clock)
        except ValueError as exc:
            raise HarnessError("frozen_clock must be an ISO-8601 timestamp: {}".format(exc))
    else:
        if frozen_clock is not None:
            raise HarnessError("frozen_clock requires clock_mode 'frozen'")
        clock = RealClock()
    entropy = SeededEntropy(seed) if seed is not None else RealEntropy()
    return Determinism(clock=clock, entropy=entropy, seed=seed, clock_mode=clock_mode)


_ACTIVE_DETERMINISM: contextvars.ContextVar[Optional[Determinism]] = contextvars.ContextVar(
    "quality_harness_determinism", default=None
)


def set_determinism(determinism: Determinism) -> contextvars.Token:
    return _ACTIVE_DETERMINISM.set(determinism)


def reset_determinism(token: contextvars.Token) -> None:
    _ACTIVE_DETERMINISM.reset(token)


def current_determinism() -> Determinism:
    return _ACTIVE_DETERMINISM.get() or PRODUCTION


def now() -> str:
    return current_determinism().now()


def new_id(prefix: str) -> str:
    return current_determinism().new_id(prefix)


def run_id() -> str:
    return current_determinism().run_id()
