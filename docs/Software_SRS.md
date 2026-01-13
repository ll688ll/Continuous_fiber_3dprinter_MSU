## Software Requirements Specification (SRS)

## 1. Introduction

### 1.1 Purpose
This document specifies the requirements for a **customized multi-axis 3D printer control program** (“the System”). The System provides:

- A desktop UI (PySide6 / Qt) for hardware configuration, manual control, G-code loading/inspection, and print execution.
- A backend that parses G-code into motion/tool commands and streams them to supported motion controllers and Tool Controllers via controller-agnostic interfaces.
- Logging of key behaviors and errors for diagnostics and traceability.

### 1.2 Scope
The machine consists of:

- **XY platform**: the base XY motion used for printing on the build plane.
- **Multiple tool modules**: configurable count; each module provides its own **Z axis** plus process hardware (e.g., **extruder + heater + fan** for FDM). The active module is selected by G-code tool change commands (`T0`, `T1`, ...).

The System focuses on **motion + job execution**, and in v1 explicitly includes **extruder + heater + fan control** for FDM modules via a **separate Tool Controller** (type TBD) using **thermocouple** temperature sensing.

### 1.3 Definitions, Acronyms, and Abbreviations
- **Motion Axis**: a controllable motion channel (e.g., X, Y, and per-tool Z) mapped to a motion controller axis index.
- **Tool Channel**: a controllable tool channel (e.g., extruder drive, heater, fan) mapped to a Tool Controller channel identifier.
- **Controller**: hardware motion controller/driver + its communication API (TCP/Serial/USB/etc.).
- **HAL** (Hardware Abstraction Layer): software interface that decouples printer logic from controller specifics.
- **G-code**: CNC/3D-printer instruction language (dialect is configurable in this System).
- **Job**: a print run associated with one G-code file and a chosen hardware profile.
- **E-Stop**: emergency stop (hardware and/or software).
- **Tool Module**: a printable-process module selected by `Tn`. In v1, an FDM module includes a Z motion axis, an extruder motor, a heater (thermocouple-sensed), and a fan.
- **Tool Controller**: a (typically separate) controller responsible for tool hardware such as heaters, thermocouple temperature sensing, fans, and extruder control.
- **Extruder axis (E)**: the filament feed channel associated with the active tool module (implemented via a Tool Controller channel in v1).

### 1.4 References
- FMC006GE31 controller header `src/printer_msu/controllers/motion/fmc006ge31/vendor/FMC006GE31.h` (vendor API example present in repository).

### 1.5 Document Overview
Sections cover system overview, functional requirements, external interfaces, non-functional requirements, and appendices describing architecture and data models.

## 2. Overall Description

### 2.1 Product Perspective
The System is a desktop application that connects to one or more motion controllers and Tool Controllers and provides:

- Machine configuration (axes, limits, homing, controller mappings)
- Manual motion controls
- G-code viewing + parsing + axis-usage analysis
- Print execution by streaming motion commands in real time

### 2.2 Product Functions (High-Level)
- Configure machine hardware (XY + N tool modules, limits, mappings).
- Connect/disconnect to controller(s) and verify readiness.
- Manual controls: home, jog/move, stop.
- Load G-code, display text, analyze axis usage, validate syntax.
- Execute print: start, pause, resume, stop; monitor position/status; show progress.
- Log key events, errors, and controller communications.
- Control tool modules: set temperatures, monitor heaters (thermocouple), control fans, and execute extrusion during printing.

### 2.3 User Classes and Characteristics
- **Operator**: runs jobs, loads files, uses manual controls; needs clear status and safety.
- **Engineer / Integrator**: configures axes/controller plugins, tunes limits, debugs logs.
- **Developer**: adds new controller plugins or G-code dialect extensions.

### 2.4 Operating Environment
- Desktop OS: **Windows** (v1 target).
  - Note: the current controller integration is expected to use a vendor Windows `.dll` (via Python `ctypes`) for FMC006GE31-type controllers.
- Language/runtime: Python 3.x
- UI toolkit: PySide6 (Qt)

### 2.5 Design and Implementation Constraints
- UI must use PySide6/Qt.
- Backend must be controller-agnostic via an explicit interface contract.
- Print streaming must run in an independent worker thread from the UI thread.
- Safety-critical actions (Stop/E-Stop) must have highest priority and preempt queued motion.

### 2.6 Assumptions and Dependencies
- Motion controllers provide, at minimum, basic move + homing + stop + status reporting.
- The G-code dialect uses tool changes (`T0`, `T1`, ...) to select the active **tool module**.
- Within a given tool selection, `Z` and `E` coordinates refer to the active module’s Z motion and extruder feed for that module.
- Only one tool module is active at a time.
- Machine has physical limit switches or reliable soft limits for safe motion.
- Print motion relies on controller interpolation (line/arc) rather than full software motion planning (v1).

