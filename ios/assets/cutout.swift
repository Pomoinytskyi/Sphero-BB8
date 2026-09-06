import AppKit
import Vision

// Lift the droid off its white background using Vision's foreground instance
// mask. Chroma-keying white would be simpler but BB-8 is itself mostly white,
// so a colour threshold eats the subject.

let args = CommandLine.arguments
guard args.count >= 3 else { fputs("usage: cutout <in> <out> [cropX cropY cropW cropH]\n", stderr); exit(1) }

guard let source = CIImage(contentsOf: URL(fileURLWithPath: args[1])) else {
    fputs("could not read image\n", stderr); exit(1)
}

var image = source
if args.count >= 7, let x = Double(args[3]), let y = Double(args[4]),
   let w = Double(args[5]), let h = Double(args[6]) {
    // CoreImage origin is bottom-left; the numbers given are top-left.
    let flippedY = source.extent.height - y - h
    image = source.cropped(to: CGRect(x: x, y: flippedY, width: w, height: h))
        .transformed(by: .init(translationX: -x, y: -flippedY))
}

let context = CIContext()
guard let cgImage = context.createCGImage(image, from: image.extent) else { exit(1) }

let request = VNGenerateForegroundInstanceMaskRequest()
let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
try handler.perform([request])

guard let result = request.results?.first else {
    fputs("no foreground instance found\n", stderr); exit(1)
}
print("instances found: \(result.allInstances.count)")

let masked = try result.generateMaskedImage(
    ofInstances: result.allInstances, from: handler, croppedToInstancesExtent: true
)
let output = CIImage(cvPixelBuffer: masked)
guard let cg = context.createCGImage(output, from: output.extent) else { exit(1) }

let rep = NSBitmapImageRep(cgImage: cg)
rep.size = NSSize(width: cg.width, height: cg.height)
guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
try png.write(to: URL(fileURLWithPath: args[2]))
print("wrote \(args[2]) — \(cg.width)x\(cg.height)")
