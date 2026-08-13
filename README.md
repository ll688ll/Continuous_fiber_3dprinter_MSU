## Continuous fiber 3d printer

Customized multi-axis 3D printer control software (desktop) for a multi-module platform:

- **XY** base motion for printing.
- Multiple **tool modules** selected via G-code `T0`, `T1`, … (one active at a time).
- v1 target: **Windows**, FMC006GE31 motion controller(s) + a separate **Tool Controller** (thermocouple heater + fan + extruder).

### Repository layout (matches the SRS architecture)

- **`docs/`**: specifications and design docs
  - `docs/Software_SRS.md`
  - `docs/PRD_Motion_Control_Library.md`
- **`libs/fmc-motion/`**: standalone motion control library (`fmc_motion`) for
  FMC006GE31 6-axis controllers — real + sim backends, homing/self-check,
  per-axis queues. Install with `pip install -e libs/fmc-motion[dev]`; test
  with `pytest libs/fmc-motion/tests`.
- **`src/printer_msu/`**: application source (Python package)
  - `ui/`: PySide6 UI layer
  - `core/`: G-code parsing/interpreting, job state machine, routing
  - `services/`: PrintService/ToolService/StatusService/etc.
  - `controllers/`: HAL interfaces + controller plugins
    - `motion/fmc006ge31/vendor/`: vendor DLL/headers/libs
- **`examples/`**: standalone scripts (hardware demos)
- **`tests/`**: unit tests (module-by-module where possible)

### Quick start (dev)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e libs/fmc-motion[dev]
pip install -e .[ui,dev]
```

Validate and print a G-code file (motion-only v1, sim mode — no hardware
needed):

```bash
printer-msu check examples/square_20mm.gcode --profile configs/printer_sim.json
printer-msu print examples/square_20mm.gcode --profile configs/printer_sim.json
```

On the lab machine, use `--profile configs/printer_msu_lab.json` (real mode,
XY only). `python -m printer_msu ...` works identically. The PySide6 print
console (requires the `[ui]` extra) opens with

```bash
printer-msu ui --profile configs/printer_sim.json examples/square_20mm.gcode
```

and offers connect, check, start/pause/resume/stop/e-stop, live axis status,
and a log tail. Design docs: `docs/PRD_Printer_App.md` (printer core) and
`docs/PRD_Motion_Control_Library.md` (motion library).

The PySide6 UI is an optional extra (`.[ui]`); the core and the motion
library install without it. The printer core reaches the motion hardware via
`printer_msu.controllers.motion.fmc_motion_adapter.FmcMotionAdapter`, which
wraps the `fmc_motion` library (real or sim) — coordinated straight-line XY
moves included (library v1.1 `move_line`).

### Tests

```bash
pytest            # printer core + motion library (152 tests, sim only)
```

### Notes
- The FMC006GE31 demo script is in `examples/fmc006ge31_basic_motion.py` (Windows-only).

