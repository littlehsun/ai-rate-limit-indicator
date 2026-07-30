// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RateLimitIndicatorMac",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(
            name: "RateLimitIndicatorMac",
            targets: ["RateLimitIndicatorMac"]
        ),
    ],
    targets: [
        .executableTarget(
            name: "RateLimitIndicatorMac",
            path: "Sources/RateLimitIndicatorMac"
        ),
        .testTarget(
            name: "RateLimitIndicatorMacTests",
            dependencies: ["RateLimitIndicatorMac"],
            path: "Tests/RateLimitIndicatorMacTests"
        ),
    ]
)
