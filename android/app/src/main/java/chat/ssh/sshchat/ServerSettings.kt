package chat.ssh.sshchat

import android.content.Context

/** SSH server host/port — defaults from build, overridable in login panel. */
object ServerSettings {
    private const val PREFS = "sshchat_ui"
    private const val KEY_HOST = "ssh_host"
    private const val KEY_PORT = "ssh_port"

    fun loadHost(context: Context): String =
        prefs(context).getString(KEY_HOST, BuildConfig.DEFAULT_HOST)?.trim().orEmpty()
            .ifEmpty { BuildConfig.DEFAULT_HOST }

    fun loadPort(context: Context): Int {
        val saved = prefs(context).getInt(KEY_PORT, BuildConfig.DEFAULT_PORT)
        return if (saved in 1..65535) saved else BuildConfig.DEFAULT_PORT
    }

    fun save(context: Context, host: String, port: Int) {
        prefs(context).edit()
            .putString(KEY_HOST, host.trim())
            .putInt(KEY_PORT, port)
            .apply()
    }

    fun display(context: Context): String = "${loadHost(context)}:${loadPort(context)}"

    fun parsePort(text: String?): Int? {
        val n = text?.trim()?.toIntOrNull() ?: return null
        return if (n in 1..65535) n else null
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