## 3. System Features and Functional Requirements

### 3.1 Hardware Configuration & Management

#### 3.1.1 Machine Profile Management
- **FR-HW-001**: The System shall support creating, editing, selecting, and deleting **Machine Profiles**.
- **FR-HW-002**: A Machine Profile shall include:
  - XY axis parameters (travel limits, max feed, acceleration, homing config)
  - Tool module configuration (module count and per-module parameters; mapped from `Tn`)
  - Axis-to-controller mapping (logical axes → controller id + axis index)
  - Controller connection settings (type, address, port, device id)
  - Tool hardware mapping (per-tool extruder + heater + fan channels)
- **FR-HW-003**: The System shall persist profiles to disk in a human-readable format (e.g., JSON/YAML).
- **FR-HW-004**: The System shall validate profile completeness and constraints before saving (e.g., no duplicate axis mapping; travel limits are consistent).

#### 3.1.2 Tool Modules (Z + Extruder + Heater + Fan)
- **FR-HW-010**: The System shall support configuring tool modules addressed by tool indices (`T0`, `T1`, ...).
- **FR-HW-011**: Each tool module shall have:
  - a module type (e.g., “FDM”, “Syringe”, “Laser”, “Custom”)
  - a mapped Z motion axis
  - an extruder configuration (for FDM-type modules) including extruder control mapping (Tool Controller id + channel) and limits
  - heater configuration (for FDM-type modules) including setpoint limits, thermocouple sensor configuration, and controller mapping (Tool Controller id + channel)
  - fan configuration (for FDM-type modules) including controller mapping (Tool Controller id + channel) (optional)
- **FR-HW-012**: The System shall support at least **4** configured tool modules per profile and shall not hard-code an upper maximum; the practical limit is defined by the number/capacity of installed controllers.
- **FR-HW-013**: The System shall allow enabling/disabling tool modules (installed count < configured).

#### 3.1.3 Controller Connection
- **FR-HW-020**: The System shall support connecting to a controller using the selected controller plugin and connection parameters.
- **FR-HW-021**: The System shall display connection status (Connected/Disconnected/Error) and last error details.
- **FR-HW-022**: The System shall support reconnecting after disconnection without restarting the application.

#### 3.1.4 Status Monitoring
- **FR-HW-030**: The System shall periodically poll controller status while connected.
- **FR-HW-031**: Status shall include at minimum: per-axis position, per-axis speed, limit switch status, controller run state, and emergency status (if available).
- **FR-HW-032**: The UI shall display live status values and clearly indicate abnormal states (limit hit, emergency active, controller error).

### 3.2 Manual Motion Controls

#### 3.2.1 Homing
- **FR-MAN-001**: The System shall provide Home controls:
  - Home All
  - Home X, Home Y
  - Home Z (selected tool module)
- **FR-MAN-002**: The System shall prevent homing if an emergency stop is active.
- **FR-MAN-003**: The System shall log homing start/end and results.

#### 3.2.2 Jog / Move
- **FR-MAN-010**: The System shall support manual movement:
  - Move Absolute (X/Y and Z of selected tool module)
  - Move Relative (jog step)
  - Continuous Jog (press-and-hold) (optional)
- **FR-MAN-011**: The System shall enforce soft limits during manual motion.
- **FR-MAN-012**: The System shall allow configuring jog step sizes and jog feed rate.

#### 3.2.3 Stop / Emergency Stop
- **FR-MAN-020**: The System shall provide:
  - **Stop**: controlled deceleration stop (if supported) or immediate stop otherwise.
  - **E-Stop**: highest-priority immediate stop command, also blocks future motion until cleared/reset.
- **FR-MAN-021**: Stop/E-Stop shall preempt print streaming and clear motion queues.

#### 3.2.4 Tool Module Controls (Extruder + Heater + Fan)
- **FR-MAN-030**: The System shall allow selecting an active tool module for manual operations (e.g., via `T0`, `T1`, ... selection in the UI).
- **FR-MAN-031**: The System shall provide manual extruder controls for the selected tool module (for FDM modules), including extrude and retract by distance at a chosen feed rate (e.g., Load/Unload/Purge).
- **FR-MAN-032**: The System shall provide manual heater controls for the selected tool module (for FDM modules), including set target temperature, stop/cooldown, and display current temperature if a sensor is available.
- **FR-MAN-033**: The System shall enforce safety interlocks during manual extrusion (configurable), such as preventing extrusion below a minimum temperature threshold unless explicitly overridden with user confirmation.
- **FR-MAN-034**: The System shall provide manual fan controls for the selected tool module (for FDM modules), including setting fan speed (PWM or percent) and turning the fan off.

