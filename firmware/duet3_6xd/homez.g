; homez.g - GENERATED from profile 'MSU lab - Duet 3 6XD' - do not hand-edit
; homes Z_T0 to its minimum switch (negative is UP - machine zero at the top)
; regenerate + upload with: printer-msu apply-config
G91
G1 H2 Z5 F300
G1 H1 Z-310 F1800 ; bounded past the 282.306 mm span
G1 H2 Z5 F300
G1 H1 Z-10 F150 ; slow re-approach
G1 H2 Z5 F300
G90
