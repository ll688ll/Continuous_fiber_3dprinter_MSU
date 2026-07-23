# PRD — Multi-Axis Motion Control Library ("Center Control Module")

| | |
|---|---|
| Status | Draft v0.2 (Q1–Q7 resolved) |
| Date | 2026-07-22 |
| Owner | Jacky Lyu |
| Related docs | `docs/Software_SRS.md` (full printer system), `docs/COMMAND_LIST.md`, `docs/FMC006GE31-二次开发库说明-V1.2.docx` (vendor SDK manual) |

## 1. Overview

### 1.1 Problem statement
The continuous-fiber 3D printer platform uses an FMC006GE31 motion controller
driving linear actuators. Today the only working code is a raw ctypes demo
script (`examples/fmc006ge31_basic_motion.py`). There is no reusable, safe,
tested layer between "vendor DLL calls" and "application logic."

### 1.2 Product vision
A **standalone Python library** (working name `fmc_motion`, see §1.4) that acts
as the central control module for the **6 motion axes** of one FMC006GE31
controller. Any consumer — the future printer application, test scripts, lab
automation, other students/researchers — installs the library and controls axes
through a clean, documented API without touching the vendor DLL.

The library is the concrete implementation of the Motion HAL sketched in the
SRS (`BaseMotionController`); it is a **subset milestone** of the full printer
system, deliverable and useful on its own.

### 1.3 Relationship to the existing repo
- `controllers/motion/base_motion_controller.py` → contract absorbed into the
  library's public interface (extended, see §5).
- `controllers/motion/fmc006ge31/` → migrates into the library as the **real
  backend** (vendor DLL via ctypes).
- A new `SimMotionController` → the **sim backend** (§4.7); satisfies SRS
  FR-CTL-010 (mock controller).
- The G-code / tool / UI layers of the SRS are **out of scope** for this PRD;
  `printer_msu` will later depend on this library for motion.

### 1.4 Packaging decision (was Q7)
The library ships as a **standalone package** so consumers do not inherit
`printer_msu`'s UI dependencies (PySide6). Plan: a separate package directory
in this repo (monorepo style) with its own `pyproject.toml`, e.g.
`libs/fmc-motion/`, installable via `pip install -e libs/fmc-motion`;
`printer_msu` adds it as a dependency. It can be split into its own repo later
without code changes.

## 2. Goals and non-goals

### 2.1 Goals (v1)
1. Control up to **6 independent axes** on one FMC006GE31 controller
   (absolute/relative moves, emulated continuous jog, stop).
2. **Homing / self-check**: home each axis against its limit switch and
   **discover the usable working range** automatically (both-end limit switches
   are present on every actuator — confirmed).
3. **Motion parameter configuration** per axis: speed, acceleration,
   deceleration (defaults + per-move overrides), persisted to a config file.
4. **Per-axis command queue**: commands to a busy axis are queued and dispatched
   in order when the axis finishes (the DLL itself rejects/ignores commands to a
   busy axis, so queuing is a library responsibility — see §4.5).
5. **Library-first design**: importable package, no UI dependency, stable public
   API, usable from a plain Python script in <10 lines.
6. **Real and Sim modes** behind the same interface, switchable by config/factory
   argument, so consumers and tests run without hardware.
7. Safety basics: software travel limits (from discovered range), stop / e-stop
   (all-axis immediate stop — the DLL has no working global stop, see §4.6),
   refusal to move unhomed axes (configurable), normalized errors mapping the
   vendor return-code table.
8. Logging of every command, result, and fault for diagnostics.

### 2.2 Non-goals (v1)
- Coordinated interpolated moves (`Line_2Axis`/`Line_3Axis`/`Arc_2Axis`).
  **Decision: v1 is independent axes only.** The API keeps `capabilities()` so
  interpolation can be added later without breaking consumers.
- Multi-controller support. **Decision: not near-term.** The DLL supports
  multiple device ids, and `device_id` stays in the config model, but v1 targets
  exactly one controller.
