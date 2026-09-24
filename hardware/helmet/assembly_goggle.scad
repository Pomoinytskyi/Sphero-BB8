// Preview: goggle helmet and lens on the mock head on the mock body.
include <bb8_dimensions.scad>
use <bb8_goggle_helmet.scad>

bb8_body_mock();
bb8_head_mock();
color([0.85, 0.35, 0.1]) helmet();
color([0.5, 0.8, 1.0, 0.45]) lens();
