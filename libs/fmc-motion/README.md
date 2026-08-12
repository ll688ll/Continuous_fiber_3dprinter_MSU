# fmc-motion

Standalone multi-axis motion control library for **FMC006GE31** 6-axis
controllers, with identical **real** (hardware) and **sim** (simulation)
backends. This is the "center control module" of the MSU continuous-fiber 3D
printer project — but it has no printer or UI dependencies and can drive any
set of linear actuators on this controller family.

See `../../docs/PRD_Motion_Control_Library.md` for the full requirements.

## Features

- Up to 6 independent axes: absolute/relative moves, emulated jog, per-axis
  command **queue** (FIFO — the vendor DLL rejects commands to a busy axis).
- **Homing & self-check**: home against a limit switch, then automatically
  discover the usable working range from the far switch and derive soft limits.
- Per-axis speed / acceleration / deceleration defaults with per-move overrides,
  persisted to a JSON/YAML config file.
- Safety: soft limits, stop / e-stop (all-axis immediate stop + latched
  lockout), limit-trip faults, normalized vendor error codes.
- `pause(axis)` / `resume(axis)` of in-flight moves (vendor
  `Pause_Run`/`Resume_Run`, validated on hardware).
- **Sim mode**: trapezoidal motion kinematics, virtual limit switches, fault
  injection, time acceleration — the full test suite and the complete
  self-check routine run on any OS with no hardware and no DLL.

## Install (dev)

```bash
pip install -e libs/fmc-motion[dev]
```

## Quick start

```python
from fmc_motion import create_controller

with create_controller("machine.json", mode="sim") as ctrl:   # or mode="real"
    report = ctrl.self_check()          # home + find working range per axis
    print(report.to_json())
    ctrl.configure_axis(0, speed=100, accel=500, decel=500)
    ctrl.move_absolute(0, 25.0)         # blocking; wait=False for async
    print(ctrl.get_status().positions)
```

Minimal config (JSON):

```json
{
  "connection": {"device_id": 1, "ip": "192.168.0.30", "port": 8088},
  "axes": [
    {"index": 0, "name": "X",
     "homing": {"direction": "negative", "speed": 20, "fall_step": 2},
     "defaults": {"speed": 100, "accel": 500, "decel": 500}}
  ],
  "sim": {"time_scale": 20, "axes": {"0": {"travel_mm": 200}}}
}
```

## Modes

| | real | sim |
|---|---|---|
| Platform | Windows (vendor DLL, 32/64-bit auto-selected) | any |
| Backend | `FMC006GE31Controller` (ctypes) | `SimMotionController` |
| Status | **hardware-validated** (2-axis acceptance run passed 2026-08-11; see `../../docs/test-records/`) | fully tested |

## Tests

```bash
pytest libs/fmc-motion/tests
```

## Layout

- `src/fmc_motion/interface.py` — public contract (`MotionController`, status
  and report types, `MoveHandle`)
- `src/fmc_motion/config.py` — config schema + validation + JSON/YAML I/O
- `src/fmc_motion/_engine.py` — shared engine: queues, safety, homing,
  self-check (both backends inherit this, so behavior is identical)
- `src/fmc_motion/sim.py` — simulation backend + fault injection
- `src/fmc_motion/fmc006ge31.py` — hardware backend (ctypes)
- `src/fmc_motion/vendor/` — vendor DLL/header/lib (win32 + win64)
