# TODO — Duet 3 6XD platform

Picked up from the commissioning session of 2026-08-25. Each item says what to
do, why it matters, and what will bite you — enough to act on without re-reading
the whole history.

Detail lives in [`firmware/duet3_6xd/README.md`](../firmware/duet3_6xd/README.md);
the branch is `claude/duet3-controller-axis-fddf1a`, not merged to `main`.

---

## Done (2026-08-25)

- [x] Duet 3 6XD bring-up — X/Y/Z_T0 on drivers 0/1/2, Delta ASD-B3 drives
      unchanged, no drive-side rewiring needed
- [x] All three axes home to NPN prox sensors on `io3`/`io4`/`io5`; `G28` works
- [x] Config persisted to the board's SD card, survives power cycling
- [x] Speed ceilings measured: X/Y 200 mm/s @ 1000 mm/s², Z 100 mm/s @ 800
- [x] Reference Cura file end to end — **2148 s** vs the FMC's modelled 4778 s
- [x] Both operating modes proven — preload 2148 s, streamed 2173 s (+1.2 %)
- [x] Static IP 192.168.0.40, Ethernet + HTTP API working
- [x] Max-end protection decided: soft limits only, no second sensor per axis

---

## 1 — Safety. Do these before printing anything real

### [ ] Wire `Dn_ERR` → Delta `ALRM` on all three drivers

**Cheapest safety win available: three wires.** The Delta closes its own
position loop and *would* raise a following-error alarm, but the fault lines are
unwired, so RRF has no idea. Right now the firmware would keep commanding a
drive that has already faulted, and the first sign would be a ruined part.

RRF raises driver-error events automatically once the pins are connected — no
firmware work beyond wiring.

> **Polarity trap.** The `Error Pull u/d Select` jumper is bridged to 5 V
> (pull-up), so RRF expects a fault to pull the line **low**. Delta's `ALRM`
> output is normally a **b-contact — it conducts when healthy**. Check the sense
> on a meter before trusting it, and verify by forcing an alarm on the drive
> (easiest: briefly disconnect a motor phase) and confirming `M122` reports the
> driver as faulted. An untested fault line is worse than none, because it looks
> like protection.

### [ ] Design and build the e-stop chain (evaluation item V4)

**The largest remaining gap.** Intended design: a GPIO-triggered `M112` *plus* a
hardware cut of motor power — the firmware path alone is not an e-stop.

Note what is *not* protecting you today: `P2-015/016/017 = 0x0001` reassigned the
Delta's own `CWL` / `CCWL` / `EMGS` inputs, so the drives have **no limit inputs
and no emergency stop of their own**. Each axis has exactly one minimum sensor,
in one direction, and nothing at the maximum end.

Worth doing before the toolboard adds a heater to the picture.

### [~] Re-check soft limits are doing their job — CHECKED 2026-08-30, one finding fixed

Shakeout confirmed: **RRF `M564 S1` silently clamps to `M208` rather than
refusing** — an out-of-envelope jog traveled to the limit with no error.
Host-side refusal now lives in the Duet adapter (prints were already covered
by the preflight envelope walk). The caveats below still stand.

Max-end protection is `M208` only, by decision. Confirm you are comfortable with
the two caveats: soft limits **only hold while the axis is homed and the datum
is trusted**, and **`G1 H2` raw moves bypass them entirely** — which is what the
homing macros use. Losing steps silently invalidates them.

---

## 2 — Verification

### [ ] Caliper check of 142.857 steps/mm

Home X, `G1 X105 F600`, measure the 100 mm, correct with
`new M92 = 142.857 × (commanded ÷ measured)`. Validated once on the FMC
(commanded 100 mm, measured 99.995) and the step counts land exactly, but it has
not been confirmed independently on this pulse path. Needs a caliper.

Do Y and Z the same way — Z is the one to watch, since its 500 steps/mm comes
from a *different* screw lead (10 mm, not 35).

### [ ] Track down the intermittent USB

Both the Duet and the USB Ethernet dongle dropped and re-enumerated repeatedly
during commissioning. **A drop mid-upload already destroyed one `config.g`
write** and is how the SD card and the repo get out of sync. Ethernet is a
workaround, not a fix.

> Diagnostic that saves time: a built-in NIC losing its cable reports
> `Disconnected`; a removable one **disappears from the adapter list entirely**.
> If the board seems unreachable, check whether the *dongle* is present before
> blaming the Duet.

### [ ] Try raising jerk (`M566`, currently 8 mm/s)

