"""Bounded and lifecycle-aware access to Home Assistant Recorder.

Recorder history is an optional recovery input (the daily charge/discharge
counter migration in ``sensors/calculated_sensors.py``). It must never become
part of the control path, and a config-entry reload must be able to invalidate
work which is still waiting for an executor result:

* only one Recorder query runs at a time;
* every query is for one local calendar day (23, 24, or 25 real hours);
* the returned state list is detached from Recorder's mapping immediately;
* a generation token prevents late executor results from being applied.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import partial
from typing import Any

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackfillToken:
    """Immutable ownership token captured by one Recorder query."""

    coordinator: "RecorderBackfillCoordinator"
    generation: int

    def is_valid(self) -> bool:
        """Return whether this job may still apply results."""
        return self.coordinator.is_token_valid(self)


def local_day_bounds(
    local_date: date,
    timezone: Any,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Return the absolute bounds for one local day, capped for the current day.

    ``datetime`` subtraction on a zone-aware pair is deliberately not used to
    construct the endpoint.  Combining each wall-clock midnight separately
    preserves the real 23/25-hour length on DST transitions.
    """
    start = datetime.combine(local_date, time.min, tzinfo=timezone)
    end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=timezone)
    if now is not None:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone)
        else:
            now = now.astimezone(timezone)
        if now.date() == local_date:
            end = min(end, now)
    return start, end


class RecorderBackfillCoordinator:
    """Serialize Recorder queries and expose cancellation-safe helpers."""

    def __init__(self, hass: Any, config_entry: Any) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self._query_lock = asyncio.Lock()
        self._generation = 1
        self._stopping = False

    @property
    def generation(self) -> int:
        """Return the current entry-owned generation."""
        return self._generation

    @property
    def stopping(self) -> bool:
        return self._stopping

    def is_token_valid(self, token: BackfillToken | None) -> bool:
        """Check a token without touching any Store state."""
        return bool(
            token is not None
            and not self._stopping
            and token.coordinator is self
            and token.generation == self._generation
        )

    def new_token(self) -> BackfillToken:
        """Return a token for a small caller-owned query."""
        return BackfillToken(self, self._generation)

    async def async_query(
        self,
        token: BackfillToken,
        entity_id: str,
        start: datetime,
        end: datetime,
        *,
        block: str | None = None,
        include_start_time_state: bool | None = None,
    ) -> list[Any] | None:
        """Query one bounded Recorder range and return only its state list.

        ``None`` means the token was invalidated.  An empty list is a valid
        query result and is intentionally distinct from cancellation.
        """
        if not token.is_valid():
            return None
        start_ts = start.timestamp()
        end_ts = end.timestamp()
        if end_ts <= start_ts:
            return []

        try:
            from homeassistant.components.recorder import get_instance, history
        except ImportError:
            return []

        try:
            recorder = get_instance(self.hass)
            async with self._query_lock:
                if not token.is_valid():
                    return None
                if include_start_time_state is None:
                    executor_job = recorder.async_add_executor_job(
                        history.state_changes_during_period,
                        self.hass,
                        start,
                        end,
                        entity_id,
                    )
                else:
                    query = partial(
                        history.state_changes_during_period,
                        self.hass,
                        start,
                        end,
                        entity_id,
                        include_start_time_state=include_start_time_state,
                    )
                    executor_job = recorder.async_add_executor_job(query)
                try:
                    # Cancelling the asyncio wrapper does not necessarily
                    # stop the executor thread. Keep _query_lock held until
                    # that thread has returned, so a reload cannot overlap
                    # it with a new Recorder query.
                    states_map = await asyncio.shield(executor_job)
                except asyncio.CancelledError:
                    try:
                        await asyncio.shield(executor_job)
                    except Exception:  # noqa: BLE001 - cancellation wins
                        pass
                    raise
            if not token.is_valid():
                return None
            if not isinstance(states_map, Mapping):
                return []
            # Detach the list from the Recorder result before yielding.
            states = list(states_map.get(entity_id, ()) or ())
            del states_map
            await asyncio.sleep(0)
            return states if token.is_valid() else None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a single block is retryable
            _LOGGER.debug(
                "Recorder query failed for %s (%s): %s", entity_id, block, exc
            )
            return []

    async def async_cancel(self) -> None:
        """Invalidate every outstanding token (entry unload)."""
        self._stopping = True
        self._generation += 1


__all__ = [
    "BackfillToken",
    "RecorderBackfillCoordinator",
    "local_day_bounds",
]
