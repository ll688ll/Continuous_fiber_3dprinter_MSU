# Tool Controller Options — SRS Q1

| | |
|---|---|
| Status | Draft v0.1 for decision |
| Date | 2026-08-13 |
| Owner | Jacky Lyu |
| Related docs | `docs/Software_SRS.md` (Q1/Q2, D6–D8), `docs/PRD_Printer_App.md` (M5), `docs/COMMAND_LIST.md` §18.2 |

## 1. What we are choosing

The hardware (and its protocol) behind `BaseToolController` — everything a
tool module needs that is not motion:

| Function | v1 requirement (SRS D6–D8) |
|---|---|
| Temperature sensing | **thermocouple** (D8), per tool |
| Heater drive | hotend cartridge, PWM/SSR, per tool |
| Part-cooling fan | PWM, per tool |
| Extruder drive | stepper; extrude/retract distance at feed, **synchronized with XY motion** (SRS Q2) |
| Safety | heater-off/emergency shutdown, thermal-runaway protection, normalized errors |
| Host interface | Windows PC, Python, USB-serial or TCP |

Scope reality check: v1 needs **one** working FDM-style fiber tool (T0);
the profile schema already scales to 4+.

## 2. The key structural insight: split extrusion from thermal

"Tool controller" is really two problems with different best answers:

### 2.1 Extrusion — use the FMC's spare channels (recommended)

The FMC006GE31 has 6 axes; only 3 are used (X, Y, Z_T0). **An extruder is
just a stepper axis.** Putting E_T0 on channel 3:

- `move_relative(3, dist, speed)` = extrude/retract — the hardware-proven
  library (queues, verification, e-stop) drives it with zero new code.
- **`Line_3Axis` answers SRS Q2 outright**: one vendor call interpolating
  X, Y, *and E* gives extrusion that is hardware-synchronized and exactly
  proportional along the path — the `ACT_MOVE_LINEAR_SYNC` semantics that
  time- or rate-based schemes only approximate. (Needs a `move_line` 3-axis
  extension + a bench item, mirroring the 2-axis work: same engine
  pattern, vendor signature already in the header.)
- Channel budget: XY + Z_T0 + E_T0 + Z_T1 + E_T1 = 6 → **two full tools on
  the existing controller**; tools 3–4 later need a second FMC (the
  library's `device_id` model anticipates this) — a deferred, known path.
- Cost: one stepper driver + PSU wiring per tool (the controller channel
  exists). Effort: small library extension we control end-to-end.

The alternative (extruder on the thermal board, Marlin-style `G1 E`) keeps
FMC channels free but makes synchronization open-loop across two
controllers — the exact coordination problem Q2 worries about. Keep it as
fallback only.

### 2.2 Thermal + fan — the real hardware choice

| | **T-A: 3D-printer board as thermal node** (recommended) | **T-B: Duet 3 Mini 5+** | **T-C: custom MCU node (ESP32/RP2040)** | **T-D: industrial PID modules** |
|---|---|---|---|---|
| Hardware | BTT SKR/Octopus-class (~$40–70) + MAX31855/56 thermocouple SPI adapter (~$15/ch) + 24 V PSU | Duet + thermocouple daughterboard (~$250+) | MCU (~$10) + MAX31856 (~$15/ch) + MOSFET drivers | e.g. Omega/Autonics PID w/ Modbus (~$80–150 **per loop**) + SSR |
| Firmware | Marlin, config-only (no code) | RepRapFirmware, config-only | **ours, from scratch** | vendor |
| Protocol → Python | `M104/M105/M106/M107` over USB-serial — maps 1:1 onto `BaseToolController`; ~150 lines | same, cleaner (object model, network) | custom serial/JSON — exact contract fit | Modbus RTU (`pymodbus`) |
| Thermocouples | supported (Marlin `TEMP_SENSOR -3/-5`), needs the SPI adapters | first-class daughterboards | native (that's the point of MAX31856) | native, industrial-grade |
| Thermal runaway protection | **built-in, battle-tested** | built-in | **we own it** (the riskiest part) | built-in per loop |
| Channels per unit | 3–4 heaters/fans on one board | 3+ | as built | 1 loop per module |
| Fan / extruder extras | fans yes; could also host extruders (fallback) | yes | yes | **no** — fans/extruder live elsewhere |
| Effort to first heated tool | **low** (wire, configure, write the serial plugin) | low, costlier | high (firmware + safety validation) | medium (per-loop wiring, no fan) |
| Risk | Marlin TC config quirks; serial robustness (well-trodden) | budget | burning down the lab is on our firmware | integration sprawl across boxes |

## 3. Recommendation

**R1 (recommended): E on FMC spare channels + one Marlin-class board as a
pure thermal/fan node (T-A).**

- Extrusion inherits the validated motion stack and gets true hardware
  sync via `Line_3Axis` (Q2 answered).
- Heater/thermocouple/fan get mature firmware with built-in thermal
  runaway protection; the printer only speaks M-codes over serial — a
  ~150-line `MarlinToolController(BaseToolController)` plugin plus a sim
  twin, testable exactly like the motion stack was.
- Scales to tool 2 with zero new architecture; tools 3–4 = second FMC +
  more heater channels (same board family).
- Rough v1 bill: board ~$60, TC adapter ~$15, 24 V PSU ~$40, stepper
  driver for E ~$20 (+ hotend/heater/fan/motor mechanicals).

**R2 (fallback if Marlin's thermocouple path fights us): T-C custom ESP32
node** — same architecture, exact protocol fit, but we then own thermal
safety; budget 2–3× the software effort and a deliberate burn-in test
plan.

## 4. What M5 looks like once decided (sketch)

1. Library v1.2: 3-axis `move_line` (Line_3Axis) + bench items (sim-first,
   like v1.1).
2. `MarlinToolController` plugin + `SimToolController` (thermal model,
   fault injection) behind `BaseToolController`; profile gains the tool
   controller connection + channel maps (SRS FR-HW-011).
3. Interpreter/runner: un-skip `M104/M109/M106/M107` + E words →
   `ACT_MOVE_LINEAR_SYNC` execution, `M109` wait policy, thermal fault →
   stop print (SRS FR-PRN-060..064). Preflight gains temperature checks.
4. UI: temperature panel (current/target), fan control.
5. Hardware bring-up: heater PID tune, min-extrude-temp interlock, first
   extruding print.

## 5. Questions for Jacky (decision inputs)

1. **Tool count timeline** — is one fiber tool (T0) the horizon for this
   year, or are two+ modules coming soon? (Decides how hard the 6-channel
   budget matters.)
2. **Heater spec** — cartridge voltage/wattage (24 V 40–60 W typical, or
   mains+SSR for something hotter?). Thermocouple type K?
3. **Extruder motor** — NEMA17-class stepper? Geared? Current rating
   (drives the driver choice on the FMC channel)?
4. **Does the continuous-fiber tool need a cutter** (servo/solenoid, like
   Markforged-style fiber shear) or other actuators (fiber tensioner)?
   These would land on the thermal node's spare outputs — good to know
   before picking the board.
5. **Budget/procurement** — any constraint that favors parts already in
   the lab or a particular vendor?
