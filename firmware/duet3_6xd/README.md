# Duet 3 MB6XD — motion platform bring-up

Replacement for the FMC006GE31, commissioned 2026-08-25. X / Y / Z_T0 all
homing and moving; configuration lives on the board's SD card and survives a
power cycle.

The Delta drives needed **no changes at all** — the existing wiring and drive
parameters carried straight across, which was the premise of the August
evaluation (`docs/Controller_Evaluation_Duet3_6XD.md` on the M6 branch).

Sources: [MB6XD hardware overview](https://docs.duet3d.com/Duet3D_hardware/Duet_3_family/Duet_3_Mainboard_6XD_Hardware_Overview),
[connecting external drivers](https://docs.duet3d.com/en/User_manual/Connecting_hardware/Motors_connecting_external),
and the site's Delta B3 setup sheet (`台达伺服参数调节1(译文).pdf`).

## As-built

| | X | Y | Z_T0 |
|---|---|---|---|
| Driver | `DRIVER_0` | `DRIVER_1` | `DRIVER_2` |
| Screw lead | 35 mm | 35 mm | **10 mm** |
| steps/mm | 142.857 | 142.857 | **500** |
| Endstop (min) | `!io3.in` | `!io4.in` | `!io5.in` |
| Negative direction | toward home | toward home | **up** |
| Span (measured on the FMC) | 987.448 mm | 290.269 mm | 282.306 mm |
| Max speed | 200 mm/s | 200 mm/s | 100 mm/s |
| Acceleration | 1000 mm/s² | 1000 mm/s² | 800 mm/s² |

Board: Duet 3 MB6XD v1.02, RRF 3.6.1, standalone, ID
`0JD2M-999AL-D2PS0-6JTDA-3S46R-1NKZ1`. VIN 24 V.

Z's 500 steps/mm is easy to copy wrong from X/Y — its lead is 10 mm, not 35.

## What the 6XD is

No on-board drivers. It emits step/dir/enable only: **5 V single-ended, 20 mA
max**, and the Step/Dir outputs are **active low, negative-going** (idle high,
pulsed low). `M906` and `M350` are inert — current, microstepping and the servo
loop all live in the Delta drive.

## Wiring

### Drivers → Delta ASD-B3-0421-L, CN1 low-speed pulse input

| `DRIVER_n` pin | Delta CN1 | |
|---|---|---|
| 1 `5V_EXT` | **39** *and* **43** | `SIGN`+, `PULSE`+ |
| 2 `Dn_STEP-` | **41** | `/PULSE` |
| 3 `Dn_DIR-` | **37** | `/SIGN` |
| 4 `Dn_EN-` | *not connected* | drive self-enables — see below |
| 5 `Dn_ERR` | *not connected* | **open item**, see Safety |
| 6 `GND` | *not connected* | opto return goes through `Dn_*-` |

No series resistor — this is the 5 V path. **Never put 24 V on CN1 41/43.**
Do not use the high-speed pins (38/40/42/44): they are line-driver only and the
6XD is single-ended. Open-collector ceiling is 200 kpps.

### Endstops — NPN proximity sensors, 24 V

Brown → +24 V. Blue → external 0 V **and Duet GND** (the common ground is
mandatory for an NPN output to sink). Black → the input pin.

24 V never reaches the pin (an NPN output only sinks) and MB6XD non-isolated
inputs are rated −1 V to +30 V regardless. Those inputs carry a permanent 27K
pull-up to +3.3 V, so the sensor idles HIGH and pulls LOW when triggered —
hence the **`!`** prefix. Released reads `not stopped`.

## Delta drive parameters

Set per the site's setup sheet; verify before trusting this config.

| Param | Value | Meaning |
|---|---|---|
| `P1-45` | **5000** | electronic gear denominator → 5000 pulses/rev |
| `P2-010` | `0x0001` | DI1 = SON as a **b-contact** → self-enable |
| `P2-015/016/017` | `0x0001` | DI6-8 reassigned **off** CWL/CCWL/EMGS |
| `P1-01` | PT | position control from external pulses |
| `P1-00` | Pulse + Direction, low-speed input | pulse format |

`P1-45` default is **100000**, and the sheet's trap is that editing only the low
five digits leaves `105000` — the axis then crawls at 1/21 speed. `P1-01` and
`P1-00` need a drive power cycle to take effect.

Because DI1 is a b-contact, **the servo enables itself with no enable wire**,
which is why there are only four conductors per drive. Consequence: `M18` /
`M84` cannot drop these motors. They are live whenever the drive is powered.

## Board jumpers

| Jumper | Position | Effect |
|---|---|---|
| Driver Enable Polarity | any | irrelevant — `Dn_EN-` unwired |
| Error Pull u/d Select | **bridged to 5 V = pull-up** | fault is signalled by pulling **low**; unconnected `Dn_ERR` idles no-fault |
| Int 5V EN | fitted (stock) | on-board 5 V regulator feeds the driver headers |

## Files

Everything here is also on the card under `/sys/`.

- `config.g` — drivers, scale, limits, endstops
- `homeall.g`, `homex.g`, `homey.g`, `homez.g` — `G28` support
- `duet_send.py` — send G-code over USB; auto-detects the board by USB ID

The stock `config.g.bak` is left intact on the card.

## Talking to the board

USB is the reliable path: the board enumerates as `VID_1D50 / PID_60EE` (an
erased board would appear as an Atmel SAM-BA device instead). Baud is ignored —
it is a USB CDC device.

```bash
pip install pyserial
```

```bash
python duet_send.py M115 M122 "M569 P0"
```

```bash
python duet_send.py --file config.g
```

Files can be written to the card over the same link with
`M28 "/sys/<name>"` … lines … `M29`, so the card never has to come out.

**Cura holds COM ports open.** Its USB-printing plugin auto-probes them, so
"Access to the port is denied" while Windows shows the device Present/OK means
close Cura.

Ethernet is set to DHCP. It was a static 192.168.1.14 out of the box, which is
the wrong subnet — the lab ran 192.168.0.x for the FMC.

## Gotchas worth keeping

**Never put an endstop on `io0.in`.** It is physically the same pin as
`serial0.rx` (PanelDue UART), and the stock `config.g` enables it with
`M575 P1 S4 B57600`. RRF accepts the endstop without error and reports the pin
as `!(io0.in,serial0.rx)`, but the UART receiver consumes the sensor's edges:
the endstop sits in one state, later sits in the *other* state, and never
appears to transition under polling. The tell is `M122`'s `Aux0 errors` counter
climbing — we watched it go 0,0,0 → 0,0,297 while the sensor was being toggled,
which proved the signal was arriving and being eaten. `M575` has no off switch
(`S-1` → "expected number after 'S'"), so the only cure is to never enable it.
This config omits `M575` entirely; `M575 P1` now reports "Channel 1 is
disabled". `io1.in` is shared with RS485 on v1.02 and `io2.in` has the I2C
bypass jumper — prefer `io3`–`io8`.

**`M584` resets axis positions and clears `M574`.** Adding Y and Z silently
wiped X's endstop config and its homed datum, with no error. Always order
`M584` before `M574`.

**Z hits the pulse-rate ceiling first.** At 500 steps/mm, 100 mm/s is already
50 kHz against the `T5:5` 100 kHz ceiling, so Z's absolute maximum is 200 mm/s.
X and Y at 142.857 steps/mm would not hit it until ~700 mm/s.

**A clean `M114`/`M122` proves only that the board emitted pulses.** There is no
feedback path from the Delta, so identical output would appear into a
disconnected cable. Physical confirmation has to come from a human or a
measurement — the same trap that produced the false "first real print" on the
FMC in August.

## Measured results, 2026-08-25

Speed ceilings were stepped up per axis with `M122` checked at every level —
**zero hiccups, zero late steps, zero underruns, exact position return, no
Delta alarm** throughout.

```
X   50 / 100 / 200 mm/s      1400 mm round trip   28.9 / 14.9 / 7.9 s
Y   50 / 100 / 200 mm/s       380 mm round trip    8.4 /  4.7 / 2.8 s
Z   25 /  50 / 100 mm/s       360 mm round trip   15.2 /  7.9 / 4.4 s
G28 (all three)  18.8 s      G28 X Y from park  10.5 s
```

`speed_bench.gcode` — 380 raster segments, 1050 mm of path — run from the
board's own SD card:

| | total | raster only | ideal |
|---|---|---|---|
| **FMC**, 100 mm/s accel 300 | 181 s | — | — |
| **Duet**, 100 mm/s accel 300 | 26.4 s | 15.9 s | 10.5 s |
| **Duet**, 200 mm/s accel 1000 | 22.8 s | 12.3 s | 5.3 s |

392/392 moves, all counters zero at both levels.

**Read that comparison carefully — two effects are bundled and only one
generalises.**

1. *Real for every file:* the host round trip is gone. The FMC carried 242 ms
   of overhead per segment even after all the streaming optimisation; RRF runs
   the file off its own SD with nothing in the loop and lands ~14 ms/segment
   above the ideal trapezoid.
2. *Overstated here:* the blending gain. `speed_bench`'s raster runs are
   collinear, so RRF fuses them into single long moves while the FMC stopped at
   every vertex. The project's earlier analysis put the **real Cura file at
   only 1.19–1.57×** between stop-and-go and full blending. **Quote the Cura
   file, not this benchmark, for any headline claim.**

Doubling top speed took the raster only 15.9 → 12.3 s (1.29×, not 2×), because
90° corners decelerate to the jerk limit and a segment of length L only reaches
`sqrt(a·L)` — 141 mm/s on a 20 mm segment at accel 1000. **This bench is an
acceleration test; its feed number is largely cosmetic.**

## Safety — current state

This is the honest picture, not a reassuring one.

- **No e-stop chain.** Eval item V4, still open. GPIO-triggered `M112` plus a
  hardware motor-power cut was the intended design.
- **No hardware maximum limits, by decision.** The Delta's own `CWL` / `CCWL` /
  `EMGS` inputs were reassigned away by `P2-015/016/017`, so the drives have no
  limit inputs at all. Maximum-end protection is `M208` soft limits only.
  Note this is a *choice not to wire more sensors*, not a board limitation —
  RRF supports high-end endstops (`M574 X2 S1 P"..."`) and `io2`, `io6`, `io7`,
  `io8` are free. **Soft limits only hold while the axis is homed and the datum
  is trusted; they do not protect against lost steps, and `G1 H2` raw moves
  bypass them entirely — which is what the homing macros use.**
- **`Dn_ERR` unwired on all three drivers.** The Delta closes its own position
  loop and would alarm on a following error, but RRF has no idea when that
  happens. These inputs exist for exactly this. Given the pull-up jumper, the
  drive's `ALRM` output must pull **low** on fault — and Delta's `ALRM` is
  normally a b-contact that conducts when healthy, so check the sense before
  trusting it.

## Open items

- Wire `Dn_ERR` → Delta `ALRM` so drive faults reach RRF.
- Design the e-stop chain (V4).
- Caliper check of 142.857 steps/mm on the new pulse path. Validated once on
  the FMC (commanded 100 mm, measured 99.995) and the step counts land exactly,
  but it has not been confirmed independently here.
- Run the reference Cura file end to end — that is the number that actually
  predicts print time.
- `printer_msu` integration: the app becomes a thin front-end over the Duet
  HTTP API (`/rr_gcode`, `/rr_reply`).
