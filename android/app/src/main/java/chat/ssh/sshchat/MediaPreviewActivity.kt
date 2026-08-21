package chat.ssh.sshchat

import android.content.Context
import android.content.Intent
import android.media.MediaPlayer
import android.net.Uri
import android.os.Bundle
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.widget.MediaController
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import chat.ssh.sshchat.databinding.ActivityMediaPreviewBinding
import java.io.File

class MediaPreviewActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMediaPreviewBinding
    private var scale = 1f
    private var baseW = 0
    private var baseH = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMediaPreviewBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val path = intent.getStringExtra(EXTRA_PATH).orEmpty()
        val name = intent.getStringExtra(EXTRA_NAME).orEmpty().ifBlank { File(path).name }
        val mime = intent.getStringExtra(EXTRA_MIME).orEmpty()
        val file = File(path)
        if (!file.isFile) {
            Toast.makeText(this, "文件不存在", Toast.LENGTH_SHORT).show()
            finish()
            return
        }

        binding.tvTitle.text = name
        binding.btnClose.setOnClickListener { finish() }
        binding.btnShare.setOnClickListener { share(file, mime) }

        when {
            mime.startsWith("image/") -> showImage(file)
            mime.startsWith("video/") -> showVideo(file)
            else -> {
                binding.tvHint.text = "非图片/视频，可分享到其他应用打开"
                binding.image.visibility = View.GONE
                binding.video.visibility = View.GONE
            }
        }
    }

    private fun showImage(file: File) {
        binding.video.visibility = View.GONE
        binding.image.visibility = View.VISIBLE
        binding.tvHint.text = "双指缩放 · 拖动平移"
        binding.image.setImageURI(Uri.fromFile(file))
        binding.image.post {
            baseW = binding.image.width
            baseH = binding.image.height
        }
        val detector = ScaleGestureDetector(this, object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
            override fun onScale(detector: ScaleGestureDetector): Boolean {
                scale = (scale * detector.scaleFactor).coerceIn(0.5f, 6f)
                applyImageScale()
                return true
            }
        })
        var lastX = 0f
        var lastY = 0f
        binding.image.setOnTouchListener { v, ev ->
            detector.onTouchEvent(ev)
            when (ev.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    lastX = ev.x
                    lastY = ev.y
                }
                MotionEvent.ACTION_MOVE -> if (ev.pointerCount == 1 && !detector.isInProgress) {
                    v.translationX += ev.x - lastX
                    v.translationY += ev.y - lastY
                    lastX = ev.x
                    lastY = ev.y
                }
            }
            true
        }
    }

    private fun applyImageScale() {
        if (baseW <= 0) return
        binding.image.scaleX = scale
        binding.image.scaleY = scale
    }

    private fun showVideo(file: File) {
        binding.image.visibility = View.GONE
        binding.video.visibility = View.VISIBLE
        binding.tvHint.text = "视频预览"
        val uri = FileProvider.getUriForFile(this, "$packageName.files", file)
        binding.video.setVideoURI(uri)
        binding.video.setMediaController(MediaController(this).also { it.setAnchorView(binding.video) })
        binding.video.setOnPreparedListener { mp: MediaPlayer ->
            mp.isLooping = false
            binding.video.start()
        }
        binding.video.setOnErrorListener { _, _, _ ->
            Toast.makeText(this, "无法播放，尝试外部打开", Toast.LENGTH_SHORT).show()
            share(file, "video/*")
            true
        }
    }

    private fun share(file: File, mime: String) {
        val uri = FileProvider.getUriForFile(this, "$packageName.files", file)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, mime.ifBlank { "*/*" })
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        startActivity(Intent.createChooser(intent, "打开文件"))
    }

    companion object {
        private const val EXTRA_PATH = "path"
        private const val EXTRA_NAME = "name"
        private const val EXTRA_MIME = "mime"

        fun intent(ctx: Context, media: DownloadedMedia): Intent =
            Intent(ctx, MediaPreviewActivity::class.java)
                .putExtra(EXTRA_PATH, media.file.absolutePath)
                .putExtra(EXTRA_NAME, media.name)
                .putExtra(EXTRA_MIME, media.mime)
    }
}