### 3.3 G-code File Loading, Viewing, and Analysis

#### 3.3.1 File Loading & Text Display
- **FR-GC-001**: The System shall allow selecting and loading a G-code file from disk.
- **FR-GC-002**: The System shall display the file as plain text with:
  - line numbers
  - basic syntax highlighting (G/M/T words, axis words, comments)
  - search/find
- **FR-GC-003**: The System shall support large files by using incremental loading or efficient text widgets (no UI freeze > 200 ms on typical file operations; see NFR-PERF).

#### 3.3.2 Parsing and Validation
- **FR-GC-010**: The System shall parse the file into a structured internal representation:
  - command type (e.g., G0/G1/G2/G3/M/T)
  - parameters (X/Y/Z/E/…/F/S/I/J/R/etc.)
  - line number + original text + parse errors/warnings
- **FR-GC-011**: The parser shall support at minimum:
  - Units: `G20` (inch), `G21` (mm)
  - Distance mode: `G90` (absolute), `G91` (relative)
  - Extruder distance mode: `M82` (absolute E), `M83` (relative E)
  - Motion: `G0`, `G1`, `G2`, `G3`
  - Set position: `G92` (e.g., `G92 E0`)
  - Program flow: `M0/M1` (pause), `M2/M30` (end)
  - Tool selection: `Tn`
  - Temperature: `M104` (set hotend temp), `M109` (set and wait), `M105` (report temps)
  - Fan: `M106` (fan on / set speed), `M107` (fan off)
  - Comments: `;...` and `( ... )`
- **FR-GC-012**: The System shall detect and report:
  - Unknown/unsupported commands (warning or error by configuration)
  - Invalid numeric formats
  - Missing required parameters for arcs (I/J or R, depending on dialect)

#### 3.3.3 Axis Usage Detection
- **FR-GC-020**: The System shall analyze the loaded file and report which tools (`Tn`) are referenced and which motion words are used (X/Y/Z/E).
- **FR-GC-021**: The UI shall visualize tool usage (e.g., `T0`, `T1`, ...) and optionally highlight lines associated with each tool’s Z/E behavior.
- **FR-GC-022**: The System shall indicate which tool module is active over time based on `Tn` commands.
- **FR-GC-023**: The System shall warn or error (configurable) if the file uses `Z` or `E` before any tool is selected (default tool behavior is configurable, e.g., assume `T0`).
- **FR-GC-024**: The System shall warn or error (configurable) if the file references a tool index that is not configured/enabled in the selected Machine Profile.

### 3.4 Print Job Execution (Streaming)

#### 3.4.1 Preflight and Start Print
- **FR-PRN-001**: The System shall require a connected controller and a selected Machine Profile before starting a print.
- **FR-PRN-002**: The System shall run a preflight check:
  - controller connected and responsive
  - emergency not active
  - machine homed or user-confirmed “start without homing” (configurable)
  - soft limits available
  - G-code parsed without fatal errors
  - all tools referenced by the G-code are configured/enabled in the Machine Profile
- **FR-PRN-003**: The System shall support starting a print from:
  - beginning of file
  - a selected line number (optional, if safe and supported)

#### 3.4.2 Real-Time Streaming Worker
- **FR-PRN-010**: Print execution shall run in an independent worker thread from the UI thread.
- **FR-PRN-011**: The worker shall:
  - read parsed G-code sequentially
  - convert G-code into controller-agnostic motion commands
  - enqueue commands to the controller plugin
  - monitor controller status and handle backpressure (buffer full, busy, etc.)
- **FR-PRN-012**: The worker shall emit UI-safe events (Qt signals) for:
  - current line number
  - progress percentage
  - estimated remaining time (optional)
  - warnings/errors

#### 3.4.3 Motion Command Semantics (Controller-Agnostic)
- **FR-PRN-020**: The printing core shall represent motion using a common data model, e.g.:
  - `MoveLinear` (end positions on one or more axes, feed, accel/decel)
  - `MoveArc` (plane, end, center/radius, direction, feed, accel/decel)
  - `HomeAxis`, `Stop`, `SetFeed`
- **FR-PRN-021**: The printing core shall not depend on any controller-specific API (no vendor calls in printing core).

#### 3.4.4 Pause/Resume/Stop
- **FR-PRN-030**: The System shall support:
  - Pause: stop issuing further motion and command controller to pause/stop safely.
  - Resume: continue from the last confirmed executed line.
  - Stop: terminate the job and clear queued commands.
- **FR-PRN-031**: On pause/resume/stop, the System shall record the event in logs and update UI state immediately.