- G-code parsing/streaming, print jobs, tool/heater/fan control (SRS scope).
- Closed-loop correction from encoder feedback (encoder position is read and
  reported alongside step-count position, not acted on).
- Linux real-hardware support. The vendor ships Ubuntu/ARM libraries with
  identical signatures, so this is a future soft goal; v1 real mode targets
  Windows only. Sim mode runs everywhere.

## 3. Users and use cases

| User | Use case |
|---|---|
| Library consumer (researcher / other developer) | `import` the library, connect, home all, move axes to positions for an experiment. |
| Printer application (future) | Uses the library as its motion HAL during prints. |
| Developer of the library itself | Runs the full test suite in sim mode on any OS; runs hardware acceptance tests on the lab PC. |
| Operator during bring-up | Runs the self-check routine to verify wiring, limit switches, and travel of a newly installed actuator. |

### Primary usage sketch (acceptance target)
```python
from fmc_motion import create_controller

ctrl = create_controller("machine.yaml", mode="sim")   # or mode="real"
ctrl.connect()
report = ctrl.self_check()          # homes axes, finds working range, returns report
ctrl.configure_axis(0, speed=100, accel=500, decel=500)
ctrl.move_absolute(0, 25.0)         # blocks until done (wait=False for async)
print(ctrl.get_status().positions)
ctrl.disconnect()
```

## 4. Functional requirements

### 4.1 Vendor API facts this design relies on (from SDK manual V1.2)
- Axis indices 0–5 map to X, Y, Z, X1, Y1, Z1; all positions/speeds are in
  **mm / mm/s / mm/s²** (controller internally applies lead/microstep from
  device parameters — no library-side unit scaling needed).
- `Jog_Single_Axis(id, axis, pos, speed, acc, dec, mode)` is the point-to-point
  move primitive (mode 1 = relative, 2 = absolute). There is **no velocity/
  continuous jog mode** in the DLL.
- A command to a moving axis is **not executed** (return code −10 "device busy"
  or silently unresponsive per manual).
- `Home_Single_Axis(id, axis, speed, accDec, fallStep, dir)`: dir 1 = home to
  positive limit, 2 = home to negative limit, 3 = zero at current position;
  `fallStep` = automatic back-off distance from the switch after homing.
- `Stop_Single_Axis(id, axis, mode)`: mode 1 = decelerate, 2 = immediate.
  `Stop_Run` (global) is documented **"未用" (unused)** — no global stop exists.
- `Get_Machine_Status` returns all-axis positions (step + encoder), speeds,
  ± limit-switch bitmasks, axis status, run status, and e-stop status in one
  call — the basis of `get_status()`.
- Return codes: 0 ok; −1 connect failed; −4 build failed; −5 send failed;
  −6/−7 receive errors; −8 null pointer; −10 busy. The library maps these to
  typed exceptions.

### 4.2 Connection & lifecycle
- **FR-CON-01**: `connect()` / `disconnect()` to a controller identified by
  (device_id, ip, port, tcp_mode) from config. Vendor return codes preserved in
  raised exceptions.
- **FR-CON-02**: `disconnect()` always releases the device (vendor requires
  `Close_Device` or the next connect fails); guaranteed on context-manager exit.
- **FR-CON-03**: Reconnect after failure without process restart.

### 4.3 Axis configuration
- **FR-CFG-01**: Config file (YAML/JSON) defines per axis: enabled, name/alias,
  controller axis index (0–5), homing direction (positive/negative limit),
  homing speed, homing acc/dec, home fall-back step, default speed/accel/decel,
  max speed/accel/decel, self-check creep speed, max search distance, and
  soft-limit safety margin.
- **FR-CFG-02**: `configure_axis(axis, ...)` updates runtime defaults; values
  validated against configured maxima.
- **FR-CFG-03**: Config round-trips to disk (`save_config` / load at create);
  discovered working ranges are persisted with a timestamp so a machine can
  skip re-discovery (but never skip homing) on restart — opt-in.
