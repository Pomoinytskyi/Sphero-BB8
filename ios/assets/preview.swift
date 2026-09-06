import AppKit

// Render the icon as iOS will actually show it: masked to the rounded-rect
// (~22.37% corner radius) and shrunk to home-screen size. Judging an icon at
// 1024px is how you ship one that is illegible at 60.

let args = CommandLine.arguments
guard let source = NSImage(contentsOfFile: args[1]) else { exit(1) }
let sizes = [180, 120, 60]
let pad = 24
let width = sizes.reduce(0) { $0 + $1 + pad } + pad
let height = sizes.max()! + pad * 2

let rep = NSBitmapImageRep(
    bitmapDataPlanes: nil, pixelsWide: width, pixelsHigh: height,
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

NSColor(white: 0.55, alpha: 1).setFill()   // neutral home-screen stand-in
NSRect(x: 0, y: 0, width: width, height: height).fill()

var x = pad
for size in sizes {
    let rect = NSRect(x: x, y: height - size - pad, width: size, height: size)
    NSGraphicsContext.current?.saveGraphicsState()
    NSBezierPath(roundedRect: rect,
                 xRadius: Double(size) * 0.2237,
                 yRadius: Double(size) * 0.2237).addClip()
    source.draw(in: rect)
    NSGraphicsContext.current?.restoreGraphicsState()
    x += size + pad
}
NSGraphicsContext.restoreGraphicsState()
try rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: args[2]))
print("wrote \(args[2])")
