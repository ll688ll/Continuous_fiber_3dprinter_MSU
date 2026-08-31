## Continuous fiber 3d printer

**Status (2026-08-25): the motion controller has been replaced.** The
FMC006GE31 is retired. X/Y/Z_T0 now run on a **Duet 3 Mainboard 6XD** driving
the same Delta ASD-B3 servo drives over the same wiring — no drive-side changes
were needed. All three axes home and move, the configuration lives on the
board's SD card and survives a power cycle, and the reference Cura file runs
end to end as a mid-air dry run in **2148 s (35m 48s)** against the FMC's
modelled 4778 s. Both required operating modes are proven: preload-and-run from
the card, and line-by-line streaming from a master machine (a 1.2 % penalty).
Wiring, drive parameters, measurements and the traps found along the way:
[`firmware/duet3_6xd/README.md`](firmware/duet3_6xd/README.md).

**Not yet done.** `printer_msu` still targets the FMC and has not been ported
to the Duet HTTP API — that is the next milestone. There is no e-stop chain and
no hardware maximum limits (soft limits only, by decision), and the drive fault
lines are unwired, so a servo alarm is currently invisible to the firmware. The
tool controller is still open; a **Duet 3 Toolboard 1LC** on CAN-FD is the
chosen route, which puts extruder sync inside the motion planner.

**M6 / Auto Run is closed** — it was an FMC-specific workaround for a controller
with no look-ahead, and is not needed on RepRapFirmware. Do not re-propose it.

Customized multi-axis 3D printer control software (desktop) for a multi-module platform:

- **XY** base motion for printing.
- Multiple **tool modules** selected via G-code `T0`, `T1`, … (one active at a time).
- v1 target: **Windows** host; motion on a **Duet 3 Mainboard 6XD**
  (RepRapFirmware) driving external Delta ASD-B3 servo drives, with a **Duet 3
  Toolboard** over CAN-FD for the extruder and heater.
  *Superseded 2026-08-24:* FMC006GE31 + a separate Marlin-class tool controller.

### Repository layout (matches the SRS architecture)

- **`docs/`**: specifications and design docs
  - `docs/Software_SRS.md`
  - `docs/PRD_Motion_Control_Library.md`
- **`firmware/duet3_6xd/`**: the live machine. Configuration that runs *on* the
  controller (`config.g`, `homeall.g`, `homex/y/z.g` — mirrored on its SD card)
  plus the host-side tools: `duet_send.py` (G-code over USB), `stream_gcode.py`
  (HTTP streaming with flow control), `cura_to_msu.py` (slicer G-code → machine
  G-code). Its README carries the wiring, Delta drive parameters, measured
  performance and the silent-failure traps.
- **`libs/fmc-motion/`**: standalone motion control library (`fmc_motion`) for
  FMC006GE31 6-axis controllers — real + sim backends, homing/self-check,
  per-axis queues. Install with `pip install -e libs/fmc-motion[dev]`; test
  with `pytest libs/fmc-motion/tests`.
  **The hardware it drives is retired**; the library is finished and
  hardware-validated, and is kept because `printer_msu` and its sim tests still
  run on it pending the Duet port.
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

### Working with the Duet

The board is at **192.168.0.40** (static — the lab segment has no DHCP server;
see the firmware README). Duet Web Control is at <http://192.168.0.40>.

```bash
python firmware/duet3_6xd/cura_to_msu.py sliced.gcode job.gcode
```

```bash
python firmware/duet3_6xd/stream_gcode.py 192.168.0.40 job.gcode
```

To preload instead of streaming, upload with `rr_upload` and run with `M32`;
`duet_send.py` does the same over USB when the network is unavailable.

### Notes
- The FMC006GE31 demo script is in `examples/fmc006ge31_basic_motion.py`
  (Windows-only). Historical — that controller is retired.

