import SwiftUI

@main
struct SSHChatApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                // Whole app chrome is light-themed; keep text/icons readable on iPad Dark Mode.
                .preferredColorScheme(.light)
        }
    }
}
