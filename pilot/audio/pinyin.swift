import Foundation

// Local macOS transliteration for subtitle / SenseVoice homophone matching.
// Input and output are JSON arrays of strings; no network access.
let input = FileHandle.standardInput.readDataToEndOfFile()
guard let values = try? JSONSerialization.jsonObject(with: input) as? [String] else {
    fputs("Expected a JSON string array\n", stderr)
    exit(2)
}
let converted = values.map { $0.applyingTransform(.mandarinToLatin, reverse: false) ?? $0 }
let output = try JSONSerialization.data(withJSONObject: converted)
FileHandle.standardOutput.write(output)
