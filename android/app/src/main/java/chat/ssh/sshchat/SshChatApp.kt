package chat.ssh.sshchat

import android.app.Application

class SshChatApp : Application() {
    override fun onCreate() {
        super.onCreate()
        SshCrypto.ensureInstalled()
    }
}
