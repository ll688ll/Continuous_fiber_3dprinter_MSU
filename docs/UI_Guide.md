# printer_msu — Operator Guide (UI + CLI)

For the MSU continuous-fiber printer on the Duet 3 6XD. Current as of
2026-09-01 (commit `1e12db6`). Requirements/design live in
`PRD_Printer_App_v2.md`; machine wiring in `../firmware/duet3_6xd/README.md`.

## Starting the app

```
printer-msu ui --profile configs/duet_lab.json
```

The profile decides everything (machine, mode); there is no mode switch in
the UI. `configs/duet_lab.json` is the real machine, `configs/printer_sim_v2.json`
is the simulator. Three tabs: **Print**, **Manual**, **Setup**.

## Print tab

1. **Load** a profile (pre-filled if given on the command line) → **Connect**.
   Connecting runs a **drift check** (board config vs profile); a mismatch is
   an error — fix the profile or re-apply it, never assume.
2. **Open** a G-code file. The **capability panel** fills immediately (before
   Connect): anything the file needs that the machine lacks — heater,
   extruder, fan — is listed, with what will happen (skipped = dry run).
3. **Check** runs the full preflight (parse, feeds, envelope vs soft limits,
   homing coverage) → PASS/FAIL with findings in the log.
4. **Start** — with *Home first* checked the job begins with homing. Progress,
   current line, and per-axis positions update live. **Pause/Resume/Stop**
   map to board-native job control (preload mode: M25/M24/M25+M0).
5. **E-STOP** (red) halts the board (M112). **Clear E-Stop** sends M999 —
   the board **reboots (~23 s)** and **loses its homed state**: re-home
   before any motion.

Execution mode: preload (default) uploads the job to the board's SD and runs
it there — nothing on the host can disturb it. Streaming
(`policies.execution: "stream"` or CLI `--execution stream`, ≈ +0.4 %) keeps
the host in the loop; use it only when that is the point.

Every job writes a report, `job-*.json` plus a `.log` twin, to `logs/` under
the app folder (the repository root from a checkout; `PRINTER_MSU_LOG_DIR`
overrides it). The Print tab log shows the path when a job starts.
Contents: board-measured duration, profile snapshot, skipped commands, end
reason.

## Manual tab

Per-axis rows: live position, homed state, **±10/±1/±0.1 mm jog**, absolute
**go-to** (type a target, Enter or Go), **Home**. Top row: **jog feed**
(clamped to each axis's max), **Home all** (tool-Z first, so the tool clears
the plane before X/Y move), and an always-live **E-STOP** mirror.

Rules the tab enforces (not just suggests):
- One manual operation at a time; while one runs, the Print tab's
  Start/Check/Connect/Clear-E-Stop are locked too (and vice versa during a
  job or its preflight). Only E-STOP stays clickable.
- Targets outside the soft limits are refused before anything is sent
  (RRF would silently clamp them, not reject — the app makes it loud).
- Jogging an unhomed axis is refused by the firmware; the message appears in
  the status strip. Home first.
- Positions are **machine coordinates** (Z zero at the TOP; positive Z moves
  the tool DOWN toward the plane).

## Setup tab

The profile JSON is the **single source of truth** for the machine
(decision D6). The tables edit it; **Save profile** validates everything and
writes atomically; **Preview board config** shows the files that would be
generated; **Apply to board…** uploads them (CRC-verified):
- `/sys/msu_machine.g` — axes, scales, limits, endstops, tools. Takes effect
  after a reset (M999).
- `homeall.g` + `home<letter>.g` — homing macros, generated in tool-Z-first
  order. Take effect immediately.

Never hand-edit those files on the card; edit the profile and re-apply.
Every connect cross-checks the board against the profile.

### Adding an axis (e.g. a second tool Z)
1. Wire the drive (CN1 low-speed pulse, same Delta recipe as X/Y/Z) and its
   NPN home sensor — **io6/io7/io8 are free; NEVER io0.in** (shared with the
   UART; the validator rejects it).
2. **Add axis** — the template picks the next free driver channel and an
   extra-axis letter (**U/V/W** — a second independent Z must not reuse `Z`,
   RRF would gang the motors). Fix name, endstop pin, limits.
3. Add/point a tool at it (**Add tool**, set its Z axis cell).
4. **Save** → **Apply to board** → reset → home → **Calibrate plane…**.

### Adding the extruder
1. Select the tool row → **Hardware…** — the dialog pre-fills an extruder
   template (driver channel, steps/mm, speeds) as JSON. Edit, OK, **Save**,
   **Apply**.
2. The moment an extruder exists in the profile, E-words in G-code are
   executed instead of skipped — cold-test and calibrate steps/mm before
   printing anything real.
3. The **heater** section works the same way but is **hardware-gated**: no
   heater in the applied config until the drive-fault (ALRM) wiring and the
   e-stop chain exist (see `TODO.md` §1). The generator always emits the
   `M143` temperature ceiling.

### Calibrate plane…
Select a tool row → jog its Z down (±1/±0.1/±0.01) until the nozzle touches
the print plane → **Capture** → **Save profile**. The captured machine-Z
becomes that tool's `plane_offset_mm`; slicer Z maps as
`machine_z = plane_offset − gcode_z`.

## CLI equivalents

```
printer-msu check  <file> --profile <p>              # preflight only
printer-msu print  <file> --profile <p> [--home-first] [--execution stream]
printer-msu apply-config --profile <p> [--dry-run]   # board files from profile
printer-msu calibrate plane --profile <p> --tool 0
```

Exit codes: 0 ok, 1 failed, 2 preflight refused, 3 usage.

## When something looks wrong

- **"profile missing required key: 'motion'"** → stale editable install;
  check `python -c "import printer_msu; print(printer_msu.__file__)"` points
  at this checkout's `src/`.
- **Drift check errors on connect** → board and profile disagree; `apply-config`
  (then M999) if the profile is right, or fix the profile if the board is.
- **Board unreachable** → it is static **192.168.0.40** on an isolated
  segment with **no DHCP**; also check the USB Ethernet dongle is present
  (it vanishes from the adapter list when it drops).
- **After Clear E-Stop nothing is homed** → correct: M999 reboots the board
  and datums are lost. Re-home.
- Job reports and logs: `logs/`; hardware measurement records:
  `docs/test-records/`.
