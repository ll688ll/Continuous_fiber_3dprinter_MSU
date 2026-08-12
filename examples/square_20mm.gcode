; 20 mm square test path — slicer-style file for the motion-only v1 core.
; Tool/temperature/fan/extrusion commands below are skipped with warnings
; (printer PRD decision D2); the XY motion streams to the machine.
M104 S200        ; set hotend temp (deferred)
M109 S200        ; wait for temp (deferred)
G21              ; millimeters
G90              ; absolute positioning
M82              ; absolute extrusion
G28 X Y          ; home the stage
G92 E0
G1 F1500
G0 X10 Y10       ; travel to start corner
G1 X30 Y10 E1.0  ; bottom edge
G1 X30 Y30 E2.0  ; right edge
G1 X10 Y30 E3.0  ; top edge
G1 X10 Y10 E4.0  ; left edge, back to start
M106 S255        ; fan on (deferred)
G0 X5 Y5         ; park
M107             ; fan off (deferred)
M84              ; steppers off (skipped)
M2               ; end of program
