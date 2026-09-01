; homey.g - GENERATED from profile 'MSU lab - Duet 3 6XD' - do not hand-edit
; homes Y to its minimum switch
; regenerate + upload with: printer-msu apply-config
G91
G1 H2 Y5 F600
G1 H1 Y-310 F1800 ; bounded past the 290.269 mm span
G1 H2 Y5 F600
G1 H1 Y-10 F150 ; slow re-approach
G1 H2 Y5 F600
G90