- **FR-CFG-04**: Invalid config (duplicate axis index, negative limits, missing
  homing direction) fails fast at load with a clear message.

### 4.4 Homing & self-check (core feature)
- **FR-HOME-01**: `home(axis)` homes one axis via `Home_Single_Axis` with
  config parameters; `home_all(order=...)` homes multiple axes in a
  configurable sequence (sequential by default; simultaneous opt-in).
- **FR-HOME-02**: Homed state tracked per axis; after homing the axis position
  is the vendor-defined zero (switch position + fall step); library treats that
  as machine zero (optional configured offset on top).
- **FR-HOME-03**: `self_check(axes=None)` per axis:
  1. read limit-switch states via `Get_Machine_Status`; verify sane (not both
     active),
  2. home to the configured primary limit (machine zero),
  3. creep toward the opposite end at the configured creep speed until the far
     limit-switch bit triggers or max-search distance is exceeded,
  4. record the discovered span; back off the safety margin,
  5. set soft limits = [margin, span − margin]; re-home (or move to zero) to
     finish in a known state.
- **FR-HOME-04**: `self_check` returns a structured per-axis report (pass/fail,
  discovered range, switch states observed, timings, errors), logged and
  exportable as JSON.
- **FR-HOME-05**: A failed step (far limit never triggers within max-search
  distance, both limits active, e-stop active, DLL error) aborts that axis's
  check with a specific error; remaining axes continue (configurable).
- **FR-HOME-06**: Homing/self-check refuse to start while e-stop is active.
- **FR-HOME-07 (bench validation)**: The behavior when a limit switch trips
  during a *normal* move is undocumented; M3 includes a controlled bench test,
  and the creep step must not rely on the controller stopping by itself — the
  library polls the limit bit and issues `Stop_Single_Axis(immediate)` when it
  trips.

### 4.5 Motion & queuing
- **FR-MOVE-01**: `move_absolute(axis, pos, speed=None, accel=None, decel=None)`
  and `move_relative(axis, delta, ...)` — omitted parameters use axis defaults.
  Implemented via `Jog_Single_Axis` mode 2 / mode 1 respectively.
- **FR-MOVE-02**: Both **blocking** (default; waits on `Check_Axis_Is_Stop`) and
  **non-blocking** (`wait=False`, returns a `MoveHandle`; `wait_for(handle |
  axis, timeout)`) execution.
- **FR-MOVE-03 (queue policy — decided)**: Commands to a busy axis are
  **queued** per axis (FIFO) and dispatched when the axis reports stopped.
  Because the DLL rejects commands to a moving axis, the queue is implemented in
  the library with a per-axis dispatcher. Requirements:
  - queue depth configurable (default unbounded; warning above threshold),
  - `stop(axis)` and `estop()` **flush the queue** (stop means stop, not
    "finish the backlog"),
  - soft-limit validation happens at *enqueue* time against the queue's
    predicted end position, and again at dispatch time,
  - `get_queue(axis)` / `clear_queue(axis)` for introspection and cancel.
- **FR-MOVE-04**: Soft limits enforced before dispatch: a target outside the
  homed working range raises `SoftLimitError` without touching hardware. Moves
  on an unhomed axis raise `NotHomedError` unless configured
  `allow_unhomed: true`.
- **FR-MOVE-05 (continuous jog — emulated)**: The DLL has no velocity mode
  (confirmed), so `jog(axis, direction, speed=None)` issues a bounded move to
  the soft-limit boundary in that direction at jog speed; `jog_stop(axis)`
  issues `Stop_Single_Axis`. On an unhomed axis, jog uses a configured
  max-jog-step instead and requires explicit opt-in.
- **FR-MOVE-06**: Multiple axes run concurrently; each axis's queue and state
  are independent.

### 4.6 Stop, e-stop, faults
- **FR-STOP-01**: `stop(axis=None, mode=controlled|immediate)` — one axis or
  all; flushes affected queues.