#### 3.4.5 Error Handling During Print
- **FR-PRN-040**: If the controller reports an error (limit hit, emergency, communication failure), the System shall:
  - stop streaming immediately
  - command Stop/E-Stop (as configured)
  - notify the user with actionable error information
  - persist diagnostics to logs
- **FR-PRN-041**: The System shall support a “safe recovery” workflow (TBD) if the controller supports resuming from a known line/state.

#### 3.4.6 Tool Changes (`Tn`) and Module Activation
- **FR-PRN-050**: The System shall interpret `Tn` commands as selection of the active tool module.
- **FR-PRN-051**: The System shall enforce that only one tool module is active at a time; after a tool change, subsequent `Z` and `E` commands shall apply to the selected tool module.
- **FR-PRN-052**: The System shall support a configurable tool-change procedure (macro/script) per profile, which may include retract, park, activate, prime, or other module-specific steps.

#### 3.4.7 Extruder and Heater Control During Print
- **FR-PRN-060**: The System shall interpret extrusion (`E`) within motion commands as extrusion for the active tool module.
- **FR-PRN-061**: The System shall support temperature set and wait semantics for the active tool module (e.g., `M104`/`M109`) according to the configured G-code dialect rules.
- **FR-PRN-062**: The System shall monitor heater status (if available) and stop the print on thermal safety faults (e.g., sensor error, over-temperature), according to configuration.
- **FR-PRN-063**: The System shall support fan control for the active tool module during print (e.g., `M106`/`M107`) according to the configured G-code dialect rules.
- **FR-PRN-064**: The System shall support tool execution even when motion and tool hardware are on separate controllers (e.g., FMC motion controller + separate Tool Controller), by executing the G-code-defined tool actions in the correct order and coordinating pause/stop across all involved controllers.

### 3.5 Logging

#### 3.5.1 Log Content and Levels
- **FR-LOG-001**: The System shall implement structured logging with levels (DEBUG/INFO/WARN/ERROR).
- **FR-LOG-002**: The System shall log, at minimum:
  - application start/stop
  - profile changes
  - controller connect/disconnect
  - manual motions (home/move/stop)
  - tool module actions (tool select, heater setpoints, temperature wait events, fan changes, extrusion actions)
  - file load, parse results
  - print start/pause/resume/stop
  - controller errors and exceptions
- **FR-LOG-003**: Logs shall include timestamps and a correlation/job id during prints.

#### 3.5.2 Log Storage and Export
- **FR-LOG-010**: The System shall store logs to disk with rotation (size or date based).
- **FR-LOG-011**: The UI shall provide a “View Logs” and “Export Logs” function.

### 3.6 Controller Plugin System (Hardware Abstraction: Motion + Tool IO)

#### 3.6.1 Baseline Controller Interface
- **FR-CTL-001**: The System shall define a baseline **motion controller** interface implemented by all motion controller plugins.
- **FR-CTL-002**: The baseline interface shall include, at minimum:
  - `connect()` / `disconnect()`
  - `get_status()`
  - `home(axis|all)`
  - `move_absolute(axes_positions, feed, accel, decel)`
  - `move_relative(axes_deltas, feed, accel, decel)`
  - `stop(mode)` and `estop()`
  - error reporting with normalized error codes/messages
- **FR-CTL-003**: The baseline interface shall support multi-axis moves (e.g., XY line moves; XYZ line moves) if the controller supports interpolation.
- **FR-CTL-004**: The System shall support configuring and using multiple controllers simultaneously; the printing core shall route each axis/tool operation (motion, heater, fan, extruder) to the correct controller based on the Machine Profile mapping.

#### 3.6.2 Example Controller Capabilities (Informative)
Some controllers may provide:
- Single-axis jog and stop
- 2-axis or 3-axis line interpolation
- 2-axis arc interpolation
- Digital IO for auxiliaries (fans/relays/heater PWM)
- Auto-run scripts, download files, pause/resume
- Machine status structures including positions, speeds, limits, emergency state

The System shall treat these as optional capabilities exposed through a capability query (e.g., `capabilities()`).

#### 3.6.3 Simulation/Mock Controller
- **FR-CTL-010**: The System shall provide a simulation/mock controller plugin for development/testing that records received commands and simulates status.

#### 3.6.4 Tool/Temperature Controller Interface
- **FR-CTL-020**: The System shall define a baseline **tool controller** interface for tool hardware (at minimum: thermocouple temperature sensing, heaters, fans, extruder control, and safety shutdown).
- **FR-CTL-021**: The tool controller interface shall support, at minimum:
  - set target temperature for a given tool module (or heater channel)
  - read current temperature (thermocouple) if available
  - heater off / emergency heater shutdown
  - set fan speed for a given tool module (or fan channel)
  - extrude/retract for a given tool module (or extruder channel)
  - normalized error reporting (sensor fault, over-temperature, communication fault)
