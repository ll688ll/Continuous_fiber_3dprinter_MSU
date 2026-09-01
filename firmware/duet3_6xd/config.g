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

; --- Axes, scale, limits, endstops: GENERATED from the machine profile -----
; Since 2026-08-30 (printer_msu v2, decision D6) the axis configuration lives
; in configs/duet_lab.json and is generated into /sys/msu_machine.g by
;   printer-msu apply-config --profile configs/duet_lab.json
; The include carries: one M584 (first - it resets datums and clears M574),
; M92, M566, M203, M201, M208 min+max, M574 endstops. Edit the PROFILE, never
; the generated file; every connect cross-checks board vs profile (drift check).
;
; Wiring/measurement rationale (leads, ceilings, NPN endstop wiring) lives in
; firmware/duet3_6xd/README.md and the profile. Two warnings that must outlive
; any regeneration:
;   - DO NOT USE io0.in for anything. It is physically the same pin as
;     serial0.rx; with the UART enabled the receiver eats the sensor's edges
;     and the endstop reads garbage (tell: M122 "Aux0 errors" climbing).
;     M575 has no off switch, so it stays absent from this file. The profile
;     validator hard-rejects io0.in.
;   - M350/M906 do NOT apply on the 6XD; microstepping and current live in
;     the Delta drives (P1-45 = 5000).
M98 P"/sys/msu_machine.g"

; M564 stays at its DEFAULTS: no movement before homing, none outside limits.
; G1 H1 (move-until-endstop) and G1 H2 (raw motor move) both bypass that on
; their own, which is what the homing macros use.
