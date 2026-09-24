// Preview: helmet on the mock head on the mock body. Not for printing.
include <bb8_dimensions.scad>
use <bb8_pilot_helmet.scad>

bb8_body_mock();
bb8_head_mock();
color([0.85, 0.35, 0.1]) helmet();
