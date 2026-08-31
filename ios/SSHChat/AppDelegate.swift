import UIKit

final class AppDelegate: NSObject, UIApplicationDelegate {
    /// Default portrait; piano WebView sets `.landscape` while visible.
    static var orientationLock: UIInterfaceOrientationMask = .portrait

    func application(
        _ application: UIApplication,
        supportedInterfaceOrientationsFor window: UIWindow?
    ) -> UIInterfaceOrientationMask {
        AppDelegate.orientationLock
    }
}

enum OrientationLock {
    static func setLandscape(_ on: Bool) {
        AppDelegate.orientationLock = on ? .landscape : .portrait
        guard let scene = UIApplication.shared.connectedScenes
            .compactMap({ $0 as? UIWindowScene })
            .first(where: { $0.activationState == .foregroundActive })
        else { return }
        if #available(iOS 16.0, *) {
            let mask: UIInterfaceOrientationMask = on ? .landscape : .portrait
            scene.requestGeometryUpdate(.iOS(interfaceOrientations: mask)) { _ in }
        } else if on {
            UIDevice.current.setValue(
                UIInterfaceOrientation.landscapeRight.rawValue,
                forKey: "orientation"
            )
        } else {
            UIDevice.current.setValue(
                UIInterfaceOrientation.portrait.rawValue,
                forKey: "orientation"
            )
        }
        UIViewController.attemptRotationToDeviceOrientation()
    }
}