The one untested speed lever. Feedrate gave 26 %, acceleration gave 3 %; on a
vertex-dense path the corner speed limit plausibly dominates what is left.
**It is a mechanical-stress parameter — raise it deliberately, in steps, with
`M122` checked at each**, the same way the speed ceilings were established.

---

## 3 — Tool controller

### [ ] Size, order and wire the Duet 3 Toolboard 1LC

Chosen route. Because CAN-FD sync happens inside RRF's motion planner, it works
identically whether G-code is preloaded or streamed — **the mode choice and the
tool architecture are independent decisions.**

> **Check the sizing before ordering.** A 1LC carries **one driver**, one heater,
> one thermistor and two fans. That covers a filament extruder. If the fiber feed
> is separately driven *and* the cutter needs actuating, one board will not
> stretch — a second 1LC, or a 1XD for the extra driver, would cover it. Also
> note the 6XD still has **drivers 3, 4 and 5 free**, so a plain step/dir
> extruder could go straight on the mainboard with no toolboard at all.

### [~] Configure the tool in RRF — generator ready (2026-08-30), applying gated on safety §1

`M563` tool definition, heater and thermistor setup, and tool-change macros
(`tfree`/`tpre`/`tpost`) for the per-tool Z arrangement. Evaluation item V1 —
the per-tool-Z pattern was never confirmed on the Duet forum.

### [~] Calibrate the real Z bed offset (SRS Q3) — workflow ready (`printer-msu calibrate plane`), lab run pending

`z_bed_offset_mm = 100` is a **placeholder for mid-air dry runs only**. The real
value has to be measured before anything touches a bed. Machine zero is at the
**top**, so `machine_z = z_bed_offset − gcode_z` — see `cura_to_msu.py`.

---

## 4 — Software

### [x] Port `printer_msu` to the Duet HTTP API — SOFTWARE DONE 2026-08-30

Implemented as printer_msu v2 (`docs/PRD_Printer_App_v2.md`): DuetClient +
adapter + drift check, generated board config, preload/stream executors, tool
functions, capability preflight, setup UI. Hardware shakeout pending
(`firmware/duet3_6xd/shakeout_adapter.py`). Original notes kept below. `cura_to_msu.py` is the shape of it: the
parser and interpreter survive as the slicer-to-machine transform, and the app
becomes a front end over `/rr_gcode`, `/rr_reply`, `/rr_upload`, `/rr_model`.
Most of the FMC dispatch layer disappears, because RRF eats G-code directly.

Three properties to carry over, each of which cost real debugging time:

- **Size streaming batches by bytes against the live `buff`, never by line
  count.** `rr_gcode` does *not* error when you exceed the 250-byte buffer — it
  silently drops the overflow and you get a part with a missing segment and no
  indication anywhere. Real lines average 23 bytes, so ~10 fit per request.
- **Verify what is actually running**: check `M32`'s reply *and* read
  `rr_model?key=job.file.fileName`. A swallowed reply once produced five minutes
  of "measurements" from the previous job.
- **Read `job.duration` from the board**, never a host-side timer.

### [x] Decide the mode-selection policy — D7 (2026-08-30): preload default, stream for host-in-the-loop

Preload is the default — nothing in the host path can disturb the print. Reach
for streaming only when the host genuinely needs to stay in the loop: adaptive
fiber logic, sensor response, path generated on the fly.

> **Structural limit, true in both modes.** RRF queues **60 moves** ahead, so
> when the host sends move N the machine is executing move N−60. Host-side timing
> cannot synchronise anything finer than that. The look-ahead that makes the
> board fast is exactly what makes host-timed sync impossible — which is why tool
> sync has to live in the planner.

### [ ] Decide what happens to `libs/fmc-motion`

Finished and hardware-validated, but the controller it drives is retired. It is
currently kept because `printer_msu` and its sim tests still run on it. Once the
Duet port lands, decide: archive, or keep as the sim backend.

---

## 5 — Housekeeping

### [ ] Merge decision for `claude/duet3-controller-axis-fddf1a`

Six commits, `main` is still at `ffff721`. Also unmerged and reference-only:
`claude/m6-auto-run-track-a42d1f` (M6, closed — do not merge) and
`claude/gcode-controller-speed-test-70b71a` (the FMC speed work, `8023a3a`).

### [ ] Run the reference Cura file again after any config change

It is the project's benchmark, and 2148 s is the number to regress against.
Quote **that** file, not `speed_bench.gcode` — `speed_bench`'s collinear raster
overstates the look-ahead gain (6.9× vs the real file's 2.22×).
