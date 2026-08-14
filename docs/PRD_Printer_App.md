# PRD — Printer Application v1 ("printer_msu")

| | |
|---|---|
| Status | Draft v0.1 (scope questions resolved 2026-08-11) |
| Date | 2026-08-11 |
| Owner | Jacky Lyu |
| Related docs | `docs/Software_SRS.md` (full system), `docs/COMMAND_LIST.md` (v1 command checklist), `docs/PRD_Motion_Control_Library.md` (motion HAL, finished) |

## 1. Overview

### 1.1 Problem statement
The motion control library (`fmc_motion`) is finished and hardware-proven, but
nothing prints yet: there is no path from a G-code file to coordinated motion.
The SRS specifies the full system (UI, tool controllers, multi-module tool
changes); this PRD cuts the first useful vertical slice through it.

### 1.2 Product vision (v1)
A **headless, sim-first print core** with a small CLI: load a slicer-generated
G-code file, parse and validate it, interpret it into a normalized action
stream, and stream **motion only** to the machine through the existing
`FmcMotionAdapter` — in sim mode on any machine, and on the real XY stage in
the lab. The PySide6 UI and the tool controller (heater/fan/extruder) plug in
later behind interfaces this core already defines.

### 1.3 Scope decisions (resolved with Jacky, 2026-08-11)
| # | Question | Decision |
|---|---|---|
| D1 | Coordinated XY motion | **Pull `Line_2Axis` into `fmc_motion` first** (library v1.1, milestone M1 of this PRD). The printer core streams true interpolated XY lines; software-synchronized independent moves are not used. |
| D2 | Tool control in v1 | **Motion only.** Tool/temperature/fan/extrusion commands (`M104/M109/M105/M106/M107`, `E` words) are parsed and **skipped with warnings** by default (strict mode: preflight error), so unmodified slicer files still run their motion. |
| D3 | UI timing | **CLI first.** v1 is headless (`printer-check` / `printer-print` style CLI); a minimal PySide6 print console is milestone M4, reusing the proven core through observer events. |
| D4 | G-code source / arcs | **Standard slicer, G1-only.** `G2/G3` are recognized by the parser but rejected at preflight in v1 (arc segmentation deferred). |

### 1.4 Relationship to the existing repo
- `libs/fmc-motion` — consumed through its public API; extended (not forked)
  with coordinated line moves in M1. Its PRD gains a "v1.1" section.
- `src/printer_msu/controllers/motion/fmc_motion_adapter.py` — the only place
  the printer touches motion; upgraded to use `move_line` for multi-axis moves.
- `src/printer_msu/core/gcode/{parser,interpreter}.py` — skeleton stubs grow
  into the real parser/interpreter specified here.
- `src/printer_msu/controllers/tool/base_tool_controller.py` — kept as the
  contract for the future tool controller; **not implemented** in v1 (D2).
- `configs/msu_lab.json` — the real lab machine (X ≈ 283 mm, Y ≈ 85 mm, no Z
  wired). Sim profiles add a virtual Z so tool-Z routing is testable.

## 2. Goals and non-goals

### 2.1 Goals (v1)
1. **Library v1.1 — coordinated line moves**: `move_line({axis: pos}, speed,
   accel, decel)` in `fmc_motion` (sim + real backend), integrated with the
   existing per-axis queues, soft limits, stop/e-stop, and faults.
2. **G-code pipeline**: file → parse (with line numbers + diagnostics) →
   validate → analyze usage (tools, axis words) → interpret into a normalized
   action stream with tool-scoped Z routing (`Tn` selects which physical axis
   `Z` means).
3. **Print job execution**: preflight, worker-thread streaming, pause / resume
   / stop / e-stop, progress + state events, error propagation from the
   controller into a terminal job state.
4. **Machine profile** (JSON): axis map (logical name → controller axis
   index), per-tool Z mapping, dialect settings, policies. Validated at load.
