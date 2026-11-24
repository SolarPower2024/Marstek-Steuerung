"""Integration-level configuration constants for Omnibattery."""

DOMAIN = "omnibattery"

# Prefix for every persistent_notification this integration creates/dismisses.
# Lets automations (e.g. the Telegram-forwarding blueprint) reliably select only
# our notifications by ID. All notification_id values MUST start with this.
NOTIFICATION_ID_PREFIX = "marstek_venus_"

# Internal debug switches for maintainer-level troubleshooting.
# Keep these disabled for normal Home Assistant debug logging; enabling them can
# generate very large logs on systems with fast polling or multiple batteries.
DEBUG_RAW_MODBUS_READS = False
DEBUG_POLL_SENSOR_SKIPS = False
DEBUG_POLL_SENSOR_VALUES = False
DEBUG_CONTROL_LOOP_DETAIL = False

SCAN_INTERVAL = {
    "high": 2,       # fast-changing sensors, e.g., power, alarms
    "medium": 5,     # moderately changing sensors, e.g., voltage, current
    "low": 30,        # slow-changing sensors, e.g., cumulative energy counters
    "very_low": 600   # rarely changing info, e.g., device info, firmware versions
}

# Battery version support
CONF_BATTERY_VERSION = "battery_version"
CONF_DC_PV_CONNECTED = "dc_pv_connected"
SUPPORTED_VERSIONS = ["v2", "v3", "vA", "vD"]

# Modbus slave/unit id. Default 1 (Marstek factory default for a direct
# connection). A Modbus TCP proxy that fans out to several batteries on one
# host:port distinguishes them by slave id, so this must be configurable.
CONF_SLAVE_ID = "slave_id"
DEFAULT_SLAVE_ID = 1

# Serial / Modbus-RTU connection. When set, the battery is reached over a serial
# port (USB-RS485 adapter) instead of Modbus TCP (discussion #350). Path string
# such as "/dev/ttyUSB0" or "COM3"; empty/absent means TCP. Marstek's RTU link is
# fixed at 115200 8N1 by the hardware, so only the port path is configurable.
CONF_SERIAL_PORT = "serial_port"
SERIAL_BAUDRATE = 115200

# Per-battery opt-in: the Modbus TCP connection lands on an RS485 gateway
# (Elfin EW11 and friends) instead of the battery's own TCP server, so the
# inter-message wait that server imposes does not apply (issue #411). Drops the
# spacing to MESSAGE_WAIT_MS_RS485_GATEWAY for any Marstek firmware version.
CONF_RS485_GATEWAY = "rs485_gateway"

# Maximum power (W) per battery version — used by config_flow to set slider limits
MAX_POWER_BY_VERSION = {
    "v2": 2500,
    "v3": 2500,
    "vA": 1500,
    "vD": 2500,
}
DEFAULT_VERSION = "v2"

VENUS_D_2500W_MIN_EMS_VERSION = 149
VENUS_D_LEGACY_MAX_POWER_W = 2200


def max_power_for_battery_version(
    battery_version: str,
    ems_version: object = None,
) -> int:
    """Return the safe per-battery power ceiling for a Marstek model/FW.

    Venus D accepts 2500 W only from EMS firmware 149 onward.  Unknown Venus D
    firmware is treated conservatively until register 30200 has been read.
    """
    default = int(MAX_POWER_BY_VERSION.get(battery_version, 2500))
    if battery_version != "vD":
        return default
    try:
        firmware = int(float(ems_version))
    except (TypeError, ValueError):
        return VENUS_D_LEGACY_MAX_POWER_W
    return (
        default
        if firmware >= VENUS_D_2500W_MIN_EMS_VERSION
        else VENUS_D_LEGACY_MAX_POWER_W
    )

# Maximum number of independently controllable battery devices in one entry.
# Keep the aggregate system-power slider envelope in sync with the largest
# supported per-battery power ceiling.
MAX_BATTERIES = 10
MAX_SYSTEM_POWER_W = MAX_BATTERIES * max(MAX_POWER_BY_VERSION.values())

# Multi-battery activation thresholds derived from efficiency tables (η external)
# Crossover = power at which splitting load across 2 batteries becomes more efficient
# than running a single battery.  Based on Venus efficiency measurements at 2500 W max.
MULTI_BATTERY_DISCHARGE_CROSSOVER_W = 1500   # 60% of 2500 W physical max
MULTI_BATTERY_CHARGE_CROSSOVER_W    = 1750   # 70% of 2500 W physical max
MULTI_BATTERY_HYSTERESIS_GAP        = 0.10   # fraction gap: activation → deactivation
MULTI_BATTERY_MIN_ACTIVATION        = 0.50   # floor: never activate below this fraction
# Cap at 0.95: stage 5% before single-battery saturation to absorb demand transients,
# even when efficiency analysis alone would keep a single battery active.
MULTI_BATTERY_MAX_ACTIVATION        = 0.95

# Charge hysteresis (per-battery). Hysteresis is mandatory — after a battery
# reaches its ceiling it must not recharge until SOC falls this far below the
# latched peak. The 2 % floor keeps the deadband wider than typical SOC-reading
# drift/quantization, which would otherwise release the latch and cause charge
# chatter at the top. Existing installs are migrated (async_migrate_entry
# v7 -> v8): previously-configured values are preserved, others get the floor.
MIN_CHARGE_HYSTERESIS_PERCENT = 2
DEFAULT_CHARGE_HYSTERESIS_PERCENT = 2
MAX_CHARGE_HYSTERESIS_PERCENT = 50
# Keep additional batteries active long enough to avoid pulsing when bursty loads
# repeatedly cross the split-load threshold. Refreshed while the split condition holds.
MULTI_BATTERY_SELECTION_HOLD_SECONDS = 120

CONF_SOLAR_PRODUCTION_SENSOR = "solar_production_sensor"
CONF_HOUSEHOLD_CONSUMPTION_SENSOR = "household_consumption_sensor"  # legacy; migrated out in v6
CONF_MAX_CONTRACTED_POWER = "max_contracted_power"

# Optional alternate meter for installations whose backed-up/off-grid circuit is
# measured separately. The software switch only selects this entity as the active
# meter; enabling the battery's physical off-grid/EPS output remains the user's
# responsibility.
CONF_OFFGRID_POWER_SENSOR = "offgrid_power_sensor"
CONF_OFFGRID_METER_INVERTED = "offgrid_meter_inverted"
CONF_OFFGRID_MODE_ENABLED = "offgrid_mode_enabled"

# Optional three-phase current protection.  The main consumption sensor remains
# the only control input; these current sensors are safety limits for the phase
# where a battery is physically installed.
CONF_THREE_PHASE_ENABLED = "three_phase_enabled"
CONF_PHASE_1_CURRENT_SENSOR = "phase_1_current_sensor"
CONF_PHASE_2_CURRENT_SENSOR = "phase_2_current_sensor"
CONF_PHASE_3_CURRENT_SENSOR = "phase_3_current_sensor"
CONF_PHASE_1_FUSE_SIZE = "phase_1_fuse_size"
CONF_PHASE_2_FUSE_SIZE = "phase_2_fuse_size"
CONF_PHASE_3_FUSE_SIZE = "phase_3_fuse_size"
CONF_BATTERY_PHASE = "battery_phase"

