// Rebel-pilot style helmet for the Sphero BB-8 head.
//
// A thin spherical shell that drops over the head dome, with:
//   * an open face so the big eye and the small lens stay visible,
//   * a drooping visor peak over the brow,
//   * a slot up the back for the two antennas,
//   * side comm-pods, and optional grip bumps inside the rim.
//
// Print it rim-down (open side on the bed). Every overhang is either < 50
// degrees or the last few millimetres of the crown. See README.md.
//
// All head figures come from bb8_dimensions.scad. Check them with
// fit_rings.scad first: the helmet is only as good as head_d.

include <bb8_dimensions.scad>

// ---- Helmet parameters ------------------------------------------------------
clearance     = 0.8;   // radial air gap between head and helmet
wall          = 1.6;   // shell thickness (4 perimeters at 0.4 mm)
edge_lift     = 0.5;   // helmet edge stops this far above the head's silver rim

face_half_az  = 58;    // half-width of the face opening, degrees
face_top_z    = 18.5;  // brow line; must clear the top of the big eye

visor_len     = 6;     // how far the peak sticks out
visor_droop   = 35;    // peak angle below horizontal (printable without support)
visor_t       = 1.6;
visor_extra_az = 4;    // peak overlaps the face opening edge by this much

slot_w        = 10;    // antenna slot width
slot_top_el   = 70;    // slot runs from the rim up to this elevation (+ rounded end)

pod_d         = 13;    // side comm-pod diameter
pod_proud     = 1.8;   // how far the pod stands out
pod_el        = 6;

grip_bumps    = true;  // small bumps inside the rim for a light friction fit
bump_d        = 1.6;
bump_h        = 0.3;   // interference per bump
bump_az       = [105, 145, 215, 255];

$fn = 96;

// ---- Derived ----------------------------------------------------------------
r_in     = head_r + clearance;
r_out    = r_in + wall;
bottom_z = rim_z + edge_lift;

module wedge(half_az, r) {
    // Solid sector of a cylinder in front (+X), |azimuth| <= half_az.
    rotate([0, 0, -half_az]) rotate_extrude(angle = 2 * half_az)
        square([r, 200], center = false);
}

module face_cut() {
    // Everything in front of the eyes and below the brow line, but only
    // within the shell, so the visor peak survives.
    intersection() {
        sphere(r = r_out + 0.05);
        translate([0, 0, -100]) wedge(face_half_az, 100);
        translate([-100, -100, -100]) cube([200, 200, 100 + face_top_z]);
    }
}

module antenna_slot_cut() {
    z_top = r_out * sin(slot_top_el);
    union() {
        translate([-100, -slot_w / 2, bottom_z - 1])
            cube([100, slot_w, z_top - bottom_z + 1]);
        rotate([0, slot_top_el - 90, 0]) cylinder(h = 100, d = slot_w);
    }
}

module visor_peak() {
    // rotate_extrude works in (horizontal radius, z). The peak has to root in
    // the shell wall at brow height, where the shell's horizontal radius is
    // far smaller than r_out, then droop from the outer surface outward.
    z0     = face_top_z;
    x_root = sqrt(r_in * r_in - (z0 + visor_t) * (z0 + visor_t));   // inside the wall
    x_surf = sqrt(r_out * r_out - z0 * z0);                          // outer surface at brow
    drop   = visor_len * tan(visor_droop);
    rotate([0, 0, -(face_half_az + visor_extra_az)])
    rotate_extrude(angle = 2 * (face_half_az + visor_extra_az))
        polygon([
            [x_root,             z0],
            [x_surf,             z0],
            [x_surf + visor_len, z0 - drop],
            [x_surf + visor_len, z0 - drop + visor_t],
            [x_surf,             z0 + visor_t],
            [x_root,             z0 + visor_t],
        ]);
}

module comm_pods() {
    for (az = [90, -90])
        on_head(az, pod_el, r_out - (pod_d * 0.3) / 2 + pod_proud)
            scale([1, 1, 0.3]) sphere(d = pod_d, $fn = 48);
}

module grip_bump_set() {
    for (az = bump_az)
        on_head(az, -2, r_in + bump_d / 2 - bump_h) sphere(d = bump_d, $fn = 24);
}

module helmet() {
    union() {
        difference() {
            union() {
                sphere(r = r_out);
                visor_peak();
                comm_pods();
            }
            sphere(r = r_in);
            translate([-100, -100, bottom_z - 100]) cube([200, 200, 100]);
            face_cut();
            antenna_slot_cut();
        }
        if (grip_bumps) grip_bump_set();
    }
}

helmet();
