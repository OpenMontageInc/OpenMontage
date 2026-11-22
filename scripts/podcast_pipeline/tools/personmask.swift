// personmask <in_dir> <out_dir> : writes an 8-bit grayscale person matte PNG for every PNG in in_dir
import Foundation
import Vision
import CoreImage
import ImageIO
import UniformTypeIdentifiers

let args = CommandLine.arguments
guard args.count == 3 else { print("usage: personmask in_dir out_dir"); exit(2) }
let inDir = URL(fileURLWithPath: args[1]), outDir = URL(fileURLWithPath: args[2])
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
let ctx = CIContext()
let files = try FileManager.default.contentsOfDirectory(atPath: inDir.path).filter { $0.hasSuffix(".png") }.sorted()
let req = VNGeneratePersonSegmentationRequest()
req.qualityLevel = .accurate
req.outputPixelFormat = kCVPixelFormatType_OneComponent8
for f in files {
    let url = inDir.appendingPathComponent(f)
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil), let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else { continue }
    let h = VNImageRequestHandler(cgImage: img, options: [:])
    try h.perform([req])
    guard let pb = req.results?.first?.pixelBuffer else { continue }
    var mask = CIImage(cvPixelBuffer: pb)
    let sx = CGFloat(img.width) / mask.extent.width, sy = CGFloat(img.height) / mask.extent.height
    mask = mask.transformed(by: CGAffineTransform(scaleX: sx, y: sy))
    let out = outDir.appendingPathComponent(f)
    guard let cg = ctx.createCGImage(mask, from: CGRect(x: 0, y: 0, width: img.width, height: img.height), format: .L8, colorSpace: CGColorSpaceCreateDeviceGray()),
          let dst = CGImageDestinationCreateWithURL(out as CFURL, UTType.png.identifier as CFString, 1, nil) else { continue }
    CGImageDestinationAddImage(dst, cg, nil); CGImageDestinationFinalize(dst)
}
print("masks: \(files.count)")
