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

### 4.5 Preflight (FR-PRN-002 subset for motion-only)
- **FR-PRE-01**: checks — controller connected; e-stop not active; parse
  produced no errors; no policy-fatal commands (G2/G3, strict-mode tool
  commands, G20); every referenced tool exists in the profile; every logical
  axis the file moves is mapped (or `missing_axis: skip` policy); axes homed
  or `require_homed: false` acknowledged; feeds within axis maxima.
- **FR-PRE-02**: preflight returns a structured report (pass/fail + itemized
  findings); `print` refuses to start on failure; `check` prints it.

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
  all mapped axes before streaming when the file lacks G28.

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
4. **M4 — minimal PySide6 print console**: open file, check, start/pause/
   resume/stop/e-stop, progress + status, log tail; Qt bridge over FR-JOB-05
   events.
5. **M5+ (deferred)**: tool controller integration (pending SRS Q1 hardware),
   `ACT_MOVE_LINEAR_SYNC`, arc segmentation, resume-from-line, profiles UI.

## 9. Open items
- SRS Q1: tool controller hardware selection (blocks M5).
- Bench **B-L4 only** (B-L1..B-L3 answered 2026-08-12, see §10). Runner:
  `libs/fmc-motion/tools/hw_line_bench.py --with-limit` (answer `n` to the
  earlier phases to jump straight to it).
- Enable mid-line pause in the library (unlocked by B-L3, §10) — optional;
  the printer's between-segment pause remains the default behavior.

## 10. M3 bench + first-print results (lab run 2026-08-12)

Records: `docs/test-records/2026-08-12/` (`line_bench_results.json`,
`first_real_print_job.json`). Runner: `tools/hw_line_bench.py`.

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
- **B-L4 — outstanding** (limit trip mid-line; optional, `--with-limit`).
  Until run, the library's own limit monitoring (stop both axes on any trip)
  is the guarantee; single-axis bench B1 (2026-08-11) showed the controller
  hardware-stops with 0.000 mm overrun, so the risk is bounded.
- **First real print PASSED:** `examples/square_20mm.gcode` on the lab XY
  stage via `printer-msu print --profile configs/printer_msu_lab.json`
  (job-20260812-212131): homed from the file's `G28 X Y`, six coordinated
  line segments, tool commands skipped per D2, COMPLETED with report.
  Acceptance §7.6 is met pending B-L4 for formal M3 sign-off.
- Lab maintenance carry-over from the library PRD: raise X's Home Timeout in
  the vendor tool; physical mm-scaling spot check per actuator.
