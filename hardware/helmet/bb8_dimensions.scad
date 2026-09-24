// Sphero BB-8 (model R001) reference dimensions, in millimetres.
//
// Shared by every model in this directory. The origin is the centre of the
// sphere that the head dome is cut from; +Z is up, +X is forward (the
// direction the big eye looks). The body ball sits below the head.
//
// Published figures (Sphero tech specs, retail listings):
//   overall height 114 mm, width 73 mm, ~200 g. Body ball ~73 mm diameter.
// The head itself is not published anywhere we could find. head_d and head_h
// are derived: the movie droid's head/body ratio is 0.58 (rimstar.org), the
// photo of this unit gives ~0.62 after perspective, and 114 - 73 (ball) - 2
// (air gap) - ~12 (antenna above the crown) leaves ~27 mm for the dome. Treat
// them as +/- 2 mm and confirm with fit_rings.scad before printing the helmet.

body_d       = 73;    // ball diameter
head_d       = 45;    // diameter of the sphere the head dome is cut from
head_h       = 27;    // head height, silver rim to crown (taller than a hemisphere)
head_gap     = 2;     // air gap between the head rim and the ball
crown_flat_d = 14;    // the raised flat cap on top of the dome

// Big black eye, high on the front.
eye_d        = 13;
eye_bulge    = 4;     // how far it stands proud of the dome
eye_el       = 28;    // elevation above the equator, degrees
eye_az       = 0;

// Small lens, low on the front and to the droid's left of the big eye. Its
// bottom edge touches the orange band just above the silver rim.
lens_d       = 8;
lens_bulge   = 2;
lens_el      = 3;
lens_az      = 22;

// Two antennas on the rear slope of the head, one tall and one short, a
// close pair about 3 mm apart. From the plan-view photo of the bare head:
// they root just outside the orange band, low on the dome (elevation ~30
// degrees), and 12 to 15 degrees toward the droid's left of the rear centre
// line. The tall one is nearer the side, the short one nearer the centre.
antenna_d        = 1.3;
antenna_long_l   = 16;
antenna_long_el  = 30;
antenna_long_az  = 163;
antenna_short_l  = 8;
antenna_short_el = 30;
antenna_short_az = 171;

// Derived.
head_r   = head_d / 2;
rim_z    = head_r - head_h;                       // rim sits below the equator
rim_d    = 2 * sqrt(head_r * head_r - rim_z * rim_z);
crown_z  = sqrt(head_r * head_r - (crown_flat_d / 2) * (crown_flat_d / 2));
body_top = rim_z - head_gap;                      // z of the top of the ball
body_z   = body_top - body_d / 2;                 // z of the ball centre

// Place a child on the head sphere surface, its +Z along the surface normal,
// at the given azimuth (0 = front, 90 = left) and elevation, at radius r.
module on_head(az, el, r = head_r) {
    rotate([0, 0, az]) rotate([0, 90 - el, 0]) translate([0, 0, r]) children();
}

// A stand-in for the real head, for previews and clearance checks.
module bb8_head_mock() {
    color("white") difference() {
        intersection() {
            sphere(r = head_r, $fn = 120);
            translate([-head_r, -head_r, rim_z]) cube([head_d, head_d, crown_z - rim_z]);
        }
    }
    color("silver") translate([0, 0, rim_z])
        cylinder(h = 1.2, r = rim_d / 2 - 0.2, $fn = 120);
    color("black") on_head(eye_az, eye_el, head_r + eye_bulge - eye_d / 2)
        sphere(d = eye_d, $fn = 48);
    color("black") on_head(lens_az, lens_el, head_r - 1)
        cylinder(h = lens_bulge + 1, d = lens_d, $fn = 32);
    color("black") on_head(antenna_long_az, antenna_long_el, head_r - 1)
        cylinder(h = antenna_long_l + 1, d = antenna_d, $fn = 12);
    color("black") on_head(antenna_short_az, antenna_short_el, head_r - 1)
        cylinder(h = antenna_short_l + 1, d = antenna_d, $fn = 12);
}

module bb8_body_mock() {
    color("white") translate([0, 0, body_z]) sphere(d = body_d, $fn = 120);
}