- **FR-CTL-022**: A tool controller may be implemented by the same physical device as the motion controller or by a separate device; the Machine Profile mapping shall define which controller handles tool functions.

#### 3.6.5 FMC006GE31-Type Controller Support (v1)
- **FR-CTL-100**: The System shall provide a controller plugin for FMC006GE31-type controllers on Windows.
- **FR-CTL-101**: The FMC006GE31 plugin shall implement the baseline motion controller interface and expose capabilities based on supported vendor functions.

## 4. External Interface Requirements

### 4.1 User Interface (PySide6 / Qt)

#### 4.1.1 Primary Screens
- **UI-001 Hardware Management**
  - Machine Profile selector (create/edit/delete)
  - Controller configuration panel:
    - controller list (add/remove)
    - type (v1 motion: FMC006GE31; tool: TBD), connection settings (IP/port/device id/other), connect/disconnect
  - Axis + tool module configuration:
    - XY axis config (limits, homing, max feed/accel)
    - tool module table (one row per `Tn`):
      - enabled, module type (FDM/Custom/…)
      - Z axis mapping (controller id + axis index), limits, homing
      - extruder mapping (tool controller id + channel) and extrusion limits (optional)
      - heater mapping (tool controller id + channel), thermocouple sensor config, min/max temp
      - fan mapping (tool controller id + channel) (optional)
  - Active tool selector (`Tn`) for manual operations
  - Live status panel (positions, limits, emergency, run status)
  - Manual controls:
    - motion: Home, Jog/Move, Stop/E-Stop
    - tool: heater set/cooldown, extrude/retract, fan (for FDM modules)

- **UI-002 G-code Viewer**
  - File open + recent files
  - Text view with line numbers + search
  - Parse/validation panel (errors/warnings)
  - Tool/axis usage panel (tools `Tn`, X/Y/Z/E usage, active tool timeline)

- **UI-003 Print Console**
  - Start/Pause/Resume/Stop
  - Progress bar + current line
  - Status telemetry (positions/speeds)
  - Active tool + temperatures (current/target) (if available)
  - Log console (tail of current job logs)

#### 4.1.2 UI Responsiveness
- **UI-010**: The UI shall remain responsive during printing; no long-running controller/G-code operations may execute on the UI thread.

### 4.2 Hardware Interfaces
- Controller communication types (minimum one; extensible):
  - TCP/IP (e.g., IP + port)
  - Serial (COM/tty) (optional)
  - USB HID/vendor APIs (optional)

### 4.3 Software Interfaces
- Plugin API for controller implementations (Python interface/ABC).
- Configuration file schema for machine profiles.
- Log output format (text + optional JSON).

## 5. Non-Functional Requirements

### 5.1 Performance
- **NFR-PERF-001**: UI operations (navigation, simple button clicks) shall respond within 200 ms under normal load.
- **NFR-PERF-002**: Print streaming shall maintain continuous motion without buffer underruns for typical jobs (target: no more than 1 underrun per 10,000 commands; exact metrics TBD).
- **NFR-PERF-003**: Status polling rate shall be configurable (default **2 Hz / 500 ms**) and must not degrade streaming performance.

### 5.2 Reliability and Fault Tolerance
- **NFR-REL-001**: The System shall handle controller disconnects gracefully (stop streaming, notify user, log details).
- **NFR-REL-002**: The System shall not crash on malformed G-code; it shall report parse errors and block printing if configured.

### 5.3 Safety
- **NFR-SAFE-001**: Stop/E-Stop shall preempt all queued motion and be processed with highest priority.
- **NFR-SAFE-002**: The System shall enforce configured soft limits for manual and print motion (unless explicitly disabled with warning).
- **NFR-SAFE-003**: The System shall display emergency/limit states prominently.
- **NFR-SAFE-004**: The System shall target an end-to-end Stop/E-Stop response time of **≤ 500 ms** (from user action to controller command dispatch + UI state update), under normal operating conditions.

### 5.4 Maintainability and Testability
- **NFR-MTN-001**: The core modules (G-code parsing, axis mapping, print job state machine, controller interface) shall be independently unit-testable without hardware by using the simulation/mock controller.
- **NFR-MTN-002**: Each controller plugin shall have isolated tests validating baseline behaviors and error normalization.

### 5.5 Portability
- **NFR-PORT-001**: The application shall separate UI, core logic, and controller plugins to support future OS/controller expansion.

### 5.6 Logging and Observability
- **NFR-OBS-001**: Logs shall be sufficient to reconstruct job lifecycle and diagnose controller errors (timestamps, line numbers, command summaries).

