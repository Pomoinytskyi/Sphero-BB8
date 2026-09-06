import SwiftUI

@main
struct BB8DroidApp: App {
    @State private var controller = DroidController(suppressLink: PreviewHarness.isEnabled)
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView(controller: controller)
                .preferredColorScheme(.dark)
                .persistentSystemOverlays(.hidden)
        }
        .onChange(of: scenePhase) { _, phase in
            // Backgrounding must stop the droid. iOS suspends the app and the
            // control loop with it; the firmware motion timeout would catch it
            // ~2s later, but two seconds of unattended droid is two seconds too
            // many when it is pointed at a staircase.
            if phase != .active { controller.transmitter.emergencyStop() }
        }
    }
}
