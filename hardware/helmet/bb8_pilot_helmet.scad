// Rebel-pilot style helmet for the Sphero BB-8 head.
//
// A thin spherical shell that drops over the head dome, with:
//   * an open face so the big eye and the small lens stay visible,
//   * a drooping visor peak over the brow,
//   * a slot up the back, centred on the two antennas,
//   * side ear pods, and optional grip bumps inside the rim.
//
// Print it rim-down (open side on the bed). Every overhang is either < 50
// degrees or the last few millimetres of the crown. See README.md.
//
// Shell, slot and bump parameters live in helmet_common.scad; head figures
// in bb8_dimensions.scad. Check them with fit_rings.scad first: the helmet
// is only as good as head_d.

include <helmet_common.scad>

// ---- Pilot helmet parameters ------------------------------------------------
face_half_az   = 58;    // half-width of the face opening, degrees
face_top_z     = 18.5;  // brow line; must clear the top of the big eye

visor_len      = 6;     // how far the peak sticks out
visor_droop    = 35;    // peak angle below horizontal (printable without support)
visor_t        = 1.6;
visor_extra_az = 4;     // peak overlaps the face opening edge by this much

module visor_peak() {
    // rotate_extrude works in (horizontal radius, z). The peak roots in the
    // shell wall at brow height, where the shell's horizontal radius is far
    // smaller than r_out, then droops from the outer surface outward.
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

module helmet() {
    difference() {
        helmet_body() { visor_peak(); ear_pods(); }
        face_cut(face_half_az, face_top_z);
    }
}

helmet();