5. **CLI**: `check` (parse/validate/analyze a file against a profile) and
   `print` (stream it, sim or real) with console progress; exit codes usable
   in scripts.
6. **Observability**: structured logs correlated by job id + source line; a
   job report (JSON) with outcome, timings, warnings, last confirmed line.
7. **Sim-first**: the full pytest suite (library line moves + printer core)
   runs on any OS with no hardware and no PySide6.

### 2.2 Non-goals (v1)
- Tool hardware: heaters, thermocouples, fans, extruders (D2; SRS Q1 hardware
  still TBD). The interpreter *recognizes* those commands; execution is
  deferred. `ACT_MOVE_LINEAR_SYNC` (motion+extrusion) is deferred with it.
- Arc execution (`G2/G3`) — parse + reject at preflight (D4).
- PySide6 UI — M4, after the core is proven (D3).
- Lookahead / junction blending: each G1 segment is one `Line_2Axis` call and
  decelerates to zero at segment end. Acceptable for fiber research prints;
  revisit only if print quality demands it.
- 3-axis interpolated moves (`Line_3Axis`): a G-code move touching X, Y, and Z
  together is split (Z first, then XY line) with a warning. Slicers rarely
  emit these outside vase mode (out of scope per SRS D9).
- Multi-controller routing, tool-change macros, resume-from-line, work
  offsets (`G92 X/Y/Z`), inch units at execution time (`G20` parses; preflight
  rejects in v1 — the lab uses mm exclusively).

## 3. Users and use cases

| User | Use case |
|---|---|
| Jacky / lab operator | `printer-msu print part.gcode --profile configs/printer_sim.json` and watch it run in sim before booking lab time. |
| Lab bring-up | Same command with the lab profile (`mode: real`) drives the XY stage through a real toolpath, no extrusion. |
| Future UI (M4) | Imports `PrintService`, subscribes to its events, renders progress. |
| Future tool integration | Implements `BaseToolController`; the interpreter already emits tool actions to route to it. |

### Primary usage sketch (acceptance target)
```text
$ python -m printer_msu check examples/square.gcode --profile configs/printer_sim.json
parsed 812 lines: 0 errors, 3 warnings (tool commands skipped: M104 x1, M109 x1, M106 x1)
tools used: T0   axes used: X Y Z E   motion segments: 640
preflight: PASS

$ python -m printer_msu print examples/square.gcode --profile configs/printer_sim.json
[job 20260811-231502] homing X Y Z_T0 ... done
[job 20260811-231502] printing: 640/640 segments (100%) line 812
[job 20260811-231502] COMPLETED in 41.2 s — report: logs/job-20260811-231502.json
```

## 4. Functional requirements

### 4.1 Library extension — coordinated line moves (`fmc_motion` v1.1)
Implemented inside `libs/fmc-motion` following its own architecture (engine
`_ll_*` primitives implemented by both backends).

- **FR-LINE-01**: `move_line(targets: Mapping[int, float], *, speed=None,
  accel=None, decel=None, wait=True, timeout=None) -> MoveHandle` — absolute
  targets on **exactly 2 axes** (v1.1), executed as one interpolated straight
  line (vendor `Line_2Axis`). `speed/accel/decel` are **composite (path)**
  values; defaults = the minimum of the participating axes' configured
  defaults; validated against the minimum of their maxima.
- **FR-LINE-02 (queueing)**: a line move occupies **both** axes' FIFO queues
  and dispatches only when both axes reach it idle. Single-axis commands
  queued behind it wait; `stop`/`estop`/`clear_queue` on **either** axis
  cancels it (stop means stop). One shared `MoveHandle` reports the whole
  line.
- **FR-LINE-03 (safety)**: soft limits validated per axis at enqueue and at
  dispatch; both axes must be homed (or `allow_unhomed`); a limit switch trip
  on either axis mid-line stops **both** axes immediately and faults the
  tripped axis; e-stop and hardware-estop behave exactly as for single-axis
  moves.
