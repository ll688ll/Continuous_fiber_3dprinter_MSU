# COMMAND_LIST.md

## Purpose
This file defines the complete command and function list required for v1 of the multi-tool 3D printer control system. Use it as the implementation checklist for UI, core logic, streaming, safety, and hardware plugins.

## Command Naming Rules
- Commands use VERB_NOUN style.
- Inputs are shown in parentheses.
- Every command must log start, success, and error.
- Stop and E-Stop must preempt all queued work across motion and tool controllers.
- Commands that depend on hardware features must be gated by capabilities.

## 1. Application Commands

### 1.1 Lifecycle
- APP_START()
- APP_SHUTDOWN()

### 1.2 Diagnostics
- APP_GET_VERSION()
- APP_GET_BUILD_INFO()
- APP_SET_LOG_LEVEL(level)
- APP_OPEN_LOG_FOLDER()

## 2. Machine Profile Commands

### 2.1 CRUD
- PROFILE_CREATE(profile_data)
- PROFILE_READ(profile_id)
- PROFILE_UPDATE(profile_id, profile_data)
- PROFILE_DELETE(profile_id)
- PROFILE_LIST()

### 2.2 Selection and Persistence
- PROFILE_SELECT(profile_id)
- PROFILE_GET_ACTIVE()
- PROFILE_SAVE_TO_DISK(profile_id, path)
- PROFILE_LOAD_FROM_DISK(path)

### 2.3 Validation
- PROFILE_VALIDATE(profile_id)
  - validates controller definitions exist
  - validates axis mappings are unique
  - validates travel limits, max feed, accel, decel
  - validates tool mappings and enabled tools
  - validates required fields for module type
  - validates unit and feedrate modes are defined per profile

### 2.4 Migration and Schema
- PROFILE_GET_SCHEMA()
- PROFILE_MIGRATE(profile_data, from_version, to_version)

## 3. Controller Management Commands

### 3.1 Plugin Registry
- PLUGIN_LIST()
- PLUGIN_GET_INFO(plugin_type)
- PLUGIN_VALIDATE_CONFIG(plugin_type, config)

### 3.2 Controller Instances
- CONTROLLER_ADD(controller_config)
- CONTROLLER_REMOVE(controller_id)
- CONTROLLER_LIST()
- CONTROLLER_GET(controller_id)
- CONTROLLER_UPDATE(controller_id, controller_config)

### 3.3 Connection
- CONTROLLER_CONNECT(controller_id)
- CONTROLLER_DISCONNECT(controller_id)
- CONTROLLER_RECONNECT(controller_id)

### 3.4 Capabilities and Status
- CONTROLLER_GET_CAPABILITIES(controller_id)
- CONTROLLER_GET_STATUS(controller_id)
- CONTROLLER_GET_STATUS_ALL()

### 3.5 Capability Gating (Required)
- REQUIRE_CAPABILITY(controller_id, capability_key)
  - fail fast with actionable error if missing

### 3.6 Queue and Execution Introspection (Optional but strongly recommended)
- CONTROLLER_GET_QUEUE_DEPTH(controller_id)
- CONTROLLER_FLUSH_QUEUE(controller_id)

### 3.7 Fault Handling
- CONTROLLER_CLEAR_FAULT(controller_id)
- CONTROLLER_RESET(controller_id)

## 4. System State Commands (Required)

### 4.1 Homed State and Coordinates
- STATE_SET_AXIS_HOMED(axis, homed_bool)
- STATE_GET_HOMED_AXES()
- STATE_SET_CURRENT_POSITION(positions)
- STATE_GET_CURRENT_POSITION()
- STATE_SET_UNITS(units)
- STATE_GET_UNITS()
- STATE_SET_DISTANCE_MODE(mode)
  - absolute or relative for X/Y/Z
- STATE_SET_EXTRUSION_MODE(mode)
  - absolute or relative for E

### 4.2 Offsets (Optional in v1, reserve the contract)
- STATE_SET_WORK_OFFSET(offsets)
- STATE_GET_WORK_OFFSET()

## 5. Active Tool and Tool Module Commands

### 5.1 Selection and Enablement
- TOOL_SELECT(tool_index)
- TOOL_GET_ACTIVE()
- TOOL_SET_ENABLED(tool_index, enabled)
- TOOL_LIST_CONFIGURED()

### 5.2 Tool Change Procedure
- TOOL_RUN_CHANGE_PROCEDURE(from_tool_index, to_tool_index)
  - retract, park, activate, prime, driven by profile settings
- TOOL_ABORT_CHANGE_PROCEDURE()

