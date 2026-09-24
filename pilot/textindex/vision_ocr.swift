// macOS Vision OCR for subtitle strips (WO-002a; merge with pilot/tools/ocr_vision.swift in WO-003).
// Reads image paths from stdin (one per line), writes one JSON line per image:
//   {"frame": path, "items": [{"text", "conf", "x", "y", "w", "h"}]}
// bbox is normalized, origin top-left. Build: swiftc -O vision_ocr.swift -o vision_ocr
import Foundation
import ImageIO
import Vision

let langs = ProcessInfo.processInfo.environment["VISION_OCR_LANGS"]?.split(separator: ",").map(String.init) ?? ["zh-Hans"]
let out = FileHandle.standardOutput

func emit(_ obj: [String: Any]) {
  if var data = try? JSONSerialization.data(withJSONObject: obj) { data.append(0x0A); out.write(data) }
}

while let line = readLine() {
  let path = line.trimmingCharacters(in: .whitespaces)
  if path.isEmpty { continue }
  autoreleasepool {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
      emit(["frame": path, "error": "unreadable"]); return
    }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = langs
    req.usesLanguageCorrection = false
    do { try VNImageRequestHandler(cgImage: img, options: [:]).perform([req]) } catch {
      emit(["frame": path, "error": "\(error)"]); return
    }
    var items: [[String: Any]] = []
    for o in req.results ?? [] {
      guard let c = o.topCandidates(1).first else { continue }
      let b = o.boundingBox
      items.append(["text": c.string, "conf": Double(c.confidence),
                    "x": Double(b.minX), "y": Double(1 - b.maxY), "w": Double(b.width), "h": Double(b.height)])
    }
    emit(["frame": path, "items": items])
  }
}