- **FR-STOP-02**: `estop()` — since the DLL's global `Stop_Run` is unused,
  e-stop = immediate `Stop_Single_Axis` on **all six axes** in a tight loop,
  plus queue flush, plus a latched software e-stop state that blocks new motion
  until `clear_estop()`. Dispatch order is fixed and total dispatch time is
  measured in the acceptance test (NFR-02).
- **FR-STOP-03**: Hardware e-stop status (from `machine_status.emergencyStatus`,
  2 = triggered) is surfaced in status and blocks new motion while active.
- **FR-STOP-04**: A limit switch triggering during a normal move (not homing /
  range-search / jog-to-limit) marks the axis faulted; the fault must be
  explicitly cleared (`clear_fault(axis)`).

### 4.7 Status & telemetry
- **FR-STAT-01**: `get_status()` returns a normalized snapshot mapped from
  `Get_Machine_Status`: per-axis position (step-count and encoder), speed,
  homed flag, busy flag, queue depth, ± limit-switch states, fault; plus
  controller-level run state, main/backup power state, and e-stop state.
- **FR-STAT-02**: All values in mm / mm/s (native DLL units — no scaling).
- **FR-STAT-03**: Optional background poller (`start_polling(rate_hz,
  callback)`); polling shares the DLL dispatch lock but must never starve
  motion/stop calls (stop has priority).

### 4.8 Sim mode
- **FR-SIM-01**: `SimMotionController` implements the identical public
  interface; selected via `mode="sim"`. Runs on any OS, no DLL.
- **FR-SIM-02**: Sim models motion kinematically — position evolves over time
  with commanded speed/accel/decel (trapezoidal profile) — so blocking waits,
  busy flags, queuing, and polling behave realistically. Sim reproduces the
  "busy axis rejects direct dispatch" behavior so the queue logic is exercised
  identically.
- **FR-SIM-03**: Sim models per-axis virtual limit switches at configurable
  positions so homing (including fall step) and the full `self_check`
  range-discovery run end-to-end in sim.
- **FR-SIM-04**: Sim supports fault injection: connection failure, vendor
  return codes (−1, −5, −10, …), limit stuck on/off, e-stop trigger, move
  timeout.
- **FR-SIM-05**: Sim records full command history for test assertions.
- **FR-SIM-06**: Optional time acceleration (`time_scale`) for fast CI runs.

### 4.9 Logging
- **FR-LOG-01**: Every public API call logs start / success / failure with
  parameters, duration, and vendor return codes, via standard `logging`
  (namespace `fmc_motion`), no global logging side effects.
- **FR-LOG-02**: Self-check reports exportable (JSON) for lab records.

## 5. Public API surface (v1)

Factory: `create_controller(config, mode="real"|"sim") -> MotionController`

| Area | Methods |
|---|---|
| Lifecycle | `connect`, `disconnect`, context manager |
| Config | `configure_axis`, `get_axis_config`, `save_config` |
| Homing | `home`, `home_all`, `self_check`, `is_homed` |
| Motion | `move_absolute`, `move_relative`, `jog`, `jog_stop`, `wait_for` |
| Queue | `get_queue`, `clear_queue` |
| Safety | `stop`, `estop`, `clear_estop`, `clear_fault`, `set_soft_limits_enabled` |
| Status | `get_status`, `get_position`, `start_polling`, `stop_polling`, `capabilities` |

Exceptions: `MotionError` (base) → `ControllerConnectionError`, `NotHomedError`,
`SoftLimitError`, `LimitHitError`, `EStopActiveError`, `DeviceBusyError`,
`HardwareFaultError` (carries vendor return code), `SelfCheckError`,
`MoveTimeoutError`.

Compatibility note: `printer_msu`'s existing `BaseMotionController` uses
multi-axis mapping signatures (`move_absolute(axes_positions: Mapping, ...)`).
Since v1 is independent-axis only, the library's canonical signatures are
single-axis; a thin mapping-style adapter is provided so `printer_msu` can
consume the library through its existing HAL contract.

## 6. Non-functional requirements