PHASE_L1 = "l1"
PHASE_L2 = "l2"
PHASE_L3 = "l3"
PHASE_VALUES = (PHASE_L1, PHASE_L2, PHASE_L3)
# Explicit selector value for a battery that is outside the protected phase
# layout. Runtime leaves it outside the phase-protection envelope.
PHASE_UNASSIGNED = "unassigned"
PHASE_ASSIGNMENT_VALUES = (PHASE_UNASSIGNED, *PHASE_VALUES)
PHASE_CONFIG = {
    PHASE_L1: (CONF_PHASE_1_CURRENT_SENSOR, CONF_PHASE_1_FUSE_SIZE),
    PHASE_L2: (CONF_PHASE_2_CURRENT_SENSOR, CONF_PHASE_2_FUSE_SIZE),
    PHASE_L3: (CONF_PHASE_3_CURRENT_SENSOR, CONF_PHASE_3_FUSE_SIZE),
}
DEFAULT_THREE_PHASE_ENABLED = False
DEFAULT_PHASE_FUSE_SIZE_A = 25


def normalize_battery_phase(value: object) -> str:
    """Normalize legacy/missing battery phase data to the selector value."""
    return value if value in PHASE_VALUES else PHASE_UNASSIGNED


# Battery set-points are expressed in active watts, while phase protection is
# measured in RMS amperes.  Use a conservative fixed conversion for the
# single-phase AC connection; the user-facing fuse limit remains in amperes.
PHASE_NOMINAL_VOLTAGE_V = 230.0
PHASE_BATTERY_POWER_FACTOR = 0.90

# Time slots (operation slots) — v3 schema keys
CONF_TIME_SLOTS = "no_discharge_time_slots"  # legacy key, kept for compat
CONF_SLOT_START_TIME = "start_time"
CONF_SLOT_END_TIME = "end_time"
CONF_SLOT_DAYS = "days"
CONF_SLOT_ENABLED = "enabled"
CONF_SLOT_BATTERY_SCOPE = "battery_scope"
CONF_SLOT_ALLOW_CHARGE = "allow_charge"
CONF_SLOT_ALLOW_DISCHARGE = "allow_discharge"
CONF_SLOT_SOC_OVERRIDE_ENABLED = "soc_override_enabled"
CONF_SLOT_SOC_MAX = "soc_max"
CONF_SLOT_SOC_MIN = "soc_min"
CONF_SLOT_POWER_OVERRIDE_ENABLED = "power_override_enabled"
CONF_SLOT_MAX_CHARGE_POWER_W = "max_charge_power_w"
CONF_SLOT_MAX_DISCHARGE_POWER_W = "max_discharge_power_w"
CONF_SLOT_MODE = "mode"

SLOT_BATTERY_SCOPE_ALL = "all"
SLOT_MODE_PD = "pd"
SLOT_MODE_MANUAL = "manual"

DEFAULT_SLOT_BATTERY_SCOPE = SLOT_BATTERY_SCOPE_ALL
DEFAULT_SLOT_ALLOW_CHARGE = False
DEFAULT_SLOT_ALLOW_DISCHARGE = True
DEFAULT_SLOT_SOC_OVERRIDE_ENABLED = False
DEFAULT_SLOT_POWER_OVERRIDE_ENABLED = False
DEFAULT_SLOT_MODE = SLOT_MODE_PD
DEFAULT_SLOT_SOC_MIN_FLOOR = 12
DEFAULT_SLOT_SOC_MAX_CEILING = 100
MAX_TIME_SLOTS = 8

# Weekly Full Charge Configuration
CONF_ENABLE_WEEKLY_FULL_CHARGE = "enable_weekly_full_charge"
CONF_MANUAL_MODE_ENABLED = "manual_mode_enabled"
CONF_BATTERY_MANUAL_MODE_ENABLED = "battery_manual_mode_enabled"
CONF_WEEKLY_FULL_CHARGE_DAY = "weekly_full_charge_day"
CONF_ENABLE_BALANCE_MONITOR = "enable_balance_monitor"

# Cell Balance Monitor
BALANCE_STORAGE_KEY = "balance_history"
BALANCE_STORAGE_VERSION = 1
# Public event emitted by the extracted active-balance blueprint after a
# settled WAIT_MEASURE interval. The integration reads its own coordinator
# telemetry when handling this event; the blueprint does not provide voltages.
EVENT_BLUEPRINT_BALANCE_MEASUREMENT_READY = "omnibattery_balance_measurement_ready"
# Marstek cells ship from the factory with a sizeable top-of-charge imbalance
# (commonly ~170-180 mV). At 3.55 V the LiFePO4 curve is very steep, so this
# factory spread is normal — not a fault. The status thresholds below are
# absolute raw-delta values chosen to sit above that factory baseline, so a
# fresh battery reads green. The baseline offset is subtracted only in the
# rising-trend magnitude gate, so steady factory-level readings do not trip a
# trend alert (slope is unaffected — subtracting a constant does not change it).
BALANCE_BASELINE_OFFSET_MV = 180  # mV — factory top-of-charge imbalance, used by the trend gate
BALANCE_THRESHOLD_YELLOW = 200    # mV — raw delta above this: yellow
BALANCE_THRESHOLD_ORANGE = 230    # mV — raw delta above this: orange
BALANCE_THRESHOLD_RED = 250       # mV — raw delta above this: red
BALANCE_HISTORY_MAX = 52         # ~1 year of weekly readings
BALANCE_RED_CONSECUTIVE_ALERT = 2
BALANCE_TREND_ALERT_AVG_MV = 40.0   # baseline-corrected avg must exceed this (raw avg > 220 mV) to fire a rising-trend alert
BALANCE_NOTIFY_COOLDOWN_DAYS = 7    # min days between cell-imbalance notifications per battery

