import SwiftUI

@main
struct OrbitApp: App {
    @StateObject private var store = SnapshotStore()

    var body: some Scene {
        WindowGroup {
            DashboardView()
                .environmentObject(store)
                .environment(\.locale, Locale(identifier: "ru_RU"))
                .preferredColorScheme(.dark)
        }
    }
}
