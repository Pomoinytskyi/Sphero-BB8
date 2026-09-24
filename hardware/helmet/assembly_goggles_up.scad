// Preview: goggles-up helmet on the mock head on the mock body.
include <bb8_dimensions.scad>
use <bb8_goggles_up_helmet.scad>

bb8_body_mock();
bb8_head_mock();
color([0.85, 0.35, 0.1]) helmet();