# Optional normal full-charge protection.
# When enabled per battery, slow charging only while the target is 100% and
# cells enter the top voltage range. This is voltage-only; SOC is intentionally
# ignored because some batteries report it unreliably near the top.
NORMAL_BALANCE_TAPER_CELL_VOLTAGE = 3.48
# Hysteresis: taper latch releases only after cell drops this far below entry.
# Prevents oscillation: at low taper power the cell can relax below 3.48 V but not
# to 3.44 V, so the latch stays active until the battery is meaningfully discharged.
NORMAL_BALANCE_TAPER_EXIT_CELL_VOLTAGE = 3.44
NORMAL_BALANCE_PAUSE_CELL_VOLTAGE = 3.60
NORMAL_BALANCE_CHARGE_POWER_W = 200
NORMAL_BALANCE_MEASURE_WAIT_SECONDS = 60
# Venus A/D can expose several coupled packs through one inverter. Their top
# cell telemetry may reach the normal pause point while the other packs still
# need charge, so keep the tapered command until the BMS confirms its cutoff.
NORMAL_BALANCE_BMS_CUTOFF_VERSIONS = ("vA", "vD")
# For Venus E models, once the top voltage is reached the taper stops charging
# and latches. It does NOT re-trickle when the cell relaxes (that would pin the
# cell at the top voltage and keep some v3 BMSs from leaving standby to
# discharge). Venus A/D coupled packs use the BMS-owned path above instead. The
# The normal SOC hysteresis releases after its configured percentage, while the
# voltage taper latch itself releases below the 3.44 V exit threshold.
# SOC recalibration on a stuck top voltage.
# A pack that hits the top cell voltage (pause point) while the BMS reports a SOC
# below full may have a drifted coulomb counter. Some BMS firmware only corrects
# that counter after performing the charge cutoff itself (users see e.g. 70% or 96% with a
# cell already at 3.60 V). In that case, instead of holding at the pause voltage,
# keep charging at the tapered power until the BMS itself cuts off, attempting to
# recalibrate SOC. The firmware may keep reporting the old SOC after cutoff, so
# recalibration is explicitly best-effort rather than a completion guarantee.
NORMAL_BALANCE_RECAL_SOC_THRESHOLD = 99        # %: below this, make one best-effort recalibration attempt
NORMAL_BALANCE_RECAL_CUTOFF_POWER_W = 10       # W: charge collapsed (BMS terminated)
NORMAL_BALANCE_RECAL_CUTOFF_CYCLES = 5         # consecutive cycles to confirm the BMS cutoff
NORMAL_BALANCE_RECAL_INVERTER_STANDBY = 1      # inverter_state raw value for Standby
INVERTER_STATE_AC_BYPASS = 6                  # inverter_state raw value for Bypass (grid passed through to the backup port)
# After a cutoff above the pause voltage, allow one extra 200 W charge when the
# cell has relaxed to this voltage.  The retry is deliberately one-shot.
NORMAL_BALANCE_RECAL_RETRY_CELL_VOLTAGE = 3.57

# BMS low-SOC discharge cutoff (low-SOC counterpart to NORMAL_BALANCE_RECAL_*).
# Below this SOC the BMS may refuse to discharge on its own (protective cutoff,
# e.g. a weak cell sagging under load) even though the reported SOC is still
# above the configured min_soc. The battery then ACKs the discharge command but
# delivers ~0W. Treat that as an expected BMS cutoff instead of a non-responsive
# fault: no repair, no wake. A refusal that lasts LOW_SOC_REFUSAL_CONFIRM_S takes
# the battery out of the discharge pool until its SOC recovers (issue #545), so
# the share goes to a battery that delivers instead of into one that stopped.
BMS_DISCHARGE_CUTOFF_SOC = 20                  # %: below this, refused discharge = BMS cutoff, not a fault
# Long enough to ride out a pack hand-off on a coupled-pack Venus A/D, short
# against the hours a stopped battery was commanded before (#408, #545).
LOW_SOC_REFUSAL_CONFIRM_S = 60

# Bus-load reduction: the PD loop normally reads 4 registers back after every
# power write (ACK verify + non-delivery detection). Those reads are the bulk of
# the write-path traffic. To cut bus load, only read back every Nth *real* write
# (option-B skips don't count); the others are write-only (no readback, no
# post-write settle delay). Trade-off: ACK mismatches and a battery that stops
# delivering are caught up to N writes later instead of immediately.
PD_READBACK_EVERY_N_WRITES = 5

# Deferred exact-ACK verification: a readback may confirm within the driver's
# echo tolerance while the battery is still ramping toward the set-point (see
# _ACK_TOLERANCE_* in drivers/marstek.py). The exact settled check happens
# later — an exact readback echo or the poll-time skip-write comparison. If
# this many consecutive readback cycles confirm only by tolerance without an
# exact match in between, warn once: the write chain (RS485-ETH bridge serial
# config, firmware) lags more than the settle window ever covers.
ACK_INEXACT_STREAK_WARN = 5

# Transient burst poll: right after a REAL power command change, the delivered-
# power reading (ac_power / battery_power) is polled at this cadence instead of
# its normal "high" scan interval, so _measured_battery_power() isn't stale
# during the actuator ramp. Bounded window — sustained bus load is unaffected.
BURST_POLL_WINDOW_S = 6.0
BURST_POLL_INTERVAL_S = 1.0

# Feedforward step detection (PD mode): a confirmed load step (kettle/oven-sized)
# is covered with ONE deadbeat cycle (measured - error, same law as no-PD mode)
# instead of the ~13 s exponential approach of the incremental P term; the PD
# resumes fine adjustment from the new operating point on the next cycle.
# Detection is 2-sample: an error jump beyond max(5*deadband, floor) arms a
# candidate, and it only fires if the next sample still shows the deviation
# (same sign, >= confirm ratio of the jump) — a 1-sample excursion is a meter
# spike and is rejected. Hardcoded on purpose (no config entities), same policy
# as the adaptive filter / burst poll above.
FEEDFORWARD_STEP_FLOOR_W = 400        # W: minimum error jump to arm a candidate
FEEDFORWARD_CONFIRM_RATIO = 0.8       # fraction of the jump that must persist next sample
FEEDFORWARD_CANDIDATE_MAX_AGE_S = 5.0 # s: candidate expires if not checked in time (deadband gap)
FEEDFORWARD_COOLDOWN_S = 10.0         # s: min spacing between fires (covers actuator ramp 3-6s)
FEEDFORWARD_PULSE_GUARD_S = 30.0      # s: opposite-sign step this soon after a fire = pulsing load, skip

# Zero-cross hold (direction-flip dwell): minimum seconds an opposite-direction
# (charge<->discharge) request must persist before the PD is allowed to flip.
# During a downward load step the grid shows a transient export while the
# discharging battery is still ramping down (actuator settle 3-6 s measured), so
# the incremental PD crosses zero and would command a real charge on another
# battery, zeroing the discharger (5-40 s dips, ping-pong every 1-3 min). The
# effective window is max(this, 2 * slowest actuator_latency_s) — see
# _apply_zero_cross_hold. Legitimate sustained surplus flips after the window.
PD_ZERO_CROSS_MIN_HOLD_S = 5.0

# A grid-power sensor at or above this interval is slow enough to warrant setup
# guidance and a Repairs warning. Slow sensors remain supported: their latest
# reading is authoritative until MAX_SENSOR_STALE_S has elapsed.
SLOW_SENSOR_WARNING_INTERVAL_S = 10.0

# Maximum age of the latest grid-power reading before the watchdog may perform a
# stale safety recalculation. This is intentionally time-based rather than a
# number of 2 s watchdog cycles, so scheduler phase and delayed cycles cannot
# shorten the promised tolerance.
MAX_SENSOR_STALE_S = 65.0

