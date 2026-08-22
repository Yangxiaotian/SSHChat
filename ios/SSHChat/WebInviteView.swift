import SwiftUI
import WebKit

struct WebInviteView: View {
    let title: String
    let url: String
    let key: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            KeyInjectingWebView(url: url, key: key.uppercased())
                .ignoresSafeArea(edges: .bottom)
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("关闭") { dismiss() }
                    }
                }
        }
    }
}

private struct KeyInjectingWebView: UIViewRepresentable {
    let url: String
    let key: String

    func makeCoordinator() -> Coordinator { Coordinator(key: key) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = context.coordinator
        web.uiDelegate = context.coordinator
        context.coordinator.webView = web
        if let u = URL(string: url) {
            web.load(URLRequest(url: u))
        }
        return web
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        let key: String
        private var injected = false
        weak var webView: WKWebView?

        init(key: String) {
            self.key = key
            super.init()
            NotificationCenter.default.addObserver(
                self,
                selector: #selector(repaintCanvas),
                name: UIApplication.didBecomeActiveNotification,
                object: nil
            )
        }

        deinit {
            NotificationCenter.default.removeObserver(self)
        }

        @objc private func repaintCanvas() {
            webView?.evaluateJavaScript(
                "(function(){ try { if (typeof paintAll === 'function') paintAll(); } catch(e) {} })();",
                completionHandler: nil
            )
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            guard !injected else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self, weak webView] in
                guard let self, let webView, !self.injected else { return }
                self.injected = true
                let safe = self.key
                    .replacingOccurrences(of: "\\", with: "\\\\")
                    .replacingOccurrences(of: "'", with: "\\'")
                let js = """
                (function(){
                  if (document.getElementById('board')
                      && document.getElementById('board').style.display === 'block')
                    return 'already';
                  var el = document.getElementById('key');
                  if (!el) return 'no-key';
                  el.value = '\(safe)';
                  el.dispatchEvent(new Event('input', {bubbles:true}));
                  var btn = document.getElementById('unlockBtn');
                  if (btn && !btn.disabled) btn.click();
                  return 'ok';
                })();
                """
                webView.evaluateJavaScript(js, completionHandler: nil)
            }
        }

        func webView(
            _ webView: WKWebView,
            runJavaScriptAlertPanelWithMessage message: String,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping () -> Void
        ) {
            presentAlert(
                from: webView,
                title: nil,
                message: message,
                actions: [("确定", .default, { completionHandler() })]
            )
        }

        func webView(
            _ webView: WKWebView,
            runJavaScriptConfirmPanelWithMessage message: String,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping (Bool) -> Void
        ) {
            presentAlert(
                from: webView,
                title: nil,
                message: message,
                actions: [
                    ("取消", .cancel, { completionHandler(false) }),
                    ("确定", .default, { completionHandler(true) }),
                ]
            )
        }

        private func presentAlert(
            from webView: WKWebView,
            title: String?,
            message: String,
            actions: [(String, UIAlertAction.Style, () -> Void)]
        ) {
            let alert = UIAlertController(title: title, message: message, preferredStyle: .alert)
            for (label, style, handler) in actions {
                alert.addAction(UIAlertAction(title: label, style: style) { _ in handler() })
            }
            guard let presenter = topViewController(from: webView) else {
                if let cancel = actions.first(where: { $0.1 == .cancel }) {
                    cancel.2()
                } else {
                    actions.first?.2()
                }
                return
            }
            presenter.present(alert, animated: true)
        }

        private func topViewController(from webView: WKWebView) -> UIViewController? {
            var vc = webView.window?.rootViewController
            while let presented = vc?.presentedViewController {
                vc = presented
            }
            return vc
        }
    }
}
