; XY simultaneity demo — long diagonal sweeps, no Z, well inside the envelope.
; Both axes must visibly move TOGETHER on every G1 (coordinated Line_2Axis).
G21
G90
G28 X Y
G0 X100 Y60 F7500
G1 X260 Y200 F2400  ; diagonal up-right, ~213 mm path
G1 X100 Y60         ; back down the same line
G1 X260 Y200
G1 X100 Y60
G1 X260 Y60 F2400   ; pure X stroke (compare by eye)
G1 X260 Y200        ; pure Y stroke
G1 X100 Y60         ; diagonal home leg
G0 X50 Y50 F7500
M2