# How long the main grid sensor may go without publishing a valid reading
# before it counts as dead: a Repairs issue is raised and automatic control is
# stopped (every automatic battery to 0W) until it publishes again. Past
# MAX_SENSOR_STALE_S the loop already stops integrating P/D and holds the last
# command; without this limit that hold lasts as long as the silence, bounded
# only by the SOC blockers. Long enough to ride out a restart or a brief
# integration reload, and far above any supported sensor cadence (slow sensors
# are authoritative up to MAX_SENSOR_STALE_S), short enough to catch a wedged
# P1 bridge or a template sensor whose inputs stopped moving.
MAIN_SENSOR_DEAD_S = 300.0

# How long a phase sensor must stay unreadable before the degradation becomes a
# Repairs entry. Long enough that the short gaps absorbed by the discharge hold
# stay silent, short enough that a genuinely dead sensor is reported the same
# day.
PHASE_SENSOR_DEGRADED_REPAIR_S = 300.0

# Consecutive slow main-sensor intervals before the slow-sensor repair is raised.
# Debouncing prevents a single outage/restart gap from flagging an otherwise fast
# sensor. Clearing uses SLOW_SENSOR_RECOVERY_INTERVALS instead.
SLOW_SENSOR_WARN_INTERVALS = 3

# Consecutive fast intervals before an existing slow-sensor repair is cleared.
# Deliberately much longer than SLOW_SENSOR_WARN_INTERVALS: clearing is the
# hysteresis side, and a sensor hovering around the threshold would otherwise
# create and delete the repair over and over. At a 3 s meter this is about a
# minute of sustained fast cadence.
SLOW_SENSOR_RECOVERY_INTERVALS = 20


# How long a configured sensor entity must stay absent from the state machine
# before a Repairs issue names it (#419). Long enough for the integration that
# provides it to finish setting up after a restart, short enough that a stale
# reference the options flow preserved becomes visible the same session.
MISSING_SENSOR_ISSUE_DELAY_S = 900.0

# A readback at or below this settle latency (seconds,
# DriverCapabilities.readback_latency_s with actuator_latency_s as fallback)
# reflects the new command within one poll. Slower telemetry paths skip the
# hot-path ACK readback so they cannot block the shared control loop.
HOT_PATH_READBACK_MAX_LATENCY_S = 1.5

# Discharge engage grace: a slow inverter takes seconds to
# reverse from charge/idle into discharge — measured up to ~20-30 s on a cold
# charge→discharge transition. During that window an ACK'd command legitimately
# reads back 0 W out, which is engage latency, not a fault. Suppress non-delivery
# recording for this long after the commanded direction flips to discharge so the
# inverter is not excluded before it has had time to engage. A battery that never
# engages is still caught, just this many seconds later.
DISCHARGE_ENGAGE_GRACE_S = 30

# A step up in commanded power by at least this factor restarts that direction's
# engage grace too, when the battery met the delivery bar (10% of the command)
# for the previous command: it needs time to ramp to the new power, and a driver
# that serves cached telemetry keeps showing the pre-step power for seconds. The
# PD's re-ramp after an anti-windup re-anchor adds near-equal increments onto the
# measured power, so each of its steps stays below 2x while the battery delivers
# anything, and a battery stuck at 0 W never meets the previous bar. Neither
# restarts the grace, so a battery that stalls under that cycle is still caught.
ENGAGE_GRACE_STEP_FACTOR = 2

# PD Quality only updates while the PD controls the grid. A gap longer than
# PD_QUALITY_RESTART_GAP_S between two updates means the PD was not in control
# meanwhile (a grid-charge slot, the weekly full charge, manual mode, a long
# block). Its numbers then describe the situation before the gap, and when the
# PD takes over the batteries are still running the previous command. So the
# metric starts over: it skips the first PD_QUALITY_RESTART_SETTLE_S, the time
# a battery gets to engage after a direction switch, and then averages one
# metric window of fresh readings before the sensor shows a verdict again. The
# gap is two 60s metric windows, well above the slowest supported meter
# (MAX_SENSOR_STALE_S), which keeps updating the metric on every reading.
PD_QUALITY_RESTART_GAP_S = 120.0
PD_QUALITY_RESTART_SETTLE_S = float(DISCHARGE_ENGAGE_GRACE_S)

# Top-of-charge tail: the last stretch before 100% often tapers hard (CC/CV
# tail current) well before is_battery_full() reports the battery full or
# tick_bms_cutoff() confirms a cutoff (which needs power <= 10 W *and*
# Standby). Observed on a hybrid inverter: 48-189 W of a 7000 W command for
# ~15 minutes while SOC climbed 99% -> 100%, inverter never reporting
# Standby. That gap was recorded as non-delivery and drove wake nudges
# (RS485 re-assert) despite SOC genuinely still rising. Matches
# tick_bms_cutoff's own taper-zone gate so both treat the same SOC band as
# "possibly tapering": this SOC, or below it the top cell in the taper zone
# (NORMAL_BALANCE_TAPER_CELL_VOLTAGE), for a BMS that tapers on cell voltage.
HIGH_SOC_CHARGE_TAPER_FLOOR = 99  # %: at/above this, judge charge non-delivery as a possible taper first

# Bound on the exemption above: a battery that gets physically stuck in the
# 99-100% band, rather than genuinely tapering to completion, must still
# surface as a fault eventually -- just later than one charging normally
# through it. Comfortably longer than the ~15-26 minutes observed for a
# real taper.
HIGH_SOC_CHARGE_TAPER_GRACE_S = 45 * 60

# Idle-runaway floor: a battery commanded to idle (0 W) that is actually moving
# more than this many watts has slipped out of RS485 forced mode and is running
# its own internal logic (a v3 can export to grid this way — see issue #434). Above
# this floor we re-assert RS485 control and write a real standby instead of letting
# the bus-load skip-write trust the matching set-points. Set above standby self-draw
# / metering noise so a resting battery doesn't trigger needless writes.
IDLE_RUNAWAY_POWER_W = 100

# A battery freshly commanded from a move into idle is still ramping down while
# battery_power telemetry lags (actuator settle ~3-4 s + poll grain ~3 s), so
# the set-points read standby a cycle or two before the delivered power reaches
# 0. Judging idle-runaway inside that window fires the RS485 re-assert on every
# ordinary discharge→idle transition (false positives, extra bus writes on a
# fragile v3). Suppress the judgment for this long after the command flips to
# idle; a genuine runaway is still caught, just this many seconds later.
IDLE_RUNAWAY_GRACE_S = 15

# Min-SOC re-entry hysteresis for discharge availability: after a battery empties
# to min_soc its resting SOC rebounds 1-2% (cell relaxation), which would re-admit
# it for a sliver of discharge and drop it again — relay on/off ping-pong plus
# micro-cycles in the worst SOC region. Once excluded at min_soc, the battery only
# becomes dischargeable again after recovering this many percent above min_soc.
DISCHARGE_MIN_SOC_REENTRY_MARGIN = 2

