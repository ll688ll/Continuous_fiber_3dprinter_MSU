# PRD — Printer Application v2 ("printer_msu" on the Duet 3 6XD)

| | |
|---|---|
| Status | **Implemented (software) 2026-08-30** — V2-M1..M6 landed, all sim/fake-board verified; hardware shakeout pending (see §6) |
| Date | 2026-08-30 |
| Owner | Jacky Lyu |
| Related | `PRD_Printer_App.md` (v1, frozen record), `PRD_Motion_Control_Library.md` (FMC library, finished), `firmware/duet3_6xd/README.md` (the live machine), `docs/TODO.md` |

## 1. Scope — Jacky's five requirements (2026-08-30)

1. **Configuration**: assign every axis/channel to a controller with its
   parameters (X → Duet driver 0, max speed/accel, min endstop io3; Z2 → FMC
   ch 0; Extruder 1 → Duet driver 3; thermocouple settings).
2. **Duet tool functions**: extruder + heater/thermocouple (+fan). FMC stays
   motion-only.
3. **UI setup section**.
4. **Home-all**; tool-Z homes UP → per-tool calibratable print-plane offset.
5. **G-code capability review** after load: report missing functions/axes.

Decisions (locked): **D-V2-1** schema multi-controller, Duet-only driven this
phase; **D-V2-2** tool hardware on the 6XD mainboard (thermocouple
daughterboard — SRS D8 kept; no CAN toolboard); **D-V2-3** PySide6 UI
extension; **D-V2-4** core-first sequencing.

New decisions made during implementation:
- **D5 — arcs are capability-gated** (supersedes D4's blanket rejection,
  which stands verbatim for the FMC): G2/G3 are accepted when the motion
  controller executes them natively (RRF) and `policies.arcs` allows.
  Helical/relative/full-circle arcs stay rejected.
- **D6 — the profile is the source of truth for board config**: the app
  generates `/sys/msu_machine.g` (single `M584` first — it clears `M574` and
  datums; mm/min conversions; endstops; tool block with `M143` ceiling);
  hand-maintained `config.g` keeps network/driver-timing/safety and includes
  it via `M98`. Upload only on explicit *apply-config*; **every connect runs
  a drift check** (profile vs live `move.axes`) surfaced through preflight
  (`policies.config_drift`).
- **D7 — preload (Mode B) is the default execution**; streaming (Mode A)
  is for host-in-the-loop work (`policies.execution` / `--execution`).

## 2. What was built (by milestone, all on `main`)

- **V2-M1 — profile schema v2** (`config/profile.py`): `controllers[]`
  (duet | fmc_motion), `axes[]` (controller, channel, letter, steps/mm,
  speed/accel/jerk maxima, limits, endstop pin — `io0.in` hard-rejected,
  `homes_up`), `tools[]` (z_axis, **plane_offset_mm**, extruder, heater
  incl. thermocouple sensor, fan). v1 auto-migrates; `to_dict()` emits
  canonical v2; atomic writer `config/profile_store.py`. New configs:
  `configs/duet_lab.json` (the real machine), `configs/printer_sim_v2.json`.
- **V2-M2 — Duet backend**: `controllers/motion/duet/client.py`
  (productionized `stream_gcode.Duet`: opener seam, retries on idempotent
  reads only, `send()` refuses payloads over live `buff`, CRC-verified
  `rr_upload`); `adapter.py` (wide-HAL over the object model; bare-G28
  home-all = board's tool-Z-first macros; error-reply checking; M112/M999
  with reconnect; **connect-time drift check**); `config/duet_config_gen.py`;
  CLI `apply-config` and `calibrate plane`; the ABC now carries the full
  wide contract.
- **V2-M3 — execution split**: `core/gcode/emitter.py` (actions → board
  G-code with byte-offset→source-line map + final-target tracking);
  `services/duet_execution.py` — `DuetFileExecutor` (upload → `M32` →
  **verify reply AND `job.file.fileName`** → progress from
  `job.filePosition` → **end-of-job position check** → duration from
  `job.lastDuration`) and `DuetStreamExecutor` (byte-batched `rr_gcode`
  feed). FMC per-move path extracted verbatim (`_run_action_stream`),
  behavior identical. Job report gains `execution`/`board_duration_s`/
  `host_duration_s`.
- **V2-M4 — tool functions**: capability-driven interpreter dispatch
  (M104/M109 → `SetToolTemp` with `max_temp_c` ceiling as a FATAL check;
  M106/M107 → `SetFanSpeed`; E words → **relative-normalized `e_delta`
  riding the planner-synced XY component**); `DuetToolController`
  (temp/fan/manual extrude/emergency shutdown; status from `heat.heaters`);
  `full_status()` grows a `tools` block. Profiles without tool hardware
  (all FMC profiles) behave exactly as v1.
