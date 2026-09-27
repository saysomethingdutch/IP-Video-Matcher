import AppKit
import Foundation
import Vision

// Usage: ocr_vision [--crop x,y,w,h] [--scale n] image ...
// Crop coordinates are normalized to the source image, with a top-left origin.
var args = Array(CommandLine.arguments.dropFirst())
var crop: [Double]? = nil
var scale = 1.0
if args.count >= 2 && args[0] == "--crop" {
    crop = args[1].split(separator: ",").compactMap { Double($0) }
    args.removeFirst(2)
}
if args.count >= 2 && args[0] == "--scale" {
    scale = Double(args[1]) ?? 1.0
    args.removeFirst(2)
}

func preparedImage(_ url: URL) -> CGImage? {
    guard let image = NSImage(contentsOf: url),
          let source = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return nil }
    let x = crop?.count == 4 ? max(0, min(1, crop![0])) : 0
    let y = crop?.count == 4 ? max(0, min(1, crop![1])) : 0
    let w = crop?.count == 4 ? max(0, min(1 - x, crop![2])) : 1
    let h = crop?.count == 4 ? max(0, min(1 - y, crop![3])) : 1
    let rect = CGRect(x: Int(x * Double(source.width)),
                      y: Int(y * Double(source.height)),
                      width: max(1, Int(w * Double(source.width))),
                      height: max(1, Int(h * Double(source.height))))
    guard let clipped = source.cropping(to: rect) else { return nil }
    if scale <= 1 { return clipped }
    let width = Int(Double(clipped.width) * scale)
    let height = Int(Double(clipped.height) * scale)
    guard let context = CGContext(data: nil, width: width, height: height,
                                  bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return clipped }
    context.interpolationQuality = .high
    context.draw(clipped, in: CGRect(x: 0, y: 0, width: width, height: height))
    return context.makeImage()
}

for path in args {
    let url = URL(fileURLWithPath: path)
    guard let image = preparedImage(url) else { continue }
    let request = VNRecognizeTextRequest()
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    do {
        try VNImageRequestHandler(cgImage: image).perform([request])
        let items: [[String: Any]] = (request.results ?? []).compactMap { observation in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let box = observation.boundingBox
            return ["text": candidate.string, "conf": candidate.confidence,
                    "x": box.minX, "y": 1 - box.maxY, "w": box.width, "h": box.height]
        }
        let record: [String: Any] = ["frame": url.lastPathComponent, "items": items]
        if let data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]),
           let json = String(data: data, encoding: .utf8) { print(json) }
    } catch {
        fputs("OCR failed \(path): \(error)\n", stderr)
    }
}
