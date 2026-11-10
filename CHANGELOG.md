# Changelog

All notable changes to this repository. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Two packages live here and are
versioned separately: `printer_msu` (the application) and `fmc-motion` (the
FMC006GE31 motion library, kept as the simulator backend).

## [Unreleased]

## printer_msu 1.1.0 - 2026-11-09

### Added
- **USB serial connection mode** for the Duet, as an alternative to
  Ethernet. Set `"transport": "usb"` in the Duet controller's `connection`
  block (`"port": "auto"` finds the board by its USB ID, or name a COM port),
  pick **USB serial** in the new Link row of the Print tab, or pass
  `--transport usb` / `--port COM3` to any machine command. Everything works
  over either link: connect with the drift check, status, homing, jogging,
  e-stop and M999 recovery, preload and stream printing, and Setup's Apply.
  The override is for the session only; the profile file is not changed.
- `printer-msu ports` lists serial ports and marks the Duet.
- `usb` extra (`pyserial`); the Windows bundle includes it.

### How USB differs from Ethernet
- Each line waits for the board's `ok`, which is the flow control, so there
  is no buffer accounting. Motion is never re-sent after a link failure.
- Status reads use `M409` and reopen the port if the USB device
  re-enumerated. M999 always re-enumerates it; the app reconnects.
- E-stop (`M112`) is written immediately, never queued behind a running
  command such as homing.
- Uploads (preload jobs, board config) use `M28`/`M29` and are verified with
  an `M38` SHA-1 check. Comments are stripped first, so files written over
  USB carry no comments. No file line is sent until the board confirms it
  is writing a file. `rr_download` has no USB equivalent.

### Known limitations
- The USB transport is verified against a simulated board only. The bench
  checklist in `docs/TODO.md` (section 4) must pass on the real 6XD before
  printing over USB.
- The safety limitations of 1.0.0 are unchanged: no e-stop chain, drive
  fault lines unwired, soft limits only, heater power hard-gated.
- USB on the lab workstation has dropped out before. Prefer Ethernet for
  long prints, and preload over stream: a preloaded job keeps running on
  the board if the cable drops.

## printer_msu 1.0.0 - 2026-09-01

First tagged release. Target machine: the MSU continuous-fiber printer on a
Duet 3 Mainboard 6XD (RepRapFirmware 3.6.1) driving Delta ASD-B3 servo drives
on X, Y and Z_T0. Windows host.

### Added
- Duet 3 backend over HTTP (`rr_gcode`, `rr_upload`, `rr_model`): connect-time
  drift check of the board configuration against the profile, host-side
  soft-limit refusal (RRF `M564 S1` clamps silently instead of rejecting),
  M112 halt and M999 recovery handling.
- Machine profiles, schema v2: several controllers per machine, axes with
  limits and endstops, tools with a per-tool Z axis and print-plane offset.
  Schema v1 (FMC) profiles migrate automatically.
- Board configuration generated from the profile: the `msu_machine.g`
  include plus `homeall.g` and per-axis homing macros (`apply-config`, Setup
  tab), CRC-verified on upload.
- Two execution modes, both verified on hardware through the app on the
  reference Cura file: preload (default, 1964 s) and stream (1972.5 s).
- Capability-gated tool functions (extruder, heater, fan) and an offline
  capability preflight that lists what a file needs and the machine lacks.
- Command line: `check`, `print`, `apply-config`, `calibrate plane`, `ui`.
- PySide6 desktop console with Print, Manual (per-axis jog, go-to, home,
  interlocked against running jobs) and Setup (axis and tool editing, board
  config apply, plane calibration) tabs. Operator guide: `docs/UI_Guide.md`.
- Report directory anchored to the app: job reports (`job-*.json`) and
  their `.log` twins go to `logs/` under the app folder (repository root
  from a checkout, the bundle folder when packaged) regardless of the
  working directory. `PRINTER_MSU_LOG_DIR` overrides it; `--report` names a
  single report explicitly. A read-only app folder falls back to the
  per-user state directory with a warning.
- MIT license (`LICENSE`, copy in `libs/fmc-motion/`).
- Release tooling under `packaging/`: PyInstaller spec (two executables,
  one shared runtime), build driver with manifest, smoke test and zip;
  GitHub Actions workflow (tests on Windows, draft release on `v*` tags).
- `constraints.txt` pinning the tested environment (PySide6 6.11.1, pytest
  9.1.1 on Python 3.13).

### Changed
- `fmc-motion` is no longer a hard dependency of `printer_msu`. It is the
  `sim` extra, pinned to 1.1.1; the Duet path never imports it.
- Package versions are read from `__version__` (one source of truth).

### Removed
- The unused in-app FMC006GE31 plugin (`controllers/motion/fmc006ge31/`) and
  its second copy of the vendor DLL. `libs/fmc-motion` is the only FMC path.
- `FMCG6A.py`, the root shim for the retired controller's demo script.

### Known limitations (read before operating)
- No e-stop chain. `M112` is a firmware halt, not a hardware power cut.
- Drive fault lines (`Dn_ERR` to Delta `ALRM`) are not wired. A servo alarm
  is invisible to the firmware.
- Soft limits only, no maximum-end sensors. They hold only while an axis is
  homed; `G1 H2` raw moves bypass them.
- Heater power is hard-gated on the items above. Tool bring-up (extruder,
  thermocouple, PID) is not part of this release.

## fmc-motion 1.1.1 - 2026-08-12

Hardware-validated final version of the FMC006GE31 library; the controller
was retired on 2026-08-25. The package version now matches the documented
v1.1.1 milestone (it was 0.1.0 in `pyproject.toml` and 0.2.1 in code).