CONF_FULL_CHARGE_VOLTAGE_TAPER_ENABLED = "full_charge_voltage_taper_enabled"
DEFAULT_FULL_CHARGE_VOLTAGE_TAPER_ENABLED = True


# Temperature-based charge power derate. When a battery runs hot, charge power is
# proportionally reduced: full power at/below the limit, ramping linearly down to
# a floor (% of the normal charge ceiling) as temperature rises across the band,
# and back up as it cools. Charge-only; per-battery on internal_temperature.
CONF_ENABLE_TEMP_CHARGE_LIMIT = "enable_temp_charge_limit"
DEFAULT_ENABLE_TEMP_CHARGE_LIMIT = False
CONF_TEMP_CHARGE_LIMIT_C = "temp_charge_limit_c"
DEFAULT_TEMP_CHARGE_LIMIT_C = 40  # °C, derate starts at/above this temperature
CONF_TEMP_CHARGE_LIMIT_BAND_C = "temp_charge_limit_band_c"
DEFAULT_TEMP_CHARGE_LIMIT_BAND_C = 10  # °C, width over which power ramps to the floor
CONF_TEMP_CHARGE_LIMIT_FLOOR_PCT = "temp_charge_limit_floor_pct"
DEFAULT_TEMP_CHARGE_LIMIT_FLOOR_PCT = 40  # % of normal charge power at limit+band (0 = full stop)
# Optionally apply the SAME derate curve to discharge. Discharge tolerates heat
# better than charge, so this shares one (charge-tuned) threshold as a deliberate
# compromise; its main benefit is staying under the BMS hard over-temp cutoff.
CONF_TEMP_LIMIT_APPLY_DISCHARGE = "temp_limit_apply_discharge"
DEFAULT_TEMP_LIMIT_APPLY_DISCHARGE = False


# Weekday mapping (mon=0, sun=6, matches datetime.weekday())
WEEKDAY_MAP = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6
}

# Capacity Protection Mode Configuration
CONF_CAPACITY_PROTECTION_ENABLED = "capacity_protection_enabled"
CONF_CAPACITY_PROTECTION_EXCLUDED_DEVICES = "capacity_protection_excluded_devices"
CONF_CAPACITY_PROTECTION_SOC_THRESHOLD = "capacity_protection_soc_threshold"
CONF_CAPACITY_PROTECTION_LIMIT = "capacity_protection_limit"

DEFAULT_CAPACITY_PROTECTION_SOC = 30
DEFAULT_CAPACITY_PROTECTION_LIMIT = 2500

# FORK: dynamic peak limit from an entity (falls back to the fixed limit above),
# a safety margin below that limit, and an optional separate physical grid meter.
CONF_CAPACITY_PROTECTION_LIMIT_ENTITY = "capacity_protection_limit_entity"
CONF_CAPACITY_PROTECTION_LIMIT_MARGIN = "capacity_protection_limit_margin"
DEFAULT_CAPACITY_PROTECTION_LIMIT_MARGIN = 0
# FORK: release hysteresis on top of the SOC threshold. Once below the
# threshold, normal discharge is allowed again only at threshold + hysteresis.
CONF_CAPACITY_PROTECTION_SOC_HYSTERESIS = "capacity_protection_soc_hysteresis"
DEFAULT_CAPACITY_PROTECTION_SOC_HYSTERESIS = 2


def capacity_protection_soc_hysteresis(data) -> float:
    """FORK: configured SOC release hysteresis in % (missing -> default)."""
    value = data.get(CONF_CAPACITY_PROTECTION_SOC_HYSTERESIS)
    if value is None:
        return float(DEFAULT_CAPACITY_PROTECTION_SOC_HYSTERESIS)
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return float(DEFAULT_CAPACITY_PROTECTION_SOC_HYSTERESIS)
CONF_PHYSICAL_GRID_SENSOR = "physical_grid_sensor"
CONF_PHYSICAL_GRID_INVERTED = "physical_grid_inverted"
DEFAULT_PHYSICAL_GRID_INVERTED = False
# FORK: repair issue once the real grid sensor has been unusable this long (s)
PHYSICAL_GRID_UNUSABLE_ISSUE_DELAY_S = 300.0
# FORK: leaving physical-meter peak shaving for "conserving" stops the battery
# only after this result has held continuously this long (s).
PEAK_EXIT_HOLD_S = 6.0

# PD Controller Advanced Configuration Keys
CONF_PD_KP = "pd_controller_kp"
CONF_PD_KD = "pd_controller_kd"
CONF_PD_DEADBAND = "pd_controller_deadband"
CONF_PD_MAX_POWER_CHANGE = "pd_controller_max_power_change"
CONF_PD_DIRECTION_HYSTERESIS = "pd_controller_direction_hysteresis"
CONF_PD_MIN_CHARGE_POWER = "pd_min_charge_power"
CONF_PD_MIN_DISCHARGE_POWER = "pd_min_discharge_power"
CONF_PD_RELAY_COOLDOWN = "pd_relay_cooldown"
# FORK: relay guard (anti-chatter) - master switch plus individual locks.
CONF_PD_RELAY_GUARD_ENABLED = "pd_relay_guard_enabled"
CONF_PD_RELAY_MIN_OFF = "pd_relay_min_off"
CONF_PD_DIRECTION_LOCK = "pd_direction_lock"
CONF_PD_ENGAGE_MARGIN = "pd_engage_margin"
CONF_PD_GUARD_BYPASS_ERROR = "pd_guard_bypass_error"
CONF_PD_RELAY_SWITCH_BUDGET = "pd_relay_switch_budget"
# FORK: reduced panel overview (UI preference).
CONF_PANEL_MINIMAL_OVERVIEW = "panel_minimal_overview"
CONF_PD_MIN_CYCLE_INTERVAL = "pd_min_cycle_interval"
CONF_TARGET_GRID_POWER = "pd_target_grid_power"
# No-PD direct-tracking mode (opt-in): track the consumption sensor 1:1 with no
# integral/derivative/smoothing curve. Reuses the deadband, min charge/discharge
# power, relay min-ON and target-grid-power knobs above; adds only a command delay.
CONF_NO_PD_MODE_ENABLED = "no_pd_mode_enabled"

