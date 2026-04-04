// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AudioAssistCaptureHelper",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "AudioAssistCaptureHelper",
            path: "Sources/AudioAssistCaptureHelper"
        )
    ]
)