## 6. Suggested Architecture (Informative)

### 6.1 Module Breakdown
- **UI Layer (PySide6)**
  - Views: Hardware, G-code Viewer, Print Console
  - ViewModels / Controllers: handle user actions and bind to backend services

- **Core**
  - `GcodeParser`: parse lines → structured commands (syntax + tokens)
  - `GcodeInterpreter`: stateful interpreter (units, distance mode, extruder mode, active tool) that converts parsed commands into actions
  - `MachineModel`: in-memory representation of the selected Machine Profile (controllers, axes, tools/modules)
  - `ToolRouter`: maps tool-scoped `Z`/`E` to the configured physical axes for the active `Tn`
  - `PrintJob`: state machine (IDLE/READY/RUNNING/PAUSED/STOPPED/ERROR)
  - `MotionCommandModel`: controller-agnostic motion commands (`MoveLinear`, `MoveArc`, `Home`, `Stop`, etc.)

- **Controller HAL**
  - `BaseMotionController` (interface/ABC)
  - `BaseToolController` (interface/ABC) for heaters/temperature/safety
  - Plugins (examples):
    - `FMC006GE31MotionController` (v1)
    - `MockMotionController` (testing)
    - `MockToolController` (testing)

- **Services**
  - `ControllerManager`: constructs and owns controller plugin instances; routes calls by mapping
  - `StatusService`: polling + status cache for UI (positions, limits, emergency, temperatures)
  - `PrintService`: owns the print worker thread; orchestrates streaming + pause/resume/stop
  - `ToolService`: executes tool-related actions (tool select, heater set/wait, fan control, extrusion commands, cooldown, safety shutdown)
  - `LogService`: centralized logging + UI tail

### 6.2 Threading Model (Concept)
- UI thread: rendering + user interactions only.
- Print worker thread: job execution and streaming.
- Status polling: separate periodic task/thread (or timer-driven worker) that must not block print streaming.
- Tool/temperature control: may run as part of `ToolService` with periodic updates (rate configurable) depending on hardware integration.

### 6.3 Data Flow (Concept)
G-code file → Parser → Interpreter → Motion/Tool actions → ControllerManager → Controller plugins → Hardware

Status updates ← Controller plugin ← Polling → UI (signals)

### 6.4 Execution Sequences (Text)

#### 6.4.1 Start Print (Typical)
- User selects Machine Profile and connects controllers.
- User loads G-code; parser + interpreter validate syntax and tool usage.
- Preflight verifies: homing state, limits, emergency state, required tools enabled.
- Print worker thread starts:
  - interprets commands sequentially
  - executes `Tn` tool changes via `ToolService`
  - sends motion commands to motion controller via `ControllerManager`
  - handles `M104/M109` heater set/wait via `ToolService`
  - handles `M106/M107` fan control via `ToolService`
  - streams progress and status to UI via Qt signals

#### 6.4.2 Tool Change (Typical)
- On `Tn`:
  - stop extrusion for previous tool (if applicable)
  - optionally retract/park (macro)
  - activate new tool module
  - optionally prime/purge (macro)
  - update `active_tool` context so subsequent `Z/E` apply to the new tool

### 6.5 Plugin/Dependency Rules (Design Principle)
- The printing core shall depend only on the controller interfaces (`BaseMotionController`, `BaseToolController`) and never call vendor APIs directly.
- All hardware-specific behavior shall live inside controller plugins or tool macros.
- Core modules shall be unit-testable using mock controllers.

## 7. Confirmed Decisions and Remaining Open Questions

### 7.1 Confirmed Decisions (v1)
- **D1**: Target OS is Windows.
- **D2**: Multi-module selection uses tool changes (`T0`, `T1`, ...).
- **D3**: Only one tool module is active at a time; `Z` and `E` apply to the active tool module.
- **D4**: v1 supports FMC006GE31-type motion controllers via a HAL/plugin layer.
- **D5**: Motion relies on controller interpolation (v1).
- **D6**: FDM modules include an extruder motor, a heater, and a fan (in scope for v1).
- **D7**: Tool hardware (heater/temperature sensing, fans, extruder control) will be handled by a **separate Tool Controller** (type TBD) via the tool-controller plugin interface.
- **D8**: Temperature sensing uses **thermocouples** (v1).
- **D9**: Helical/vase mode is **not required** (v1).
- **D10**: Target status update period and Stop/E-Stop response time are **≤ 500 ms** (initial target; configurable).

### 7.2 Remaining Open Questions
- **Q1 (Tool Controller selection)**: What is the Tool Controller hardware/protocol to use (USB/serial/TCP/etc.) and what command API does it expose for:
  - thermocouple temperature readout
  - heater drive (PWM/SSR/relay)
  - fan drive (PWM)
  - extruder motor control
