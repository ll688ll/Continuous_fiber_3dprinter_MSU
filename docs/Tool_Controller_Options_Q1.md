# Tool Controller Options — SRS Q1

| | |
|---|---|
| Status | Draft v0.2 (scoped by decision D-Q1a, 2026-08-13) |
| Date | 2026-08-13 |
| Owner | Jacky Lyu |
| Related docs | `docs/Software_SRS.md` (Q1/Q2, D6–D8), `docs/PRD_Printer_App.md` (M5), `docs/COMMAND_LIST.md` §18.2 |

## 0. Decision D-Q1a (Jacky, 2026-08-13): the FMC is motion-only

The FMC006GE31's channels are reserved for **motion**: X, Y, and the tool
modules' Z axes (Z_T0/Z_T1/Z_T2 on channels 2/3/4; channel 5 spare). The
extruder is **not** placed on FMC channels — it belongs to the (separate)
tool controller, exactly as SRS D7 specifies. Consequences:

- §2.1's "extrusion on FMC spare channels / `Line_3Axis` sync" proposal is
  **rejected** (kept below for the record).
- Extrusion — and with it the motion↔extrusion sync design (SRS Q2) — is
  **deferred** until the tool controller is chosen; the printer stays
  motion-only (XYZ) meanwhile.
- The tool-controller choice (§2.2) must therefore cover extruder stepper
  drive as well as heater/thermocouple/fan.

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

## 2. Options considered

### 2.1 Extrusion on the FMC's spare channels — REJECTED by D-Q1a

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

Per D-Q1a this stays on record only: the channels are reserved for tool-Z
axes (three modules), and cross-controller sync (SRS Q2) will be designed
when extrusion lands on the tool controller — realistic mechanisms are
rate-based (set E feed while the XY line runs) or time-based (extrude
d mm over the segment's duration), both supported by the boards below.

### 2.2 The tool controller (heater + thermocouple + fan + extruder)

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

## 3. Recommendation (under D-Q1a)

**R1 (recommended): one Marlin-class board (T-A) as the complete tool
controller — heaters, thermocouples, fans, AND extruder steppers.**

- This is exactly what those boards are built for: an Octopus-class board
  drives 4+ extruder steppers and 3–4 heater/fan channels — covering all
  three planned tool modules from one board.
- Built-in thermal runaway protection, PID, proven extruder motion; the
  printer speaks `M104/M105/M106/G1 E` over USB-serial — a ~150-line
  `MarlinToolController(BaseToolController)` plugin plus a sim twin,
  testable exactly like the motion stack was.
- Extrusion sync (SRS Q2, deferred): rate- or time-based extrusion during
  XY segments; Marlin's `G1 En Ffeed` supports both shapes.
- Rough bill for tool 1: board ~$60, TC adapter ~$15, 24 V PSU ~$40
  (+ hotend/heater/fan/motor mechanicals).

**R2 (premium): Duet 3 (T-B)** — same shape, first-class thermocouple
daughterboards and a cleaner object-model protocol, at ~3–4× the price.

**R3 (fallback if board firmware fights the thermocouples): T-C custom
ESP32 node** — exact protocol fit, but we then own thermal safety; budget
2–3× the software effort and a deliberate burn-in plan.

## 4. What M5 looks like once the board is chosen (sketch)

1. `MarlinToolController` plugin + `SimToolController` (thermal model,
   fault injection) behind `BaseToolController`; profile gains the tool
   controller connection + channel maps (SRS FR-HW-011).
2. Interpreter/runner: un-skip `M104/M109/M106/M107` + E words, `M109`
   wait policy, thermal fault → stop print (SRS FR-PRN-060..064);
   preflight gains temperature checks. Sync design (SRS Q2) decided
   against the board's actual capabilities.
3. UI: temperature panel (current/target), fan control.
4. Hardware bring-up: heater PID tune, min-extrude-temp interlock, first
   extruding print.

Until then the printer stays motion-only (XYZ), which D-Q1a explicitly
endorses; wiring Z_T1/Z_T2 onto channels 3/4 needs only config + profile
entries (the multi-tool routing already works).

## 5. Questions for Jacky (decision inputs)

1. **Heater spec** — cartridge voltage/wattage (24 V 40–60 W typical, or
   mains+SSR for something hotter?). Thermocouple type K?
2. **Extruder motor** — NEMA17-class stepper? Geared? Current rating
   (drives the board's stepper-driver choice)?
3. **Does the continuous-fiber tool need a cutter** (servo/solenoid, like
   Markforged-style fiber shear) or other actuators (fiber tensioner)?
   These land on the board's spare outputs — good to know before picking.
4. **Budget/procurement** — any constraint that favors parts already in
   the lab or a particular vendor?
