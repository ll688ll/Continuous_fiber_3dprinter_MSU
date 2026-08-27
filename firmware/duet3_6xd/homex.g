; Home X to its minimum sensor (io3.in). Negative is toward home - confirmed.
G91                              ; relative
G1 H2 X5 F600                    ; lift off the switch in case we start on it
G1 H1 X-1000 F1800                ; fast approach, bounded past the 987.448 span
G1 H2 X5 F600                    ; back off
G1 H1 X-10 F150                  ; slow re-approach for repeatability
G1 H2 X5 F600                    ; clear the sensor - matches the FMC's
                                 ; clear_offset_mm = 5.0 convention
G90                              ; absolute