- **Q2 (Extrusion coordination)**: For G-code moves that include both XY(Z) and `E` in the same command (typical FDM), what coordination method will the Tool Controller support?
  - time-based extrusion (extrude X mm over Y seconds), or
  - rate-based extrusion (set mm/s while motion runs), or
  - another mechanism
- **Q3 (Calibration scope)**: Calibration workflows are TBD. Which should be included in v1 (home offsets, steps/mm, temperature tuning, etc.)?

## 8. Appendices (Informative)

### 8.1 Appendix A: Machine Profile Data Model (Example JSON)
This is an example of a persisted Machine Profile. Field names are illustrative and may be adjusted during implementation.

```json
{
  "profile_name": "MSU Multi-Module Printer - v1",
  "units": "mm",
  "controllers": [
    {
      "controller_id": "motion_xy",
      "type": "fmc006ge31",
      "transport": "tcp",
      "ip": "192.168.0.30",
      "port": 8088,
      "device_id": 1
    },
    {
      "controller_id": "motion_z",
      "type": "fmc006ge31",
      "transport": "tcp",
      "ip": "192.168.0.31",
      "port": 8088,
      "device_id": 2
    },
    {
      "controller_id": "tool_ctrl",
      "type": "tbd_tool_controller",
      "transport": "tbd",
      "connection": {}
    }
  ],
  "motion_axes": {
    "X": {
      "enabled": true,
      "controller_id": "motion_xy",
      "controller_axis_index": 0,
      "travel_min": 0.0,
      "travel_max": 300.0,
      "max_feed": 300.0,
      "accel": 1000.0,
      "decel": 1000.0,
      "home": { "enabled": true, "dir": -1, "speed": 50.0 }
    },
    "Y": {
      "enabled": true,
      "controller_id": "motion_xy",
      "controller_axis_index": 1,
      "travel_min": 0.0,
      "travel_max": 300.0,
      "max_feed": 300.0,
      "accel": 1000.0,
      "decel": 1000.0,
      "home": { "enabled": true, "dir": -1, "speed": 50.0 }
    },
    "Z_T0": {
      "enabled": true,
      "controller_id": "motion_z",
      "controller_axis_index": 0,
      "travel_min": 0.0,
      "travel_max": 150.0,
      "max_feed": 100.0,
      "accel": 500.0,
      "decel": 500.0,
      "home": { "enabled": true, "dir": 1, "speed": 20.0 }
    },
    "Z_T1": {
      "enabled": true,
      "controller_id": "motion_z",
      "controller_axis_index": 1,
      "travel_min": 0.0,
      "travel_max": 150.0,
      "max_feed": 100.0,
      "accel": 500.0,
      "decel": 500.0,
      "home": { "enabled": true, "dir": 1, "speed": 20.0 }
    },
    "Z_T2": {
      "enabled": true,
      "controller_id": "motion_z",
      "controller_axis_index": 2,
      "travel_min": 0.0,
      "travel_max": 150.0,
      "max_feed": 100.0,
      "accel": 500.0,
      "decel": 500.0,
      "home": { "enabled": true, "dir": 1, "speed": 20.0 }
    },
    "Z_T3": {
      "enabled": true,
      "controller_id": "motion_z",
      "controller_axis_index": 3,
      "travel_min": 0.0,
      "travel_max": 150.0,
      "max_feed": 100.0,
      "accel": 500.0,
      "decel": 500.0,
      "home": { "enabled": true, "dir": 1, "speed": 20.0 }
    }
  },
  "tools": [
    {
      "tool_index": 0,
      "enabled": true,
      "module_type": "FDM",
      "z_axis_id": "Z_T0",
      "extruder": {
        "controller_id": "tool_ctrl",
        "channel": 0
      },
      "fan": {
        "controller_id": "tool_ctrl",
        "channel": 0
      },
      "heater": {
        "controller_id": "tool_ctrl",
        "channel": 0,
        "sensor_type": "thermocouple",
        "min_temp_c": 0,
        "max_temp_c": 300,
        "min_extrude_temp_c": 170
      }
    },
    {
      "tool_index": 1,
      "enabled": true,
      "module_type": "FDM",
      "z_axis_id": "Z_T1",
      "extruder": {
        "controller_id": "tool_ctrl",
        "channel": 1
      },
      "fan": {
        "controller_id": "tool_ctrl",
        "channel": 1
      },
      "heater": {
        "controller_id": "tool_ctrl",
        "channel": 1,
        "sensor_type": "thermocouple",
        "min_temp_c": 0,
        "max_temp_c": 300,
        "min_extrude_temp_c": 170
      }
    },
    {
      "tool_index": 2,
      "enabled": true,
      "module_type": "FDM",
      "z_axis_id": "Z_T2",
      "extruder": {
        "controller_id": "tool_ctrl",
        "channel": 2
      },
      "fan": {
        "controller_id": "tool_ctrl",
        "channel": 2
      },
      "heater": {
        "controller_id": "tool_ctrl",
        "channel": 2,
        "sensor_type": "thermocouple",
        "min_temp_c": 0,
        "max_temp_c": 300,
        "min_extrude_temp_c": 170
      }
    },
    {
      "tool_index": 3,
      "enabled": true,
      "module_type": "FDM",
      "z_axis_id": "Z_T3",
      "extruder": {
        "controller_id": "tool_ctrl",
        "channel": 3
      },
      "fan": {
        "controller_id": "tool_ctrl",
        "channel": 3
      },
      "heater": {
        "controller_id": "tool_ctrl",
        "channel": 3,
        "sensor_type": "thermocouple",
        "min_temp_c": 0,
        "max_temp_c": 300,
        "min_extrude_temp_c": 170
      }
    }
  ],
  "gcode": {
    "dialect": "custom_fdm_multimodule",
    "default_tool_index": 0,
    "default_feed": 100.0
  }
}
```

