# Pilot helmet for the Sphero BB-8

A 3D-printable Rebel-pilot style helmet that drops over the head of the
Sphero BB-8 (model R001). Parametric OpenSCAD, ready-to-slice STLs, and a set
of fit-test rings so the one number nobody publishes, the head diameter, can
be measured on the real droid before the helmet is printed.

![Helmet on the droid](preview/assembly_front.png)

| Front | Rear (antenna slot) | Side |
|---|---|---|
| ![](preview/assembly_front.png) | ![](preview/assembly_rear.png) | ![](preview/assembly_side.png) |

## Files

| File | What it is |
|---|---|
| `bb8_dimensions.scad` | Every droid measurement, plus a mock head and body used by the previews |
| `bb8_pilot_helmet.scad` | The helmet. All helmet parameters are at the top of the file |
| `fit_rings.scad` | Six rings, 43 to 48 mm, to find the real head diameter |
| `head_reference.scad` | The mock head alone, printable as a stand-in for test fitting |
| `assembly.scad` | Preview only: helmet on head on body |
| `stl/` | Exported meshes, watertight, millimetres |
| `preview/` | Renders of the above |
| `Makefile` | `make` regenerates `stl/` and `preview/` from the sources |

## The droid's dimensions

Sphero publishes only the outer envelope. The head is derived, and the
derivation is written next to each number in `bb8_dimensions.scad`.

| Measurement | Value | Source |
|---|---|---|
| Overall height, antenna included | 114 mm | Sphero tech specs (via [Sphero Wiki](https://sphero.fandom.com/wiki/Sphero_BB8), [Gadget Flow](https://thegadgetflow.com/product/sphero-bb-8-ultimate-star-wars-droid/)) |
| Width, i.e. ball diameter | 73 mm | Same |
| Weight, whole droid | ~200 g | Same |
| Head dome sphere diameter | **45 mm, estimated** | Movie ratio 0.58 ([rimstar.org](https://rimstar.org/science_electronics_projects/bb-8_dimensions.htm)) gives 42; the supplied photo gives ~0.62 after perspective, 45 |
| Head height, rim to crown | **27 mm, estimated** | 114 − 73 ball − 2 air gap − ~12 antenna above the crown |
| Head rim diameter | 44.1 mm, derived | From the two figures above; the rim sits below the equator |
| Antennas | Two, a close pair ~16 mm and ~8 mm, elevation ~30°, 12 to 15° left of dead rear | Plan-view and rear photos of the bare head |

The two bold figures are ±2 mm guesses and they set the fit. That is what
`fit_rings.scad` is for. Older reviews quote 70 mm for the ball and 90 mm
overall ([Impulse Gamer](https://www.impulsegamer.com/bb-8-droid-by-sphero-the-review/));
Sphero's own 73 / 114 mm figures are used here.

## Print order

1. **Print `stl/fit_rings.stl`** (flat, 5 minutes). Drop each ring over the
   crown of the head. The smallest ring that slides past the widest part of
   the dome, which is just above the silver rim, is the real head diameter plus
   clearance.
2. **Set `head_d`** in `bb8_dimensions.scad` to that ring's inner diameter
   minus 0.5 mm. If the head is clearly taller or shorter than 27 mm from the
   silver rim to the top, set `head_h` too.
3. **Export the helmet**: `make stl/bb8_pilot_helmet.stl`, or open the file in
   OpenSCAD and press F6 then export.
4. **Print the helmet** rim-down, open side on the bed.

The slot is centred on the antenna pair (`slot_az`, 167°), not on the rear
centre line, because the pair sits to the left of it. If the antennas foul
the slot, widen `slot_w` (default 11 mm) or swing `slot_az`. To fit the head into the helmet, go in
face-first with the head tilted back so the antennas ride up the slot, then
level it. The head lifts off the ball, which makes this easier than doing it in
place.

## Print settings

| Setting | Value |
|---|---|
| Material | PLA or PETG; the helmet weighs ~6.6 g in PLA |
| Layer height | 0.12 to 0.16 mm |
| Perimeters | 4 (the 1.6 mm wall is all perimeter, no infill) |
| Supports | None needed. The visor droops at 35° and the comm-pods are domed. The last few millimetres of the crown are steep; a brim and slow cooling handle it, or add a small support blocker-free tree support if your printer struggles |
| Orientation | Rim down |

The head is held on the ball by magnets and balances on wheels, so keep the
helmet light and centred. The default shell is 1.6 mm; 1.2 mm with 3
perimeters also works and saves 1.5 g.

## Retention

Four 0.3 mm bumps just inside the rim give a light friction grip on the glossy
head (`grip_bumps`). If the helmet still slides when the droid corners, a pea
of Blu Tack at the crown works and leaves no mark; set `grip_bumps = false`
if the fit is already tight.

## Helmet parameters

All at the top of `bb8_pilot_helmet.scad`:

| Parameter | Default | Meaning |
|---|---|---|
| `clearance` | 0.8 | Radial air gap between head and shell |
| `wall` | 1.6 | Shell thickness |
| `edge_lift` | 0.5 | Helmet edge stops this far above the silver rim |
| `face_half_az` | 58° | Half-width of the face opening |
| `face_top_z` | 18.5 | Brow line height above the head's sphere centre; must clear the big eye |
| `visor_len`, `visor_droop` | 6 mm, 35° | Peak size and downward angle |
| `slot_az`, `slot_w`, `slot_top_el` | 167°, 11 mm, 50° | Antenna slot centre line, width, and how far up the back it runs |
| `pod_d`, `pod_proud` | 13 mm, 1.8 mm | Side comm-pods |
| `grip_bumps`, `bump_h` | true, 0.3 | Friction bumps inside the rim |

Coordinates: origin at the centre of the sphere the head is cut from, +Z up,
+X toward the big eye. Azimuth 0 is the front, 180 the back.