# --- Mixed-fleet control (a DC-coupled hybrid beside an AC battery) ----------
# Which battery serves the house first, and whether it is handed the real
# quantity directly instead of waiting for the meter to deviate. Both opt-in.
CONF_PRIMARY_BATTERY = "primary_battery"
DEFAULT_PRIMARY_BATTERY = ""
CONF_PRIMARY_FEEDFORWARD_ENABLED = "primary_feedforward_enabled"
DEFAULT_PRIMARY_FEEDFORWARD_ENABLED = False
# Which battery is filled first. Empty = follow the day's outlook.
CONF_CHARGE_PRIORITY = "charge_priority"
DEFAULT_CHARGE_PRIORITY = ""
# How far the guards have to want to move the standing command before the
# deadband/stale shortcuts are skipped to let them. Below this a correction is
# not worth a write.
GUARD_PENDING_TOLERANCE_W = 100
# How clear a surplus has to be before the guard blocks discharge. Wider than
# meter noise, so a cloud edge does not toggle the battery every cycle; release
# has no band at all, because by then the house genuinely needs the battery.
SURPLUS_GUARD_HYSTERESIS_W = 100
# How long that surplus has to persist before the guard latches. The figure mixes
# a meter reading with battery readings polled seconds apart: a battery just told
# to ramp up still reports its old output while the meter already sees the new
# one, and reads as a surplus for one sample. The effective window is
# max(this, 2 * slowest actuator_latency_s), as for the zero-cross hold.
SURPLUS_GUARD_MIN_HOLD_S = 5.0
CONF_NO_PD_COMMAND_DELAY = "no_pd_command_delay"
CONF_ENABLE_SYSTEM_POWER_LIMITS = "enable_system_power_limits"
CONF_SYSTEM_MAX_CHARGE_POWER = "system_max_charge_power"
CONF_SYSTEM_MAX_DISCHARGE_POWER = "system_max_discharge_power"

# Default PD Controller Parameters
# Lowered from Kp 0.65 / Kd 0.5 to curb overshoot under the cadence-independent
# control loop; existing installs on the old defaults are migrated (see
# async_migrate_entry, config entry v3 -> v4).
DEFAULT_PD_KP = 0.35
DEFAULT_PD_KD = 0.3
DEFAULT_PD_DEADBAND = 40
DEFAULT_PD_MAX_POWER_CHANGE = 800
DEFAULT_PD_DIRECTION_HYSTERESIS = 60
DEFAULT_PD_MIN_CHARGE_POWER = 0       # Minimum charge power (0 = disabled)
DEFAULT_PD_MIN_DISCHARGE_POWER = 0    # Minimum discharge power (0 = disabled)
# Relay anti-chatter: minimum time (s) the battery stays engaged after leaving
# idle before it may return to 0. Stops the relay toggling on/off when the grid
# signal hovers at the deadband edge during solar ramp-up/down. 0 = disabled
# (default: preserves the pre-feature behaviour; opt-in via the slider).
DEFAULT_PD_RELAY_COOLDOWN = 0
# FORK: relay guard defaults. Guard is off by default (v1.6.0 behaviour); every
# lock is individually disabled by 0.
DEFAULT_PD_RELAY_GUARD_ENABLED = False
DEFAULT_PD_RELAY_MIN_OFF = 0
DEFAULT_PD_DIRECTION_LOCK = 0
DEFAULT_PD_ENGAGE_MARGIN = 0
DEFAULT_PD_GUARD_BYPASS_ERROR = 200
DEFAULT_PD_RELAY_SWITCH_BUDGET = 0
DEFAULT_PANEL_MINIMAL_OVERVIEW = False
# Power held in the already-engaged direction while the cooldown is running, when
# the user's min charge/discharge power is 0 (otherwise that min is used).
RELAY_COOLDOWN_HOLD_POWER = 100
# Minimum spacing (s) between event-driven control cycles. The grid sensor can
# publish several times per second; without a floor, each out-of-deadband cycle
# issues a Modbus write burst, which slow TCP-serial bridges (e.g. Elfin EW11)
# can choke on. Drops surplus sensor-triggered cycles; 2 s safety-timer ticks
# inside the window keep the last command (#654). 0 = disabled (pre-feature behaviour); default 1 s caps bursts.
DEFAULT_PD_MIN_CYCLE_INTERVAL = 1.0
# Grid-sample EMA smoothing time constant (s). Single source of truth so no-PD
# mode can drop it to 0 (raw passthrough) and restore it when the mode is off.
DEFAULT_GRID_FILTER_TAU = 2.0
# No-PD direct-tracking mode defaults. Command delay debounces fast meters: events
# inside a delay window collapse into one command issued on the latest value
# (0 = act on every event, paced only by CONF_PD_MIN_CYCLE_INTERVAL).
DEFAULT_NO_PD_MODE_ENABLED = False
DEFAULT_NO_PD_COMMAND_DELAY = 0.0
DEFAULT_TARGET_GRID_POWER = 0
DEFAULT_SYSTEM_MAX_CHARGE_POWER = 0       # 0 = disabled
DEFAULT_SYSTEM_MAX_DISCHARGE_POWER = 0    # 0 = disabled

# Legacy alias so existing __init__.py imports don't break during transition
DEFAULT_SLOT_TARGET_GRID_POWER = DEFAULT_TARGET_GRID_POWER


# --- Configured system power envelope ---------------------------------------
# Single source of truth for "how much power did the user configure this system
# to move", derived from config_entry.data alone (no hass, no coordinators).
#
# Deliberately NOT MarstekController._effective_system_capacity: that one sums
# the *live* per-coordinator limits after temperature derate, SOC taper and slot
# ceilings for the current control cycle. This is the *static configured*
# envelope, which is what a UI bound must advertise. Do not merge them — slider
# bounds must not jump around with battery temperature.