### 5.3 Tool State
- TOOL_GET_STATE(tool_index)
  - mapped Z axis id
  - extruder, heater, fan channels
  - min extrude temp
  - min and max temp limits

## 6. Manual Motion Commands

### 6.1 Homing
- MOTION_HOME_ALL()
- MOTION_HOME_AXES(axes_list)
  - allowed: X, Y
- MOTION_HOME_TOOL_Z(tool_index)

### 6.2 Moves
- MOTION_MOVE_ABSOLUTE(positions, feed, accel_optional, decel_optional)
  - positions may include X, Y, and active tool Z only
- MOTION_MOVE_RELATIVE(deltas, feed, accel_optional, decel_optional)

### 6.3 Jog Controls
- MOTION_SET_JOG_SETTINGS(step_mm, feed, accel_optional)
- MOTION_JOG(axis, direction, step_or_continuous)
- MOTION_JOG_STOP(axis_optional)

### 6.4 Stops
- MOTION_STOP(mode)
  - controlled or immediate
- MOTION_ESTOP()

### 6.5 Soft Limits and Validation
- MOTION_VALIDATE_MOVE(planned_move)
- MOTION_SET_SOFT_LIMITS_ENABLED(enabled)
- MOTION_SET_AXIS_LIMITS(axis, min_value, max_value)

## 7. Manual Tool Hardware Commands

### 7.1 Heater
- TOOL_HEATER_SET_TARGET(tool_index, target_c)
- TOOL_HEATER_OFF(tool_index)
- TOOL_HEATER_WAIT(tool_index, target_c, tolerance_c, stable_time_s, timeout_s)
- TOOL_HEATER_GET_TEMPERATURE(tool_index)
- TOOL_HEATER_GET_TARGET(tool_index)

### 7.2 Fan
- TOOL_FAN_SET(tool_index, value)
  - value is percent or pwm based on profile dialect config
- TOOL_FAN_OFF(tool_index)
- TOOL_FAN_GET(tool_index)

### 7.3 Extrusion
- TOOL_EXTRUDE(tool_index, distance_mm, feed_mm_s)
- TOOL_RETRACT(tool_index, distance_mm, feed_mm_s)

### 7.4 Tool Safety Interlocks
- TOOL_SET_MIN_EXTRUDE_TEMP(tool_index, min_temp_c)
- TOOL_SET_THERMAL_LIMITS(tool_index, min_temp_c, max_temp_c)
- TOOL_OVERRIDE_INTERLOCK(interlock_key, enabled, user_ack_token)

### 7.5 Emergency Tool Shutdown
- TOOL_EMERGENCY_SHUTDOWN()
  - heater off all channels, stop extrusion, fan safe state per policy

## 8. G-code Commands Supported (Dialect Level)

### 8.1 Motion
- G0 rapid move
- G1 linear move
- G2 arc clockwise
- G3 arc counterclockwise
- G92 set position

### 8.2 Modes
- G20 inches
- G21 millimeters
- G90 absolute positioning
- G91 relative positioning
- M82 absolute extrusion
- M83 relative extrusion

### 8.3 Tool Selection
- Tn tool select

### 8.4 Temperature
- M104 set temperature, do not wait
- M109 set temperature and wait
- M105 report temperature

### 8.5 Fan
- M106 fan on or set speed
- M107 fan off

### 8.6 Flow Control
- M0 pause
- M1 pause
- M2 end
- M30 end

## 9. G-code File Pipeline Commands

### 9.1 File Handling
- GCODE_FILE_OPEN(path)
- GCODE_FILE_CLOSE()
- GCODE_FILE_GET_TEXT(range_optional)
- GCODE_FILE_GET_METADATA()
  - file size, line count, last modified

### 9.2 Parsing
- GCODE_PARSE()
  - returns parsed commands with diagnostics
- GCODE_PARSE_GET_ERRORS()
- GCODE_PARSE_GET_WARNINGS()

### 9.3 Interpretation
- GCODE_INTERPRET(profile_id)
  - converts parsed commands into normalized action stream using tool-scoped Z and E routing
- GCODE_INTERPRET_PREVIEW(limit_optional)

### 9.4 Validation and Analysis
- GCODE_VALIDATE(profile_id)
  - unknown command policy
  - arc parameter rules
  - tool index validity
  - Z or E before tool selection handling
- GCODE_ANALYZE_USAGE()
  - tools used
  - axis words used: X, Y, Z, E
  - active tool timeline
- GCODE_FIND(text_query)
- GCODE_GOTO_LINE(line_number)

