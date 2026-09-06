import AppKit

// Compose the app icon.
//
// Two constraints shape this. iOS applies its own corner mask, so we draw a
// full square with the droid inset. And iOS rejects an app icon with an alpha
// channel — but AppKit will not give us a drawing context for an alpha-less
// bitmap (NSGraphicsContext(bitmapImageRep:) simply returns nil, and every
// draw call becomes a silent no-op producing a black square). So: draw in
// RGBA, then flatten onto an opaque canvas.

let args = CommandLine.arguments
guard args.count >= 3 else { fputs("usage: icon <cutout> <out> [--transparent]\n", stderr); exit(1) }
let transparent = args.contains("--transparent")
let side = 1024

guard let droid = NSImage(contentsOfFile: args[1]) else {
    fputs("could not read cutout\n", stderr); exit(1)
}

func makeRGBARep() -> NSBitmapImageRep {
    NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: side, pixelsHigh: side,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0
    )!
}

let rep = makeRGBARep()
rep.size = NSSize(width: side, height: side)

guard let context = NSGraphicsContext(bitmapImageRep: rep) else {
    fputs("could not create drawing context\n", stderr); exit(1)
}
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = context

let full = NSRect(x: 0, y: 0, width: side, height: side)
NSColor.clear.setFill()
full.fill(using: .copy)

if !transparent {
    // Matches the app's ground: near-black, lifted a little below centre so the
    // droid reads against it without a hard vignette.
    NSGradient(colors: [
        NSColor(srgbRed: 0.15, green: 0.16, blue: 0.19, alpha: 1),
        NSColor(srgbRed: 0.04, green: 0.04, blue: 0.05, alpha: 1),
    ])!.draw(in: full, relativeCenterPosition: NSPoint(x: 0, y: 0.35))

    // A warm pool behind the droid, picking up its orange trim. Kept low in
    // alpha — at 60px the icon should read as "droid on dark", not "orange blob".
    NSGradient(colors: [
        NSColor(srgbRed: 0.92, green: 0.50, blue: 0.16, alpha: 0.16),
        NSColor(srgbRed: 0.92, green: 0.50, blue: 0.16, alpha: 0),
    ])!.draw(in: NSRect(x: Double(side) * 0.10, y: Double(side) * 0.02,
                        width: Double(side) * 0.80, height: Double(side) * 0.80),
             relativeCenterPosition: .zero)

}

// Fit the droid to ~76% of the canvas, biased slightly low so the head does not
// crowd the top edge once iOS rounds the corners.
// The photo has BB-8 seated in its cradle, so the sphere is truncated at the
// bottom. Rather than disguise the flat cut, run it off the bottom edge — the
// droid then reads as continuing past the frame instead of being sliced.
// Side and top margins stay generous because iOS masks the corners.
let aspect = droid.size.width / droid.size.height
let height = Double(side) * 0.90
let width = height * aspect
droid.draw(
    in: NSRect(x: (Double(side) - width) / 2,
               y: -Double(side) * 0.055,
               width: width, height: height),
    from: .zero, operation: .sourceOver, fraction: 1.0
)
context.flushGraphics()
NSGraphicsContext.restoreGraphicsState()

var output = rep
if !transparent {
    // Flatten onto opaque black. CGBitmapContext accepts noneSkipLast, which
    // AppKit's bitmap rep would not give us directly.
    guard let cgSource = rep.cgImage,
          let flat = CGContext(
            data: nil, width: side, height: side, bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
    else { fputs("flatten failed\n", stderr); exit(1) }
    flat.setFillColor(CGColor(red: 0.04, green: 0.04, blue: 0.05, alpha: 1))
    flat.fill(CGRect(x: 0, y: 0, width: side, height: side))
    flat.draw(cgSource, in: CGRect(x: 0, y: 0, width: side, height: side))
    guard let composed = flat.makeImage() else { exit(1) }
    output = NSBitmapImageRep(cgImage: composed)
}

guard let png = output.representation(using: .png, properties: [:]) else { exit(1) }
try png.write(to: URL(fileURLWithPath: args[2]))
print("wrote \(args[2]) — \(output.pixelsWide)x\(output.pixelsHigh), alpha=\(output.hasAlpha)")
