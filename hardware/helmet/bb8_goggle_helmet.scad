// Goggle helmet for the Sphero BB-8 head, in the spirit of the GitHub
// Copilot mark: a smooth closed dome with one wide, thick-rimmed goggle
// lens across the face.
//
//   * closed shell all the way round, no open face,
//   * a single stadium-shaped window over the big eye and the small lens,
//   * a raised bezel around the window, tall enough that a lens seated in
//     it clears the eye, which stands ~4 mm proud of the head,
//   * an optional clear lens (goggle_lens.scad) that drops into a rebate
//     in the bezel; print it in transparent PETG or leave the window open,
//   * the antenna slot up the back and grip bumps inside the rim.
//
// Print rim-down. The bezel is a 90-degree overhang only on its underside
// arc, 2.4 mm wide, which prints fine at 0.12 mm layers.

include <helmet_common.scad>

// ---- Goggle parameters ------------------------------------------------------
win_w     = 33;    // window width, measured across the face
win_h     = 22;    // window height; big eye at the top, small lens at the bottom
win_el    = 17;    // elevation of the window centre (big eye is at 28, small lens at 3)
win_az    = 6;     // nudged toward the small lens, which sits at azimuth 22

bezel_w   = 2.4;   // frame width around the window
bezel_h   = 3.5;   // frame height above the shell; sets the lens clearance over the eye
lens_t    = 1.0;   // lens thickness
lens_lip  = 1.0;   // how far the lens overlaps the bezel in its rebate
lens_gap  = 0.15;  // clearance between lens and rebate, per side

lens_r_in = r_out + bezel_h - lens_t;     // underside of the lens

// Window outline in the tangent plane. Long axis along 2D y so that aimed()
// lays it horizontally across the face.
module win2d(off = 0) {
    offset(r = off) hull()
        for (s = [-1, 1]) translate([0, s * (win_w - win_h) / 2]) circle(d = win_h);
}

// A straight prism of the outline, along the normal at the window centre,
// from radius `from` outward.
module beam(off = 0, from = 0, len = 60) {
    aimed(win_az, win_el) translate([0, 0, from]) linear_extrude(len) win2d(off);
}

module bezel() {
    intersection() {
        difference() { sphere(r = r_out + bezel_h); sphere(r = r_out - 0.5); }
        beam(bezel_w);
    }
}

module lens_rebate_cut() {
    difference() { beam(lens_lip + lens_gap); sphere(r = lens_r_in); }
}

module helmet() {
    difference() {
        helmet_body() { bezel(); }
        beam(0);
        lens_rebate_cut();
    }
}

// The optional clear lens: a spherical cap that seats in the bezel rebate.
module lens() {
    intersection() {
        difference() { sphere(r = r_out + bezel_h); sphere(r = lens_r_in); }
        beam(lens_lip);
    }
}

// Laid dome-up on the bed for printing, lowest rim points at z = 0.
module lens_flat() {
    z_low = sqrt(lens_r_in * lens_r_in - (win_w / 2 + lens_lip) * (win_w / 2 + lens_lip));
    translate([0, 0, -z_low]) rotate([0, win_el - 90, 0]) rotate([0, 0, -win_az]) lens();
}

part = "helmet";   // goggle_lens.scad includes this file and sets part = "lens"
if (part == "lens") lens_flat(); else helmet();