## 10. Normalized Action Model Commands (Interpreter Output)
Each action must be serializable and loggable. Each action references a source line number.

### 10.1 Motion Actions
- ACT_MOVE_LINEAR(end_positions, feed, accel_optional, decel_optional)
- ACT_MOVE_ARC(plane, end_positions, center_or_radius, direction, feed)
- ACT_HOME(axes_optional)
- ACT_STOP(mode)
- ACT_ESTOP()

### 10.2 Synchronized Motion and Extrusion (Required for FDM correctness)
- ACT_MOVE_LINEAR_SYNC(end_positions, e_delta, feed, accel_optional, decel_optional)
  - single action representing one G1 line that includes E
  - execution must synchronize extrusion with motion according to supported sync mode

### 10.3 Tool Actions
- ACT_TOOL_SELECT(tool_index)
- ACT_SET_TEMPERATURE(tool_index, target_c)
- ACT_WAIT_TEMPERATURE(tool_index, target_c, tolerance_c, stable_time_s, timeout_s)
- ACT_FAN_SET(tool_index, value)
- ACT_FAN_OFF(tool_index)
- ACT_EXTRUDE(tool_index, distance_mm, feed_mm_s)
- ACT_HEATER_OFF(tool_index)

### 10.4 Program Actions
- ACT_PAUSE(reason_optional)
- ACT_END()

## 11. Arc Execution Policy (Required for real support)
- ARC_SET_MODE(mode)
  - NATIVE_CONTROLLER_ARC
  - SEGMENT_APPROXIMATION
- ARC_SET_SEGMENT_SETTINGS(max_chord_error_mm, max_segment_length_mm)

## 12. Thermal Policies (Required)
- THERMAL_SET_WAIT_POLICY(tool_index, tolerance_c, stable_time_s, timeout_s)
- THERMAL_SET_FAULT_POLICY(tool_index, on_fault_action)
  - STOP_PRINT
  - ESTOP_ALL
- THERMAL_SET_FILTER_SETTINGS(tool_index, filter_type, params)

## 13. Print Job Commands

### 13.1 Job Lifecycle
- JOB_CREATE(gcode_path, profile_id)
- JOB_DELETE(job_id)
- JOB_GET(job_id)
- JOB_LIST()

### 13.2 Preflight
- JOB_PREFLIGHT(job_id)
  - controllers connected
  - emergency not active
  - homed state or policy acknowledgement
  - soft limits enabled or policy acknowledgement
  - gcode parsed and no fatal errors
  - referenced tools are configured and enabled
  - thermal policies defined if temperature commands exist
  - sync mode supported if G1 with E exists

### 13.3 Execution Control
- JOB_START(job_id, start_line_optional)
- JOB_PAUSE(job_id)
- JOB_RESUME(job_id)
- JOB_STOP(job_id)
- JOB_ESTOP(job_id)

### 13.4 Job State and Progress
- JOB_GET_STATE(job_id)
  - idle, ready, running, paused, stopped, error, completed
- JOB_GET_PROGRESS(job_id)
  - current line, percent, optional eta
- JOB_GET_CURRENT_ACTION(job_id)

### 13.5 Execution Confirmation and Resume Safety
- JOB_MARK_LINE_CONFIRMED(job_id, line_number, confirm_level)
  - confirm_level: queued, accepted, completed
- JOB_GET_LAST_CONFIRMED(job_id)
- JOB_RESUME_FROM_CONFIRMED(job_id)

### 13.6 Persistent Job Artifacts (Required)
- JOB_GET_MANIFEST(job_id)
  - profile id and profile snapshot hash
  - gcode path and gcode hash
  - controller plugin versions and connection info
  - start time, end time, end reason
  - last confirmed line
- JOB_SAVE_MANIFEST(job_id, path)
- JOB_EXPORT_REPORT(job_id, path)

## 14. Streaming Worker Commands

### 14.1 Worker Control
- STREAMER_START(job_id)
- STREAMER_STOP(job_id)
- STREAMER_PAUSE(job_id)
- STREAMER_RESUME(job_id)

### 14.2 Cancellation and Shutdown (Required)
- STREAMER_REQUEST_CANCEL(job_id)
- STREAMER_JOIN(job_id, timeout_s)

### 14.3 Backpressure and Synchronization
- STREAMER_HANDLE_BACKPRESSURE(controller_id)
- STREAMER_SYNC_MOTION_AND_TOOL()
  - enforces ordering between tool and motion actions
  - blocks on ACT_WAIT_TEMPERATURE safely
  - executes ACT_MOVE_LINEAR_SYNC based on supported sync mode

