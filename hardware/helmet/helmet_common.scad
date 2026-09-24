// Shared shell, antenna slot and grip bumps for every BB-8 helmet variant.
//
// include<> this file, then override any parameter below it. Coordinates:
// origin at the centre of the head sphere, +Z up, +X toward the big eye,
// azimuth 0 = front, 90 = the droid's left, 180 = back.

include <bb8_dimensions.scad>

clearance     = 0.8;   // radial air gap between head and helmet
wall          = 1.6;   // shell thickness (4 perimeters at 0.4 mm)
edge_lift     = 0.5;   // helmet edge stops this far above the head's silver rim

slot_az       = 167;   // azimuth of the antenna slot centre line (the pair sits left of dead rear)
slot_w        = 11;    // antenna slot width; the pair spans ~3.5 mm
slot_top_el   = 50;    // slot runs from the rim up to this elevation (+ rounded end)

grip_bumps    = true;  // small bumps inside the rim for a light friction fit
bump_d        = 1.6;
bump_h        = 0.3;   // interference per bump
bump_az       = [105, 145, 215, 255];

$fn = 96;

r_in     = head_r + clearance;
r_out    = r_in + wall;
bottom_z = rim_z + edge_lift;

// Rotate a child so its +Z points along the surface normal at (az, el).
// The child's 2D y axis ends up horizontal, its 2D x axis points down the dome.
module aimed(az, el) {
    rotate([0, 0, az]) rotate([0, 90 - el, 0]) children();
}

module cavity() { sphere(r = r_in); }

module below_rim_cut() {
    translate([-100, -100, bottom_z - 100]) cube([200, 200, 100]);
}

module antenna_slot_cut() {
    z_top = r_out * sin(slot_top_el);
    rotate([0, 0, slot_az - 180]) union() {
        translate([-100, -slot_w / 2, bottom_z - 1])
            cube([100, slot_w, z_top - bottom_z + 1]);
        rotate([0, slot_top_el - 90, 0]) cylinder(h = 100, d = slot_w);
    }
}

module grip_bump_set() {
    for (az = bump_az)
        on_head(az, -2, r_in + bump_d / 2 - bump_h) sphere(d = bump_d, $fn = 24);
}

module wedge(half_az, r) {
    // Solid sector of a cylinder in front (+X), |azimuth| <= half_az.
    rotate([0, 0, -half_az]) rotate_extrude(angle = 2 * half_az)
        square([r, 200], center = false);
}

// Open face: everything in front, below top_z, within |az| <= half_az, but
// only inside the shell so that anything standing proud of it survives.
module face_cut(half_az, top_z) {
    intersection() {
        sphere(r = r_out + 0.05);
        translate([0, 0, -100]) wedge(half_az, 100);
        translate([-100, -100, -100]) cube([200, 200, 100 + top_z]);
    }
}

// Outer additions go in children(); the cavity, rim, slot and any extra cuts
// passed through extra_cuts() are removed afterwards, then the bumps added.
module helmet_body() {
    union() {
        difference() {
            union() { sphere(r = r_out); children(); }
            cavity();
            below_rim_cut();
            antenna_slot_cut();
        }
        if (grip_bumps) grip_bump_set();
    }
}
