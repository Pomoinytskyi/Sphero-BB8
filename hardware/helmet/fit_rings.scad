// Fit-test rings for the BB-8 head.
//
// Print this first. Drop each ring over the crown of the head; the smallest
// ring that slides past the widest part of the dome tells you the real
// head_d. Set head_d in bb8_dimensions.scad to that ring's inner diameter
// minus 0.5 mm, then print the helmet.
//
// Each ring is 4 mm tall, 1.6 mm thick, and has its inner diameter embossed.

ring_ids = [43, 44, 45, 46, 47, 48];
ring_h   = 4;
ring_t   = 1.6;
pitch    = 56;
$fn = 120;

module ring(id) {
    difference() {
        cylinder(h = ring_h, d = id + 2 * ring_t);
        translate([0, 0, -1]) cylinder(h = ring_h + 2, d = id);
    }
    translate([0, id / 2 + ring_t / 2, ring_h])
        linear_extrude(0.6)
            text(str(id), size = 4, halign = "center", valign = "center");
}

for (i = [0 : len(ring_ids) - 1])
    translate([(i % 3) * pitch, floor(i / 3) * pitch, 0]) ring(ring_ids[i]);