### 8.2 Appendix B: Normalized Status Model (Concept)
All controller plugins should normalize raw status data into a common model for UI and print logic:

- `positions`: map of logical axis → float position (in configured units)
- `speeds`: map of logical axis → float speed
- `limits_negative`, `limits_positive`: map of logical axis → bool (if supported)
- `run_state`: enum (IDLE/RUNNING/PAUSED/STOPPED/ERROR)
- `emergency_active`: bool (if supported)
- `active_tool_index`: current selected tool (`Tn`) (if tracked by the system)
- `temperatures_current`: map of tool/heater channel → float °C (if available)
- `temperatures_target`: map of tool/heater channel → float °C (if available)
- `fans_current`: map of tool/fan channel → float (PWM or percent) (if available)
- `last_error`: optional normalized error (code + message + raw details)

### 8.3 Appendix C: Controller Plugin Interface (Pseudo-code)
This is the baseline contract the printing core depends on (illustrative).

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional


class StopMode(str, Enum):
    CONTROLLED = "controlled"
    IMMEDIATE = "immediate"


@dataclass(frozen=True)
class NormalizedError:
    code: str
    message: str
    raw: Optional[object] = None


@dataclass(frozen=True)
class MotionStatus:
    positions: Mapping[str, float]
    speeds: Mapping[str, float]
    emergency_active: bool
    run_state: str
    last_error: Optional[NormalizedError] = None


class BaseMotionController(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def get_status(self) -> MotionStatus: ...

    @abstractmethod
    def home(self, axes: Optional[list[str]] = None) -> None: ...

    @abstractmethod
    def move_absolute(
        self,
        axes_positions: Mapping[str, float],
        feed: float,
        accel: Optional[float] = None,
        decel: Optional[float] = None
    ) -> None: ...

    @abstractmethod
    def move_relative(
        self,
        axes_deltas: Mapping[str, float],
        feed: float,
        accel: Optional[float] = None,
        decel: Optional[float] = None
    ) -> None: ...

    @abstractmethod
    def stop(self, mode: StopMode = StopMode.CONTROLLED) -> None: ...

    @abstractmethod
    def estop(self) -> None: ...


@dataclass(frozen=True)
class TemperatureStatus:
    current_c: float
    target_c: float
    heater_on: bool


@dataclass(frozen=True)
class ToolStatus:
    temperatures: Mapping[int, TemperatureStatus]
    fans: Mapping[int, float]
    last_error: Optional[NormalizedError] = None


class BaseToolController(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def get_status(self) -> ToolStatus: ...

    @abstractmethod
    def set_target_temperature(self, channel: int, target_c: float) -> None: ...

    @abstractmethod
    def heater_off(self, channel: int) -> None: ...

    @abstractmethod
    def set_fan_speed(self, channel: int, value: float) -> None: ...

    @abstractmethod
    def extrude(self, channel: int, distance_mm: float, feed_mm_s: float) -> None: ...

    @abstractmethod
    def emergency_shutdown(self) -> None: ...
```

### 8.4 Appendix D: Print Job State Machine (Concept)
- **IDLE**: no file selected / no active job
- **READY**: file parsed, hardware connected, preflight passed
- **RUNNING**: worker thread streaming commands
- **PAUSED**: streaming halted, machine stopped/paused safely
- **STOPPED**: job terminated by user
- **ERROR**: terminated due to controller error or fatal parse/runtime fault