- **FR-LINE-04 (pause)**: `pause(axis)` on an axis running a line move
  **raises** (same as homing/creep) until vendor pause semantics on
  interpolated motion are bench-validated (B-L3). The printer pauses between
  segments instead, which is hardware-safe.
- **FR-LINE-05 (sim parity)**: the sim backend executes line moves with
  per-axis kinematic profiles scaled from the composite values
  (`v_i = v·|d_i|/L`), so both axes start/finish together and the traced path
  is straight; busy rejection (−10), history recording, fault injection, and
  time scaling apply as for single-axis moves.
- **FR-LINE-06**: `capabilities()` reports `"interpolation": True` and
  `"line_axes": 2`. Consumers must gate on this (COMMAND_LIST §3.5).
- **FR-LINE-07 (bench items, real hardware — M3)**:
  - **B-L1**: endpoint semantics — the header says the move starts "from the
    current point"; the backend assumes `endX/endY` are **absolute** machine
    coordinates (axis-index order: lower index → `endX`). Verify.
  - **B-L2**: composite speed/accel interpretation and busy (−10) behavior on
    a line move.
  - **B-L3**: `Pause_Run`/`Resume_Run` on an interpolated move (unlocks
    FR-LINE-04).
  - **B-L4**: limit-trip mid-line — does the controller stop both axes itself
    (single-axis bench B1 says yes for one axis)?

### 4.2 Machine profile
- **FR-PROF-01**: JSON profile with: `profile_name`; `motion` (path to the
  `fmc_motion` config, `mode` sim/real, `axis_map` logical→index); `tools`
  (list of `{tool_index, z_axis}`, `z_axis` a logical name or null);
  `gcode` (dialect policies, §4.4); `policies` (unknown command warn/error,
  missing-axis skip/error, require-homed).
- **FR-PROF-02**: validation at load fails fast: duplicate logical names or
  indices, `z_axis` not in `axis_map`, duplicate tool indices, unknown policy
  values. (Travel/feed limits live in the `fmc_motion` config — single source
  of truth; the profile does not duplicate them.)
- **FR-PROF-03**: the profile is echoed into the job report (§4.6) so a run
  is reproducible.

### 4.3 G-code parsing (file level, stateless)
- **FR-PARSE-01**: parse a file (or line iterable) into `GcodeLine` records:
  original text, 1-based line number, word map, comment, and per-line
  diagnostics; never raises on malformed input (NFR-REL-002).
- **FR-PARSE-02**: diagnostics detected at parse time: invalid numeric field,
  duplicate word letter on a line, `G`/`M`/`T` with a non-integer code, empty
  command with axis words (e.g. `X10` with no G/M/T and no active modal group
  — flagged for the interpreter).
- **FR-PARSE-03**: comments `;...` and `(...)` stripped and preserved;
  case-insensitive words; whitespace-tolerant (`G1X10Y5`).
- **FR-PARSE-04**: usage analysis over a parsed file: set of `Tn` referenced,
  axis words used (X/Y/Z/E), count of motion segments, commands seen with
  counts — the basis of `check` output and preflight (FR-GC-020..024).

### 4.4 Interpretation (stateful, profile-aware)
- **FR-INT-01**: modal state per SRS: units (`G20/G21`), distance mode
  (`G90/G91`), extruder mode (`M82/M83` — tracked, execution deferred),
  active tool (`Tn`), current feed (`F`, **mm/min in G-code**, converted to
  mm/s for the HAL), current logical position.
- **FR-INT-02**: emitted actions (each carrying its source line number):
  `MoveLinear(axes_mm, feed_mm_s)`, `Home(axes)`, `SelectTool(n)`,
  `Pause(reason)` (`M0/M1`), `End` (`M2/M30`), `SetPosition` (`G92 E` only),
  and `SkippedCommand(reason)` records for D2-deferred commands.
