package chat.ssh.sshchat

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import chat.ssh.sshchat.databinding.ActivityWebInviteBinding

/**
 * Opens canvas / upload HTML pages and auto-fills the 6-char key.
 */
class WebInviteActivity : AppCompatActivity() {
    private lateinit var binding: ActivityWebInviteBinding
    private var keyInjected = false
    private var maximized = false
    private var isCanvas = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityWebInviteBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val url = intent.getStringExtra(EXTRA_URL).orEmpty()
        val key = intent.getStringExtra(EXTRA_KEY).orEmpty().uppercase()
        val title = intent.getStringExtra(EXTRA_TITLE).orEmpty().ifBlank { "SSHChat" }
        isCanvas = intent.getBooleanExtra(EXTRA_CANVAS, false)
        if (url.isBlank() || key.isBlank()) {
            Toast.makeText(this, "无效邀请", Toast.LENGTH_SHORT).show()
            finish()
            return
        }

        binding.tvTitle.text = title
        binding.btnClose.setOnClickListener { finish() }
        binding.btnCloseFloating.setOnClickListener { finish() }
        binding.btnMaximize.setOnClickListener { setMaximized(true) }
        binding.btnRestore.setOnClickListener { setMaximized(false) }

        binding.web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            mediaPlaybackRequiresUserGesture = false
        }
        binding.web.webChromeClient = WebChromeClient()
        val safeKey = key.replace("\\", "\\\\").replace("'", "\\'")
        binding.web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean = false

            override fun onPageStarted(view: WebView, url: String?, favicon: android.graphics.Bitmap?) {
                // Before deferred ES modules run — canvas page reads this after CDN load.
                view.evaluateJavascript("window.__SSHCHAT_KEY='$safeKey';", null)
            }

            override fun onPageFinished(view: WebView, url: String?) {
                // Retry until board unlocks: Excalidraw listeners bind only after esm.sh loads.
                attemptUnlock(view, 0)
            }
        }
        binding.web.loadUrl(url)

        // Canvas starts maximized so the board fills the phone screen.
        if (isCanvas) {
            setMaximized(true)
        }
    }

    private fun setMaximized(on: Boolean) {
        maximized = on
        binding.toolbar.visibility = if (on) View.GONE else View.VISIBLE
        binding.floatingChrome.visibility = if (on) View.VISIBLE else View.GONE

        WindowCompat.setDecorFitsSystemWindows(window, !on)
        val controller = WindowInsetsControllerCompat(window, window.decorView)
        if (on) {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            controller.hide(WindowInsetsCompat.Type.systemBars())
            controller.systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            controller.show(WindowInsetsCompat.Type.systemBars())
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (maximized && isCanvas) {
            setMaximized(false)
            return
        }
        @Suppress("DEPRECATION")
        super.onBackPressed()
    }

    override fun onResume() {
        super.onResume()
        // WebView may discard the canvas bitmap while paused; ask the page to repaint.
        if (::binding.isInitialized) {
            binding.web.evaluateJavascript(
                "(function(){ try { if (typeof paintAll === 'function') paintAll(); } catch(e) {} })();",
                null,
            )
        }
    }

    private fun attemptUnlock(view: WebView, attempt: Int) {
        if (keyInjected || isFinishing || isDestroyed || attempt >= 40) return
        val safe = intent.getStringExtra(EXTRA_KEY).orEmpty().uppercase()
            .replace("\\", "\\\\").replace("'", "\\'")
        view.evaluateJavascript(
            """
            (function(){
              window.__SSHCHAT_KEY = '$safe';
              var board = document.getElementById('board');
              if (board) {
                var d = board.style.display;
                if (d === 'flex' || d === 'block') return 'done';
              }
              var el = document.getElementById('key');
              if (!el) return 'wait';
              el.value = '$safe';
              el.dispatchEvent(new Event('input', {bubbles:true}));
              var btn = document.getElementById('unlockBtn');
              if (btn && !btn.disabled) btn.click();
              return 'pending';
            })();
            """.trimIndent(),
        ) { result ->
            if (result == "\"done\"") {
                keyInjected = true
                return@evaluateJavascript
            }
            view.postDelayed({
                if (isFinishing || isDestroyed) return@postDelayed
                attemptUnlock(view, attempt + 1)
            }, 250L)
        }
    }

    override fun onDestroy() {
        binding.web.destroy()
        super.onDestroy()
    }

    companion object {
        private const val EXTRA_URL = "url"
        private const val EXTRA_KEY = "key"
        private const val EXTRA_TITLE = "title"
        private const val EXTRA_CANVAS = "canvas"

        fun canvas(ctx: Context, url: String, key: String): Intent =
            Intent(ctx, WebInviteActivity::class.java)
                .putExtra(EXTRA_URL, url)
                .putExtra(EXTRA_KEY, key)
                .putExtra(EXTRA_TITLE, "共享画布")
                .putExtra(EXTRA_CANVAS, true)

        fun upload(ctx: Context, url: String, key: String): Intent =
            Intent(ctx, WebInviteActivity::class.java)
                .putExtra(EXTRA_URL, url)
                .putExtra(EXTRA_KEY, key)
                .putExtra(EXTRA_TITLE, "上传文件")
                .putExtra(EXTRA_CANVAS, false)
    }
}
