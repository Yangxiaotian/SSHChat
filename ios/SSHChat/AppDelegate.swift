import UIKit

final class AppDelegate: NSObject, UIApplicationDelegate {
    /// iPhone: portrait; iPad: all orientations. Piano WebView temporarily locks landscape.
    static var orientationLock: UIInterfaceOrientationMask = OrientationLock.defaultMask

    func application(
        _ application: UIApplication,
        supportedInterfaceOrientationsFor window: UIWindow?
    ) -> UIInterfaceOrientationMask {
        AppDelegate.orientationLock
    }
}

enum OrientationLock {
    static var defaultMask: UIInterfaceOrientationMask {
        UIDevice.current.userInterfaceIdiom == .pad ? .allButUpsideDown : .portrait
    }

    static func setLandscape(_ on: Bool) {
        let mask: UIInterfaceOrientationMask = on ? .landscape : defaultMask
        AppDelegate.orientationLock = mask
        guard let scene = UIApplication.shared.connectedScenes
            .compactMap({ $0 as? UIWindowScene })
            .first(where: { $0.activationState == .foregroundActive })
        else { return }
        if #available(iOS 16.0, *) {
            scene.requestGeometryUpdate(.iOS(interfaceOrientations: mask)) { _ in }
        } else if on {
            UIDevice.current.setValue(
                UIInterfaceOrientation.landscapeRight.rawValue,
                forKey: "orientation"
            )
        } else if UIDevice.current.userInterfaceIdiom != .pad {
            UIDevice.current.setValue(
                UIInterfaceOrientation.portrait.rawValue,
                forKey: "orientation"
            )
        }
        UIViewController.attemptRotationToDeviceOrientation()
    }
}
