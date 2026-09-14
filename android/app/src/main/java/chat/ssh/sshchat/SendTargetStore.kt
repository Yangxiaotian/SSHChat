package chat.ssh.sshchat

import android.content.Context
import org.json.JSONArray

object SendTargetStore {
    private const val PREFS = "sshchat_ui"
    private const val KEY_KIND = "send_target_kind"
    private const val KEY_VALUE = "send_target_value"
    private const val KEY_RECENT = "send_target_recent"
    private const val KEY_ROOM = "current_room"
    private const val KEY_KNOWN_ROOMS = "known_rooms"

    fun loadCurrentRoom(context: Context): String =
        prefs(context).getString(KEY_ROOM, "default")?.trim().orEmpty().ifEmpty { "default" }

    fun saveCurrentRoom(context: Context, room: String) {
        val cleaned = room.trim().ifEmpty { "default" }
        prefs(context).edit().putString(KEY_ROOM, cleaned).apply()
        rememberRooms(context, listOf(cleaned))
    }

    fun loadKnownRooms(context: Context): List<String> {
        val rooms = loadStringList(context, KEY_KNOWN_ROOMS).toMutableList()
        val current = loadCurrentRoom(context)
        if (rooms.none { it.equals(current, true) }) rooms.add(0, current)
        if (rooms.none { it.equals("default", true) }) rooms.add("default")
        return rooms
    }

    fun rememberRooms(context: Context, rooms: List<String>) {
        var merged = loadKnownRooms(context).toMutableList()
        for (raw in rooms) {
            val key = raw.trim().trimStart('#')
            if (key.isEmpty()) continue
            merged.removeAll { it.equals(key, true) }
            merged.add(0, key)
        }
        prefs(context).edit().putString(KEY_KNOWN_ROOMS, JSONArray(merged.take(32)).toString()).apply()
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
                rememberRooms(context, listOf(target.room))
            }
            is SendTarget.User -> {
                ed.putString(KEY_KIND, "user")
                ed.putString(KEY_VALUE, target.nick)
                rememberRecent(context, target.nick)
            }
            is SendTarget.NamedRoom -> {
                ed.putString(KEY_KIND, "named_room")
                ed.putString(KEY_VALUE, target.room)
                rememberRooms(context, listOf(target.room))
            }
        }
        ed.apply()
    }

    fun loadRecentUsers(context: Context): List<String> = loadStringList(context, KEY_RECENT)

    fun rememberRecent(context: Context, nick: String) {
        val key = nick.trim()
        if (key.isEmpty()) return
        val merged = (listOf(key) + loadRecentUsers(context).filter { !it.equals(key, true) }).take(8)
        prefs(context).edit().putString(KEY_RECENT, JSONArray(merged).toString()).apply()
    }

    private fun loadStringList(context: Context, key: String): List<String> {
        val raw = prefs(context).getString(key, "[]") ?: "[]"
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

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
