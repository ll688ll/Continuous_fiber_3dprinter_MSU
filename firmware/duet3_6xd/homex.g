; homex.g - GENERATED from profile 'MSU lab - Duet 3 6XD' - do not hand-edit
; homes X to its minimum switch
; regenerate + upload with: printer-msu apply-config
G91
G1 H2 X5 F600
G1 H1 X-1050 F1800 ; bounded past the 987.448 mm span
G1 H2 X5 F600
G1 H1 X-10 F150 ; slow re-approach
G1 H2 X5 F600
G90
