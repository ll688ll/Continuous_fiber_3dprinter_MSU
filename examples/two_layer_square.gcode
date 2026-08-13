; Two-layer 20 mm square with Z moves — exercises tool-Z routing (T0 -> Z_T0).
; Z heights sit at ~10 mm, well inside the Z axis's working range, because
; true bed-zero offsets are a calibration topic (SRS Q3, deferred).
G21
G90
M82
G28              ; home ALL mapped axes (X, Y, and T0's Z)
G92 E0
G1 Z10.0 F600    ; first layer height
G0 X10 Y10
G1 X30 Y10 E1.0 F1500
G1 X30 Y30 E2.0
G1 X10 Y30 E3.0
G1 X10 Y10 E4.0
G1 Z10.4 F600    ; layer change
G1 X30 Y10 E5.0 F1500
G1 X30 Y30 E6.0
G1 X10 Y30 E7.0
G1 X10 Y10 E8.0
G0 Z15           ; lift clear of the part
G0 X5 Y5         ; park
M84
M2
