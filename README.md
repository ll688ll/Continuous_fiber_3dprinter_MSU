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

**Update (2026-09-01): the Duet port is HARDWARE-PROVEN.** `printer_msu` v2
drives the Duet over HTTP: multi-controller machine profiles, generated board
config + homing macros with connect-time drift checking, preload/stream job
execution (**both modes verified through the app**: 1964 s / 1972.5 s on the
reference Cura file — the app-path baselines), capability-gated tool
functions, an offline capability preflight, and a three-tab UI — **Print,
Manual (per-axis jog/go-to/home), Setup (axis/tool editing, add-axis and
add-extruder, board config apply, plane calibration — captured: T0 at
94.4 mm)**. Docs: **[`docs/UI_Guide.md`](docs/UI_Guide.md)** (how to operate
it), `docs/PRD_Printer_App_v2.md` (what was built and why). Remaining
hardware work is the safety-gated tool bring-up (PRD v2 §4).

**Safety state unchanged:** no e-stop chain, no hardware maximum limits (soft
limits only, by decision), drive fault lines unwired — a servo alarm is still
invisible to the firmware. These remain priority 1 in `docs/TODO.md` and gate
any heater power. **Tool hardware decision (D-V2-2, 2026-08-30):** everything
on the 6XD mainboard — extruder on a spare driver, heater on a mainboard
output, thermocouple via daughterboard (no CAN toolboard this phase).

**M6 / Auto Run is closed** — it was an FMC-specific workaround for a controller
with no look-ahead, and is not needed on RepRapFirmware. Do not re-propose it.

**Picking this up again?** Start at [`docs/TODO.md`](docs/TODO.md) — the
outstanding work in priority order, with the traps that go with each item.

Customized multi-axis 3D printer control software (desktop) for a multi-module platform:

- **XY** base motion for printing.
- Multiple **tool modules** selected via G-code `T0`, `T1`, … (one active at a time).
- target: **Windows** host; motion on a **Duet 3 Mainboard 6XD**
  (RepRapFirmware) driving external Delta ASD-B3 servo drives; extruder/
  heater/thermocouple on the same mainboard (D-V2-2).
  *Superseded:* FMC006GE31 (2026-08-24) and the CAN toolboard route
  (2026-08-30).

### Repository layout (matches the SRS architecture)

- **`docs/`**: specifications and design docs
  - `docs/UI_Guide.md` — operator guide (UI tabs, CLI, workflows)
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
- **`examples/`**: standalone scripts (hardware demos)
- **`tests/`**: unit tests (module-by-module where possible)

### Quick start (dev)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e libs/fmc-motion[dev] -c constraints.txt
pip install -e .[ui,dev] -c constraints.txt
```

`constraints.txt` pins the tested versions (PySide6 6.11.1, pytest 9.1 on
Python 3.13). `fmc-motion` is only needed by the simulator profiles and the
sim tests; on a wheel install it is the `sim` extra
(`pip install "printer_msu[ui,sim]" --find-links <folder with both wheels>`).

Validate and print a G-code file (motion-only v1, sim mode — no hardware
needed):

```bash
printer-msu check examples/square_20mm.gcode --profile configs/printer_sim.json
printer-msu print examples/square_20mm.gcode --profile configs/printer_sim.json
```

On the lab machine use `--profile configs/duet_lab.json` — the Duet profile
(schema v2). Useful v2 commands: `printer-msu apply-config --profile
configs/duet_lab.json --dry-run` previews the generated board config;
`printer-msu calibrate plane --profile configs/duet_lab.json --tool 0` jogs
and captures a tool's print-plane offset; `printer-msu print ...
--execution stream` picks streamed execution over the preload default.
The old FMC profile `configs/printer_msu_lab.json` still loads (auto-migrated).
`python -m printer_msu ...` works identically. The PySide6 print
console (requires the `[ui]` extra) opens with

```bash
printer-msu ui --profile configs/printer_sim.json examples/square_20mm.gcode
```

and offers connect, check, start/pause/resume/stop/e-stop, live axis status,
and a log tail. Design docs: `docs/PRD_Printer_App_v2.md` (current), `docs/PRD_Printer_App.md`
(v1 record) and `docs/PRD_Motion_Control_Library.md` (motion library).

Job reports (`job-*.json` plus a `.log` twin) go to `logs/` under the app:
the repository root from a checkout, the bundle folder when packaged. The
location does not depend on the working directory. `PRINTER_MSU_LOG_DIR`
overrides it, and `printer-msu print --report <path>` names one report
explicitly. Both the CLI and the Print tab print the path when a job starts.

The PySide6 UI is an optional extra (`.[ui]`); the core and the motion
library install without it. The printer core reaches the motion hardware via
`printer_msu.controllers.motion.fmc_motion_adapter.FmcMotionAdapter`, which
wraps the `fmc_motion` library (real or sim) — coordinated straight-line XY
moves included (library v1.1 `move_line`).

### Tests

```bash
pytest            # printer core + motion library (301 tests; Duet paths run against an in-process fake board)
```

### Packaging a release

`packaging/` holds the PyInstaller spec and the build driver. One command
runs the tests, builds both wheels, freezes the one-dir Windows bundle
(`printer-msu.exe` console + `printer-msu-ui.exe` windowed), adds configs,
board macros, docs and a `RELEASE_MANIFEST.json` with SHA-256 hashes, smoke
tests the frozen executables and zips the result into `dist/`:

```bash
pip install -e ".[ui,sim,dev,build]" -c constraints.txt
python packaging/build_release.py
```

The procedure around it (verification on the machine, tagging, the GitHub
Release) is in [`packaging/README.md`](packaging/README.md). CI runs the
suite on Windows for every push and opens a draft release for `v*` tags.

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

### License

MIT, see [`LICENSE`](LICENSE). Third-party material keeps its own terms and is
not covered by it: the FMC006GE31 vendor DLL, header and import library under
`libs/fmc-motion/src/fmc_motion/vendor/` and the vendor documents under
`docs/`, whose redistribution terms should be confirmed before publishing
them; and the RepRapFirmware configuration under `firmware/duet3_6xd/`, which
targets Duet3D's firmware.
