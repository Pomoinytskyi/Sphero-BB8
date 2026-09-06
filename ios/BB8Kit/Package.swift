// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "BB8Kit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "BB8Kit", targets: ["BB8Kit"]),
    ],
    targets: [
        // Pure logic: no CoreBluetooth, no GameController, no SwiftUI.
        // Keeping it platform-free is what lets `swift test` run the whole
        // codec suite from the command line, exactly as the Python side does.
        .target(name: "BB8Kit"),
        .testTarget(
            name: "BB8KitTests",
            dependencies: ["BB8Kit"],
            resources: [.copy("Resources/bb8_vectors.json")]
        ),
    ]
)
