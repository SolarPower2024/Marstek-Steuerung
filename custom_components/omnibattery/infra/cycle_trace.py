"""Control-cycle trace (fork addition, docs/fork/08-trace.md).

One line per control cycle with every intermediate value of the command
pipeline, plus event lines for latch changes and real register writes. Built to
find the cause of relay chatter: which stage turned a discharge into 0 W and
with which inputs.

Switched by the level of its own logger, independent of the integration's:

    service: logger.set_level
    data:
      custom_components.omnibattery.trace: debug     # on
      custom_components.omnibattery.trace: warning   # off

While on, lines go to the Home Assistant log (search ``omnibattery.trace``)
and to ``<config>/omnibattery_trace/trace.log`` (rotating, 10 x 10 MB).

Nothing in here may influence control: every entry point is a no-op while the
logger is off, and every failure is swallowed.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import queue
import time

TRACE_LOGGER_NAME = "custom_components.omnibattery.trace"
TRACE_DIR_NAME = "omnibattery_trace"
TRACE_FILE_NAME = "trace.log"
TRACE_FILE_MAX_BYTES = 10 * 1024 * 1024
TRACE_FILE_BACKUPS = 10

_LOG = logging.getLogger(TRACE_LOGGER_NAME)
_CTX_ATTR = "_trace_ctx"

_listener: logging.handlers.QueueListener | None = None
_cycle_no = 0


def enabled() -> bool:
    return _LOG.isEnabledFor(logging.DEBUG)


def setup_file(trace_dir: str) -> None:
    """Attach the rotating trace file once per process.

    Called in an executor job (creates the directory). Afterwards all file I/O
    runs on the QueueListener's thread, never on the event loop.
    """
    global _listener
    if _listener is not None:
        return
    try:
        os.makedirs(trace_dir, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(trace_dir, TRACE_FILE_NAME),
            maxBytes=TRACE_FILE_MAX_BYTES,
            backupCount=TRACE_FILE_BACKUPS,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(message)s",
                                               "%Y-%m-%d %H:%M:%S"))
        q: queue.SimpleQueue = queue.SimpleQueue()
        _LOG.addHandler(logging.handlers.QueueHandler(q))
        _listener = logging.handlers.QueueListener(q, handler)
        _listener.start()
    except Exception:  # noqa: BLE001 - a trace must never break setup
        _LOG.warning("Trace file could not be set up", exc_info=True)


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------
def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:.1f}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_fmt(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(f"{k}:{_fmt(v)}" for k, v in value.items()) + "}"
    return str(value).replace(" ", "_")


def _line(kind: str, fields: dict) -> str:
    return kind + " " + " ".join(f"{k}={_fmt(v)}" for k, v in fields.items())


# ---------------------------------------------------------------------------
# per-cycle context
# ---------------------------------------------------------------------------
def begin(controller) -> None:
    """Open a cycle record and snapshot what the controller sees."""
    global _cycle_no
    if not enabled():
        _set_ctx(controller, None)
        return
    try:
        _cycle_no += 1
        ctx = {"cyc": _cycle_no, "_t0": time.monotonic()}
        _set_ctx(controller, ctx)
        ctx["prev_p"] = getattr(controller, "previous_power", None)
        ctx["out_sign"] = getattr(controller, "last_output_sign", None)
        ctx["zc_armed"] = getattr(controller, "_zero_cross_since", None) is not None
        ctx["dwell_armed"] = getattr(controller, "_relay_shutoff_since", None) is not None
        ctx["sg_latched"] = getattr(controller, "_surplus_guard_latched", None)
        ctx["bat"] = battery_snapshot(getattr(controller, "coordinators", []))
    except Exception:  # noqa: BLE001
        _set_ctx(controller, None)


def put(controller, **fields) -> None:
    ctx = getattr(controller, _CTX_ATTR, None)
    if ctx is None:
        return
    ctx.update(fields)


def put_lazy(controller, build) -> None:
    """``put`` for fields that need calls to compute: only built while tracing,
    and a failure while building them never reaches the control cycle."""
    ctx = getattr(controller, _CTX_ATTR, None)
    if ctx is None:
        return
    try:
        ctx.update(build())
    except Exception as err:  # noqa: BLE001
        ctx["trace_err"] = type(err).__name__


def append(controller, key: str, value) -> None:
    """Collect repeated values (a guard evaluated several times per cycle)."""
    ctx = getattr(controller, _CTX_ATTR, None)
    if ctx is None:
        return
    ctx.setdefault(key, []).append(value)


def end(controller, error: BaseException | None = None) -> None:
    ctx = getattr(controller, _CTX_ATTR, None)
    if ctx is None:
        return
    _set_ctx(controller, None)
    try:
        t0 = ctx.pop("_t0", None)
        ctx.setdefault("exit", "early")
        ctx["new_p"] = getattr(controller, "previous_power", None)
        ctx["sg_latched_end"] = getattr(controller, "_surplus_guard_latched", None)
        if error is not None:
            ctx["exc"] = type(error).__name__
        if t0 is not None:
            ctx["ms"] = round((time.monotonic() - t0) * 1000.0, 1)
        _LOG.debug(_line("CYC", ctx))
    except Exception:  # noqa: BLE001
        pass


def event(kind: str, controller=None, **fields) -> None:
    """A standalone line (latch change, register write)."""
    if not enabled():
        return
    try:
        ctx = getattr(controller, _CTX_ATTR, None) if controller is not None else None
        if ctx is not None:
            fields = {"cyc": ctx.get("cyc"), **fields}
        _LOG.debug(_line(kind, fields))
    except Exception:  # noqa: BLE001
        pass


def _set_ctx(controller, ctx) -> None:
    try:
        setattr(controller, _CTX_ATTR, ctx)
    except Exception:  # noqa: BLE001
        pass


def battery_snapshot(coordinators) -> dict:
    """Compact per-battery view of the values the guards and PD read."""
    out = {}
    for coordinator in coordinators or []:
        try:
            data = getattr(coordinator, "data", None) or {}
            out[getattr(coordinator, "name", "?")] = (
                f"soc{_fmt(data.get('battery_soc'))}"
                f"/bp{_fmt(data.get('battery_power'))}"
                f"/ac{_fmt(data.get('ac_power'))}"
                f"/acd{_fmt(data.get('ac_delivered_power'))}"
                f"/inv{_fmt(data.get('inverter_state'))}"
                f"/fm{_fmt(data.get('force_mode'))}"
                f"/sc{_fmt(data.get('set_charge_power'))}"
                f"/sd{_fmt(data.get('set_discharge_power'))}"
                f"/av{_fmt(getattr(coordinator, 'is_available', None))}"
                f"/man{_fmt(getattr(coordinator, 'battery_manual_mode_enabled', None))}"
            )
        except Exception:  # noqa: BLE001
            continue
    return out