- **FR-INT-03 (tool-scoped Z)**: `Z` words route to the active tool's mapped
  z-axis (`Z_T0`, …) per the profile; `Tn` switches the routing (FR-PRN-051).
  `Z`/`E` before any `Tn` uses the profile's `default_tool` (with a warning)
  or errors, per policy (FR-GC-023).
- **FR-INT-04 (motion assembly)**: `G0/G1` produce one `MoveLinear` per
  segment. XY together → one coordinated 2-axis move; a move also touching Z
  is split Z-first (+warning, §2.2). `G0` uses the profile's travel feed (or
  axis maxima), `G1` uses modal `F`. Zero-length segments are dropped.
- **FR-INT-05 (motion-only policy, D2)**: `M104/M109/M105/M106/M107` and `E`
  words produce `SkippedCommand` warnings (aggregated by code in reports); in
  `strict` policy they are preflight errors. `G92 E0` is accepted silently
  (tracks virtual E so files with relative-E logic stay consistent).
- **FR-INT-06 (dialect extension)**: `G28 [X][Y][Z]` homes the named logical
  axes (no words = all mapped axes incl. active tool Z). Slicer start-code
  needs it even though the SRS list omitted it.
- **FR-INT-07**: unknown/unsupported commands follow the profile policy:
  `warn` (default; skipped, counted) or `error` (preflight fails). `G2/G3`
  are always preflight errors in v1 (D4), with a message naming the line.
  A `T` word is a tool change **only standalone**; on G/M lines it is a
  parameter (Cura's `M204 T500` travel accel must not select T500).
- **FR-INT-08 (workspace transforms — added 2026-08-13 from the first
  realistic-slicer-file trial)**: the profile's `workspace` section maps
  slicer coordinates into the machine frame: `xy_offset` shifts absolute
  X/Y targets (slicer bed origin → machine envelope); `z_mode: "bed"`
  treats G-code Z as height above the print plane on this top-zero machine
  (`machine_z = z_bed_offset_mm − gcode_z`; relative Z deltas negate).
  Default `z_mode: "machine"` passes coordinates through raw. The real
  machine's `z_bed_offset_mm` awaits Q3 calibration.

### 4.5 Preflight (FR-PRN-002 subset for motion-only)
- **FR-PRE-01**: checks — controller connected; e-stop not active; parse
  produced no errors; no policy-fatal commands (G2/G3, strict-mode tool
  commands, G20); every referenced tool exists in the profile; every logical
  axis the file moves is mapped (or `missing_axis: skip` policy); axes homed
  or `require_homed: false` acknowledged; feeds within axis maxima.
- **FR-PRE-02**: preflight returns a structured report (pass/fail + itemized
  findings); `print` refuses to start on failure; `check` prints it.
- **FR-PRE-03 (envelope check — added 2026-08-13)**: preflight walks the
  machine-frame move stream tracking per-axis position (absolute targets +
  accumulated relative deltas) and errors on any target outside the
  discovered soft limits, per axis with counts and first offending line.
  Axes without discovered limits produce an info finding; relative moves
  before any absolute position are counted as unchecked.

### 4.6 Print job execution
- **FR-JOB-01**: job state machine: `IDLE → READY → RUNNING ⇄ PAUSED →
  COMPLETED | STOPPED | ERROR` (adds `COMPLETED` to the skeleton's states).
- **FR-JOB-02**: streaming runs in a dedicated worker thread; actions execute
  sequentially and blocking (one segment = one `move_line`/`move_absolute`
  wait). The UI thread rule (SRS 2.5) is satisfied by construction — the CLI
  or future UI only ever observes events.
- **FR-JOB-03**: `pause()` takes effect **between actions** (current segment
  finishes, then the worker holds; hardware-safe per FR-LINE-04); `resume()`
  continues; `stop()` aborts the current motion via controller `stop()` and
  flushes; `estop()` calls controller `estop()` and latches ERROR-adjacent
  STOPPED state. `M0/M1` actions enter the same PAUSED state.
- **FR-JOB-04**: controller errors (`LimitHitError`, `MoveTimeoutError`,
  disconnects, …) abort the job into `ERROR` with the failing source line and
  the normalized error preserved.
- **FR-JOB-05 (events)**: observers subscribe to: state changes, progress
  (action index / total, source line, percent), warnings, and errors.
  Callbacks are invoked from the worker thread and must be cheap; the future
  Qt bridge marshals them to signals.
- **FR-JOB-06 (report)**: every run writes a JSON job report: job id, file
  path + SHA-256, profile snapshot, mode, start/end times, end reason, last
  executed line, skipped-command tallies, warnings, error (if any).
- **FR-JOB-07**: homing policy: `print` runs `Home` actions from G28 in the
  file; `--home-first` CLI flag (or profile `home_before_print: true`) homes
  all mapped axes before streaming when the file lacks G28. Homing order
  (SRS D11, added 2026-08-13): **tool Z axes first** — every axis homes to
  its negative limit, and for Z that is physically up, so Z clears the
  print plane before X/Y travel — then the remaining axes.

### 4.7 CLI
- **FR-CLI-01**: `python -m printer_msu <cmd>` (and console script
  `printer-msu`): `check <file> --profile <p>` and
  `print <file> --profile <p> [--mode sim|real] [--home-first] [--report <path>]`.
- **FR-CLI-02**: `print` shows line/percent progress on the console, handles
  Ctrl-C as `stop()` (second Ctrl-C = `estop()`), and exits 0 only on
  COMPLETED. `check` exits 0 only if preflight passes.
- **FR-CLI-03**: no new dependencies (argparse + stdlib only); PySide6 stays
  an optional extra.

### 4.8 Logging
- **FR-LOG-01**: standard `logging` under the `printer_msu` namespace; every
  action logs dispatch/completion at DEBUG, lifecycle at INFO, skipped
  commands at WARNING (once per code + count summary), errors with source
  line at ERROR. Job id in every record during a job.
- **FR-LOG-02**: file logging + rotation configured by the CLI (per-job log
  file next to the report); library consumers get no global logging side
  effects.

## 5. Module layout (v1 target)

```
src/printer_msu/
  config/profile.py          # MachineProfile schema + validation (FR-PROF)
  core/gcode/parser.py       # FR-PARSE (grows from skeleton)
  core/gcode/interpreter.py  # FR-INT: GcodeLine stream -> Action stream
  core/gcode/analysis.py     # FR-PARSE-04 usage analysis
  core/model/actions.py      # normalized Action dataclasses (FR-INT-02)
  core/model/print_job.py    # job state machine (FR-JOB-01)
  core/routing/tool_router.py# tool -> z-axis routing (FR-INT-03)
  services/print_service.py  # preflight + worker + events (FR-PRE, FR-JOB)
  controllers/motion/fmc_motion_adapter.py  # + move_line support
  cli.py                     # FR-CLI
```

Exceptions: `PrinterError` (base) → `ProfileError`, `GcodeError` (carries line
number), `PreflightError`, `JobError`.

## 6. Non-functional requirements
- **NFR-01 Testability**: parser, interpreter, profile, preflight, job state
  machine, and streaming are unit-testable with the sim backend; no PySide6,
  no hardware, no network in the test suite.
- **NFR-02 Safety**: stop/e-stop preempt streaming (worker aborts within one
  poll interval); no motion is ever dispatched outside the HAL; soft limits
  remain the library's job (single enforcement point).
- **NFR-03 Responsiveness**: progress events at least once per action;
  pause/stop acknowledged in the job state within one segment.
- **NFR-04 Portability**: core + CLI run on Windows/macOS/Linux in sim mode;
  real mode is Windows (library constraint).
- **NFR-05 Compatibility**: no breaking change to `fmc_motion`'s existing
  public API; v1.1 is additive (existing 60 library tests keep passing).

## 7. Acceptance criteria (v1 done =)
1. **Library**: full `libs/fmc-motion/tests` suite passes with the new line
   tests — queued line+single mixes, straightness (mid-move XY position stays
   on the segment within tolerance), simultaneous finish, soft limits on both
   axes, stop/estop/limit-trip mid-line, busy retry, capability report.
2. **Printer sim run**: a real slicer G-code file (with heater/fan commands
   and `G92 E0`) streams to COMPLETED in sim via CLI; the job report tallies
   skipped tool commands; final sim positions match the file's last XY.
3. Pause/resume/stop/e-stop each verified mid-print in sim, including `M0`.
4. Preflight rejects: G2/G3 file, unknown tool index, unmapped axis (error
   policy), unhomed axes (when required), G20 file.
5. `check` and `print` exit codes correct; usage sketch in §3 works verbatim.
6. **Hardware (M3, lab)**: XY toolpath file (no Z/E) prints on the lab stage
   in real mode; bench items B-L1..B-L4 answered and recorded in this PRD.

## 8. Milestones
1. **M1 — `fmc_motion` v1.1 line interpolation (sim-first)**: engine group
   command + barrier dispatch, sim kinematics, real-backend `Line_2Axis`
   binding (bench-pending), tests, library PRD/README/version update.
2. **M2 — print core + CLI (sim)**: profile, parser+analysis, interpreter,
   actions, preflight, print service + events, job report, CLI, adapter
   `move_line` upgrade, full test suite. Acceptance §7.1–7.5.
3. **M3 — hardware validation (lab)**: bench B-L1..B-L4 on the real stage,
   then a real XY toolpath run; record artifacts under `docs/test-records/`.
4. **M4 — minimal PySide6 print console** *(DONE 2026-08-12, sim-verified)*:
   `printer-msu ui [--profile P] [file]` — load profile / connect, open +
   check a file, start/pause/resume/stop/e-stop with Home-first and Clear
   E-Stop, progress bar + live axis table + log tail. Structure:
   `ui/bridge.py` (JobBridge marshals FR-JOB-05 worker events to queued Qt
   signals; StatusPoller + TaskRunner keep all controller I/O off the UI
   thread per UI-010) and `ui/print_console.py` (window). Tested offscreen
   against the sim backend (`tests/test_ui_console.py`); skipped when
   PySide6 (the `[ui]` extra) is absent. Lab (real-mode) shakeout pending.
5. **M5+ (deferred)**: tool controller integration (pending SRS Q1 hardware),
   `ACT_MOVE_LINEAR_SYNC`, arc segmentation, resume-from-line, profiles UI.

## 9. Open items
- SRS Q1: tool controller hardware selection (blocks M5) — options drafted
  in `docs/Tool_Controller_Options_Q1.md`; **decision D-Q1a (2026-08-13):
  the FMC is motion-only — channels reserved for X, Y, Z_T0/Z_T1/Z_T2 —
  and the extruder lives on the future tool controller (per SRS D7), so
  extrusion + the Q2 sync design are deferred together.** Board choice
  (recommendation: Marlin-class board for heat/TC/fan/E) still pending
  Jacky's inputs on heater spec, extruder motor, fiber cutter, budget.
- ~~Re-run X/Y self-check after the lead correction~~ **DONE 2026-08-13
  21:47 — true-scale spans discovered and persisted: X 987.448 mm, Y
  290.269 mm** (soft limits [2, 985.4] / [2, 288.3]; move error ≤
  0.004 mm; e-stop 59.5 ms; records in `docs/test-records/2026-08-13/`).
  En route, a wrong-way homing incident (axes drove to the POSITIVE end
  on commanded-negative homing after the vendor parameter edit) was
  resolved at the vendor tool; `tools/hw_direction_check.py` then verified
  polarity NORMAL on all three axes. X's vendor Home Timeout raised to
  30000. Y clamp headroom is ~10 mm (span 290.3 vs ±300) — adequate.
  Probe finding: commanding motion INTO an active limit switch returns
  rc=0 with zero motion (silent refusal, same family as the line-move
  behavior).
- **Z/XY frame transforms: IMPLEMENTED 2026-08-13 (FR-INT-08), calibration
  pending.** The workspace section handles `machine_z = z_bed_offset −
  gcode_z` and XY bed-origin offsets; validated end-to-end against a real
  Cura file (Ender-5 profile, 11k lines) in sim. Remaining for the real
  machine: measure the actual `z_bed_offset_mm` (and per-tool refinement)
  — SRS Q3 calibration. Until then the lab profile stays `z_mode:
  "machine"`; a deliberately small offset (e.g. 100) allows a safe mid-air
  dry run of sliced files high above the plane.

- Enable mid-line pause in the library (unlocked by B-L3, §10) — optional;
  the printer's between-segment pause remains the default behavior.
- Minor: B-L4 trip-point bookkeeping — the switch region showed ~0.5 mm
  run-to-run variance (85.0 vs 85.4 mm) and the bit can release before the
  next ~150 ms poll; harmless (soft limits keep 2 mm clearance) but worth a
  re-measure when the remaining axes are wired.

## 10. M3 bench + first-print results (lab run 2026-08-12)

Records: `docs/test-records/2026-08-12/` (`line_bench_results.json`,
`retracted_no_motion_print_job.json`, `first_verified_print_job.json`).
Runner: `tools/hw_line_bench.py`.

- **B-L1 — RESOLVED: endpoints are ABSOLUTE machine coordinates**, in
  ascending-axis order (`endX` = lower axis index). From (20, 20) commanding
  (40, 35), the machine finished at exactly (40.000, 35.000). The backend's
  assumption stands as implemented.
- **B-L2 — RESOLVED: composite (path) speed, Marlin-equivalent semantics.**
  Commanded 15 mm/s over a 25 mm path (Δ 20/15 mm): measured per-axis peaks
  **11.99 / 8.99 mm/s** vs the composite prediction 12.0 / 9.0 (= v·Δᵢ/D);
  axes moved simultaneously; **max path deviation 0.004 mm**. Feed = toolhead
  speed along the path, exactly like Marlin's planner.
- **B-L2b — busy behavior:** `Line_2Axis` during a line returns **−10**; the
  library's queue + retry design applies unchanged to lines.
- **B-L3 — RESOLVED: Pause_Run/Resume_Run WORK on an interpolated move**
  (mask = both axes): rc 0/0, position held while paused, resumed and reached
  the original target. FR-LINE-04's refusal can be relaxed in a future
  library rev; not yet implemented (printer pauses between segments, which
  remains correct and safe).
- **B-L4 — RESOLVED: the controller stops BOTH axes, coordinated, on its
  own.** Shallow line (40, 35) → (70, 120) at 5 mm/s composite: both axes
  stopped at the SAME path fraction (X 0.587, Y 0.588 — on the line, no
  diagonal drag) with Y at 84.954 mm, i.e. at the positive-switch region,
  with no host-side stop issued. The limit bit was not captured by the
  ~150 ms status polls and the stop sat ~0.5 mm before the prior day's
  measured trip point — attributed to early mechanical trip + switch
  hysteresis releasing the bit before the next poll (wiring itself was
  proven by the 2026-08-11 hand-press + B1 tests). Library-side monitoring
  stays as belt-and-braces; printable envelope (soft limits, 2 mm margin)
  clears the observed trip zone by >1.5 mm.
- **First real print — RETRACTED (silent no-motion defect).** The job
  (job-20260812-212131) reported COMPLETED, but forensics show the machine
  never moved: every `Line_2Axis` was dispatched rc=0 yet each segment took
  exactly the grace+settle floor (~2.04 s, no motion time) and positions
  never changed; the operator confirmed no visible motion beyond homing.
  Prime suspect: the print fires its first line straight from the post-home
  position at the home switch, which stays engaged past its trip point
  (hysteresis) — the raw bench that DID move had jogged single-axis to
  (20, 20) first. Under investigation via
  `tools/hw_lib_line_check.py` (library-path repro: post-home limit bits,
  line-from-home vs line-when-clear).
- **Hardening added (2026-08-12):** the print worker now verifies the
  reported position against the commanded target after every move
  (`POSITION_TOLERANCE_MM = 0.25`) and fails the job with a clear error if
  the controller silently ignored a command — COMPLETED now means the
  toolpath physically happened. Moves also log at INFO (visible in the UI
  log tail).

- **Root cause + fix (same day):** `tools/hw_lib_line_check.py` confirmed
  the controller **silently ignores interpolated moves while a limit switch
  is active**, and the home switch stays engaged at the post-home rest
  position (fall_step 2 mm < hysteresis). fmc-motion v0.2.1 fixes:
  `homing.clear_offset_mm = 5.0` post-home clearance in both machine
  configs, engine success-path target verification (0.5 mm), sim
  `inject_silent_ignore()` parity. Records: `lib_line_check_results.json`,
  `retracted_no_motion_print_job.json`.
- **First VERIFIED real print PASSED (2026-08-12, job-20260812-231448):**
  `square_20mm.gcode` on the lab XY stage — homing + switch clearance,
  six coordinated line segments each position-verified (runner 0.25 mm +
  engine 0.5 mm checks active), COMPLETED. Record:
  `first_verified_print_job.json`.

**M3 is signed off** (2026-08-12, second pass): all bench items B-L1..B-L4
answered, the silent-ignore defect found/fixed/regression-tested, and a
real toolpath printed with position verification proving physical motion.

## 11. Z-axis bring-up results (lab, night of 2026-08-12→13)

T0's Z (controller axis 2, `Z_T0`) is fully commissioned; records in
`docs/test-records/2026-08-13/`.

- Limit switches verified on **bit 2** (hand-press, `limit_monitor.py`).
- **Controller Z+ soft limit raised 200 → 300 mm in the vendor tool**
  (Jacky): the actuator's span is ~282 mm, above the ±200 default that
  would have clamped it (pre-raise snapshot:
  `device_para_pre_limit_raise.json`).
- **3-axis acceptance PASSED** (`hw_acceptance_report_3axis.json`,
  reconstructed from console after an aborted re-run overwrote the file):
  spans X 283.146 / Y 85.390 / **Z 282.306 mm** (X/Y within 0.03 mm of the
  2026-08-11 values), move error 0.001 mm on all axes, queued moves and jog
  clean, **e-stop dispatch 59.9 ms across 3 axes** (budget 500 ms), config
  soft limits persisted via the new `hw_acceptance.py --save`.
- **Two three-axis two-layer prints COMPLETED position-verified**
  (`two_layer_square.gcode`, jobs 20260813-000059 / -000355): Z routed
  through T0, layer changes 10.0 → 10.4 → 15 mm lift, all XY lines
  interpolated. Record: `first_three_axis_print_job.json`.
- Incidental robustness proof: the first print homed and ran cleanly right
  after an acceptance re-run was Ctrl-C'd mid-creep — the −10 busy-retry
  path recovered without operator intervention.
- **Home Timeout concern CLOSED:** Z's homing in this run started from the
  positive limit — ~282 mm at 15 mm/s ≈ 19 s — and succeeded with the
  stock `home_timeout: 10000`. The unit is therefore not milliseconds (the
  2026-08-11 "raise the timeout" advice assumed ms and was a false alarm);
  `device_para_check` now only warns when a timeout is too small under
  every unit interpretation.
- **Z orientation confirmed intentional:** homing (machine zero) is at the
  physical TOP, far from the print plane — Jacky's design choice. See §9
  for the resulting slicer-Z transform requirement.

The lab machine is now a validated 3-axis (X/Y/Z_T0) motion platform;
remaining hardware work is the tool controller (SRS Q1, §9).
