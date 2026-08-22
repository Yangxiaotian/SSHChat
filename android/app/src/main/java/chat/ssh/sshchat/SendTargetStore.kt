package chat.ssh.sshchat

import android.content.Context
import org.json.JSONArray

object SendTargetStore {
    private const val PREFS = "sshchat_ui"
    private const val KEY_KIND = "send_target_kind"
    private const val KEY_VALUE = "send_target_value"
    private const val KEY_RECENT = "send_target_recent"
    private const val KEY_ROOM = "current_room"

    fun loadCurrentRoom(context: Context): String =
        prefs(context).getString(KEY_ROOM, "default")?.trim().orEmpty().ifEmpty { "default" }

    fun saveCurrentRoom(context: Context, room: String) {
        prefs(context).edit().putString(KEY_ROOM, room.trim().ifEmpty { "default" }).apply()
    }

    fun loadTarget(context: Context): SendTarget {
        val room = loadCurrentRoom(context)
        return when (prefs(context).getString(KEY_KIND, "room")) {
            "user" -> {
                val nick = prefs(context).getString(KEY_VALUE, "").orEmpty().trim()
                if (nick.isEmpty()) SendTarget.CurrentRoom(room) else SendTarget.User(nick)
            }
            "named_room" -> {
                val r = prefs(context).getString(KEY_VALUE, "").orEmpty().trim()
                if (r.isEmpty()) SendTarget.CurrentRoom(room) else SendTarget.NamedRoom(r)
            }
            else -> SendTarget.CurrentRoom(room)
        }
    }

    fun saveTarget(context: Context, target: SendTarget) {
        val ed = prefs(context).edit()
        when (target) {
            is SendTarget.CurrentRoom -> {
                ed.putString(KEY_KIND, "room")
                ed.putString(KEY_VALUE, target.room)
                ed.putString(KEY_ROOM, target.room)
            }
            is SendTarget.User -> {
                ed.putString(KEY_KIND, "user")
                ed.putString(KEY_VALUE, target.nick)
                rememberRecent(context, target.nick)
            }
            is SendTarget.NamedRoom -> {
                ed.putString(KEY_KIND, "named_room")
                ed.putString(KEY_VALUE, target.room)
            }
        }
        ed.apply()
    }

    fun loadRecentUsers(context: Context): List<String> {
        val raw = prefs(context).getString(KEY_RECENT, "[]") ?: "[]"
        return try {
            val arr = JSONArray(raw)
            buildList {
                for (i in 0 until arr.length()) {
                    val n = arr.optString(i).trim()
                    if (n.isNotEmpty()) add(n)
                }
            }
        } catch (_: Exception) {
            emptyList()
        }
    }

    private fun rememberRecent(context: Context, nick: String) {
        val key = nick.trim()
        if (key.isEmpty()) return
        val merged = (listOf(key) + loadRecentUsers(context).filter { !it.equals(key, true) }).take(8)
        prefs(context).edit().putString(KEY_RECENT, JSONArray(merged).toString()).apply()
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
