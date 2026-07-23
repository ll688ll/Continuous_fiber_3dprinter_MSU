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
pip install -e .[dev]
python -m printer_msu
```

### Notes
- The FMC006GE31 demo script is in `examples/fmc006ge31_basic_motion.py` (Windows-only).

