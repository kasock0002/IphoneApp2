// swift-tools-version: 5.9
import PackageDescription

/// Foundation-only validation tests can run without building the iPhone UI.
let package = Package(
    name: "OrbitCore",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "OrbitCore", targets: ["OrbitCore"])],
    targets: [
        .target(
            name: "OrbitCore",
            path: "AgentOrbit",
            exclude: [
                "Assets.xcassets", "Resources", "Info.plist",
                "DashboardView.swift", "DataSourceView.swift", "DemoData.swift",
                "OrbitApp.swift", "ProjectDetailView.swift", "ProjectSceneView.swift",
                "SnapshotConnection.swift", "SnapshotStore.swift", "Theme.swift"
            ],
            sources: ["Models.swift", "SnapshotCodec.swift", "ConnectionProfile.swift"]
        ),
        .testTarget(
            name: "OrbitCoreTests",
            dependencies: ["OrbitCore"],
            path: "NativeTests"
        )
    ]
)
