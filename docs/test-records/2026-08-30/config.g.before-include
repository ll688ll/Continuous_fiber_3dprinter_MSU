; ---------------------------------------------------------------------------
; Duet 3 MB6XD - MSU continuous fiber printer
; X/Y/Z_T0 on DRIVER_0/1/2 -> Delta ASD-B3 servo drives, CN1 low-speed pulse
; Bring-up config, 2026-08-25. Every value below was verified on hardware.
;
; SAFETY STATE - do not treat this as a production machine config:
;   - NO E-STOP CHAIN (eval item V4). Still the largest gap.
;   - NO HARDWARE MAXIMUM LIMITS, BY DECISION (Jacky, 2026-08-25). The Delta's
;     own CWL/CCWL/EMGS inputs are reassigned away (P2-015/016/017 = 0x0001),
;     so the drives have no limit inputs at all. Max-end protection is the
;     M208 soft limits below, and nothing else.
;       This is a choice not to wire more sensors, NOT a board limitation:
;       RRF supports high-end endstops (M574 X2 S1 P"...") and io2/io6/io7/io8
;       are free. Recorded so nobody later assumes it was impossible.
;       CAVEAT: soft limits only hold while the axis is homed and the datum is
;       trusted. They do not protect against lost steps, and G1 H2 raw moves
;       bypass them entirely - which is what the homing macros use.
;   - Dn_ERR unwired on all three drivers, so a Delta following-error alarm is
;     invisible to RRF. Wiring it is the cheapest safety win available.
;   - no tool, no heaters, no extruder.
; ---------------------------------------------------------------------------

M111 S0                          ; debug off
G21                              ; millimetres
G90                              ; absolute moves
M550 P"msu-6xd"                  ; machine name

; --- Network --------------------------------------------------------------
; STATIC, and it has to be: the lab segment is isolated with NO DHCP SERVER.
; The workstation's 192.168.0.38 is manually configured (PrefixOrigin Manual,
; DHCP disabled on the interface, no default gateway, empty ARP table), left
; over from the FMC at 192.168.0.30. A DHCP request here waits forever and the
; board sits at 0.0.0.0.
;   .30 = retired FMC, .38 = workstation, .40 = this board.
; No gateway is needed on an isolated segment.
M553 P255.255.255.0
M554 P0.0.0.0
M552 P192.168.0.40 S1

; NOTE: M575 is deliberately ABSENT. It would claim serial0.rx, which is the
; same physical pin as io0.in - see the endstop section below.

; --- Drivers --------------------------------------------------------------
; S1 on all three CONFIRMED on hardware: negative is toward home on X and Y,
; and negative is UP on Z (SRS D11 - machine zero at the top).
; R is irrelevant: the enable lines are not wired. The Delta drives self-enable
; via DI1 = SON as a b-contact (P2-010 = 0x0001), so M18/M84 cannot drop them.
; T = step pulse : interval : DIR setup : DIR hold, microseconds. The Delta
; low-speed opto input tops out at 200 kpps; 5:5 gives a 100 kHz ceiling.
M569 P0 S1 R1 T5:5:10:10         ; X
M569 P1 S1 R1 T5:5:10:10         ; Y
M569 P2 S1 R1 T5:5:10:10         ; Z_T0

M584 X0 Y1 Z2                    ; CAUTION: M584 resets axis positions AND
                                 ; clears M574 endstop config. Always set
                                 ; M584 BEFORE M574, never after.

; --- Scale ----------------------------------------------------------------
; Delta electronic gear P1-45 = 5000 -> 5000 pulses/rev on every drive.
;   X, Y: 35 mm ballscrew lead -> 5000/35 = 142.857 steps/mm
;   Z_T0: 10 mm lead           -> 5000/10 = 500     steps/mm
; Verified against step counts on hardware: Z moved exactly 10000 steps for
; 20.000 mm. Matches the FMC's own lead/div parameters.
M92 X142.857 Y142.857 Z500

; --- Motion limits --------------------------------------------------------
; MEASURED ON HARDWARE 2026-08-25, stepped up in three stages per axis with
; M122 checked at each: zero hiccups, zero late steps, zero underruns, exact
; position return, no Delta alarm. Conservative vs the FMC's 225 mm/s / 1125.
;
; Z's ceiling is a PULSE-RATE limit, not a mechanical one: at 500 steps/mm,
; 100 mm/s is already 50 kHz against the T5:5 100 kHz ceiling, so Z's absolute
; maximum is 200 mm/s. X/Y at 142.857 steps/mm would not hit it until 700 mm/s.
M566 X480 Y480 Z120              ; jerk, mm/min (8, 8, 2 mm/s). X/Y match the
                                 ; M205 X8 Y8 in the reference Cura file.
M203 X12000 Y12000 Z6000         ; max speed, mm/min (200, 200, 100 mm/s)
M201 X1000 Y1000 Z800            ; acceleration, mm/s^2

; Spans measured on the FMC at true scale, 2026-08-13.
M208 X0 Y0 Z0 S1                 ; minima
M208 X987.448 Y290.269 Z282.306 S0

; NOTE: M350 (microstepping) and M906 (current) do NOT apply on the 6XD. Both
; live in the Delta drive.

; --- Endstops -------------------------------------------------------------
; NPN proximity sensors, 24 V. Brown -> +24 V, Blue -> external 0 V AND Duet
; GND (the common ground is mandatory for an NPN output to sink), Black -> the
; input pin. All three are minimum/home switches (SRS D11).
;
; The "!" is correct and confirmed: MB6XD non-isolated inputs carry a permanent
; 27K pullup to +3.3 V, so an NPN output idles HIGH and pulls LOW when
; triggered. Released reads "not stopped".
;
; DO NOT USE io0.in ON THIS BOARD. It is physically the same pin as serial0.rx
; (PanelDue UART). RRF accepts an endstop there and reports the pin as
; "!(io0.in,serial0.rx)", but with the UART enabled the receiver consumes the
; sensor's edges and the endstop reads garbage - it sits in one state, later
; sits in the other, and never appears to transition. The tell is M122's
; "Aux0 errors" counter climbing. M575 has no off switch (S-1 is rejected),
; so the cure is to never enable it. Hence no M575 in this file, and the X
; sensor lives on io3.in.
M574 X1 S1 P"!io3.in"
M574 Y1 S1 P"!io4.in"
M574 Z1 S1 P"!io5.in"

; M564 stays at its DEFAULTS: no movement before homing, none outside limits.
; G1 H1 (move-until-endstop) and G1 H2 (raw motor move) both bypass that on
; their own, which is what the homing macros use.
