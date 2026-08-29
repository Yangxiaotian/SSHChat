package chat.ssh.sshchat

import android.app.Application
import android.content.Context
import android.content.res.Configuration

class SshChatApp : Application() {
    override fun attachBaseContext(base: Context) {
        // Ignore system font scale; chat uses in-app A- / A+ instead.
        val config = Configuration(base.resources.configuration)
        config.fontScale = 1f
        super.attachBaseContext(base.createConfigurationContext(config))
    }

    override fun onCreate() {
        super.onCreate()
        SshCrypto.ensureInstalled()
    }
}