def _non_negative_int(value) -> int:
    """Coerce a config value to a non-negative int (missing/None/junk -> 0)."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def effective_battery_power_limits(battery) -> tuple[int, int]:
    """Return the static effective (charge_w, discharge_w) limits for one battery.

    New entries may persist the normalized ``device_max_*`` and
    ``configured_max_*`` fields. Legacy entries use ``max_*_power`` and soft-max
    entries keep the user's additional ceiling
    in ``user_max_*_power``. Missing normalized/soft-limit keys preserve legacy
    entries while applying the same ``min(device, configured)`` rule.
    """
    device_charge = battery.get("device_max_charge_power")
    device_discharge = battery.get("device_max_discharge_power")
    configured_charge = battery.get("configured_max_charge_power")
    configured_discharge = battery.get("configured_max_discharge_power")

    if device_charge is None:
        device_charge = battery.get("max_charge_power")
    if device_discharge is None:
        device_discharge = battery.get("max_discharge_power")
    if configured_charge is None:
        configured_charge = battery.get(
            "user_max_charge_power", battery.get("max_charge_power")
        )
    if configured_discharge is None:
        configured_discharge = battery.get(
            "user_max_discharge_power", battery.get("max_discharge_power")
        )

    return (
        min(_non_negative_int(device_charge), _non_negative_int(configured_charge)),
        min(
            _non_negative_int(device_discharge),
            _non_negative_int(configured_discharge),
        ),
    )


def total_battery_power(data) -> tuple[int, int]:
    """Return summed static effective (charge_w, discharge_w) limits."""
    batteries = data.get("batteries") or []
    limits = [effective_battery_power_limits(battery) for battery in batteries]
    return (
        sum(charge_w for charge_w, _ in limits),
        sum(discharge_w for _, discharge_w in limits),
    )


def system_power_limits_enabled(data) -> bool:
    """Return whether the optional system-wide power cap is active.

    Entries predating CONF_ENABLE_SYSTEM_POWER_LIMITS had the feature on iff a
    non-zero cap was set, so that stays the default for the missing key.
    """
    charge = _non_negative_int(
        data.get(CONF_SYSTEM_MAX_CHARGE_POWER, DEFAULT_SYSTEM_MAX_CHARGE_POWER)
    )
    discharge = _non_negative_int(
        data.get(CONF_SYSTEM_MAX_DISCHARGE_POWER, DEFAULT_SYSTEM_MAX_DISCHARGE_POWER)
    )
    return bool(
        data.get(CONF_ENABLE_SYSTEM_POWER_LIMITS, charge > 0 or discharge > 0)
    )


def effective_system_power(data) -> tuple[int, int]:
    """Return (charge_w, discharge_w) after the optional system-wide cap.

    A cap of 0 means "disabled" for that direction, mirroring
    MarstekController._configured_system_limit. The cap can only narrow the
    per-battery sum, never widen it.
    """
    charge_w, discharge_w = total_battery_power(data)
    if not system_power_limits_enabled(data):
        return charge_w, discharge_w

    charge_cap = _non_negative_int(
        data.get(CONF_SYSTEM_MAX_CHARGE_POWER, DEFAULT_SYSTEM_MAX_CHARGE_POWER)
    )
    discharge_cap = _non_negative_int(
        data.get(CONF_SYSTEM_MAX_DISCHARGE_POWER, DEFAULT_SYSTEM_MAX_DISCHARGE_POWER)
    )
    return (
        min(charge_w, charge_cap) if charge_cap else charge_w,
        min(discharge_w, discharge_cap) if discharge_cap else discharge_w,
    )


# PD Tuning Profiles
# One-click presets for the PD response-shape parameters (Kp, Kd, max power
# change). Selecting a profile writes those at once; the "custom" profile leaves
# the sliders to the user. Profiles are ordered smoothest → fastest. "balanced"
# equals the shipping defaults, so an untouched install maps onto it.
#
# Deadband is deliberately NOT part of the profiles: it is both the user's
# precision/meter-noise preference and the reference the control-quality sensor
# measures against (oscillation is counted only outside the deadband). Bundling it
# into a profile would clobber that preference and bias the sensor's own yardstick.
CONF_PD_TUNING_PROFILE = "pd_tuning_profile"
PD_PROFILE_CUSTOM = "custom"
DEFAULT_PD_TUNING_PROFILE = PD_PROFILE_CUSTOM

PD_TUNING_PROFILES = {
    "very_smooth": {
        CONF_PD_KP: 0.22,
        CONF_PD_KD: 0.15,
        CONF_PD_MAX_POWER_CHANGE: 400,
    },
    "smooth": {
        CONF_PD_KP: 0.30,
        CONF_PD_KD: 0.25,
        CONF_PD_MAX_POWER_CHANGE: 600,
    },
    "balanced": {
        CONF_PD_KP: DEFAULT_PD_KP,
        CONF_PD_KD: DEFAULT_PD_KD,
        CONF_PD_MAX_POWER_CHANGE: DEFAULT_PD_MAX_POWER_CHANGE,
    },
    "aggressive": {
        CONF_PD_KP: 0.55,
        CONF_PD_KD: 0.45,
        CONF_PD_MAX_POWER_CHANGE: 1200,
    },
    "very_aggressive": {
        CONF_PD_KP: 0.75,
        CONF_PD_KD: 0.45,
        CONF_PD_MAX_POWER_CHANGE: 2000,
    },
}

# Option order shown in the select (custom last); 6 total incl. manual.
PD_TUNING_PROFILE_OPTIONS = list(PD_TUNING_PROFILES.keys()) + [PD_PROFILE_CUSTOM]

# Effective value of each profiled PD param when absent from config_entry.data.
_PD_PROFILE_PARAM_DEFAULTS = {
    CONF_PD_KP: DEFAULT_PD_KP,
    CONF_PD_KD: DEFAULT_PD_KD,
    CONF_PD_MAX_POWER_CHANGE: DEFAULT_PD_MAX_POWER_CHANGE,
}


def pd_profile_from_params(data) -> str:
    """Return the preset name whose values match the PD gain params in `data`.

    Falls back to PD_PROFILE_CUSTOM when no preset matches (i.e. the user has
    hand-tuned the sliders). Deadband is not considered — it is user-owned and not
    part of the profiles. Compared with a small epsilon to tolerate float Kp/Kd.
    """
    for name, params in PD_TUNING_PROFILES.items():
        if all(
            abs(float(data.get(key, _PD_PROFILE_PARAM_DEFAULTS[key])) - float(value)) < 1e-6
            for key, value in params.items()
        ):
            return name
    return PD_PROFILE_CUSTOM


CONF_METER_INVERTED = "meter_inverted"


# Marker for CONFIG_NUMBER_DEFINITIONS entries whose slider bounds are derived
# at runtime; the authored min/max become the fallback.
DYNAMIC_BOUNDS_SYSTEM_POWER = "system_power"
DYNAMIC_BOUNDS_SYSTEM_POWER_CAP = "system_power_cap"

# Configuration Number Definitions (for config entities exposed in the UI)
CONFIG_NUMBER_DEFINITIONS = [
    {
        "key": CONF_PD_KP,
        "name": "PD Kp",
        "min": 0.1,
        "max": 2.0,
        "step": 0.05,
        "default": DEFAULT_PD_KP,
        "icon": "mdi:tune",
    },
    {
        "key": CONF_PD_KD,
        "name": "PD Kd",
        "min": 0.0,
        "max": 2.0,
        "step": 0.05,
        "default": DEFAULT_PD_KD,
        "icon": "mdi:tune",
    },
    {
        "key": CONF_PD_DEADBAND,
        "name": "PD Deadband",
        "min": 0,
        "max": 200,
        "step": 5,
        "unit": "W",
        "default": DEFAULT_PD_DEADBAND,
        "icon": "mdi:arrow-collapse-horizontal",
    },
    {
        "key": CONF_PD_MAX_POWER_CHANGE,
        "name": "PD Max Power Change",
        "min": 100,
        "max": 2000,
        "step": 50,
        "unit": "W",
        "default": DEFAULT_PD_MAX_POWER_CHANGE,
        "icon": "mdi:delta",
    },
    {
        "key": CONF_PD_DIRECTION_HYSTERESIS,
        "name": "PD Direction Hysteresis",
        "min": 0,
        "max": 200,
        "step": 5,
        "unit": "W",
        "default": DEFAULT_PD_DIRECTION_HYSTERESIS,
        "icon": "mdi:swap-horizontal",
    },
    {
        "key": CONF_PD_MIN_CHARGE_POWER,
        "name": "PD Min Charge Power",
        "min": 0,
        "max": 2000,
        "step": 10,
        "unit": "W",
        "default": DEFAULT_PD_MIN_CHARGE_POWER,
        "icon": "mdi:battery-charging-low",
    },
    {
        "key": CONF_PD_MIN_DISCHARGE_POWER,
        "name": "PD Min Discharge Power",
        "min": 0,
        "max": 2000,
        "step": 10,
        "unit": "W",
        "default": DEFAULT_PD_MIN_DISCHARGE_POWER,
        "icon": "mdi:battery-low",
    },
    {
        "key": CONF_PD_RELAY_COOLDOWN,
        "name": "PD Relay Cooldown",
        "min": 0,
        "max": 600,
        "step": 1,
        "unit": "s",
        "default": DEFAULT_PD_RELAY_COOLDOWN,
        "icon": "mdi:timer-cog-outline",
    },
    {
        "key": CONF_PD_RELAY_MIN_OFF,
        "name": "PD Relay Minimum Off Time",
        "min": 0,
        "max": 600,
        "step": 5,
        "unit": "s",
        "default": DEFAULT_PD_RELAY_MIN_OFF,
        "icon": "mdi:timer-off-outline",
    },
    {
        "key": CONF_PD_DIRECTION_LOCK,
        "name": "PD Direction Change Lock",
        "min": 0,
        "max": 900,
        "step": 5,
        "unit": "s",
        "default": DEFAULT_PD_DIRECTION_LOCK,
        "icon": "mdi:swap-horizontal-bold",
    },
    {
        "key": CONF_PD_ENGAGE_MARGIN,
        "name": "PD Engage Margin",
        "min": 0,
        "max": 500,
        "step": 5,
        "unit": "W",
        "default": DEFAULT_PD_ENGAGE_MARGIN,
        "icon": "mdi:arrow-expand-horizontal",
    },
    {
        "key": CONF_PD_GUARD_BYPASS_ERROR,
        "name": "PD Guard Bypass Error",
        "min": 50,
        "max": 3000,
        "step": 10,
        "unit": "W",
        "default": DEFAULT_PD_GUARD_BYPASS_ERROR,
        "icon": "mdi:shield-off-outline",
    },
    {
        "key": CONF_PD_RELAY_SWITCH_BUDGET,
        "name": "PD Relay Switch Budget",
        "min": 0,
        "max": 120,
        "step": 1,
        "unit": "/h",
        "default": DEFAULT_PD_RELAY_SWITCH_BUDGET,
        "icon": "mdi:counter",
    },
    {
        "key": CONF_PD_MIN_CYCLE_INTERVAL,
        "name": "PD Min Cycle Interval",
        "min": 0,
        "max": 5,
        "step": 0.1,
        "unit": "s",
        "default": DEFAULT_PD_MIN_CYCLE_INTERVAL,
        "icon": "mdi:timer-pause-outline",
    },
    {
        "key": CONF_NO_PD_COMMAND_DELAY,
        "name": "No-PD Command Delay",
        "min": 0,
        "max": 3,
        "step": 0.1,
        "unit": "s",
        "default": DEFAULT_NO_PD_COMMAND_DELAY,
        "icon": "mdi:timer-sand",
    },
    {
        "key": CONF_TARGET_GRID_POWER,
        "name": "PD Target Grid Power",
        # Fallback only. The live bounds follow the configured system power
        # envelope (see DYNAMIC_BOUNDS_SYSTEM_POWER); these apply when no
        # battery limits are configured yet, or a direction is configured to 0 W.
        "min": -2500,
        "max": 2500,
        "step": 10,
        "unit": "W",
        "default": DEFAULT_TARGET_GRID_POWER,
        "icon": "mdi:transmission-tower-export",
        "dynamic_bounds": DYNAMIC_BOUNDS_SYSTEM_POWER,
    },
    {
        "key": CONF_SYSTEM_MAX_CHARGE_POWER,
        "name": "System Max Charge Power",
        "min": 0,
        # Fallback only; the live maximum is the configured sum of charge
        # limits, so batteries with 4 kW (or higher) ceilings are supported.
        "max": MAX_SYSTEM_POWER_W,
        "step": 50,
        "unit": "W",
        "default": DEFAULT_SYSTEM_MAX_CHARGE_POWER,
        "icon": "mdi:battery-arrow-up-outline",
        "condition": CONF_ENABLE_SYSTEM_POWER_LIMITS,
        "dynamic_bounds": DYNAMIC_BOUNDS_SYSTEM_POWER_CAP,
        "power_direction": "charge",
    },
    {
        "key": CONF_SYSTEM_MAX_DISCHARGE_POWER,
        "name": "System Max Discharge Power",
        "min": 0,
        # Fallback only; the live maximum is the configured sum of discharge
        # limits, so batteries with 4 kW (or higher) ceilings are supported.
        "max": MAX_SYSTEM_POWER_W,
        "step": 50,
        "unit": "W",
        "default": DEFAULT_SYSTEM_MAX_DISCHARGE_POWER,
        "icon": "mdi:battery-arrow-down-outline",
        "condition": CONF_ENABLE_SYSTEM_POWER_LIMITS,
        "dynamic_bounds": DYNAMIC_BOUNDS_SYSTEM_POWER_CAP,
        "power_direction": "discharge",
    },
    {
        "key": CONF_MAX_CONTRACTED_POWER,
        "name": "Max Contracted Power",
        "min": 1000,
        "max": 20000,
        "step": 100,
        "unit": "W",
        "default": 7000,
        "icon": "mdi:transmission-tower",
    },
    {
        "key": CONF_CAPACITY_PROTECTION_SOC_THRESHOLD,
        "name": "Capacity Protection SOC Threshold",
        "min": 20,
        "max": 100,
        "step": 1,
        "unit": "%",
        "default": DEFAULT_CAPACITY_PROTECTION_SOC,
        "icon": "mdi:battery-alert-variant-outline",
        "condition": CONF_CAPACITY_PROTECTION_ENABLED,
    },
    {
        # FORK: release hysteresis (on top of the SOC threshold)
        "key": CONF_CAPACITY_PROTECTION_SOC_HYSTERESIS,
        "name": "Capacity Protection Discharge Hysteresis",
        "min": 0,
        "max": 10,
        "step": 1,
        "unit": "%",
        "default": DEFAULT_CAPACITY_PROTECTION_SOC_HYSTERESIS,
        "icon": "mdi:battery-sync-outline",
        "condition": CONF_CAPACITY_PROTECTION_ENABLED,
    },
    {
        "key": CONF_CAPACITY_PROTECTION_LIMIT,
        "name": "Capacity Protection Peak Limit",
        "min": 500,
        "max": 20000,
        "step": 100,
        "unit": "W",
        "default": DEFAULT_CAPACITY_PROTECTION_LIMIT,
        "icon": "mdi:flash-alert",
        "condition": CONF_CAPACITY_PROTECTION_ENABLED,
    },
    {
        "key": CONF_CAPACITY_PROTECTION_LIMIT_MARGIN,
        "name": "Capacity Protection Limit Margin",
        "min": 0,
        "max": 30,
        "step": 1,
        "unit": "%",
        "default": DEFAULT_CAPACITY_PROTECTION_LIMIT_MARGIN,
        "icon": "mdi:percent-outline",
        "condition": CONF_CAPACITY_PROTECTION_ENABLED,
    },
]
