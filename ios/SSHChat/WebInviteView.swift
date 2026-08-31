import SwiftUI
import WebKit

struct WebInviteView: View {
    let title: String
    let url: String
    let key: String
    /// When true (canvas), start edge-to-edge; upload pages keep a normal chrome bar.
    var startsMaximized: Bool = false
    /// Piano: allow landscape while this view is visible.
    var allowLandscape: Bool = false
    @Environment(\.dismiss) private var dismiss
    @State private var maximized = false

    var body: some View {
        Group {
            if startsMaximized {
                maximizedShell
            } else {
                navigationShell
            }
        }
        .onAppear {
            if startsMaximized {
                maximized = true
            }
            if allowLandscape {
                OrientationLock.setLandscape(true)
            }
        }
        .onDisappear {
            if allowLandscape {
                OrientationLock.setLandscape(false)
            }
        }
    }

    private var maximizedShell: some View {
        ZStack(alignment: .topTrailing) {
            KeyInjectingWebView(url: url, key: key.uppercased())
                .ignoresSafeArea()
            HStack(spacing: 12) {
                Button("关闭") { dismiss() }
            }
            .font(.subheadline.weight(.semibold))
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(.ultraThinMaterial, in: Capsule())
            .padding(.top, 8)
            .padding(.trailing, 12)
        }
        .statusBarHidden(true)
        .persistentSystemOverlays(.hidden)
    }

    private var navigationShell: some View {
        NavigationStack {
            KeyInjectingWebView(url: url, key: key.uppercased())
                .ignoresSafeArea(edges: maximized ? .all : .bottom)
                .navigationTitle(maximized ? "" : title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarLeading) {
                        Button(maximized ? "还原" : "最大化") {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                maximized.toggle()
                            }
                        }
                    }
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("关闭") { dismiss() }
                    }
                }
                .toolbar(maximized ? .hidden : .automatic, for: .navigationBar)
                .statusBarHidden(maximized)
                .overlay(alignment: .topTrailing) {
                    if maximized {
                        HStack(spacing: 12) {
                            Button("还原") {
                                withAnimation(.easeInOut(duration: 0.2)) {
                                    maximized = false
                                }
                            }
                            Button("关闭") { dismiss() }
                        }
                        .font(.subheadline.weight(.semibold))
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(.ultraThinMaterial, in: Capsule())
                        .padding(.top, 8)
                        .padding(.trailing, 12)
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
        // Set before page JS (incl. deferred ES modules) so canvas can auth
        // after Excalidraw finishes loading from CDN — not on a premature click.
        let safe = Self.jsStringLiteral(key)
        let boot = WKUserScript(
            source: "window.__SSHCHAT_KEY='\(safe)';",
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true
        )
        config.userContentController.addUserScript(boot)
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

    private static func jsStringLiteral(_ value: String) -> String {
        value
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "'", with: "\\'")
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        let key: String
        private var unlockDone = false
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
            // Fallback for upload pages / older canvas HTML: retry until gate opens
            // or Excalidraw module binds unlock listeners (CDN can take seconds).
            attemptUnlock(webView, attempt: 0)
        }

        private func attemptUnlock(_ webView: WKWebView, attempt: Int) {
            guard !unlockDone, attempt < 40 else { return }
            let safe = KeyInjectingWebView.jsStringLiteral(key)
            let js = """
            (function(){
              window.__SSHCHAT_KEY = '\(safe)';
              var board = document.getElementById('board');
              if (board) {
                var d = board.style.display;
                if (d === 'flex' || d === 'block') return 'done';
              }
              var stage = document.getElementById('stageWrap');
              if (stage && stage.style.display === 'flex') return 'done';
              var el = document.getElementById('key');
              if (!el) return 'wait';
              el.value = '\(safe)';
              el.dispatchEvent(new Event('input', {bubbles:true}));
              var btn = document.getElementById('unlockBtn');
              if (btn && !btn.disabled) btn.click();
              return 'pending';
            })();
            """
            webView.evaluateJavaScript(js) { [weak self, weak webView] result, _ in
                guard let self, let webView else { return }
                if (result as? String) == "done" {
                    self.unlockDone = true
                    return
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self, weak webView] in
                    guard let self, let webView else { return }
                    self.attemptUnlock(webView, attempt: attempt + 1)
                }
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
