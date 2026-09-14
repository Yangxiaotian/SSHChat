import SwiftUI
import UIKit
import UniformTypeIdentifiers

struct CameraPicker: UIViewControllerRepresentable {
    enum Mode { case photo, video }
    let mode: Mode
    var onPicked: (URL) -> Void
    var onCancel: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let p = UIImagePickerController()
        p.delegate = context.coordinator
        p.sourceType = .camera
        switch mode {
        case .photo:
            p.mediaTypes = [UTType.image.identifier]
            p.cameraCaptureMode = .photo
        case .video:
            p.mediaTypes = [UTType.movie.identifier]
            p.cameraCaptureMode = .video
            p.videoQuality = .typeMedium
        }
        return p
    }

    func updateUIViewController(_ uiViewController: UIImagePickerController, context: Context) {}

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let parent: CameraPicker
        init(parent: CameraPicker) { self.parent = parent }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            parent.onCancel()
        }

        func imagePickerController(
            _ picker: UIImagePickerController,
            didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]
        ) {
            if let url = info[.mediaURL] as? URL {
                parent.onPicked(url)
                return
            }
            if let image = info[.originalImage] as? UIImage {
                let dir = FileManager.default.temporaryDirectory.appendingPathComponent("sshchat-camera", isDirectory: true)
                try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
                let out = dir.appendingPathComponent("cap-\(Int(Date().timeIntervalSince1970 * 1000)).jpg")
                if let data = image.jpegData(compressionQuality: 0.9) {
                    try? data.write(to: out)
                    parent.onPicked(out)
                    return
                }
            }
            parent.onCancel()
        }
    }
}