### 14.4 Completion and Acknowledgement (Required)
- STREAMER_WAIT_MOTION_COMPLETE(axes_optional, timeout_s)
- STREAMER_WAIT_TOOL_COMPLETE(tool_index_optional, timeout_s)

### 14.5 Error Handling
- STREAMER_ABORT_ON_ERROR(job_id, error)
- STREAMER_SET_ERROR_POLICY(policy)
  - STOP_PRINT or ESTOP_ALL

## 15. Safety Commands

### 15.1 Stop and E-Stop Policy (Required)
- SAFE_SET_STOP_POLICY(on_stop, on_estop)
  - defines tool safe states and motion behavior
- SAFE_APPLY_STOP_POLICY(action_type)
  - action_type: STOP or ESTOP

### 15.2 Emergency
- SAFE_ESTOP_ALL()
  - motion estop and tool emergency shutdown
- SAFE_CLEAR_ESTOP()
  - only if hardware supports and policy allows

### 15.3 Interlocks
- SAFE_CHECK_EMERGENCY()
- SAFE_CHECK_SOFT_LIMITS(planned_move)
- SAFE_CHECK_MIN_EXTRUDE_TEMP(tool_index)
- SAFE_CHECK_THERMAL_LIMITS(tool_index, current_temp_c)

### 15.4 Fault Processing
- SAFE_ON_LIMIT_HIT(axis)
- SAFE_ON_CONTROLLER_FAULT(controller_id, fault)
- SAFE_ON_TOOL_FAULT(tool_index, fault)
- SAFE_ON_COMMUNICATION_LOSS(controller_id)

## 16. Status and Telemetry Commands

### 16.1 Polling
- STATUS_POLL_START(rate_hz)
- STATUS_POLL_STOP()
- STATUS_POLL_SET_RATE(rate_hz)

### 16.2 Thread Join (Required)
- STATUS_POLL_JOIN(timeout_s)

### 16.3 Cached Status
- STATUS_GET_SNAPSHOT()
  - positions, speeds, limits, emergency, run_state, temps, targets, last errors
- STATUS_GET_AXIS_POSITIONS()
- STATUS_GET_TEMPERATURES()
- STATUS_GET_LIMITS()
- STATUS_GET_LAST_ERRORS()

### 16.4 UI Events
- UI_EVENT_EMIT_STATUS(snapshot)
- UI_EVENT_EMIT_JOB_PROGRESS(job_id, progress)
- UI_EVENT_EMIT_JOB_STATE(job_id, state)
- UI_EVENT_EMIT_WARNING(message, context)
- UI_EVENT_EMIT_ERROR(message, context)

## 17. Logging Commands

### 17.1 Structured Logging
- LOG_WRITE(level, event_type, data)
- LOG_SET_JOB_CONTEXT(job_id)
- LOG_CLEAR_JOB_CONTEXT()

### 17.2 Log Storage
- LOG_CONFIGURE_ROTATION(policy)
- LOG_EXPORT(job_id_optional, path)

## 18. Required HAL Interfaces

### 18.1 Motion Controller Plugin Interface
- connect()
- disconnect()
- get_status()
- home(axes_optional)
- move_absolute(axes_positions, feed, accel_optional, decel_optional)
- move_relative(axes_deltas, feed, accel_optional, decel_optional)
- stop(mode)
- estop()
- capabilities()

Optional but recommended:
- get_queue_depth()
- flush_queue()
- wait_motion_complete(axes_optional, timeout_s)

### 18.2 Tool Controller Plugin Interface
- connect()
- disconnect()
- get_status()
- set_target_temperature(channel, target_c)
- heater_off(channel)
- set_fan_speed(channel, value)
- extrude(channel, distance_mm, feed_mm_s)
- emergency_shutdown()
- capabilities()

Optional but recommended:
- wait_tool_complete(channel_optional, timeout_s)

## 19. Feature Coverage Checklist (v1 minimum)
To claim v1 complete, implement and test at minimum:
- Profile CRUD, schema, and validation
- Controller connect, status, stop, estop, capabilities gating
- System state tracking for units, modes, homing
- Manual home and move with soft limits
- G-code open, parse, validate, usage analysis
- Interpreter emits tool-scoped Z and E actions correctly
- ACT_MOVE_LINEAR_SYNC for G1 lines with E
- Arc policy implementation, native or segmented
- Thermal wait and fault policies for thermocouple systems
- Print job preflight, start, pause, resume, stop, estop
- Streaming worker cancellation, join, and completion waits
- Job manifest and report export
- Logs with job id and line number correlation