- **V2-M5 — capability preflight**: `_check_capability_gaps` runs FIRST and
  fully offline ("file extrudes 4x E words but no extruder is configured —
  motion will run WITHOUT extrusion"); `UsageReport` gains
  needs_heater/needs_fan/extrudes/reports_temp; arcs per D5 with a
  conservative endpoint±radius envelope band.
- **V2-M6 — UI**: tabbed console (Print | Setup); Setup page edits
  controllers/axes/tools in tables with full-validator round-trip saves,
  Preview/Apply board config, per-tool **plane calibration dialog**
  (jog ±1/±0.1/±0.01, Capture); capability panel shows the gap scan the
  moment a file opens, before Connect.

Tests: **191 printer + 83 fmc-motion**, all green. The Duet path runs
headless in CI against `tests/duet_testlib.ScriptedDuetBoard`, which
reproduces the board's real failure modes (silent buffer overflow drop,
rr_reply-only refusals, M999 reboot semantics, ghost-job start).

## 3. Traps encoded as tests (do not relearn these)

`rr_gcode` silently drops overflow (batch by bytes); single `M584` first;
never `io0.in`/`M575`; static IP stays hand-maintained; `G1 H2` bypasses
M208; RRF wants mm/min (M203/M566/F); verify `M32` reply AND
`job.file.fileName`; durations from the board; `clear_estop`=M999=reboot
(homed state lost); no per-move verification behind 60-move look-ahead
(upload CRC + run verification + end-of-job position check instead);
E rides the XY planner line; heater hardware-gated; **RRF M564 S1 CLAMPS
out-of-envelope moves silently** (hardware-confirmed 2026-08-30) — the loud
refusal lives host-side in the adapter, the board's clamp is the backstop.

## 4. Hardware validation still owed (next lab visit)

1. ~~`shakeout_adapter.py`~~ **RUN 2026-08-30** (record:
   `docs/test-records/2026-08-30/shakeout_adapter_first_run.json`):
   drift CLEAN, home-all 16.6 s, jog square exact, M112 halt + M999
   recovery 22.4 s with homed state correctly lost. **Finding: RRF
   `M564 S1` silently CLAMPS out-of-envelope targets to M208 instead of
   rejecting them** (the axis traveled to X=987.448 with no error — same
   family as the FMC silent-ignore). Fixed same day: host-side soft-limit
   refusal in `DuetMotionAdapter._check_soft_limits` (absolute + relative
   jogs), fake board now clamps like the real one, 3 regression tests.
   **Re-run the script once to see step 4 pass.**
2. ~~One-time `config.g` edit~~ **DONE 2026-08-30** via
   `firmware/duet3_6xd/migrate_config_include.py`: card config.g verified
   against git HEAD, backed up (`/sys/config.g.premsu.bak` + test-records),
   generated `/sys/msu_machine.g` (784 B) uploaded first, include-style
   config.g (4336 B) second, both byte-verified via the new
   `DuetClient.download`; M999 reboot (23.5 s) and the **connect drift check
   read CLEAN** — the profile now drives the board config (D6 realized).
   Also: second shakeout run all-6-steps PASS (envelope refused host-side).
3. **Mode B DONE 2026-08-31** (job-20260831-220120, record in
   `docs/test-records/2026-08-31/`): first app-driven print COMPLETED —
   10,043 emitted lines uploaded, run verified, end-of-job position check
   passed, **board_duration 1964 s** (incl. the in-file G28) vs the 2148 s
   raw-board reference. The −8.6 % is EXPLAINED, not suspicious: the
   reference run kept the file's `M204 P500` (cura_to_msu), while the v2
   interpreter drops M204, so the app print ran at the machine's validated
   M201=1000 mm/s² — accel is the documented lever. **1964 s is the
   app-path baseline going forward.** Open design choice: honour M204 as
   modal accel (slower, obeys the file) vs keep machine accel (current).
   `--execution stream` run still pending (expect ≈ +1.2 %).
4. `printer-msu calibrate plane --tool 0` dry run (mid-air capture).
5. Tool bring-up, gated (V2-M4 §hardware): cold extruder jog + steps/mm →
   thermocouple bench sanity → **HARD GATE: `Dn_ERR`→ALRM wiring
   meter-verified (Delta ALRM is a b-contact — conducts when healthy; RRF
   pull-up expects fault-LOW) + V4 e-stop chain** → M303 PID tune → first
   heated extrusion.

## 5. Open items beyond this phase

- Fiber process design doc (cutter, feed sync, toolpath generation) — the
  60-move look-ahead constraint shapes it; extruder-as-planner-axis was the
  architectural preparation.
- Multi-controller live driving (schema ready; runtime refuses per D-V2-1).
- `cura_to_msu.py` limits should source from a profile (deprecation note in
  its header); most of its job is now `emit_machine_gcode`.
- Push `main` to `origin/main` (still local-only — Jacky's call).
