"""GridView: optional second (physical) grid sensor and dynamic peak limit.

Pure logic, no Home Assistant imports. ``states`` is any object with
``.get(entity_id)`` returning a State-like object (``.state``,
``.attributes``, ``.last_reported`` / ``.last_updated``). ``now`` is a
timezone-aware datetime supplied by the caller.

Sign convention: positive = grid import, negative = export.

Design decision: ``read_physical`` returns the raw (unsmoothed) value. EMA
smoothing was left out because it would make exact-value expectations and
``physical_is_new`` (value changed vs. previous read) ambiguous.
"""
from __future__ import annotations

from datetime import datetime, timedelta

PHYSICAL_MAX_AGE_S = 60
PEAK_LIMIT_MIN_W = 500.0
PEAK_LIMIT_MAX_W = 20000.0
PEAK_LIMIT_HOLD = timedelta(minutes=15)


def _read_watts(state) -> float | None:
    """Numeric state in W (kW converted), or None if unusable."""
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    try:
        value = float(state.state)
    except (ValueError, TypeError):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    if state.attributes.get("unit_of_measurement", "W") == "kW":
        value *= 1000.0
    return value


class GridView:
    def __init__(self) -> None:
        self._physical_entity: str | None = None
        self._inverted = False
        self._limit_entity: str | None = None
        self._margin_pct = 0.0
        self._last_w: float | None = None
        self._prev_w: float | None = None
        self._unusable_since: datetime | None = None
        self._source = "none"
        self._held_w: float | None = None
        self._held_at: datetime | None = None
        self._limit_w: float | None = None
        self._limit_source = "static"

    def configure(self, *, physical_entity, physical_inverted,
                  limit_entity, limit_margin_pct) -> None:
        self._physical_entity = physical_entity or None
        self._inverted = bool(physical_inverted)
        self._limit_entity = limit_entity or None
        self._margin_pct = float(limit_margin_pct or 0.0)
        if not self._physical_entity:
            self._last_w = self._prev_w = self._unusable_since = None
            self._source = "none"

    @property
    def has_physical(self) -> bool:
        return self._physical_entity is not None

    def read_physical(self, states, now) -> float | None:
        """Real grid power in W (+import / -export), or None if unusable."""
        if not self._physical_entity:
            self._source = "none"
            return None
        state = states.get(self._physical_entity)
        value = _read_watts(state)
        if value is not None:
            ts = getattr(state, "last_reported", None) or getattr(
                state, "last_updated", None)
            if ts is not None and (now - ts).total_seconds() > PHYSICAL_MAX_AGE_S:
                value = None
        if value is None:
            if self._unusable_since is None:
                self._unusable_since = now
            self._source = "fallback"
            return None
        if self._inverted:
            value = -value
        self._unusable_since = None
        self._prev_w = self._last_w
        self._last_w = value
        self._source = "physical"
        return value

    def physical_is_new(self) -> bool:
        """True if the last value read differs from the one read before."""
        return self._last_w is not None and self._last_w != self._prev_w

    def peak_limit(self, states, static_limit_w, now) -> float:
        """Effective peak limit in W.

        The margin applies only to the entity value, not to the static limit.
        """
        if self._limit_entity:
            value = _read_watts(states.get(self._limit_entity))
            if value is not None:
                value *= 1.0 - self._margin_pct / 100.0
                value = max(PEAK_LIMIT_MIN_W, min(PEAK_LIMIT_MAX_W, value))
                self._held_w, self._held_at = value, now
                return self._set_limit(value, "entity")
            if (self._held_w is not None and self._held_at is not None
                    and now - self._held_at <= PEAK_LIMIT_HOLD):
                return self._set_limit(self._held_w, "held")
        return self._set_limit(static_limit_w, "static")

    def _set_limit(self, value, source) -> float:
        self._limit_w, self._limit_source = value, source
        return value

    def status(self) -> dict:
        return {
            "physical_entity": self._physical_entity,
            "physical_w": self._last_w,
            "physical_source": self._source,
            "unusable_since": self._unusable_since,
            "peak_limit_w": self._limit_w,
            "peak_limit_source": self._limit_source,
        }
