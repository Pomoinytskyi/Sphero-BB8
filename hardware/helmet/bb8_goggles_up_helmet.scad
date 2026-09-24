// Helmet with the goggles pushed up on the forehead, Copilot style.
//
//   * open face so the big eye and the small lens stay visible,
//   * a pair of round goggle lenses with raised frames and a bridge,
//     sitting on the forehead just above the brow,
//   * a strap band that runs from the goggles round the back of the helmet,
//   * the antenna slot up the back and grip bumps inside the rim.
//
// The lens floors are recessed 1 mm inside the frames: paint them dark, or
// print the helmet in two colours with a swap at the lens floor height.
// Print rim-down; the frames and strap are low and need no support.

include <helmet_common.scad>

// ---- Goggles-up parameters --------------------------------------------------
face_half_az = 56;     // half-width of the face opening, degrees
face_top_z   = 18.5;   // brow line; must clear the top of the big eye

goggle_el    = 62;     // elevation of the lens centres; lower edge lands just above the brow
lens_az      = 28;     // each lens this far either side of the front
lens_od      = 11;     // frame outer diameter
lens_id      = 8.5;    // frame inner diameter
frame_h      = 2.2;    // frame height above the shell
floor_h      = 1.2;    // lens floor height above the shell (recess = frame_h - floor_h)

bridge_w     = 3;      // bridge between the lenses
bridge_h     = 1.8;

strap_w      = 5;      // strap band width
strap_h      = 1.0;    // strap band height above the shell
strap_tilt   = 22;     // band drops toward the back by this angle (front at goggle height, back at ~20 deg)

module lens_frames() {
    for (s = [-1, 1]) on_head(s * lens_az, goggle_el, r_out - 1) {
        difference() {
            cylinder(h = frame_h + 1, d = lens_od);
            translate([0, 0, -1]) cylinder(h = frame_h + 3, d = lens_id);
        }
        cylinder(h = floor_h + 1, d = lens_id + 0.1);
    }
}

module bridge() {
    hull() for (s = [-1, 1])
        on_head(s * (lens_az - 9), goggle_el, r_out - 1)
            cylinder(h = bridge_h + 1, d = bridge_w);
}

module strap() {
    // A band around the shell in a plane tilted so it sits at goggle height
    // in front and lower at the back.
    z_front = (r_out + strap_h) * sin(goggle_el);
    x_front = (r_out + strap_h) * cos(goggle_el);
    zc = -x_front * sin(strap_tilt) + z_front * cos(strap_tilt);
    intersection() {
        difference() { sphere(r = r_out + strap_h); sphere(r = r_out - 0.5); }
        rotate([0, -strap_tilt, 0])
            translate([-50, -50, zc - strap_w / 2]) cube([100, 100, strap_w]);
    }
}

module helmet() {
    difference() {
        helmet_body() { lens_frames(); bridge(); strap(); }
        face_cut(face_half_az, face_top_z);
    }
}

helmet();
