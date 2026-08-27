; Home Z_T0 to its minimum sensor (io5.in).
; NEGATIVE IS UP (SRS D11: machine zero at the top, away from the print plane).
G91
G1 H2 Z5 F300                    ; move DOWN 5 mm to lift off the switch
G1 H1 Z-290 F1800                 ; fast approach upward, past the 282.306 span
G1 H2 Z5 F300
G1 H1 Z-10 F150
G1 H2 Z5 F300                    ; clear the sensor
G90
