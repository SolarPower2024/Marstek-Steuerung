"""Hardware driver abstraction for the energy manager.

A *driver* owns all brand-specific hardware I/O — transport, connection
lifecycle, telemetry decoding, and control commands — behind a single
brand-agnostic interface (:class:`base.BatteryDriver`). The coordinator and the
control loop talk only to that interface, so a second battery brand is added by
writing a new driver, not by editing the control logic.

Architectural rule: shared calculations, state representations, orchestration,
and user-facing behavior remain brand/model agnostic. Hardware differences are
expressed through semantic driver capabilities or hooks; brand/model branches
are limited to driver selection, configuration, and migrations.

Drivers:
  - ``marstek``: Modbus-TCP, register based, polled.

See ``docs/plans/driver_abstraction.md`` for the phased extraction plan.
"""

from .base import (
    DELIVERED_AC_POWER_KEY,
    BatteryDriver,
    DriverCapabilities,
    ReadGroup,
    SetpointResult,
    TelemetrySnapshot,
)
from .marstek import MarstekModbusDriver

__all__ = [
    "DELIVERED_AC_POWER_KEY",
    "BatteryDriver",
    "DriverCapabilities",
    "ReadGroup",
    "SetpointResult",
    "TelemetrySnapshot",
    "MarstekModbusDriver",
]
