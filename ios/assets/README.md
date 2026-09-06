# Icon assets

Generated from a product photo of the Sphero BB-8, using macOS Vision to lift the
subject off its white background.

| File | What it is |
|---|---|
| `bb8-cutout.png` | The droid on a transparent background, 504×585 |
| `cutout.swift` | Vision subject-lift — crops, masks, writes RGBA PNG |
| `icon.swift` | Composes the 1024×1024 icon (add `--transparent` for the dark variant) |
| `preview.swift` | Renders the icon at 180/120/60 px with iOS's corner mask |

```bash
swift cutout.swift source.jpg bb8-cutout.png 80 40 590 645
swift icon.swift bb8-cutout.png icon-1024.png
swift icon.swift bb8-cutout.png icon-1024-dark.png --transparent
swift preview.swift icon-1024.png preview.png
```

## Two things worth knowing

**iOS app icons cannot be transparent.** The primary icon must be opaque — an
alpha channel is rejected. The transparent cutout is kept as a *source* asset and
used for the iOS 18 dark-appearance variant, which the system composites itself.

AppKit makes this awkward: `NSGraphicsContext(bitmapImageRep:)` returns `nil` for
an alpha-less bitmap, so every draw call silently no-ops and you get a black
square. `icon.swift` therefore draws in RGBA and flattens onto an opaque canvas
afterwards.

**The charging cradle is cropped out.** It carries the Star Wars logo and a
Lucasfilm watermark. Removing it is better icon design anyway — the base is
illegible noise at 60 px — and it keeps someone else's trademark out of the app.

The droid itself is still a photo of a licensed product. Fine for a personal build
on your own phone; not something to ship.
