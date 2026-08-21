package chat.ssh.sshchat

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import chat.ssh.sshchat.databinding.ActivityWebInviteBinding

/**
 * Opens canvas / upload HTML pages and auto-fills the 6-char key.
 */
class WebInviteActivity : AppCompatActivity() {
    private lateinit var binding: ActivityWebInviteBinding
    private var keyInjected = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityWebInviteBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val url = intent.getStringExtra(EXTRA_URL).orEmpty()
        val key = intent.getStringExtra(EXTRA_KEY).orEmpty().uppercase()
        val title = intent.getStringExtra(EXTRA_TITLE).orEmpty().ifBlank { "SSHChat" }
        if (url.isBlank() || key.isBlank()) {
            Toast.makeText(this, "无效邀请", Toast.LENGTH_SHORT).show()
            finish()
            return
        }

        binding.tvTitle.text = title
        binding.btnClose.setOnClickListener { finish() }

        binding.web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            mediaPlaybackRequiresUserGesture = false
        }
        binding.web.webChromeClient = WebChromeClient()
        binding.web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean = false

            override fun onPageFinished(view: WebView, url: String?) {
                if (keyInjected) return
                // Wait a tick so canvas JS listeners are bound; avoid double unlock.
                view.postDelayed({
                    if (isFinishing || isDestroyed) return@postDelayed
                    if (keyInjected) return@postDelayed
                    keyInjected = true
                    val safe = key.replace("\\", "\\\\").replace("'", "\\'")
                    view.evaluateJavascript(
                        """
                        (function(){
                          if (document.getElementById('board')
                              && document.getElementById('board').style.display === 'block')
                            return 'already';
                          var el = document.getElementById('key');
                          if (!el) return 'no-key';
                          el.value = '$safe';
                          el.dispatchEvent(new Event('input', {bubbles:true}));
                          var btn = document.getElementById('unlockBtn');
                          if (btn && !btn.disabled) btn.click();
                          return 'ok';
                        })();
                        """.trimIndent(),
                        null,
                    )
                }, 250L)
            }
        }
        binding.web.loadUrl(url)
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

    override fun onDestroy() {
        binding.web.destroy()
        super.onDestroy()
    }

    companion object {
        private const val EXTRA_URL = "url"
        private const val EXTRA_KEY = "key"
        private const val EXTRA_TITLE = "title"

        fun canvas(ctx: Context, url: String, key: String): Intent =
            Intent(ctx, WebInviteActivity::class.java)
                .putExtra(EXTRA_URL, url)
                .putExtra(EXTRA_KEY, key)
                .putExtra(EXTRA_TITLE, "共享画布")

        fun upload(ctx: Context, url: String, key: String): Intent =
            Intent(ctx, WebInviteActivity::class.java)
                .putExtra(EXTRA_URL, url)
                .putExtra(EXTRA_KEY, key)
                .putExtra(EXTRA_TITLE, "上传文件")
    }
}
