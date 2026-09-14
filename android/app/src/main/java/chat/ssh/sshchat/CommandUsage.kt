package chat.ssh.sshchat

import android.content.Context
import org.json.JSONObject

/** Tracks per-user command usage counts for completion sorting. */
object CommandUsage {
    private const val PREFS = "sshchat_ui"
    private const val KEY = "command_usage_v1"

    @Volatile
    private var appCtx: Context? = null

    fun init(context: Context) {
        appCtx = context.applicationContext
    }

    fun record(text: String) {
        if (!text.startsWith("/")) return
        val parts = text.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
        if (parts.isEmpty()) return
        val counts = loadCounts().toMutableMap()
        var path = ""
        for ((i, part) in parts.withIndex()) {
            path = if (i == 0) part.lowercase() else "$path ${part.lowercase()}"
            counts[path] = (counts[path] ?: 0) + 1
        }
        saveCounts(counts)
    }

    fun count(key: String): Int = loadCounts()[key.lowercase()] ?: 0

    fun sort(items: List<String>, defaultOrder: List<String>): List<String> {
        return items.sortedWith { a, b ->
            val ca = count(a)
            val cb = count(b)
            when {
                ca != cb -> cb.compareTo(ca)
                else -> {
                    val ia = defaultOrder.indexOf(a).let { if (it < 0) Int.MAX_VALUE else it }
                    val ib = defaultOrder.indexOf(b).let { if (it < 0) Int.MAX_VALUE else it }
                    ia.compareTo(ib)
                }
            }
        }
    }

    private fun loadCounts(): Map<String, Int> {
        val ctx = appCtx ?: return emptyMap()
        val raw = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null)
            ?: return emptyMap()
        return try {
            val json = JSONObject(raw)
            buildMap {
                for (key in json.keys()) {
                    put(key, json.getInt(key))
                }
            }
        } catch (_: Exception) {
            emptyMap()
        }
    }

    private fun saveCounts(counts: Map<String, Int>) {
        val ctx = appCtx ?: return
        val json = JSONObject()
        for ((k, v) in counts) json.put(k, v)
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, json.toString()).apply()
    }
}