- **NFR-01 Thread safety**: public API callable from multiple threads; DLL
  access serialized by a single dispatch lock per controller; stop/e-stop jump
  the lock queue.
- **NFR-02 Responsiveness**: `stop`/`estop` begin hardware dispatch ≤ 100 ms
  from call; full 6-axis e-stop dispatch time is measured and reported in the
  acceptance run (SRS end-to-end target ≤ 500 ms).
- **NFR-03 Testability**: ≥ 90 % of library code exercised by the sim-mode test
  suite; all DLL specifics isolated in one backend module.
- **NFR-04 Portability**: package import and sim mode work on
  Windows/macOS/Linux; real mode requires Windows in v1 (vendor Linux libs
  exist for the future).
- **NFR-05 Distribution**: standalone package (`libs/fmc-motion/`, own
  `pyproject.toml`, no PySide6 dependency); 64-bit vendor DLL ships as package
  data; `printer_msu` depends on it.
- **NFR-06 Documentation**: README quick-start, API docstrings, one runnable
  real-mode example and one sim-mode example.

## 7. Acceptance criteria (v1 done =)
1. Sim: full pytest suite (config, homing, self-check, moves, queuing, limits,
   faults, concurrency, e-stop) passes on a machine with no DLL.
2. Real: scripted acceptance run on the lab machine — connect → self_check on
   all wired axes → moves at configured speed/accel/decel → queued moves →
   jog → stop → estop → recover — completes with expected log/report artifacts,
   including measured e-stop dispatch time.
3. The usage sketch in §3 works verbatim in both modes.
4. Swapping real↔sim is one argument, zero other code changes.
5. Bench tests answered and documented: limit-hit-during-move behavior
   (FR-HOME-07), busy-axis return value, per-call DLL round-trip latency
   (informs max polling rate).

## 8. Milestones
1. **M1 — Contract & sim**: package scaffold (`libs/fmc-motion/`), finalized
   API, config schema, `SimMotionController` with kinematics + virtual limits +
   fault injection, full test suite.
2. **M2 — Real backend**: complete typed ctypes binding (argtypes/restype,
   `machine_status` struct), `FMC006GE31MotionController` passing the same
   interface test suite against sim-verified expectations.
3. **M3 — Hardware bring-up**: bench tests (§7.5), homing, range discovery,
   soft limits, queue behavior, e-stop timing on real hardware; acceptance run.
4. **M4 — Packaging & docs**: examples, README, `printer_msu` adapter, tag v1.

## 9. Resolved decisions (log)
| # | Question | Decision |
|---|---|---|
| Q1 | Limit switches | Both ends on every actuator → range discovery as specified |
| Q2 | Units | DLL native mm / mm/s / mm/s²; no library-side scaling |
| Q3 | Busy-axis policy | **Queue** (library-side per-axis FIFO; DLL rejects busy commands) |
| Q4 | Coordinated moves | v1 independent axes only; interpolation deferred behind `capabilities()` |
| Q5 | Multiple controllers | Not near-term; single controller in v1, `device_id` kept in model |
| Q6 | Continuous jog | DLL has none (manual confirms `Jog_Single_Axis` is position-bounded p2p) → emulate via bounded move + stop |
| Q7 | Packaging | Standalone package `fmc_motion` in `libs/fmc-motion/`, no UI deps |

## 10. Remaining open items (bench tests, not blockers)
- **B1**: What does the controller do when a limit switch trips mid-move —
  hard stop, decel stop, or nothing? (Drives FR-HOME-07 / FR-STOP-04 details.)
- **B2**: Exact busy-axis behavior: does `Jog_Single_Axis` on a moving axis
  return −10, or 0 with no action?
- **B3**: DLL call round-trip latency over TCP → sets safe status-polling rate
  and queue-dispatch poll interval.
- **B4**: `Pause_Run`/`Resume_Run` exist in the header but are undocumented in
  the manual — verify whether they work for single-axis moves (would improve
  pause behavior for the future printer app).
